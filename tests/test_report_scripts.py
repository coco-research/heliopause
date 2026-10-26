"""Report hooks call the connector and never speak, even when Heliopause is down."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import atlas
from atlas import plugin

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "connectors"
PY = os.path.abspath(sys.executable)
REPO = str(Path(atlas.__file__).resolve().parent.parent)
REPORT = {"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}/hooks/report.sh"'}
EVENTS = ("UserPromptSubmit", "PermissionRequest", "Stop", "SessionEnd")
_BANNED = ("auth" + ".json", "XAI_" + "API_KEY")
_LINE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z grok (\S+) (\S*)")
_RAW = {
    "claude-code": "claude-code-PermissionRequest.json",
    "cursor": "cursor-beforeSubmitPrompt.json",
    "codex": "codex-PermissionRequest.json",
    "grok": "grok-PreToolUse.json",
}
_LABEL = {"cursor": "cursor-report", "codex": "codex-report", "claude-code": "claude-report"}


def _quiet(proc):
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b"", (
        proc.returncode, proc.stdout, proc.stderr)


def _env():
    env = os.environ.copy()
    env.pop("ATLAS_LAB", None)
    for key in list(env):
        if key.startswith("GROK_"):
            del env[key]
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _own(path, text, mode=0o600):
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def _runtime(lab):
    return f"{PY}\n{REPO}\n{lab}\n"


def _chmod_tree(path):
    for dirpath, dirnames, _names in os.walk(path):
        os.chmod(dirpath, 0o700)
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]


def _install(base, lab):
    """A fake install: plugin hook beside its runtime, tool hooks beside ../runtime."""
    hooks = base / "hooks"
    hooks.mkdir(parents=True)
    conn = base / "connectors"
    conn.mkdir()
    shutil.copy(ROOT / "plugin" / "heliopause" / "hooks" / "report.sh", hooks / "report.sh")
    shutil.copy(ROOT / "connectors" / "report-common.sh", conn / "report-common.sh")
    scripts = {"claude-code": hooks / "report.sh"}
    for tool in ("cursor", "codex", "grok"):
        folder = conn / tool
        folder.mkdir()
        dest = folder / "report.sh"
        shutil.copy(ROOT / "connectors" / tool / "report.sh", dest)
        scripts[tool] = dest
    _chmod_tree(base)
    for script in scripts.values():
        os.chmod(script, 0o755)
    os.chmod(conn / "report-common.sh", 0o755)
    _own(hooks / "runtime", _runtime(lab))
    _own(conn / "runtime", _runtime(lab))
    return scripts


def _runtime_of(script, tool):
    return script.parent / "runtime" if tool == "claude-code" else script.parent.parent / "runtime"


def _run(script, payload, extra=None):
    env = _env()
    if extra:
        env.update(extra)
    return subprocess.run([str(script)], input=payload, capture_output=True, env=env, timeout=30)


def _payload(tool):
    return (FIX / _RAW[tool]).read_bytes()


def _log_line(lab):
    text = (lab / "logs" / "grok-hooks.log").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert text == lines[0] + "\n" and len(lines) == 1
    return lines[0]


def _card(lab, tool, event):
    return lab / ".heliopause" / "cards" / tool / f"{event}.json"


def test_each_tool_writes_a_card(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    scripts = _install(tmp_path / "install", lab)
    for tool, script in scripts.items():
        fixtures = sorted(FIX.glob(tool + "-*.json"))
        assert fixtures, tool
        for fixture in fixtures:
            _quiet(_run(script, fixture.read_bytes()))
            event = fixture.name[len(tool) + 1:-len(".json")]
            card = _card(lab, tool, event)
            assert card.is_file(), (tool, event)
            body = json.loads(card.read_text(encoding="utf-8"))
            assert body["tool"] == tool and body["event"] == event


def test_card_written_while_heliopause_is_down(tmp_path):
    # J14: this test starts no server. The card is a file write, so Heliopause being down is irrelevant.
    lab = tmp_path / "lab"
    lab.mkdir()
    script = _install(tmp_path / "install", lab)["claude-code"]
    raw = (FIX / "claude-code-PermissionRequest.json").read_bytes()
    _quiet(_run(script, raw))
    card = _card(lab, "claude-code", "PermissionRequest")
    assert card.is_file()
    assert json.loads(card.read_text(encoding="utf-8"))["state"] == "needs_you"


def test_bad_runtime_writes_nothing(tmp_path):
    not_dir = tmp_path / "not-a-dir"
    not_dir.write_text("x", encoding="utf-8")
    raw = {
        "claude-code": (FIX / "claude-code-PermissionRequest.json").read_bytes(),
        "cursor": (FIX / "cursor-beforeSubmitPrompt.json").read_bytes(),
    }
    for tool in ("claude-code", "cursor"):
        for kind in ("missing", "symlink", "mode", "no-lab", "not-dir", "relative"):
            lab = tmp_path / f"lab-{tool}-{kind}"
            lab.mkdir()
            work = tmp_path / f"work-{tool}-{kind}"
            script = _install(work, lab)[tool]
            runtime = _runtime_of(script, tool)
            if kind == "missing":
                runtime.unlink()
            elif kind == "symlink":
                runtime.unlink()
                real = work / "real-runtime"
                _own(real, _runtime(lab))
                runtime.symlink_to(real)
            elif kind == "mode":
                os.chmod(runtime, 0o666)
            elif kind == "no-lab":
                _own(runtime, f"{PY}\n{REPO}\n")
            elif kind == "not-dir":
                _own(runtime, f"{PY}\n{REPO}\n{not_dir}\n")
            else:
                _own(runtime, f"python3\n{REPO}\n{lab}\n")
            _quiet(_run(script, raw[tool]))
            assert not (lab / ".heliopause").exists(), (tool, kind)


def test_unsafe_common_script_is_not_sourced(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    script = _install(tmp_path / "install", lab)["cursor"]
    common = script.parent.parent / "report-common.sh"
    raw = (FIX / "cursor-beforeSubmitPrompt.json").read_bytes()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real = elsewhere / "report-common.sh"
    shutil.copy(common, real)
    for kind in ("mode", "symlink"):
        if kind == "mode":
            os.chmod(common, 0o666)
        else:
            os.chmod(common, 0o755)
            common.unlink()
            common.symlink_to(real)
        _quiet(_run(script, raw))
        assert not (lab / ".heliopause").exists(), kind


def test_plugin_hooks_report_on_four_events():
    hooks = json.loads((ROOT / "plugin" / "heliopause" / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    start = hooks["SessionStart"][0]["hooks"][0]
    assert start == {"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"'}
    for name in EVENTS:
        assert hooks[name][0]["hooks"] == [REPORT]
    assert "PreToolUse" not in hooks and "PostToolUse" not in hooks


def test_write_runtime_records_the_lab(tmp_path, monkeypatch):
    def lines(with_lab):
        home = tmp_path / ("with-lab" if with_lab else "no-lab")
        home.mkdir()
        os.chmod(home, 0o700)
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setenv("HOME", str(home))
        if with_lab:
            lab = tmp_path / "lab"
            lab.mkdir()
            (lab / "atlas.json").write_text("{}\n", encoding="utf-8")
            monkeypatch.setenv("ATLAS_LAB", str(lab))
        else:
            lab = None
            monkeypatch.delenv("ATLAS_LAB", raising=False)
        plugin._write_runtime()
        path = home / "Heliopause" / "marketplace" / "heliopause" / "hooks" / "runtime"
        assert path.stat().st_mode & 0o777 == 0o600
        return path.read_text(encoding="utf-8").splitlines(), lab

    two, _ = lines(False)
    assert len(two) == 2
    assert two[0] == PY and two[1] == REPO
    three, lab = lines(True)
    assert three[:2] == two
    assert three[2] == str(lab)


def test_grok_session_skips_foreign_reports(tmp_path):
    for tool, label in _LABEL.items():
        lab = tmp_path / tool
        lab.mkdir()
        script = _install(tmp_path / f"install-{tool}", lab)[tool]
        _quiet(_run(script, _payload(tool), {"GROK_HOOK_EVENT": "Stop"}))
        assert not (lab / ".heliopause").exists(), tool
        found = _LINE.fullmatch(_log_line(lab))
        assert found and found.group(1) == label and found.group(2) == "Stop", tool


def test_grok_report_still_writes_its_card(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    script = _install(tmp_path / "install", lab)["grok"]
    _quiet(_run(script, _payload("grok"), {"GROK_HOOK_EVENT": "Stop"}))
    assert _card(lab, "grok", "PreToolUse").is_file()
    assert not (lab / "logs" / "grok-hooks.log").exists()


def test_other_grok_variables_still_report(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    script = _install(tmp_path / "install", lab)["cursor"]
    _quiet(_run(script, _payload("cursor"), {
        "GROK_HOOK_NAME": "Stop",
        "GROK_SESSION_ID": "abc",
        "GROK_WORKSPACE_ROOT": "/tmp/not-used",
    }))
    assert _card(lab, "cursor", "beforeSubmitPrompt").is_file()
    assert not (lab / "logs").exists()


def test_grok_hook_event_is_a_short_word(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    script = _install(tmp_path / "install", lab)["cursor"]
    dirty = "Stop;$(evil)_" + ("B" * 40)
    _quiet(_run(script, _payload("cursor"), {"GROK_HOOK_EVENT": dirty}))
    found = _LINE.fullmatch(_log_line(lab))
    assert found and found.group(2) == ("Stopevil_" + ("B" * 40))[:40]
    assert len(found.group(2)) == 40
    assert not (lab / ".heliopause").exists()


def test_sealed_grok_home_stays_silent(tmp_path):
    home = tmp_path / "home"
    (home / ".grok").mkdir(parents=True)
    _own(home / ".grok" / _BANNED[0], "{}\n", 0)
    sealed = {"HOME": str(home), _BANNED[1]: "sentinel"}
    for tool in ("cursor", "codex", "claude-code", "grok"):
        for event in (None, "Stop"):
            lab = tmp_path / f"{tool}-{event or 'plain'}"
            lab.mkdir()
            script = _install(tmp_path / f"install-{tool}-{event or 'plain'}", lab)[tool]
            extra = dict(sealed)
            if event:
                extra["GROK_HOOK_EVENT"] = event
            _quiet(_run(script, _payload(tool), extra))
            card = _card(lab, tool, "PreToolUse" if tool == "grok" else (
                "beforeSubmitPrompt" if tool == "cursor" else "PermissionRequest"))
            if event and tool != "grok":
                assert not (lab / ".heliopause").exists(), tool
                assert _LINE.fullmatch(_log_line(lab))
            else:
                assert card.is_file(), (tool, event)
                assert not (lab / "logs" / "grok-hooks.log").exists(), (tool, event)


def test_report_hooks_do_not_name_grok_secrets():
    paths = [
        ROOT / "connectors" / "report-common.sh",
        ROOT / "plugin" / "heliopause" / "hooks" / "report.sh",
        ROOT / "connectors" / "cursor" / "report.sh",
        ROOT / "connectors" / "codex" / "report.sh",
        ROOT / "connectors" / "grok" / "report.sh",
        Path(__file__),
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for word in _BANNED:
            assert word not in text, (path.name, word)

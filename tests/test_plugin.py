"""Atlas plugin: host stdin fields, local install, no purge."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from atlas.__main__ import main
from atlas import __version__, adopt, plugin

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "heliopause"
OUTSIDE_NAME = "lab-nope"


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert match, "pyproject.toml has no version"
    return match.group(1)


def _exclude(text: str) -> list[str]:
    drops = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().rstrip("/")
        if not line or line.endswith("=> {}"):
            continue
        drops.append(line)
    return drops


def _lab(tmp_path: Path, lead: str = "Ada", marked: list[str] | None = None) -> Path:
    lab = tmp_path / "lab"
    lab.mkdir()
    data = {
        "v": 1,
        "owner": "Owner",
        "tz": "UTC",
        "lead": lead,
        "nodes": [{"id": "lead", "label": "Lead", "role": "lab"}],
    }
    if marked is not None:
        data["marked"] = marked
    (lab / "atlas.json").write_text(json.dumps(data), encoding="utf-8")
    return lab


def _hook(payload: object, lab: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ATLAS_LAB"] = str(lab)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # The first-run line reads hook files under HOME. Keep that off the real home.
    home = lab.parent / "hook-home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-B", "-m", "atlas", "hook", "session-start"],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
    )


def _context(proc: subprocess.CompletedProcess[str]) -> str:
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert set(data) == {"hookSpecificOutput"}
    block = data["hookSpecificOutput"]
    assert set(block) == {"hookEventName", "additionalContext"}
    assert block["hookEventName"] == "SessionStart"
    text = block["additionalContext"]
    assert isinstance(text, str) and len(text) < 3000
    assert "additionalContext" not in data
    return text


def _fake_claude(tmp_path: Path, monkeypatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.log"
    script = bin_dir / "claude"
    script.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" >> \"$CLAUDE_ARGV_LOG\"\nprintf '\\n' >> \"$CLAUDE_ARGV_LOG\"\nexit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("CLAUDE_ARGV_LOG", str(log))
    return log


def test_plugin_version_matches_package() -> None:
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "heliopause"
    assert manifest["license"] == "MIT"
    assert manifest["version"] == __version__ == _pyproject_version()
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    groups = hooks["hooks"]["SessionStart"]
    assert len(groups) == 1 and len(groups[0]["hooks"]) == 1
    assert groups[0]["hooks"][0]["command"] == '"${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"'
    assert (PLUGIN / "skills" / "lead" / "SKILL.md").is_file()
    assert not (PLUGIN / ".mcp.json").exists()
    assert not (PLUGIN / "agents").exists()
    assert not (PLUGIN / "commands").exists()


def test_plugin_is_in_the_release_zip() -> None:
    drops = _exclude((ROOT / ".public-exclude").read_text(encoding="utf-8"))
    for rel in (
        "plugin/heliopause/.claude-plugin/plugin.json",
        "plugin/heliopause/hooks/hooks.json",
        "plugin/heliopause/hooks/session-start.sh",
        "plugin/heliopause/skills/lead/SKILL.md",
    ):
        assert (ROOT / rel).is_file()
        assert not any(rel == drop or rel.startswith(drop + "/") for drop in drops)


def test_hook_is_silent_outside_the_lab(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    plugin_root = PLUGIN
    outside = tmp_path / OUTSIDE_NAME
    claude = _hook({"hook_event_name": "SessionStart", "cwd": str(outside)}, lab, lab)
    assert claude.returncode == 0 and claude.stdout == ""
    cursor = _hook(
        {"hook_event_name": "SessionStart", "cwd": str(plugin_root), "workspace": str(outside)},
        lab,
        plugin_root,
    )
    assert cursor.returncode == 0 and cursor.stdout == ""


def test_hook_context_shape_and_length(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    proc = _hook({"cwd": str(lab)}, lab, PLUGIN)
    text = _context(proc)
    assert "Ada" in text
    assert str(lab / "board.md") in text
    assert str(lab / "feed.md") in text
    assert str(lab / "START-HERE.md") in text
    rules = [line for line in text.splitlines() if line.startswith("- ")]
    assert 6 <= len(rules) <= 8
    low = text.lower()
    assert "read the board first" in low
    assert "never read other chats' transcripts" in low
    assert "never archive, message, or retitle other chats" in low
    assert "board line" in low
    assert "ask the owner" in low


def test_cursor_workspace_is_the_lab_even_when_cwd_is_the_plugin(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    proc = _hook({"cwd": str(PLUGIN), "workspace": str(lab)}, lab, PLUGIN)
    text = _context(proc)
    assert "Ada" in text


def test_first_run_is_only_while_nothing_is_marked(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    text = _context(_hook({"cwd": str(lab)}, lab, PLUGIN))
    assert len(text) < 10000
    assert "I didn't find anything to flag in Hooks & checks or Repo standard." in text
    assert "Which project should I watch first?" in text
    assert "10 most recent" not in text
    data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    data["marked"] = [str(tmp_path / "proj")]
    (lab / "atlas.json").write_text(json.dumps(data), encoding="utf-8")
    quiet = _context(_hook({"cwd": str(lab)}, lab, PLUGIN))
    assert len(quiet) < 10000
    assert "Which project should I watch first?" not in quiet
    assert "Hooks & checks" not in quiet


def test_marked_folder_gets_context(tmp_path: Path) -> None:
    marked = tmp_path / "marked"
    lab = _lab(tmp_path, marked=[str(marked)])
    proc = _hook({"cwd": str(marked)}, lab, PLUGIN)
    assert "Ada" in _context(proc)
    sibling = _hook({"cwd": str(marked) + "-nope"}, lab, PLUGIN)
    assert sibling.returncode == 0 and sibling.stdout == ""


def test_garbage_stdin_is_silent(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    for payload in ("", "not json", "{", "[]", "null", "42"):
        proc = _hook(payload, lab, PLUGIN)
        assert proc.returncode == 0 and proc.stdout == "", payload


def test_broken_atlas_json_is_silent(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "atlas.json").write_text("{", encoding="utf-8")
    proc = _hook({"cwd": str(lab)}, lab, PLUGIN)
    assert proc.returncode == 0 and proc.stdout == ""


def test_session_start_sh_fail_soft(tmp_path: Path) -> None:
    script = PLUGIN / "hooks" / "session-start.sh"
    home = tmp_path / "empty-home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    missing = subprocess.run(["sh", str(script)], input="garbage", capture_output=True, text=True, env=env)
    assert missing.returncode == 0 and missing.stdout == ""

    bindir = home / ".atlas" / "bin"
    bindir.mkdir(parents=True)
    log = tmp_path / "shim.log"
    stdin_path = tmp_path / "shim-stdin"
    shim = bindir / "atlas"
    shim.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$ATLAS_SHIM_LOG\"\ncat > \"$ATLAS_SHIM_STDIN\"\nexit 1\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    env["ATLAS_SHIM_LOG"] = str(log)
    env["ATLAS_SHIM_STDIN"] = str(stdin_path)
    ran = subprocess.run(["sh", str(script)], input='{"cwd":"/tmp"}\n', capture_output=True, text=True, env=env)
    assert ran.returncode == 0 and ran.stdout == ""
    assert log.read_text(encoding="utf-8").strip() == "hook session-start"
    assert '"cwd"' in stdin_path.read_text(encoding="utf-8")


def test_installed_hook_runs_without_shim(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ATLAS_LAB", raising=False)
    _fake_claude(tmp_path, monkeypatch)
    assert plugin.run(["install", "--host", "claude", "--yes"]) == 0
    assert not (home / ".atlas" / "bin" / "atlas").exists()

    script = home / "Heliopause" / "marketplace" / "heliopause" / "hooks" / "session-start.sh"
    runtime = script.parent / "runtime"
    assert runtime.is_file()
    assert runtime.stat().st_mode & 0o777 == 0o600
    lab = _lab(tmp_path, lead="Ada")
    (home / ".atlas" / "lab").write_text(str(lab) + "\n", encoding="utf-8")

    def run(cwd: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        env.pop("ATLAS_LAB", None)
        env["PATH"] = "/bin:/usr/bin"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            ["sh", str(script)],
            input=json.dumps({"hook_event_name": "SessionStart", "cwd": str(cwd)}),
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
        )

    assert "Ada" in _context(run(lab))

    # A repo with its own atlas/ package in the hook's working folder must never run.
    evil = tmp_path / "evil-repo"
    (evil / "atlas").mkdir(parents=True)
    marker = tmp_path / "planted-ran"
    (evil / "atlas" / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    (evil / "atlas" / "__main__.py").write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.update(HOME=str(home), PATH="/bin:/usr/bin", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(evil))
    env.pop("ATLAS_LAB", None)
    planted = subprocess.run(
        ["sh", str(script)],
        input=json.dumps({"hook_event_name": "SessionStart", "cwd": str(lab)}),
        capture_output=True, text=True, env=env, cwd=str(evil),
    )
    assert not marker.exists()
    assert "Ada" in _context(planted)
    outside = run(tmp_path / OUTSIDE_NAME)
    assert outside.returncode == 0 and outside.stdout == ""

    runtime.unlink()
    removed = run(lab)
    assert removed.returncode == 0 and removed.stdout == ""

    bogus = tmp_path / "no-main"
    bogus.mkdir()
    runtime.write_text(sys.executable + "\n" + str(bogus) + "\n", encoding="utf-8")
    bad = run(lab)
    assert bad.returncode == 0 and bad.stdout == ""

    # Writable by others, or relative paths: refused, silently.
    root = Path(plugin.__file__).resolve().parent.parent
    runtime.write_text(sys.executable + "\n" + str(root) + "\n", encoding="utf-8")
    os.chmod(runtime, 0o600)
    assert "Ada" in _context(run(lab))
    os.chmod(runtime, 0o620)
    loose = run(lab)
    assert loose.returncode == 0 and loose.stdout == ""
    os.chmod(runtime, 0o600)
    runtime.write_text("python3\n" + str(root) + "\n", encoding="utf-8")
    relative = run(lab)
    assert relative.returncode == 0 and relative.stdout == ""


def test_install_uninstall_argv_and_autoupdate(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    log = _fake_claude(tmp_path, monkeypatch)
    assert plugin.run(["install", "--yes"]) == 0
    market = json.loads((home / "Heliopause" / "marketplace" / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["autoUpdate"] is False
    assert market["name"] == "heliopause-local"
    copied = json.loads((home / "Heliopause" / "marketplace" / "heliopause" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert copied["version"] == __version__
    assert plugin.run(["status"]) == 0
    assert plugin.run(["uninstall"]) == 0
    assert not (home / "Heliopause" / "marketplace").exists()
    recorded = log.read_text(encoding="utf-8")
    assert "purge" not in recorded
    assert recorded.split("\n\n") == [
        "\n".join(["plugin", "marketplace", "add", str(home / "Heliopause" / "marketplace")]),
        "\n".join(["plugin", "install", "heliopause@heliopause-local"]),
        "\n".join(["plugin", "list"]),
        "\n".join(["plugin", "uninstall", "heliopause@heliopause-local"]),
        "\n".join(["plugin", "marketplace", "remove", "heliopause-local"]),
        "",
    ]


def test_apply_offers_plugin_install_as_its_own_question(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    log = _fake_claude(tmp_path, monkeypatch)
    prompts: list[str] = []

    def ask(prompt: str = "") -> str:
        prompts.append(prompt)
        return "y" if len(prompts) == 1 else "n"

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", ask)
    lab = tmp_path / "lab"
    assert adopt.run(["--apply", "--yes", "--home", str(home), "--lab", str(lab), "--lead-name", "Ada"]) == 0
    assert len(prompts) == 2 and prompts[0] != prompts[1]
    assert "plugin" in prompts[0].lower()
    assert "command" in prompts[1].lower()
    assert not log.exists()
    assert (home / ".atlas" / "lab").read_text(encoding="utf-8") == str(lab) + "\n"
    # The second prompt said no: the marketplace copy is not built either.
    assert not (home / "Heliopause" / "marketplace").exists()


def test_pointer_lab_is_used_when_atlas_lab_is_unset(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ATLAS_LAB", raising=False)
    lab = _lab(tmp_path, lead="Ada")
    pointer = home / ".atlas"
    pointer.mkdir()
    (pointer / "lab").write_text(str(lab) + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps({"cwd": str(PLUGIN)})))
    assert plugin.session_start() == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps({"workspace": str(lab)})))
    assert plugin.session_start() == 0
    text = _context_from(capsys.readouterr().out)
    assert "Ada" in text


def test_session_start_swallows_errors_but_not_keyboard_interrupt(monkeypatch) -> None:
    def interrupt() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(plugin, "_emit", interrupt)
    with pytest.raises(KeyboardInterrupt):
        plugin.session_start()

    def stop() -> None:
        raise SystemExit(2)

    monkeypatch.setattr(plugin, "_emit", stop)
    assert plugin.session_start() == 0

    def bad() -> None:
        raise ValueError("x")

    monkeypatch.setattr(plugin, "_emit", bad)
    assert plugin.session_start() == 0


def _context_from(raw: str) -> str:
    data = json.loads(raw)
    assert set(data) == {"hookSpecificOutput"}
    block = data["hookSpecificOutput"]
    assert block["hookEventName"] == "SessionStart"
    text = block["additionalContext"]
    assert len(text) < 3000
    return text


def _fixture(name: str, folder: str, plugin_root: str) -> dict:
    raw = (ROOT / "tests" / "fixtures" / "hooks" / name).read_text(encoding="utf-8")
    return json.loads(raw.replace("__FOLDER__", folder).replace("__PLUGIN__", plugin_root))


def _fake_hosts(tmp_path: Path, monkeypatch, blocked: set[str] | None = None) -> Path:
    blocked = blocked or set()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.log"
    for name in ("claude", "grok", "cursor-agent"):
        # PATH is only this directory, so the script cannot call basename.
        tail = "echo 'Marketplace source blocked by allowlist' >&2\nexit 1\n" if name in blocked else "exit 0\n"
        script = bin_dir / name
        script.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' {name} \"$@\" >> \"$ATLAS_ARGV_LOG\"\n"
            "printf '\\n' >> \"$ATLAS_ARGV_LOG\"\n"
            + tail,
            encoding="utf-8",
        )
        script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("ATLAS_ARGV_LOG", str(log))
    return log


def _ready_cursor(home: Path, lab: Path, monkeypatch) -> None:
    """A tmp ~/Heliopause with the connector scripts, and _root() pointed at it."""
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    os.chmod(home, 0o700)
    dest = home / "Heliopause"
    dest.mkdir(mode=0o700, exist_ok=True)
    os.chmod(dest, 0o700)
    shutil.copytree(ROOT / "connectors", dest / "connectors")
    for folder, _dirs, files in os.walk(dest / "connectors"):
        os.chmod(folder, 0o700)
        for name in files:
            path = Path(folder) / name
            path.chmod(0o755 if name.endswith(".sh") else 0o644)
    monkeypatch.setattr(plugin, "_root", lambda: dest)


def _argv_groups(log: Path) -> list[list[str]]:
    text = log.read_text(encoding="utf-8")
    assert "purge" not in text
    return [block.split("\n") for block in text.split("\n\n") if block]


def test_host_payload_fixtures(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    outside = str(tmp_path / OUTSIDE_NAME)
    speaks = {"claude-session.json", "cursor-session.json", "grok-session.json"}
    for name in (
        "claude-session.json",
        "cursor-session.json",
        "grok-session.json",
        "cursor-no-workspace.json",
        "grok-no-workspace.json",
        "unknown-session.json",
    ):
        proc = _hook(_fixture(name, str(lab), str(PLUGIN)), lab, PLUGIN)
        if name in speaks:
            assert "Ada" in _context(proc), name
        else:
            assert proc.returncode == 0 and proc.stdout == "", name
        silent = _hook(_fixture(name, outside, str(PLUGIN)), lab, PLUGIN)
        assert silent.returncode == 0 and silent.stdout == "", name


def test_host_install_status_update_uninstall_argv(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    _ready_cursor(home, _lab(tmp_path), monkeypatch)
    log = _fake_hosts(tmp_path, monkeypatch)
    market = home / "Heliopause" / "marketplace"
    plugin_dir = market / "heliopause"
    assert plugin.run(["install", "--host", "all", "--yes"]) == 0
    market_json = json.loads((market / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market_json["autoUpdate"] is False
    assert (home / ".atlas" / "plugin-hosts").read_text(encoding="utf-8") == "claude\ngrok\n"
    assert plugin.run(["status", "--host", "all"]) == 0
    assert plugin.after_switch() == 0
    assert plugin.run(["uninstall", "--host", "all"]) == 0
    assert not market.exists()
    assert not (home / ".atlas" / "plugin-hosts").exists()
    assert _argv_groups(log) == [
        ["claude", "plugin", "marketplace", "add", str(market)],
        ["claude", "plugin", "install", "heliopause@heliopause-local"],
        ["grok", "plugin", "install", str(plugin_dir)],
        ["claude", "plugin", "list"],
        ["grok", "plugin", "list"],
        ["claude", "plugin", "update", "heliopause@heliopause-local"],
        ["grok", "plugin", "update", "heliopause"],
        ["claude", "plugin", "uninstall", "heliopause@heliopause-local"],
        ["claude", "plugin", "marketplace", "remove", "heliopause-local"],
        ["grok", "plugin", "uninstall", "heliopause"],
    ]
    out = capsys.readouterr().out
    assert "cursor-agent --plugin-dir" not in out
    assert "persistent install is not supported" not in out
    assert "cursor: connected" in out
    assert "no auto-update flag" in out
    assert "purge" not in out
    assert "cursor-agent" not in log.read_text(encoding="utf-8")


def test_blocked_host_is_rules_only(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    lab = _lab(tmp_path)
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    log = _fake_hosts(tmp_path, monkeypatch, blocked={"claude"})
    assert plugin.run(["install", "--host", "claude", "--yes"]) == 0
    rules = lab / ".atlas" / "rules" / "claude.md"
    text = rules.read_text(encoding="utf-8")
    assert "Ada" in text and "Read the board first." in text
    assert "Rules only" in capsys.readouterr().out
    assert _argv_groups(log) == [
        ["claude", "plugin", "marketplace", "add", str(home / "Heliopause" / "marketplace")],
    ]
    assert not (home / ".atlas" / "plugin-hosts").exists()


def test_plain_failure_does_not_write_rules(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    lab = _lab(tmp_path)
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.log"
    script = bin_dir / "grok"
    script.write_text(
        "#!/bin/sh\nprintf '%s\\n' grok \"$@\" >> \"$ATLAS_ARGV_LOG\"\nprintf '\\n' >> \"$ATLAS_ARGV_LOG\"\necho 'not found' >&2\nexit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("ATLAS_ARGV_LOG", str(log))
    assert plugin.run(["install", "--host", "grok", "--yes"]) == 1
    assert not (lab / ".atlas" / "rules").exists()
    assert _argv_groups(log) == [
        ["grok", "plugin", "install", str(home / "Heliopause" / "marketplace" / "heliopause")],
    ]


def test_apply_offers_the_hosts_it_found(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    (home / ".claude").mkdir()
    (home / ".grok").mkdir()
    log = _fake_hosts(tmp_path, monkeypatch)
    prompts: list[str] = []

    def ask(prompt: str = "") -> str:
        prompts.append(prompt)
        return "n"

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", ask)
    lab = tmp_path / "lab"
    assert adopt.run(["--apply", "--yes", "--home", str(home), "--lab", str(lab), "--lead-name", "Ada"]) == 0
    assert len(prompts) == 1
    assert "claude" in prompts[0] and "grok" in prompts[0] and "cursor" not in prompts[0]
    assert "plugin" in prompts[0].lower()
    assert not log.exists()


def test_uninstall_keeps_the_copy_while_another_host_uses_it(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    _ready_cursor(home, _lab(tmp_path), monkeypatch)
    _fake_hosts(tmp_path, monkeypatch)
    market = home / "Heliopause" / "marketplace"
    assert plugin.run(["install", "--host", "all", "--yes"]) == 0
    assert plugin.run(["uninstall", "--host", "claude"]) == 0
    assert market.is_dir()
    assert (home / ".atlas" / "plugin-hosts").read_text(encoding="utf-8") == "grok\n"
    assert plugin.run(["uninstall", "--host", "grok"]) == 0
    assert not market.exists()


def _claude_only_market(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    _fake_hosts(tmp_path, monkeypatch)
    market = home / "Heliopause" / "marketplace"
    market.mkdir(parents=True)
    meta = market / ".claude-plugin"
    meta.mkdir()
    (meta / "marketplace.json").write_text(
        json.dumps({"name": "heliopause-local"}) + "\n",
        encoding="utf-8",
    )
    (home / ".atlas").mkdir()
    (home / ".atlas" / "plugin-hosts").write_text("claude\n", encoding="utf-8")
    return home, market


def test_uninstall_drops_marketplace_when_cursor_home_exists(tmp_path: Path, monkeypatch) -> None:
    home, market = _claude_only_market(tmp_path, monkeypatch)
    (home / ".cursor").mkdir()
    assert plugin.uninstall(["claude"]) == 0
    assert not market.exists()


def test_uninstall_drops_marketplace_when_cursor_is_named(tmp_path: Path, monkeypatch, capsys) -> None:
    home, market = _claude_only_market(tmp_path, monkeypatch)
    (home / ".cursor").mkdir()
    monkeypatch.setenv("HOME", str(home))
    lab = _lab(tmp_path)
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    os.chmod(home / "Heliopause", 0o700)
    assert plugin.uninstall(["claude", "grok", "cursor"]) == 0
    assert not market.exists()
    path = str(lab / ".cursor" / "hooks.json")
    assert capsys.readouterr().out.splitlines() == [
        f"cursor: removed Heliopause's hook entries from {path}. Other entries are unchanged."
    ]


def test_uninstall_drops_marketplace_when_cursor_home_is_absent(tmp_path: Path, monkeypatch) -> None:
    _home, market = _claude_only_market(tmp_path, monkeypatch)
    assert plugin.uninstall(["claude"]) == 0
    assert not market.exists()


def test_failed_host_uninstall_keeps_the_copy(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    _fake_hosts(tmp_path, monkeypatch)
    market = home / "Heliopause" / "marketplace"
    assert plugin.run(["install", "--host", "claude", "--yes"]) == 0
    monkeypatch.setattr(plugin, "_uninstall_one", lambda name: 1)  # the host CLI failed; it may still load the plugin
    monkeypatch.setattr(plugin, "_installed", lambda: [])
    assert plugin.run(["uninstall", "--host", "claude"]) != 0
    assert market.is_dir()


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude not on PATH")
def test_claude_plugin_validate_strict() -> None:
    proc = subprocess.run(
        ["claude", "plugin", "validate", "--strict", str(PLUGIN)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_host_cli_path_is_shown_and_hang_times_out(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    # A host CLI that waits on its stdin: with no timeout and the terminal's stdin it would block.
    script.write_text("#!/bin/sh\nread answer\nwhile :; do :; done\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(plugin, "HOST_TIMEOUT", 1)
    assert plugin.run(["install", "--host", "claude", "--yes"]) == 1
    captured = capsys.readouterr()
    assert f"claude: will run {script}" in captured.out
    assert f"{script} did not finish within 1 s" in captured.err


def test_missing_host_cli_is_reported_not_raised(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert plugin.run(["install", "--host", "grok", "--yes"]) == 1
    assert "grok: not found on PATH; nothing to run" in capsys.readouterr().out


def _raise_at_prompt(exc):
    def ask(prompt: str = "") -> str:
        if exc is None:
            return "n"
        raise exc

    return ask


@pytest.mark.parametrize(
    "tty, exc",
    [(False, None), (True, None), (True, EOFError), (True, KeyboardInterrupt)],
    ids=["no-tty", "n", "eof", "ctrl-c"],
)
@pytest.mark.parametrize("host", ["claude", "cursor", "all"])
def test_install_refusal_exits_1_and_keeps_the_marketplace(tmp_path: Path, monkeypatch, capsys, tty, exc, host) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".grok").mkdir()
    (home / ".cursor").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    log = _fake_claude(tmp_path, monkeypatch)
    grok = tmp_path / "bin" / "grok"
    grok.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    grok.chmod(0o755)
    market = home / "Heliopause" / "marketplace"
    market.mkdir(parents=True)
    (market / "keep.txt").write_text("previous copy\n", encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
    monkeypatch.setattr("builtins.input", _raise_at_prompt(exc))
    try:
        with pytest.raises(SystemExit) as done:
            main(["plugin", "install", "--host", host])
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C at the prompt escaped as KeyboardInterrupt")
    captured = capsys.readouterr()
    assert done.value.code == 1
    assert "Traceback" not in captured.err
    assert sorted(p.name for p in market.iterdir()) == ["keep.txt"]
    assert not log.exists()
    assert not (home / ".atlas" / "plugin-hosts").exists()

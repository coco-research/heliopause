"""Cursor plugin install writes the lab hook file and ~/Heliopause/connectors/runtime."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from atlas import plugin
from atlas.connect import CURSOR_COMMAND
from atlas.connectors import _CURSOR

ROOT = Path(__file__).resolve().parents[1]


def _chmod_connectors(dest: Path) -> None:
    for folder, _dirs, files in os.walk(dest):
        os.chmod(folder, 0o700)
        for name in files:
            path = Path(folder) / name
            path.chmod(0o755 if name.endswith(".sh") else 0o644)


def _home_with_connectors(tmp_path: Path, monkeypatch, *, lab: bool, atlas_pkg: bool) -> tuple[Path, Path | None]:
    """Tmp HOME with the connector scripts. _root() is that ~/Heliopause."""
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    os.chmod(home, 0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ATLAS_LAB", raising=False)
    dest = home / "Heliopause"
    dest.mkdir(mode=0o700)
    os.chmod(dest, 0o700)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "connectors", dest / "connectors", ignore=ignore)
    _chmod_connectors(dest / "connectors")
    if atlas_pkg:
        # The runtime root line is this tmp copy, not the repo: _root() is monkeypatched
        # here, and the hook imports whatever path that line names.
        shutil.copytree(ROOT / "atlas", dest / "atlas", ignore=ignore)
    found: Path | None = None
    if lab:
        found = tmp_path / "lab"
        found.mkdir(mode=0o700)
        (found / "atlas.json").write_text("{}\n", encoding="utf-8")
        monkeypatch.setenv("ATLAS_LAB", str(found))
    monkeypatch.setattr(plugin, "_root", lambda: dest)
    return home, found


def _seed(lab: Path) -> bytes:
    body = json.dumps(
        {"version": 1, "hooks": {"sessionStart": [{"command": "/bin/other-tool"}]}},
        indent=2,
    ) + "\n"
    folder = lab / ".cursor"
    folder.mkdir()
    path = folder / "hooks.json"
    path.write_bytes(body.encode("utf-8"))
    return body.encode("utf-8")


def _files(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    if not root.exists():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")
        for name in sorted(filenames):
            if name.endswith(".pyc"):
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            found[rel] = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
    return found


def _stamp(path: Path) -> tuple[int, int, bytes]:
    st = path.stat()
    return (st.st_ino, st.st_mtime_ns, path.read_bytes())


def _unchanged(path: Path) -> tuple[str, int]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def test_install_writes_runtime_hooks_and_keeps_other_entries(tmp_path: Path, monkeypatch, capsys) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    original = _seed(lab)
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 0
    out = capsys.readouterr().out
    hooks = lab / ".cursor" / "hooks.json"
    runtime = home / "Heliopause" / "connectors" / "runtime"
    backs = sorted((lab / ".cursor").glob("hooks.json.heliopause-bak-*"))
    assert len(backs) == 1
    assert backs[0].read_bytes() == original
    assert plugin.PLAN_ADD.format(path=str(hooks), backup=str(backs[0])) in out
    assert plugin.RUNTIME.format(runtime=str(runtime)) in out
    assert plugin.DONE.format(lab=lab) in out
    assert plugin.PROMPT not in out
    assert plugin.ALREADY not in out

    assert stat.S_IMODE(runtime.stat().st_mode) == 0o600
    assert runtime.read_text(encoding="utf-8") == (
        f"{os.path.abspath(sys.executable)}\n{(home / 'Heliopause').resolve()}\n{lab.resolve()}\n"
    )
    data = json.loads(hooks.read_text(encoding="utf-8"))
    assert list(data["hooks"]) == list(_CURSOR)
    assert data["hooks"]["sessionStart"][0] == {"command": "/bin/other-tool"}
    for event in _CURSOR:
        ours = [item for item in data["hooks"][event] if item.get("command") == CURSOR_COMMAND]
        assert ours == [{"command": CURSOR_COMMAND}]

    assert plugin.run(["status", "--host", "cursor"]) == 0
    assert capsys.readouterr().out == plugin.STATUS_ON.format(path=str(hooks)) + "\n"
    before = (_stamp(hooks), _stamp(runtime), len(backs))
    assert plugin._update_one("cursor") == 0
    assert capsys.readouterr().out == plugin.UPDATE + "\n"
    assert (_stamp(hooks), _stamp(runtime), len(list((lab / ".cursor").glob("hooks.json.heliopause-bak-*")))) == before


def test_second_install_changes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    _seed(lab)
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 0
    capsys.readouterr()
    hooks = lab / ".cursor" / "hooks.json"
    runtime = home / "Heliopause" / "connectors" / "runtime"
    before = (_stamp(hooks), _stamp(runtime))
    backups = list((lab / ".cursor").glob("hooks.json.heliopause-bak-*"))
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 0
    assert capsys.readouterr().out == plugin.ALREADY + "\n"
    assert (_stamp(hooks), _stamp(runtime)) == before
    assert list((lab / ".cursor").glob("hooks.json.heliopause-bak-*")) == backups


def test_uninstall_removes_only_heliopause_entries_and_runtime(tmp_path: Path, monkeypatch, capsys) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    original = _seed(lab)
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 0
    capsys.readouterr()
    runtime = home / "Heliopause" / "connectors" / "runtime"
    assert runtime.is_file()
    hooks = lab / ".cursor" / "hooks.json"
    backs = sorted((lab / ".cursor").glob("hooks.json.heliopause-bak-*"))
    assert len(backs) == 1
    assert plugin.run(["uninstall", "--host", "cursor"]) == 0
    assert capsys.readouterr().out == (
        "cursor: removed Heliopause's hook entries from "
        f"{hooks} and deleted {runtime}. Other entries are unchanged. "
        f"Your backup is still at {backs[0]}.\n"
    )
    assert hooks.read_bytes() == original
    assert list((lab / ".cursor").glob("hooks.json.heliopause-bak-*")) == backs
    data = json.loads(hooks.read_text(encoding="utf-8"))
    assert data["hooks"] == {"sessionStart": [{"command": "/bin/other-tool"}]}
    assert not runtime.exists()
    assert (home / "Heliopause" / "connectors" / "cursor" / "report.sh").is_file()


def test_noninteractive_install_without_yes_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    _seed(lab)
    hooks = lab / ".cursor" / "hooks.json"
    digest, mtime = _unchanged(hooks)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert not sys.stdin.isatty()
    assert plugin.run(["install", "--host", "cursor"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "atlas plugin: non-interactive terminal: re-run with --yes\n"
    assert plugin.NOTHING in captured.out
    assert "Nothing changed." not in captured.out
    assert _unchanged(hooks) == (digest, mtime)
    assert not (home / "Heliopause" / "connectors" / "runtime").exists()
    assert list((lab / ".cursor").glob("hooks.json.heliopause-bak-*")) == []


@pytest.mark.parametrize(
    "answer",
    ["n", "", EOFError, KeyboardInterrupt],
    ids=["n", "enter", "eof", "ctrl-c"],
)
def test_declined_install_writes_nothing(tmp_path: Path, monkeypatch, capsys, answer) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    original = _seed(lab)
    hooks = lab / ".cursor" / "hooks.json"
    digest, mtime = _unchanged(hooks)
    seen: list[str] = []

    def ask(prompt: str = "") -> str:
        seen.append(prompt)
        if answer in (EOFError, KeyboardInterrupt):
            raise answer
        return answer

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", ask)
    assert plugin.run(["install", "--host", "cursor"]) == 1
    captured = capsys.readouterr()
    assert seen == [plugin.PROMPT]
    assert plugin.NOTHING in captured.out
    assert "Nothing changed." not in captured.out
    assert captured.err == ""
    assert hooks.read_bytes() == original
    assert _unchanged(hooks) == (digest, mtime)
    assert not (home / "Heliopause" / "connectors" / "runtime").exists()
    assert list((lab / ".cursor").glob("hooks.json.heliopause-bak-*")) == []


def test_uninstall_restores_a_differently_formatted_hooks_file(tmp_path: Path, monkeypatch, capsys) -> None:
    _home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    # 4-space indent, hooks before version, timeout before command, no trailing newline.
    raw = (
        "{\n"
        '    "hooks": {\n'
        '        "sessionStart": [\n'
        "            {\n"
        '                "timeout": 9,\n'
        '                "command": "/bin/other-tool"\n'
        "            }\n"
        "        ]\n"
        "    },\n"
        '    "version": 1\n'
        "}"
    )
    assert not raw.endswith("\n")
    folder = lab / ".cursor"
    folder.mkdir()
    hooks = folder / "hooks.json"
    payload = raw.encode("utf-8")
    hooks.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 0
    capsys.readouterr()
    assert plugin.run(["uninstall", "--host", "cursor"]) == 0
    assert hashlib.sha256(hooks.read_bytes()).hexdigest() == digest


def test_uninstall_removes_a_hooks_file_install_created(tmp_path: Path, monkeypatch, capsys) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    hooks = lab / ".cursor" / "hooks.json"
    runtime = home / "Heliopause" / "connectors" / "runtime"
    assert not hooks.exists()
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 0
    installed = capsys.readouterr().out
    assert plugin.PLAN_CREATE.format(path=str(hooks)) in installed
    assert "heliopause-bak" not in installed
    assert hooks.is_file()
    assert list((lab / ".cursor").glob("hooks.json.heliopause-bak-*")) == []
    assert plugin.run(["uninstall", "--host", "cursor"]) == 0
    assert capsys.readouterr().out == (
        "cursor: removed Heliopause's hook entries from "
        f"{hooks} and deleted {runtime}. Other entries are unchanged.\n"
    )
    assert not hooks.exists()
    assert not runtime.exists()
    assert list(lab.rglob("*heliopause-bak*")) == []


def test_created_file_install_uninstall_twice_leaves_no_backup(tmp_path: Path, monkeypatch, capsys) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    hooks = lab / ".cursor" / "hooks.json"
    for _ in range(2):
        assert not hooks.exists()
        assert plugin.run(["install", "--host", "cursor", "--yes"]) == 0
        assert plugin.PLAN_CREATE.format(path=str(hooks)) in capsys.readouterr().out
        assert hooks.is_file()
        assert list(lab.rglob("*heliopause-bak*")) == []
        assert plugin.run(["uninstall", "--host", "cursor"]) == 0
        capsys.readouterr()
        assert not hooks.exists()
        assert not (home / "Heliopause" / "connectors" / "runtime").exists()
        assert list(lab.rglob("*heliopause-bak*")) == []


def test_third_party_entry_is_json_identical_and_uninstall_restores_bytes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    entry = {"timeout": 9, "matcher": "git", "command": "/bin/other-tool"}
    raw = (
        "{\n"
        '  "hooks": {"sessionStart": [\n'
        '    {"timeout": 9, "matcher": "git", "command": "/bin/other-tool"}\n'
        "  ]},\n"
        '  "version": 1\n'
        "}"
    )
    folder = lab / ".cursor"
    folder.mkdir()
    hooks = folder / "hooks.json"
    payload = raw.encode("utf-8")
    hooks.write_bytes(payload)
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 0
    capsys.readouterr()
    data = json.loads(hooks.read_text(encoding="utf-8"))
    assert data["hooks"]["sessionStart"][0] == entry
    for event in _CURSOR:
        ours = [item for item in data["hooks"][event] if item.get("command") == CURSOR_COMMAND]
        assert ours == [{"command": CURSOR_COMMAND}]
    assert plugin.run(["uninstall", "--host", "cursor"]) == 0
    assert hooks.read_bytes() == payload


def test_hooks_file_and_runtime_hold_only_the_expected_content(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 0
    capsys.readouterr()
    hooks = lab / ".cursor" / "hooks.json"
    runtime = home / "Heliopause" / "connectors" / "runtime"
    hook_text = hooks.read_text(encoding="utf-8")
    assert str(home) not in hook_text
    data = json.loads(hook_text)
    expected = {"command": "~/Heliopause/connectors/cursor/report.sh"}
    for event in _CURSOR:
        assert data["hooks"][event] == [expected]
    body = runtime.read_text(encoding="utf-8")
    lines = body.split("\n")
    assert lines[-1] == "" and len(lines) == 4
    paths = lines[:-1]
    assert paths == [
        os.path.abspath(sys.executable),
        str((home / "Heliopause").resolve()),
        str(lab.resolve()),
    ]
    assert all(item.startswith("/") for item in paths)
    # The hook requires an absolute root. That root is ~/Heliopause, so this one line is under HOME.
    # The hooks file, python, and the lab are not.
    assert str(home) not in paths[0]
    assert str(home) not in paths[2]
    assert paths[1].startswith(str(home.resolve()) + os.sep)


def test_uninstall_line_drops_a_missing_runtime(tmp_path: Path, monkeypatch, capsys) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    original = _seed(lab)
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 0
    capsys.readouterr()
    runtime = home / "Heliopause" / "connectors" / "runtime"
    runtime.unlink()
    hooks = lab / ".cursor" / "hooks.json"
    bak = next((lab / ".cursor").glob("hooks.json.heliopause-bak-*"))
    assert plugin.run(["uninstall", "--host", "cursor"]) == 0
    assert capsys.readouterr().out == (
        "cursor: removed Heliopause's hook entries from "
        f"{hooks}. Other entries are unchanged. Your backup is still at {bak}.\n"
    )
    assert hooks.read_bytes() == original
    assert list((lab / ".cursor").glob("hooks.json.heliopause-bak-*")) == [bak]


def test_cursor_report_logs_a_grok_session_and_writes_no_card(tmp_path: Path, monkeypatch) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=True)
    assert lab is not None
    root = home / "Heliopause"
    runtime = root / "connectors" / "runtime"
    runtime.write_text(
        f"{os.path.abspath(sys.executable)}\n{root.resolve()}\n{lab.resolve()}\n",
        encoding="utf-8",
    )
    os.chmod(runtime, 0o600)
    script = root / "connectors" / "cursor" / "report.sh"
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GROK_HOOK_EVENT": "SessionStart",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(tmp_path),
    }
    proc = subprocess.run(
        ["/bin/sh", str(script)],
        input=json.dumps({
            "hook_event_name": "sessionStart",
            "conversation_id": "abc123",
            "workspace_roots": [str(tmp_path / "work" / "myrepo")],
        }),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""
    log = (lab / "logs" / "grok-hooks.log").read_text(encoding="utf-8")
    lines = log.splitlines()
    assert len(lines) == 1
    assert "grok cursor-report SessionStart" in lines[0]
    assert not (lab / ".heliopause" / "cards").exists()


def test_install_without_a_lab_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=False, atlas_pkg=False)
    assert lab is None
    before = _files(home)
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 1
    captured = capsys.readouterr()
    assert captured.err == plugin._NO_LAB + "\n"
    assert captured.out == ""
    assert _files(home) == before
    assert not (home / "atlas-lab").exists()
    assert not (home / ".cursor").exists()
    assert plugin.run(["status", "--host", "cursor"]) == 0
    captured = capsys.readouterr()
    assert captured.out == plugin.STATUS_OFF + "\n"
    assert captured.err == ""
    assert _files(home) == before


def test_install_refuses_a_root_that_is_not_home_heliopause(tmp_path: Path, monkeypatch, capsys) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=False)
    assert lab is not None
    before_home = _files(home)
    before_lab = _files(lab)
    monkeypatch.setattr(plugin, "_root", lambda: tmp_path / "not-here")
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 1
    captured = capsys.readouterr()
    assert captured.err == plugin._UNSUPPORTED + "\n"
    assert captured.out == ""
    assert _files(home) == before_home
    assert _files(lab) == before_lab


def test_hook_command_writes_a_session_card(tmp_path: Path, monkeypatch) -> None:
    home, lab = _home_with_connectors(tmp_path, monkeypatch, lab=True, atlas_pkg=True)
    assert lab is not None
    root = str((home / "Heliopause").resolve())
    probe = subprocess.run(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import atlas.connectors", root],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.skip("copied atlas cannot import: " + probe.stderr.strip())
    assert plugin.run(["install", "--host", "cursor", "--yes"]) == 0
    hook_file = lab / ".cursor" / "hooks.json"
    hook_text = hook_file.read_text(encoding="utf-8")
    assert str(home) not in hook_text
    hooks = json.loads(hook_text)
    command = hooks["hooks"]["sessionStart"][0]["command"]
    assert command == "~/Heliopause/connectors/cursor/report.sh"
    env = {key: value for key, value in os.environ.items() if not key.startswith("GROK_")}
    env["HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        ["/bin/sh", "-c", command],
        input=json.dumps({
            "hook_event_name": "sessionStart",
            "conversation_id": "abc123",
            "workspace_roots": [str(tmp_path / "work" / "myrepo")],
        }),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""
    card = json.loads((lab / ".heliopause" / "cards" / "cursor" / "abc123.json").read_text(encoding="utf-8"))
    assert card["tool"] == "cursor"
    assert card["event"] == "sessionStart"
    assert card["session"] == "abc123"
    assert card["repo"] == "myrepo"
    assert card["state"] == "idle"

"""Hooks list is metadata from a tmp home. The real home is never touched."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from atlas.hookcheck import list_hooks

SENTINEL = "SENTINEL-HOOK-9c1e"
REPO = Path(__file__).resolve().parents[1]

def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")

def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    plugin = json.loads((REPO / "plugin/heliopause/hooks/hooks.json").read_text(encoding="utf-8"))
    command = plugin["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    _write(home / ".claude/settings.json", {"hooks": {
        SENTINEL: [{"command": "/bin/true"}],
        "SessionStart": [{"hooks": [
            {"type": "command", "command": f"/opt/atlas --flag {SENTINEL}"},
            {"type": "command", "command": f"/usr/local/bin/foreign-hook --flag {SENTINEL}"},
            {"type": "http", "url": f"http://127.0.0.1:4300/hook?token={SENTINEL}"},
            {"type": "command", "command": f"API_KEY={SENTINEL} env -u HOME -i TOKEN={SENTINEL} /usr/local/bin/mytool --x"},
            {"type": "command", "command": f"API_KEY={SENTINEL} mytool '"},
            {"type": "command", "command": f"{SENTINEL}!"},
            {"type": f"x{SENTINEL}"},
            {"type": "http", "url": f"http://{SENTINEL}@127.0.0.1/x"},
            {"type": "http", "url": "http://[::1]/x"},
            {"type": "http", "url": "http://bad_host/x"},
            {"type": "mcp_tool"},
            {"type": "prompt"},
            {"type": "agent"},
        ]}],
    }})
    _write(home / ".atlas/marketplace/atlas/hooks/hooks.json", {"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": command}]},
    ]}})
    _write(home / "Heliopause/marketplace/heliopause/hooks/hooks.json", {"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": f"/opt/bin/check.sh {SENTINEL}"}]},
    ]}})
    _write(home / ".codex/hooks.json", {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": f"python3 -m atlas connector report --tool codex {SENTINEL}"},
    ]}]}})
    _write(home / ".cursor/hooks.json", {"version": 1, "hooks": {"beforeSubmit": [
        {"command": f"/opt/cursor/foreign.sh --token {SENTINEL}"},
    ]}})
    _write(home / ".grok/hooks/heliopause.json", {"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": f"echo {SENTINEL}"}]},
    ]}})
    _write(home / ".grok/hooks/other.json", {"hooks": {"PreToolUse": [
        {"command": f"/usr/bin/other-hook {SENTINEL}"},
    ]}})
    _write(home / ".grok/hooks/nested/secret.json", {"hooks": {"X": [{"command": f"/bin/nested {SENTINEL}"}]}})
    (home / ".grok/hooks/readme.txt").write_text(SENTINEL, encoding="utf-8")
    return home

def _snap(root: Path) -> dict:
    found = {str(root): root.lstat().st_mtime_ns}
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            found[str(path)] = path.lstat().st_mtime_ns
    return found

def _problem(tool: str, file: str) -> dict:
    return {"tool": tool, "file": file, "event": None, "name": None, "ours": False, "problem": "couldn't read"}

def _line(row: dict) -> str:
    return f"{row['tool']}  {row['file']}  {row['event']}  {row['name']}  {'ours' if row['ours'] else 'not ours'}"

_EXPECTED = [
    {"tool": "claude-code", "file": "~/.atlas/marketplace/atlas/hooks/hooks.json", "event": "SessionStart", "name": "session-start.sh", "ours": True},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "?", "name": "true", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "?", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "?", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "agent", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "atlas", "ours": True},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "foreign-hook", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "http 127.0.0.1", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "http 127.0.0.1", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "http ::1", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "http ?", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "mcp_tool", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "mytool", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "mytool", "ours": False},
    {"tool": "claude-code", "file": "~/.claude/settings.json", "event": "SessionStart", "name": "prompt", "ours": False},
    {"tool": "claude-code", "file": "~/Heliopause/marketplace/heliopause/hooks/hooks.json", "event": "UserPromptSubmit", "name": "check.sh", "ours": True},
    {"tool": "codex", "file": "~/.codex/hooks.json", "event": "Stop", "name": "python3", "ours": True},
    {"tool": "cursor", "file": "~/.cursor/hooks.json", "event": "beforeSubmit", "name": "foreign.sh", "ours": False},
    {"tool": "grok", "file": "~/.grok/hooks/heliopause.json", "event": "SessionStart", "name": "echo", "ours": True},
    {"tool": "grok", "file": "~/.grok/hooks/other.json", "event": "PreToolUse", "name": "other-hook", "ours": False},
]

def _quiet(text: str) -> None:
    for needle in (SENTINEL, "CLAUDE_PLUGIN_ROOT", "4300", "?token", "--flag", "--tool", "connector", "http://", "/opt/", "/usr/"):
        assert needle not in text

def test_rows_and_cli_hide_the_sentinel(tmp_path):
    home = _home(tmp_path)
    before = _snap(home)
    rows = list_hooks(home)
    assert rows == _EXPECTED
    _quiet(json.dumps(rows))
    env = os.environ.copy()
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run([sys.executable, "-m", "atlas", "hooks-list"], cwd=REPO, env=env, capture_output=True, text=True)
    assert proc.returncode == 0
    assert proc.stdout.splitlines() == [_line(row) for row in _EXPECTED]
    _quiet(proc.stdout)
    _quiet(proc.stderr)
    assert _snap(home) == before

def test_unreadable(tmp_path):
    secret = "followed-symlink-hook"
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"hooks": {"SessionStart": [{"command": f"/bin/{secret} {SENTINEL}"}]}}), encoding="utf-8")
    raw = target.read_bytes()
    specs = [
        ("bad", ".claude/settings.json", "{" + SENTINEL, "claude-code", "~/.claude/settings.json"),
        ("big", ".codex/hooks.json", SENTINEL.encode() + b" " * (2 * 1024 * 1024), "codex", "~/.codex/hooks.json"),
        ("list", ".cursor/hooks.json", json.dumps([{"command": f"leak {SENTINEL}"}]), "cursor", "~/.cursor/hooks.json"),
    ]
    for name, rel, content, tool, shown in specs:
        home = tmp_path / name
        path = home / rel
        path.parent.mkdir(parents=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        before = _snap(home)
        rows = list_hooks(home)
        assert rows == [_problem(tool, shown)] and SENTINEL not in json.dumps(rows) and _snap(home) == before
    home = tmp_path / "link"
    link = home / ".claude/settings.json"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    before = _snap(home)
    rows = list_hooks(home)
    blob = json.dumps(rows)
    assert rows == [_problem("claude-code", "~/.claude/settings.json")]
    assert SENTINEL not in blob and secret not in blob and "elsewhere" not in blob
    assert target.read_bytes() == raw and _snap(home) == before

def test_missing_home(tmp_path):
    missing = tmp_path / "nope"
    assert list_hooks(missing) == [] and not missing.exists()

def _cursor_lab(home: Path) -> Path:
    lab = home / "atlas-lab"
    _write(lab / ".cursor/hooks.json", {"version": 1, "hooks": {"sessionStart": [
        {"command": f"{home}/Heliopause/connectors/cursor/report.sh"},
    ]}})
    return lab

_LAB_ROW = {
    "tool": "cursor", "file": "~/atlas-lab/.cursor/hooks.json", "event": "sessionStart", "name": "report.sh", "ours": True,
}

def test_lab_cursor_file_counts_connector_as_ours(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    lab = _cursor_lab(home)
    assert list_hooks(home, lab) == [_LAB_ROW]

def test_list_hooks_without_lab_skips_the_lab_file(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    _cursor_lab(home)
    assert list_hooks(home) == []

def test_symlinked_lab_hooks_file_is_unreadable(tmp_path):
    secret = "followed-symlink-hook"
    home = tmp_path / "home"
    home.mkdir()
    lab = home / "atlas-lab"
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"hooks": {"sessionStart": [{"command": f"/bin/{secret}"}]}}), encoding="utf-8")
    link = lab / ".cursor" / "hooks.json"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    rows = list_hooks(home, lab)
    blob = json.dumps(rows)
    assert rows == [_problem("cursor", "~/atlas-lab/.cursor/hooks.json")]
    assert secret not in blob and "elsewhere" not in blob

def test_home_rows_stay_beside_the_lab_file(tmp_path):
    home = _home(tmp_path)
    lab = _cursor_lab(home)
    rows = list_hooks(home, lab)
    expected = [*_EXPECTED, _LAB_ROW]
    expected.sort(key=lambda row: (row["tool"], row["file"], row["event"] or "", row["name"] or ""))
    assert rows == expected

def test_lab_that_is_home_is_not_listed_twice(tmp_path):
    home = _home(tmp_path)
    assert list_hooks(home, home) == _EXPECTED

def test_lab_outside_home_shows_the_absolute_path(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    lab = tmp_path / "outside"
    _write(lab / ".cursor/hooks.json", {"version": 1, "hooks": {"sessionStart": [
        {"command": f"{home}/Heliopause/connectors/cursor/report.sh"},
    ]}})
    shown = os.path.abspath(lab / ".cursor" / "hooks.json")
    assert list_hooks(home, lab) == [{
        "tool": "cursor", "file": shown, "event": "sessionStart", "name": "report.sh", "ours": True,
    }]

def test_codex_and_grok_connector_paths_count_as_ours(tmp_path):
    home = tmp_path / "home"
    _write(home / ".codex/hooks.json", {"hooks": {"Stop": [
        {"command": f"{home}/Heliopause/connectors/codex/report.sh"},
    ]}})
    _write(home / ".grok/hooks/other.json", {"hooks": {"PreToolUse": [
        {"command": f"{home}/Heliopause/connectors/grok/report.sh"},
    ]}})
    rows = list_hooks(home)
    assert {(row["tool"], row["ours"], row["name"]) for row in rows} == {
        ("codex", True, "report.sh"), ("grok", True, "report.sh"),
    }

"""find_tools sees CLIs, app bundles, and config paths, and never launches or reads them."""

from __future__ import annotations

import builtins
import io
import os
import stat
import subprocess
from pathlib import Path

from atlas.detect import find_tools

_ORDER = (
    ("claude-code", "Claude Code", "https://docs.claude.com/en/docs/claude-code/setup"),
    ("cursor", "Cursor", "https://cursor.com/downloads"),
    ("codex", "Codex", "https://developers.openai.com/codex/cli"),
    ("grok", "Grok Build", "https://docs.x.ai/build/overview"),
    ("gemini", "Gemini CLI", "https://github.com/google-gemini/gemini-cli"),
    ("opencode", "OpenCode", "https://opencode.ai"),
    ("aider", "Aider", "https://aider.chat/docs/install.html"),
    ("copilot", "Copilot CLI", "https://docs.github.com/copilot/how-tos/set-up/install-copilot-cli"),
)
_CLIS = ("claude", "cursor-agent", "codex", "grok", "gemini", "opencode", "aider", "copilot")
_APPS = {"claude-code": "Claude.app", "cursor": "Cursor.app", "codex": "Codex.app"}
_CONFIGS = {
    "claude-code": (".claude",),
    "cursor": (".cursor",),
    "codex": (".codex",),
    "grok": (".grok",),
    "gemini": (".gemini",),
    "opencode": (".config", "opencode"),
    "aider": (".aider.conf.yml",),
    "copilot": (".copilot",),
}


def exe(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def place(tmp_path: Path) -> tuple[Path, Path, Path]:
    return tmp_path / "bin", tmp_path / "home", tmp_path / "Applications"


def test_each_tool_found_by_cli(tmp_path):
    bindir, home, apps = place(tmp_path)
    home.mkdir()
    apps.mkdir()
    for name in _CLIS:
        exe(bindir, name)
    tools = find_tools(str(bindir), home, apps)
    assert [tool["id"] for tool in tools] == [row[0] for row in _ORDER]
    assert [Path(tool["cli"]).name for tool in tools] == list(_CLIS)
    assert all(tool["found"] is True and tool["app"] is None and tool["config_dir"] is None for tool in tools)


def test_unrelated_agent_ignored(tmp_path):
    # WHY: the real path is what counts. This file's own path must not contain the marker.
    bindir, home, apps = place(tmp_path)
    home.mkdir()
    apps.mkdir()
    agent = exe(bindir, "agent")
    assert "cursor" not in os.path.realpath(agent)
    cursor = find_tools(str(bindir), home, apps)[1]
    assert cursor["id"] == "cursor"
    assert cursor["found"] is False
    assert cursor["cli"] is None


def test_agent_realpath_counts(tmp_path):
    bindir, home, apps = place(tmp_path)
    home.mkdir()
    apps.mkdir()
    target = exe(tmp_path / "share" / "versions" / "1", "cursor-agent")
    bindir.mkdir()
    link = bindir / "agent"
    link.symlink_to(target)
    assert "cursor" not in str(link)
    assert "cursor" in os.path.realpath(link)
    cursor = find_tools(str(bindir), home, apps)[1]
    assert cursor["found"] is True
    assert cursor["cli"] == str(link)
    assert cursor["app"] is None and cursor["config_dir"] is None


def test_agent_inside_capitalised_bundle_counts(tmp_path):
    bindir, home, apps = place(tmp_path)
    home.mkdir()
    apps.mkdir()
    target = exe(tmp_path / "Cursor.app" / "Contents" / "bin", "agent")
    bindir.mkdir()
    (bindir / "agent").symlink_to(target)
    assert "cursor" not in os.path.realpath(bindir / "agent").replace("Cursor", "")
    assert find_tools(str(bindir), home, apps)[1]["found"] is True


def test_agent_under_a_cursor_named_folder_is_not_cursor(tmp_path):
    bindir, home, apps = place(tmp_path)
    home.mkdir()
    apps.mkdir()
    target = exe(tmp_path / "cursor-fan" / "bin", "agent")
    bindir.mkdir()
    (bindir / "agent").symlink_to(target)
    assert find_tools(str(bindir), home, apps)[1]["found"] is False


def test_cursor_agent_wins_over_agent(tmp_path):
    bindir, home, apps = place(tmp_path)
    home.mkdir()
    apps.mkdir()
    exe(bindir, "cursor-agent")
    target = exe(tmp_path / "share" / "cursor" / "bin", "agent")
    (bindir / "agent").symlink_to(target)
    cursor = find_tools(str(bindir), home, apps)[1]
    assert cursor["cli"] == str(bindir / "cursor-agent")


def test_app_only(tmp_path):
    bindir, home, apps = place(tmp_path)
    home.mkdir()
    for tool_id, bundle in _APPS.items():
        (apps / bundle).mkdir(parents=True)
    tools = {tool["id"]: tool for tool in find_tools(str(bindir), home, apps)}
    for tool_id, bundle in _APPS.items():
        assert tools[tool_id]["found"] is True
        assert tools[tool_id]["app"] == str(apps / bundle)
        assert tools[tool_id]["cli"] is None
        assert tools[tool_id]["config_dir"] is None
    for tool_id in ("grok", "gemini", "opencode", "aider", "copilot"):
        assert tools[tool_id]["found"] is False
        assert tools[tool_id]["app"] is None


def test_app_file_does_not_count(tmp_path):
    bindir, home, apps = place(tmp_path)
    home.mkdir()
    apps.mkdir()
    (apps / "Cursor.app").write_bytes(b"")
    cursor = find_tools(str(bindir), home, apps)[1]
    assert cursor["found"] is False
    assert cursor["app"] is None


def test_config_only(tmp_path):
    bindir, home, apps = place(tmp_path)
    apps.mkdir()
    for tool_id, parts in _CONFIGS.items():
        path = home.joinpath(*parts)
        if tool_id == "aider":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
        else:
            path.mkdir(parents=True)
    tools = {tool["id"]: tool for tool in find_tools(str(bindir), home, apps)}
    for tool_id, parts in _CONFIGS.items():
        assert tools[tool_id]["found"] is True
        assert tools[tool_id]["config_dir"] == str(home.joinpath(*parts))
        assert tools[tool_id]["cli"] is None
        assert tools[tool_id]["app"] is None


def test_nothing_found(tmp_path):
    bindir, home, apps = place(tmp_path)
    tools = find_tools(str(bindir), home, apps)
    assert tools == [
        {
            "id": tool_id,
            "name": name,
            "found": False,
            "cli": None,
            "app": None,
            "config_dir": None,
            "install_url": url,
        }
        for tool_id, name, url in _ORDER
    ]


def test_runs_no_program(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise AssertionError("ran a program")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)
    for name in ("popen", "execv", "execve", "execvp", "execvpe", "spawnv", "spawnve", "posix_spawn", "posix_spawnp", "fork"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, boom)
    bindir, home, apps = place(tmp_path)
    exe(bindir, "claude")
    (apps / "Cursor.app").mkdir(parents=True)
    (home / ".grok").mkdir(parents=True)
    tools = {tool["id"]: tool for tool in find_tools(str(bindir), home, apps)}
    assert tools["claude-code"]["found"] is True
    assert tools["cursor"]["app"] == str(apps / "Cursor.app")
    assert tools["grok"]["config_dir"] == str(home / ".grok")


def test_opens_nothing(monkeypatch, tmp_path):
    bindir, home, apps = place(tmp_path)
    exe(bindir, "claude")
    (apps / "Codex.app").mkdir(parents=True)
    config = home / ".aider.conf.yml"
    config.parent.mkdir(parents=True)
    config.write_bytes(b"not-read")
    opened = []

    def spy(file, *args, **kwargs):
        opened.append(os.fspath(file))
        raise AssertionError("opened a file")

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(os, "open", spy)
    monkeypatch.setattr(Path, "open", spy)
    monkeypatch.setattr(Path, "read_text", spy)
    monkeypatch.setattr(Path, "read_bytes", spy)
    tools = find_tools(str(bindir), home, apps)
    assert opened == []
    assert tools[0]["cli"] == str(bindir / "claude")
    assert tools[2]["app"] == str(apps / "Codex.app")
    assert tools[6]["config_dir"] == str(config)

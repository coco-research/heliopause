"""B2: a link planted in the lab never turns a user-run command into a write outside the lab.

Here: plugin rules-only and bus install (both reproduced by the review), dispatch, memory lite, the server's
board and alert appends, and the home-folder allow-list. atlas up, atlas hooks and uninstall are in
test_hooks_home.py. Each test plants a symlink or hard link the way a lab agent could, runs the command as the
user would, and checks that nothing outside the lab changed. Every test here fails on 8968225.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from atlas import bus, dispatch, init, plugin

_SETTINGS = json.dumps({"theme": "dark", "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "/bin/true"}]}]}}, indent=2) + "\n"
_RC = "# my shell startup file\nexport PATH=\"$HOME/bin:$PATH\"\n"


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A home with ~/.claude/settings.json and a shell rc file, and a fresh lab outside it."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(_SETTINGS, encoding="utf-8")
    (home / ".bashrc").write_text(_RC, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    lab = tmp_path / "lab"
    assert init.run(["--yes", "--dir", str(lab)]) == 0
    outside = tmp_path / "outside"
    outside.mkdir()
    return home, lab, outside


def _untouched(home: Path, outside: Path) -> None:
    assert (home / ".claude" / "settings.json").read_text(encoding="utf-8") == _SETTINGS
    assert (home / ".bashrc").read_text(encoding="utf-8") == _RC
    assert list(outside.iterdir()) == []


# plugin install, host blocked -> rules only (plugin.py _rules_only)

def test_plugin_rules_only_never_writes_through_a_planted_link(box, monkeypatch, capsys):
    home, lab, outside = box
    (lab / ".atlas" / "rules").mkdir(parents=True)
    (lab / ".atlas" / "rules" / "claude.md").symlink_to(home / ".claude" / "settings.json")
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    assert plugin._rules_only("claude") == 1
    assert "claude.md: it is a symlink" in capsys.readouterr().err
    _untouched(home, outside)


def test_plugin_rules_only_refuses_a_symlinked_rules_folder(box, monkeypatch):
    home, lab, outside = box
    (lab / ".atlas").mkdir(exist_ok=True)
    (lab / ".atlas" / "rules").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    assert plugin._rules_only("claude") == 1
    _untouched(home, outside)


# bus install (bus.py _install)

def test_bus_install_never_creates_a_file_through_a_dangling_link(box, monkeypatch):
    home, lab, outside = box
    (lab / "bin" / "bus-ask").unlink()
    (lab / "bin" / "bus-ask").symlink_to(outside / "planted")
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    bus.run(["install", "--lab", str(lab)])
    _untouched(home, outside)


def test_bus_install_never_writes_into_a_symlinked_bin(box, monkeypatch):
    home, lab, outside = box
    for path in (lab / "bin").iterdir():
        path.unlink()
    (lab / "bin").rmdir()
    (lab / "bin").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    bus.run(["install", "--lab", str(lab)])
    _untouched(home, outside)


# dispatch (dispatch.py _record)

def test_dispatch_never_appends_through_a_symlinked_usage_folder(tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "root" / ".heliopause" / "inbox" / "lab").mkdir(parents=True)
    (tmp_path / "root" / ".heliopause" / "inbox" / "lab" / "usage").symlink_to(outside, target_is_directory=True)
    dispatch._record(tmp_path / "root", "review", "a story", "job-1", None, [], False)
    assert list(outside.iterdir()) == []
    assert "route not recorded" in capsys.readouterr().err


# memory lite (memory_lite.py install); left out of the public build, fixed anyway

def test_memory_install_never_writes_through_a_link(box):
    from atlas import memory

    if not memory.available():
        pytest.skip("this build has no memory lite")
    home, lab, outside = box
    (lab / "bin" / "m0-decide").symlink_to(home / ".bashrc")
    assert memory.install(lab) is False
    _untouched(home, outside)


# the server's board and alert appends (write.py, alerts.py)

def test_server_board_append_refuses_a_hard_linked_board(tmp_path):
    from atlas.write import _append

    inbox = tmp_path / ".heliopause" / "inbox" / "lab"
    inbox.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n", encoding="utf-8")
    os.link(victim, inbox / "board.md")
    status, _body = _append(tmp_path, "VP", "working", "Hello there", "more here")
    assert status == 503
    assert victim.read_text(encoding="utf-8") == "keep\n"


def test_server_alert_append_refuses_a_hard_linked_log(tmp_path):
    from atlas import alerts

    (tmp_path / ".atlas").mkdir(mode=0o700)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n", encoding="utf-8")
    os.link(victim, tmp_path / ".atlas" / "alerts.jsonl")
    record = {"ts": 1, "id": "a1b2c3", "source": "lab", "kind": "review", "severity": "info", "text": "x", "state": "open"}
    status, _body = alerts._commit(tmp_path, record, "a1b2c3", True)
    assert status == 500
    assert victim.read_text(encoding="utf-8") == "keep\n"


# home-folder files go through labfs.home(): an explicit allow-list, your own real folders

def test_plugin_hosts_record_is_never_written_through_a_link(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".atlas").mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_text("keep\n", encoding="utf-8")
    (home / ".atlas" / "plugin-hosts").symlink_to(victim)
    monkeypatch.setenv("HOME", str(home))
    assert plugin._save_hosts(["claude"]) == 1
    assert victim.read_text(encoding="utf-8") == "keep\n"

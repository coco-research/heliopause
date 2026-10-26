"""B2 and N1 for atlas up, atlas hooks and atlas uninstall.

atlas up and atlas hooks were reproduced by the review; the uninstall test is L7. N1: the hook scripts that
~/.claude/settings.json runs live in ~/.atlas/hooks (yours, 0700), check that before they run, and run no
script from the lab. Every test here fails on 8968225, except the two marked "guard".
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from atlas import hooks, init, labfs, lifecycle
from atlas.__main__ import main

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


# atlas up -> echo job (lifecycle.py echo_job, _board)

def test_atlas_up_echo_job_never_overwrites_through_the_queue(box):
    home, lab, outside = box
    (lab / "queue" / "inbox" / "echo-hello.md").symlink_to(home / ".claude" / "settings.json")
    with pytest.raises(labfs.Refused):
        lifecycle.echo_job(lab)
    _untouched(home, outside)


def test_atlas_up_never_appends_through_a_board_link(box):
    home, lab, outside = box
    (lab / "board.md").unlink()
    (lab / "board.md").symlink_to(home / ".bashrc")
    with pytest.raises(labfs.Refused):
        lifecycle.echo_job(lab)
    _untouched(home, outside)


def test_atlas_up_never_moves_the_job_into_a_symlinked_folder(box):
    home, lab, outside = box
    (lab / "queue" / "done").rmdir()
    (lab / "queue" / "done").symlink_to(outside, target_is_directory=True)
    with pytest.raises(labfs.Refused):
        lifecycle.echo_job(lab)
    _untouched(home, outside)


def test_atlas_up_reports_the_refusal_and_still_starts(box, monkeypatch, capsys):
    home, lab, outside = box
    (lab / "board.md").unlink()
    (lab / "board.md").symlink_to(home / ".bashrc")
    import atlas.__main__ as entry

    started = []
    monkeypatch.setattr(entry, "main", lambda argv: started.append(argv))
    assert lifecycle.up(["--lab", str(lab), "--port", "0"]) == 0
    assert started and "echo job skipped: refusing" in capsys.readouterr().err
    _untouched(home, outside)


def test_board_line_never_appends_through_a_hard_link(box):
    home, lab, outside = box
    (lab / "board.md").unlink()
    os.link(home / ".bashrc", lab / "board.md")
    with pytest.raises(labfs.Refused):
        lifecycle._board(lab, "x · working · y")
    _untouched(home, outside)


# atlas hooks (hooks.py)

def test_hooks_never_write_into_a_symlinked_bin(box):
    home, lab, outside = box
    for path in (lab / "bin").iterdir():
        path.unlink()
    (lab / "bin").rmdir()
    (lab / "bin").symlink_to(outside, target_is_directory=True)
    hooks.run(["--lab", str(lab), "--yes"])
    assert list(outside.iterdir()) == []
    assert (home / ".bashrc").read_text(encoding="utf-8") == _RC


def test_hooks_never_copy_settings_into_a_symlinked_backups_folder(box):
    home, lab, outside = box
    (lab / "backups").symlink_to(outside, target_is_directory=True)
    assert hooks.run(["--lab", str(lab), "--yes"]) == 1
    _untouched(home, outside)


# atlas uninstall (lifecycle.py _unhook, L7)

def test_uninstall_restores_settings_at_0600_whatever_the_lab_backup_mode(box):
    home, lab, outside = box
    settings = home / ".claude" / "settings.json"
    settings.chmod(0o600)
    assert hooks.run(["--lab", str(lab), "--yes"]) == 0
    backup = next((lab / "backups").glob("settings.json.*"))
    backup.chmod(0o666)  # an agent can do this: the backups folder is in the lab
    assert lifecycle.uninstall(["--lab", str(lab), "--yes"]) == 0
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    assert json.loads(settings.read_text(encoding="utf-8")) == json.loads(_SETTINGS)


# N1: the hook scripts live in ~/.atlas/hooks (yours, 0700), never in the lab

def test_hook_scripts_live_owner_only_outside_the_lab(box):
    home, lab, _outside = box
    assert hooks.run(["--lab", str(lab), "--yes"]) == 0
    folder = home / ".atlas" / "hooks"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700 and folder.stat().st_uid == os.getuid()
    data = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [h["command"] for groups in data["hooks"].values() for g in groups for h in g["hooks"]]
    for rel in hooks.hook_rels(lab):
        path = home / rel
        assert stat.S_IMODE(path.stat().st_mode) == 0o700 and path.stat().st_uid == os.getuid()
        assert str(path) in commands
    assert not any(str(lab) in command for command in commands)
    assert not (lab / "bin" / "atlas-hook-start").exists()


def test_start_hook_runs_no_script_from_the_lab(box, tmp_path):
    home, lab, _outside = box
    hooks.run(["--lab", str(lab), "--yes"])
    marker = tmp_path / "ran"
    (lab / "bin" / "feed-append").write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    start = home / hooks.hook_rels(lab)[0]
    res = subprocess.run([str(start)], cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    assert res.returncode == 0
    assert not marker.exists()
    assert "Session started" in (lab / "board.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("which, mode", [("file", 0o720), ("file", 0o702), ("folder", 0o770)])
def test_hook_refuses_to_run_unless_owner_only(box, tmp_path, which, mode):
    home, lab, _outside = box
    hooks.run(["--lab", str(lab), "--yes"])
    start = home / hooks.hook_rels(lab)[0]
    (start if which == "file" else start.parent).chmod(mode)
    before = (lab / "board.md").read_text(encoding="utf-8")
    res = subprocess.run([str(start)], cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    assert res.returncode == 0 and "not running it" in res.stderr
    assert (lab / "board.md").read_text(encoding="utf-8") == before


def test_hooks_refuse_a_hooks_folder_others_can_use(box, capsys):
    home, lab, _outside = box
    (home / ".atlas" / "hooks").mkdir(parents=True, mode=0o755)
    (home / ".atlas" / "hooks").chmod(0o755)
    assert hooks.run(["--lab", str(lab), "--yes"]) == 1
    assert "chmod 700" in capsys.readouterr().err
    assert json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8")) == json.loads(_SETTINGS)


def test_hooks_migrate_an_install_from_the_lab_bin(box, capsys):
    home, lab, _outside = box
    settings = home / ".claude" / "settings.json"
    data = json.loads(_SETTINGS)
    data["hooks"]["SessionStart"] = [{"hooks": [{"type": "command", "command": str(lab / "bin" / "atlas-hook-start")}]}]
    data["hooks"]["UserPromptSubmit"] = [{"hooks": [{"type": "command", "command": str(lab / "bin" / "atlas-recall-hook")}]}]
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["doctor", "--lab", str(lab), "--port", "1"])
    assert "hooks: FIX: the hook scripts are still in the lab" in capsys.readouterr().out
    assert exc.value.code in (0, 1)
    assert hooks.run(["--lab", str(lab), "--yes"]) == 0
    assert "moving the hook scripts out of the lab" in capsys.readouterr().out
    text = settings.read_text(encoding="utf-8")
    assert str(lab / "bin" / "atlas-hook-start") not in text and str(lab / "bin" / "atlas-recall-hook") not in text
    for command in hooks.hook_commands(lab):
        assert command in text
    assert "/bin/true" in text


def test_hooks_migration_asks_first(box, monkeypatch):
    # guard: 8968225 also writes nothing on a no.
    home, lab, _outside = box
    settings = home / ".claude" / "settings.json"
    data = json.loads(_SETTINGS)
    data["hooks"]["SessionStart"] = [{"hooks": [{"type": "command", "command": str(lab / "bin" / "atlas-hook-start")}]}]
    old = json.dumps(data, indent=2) + "\n"
    settings.write_text(old, encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert hooks.run(["--lab", str(lab)]) == 1
    assert settings.read_text(encoding="utf-8") == old
    assert not (home / ".atlas" / "hooks").exists()


def test_uninstall_removes_this_labs_hook_scripts(box):
    # guard: 8968225 never made ~/.atlas/hooks, so it passes there too.
    home, lab, _outside = box
    assert hooks.run(["--lab", str(lab), "--yes"]) == 0
    assert lifecycle.uninstall(["--lab", str(lab), "--yes"]) == 0
    assert not (home / ".atlas").exists()
    assert "atlas-hook" not in (home / ".claude" / "settings.json").read_text(encoding="utf-8")


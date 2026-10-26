"""Tests for atlas hooks Claude Code connection."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from atlas import hooks, init, labfs, memory
from atlas.__main__ import main

_TMP = re.compile(r"\.atlas-[0-9a-f]{12}$")


def _hook(lab, which=0):
    """The hook script path in ~/.atlas/hooks for this lab (0: start, 1: recall)."""
    return Path.home() / hooks.hook_rels(lab)[which]


def _cmd(lab, which=0):
    return hooks.hook_commands(lab)[which]


def _fail_write(monkeypatch, name):
    """Writing name fails (disk full) after the files before it were written."""
    real = labfs.write

    def write(root, rel, data, mode=None, **k):
        if str(rel) == name:
            raise OSError(28, "No space left on device", str(Path(root) / rel))
        return real(root, rel, data, mode, **k)

    monkeypatch.setattr(hooks.labfs, "write", write)


@pytest.fixture
def lab_env(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    return fake_home, lab


def test_missing_atlas_json(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    with pytest.raises(SystemExit) as exc:
        hooks.run(["--lab", str(tmp_path / "nonexistent")])
    assert exc.value.code == 2


def test_no_tty_no_flags_absent(lab_env, monkeypatch):
    fake_home, lab = lab_env
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert hooks.run(["--lab", str(lab)]) == 1
    assert not (fake_home / ".claude" / "settings.json").exists()
    assert not (lab / "backups").exists()


def test_no_tty_no_flags_present(lab_env, monkeypatch):
    fake_home, lab = lab_env
    settings = fake_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"env": {"A": "1"}, "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "/bin/echo"}]}]}}, indent=2) + "\n"
    settings.write_text(content, encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    hooks.run(["--lab", str(lab)])
    assert settings.read_text(encoding="utf-8") == content
    assert not (lab / "backups").exists()


def test_yes_preserves_existing_adds_once_backup(lab_env):
    fake_home, lab = lab_env
    settings = fake_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    orig_data = {
        "theme": "dark",
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "/bin/existing-start"}]}],
        },
    }
    settings.write_text(json.dumps(orig_data, indent=2) + "\n", encoding="utf-8")

    hooks.run(["--lab", str(lab), "--yes"])

    backups = list((lab / "backups").glob("settings.json.*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == orig_data

    updated = json.loads(settings.read_text(encoding="utf-8"))
    assert updated["theme"] == "dark"
    session_cmds = [h["command"] for g in updated["hooks"]["SessionStart"] for h in g["hooks"]]
    assert "/bin/existing-start" in session_cmds
    assert _cmd(lab, 0) in session_cmds
    recall_cmds = [h["command"] for g in updated["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert _cmd(lab, 1) in recall_cmds
    assert session_cmds.count(_cmd(lab, 0)) == 1
    assert recall_cmds.count(_cmd(lab, 1)) == 1

    # running --yes again adds nothing
    hooks.run(["--lab", str(lab), "--yes"])
    updated2 = json.loads(settings.read_text(encoding="utf-8"))
    assert updated2 == updated
    assert len(list((lab / "backups").glob("settings.json.*"))) == 1


def test_invalid_settings_json_exits_2(lab_env):
    fake_home, lab = lab_env
    settings = fake_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    raw = '{"hooks": [unclosed'
    settings.write_text(raw, encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        hooks.run(["--lab", str(lab), "--yes"])
    assert exc.value.code == 2
    assert settings.read_text(encoding="utf-8") == raw
    assert not (lab / "backups").exists()


def test_snippet_writes_claude_hooks_json_and_leaves_settings_untouched(lab_env):
    fake_home, lab = lab_env
    settings = fake_home / ".claude" / "settings.json"
    hooks.run(["--lab", str(lab), "--snippet", "--yes"])
    assert not settings.exists()
    assert not (lab / "backups").exists()

    snippet_file = lab / "claude-hooks.json"
    assert snippet_file.is_file()
    data = json.loads(snippet_file.read_text(encoding="utf-8"))
    assert data["hooks"]["SessionStart"] == [{"hooks": [{"type": "command", "command": _cmd(lab, 0)}]}]
    assert data["hooks"]["UserPromptSubmit"] == [{"hooks": [{"type": "command", "command": _cmd(lab, 1)}]}]


def test_scripts_behavior(lab_env, tmp_path):
    _, lab = lab_env
    hooks.run(["--lab", str(lab), "--snippet", "--yes"])

    # atlas-recall-hook without m0-capture-hook exits 0 with no output
    res = subprocess.run([str(_hook(lab, 1))], capture_output=True, text=True)
    assert res.returncode == 0
    assert res.stdout == ""
    assert res.stderr == ""

    # atlas-hook-start exits 0 even when Atlas isn't running, and appends exactly one board line
    board = lab / "board.md"
    before = board.read_text(encoding="utf-8").splitlines()
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    res = subprocess.run([str(_hook(lab, 0))], cwd=str(workdir), capture_output=True, text=True)
    assert res.returncode == 0
    after = board.read_text(encoding="utf-8").splitlines()
    assert len(after) == len(before) + 1
    assert " · Lead · working · Session started · workdir" in after[-1]


def test_odd_lab_path_is_quoted(tmp_path, monkeypatch):
    # A path with a space, quote and $ must not break or inject into the generated scripts.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    lab = tmp_path / 'la b"$(touch pwned)'
    init.run(["--yes", "--dir", str(lab)])
    hooks.run(["--lab", str(lab), "--snippet", "--yes"])
    res = subprocess.run([str(_hook(lab, 0))], cwd=str(tmp_path), capture_output=True, text=True)
    assert res.returncode == 0
    assert "Session started" in (lab / "board.md").read_text(encoding="utf-8")
    assert not (tmp_path / "pwned").exists()


@pytest.mark.skipif(not memory.available(), reason="memory lite is not included in this build")
def test_m0_capture_hook_execution(lab_env):
    fake_home, lab = lab_env
    hooks.run(["--lab", str(lab), "--snippet", "--yes"])

    # Dev builds: the hook runs the owner-only copies in ~/.atlas/hooks/m0, when the lab has memory lite.
    (lab / "bin" / "m0-decide").write_text("# memory lite installed\n", encoding="utf-8")
    tools = fake_home / ".atlas" / "hooks" / "m0"
    tools.mkdir(mode=0o700)
    for name in ("m0-decide", "m0-recall", "m0lite_capture.py", "m0lite_recall_lanes.py"):
        (tools / name).write_text("", encoding="utf-8")
        (tools / name).chmod(0o600)
    capture = tools / "m0-capture-hook"
    capture.write_text(
        "#!/usr/bin/env bash\necho \"LAB=$ATLAS_LAB OWNER=${M0_OWNER:-unset} DECIDE=$M0_DECIDE_BIN\"\ncat\n",
        encoding="utf-8",
    )
    capture.chmod(0o700)

    res = subprocess.run(
        [str(_hook(lab, 1))],
        input="user prompt text\n",
        env={"PATH": os.environ.get("PATH", ""), "M0_OWNER": "secret-owner"},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert f"LAB={lab} OWNER=unset DECIDE={tools / 'm0-decide'}" in res.stdout
    assert "user prompt text" in res.stdout


def test_agents_snippet_written(lab_env):
    _, lab = lab_env
    hooks.run(["--lab", str(lab), "--snippet", "--yes"])
    agents_file = lab / "AGENTS-snippet.md"
    assert agents_file.is_file()
    text = agents_file.read_text(encoding="utf-8")
    assert f'{lab}/bin/atlas recall "<question>"' in text
    assert f'{lab}/bin/atlas decide "<what was decided>"' in text
    assert f"`{lab}/bin/feed-append --board ...` (see {lab}/BUS.md)." in text


def test_interactive_prompt_yes_and_no(lab_env, monkeypatch):
    fake_home, lab = lab_env
    settings = fake_home / ".claude" / "settings.json"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    hooks.run(["--lab", str(lab)])
    assert not settings.exists()

    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    hooks.run(["--lab", str(lab)])
    assert settings.exists()


def test_main_subcommand_dispatch(lab_env):
    fake_home, lab = lab_env
    with pytest.raises(SystemExit) as exc:
        main(["hooks", "--lab", str(lab), "--yes"])
    assert exc.value.code == 0
    assert (fake_home / ".claude" / "settings.json").exists()


def test_main_hooks_refusal_exits_nonzero(lab_env, monkeypatch):
    fake_home, lab = lab_env
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        main(["hooks", "--lab", str(lab)])
    assert exc.value.code == 1
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(SystemExit) as exc:
        main(["hooks", "--lab", str(lab)])
    assert exc.value.code == 1
    assert not (fake_home / ".claude" / "settings.json").exists()


def test_main_hooks_already_connected_exits_zero(lab_env):
    _, lab = lab_env
    hooks.run(["--lab", str(lab), "--yes"])
    with pytest.raises(SystemExit) as exc:
        main(["hooks", "--lab", str(lab)])
    assert exc.value.code == 0


def test_cli_hooks_declined_exits_nonzero(lab_env):
    fake_home, lab = lab_env
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, HOME=str(fake_home))
    res = subprocess.run([sys.executable, "-B", "-m", "atlas", "hooks", "--lab", str(lab)], cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    assert res.returncode == 1
    assert not (fake_home / ".claude" / "settings.json").exists()


def _snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


def _raises(exc):
    def ask(prompt):
        raise exc
    return ask


@pytest.mark.parametrize(
    "tty, answer",
    [(True, "n"), (True, ""), (False, None), (True, EOFError), (True, KeyboardInterrupt)],
    ids=["n", "enter", "no-tty", "eof", "ctrl-c"],
)
@pytest.mark.parametrize("snippet", [False, True], ids=["settings", "snippet"])
def test_refusal_writes_nothing(lab_env, monkeypatch, tty, answer, snippet):
    fake_home, lab = lab_env
    before_lab, before_home = _snapshot(lab), _snapshot(fake_home)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
    if isinstance(answer, type):
        monkeypatch.setattr("builtins.input", _raises(answer))
    elif answer is not None:
        monkeypatch.setattr("builtins.input", lambda prompt: answer)
    assert hooks.run(["--lab", str(lab)] + (["--snippet"] if snippet else [])) == 1
    assert _snapshot(lab) == before_lab
    assert _snapshot(fake_home) == before_home
    assert not _hook(lab).exists()
    assert not (lab / "AGENTS-snippet.md").exists()


def test_refusal_keeps_a_missing_wrapper_missing(lab_env, monkeypatch):
    _, lab = lab_env
    (lab / "bin" / "atlas").unlink()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    hooks.run(["--lab", str(lab)])
    assert not (lab / "bin" / "atlas").exists()
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    hooks.run(["--lab", str(lab)])
    assert (lab / "bin" / "atlas").is_file()
    assert _hook(lab).is_file()
    assert (lab / "AGENTS-snippet.md").is_file()


def test_connected_and_current_writes_nothing_without_asking(lab_env, monkeypatch):
    fake_home, lab = lab_env
    hooks.run(["--lab", str(lab), "--yes"])
    before = _snapshot(lab)

    def no_prompt(prompt):
        raise AssertionError("asked when nothing changes")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", no_prompt)
    assert hooks.run(["--lab", str(lab)]) == 0
    assert _snapshot(lab) == before


def test_failed_lab_write_rolls_back_and_exits_nonzero(lab_env, monkeypatch, capsys):
    # One target is a folder, so its write fails after the hook scripts were already written.
    fake_home, lab = lab_env
    _fail_write(monkeypatch, "AGENTS-snippet.md")
    before_lab, before_home = _snapshot(lab), _snapshot(fake_home)
    assert hooks.run(["--lab", str(lab), "--yes"]) == 1
    assert "nothing changed" in capsys.readouterr().err
    assert _snapshot(lab) == before_lab
    assert _snapshot(fake_home) == before_home
    assert not any(_TMP.search(p.name) for p in [*lab.rglob("*"), *fake_home.rglob("*")])


def test_failed_lab_write_restores_a_replaced_file(lab_env, monkeypatch):
    _, lab = lab_env
    hooks.run(["--lab", str(lab), "--yes"])
    start = _hook(lab)
    start.write_text("#!/bin/sh\n# an older hook\n", encoding="utf-8")
    (lab / "AGENTS-snippet.md").write_text("older\n", encoding="utf-8")
    _fail_write(monkeypatch, "AGENTS-snippet.md")
    assert hooks.run(["--lab", str(lab), "--yes"]) == 1
    assert start.read_text(encoding="utf-8") == "#!/bin/sh\n# an older hook\n"


def test_failed_settings_write_leaves_no_marker(lab_env):
    # ~/.claude is a file: refused before anything is written.
    fake_home, lab = lab_env
    (fake_home / ".claude").write_text("not a folder\n", encoding="utf-8")
    before_lab, before_home = _snapshot(lab), _snapshot(fake_home)
    assert hooks.run(["--lab", str(lab), "--yes"]) == 1
    assert not (lab / "backups" / "created-settings").exists()
    assert _snapshot(lab) == before_lab
    assert _snapshot(fake_home) == before_home


def test_created_settings_marker_only_after_settings_written(lab_env, monkeypatch):
    fake_home, lab = lab_env
    settings = fake_home / ".claude" / "settings.json"
    seen = []
    real = labfs.write

    def spy(root, rel, data, mode=None, **k):
        if str(rel).endswith("created-settings"):
            seen.append(settings.is_file())
        return real(root, rel, data, mode, **k)

    monkeypatch.setattr(hooks.labfs, "write", spy)
    assert hooks.run(["--lab", str(lab), "--yes"]) == 0
    assert seen == [True]
    assert (lab / "backups" / "created-settings").is_file()
    assert settings.stat().st_mode & 0o777 == 0o600


def test_snippet_write_failure_rolls_back(lab_env):
    _, lab = lab_env
    (lab / "claude-hooks.json").mkdir()
    before = _snapshot(lab)
    assert hooks.run(["--lab", str(lab), "--snippet", "--yes"]) == 1
    assert _snapshot(lab) == before


def test_snippet_asks_before_writing(lab_env, monkeypatch, capsys):
    fake_home, lab = lab_env
    before_lab, before_home = _snapshot(lab), _snapshot(fake_home)
    # Non-interactive without --yes: refuses, exits 1, writes nothing.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert hooks.run(["--lab", str(lab), "--snippet"]) == 1
    assert "re-run with --yes" in capsys.readouterr().err
    assert _snapshot(lab) == before_lab and _snapshot(fake_home) == before_home
    # Interactive: the plan names every file, and only "y" writes.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert hooks.run(["--lab", str(lab), "--snippet"]) == 1
    out = capsys.readouterr().out
    for name in ("AGENTS-snippet.md", "claude-hooks.json"):
        assert f"plan: write {lab / name}" in out
    for which in (0, 1):
        assert f"plan: write {_hook(lab, which)}" in out
    assert _snapshot(lab) == before_lab and _snapshot(fake_home) == before_home
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert hooks.run(["--lab", str(lab), "--snippet"]) == 0
    assert (lab / "claude-hooks.json").is_file()
    assert not (fake_home / ".claude" / "settings.json").exists()


def test_snippet_current_writes_nothing_without_asking(lab_env, monkeypatch):
    _, lab = lab_env
    hooks.run(["--lab", str(lab), "--snippet", "--yes"])
    before = _snapshot(lab)

    def no_prompt(prompt):
        raise AssertionError("asked when nothing changes")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", no_prompt)
    assert hooks.run(["--lab", str(lab), "--snippet"]) == 0
    assert _snapshot(lab) == before


def test_failed_rollback_names_the_files_and_does_not_claim_nothing_changed(lab_env, monkeypatch, capsys):
    # The run fails on one target, and undoing an earlier write fails too.
    fake_home, lab = lab_env
    _fail_write(monkeypatch, "AGENTS-snippet.md")
    real_unlink = os.unlink
    stuck = []

    def unlink(path, *a, **k):
        if not _TMP.search(str(path).rsplit("/", 1)[-1]):
            stuck.append(str(path))
            raise PermissionError(13, "Permission denied", str(path))
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(hooks.os, "unlink", unlink)
    assert hooks.run(["--lab", str(lab), "--yes"]) == 1
    err = capsys.readouterr().err
    assert "nothing changed" not in err
    assert "may be left changed" in err
    assert stuck
    for path in stuck:
        assert path in err


def test_rollback_reports_nothing_when_it_succeeds(tmp_path):
    batch = hooks._Batch()
    batch.write(tmp_path, "a.txt", "x", 0o644)
    assert batch.rollback() == []
    assert not (tmp_path / "a.txt").exists()


def _fail_first_replace(monkeypatch):
    # The first publish of a written file (a link for a new one, a rename for an existing one) fails.
    calls = []

    def failing(real):
        def publish(src, dst, *a, **k):
            calls.append(dst)
            if len(calls) == 1:
                raise OSError(28, "No space left on device", str(dst))
            return real(src, dst, *a, **k)
        return publish

    monkeypatch.setattr(labfs.os, "replace", failing(os.replace))
    monkeypatch.setattr(labfs.os, "link", failing(os.link))


def _stuck_tmp_unlink(monkeypatch, times):
    real_unlink = os.unlink
    left = {"n": times}

    def unlink(path, *a, **k):
        if _TMP.search(str(path).rsplit("/", 1)[-1]) and left["n"] != 0:
            left["n"] -= 1
            raise PermissionError(13, "Permission denied", str(path))
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(hooks.os, "unlink", unlink)


def test_stuck_temp_cleanup_is_listed_and_the_original_error_kept(lab_env, monkeypatch, capsys):
    fake_home, lab = lab_env
    _fail_first_replace(monkeypatch)
    _stuck_tmp_unlink(monkeypatch, -1)
    assert hooks.run(["--lab", str(lab), "--yes"]) == 1
    err = capsys.readouterr().err
    assert "No space left on device" in err
    assert "nothing changed" not in err
    leftovers = [p for p in [*lab.rglob("*"), *fake_home.rglob("*")] if _TMP.search(p.name)]
    assert leftovers
    for path in leftovers:
        assert f"{path} (temporary file left behind" in err


def test_temp_cleanup_failing_once_is_retried_and_nothing_is_left(lab_env, monkeypatch, capsys):
    fake_home, lab = lab_env
    before = _snapshot(lab)
    _fail_first_replace(monkeypatch)
    _stuck_tmp_unlink(monkeypatch, 1)
    assert hooks.run(["--lab", str(lab), "--yes"]) == 1
    err = capsys.readouterr().err
    assert "No space left on device" in err and "Permission denied" not in err
    assert not [p for p in [*lab.rglob("*"), *fake_home.rglob("*")] if _TMP.search(p.name)]
    assert _snapshot(lab) == before


def test_labfs_write_keeps_the_original_error_when_cleanup_fails(tmp_path, monkeypatch):
    # L6: the hooks rule, now in labfs. The cleanup error is attached, never raised in place of the first.
    _fail_first_replace(monkeypatch)
    _stuck_tmp_unlink(monkeypatch, -1)
    with pytest.raises(OSError) as info:
        labfs.write(tmp_path, "f", b"x", 0o644)
    assert info.value.errno == 28
    left = info.value.atlas_leftovers
    assert left and left[0][0].startswith(str(tmp_path)) and isinstance(left[0][1], PermissionError)

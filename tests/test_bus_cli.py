"""`atlas bus ask/close/check/look` are package code: they never run DIR/bin/* and never follow links in the lab."""

import json
import os

import pytest

from atlas import bus, dispatch, init

_EVIL = "#!/bin/sh\ntouch \"{marker}\"\nexit 0\n"


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("ATLAS_LAB", raising=False)
    path = tmp_path / "lab"
    assert init.run(["--yes", "--dir", str(path)]) == 0
    return path


def _plant(lab, marker):
    for name in ("bus-ask", "bus-check", "lab-ops-check", "feed-append"):
        script = lab / "bin" / name
        script.write_text(_EVIL.format(marker=marker), encoding="utf-8")
        script.chmod(0o755)


@pytest.mark.parametrize("verb", ["ask", "check", "look", "close"])
def test_bus_never_runs_lab_scripts(lab, tmp_path, verb):
    marker = tmp_path / "ran"
    _plant(lab, marker)
    if verb == "ask":
        assert bus.run(["ask", "--lab", str(lab), "owner", "Pick the layout"]) == 0
        files = list((lab / "inbox").glob("*.md"))
        assert len(files) == 1 and "title: Pick the layout" in files[0].read_text(encoding="utf-8")
    elif verb == "close":
        assert bus.run(["ask", "--lab", str(lab), "owner", "Pick"]) == 0
        ask_id = next((lab / "inbox").glob("*.md")).stem
        assert bus.run(["close", "--lab", str(lab), ask_id]) == 0
        assert (lab / "done" / f"{ask_id}.ask.md").is_file()
    else:
        assert bus.run([verb, "--lab", str(lab)]) == 0
    assert not marker.exists()


def test_bus_ask_refuses_a_linked_inbox(lab, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.rmdir(lab / "inbox")
    (lab / "inbox").symlink_to(outside, target_is_directory=True)
    assert bus.run(["ask", "--lab", str(lab), "owner", "Pick"]) != 0
    assert list(outside.iterdir()) == []


def test_bus_check_does_not_follow_a_linked_board(lab, tmp_path, capsys):
    outside = tmp_path / "board.md"
    outside.write_text("2026-10-20 08:00 · W · working · Fine\n", encoding="utf-8")
    (lab / "board.md").unlink()
    (lab / "board.md").symlink_to(outside)
    assert bus.run(["check", "--lab", str(lab)]) == 1
    out = capsys.readouterr().out
    assert "FAIL  board.md is a symlink; not followed" in out
    assert "board.md lines valid" not in out


def test_bus_check_reports_a_hard_linked_queue_file(lab, tmp_path, capsys):
    outside = tmp_path / "secret.md"
    outside.write_text("---\nid: x\n---\n", encoding="utf-8")
    os.link(outside, lab / "inbox" / "x.md")
    assert bus.run(["check", "--lab", str(lab)]) == 1
    assert "inbox/x.md: not read" in capsys.readouterr().out


def test_bus_check_matches_the_agent_script(lab):
    import subprocess

    with (lab / "board.md").open("a", encoding="utf-8") as fh:
        fh.write("bad line\n2026-10-20 08:00 · W · sleeping · Hm\n")
    with (lab / "feed.md").open("a", encoding="utf-8") as fh:
        fh.write("## not a header\n")
    (lab / "briefs" / "b1.md").write_text("---\nid: b1\nrepo: r\nbranch: main\nmodel: m\n---\n", encoding="utf-8")
    script = subprocess.run([str(lab / "bin" / "bus-check")], env={"LAB_ROOT": str(lab), "PATH": os.environ["PATH"]},
                            capture_output=True, text=True)
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = bus.run(["check", "--lab", str(lab)])
    assert code == script.returncode == 1
    assert buf.getvalue() == script.stdout


def test_dispatch_refuses_routes_inside_a_lab(lab, capsys):
    usage = lab / ".heliopause" / "inbox" / "lab" / "usage"
    usage.mkdir(parents=True)
    (usage / "routes.json").write_text(json.dumps({"v": 1, "types": {"code": {"lanes": []}}}), encoding="utf-8")
    assert dispatch._table(lab) is None
    assert "inside the lab" in capsys.readouterr().err


def test_dispatch_refuses_a_heliopause_linked_into_a_lab(lab, tmp_path, capsys):
    root = tmp_path / "root"
    root.mkdir()
    usage = lab / "queue" / "h" / "inbox" / "lab" / "usage"
    usage.mkdir(parents=True)
    (usage / "routes.json").write_text(json.dumps({"v": 1, "types": {}}), encoding="utf-8")
    (root / ".heliopause").symlink_to(lab / "queue" / "h", target_is_directory=True)
    assert dispatch._table(root) is None
    assert "overlaps the lab" in capsys.readouterr().err


def test_dispatch_refuses_a_heliopause_that_contains_a_lab(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".heliopause" / "inbox" / "lab" / "usage").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ATLAS_LAB", str(home / ".heliopause" / "inbox"))
    (home / ".heliopause" / "inbox" / "lab" / "usage" / "routes.json").write_text('{"v": 1, "types": {}}', encoding="utf-8")
    assert dispatch._table(home) is None
    assert "lab" in capsys.readouterr().err


def test_dispatch_refuses_routes_writable_by_others(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    usage = home / ".heliopause" / "inbox" / "lab" / "usage"
    usage.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ATLAS_LAB", raising=False)
    routes = usage / "routes.json"
    routes.write_text('{"v": 1, "types": {}}', encoding="utf-8")
    assert dispatch._table(home) == {"v": 1, "types": {}}
    routes.chmod(0o666)
    assert dispatch._table(home) is None
    assert "writable by other users" in capsys.readouterr().err


def test_shown_drops_c0_c1_and_del_but_keeps_tab_and_newline():
    raw = "a\x1b]0;owned\x07b\x9b31mc\x7fd\re\tf\ng\x00h\x85i"
    assert bus._shown(raw) == "a]0;ownedb31mcde\tf\nghi"


def test_look_and_check_never_print_terminal_escapes(lab, capsys):
    board = lab / "board.md"
    evil = "2026-10-21 10:00 · Worker\x1b[2J · blocked · stuck \x1b]8;;http://x\x07link\x9b31m red\tok"
    board.write_text(board.read_text(encoding="utf-8") + evil + "\n", encoding="utf-8")
    (lab / "inbox" / "bad\x1b[31m.md").write_text("no front matter\x1b[0m\n", encoding="utf-8")
    assert bus.run(["look", "--lab", str(lab)]) == 0
    bus.run(["check", "--lab", str(lab)])
    out = capsys.readouterr().out
    assert "ATTENTION Worker[2J is blocked" in out and "\tok" in out
    assert not any(char in out for char in "\x1b\x07\x9b")

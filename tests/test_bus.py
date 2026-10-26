"""Bus scripts and `atlas bus`, against temporary labs only."""

import os
import subprocess
from pathlib import Path

import pytest

from atlas import bus, init

_BANNED = ("lab-inbox", ".heliopause", "/Users/")
_TEMPLATE = Path(__file__).resolve().parents[1] / "atlas" / "templates"


def _env(lab):
    return {"LAB_ROOT": str(lab), "PATH": os.environ.get("PATH", ""), "HOME": str(lab)}


def _run(lab, name, args, **kwargs):
    return subprocess.run(
        [str(lab / "bin" / name), *args],
        env=_env(lab),
        capture_output=True,
        text=True,
        **kwargs,
    )


def test_scripts_against_fixture_lab(tmp_path):
    lab = tmp_path / "lab"
    assert init.run(["--yes", "--dir", str(lab)]) == 0
    for name in ("bus-check", "lab-ops-check", "bus-ask", "feed-append"):
        path = lab / "bin" / name
        assert path.is_file() and os.access(path, os.X_OK)

    asked = _run(lab, "bus-ask", ["--to", "owner", "--type", "review", "Look at the page"], stdin=subprocess.DEVNULL)
    assert asked.returncode == 0
    ask_path = Path(asked.stdout.strip())
    assert ask_path.is_file() and ask_path.parent == lab / "inbox"
    assert "to: owner" in ask_path.read_text(encoding="utf-8")

    closed = _run(lab, "bus-ask", ["--close", ask_path.stem])
    assert closed.returncode == 0
    assert (lab / "done" / f"{ask_path.stem}.ask.md").is_file()
    assert not ask_path.exists()

    fed = _run(lab, "feed-append", ["--board", "Worker · working · Doing the task"])
    assert fed.returncode == 0
    assert " · Worker · working · Doing the task" in (lab / "board.md").read_text(encoding="utf-8")
    noted = _run(lab, "feed-append", ["Page ready", "The report page is up"])
    assert noted.returncode == 0
    assert "Page ready" in (lab / "feed.md").read_text(encoding="utf-8")

    checked = _run(lab, "bus-check", [])
    assert checked.returncode == 0
    assert "error(s)" in checked.stdout and "FAIL" not in checked.stdout

    morning = _run(lab, "lab-ops-check", [])
    assert morning.returncode == 0
    assert "open ask(s)" in morning.stdout


def test_bus_check_uses_its_own_folder_without_env(tmp_path):
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    checked = subprocess.run(
        [str(lab / "bin" / "bus-check")],
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / "home")},
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0
    assert "FAIL" not in checked.stdout


def test_bus_check_fails_on_a_bad_board_line(tmp_path):
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    board = lab / "board.md"
    board.write_text(board.read_text(encoding="utf-8") + "not a board line\n", encoding="utf-8")
    checked = _run(lab, "bus-check", [])
    assert checked.returncode == 1
    assert "FAIL" in checked.stdout


def test_lab_ops_check_flags_blocked(tmp_path):
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    assert _run(lab, "feed-append", ["--board", "Worker · blocked · Need a decision"]).returncode == 0
    morning = _run(lab, "lab-ops-check", [])
    assert morning.returncode == 0
    assert "ATTENTION Worker is blocked: Need a decision" in morning.stdout


def test_lab_ops_check_clears_blocked_when_resolved_headline_differs(tmp_path):
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    (lab / "board.md").write_text(
        "2026-10-20 08:00 · Worker · blocked · stuck on CI\n"
        "2026-10-20 08:01 · Worker · resolved · it is done\n",
        encoding="utf-8",
    )
    morning = _run(lab, "lab-ops-check", [])
    assert morning.returncode == 0
    assert "ATTENTION Worker" not in morning.stdout


def test_templates_have_no_private_paths():
    files = [p for p in _TEMPLATE.rglob("*") if p.is_file()]
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        for banned in _BANNED:
            assert banned not in text, path


def test_bus_ask_and_check(tmp_path):
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    assert bus.run(["ask", "--lab", str(lab), "owner", "Pick the layout"]) == 0
    files = list((lab / "inbox").glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "to: owner" in body and "type: decision" in body and "Pick the layout" in body
    assert bus.run(["check", "--lab", str(lab)]) == 0


def test_ask_uses_atlas_lab(tmp_path, monkeypatch):
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    assert bus.run(["ask", "owner", "Use the env lab"]) == 0
    assert any("Use the env lab" in p.read_text(encoding="utf-8") for p in (lab / "inbox").glob("*.md"))


def test_install_skips_an_edited_script(tmp_path, monkeypatch, capsys):
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    script = lab / "bin" / "bus-ask"
    script.write_text(script.read_text(encoding="utf-8") + "\n# user edit\n", encoding="utf-8")

    def _no(_prompt):
        raise AssertionError("should not ask")

    monkeypatch.setattr("builtins.input", _no)
    assert bus.run(["install", "--lab", str(lab)]) == 0
    first = capsys.readouterr().out
    assert "skip bus-ask: differs from the shipped script, left unchanged" in first
    assert "nothing to install" in first
    assert "# user edit" in script.read_text(encoding="utf-8")
    (lab / "bin" / "lab-ops-check").unlink()

    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert bus.run(["install", "--lab", str(lab)]) == 0
    err = capsys.readouterr()
    assert "install these?" in err.out and "lab-ops-check" in err.out and "not installed" in err.out
    assert not (lab / "bin" / "lab-ops-check").exists()
    assert "# user edit" in script.read_text(encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert bus.run(["install", "--lab", str(lab)]) == 0
    assert "installed lab-ops-check" in capsys.readouterr().out
    assert (lab / "bin" / "lab-ops-check").is_file()
    assert os.access(lab / "bin" / "lab-ops-check", os.X_OK)
    assert "# user edit" in script.read_text(encoding="utf-8")


def test_install_needs_a_lab(tmp_path, capsys):
    missing = tmp_path / "missing"
    missing.mkdir()
    assert bus.run(["install", "--lab", str(missing)]) == 2
    assert "atlas init" in capsys.readouterr().err


def test_main_dispatches_bus(tmp_path):
    from atlas.__main__ import main

    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    with pytest.raises(SystemExit) as exc:
        main(["bus", "check", "--lab", str(lab)])
    assert exc.value.code == 0

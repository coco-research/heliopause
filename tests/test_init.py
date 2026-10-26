import json, os, shutil, subprocess, sys
import pytest
from atlas import init
from atlas.__main__ import main

_DIRS = ("inbox", "done", "queue/inbox", "queue/running", "queue/done", "queue/failed", "nodes", "decisions", "bin")
_FILES = ("BUS.md", "board.md", "feed.md", "atlas.json", "nodes/lead.md", "nodes/worker.md", "bin/feed-append", "bin/bus-ask")


def test_init_creates_full_tree_and_parses_json(tmp_path):
    lab = tmp_path / "lab"
    assert init.run(["--yes", "--dir", str(lab), "--owner", "Alice"]) == 0
    assert all((lab / d).is_dir() for d in _DIRS)
    assert all((lab / f).is_file() for f in _FILES)

    data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    assert data["v"] == 1 and data["owner"] == "Alice" and isinstance(data["tz"], str) and data["tz"]
    assert data["nodes"] == [{"id": "lead", "label": "Lead", "role": "lab"}, {"id": "worker", "label": "Worker", "role": "factory"}]

    lead_lines = (lab / "nodes" / "lead.md").read_text(encoding="utf-8").splitlines()
    assert len(lead_lines) == 3 and lead_lines[0] == "Lead" and "lab" in lead_lines[1]
    worker_lines = (lab / "nodes" / "worker.md").read_text(encoding="utf-8").splitlines()
    assert len(worker_lines) == 3 and worker_lines[0] == "Worker" and "factory" in worker_lines[1]

    board = (lab / "board.md").read_text(encoding="utf-8")
    assert " · Lead · done · Lab created · atlas init" in board


def test_scripts_are_executable(tmp_path):
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    for name in ("feed-append", "bus-ask"):
        p = lab / "bin" / name
        assert p.is_file() and os.access(p, os.X_OK) and p.stat().st_mode & 0o777 == 0o755


def test_second_run_refuses_and_changes_nothing(tmp_path):
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    before_f = {p.relative_to(lab): p.read_bytes() for p in lab.rglob("*") if p.is_file()}
    before_d = {p.relative_to(lab) for p in lab.rglob("*") if p.is_dir()}

    with pytest.raises(SystemExit) as exc:
        init.run(["--yes", "--dir", str(lab)])
    assert exc.value.code == 1

    after_f = {p.relative_to(lab): p.read_bytes() for p in lab.rglob("*") if p.is_file()}
    after_d = {p.relative_to(lab) for p in lab.rglob("*") if p.is_dir()}
    assert before_f == after_f and before_d == after_d


def test_nothing_written_outside_dir(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    init.run(["--yes", "--dir", str(tmp_path / "lab")])
    assert list(fake_home.iterdir()) == []


def test_bad_owner_exits_nonzero(tmp_path):
    for i, bad in enumerate(("", "a" * 41, "Alice · Bob", "Alice\nBob", "Alice\rBob", "\x01")):
        with pytest.raises(SystemExit) as exc:
            init.run(["--yes", "--dir", str(tmp_path / f"bad_{i}"), "--owner", bad])
        assert exc.value.code != 0


def test_feed_append_with_lab_root(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("bash missing")
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    cmd = [str(lab / "bin" / "feed-append"), "--board", "Worker · working · Doing task"]
    res = subprocess.run(cmd, env={"LAB_ROOT": str(lab), "PATH": os.environ.get("PATH", "")}, capture_output=True, text=True)
    assert res.returncode == 0
    assert " · Worker · working · Doing task" in (lab / "board.md").read_text(encoding="utf-8")


def test_main_subcommand_dispatch(tmp_path):
    lab = tmp_path / "lab"
    with pytest.raises(SystemExit) as exc:
        main(["init", "--yes", "--dir", str(lab)])
    assert exc.value.code == 0
    assert (lab / "atlas.json").is_file()


def test_main_init_exit_code_when_not_created(tmp_path, monkeypatch):
    lab = tmp_path / "lab"
    # Non-interactive without --yes: exits 1, nothing written.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        main(["init", "--dir", str(lab)])
    assert exc.value.code == 1
    assert not lab.exists()
    # Interactive "n": exits 1, nothing written.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(SystemExit) as exc:
        main(["init", "--dir", str(lab)])
    assert exc.value.code == 1
    assert not lab.exists()


def test_cli_init_declined_exits_nonzero(tmp_path):
    lab = tmp_path / "lab"
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    res = subprocess.run([sys.executable, "-B", "-m", "atlas", "init", "--dir", str(lab)], cwd=repo, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    assert res.returncode == 1
    assert not lab.exists()


def test_init_asks_before_writing(tmp_path, monkeypatch, capsys):
    lab = tmp_path / "lab"
    # Non-interactive without --yes: nothing is written.
    assert init.run(["--dir", str(lab)]) == 1
    assert not lab.exists()
    assert "re-run with --yes" in capsys.readouterr().err
    # Interactive: the plan and the undo come first, and only "y" writes.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert init.run(["--dir", str(lab)]) == 1
    out = capsys.readouterr().out
    assert f"plan: create the lab folder {lab}" in out and f"undo: python3 -m atlas uninstall --lab {lab}" in out
    assert not lab.exists()
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert init.run(["--dir", str(lab)]) == 0
    assert (lab / "atlas.json").is_file()


@pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt], ids=["eof", "ctrl-c"])
def test_init_eof_or_ctrl_c_is_a_refusal(tmp_path, monkeypatch, capsys, exc):
    lab = tmp_path / "lab"

    def ask(prompt):
        raise exc

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", ask)
    with pytest.raises(SystemExit) as done:
        main(["init", "--dir", str(lab)])
    assert done.value.code == 1
    assert not lab.exists()
    assert "Nothing created." in capsys.readouterr().out


def test_new_lab_is_owner_only(tmp_path):
    lab = tmp_path / "lab"
    assert init.run(["--yes", "--dir", str(lab)]) == 0
    assert lab.stat().st_mode & 0o777 == 0o700
    assert (lab / "atlas.json").stat().st_mode & 0o777 == 0o600


def test_symlinked_dir_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "lab"
    link.symlink_to(real)
    with pytest.raises(SystemExit) as exc:
        init.run(["--yes", "--dir", str(link)])
    assert exc.value.code == 1
    assert list(real.iterdir()) == []


def test_link_planted_while_prompt_waits_is_not_followed(tmp_path, monkeypatch):
    # The folder is empty when checked; a dangling link appears inside it during the prompt.
    lab = tmp_path / "lab"
    outside = tmp_path / "outside.md"

    def ask(prompt):
        lab.mkdir()
        (lab / "board.md").symlink_to(outside)
        return "y"

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", ask)
    assert init.run(["--dir", str(lab)]) == 1
    assert not outside.exists()
    assert not (lab / "atlas.json").exists()


def test_ensure_never_follows_a_dangling_link(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    outside = tmp_path / "outside.json"
    (lab / "atlas.json").symlink_to(outside)
    init.ensure(lab)
    assert not outside.exists()
    assert (lab / "atlas.json").is_symlink()


@pytest.mark.parametrize("name", ["my lab", "lab;touch PWNED", "lab$(touch PWNED)", "it's"])
def test_init_plan_and_undo_are_shell_quoted(tmp_path, monkeypatch, capsys, name):
    import shlex

    lab = tmp_path / name
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert init.run(["--dir", str(lab)]) == 1
    undo = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("undo: "))
    assert shlex.split(undo[len("undo: "):]) == ["python3", "-m", "atlas", "uninstall", "--lab", str(lab)]


def test_init_plan_lists_every_folder_it_creates(tmp_path, monkeypatch, capsys):
    lab = tmp_path / "lab"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert init.run(["--dir", str(lab)]) == 1
    plan = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("plan: create"))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert init.run(["--dir", str(lab)]) == 0
    for top in sorted({p.name for p in lab.iterdir() if p.is_dir()}):
        assert f"{top}/" in plan, top


def test_adopt_undo_line_is_shell_quoted(tmp_path, monkeypatch, capsys):
    import shlex

    from atlas import adopt

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    lab = tmp_path / "my lab;x"
    assert adopt.run(["--apply", "--yes", "--home", str(home), "--lab", str(lab), "--lead-name", "Ada"]) == 0
    undo = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Undo: atlas"))
    assert shlex.split(undo[len("Undo: "):]) == ["atlas", "adopt", "--uninstall", "--lab", str(lab)]


@pytest.mark.parametrize("sub", ["queue", "bin", "nodes", "done"])
def test_ensure_refuses_a_symlinked_subfolder(tmp_path, sub):
    lab = tmp_path / "lab"
    lab.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (lab / sub).symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        init.ensure(lab)
    assert list(outside.iterdir()) == []


def test_init_reports_a_symlinked_subfolder_planted_after_the_prompt(tmp_path, monkeypatch, capsys):
    lab = tmp_path / "lab"
    outside = tmp_path / "outside"
    outside.mkdir()
    real_chmod = init.labfs.chmod

    def chmod(root, rel, mode, *a, **k):
        real_chmod(root, rel, mode, *a, **k)
        if str(root / rel) == str(lab) and not (lab / "queue").exists():
            (lab / "queue").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(init.labfs, "chmod", chmod)
    assert init.run(["--dir", str(lab), "--yes"]) == 1
    err = capsys.readouterr().err
    assert "queue/ is a symlink" in err and "Traceback" not in err
    assert list(outside.iterdir()) == []


def test_a_failed_write_leaves_no_half_file(tmp_path, monkeypatch):
    lab = tmp_path / "lab"
    lab.mkdir()
    real_write = os.write

    def half(fd, data):
        real_write(fd, bytes(data[: len(data) // 2]))
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(init.labfs.os, "write", half)
    with pytest.raises(OSError):
        init.ensure(lab)
    monkeypatch.undo()
    leftovers = [p for p in lab.rglob("*") if p.is_file()]
    assert leftovers == []


def test_board_is_written_with_its_first_line(tmp_path):
    lab = tmp_path / "lab"
    init.ensure(lab)
    assert (lab / "board.md").read_text(encoding="utf-8").rstrip().endswith("Lab created · atlas init")

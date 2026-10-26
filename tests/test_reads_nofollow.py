"""The server's reads of lab files never follow a planted symlink or serve a hard-linked file (labfs)."""

import os
import threading

import pytest

from atlas import adopt, collect, events


@pytest.fixture
def lab(tmp_path, monkeypatch):
    root = tmp_path / "lab"
    root.mkdir()
    (root / "atlas.json").write_text("{}\n", encoding="utf-8")
    secret = tmp_path / "secret.md"
    secret.write_text("2026-10-20 08:00 · Secret · working · from outside the lab\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_LAB", str(root))
    return root, secret


def test_collect_read_skips_a_symlinked_board(lab):
    root, secret = lab
    (root / "board.md").symlink_to(secret)
    assert collect._read(root / "board.md") is None


def test_collect_read_skips_a_hard_linked_board(lab):
    root, secret = lab
    os.link(secret, root / "board.md")
    assert collect._read(root / "board.md") is None


def test_collect_read_skips_a_linked_folder_inside_the_lab(lab, tmp_path, monkeypatch):
    root, secret = lab
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "feed.md").write_text("## outside\n", encoding="utf-8")
    (root / "queue").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(collect, "LAB_DIR", root)
    assert collect._read(root / "queue" / "feed.md") is None
    (root / "board.md").write_text("2026-10-20 08:00 · W · working · Fine\n", encoding="utf-8")
    assert collect._read(root / "board.md").startswith("2026-10-20")


def test_collect_read_skips_a_fifo(lab):
    root, _secret = lab
    os.mkfifo(root / "board.md")
    got = []
    # A plain open() of a FIFO blocks until a writer appears; fail in 5 s instead of hanging the run.
    reader = threading.Thread(target=lambda: got.append(collect._read(root / "board.md")), daemon=True)
    reader.start()
    reader.join(5)
    if reader.is_alive():
        with open(root / "board.md", "wb"):  # release the blocked reader
            pass
        reader.join(5)
        pytest.fail("reading a FIFO board.md blocked")
    assert got == [None]


def test_events_tail_skips_a_symlinked_board(lab):
    root, secret = lab
    (root / "board.md").symlink_to(secret)
    watcher = events.Watcher.__new__(events.Watcher)
    assert watcher._tail(root / "board.md", None, False, lambda line: None) is None
    (root / "board.md").unlink()
    (root / "board.md").write_text("2026-10-20 08:00 · W · working · Fine\n", encoding="utf-8")
    state = watcher._tail(root / "board.md", None, False, lambda line: None)
    assert state is not None and state[1] == (root / "board.md").stat().st_size


def test_events_tail_skips_a_hard_linked_board(lab):
    root, secret = lab
    os.link(secret, root / "board.md")
    watcher = events.Watcher.__new__(events.Watcher)
    assert watcher._tail(root / "board.md", None, False, lambda line: None) is None


def _stub(root):
    folder = root / "projects" / "demo"
    folder.mkdir(parents=True)
    return folder / "START-HERE.md"


def test_projects_serves_a_plain_stub(lab):
    root, _secret = lab
    _stub(root).write_text("# Demo\n", encoding="utf-8")
    assert adopt.start_here_bytes(root, "/projects/demo/START-HERE.md") == b"# Demo\n"


def test_projects_refuses_a_hard_linked_stub(lab):
    root, secret = lab
    os.link(secret, _stub(root))
    assert adopt.start_here_bytes(root, "/projects/demo/START-HERE.md") is None


def test_projects_refuses_a_symlinked_stub_or_folder(lab, tmp_path):
    root, secret = lab
    _stub(root).symlink_to(secret)
    assert adopt.start_here_bytes(root, "/projects/demo/START-HERE.md") is None
    other = tmp_path / "other"
    other.mkdir()
    (other / "START-HERE.md").write_text("outside\n", encoding="utf-8")
    (root / "projects" / "elsewhere").symlink_to(other, target_is_directory=True)
    assert adopt.start_here_bytes(root, "/projects/elsewhere/START-HERE.md") is None

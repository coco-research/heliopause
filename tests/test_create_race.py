"""A first create under a held folder descriptor never uses a plain openat(O_CREAT).

On macOS, openat(dirfd, name, O_CREAT) without O_EXCL sometimes fails with ENOENT when several threads create the
same new name at once (the CI runner: 1 of 20 concurrent first handoffs got 503 "no inbox", errno 2 on
handoffs.jsonl). labfs creates with O_CREAT|O_EXCL and opens an existing file without O_CREAT.
"""

import importlib
import os
import threading
from pathlib import Path

import pytest

from atlas import labfs

_VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "m0-lite"


def _macos_race(monkeypatch):
    """Make os.open behave like the losing side of the macOS race on every plain O_CREAT under a dir_fd."""
    real = os.open
    plain = []

    def fake(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and flags & os.O_CREAT and not flags & os.O_EXCL:
            plain.append(path)
            raise FileNotFoundError(2, "No such file or directory", path)
        return real(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", fake)
    return plain


@pytest.mark.parametrize("exists", [False, True])
def test_open_append_creates_without_plain_ocreat(tmp_path, monkeypatch, exists):
    (tmp_path / "inbox" / "atlas").mkdir(parents=True)
    log = tmp_path / "inbox" / "atlas" / "handoffs.jsonl"
    if exists:
        log.write_bytes(b"one\n")
    plain = _macos_race(monkeypatch)
    fd = labfs.open_append(tmp_path, "inbox/atlas/handoffs.jsonl", create=True, mode=0o600, read=True, fix_mode=True)
    try:
        os.write(fd, b"two\n")
    finally:
        os.close(fd)
    assert plain == []
    assert log.read_bytes() == (b"one\ntwo\n" if exists else b"two\n")
    assert log.stat().st_mode & 0o777 == 0o600


def test_create_false_still_reports_a_missing_file(tmp_path):
    (tmp_path / "inbox").mkdir()
    with pytest.raises(FileNotFoundError):
        labfs.open_append(tmp_path, "inbox/missing.jsonl", create=False)


def test_concurrent_first_creates_all_succeed(tmp_path):
    # On macOS this fails within a few rounds with a plain O_CREAT; here it checks that nobody loses.
    errors = []
    for round_ in range(40):
        root = tmp_path / f"r{round_}"
        (root / ".heliopause" / "inbox").mkdir(parents=True)
        gate = threading.Barrier(20)

        def one():
            gate.wait()
            try:
                os.close(labfs.open_append(root, ".heliopause/inbox/atlas/handoffs.jsonl", make_dirs=True, read=True, fix_mode=True))
            except OSError as err:
                errors.append(repr(err))

        threads = [threading.Thread(target=one) for _ in range(20)]
        for item in threads:
            item.start()
        for item in threads:
            item.join()
    assert errors == []


@pytest.mark.skipif(not _VENDOR.is_dir(), reason="dev checkout only (vendor/ is not shipped)")
def test_dev_recall_log_creates_without_plain_ocreat(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(_VENDOR))
    monkeypatch.setenv("M0_LITE_HOME", str(tmp_path / "lite"))
    module = importlib.import_module("m0lite_recall_lanes")
    plain = _macos_race(monkeypatch)
    module.log_call("s1", "none", False, 0, 3)
    assert plain == [] and (tmp_path / "lite" / "logs" / "push-recall.jsonl").read_text(encoding="utf-8")

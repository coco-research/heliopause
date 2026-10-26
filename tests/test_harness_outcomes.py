"""Each harness outcome, using tests/fixtures/fake-harness."""

from __future__ import annotations

import fcntl
import io
import os
import sys
import threading
import time
from pathlib import Path

import pytest

import harness
from harness.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fixtures" / "fake-harness"


def _use_fake(monkeypatch):
    monkeypatch.setenv("PATH", str(FAKE) + os.pathsep + os.environ.get("PATH", ""))


def test_ok_echoes_stdin(tmp_path, monkeypatch):
    _use_fake(monkeypatch)
    result = harness.run("claude", "hello", str(tmp_path), 10, env={"FAKE_MODE": "ok"})
    assert result.outcome == "ok" and result.exit_code == 0 and "hello" in result.text


def test_timeout_outcome(tmp_path, monkeypatch):
    _use_fake(monkeypatch)
    started = time.monotonic()
    result = harness.run(
        "claude", "x", str(tmp_path), 0.4, env={"FAKE_MODE": "slow", "FAKE_SLEEP": "30"}
    )
    assert result.outcome == "timeout" and result.timed_out is True
    assert time.monotonic() - started < 8


@pytest.mark.parametrize(
    "name,needle",
    [("claude", "please run /login"), ("cursor", "not logged in")],
)
def test_not_signed_in(name, needle, tmp_path, monkeypatch):
    _use_fake(monkeypatch)
    result = harness.run(name, "x", str(tmp_path), 10, env={"FAKE_MODE": "signed_out"})
    assert result.outcome == "not_signed_in" and result.exit_code == 1
    assert needle in result.text.lower()


def test_error_outcome(tmp_path, monkeypatch):
    _use_fake(monkeypatch)
    result = harness.run("cursor", "x", str(tmp_path), 10, env={"FAKE_MODE": "fail"})
    assert result.outcome == "error" and result.exit_code == 2 and result.text == "boom\n"


def test_harness_missing(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    result = harness.run("claude", "x", str(tmp_path), 5)
    assert result.outcome == "harness_missing" and result.exit_code == 127
    assert result.text == "" and result.seconds == 0.0 and result.argv == ["claude"]


def test_busy_then_available(tmp_path, monkeypatch):
    _use_fake(monkeypatch)
    lock, first, second, third = (tmp_path / name for name in ("lock", "first", "second", "third"))
    for work in (lock, first, second, third):
        work.mkdir()
    got = threading.Event()
    real_flock, acquire = harness.fcntl.flock, fcntl.LOCK_EX | fcntl.LOCK_NB

    def flock(fd, op):
        real_flock(fd, op)
        if op == acquire:
            got.set()

    monkeypatch.setattr(harness.fcntl, "flock", flock)
    spawned = []
    real_popen = harness.subprocess.Popen

    def popen(*args, **kwargs):
        spawned.append(kwargs.get("cwd"))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(harness.subprocess, "Popen", popen)
    box = {}

    def hold():
        box["result"] = harness.run(
            "claude", "hold", str(first), 30,
            env={"FAKE_MODE": "slow", "FAKE_SLEEP": "2"}, lock_dir=str(lock),
        )

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert got.wait(5)
        started = time.monotonic()
        busy = harness.run(
            "cursor", "nope", str(second), 30,
            env={"FAKE_MODE": "slow", "FAKE_SLEEP": "30"}, lock_dir=str(lock),
        )
        assert time.monotonic() - started < 1
        assert busy.outcome == "busy" and busy.exit_code == 75
        assert str(second) not in spawned
    finally:
        thread.join(15)
    assert box["result"].outcome == "ok"
    again = harness.run("claude", "again", str(third), 10, env={"FAKE_MODE": "ok"}, lock_dir=str(lock))
    assert again.outcome == "ok" and "again" in again.text


def test_lock_symlink_is_refused(tmp_path, monkeypatch):
    _use_fake(monkeypatch)
    lock = tmp_path / "lock"
    lock.mkdir()
    (lock / "harness.lock").symlink_to(tmp_path / "real.lock")
    (tmp_path / "real.lock").write_bytes(b"")
    spawned = []
    real_popen = harness.subprocess.Popen
    monkeypatch.setattr(
        harness.subprocess, "Popen",
        lambda *args, **kwargs: spawned.append(kwargs.get("cwd")) or real_popen(*args, **kwargs),
    )
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises((OSError, ValueError)):
        harness.run("claude", "x", str(work), 10, env={"FAKE_MODE": "ok"}, lock_dir=str(lock))
    assert spawned == []


def test_keyboard_interrupt_releases_the_lock(tmp_path, monkeypatch):
    _use_fake(monkeypatch)
    lock = tmp_path / "lock"
    lock.mkdir()

    def popen(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(harness.subprocess, "Popen", popen)
    with pytest.raises(KeyboardInterrupt):
        harness.run("claude", "x", str(tmp_path), 5, env={"FAKE_MODE": "ok"}, lock_dir=str(lock))
    fd = os.open(str(lock / "harness.lock"), os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_cli_outcome_and_lock_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", type("In", (), {"buffer": io.BytesIO(b"x")})())
    def fake_run(*args, **kwargs):
        fake_run.dir = kwargs.get("lock_dir")
        return harness.Result(1, "", False, 0.0, ["claude"], outcome="not_signed_in")
    monkeypatch.setattr("harness.__main__.run", fake_run)
    code = main(["run", "--name", "claude", "--cwd", str(tmp_path), "--timeout", "10", "--lock-dir", str(tmp_path)])
    assert code == 77 and capsys.readouterr().err == "outcome: not_signed_in\n"
    assert fake_run.dir == str(tmp_path)

"""`adopt --apply` with no terminal and no --yes prints the plan, says what to do, exits 2 and writes nothing.

It never waits on stdin: /dev/null, a closed pipe and an open pipe nobody writes to all end in under 2 seconds.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MESSAGE = "Non-interactive terminal: re-run with --yes to apply. Nothing changed."


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"theme": "dark"}\n')
    return home


def _adopt(home: Path, lab: Path, *args: str, stdin) -> tuple[subprocess.CompletedProcess, float]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ATLAS_", "HELIOPAUSE_"))}
    env.update(HOME=str(home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1")
    cmd = [sys.executable, "-B", "-m", "atlas", "adopt", "--home", str(home), "--lab", str(lab), *args]
    start = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=2 + 8)  # the test asserts < 2 s below; this only stops a hang
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise AssertionError("adopt waited for input")
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err), time.monotonic() - start


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None for p in sorted(root.rglob("*"))}


def test_apply_on_dev_null_exits_2_fast_and_writes_nothing(tmp_path):
    home, lab = _home(tmp_path), tmp_path / "lab"
    before = _tree(home)
    got, took = _adopt(home, lab, "--apply", "--lead-name", "Ada", stdin=subprocess.DEVNULL)
    assert got.returncode == 2, got.stderr
    assert took < 2
    assert got.stdout.rstrip().splitlines()[-1] == MESSAGE
    assert "--apply scans first" in got.stdout  # the plan it would have asked about
    assert not lab.exists()
    assert _tree(home) == before


def test_apply_on_an_open_pipe_nobody_writes_to_does_not_hang(tmp_path):
    home, lab = _home(tmp_path), tmp_path / "lab"
    proc_in = subprocess.Popen(["cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)  # a pipe that stays open
    try:
        got, took = _adopt(home, lab, "--apply", "--lead-name", "Ada", stdin=proc_in.stdin)
    finally:
        proc_in.kill()
        proc_in.wait()
    assert got.returncode == 2 and took < 2
    assert got.stdout.rstrip().splitlines()[-1] == MESSAGE
    assert not lab.exists()


def test_apply_after_a_scan_prints_the_exact_plan_then_exits_2(tmp_path):
    home, lab = _home(tmp_path), tmp_path / "lab"
    scanned, _ = _adopt(home, lab, "--scan", stdin=subprocess.DEVNULL)
    assert scanned.returncode == 0, scanned.stderr
    before = _tree(lab)
    got, took = _adopt(home, lab, "--apply", "--lead-name", "Ada", stdin=subprocess.DEVNULL)
    assert got.returncode == 2 and took < 2
    assert "Heliopause adopt will make these changes:" in got.stdout
    assert "created BUS.md" in got.stdout
    assert got.stdout.rstrip().splitlines()[-1] == MESSAGE
    assert not (lab / "BUS.md").exists() and not (lab / "atlas.json").exists()
    assert _tree(lab) == before


@pytest.mark.parametrize("stdin", [subprocess.DEVNULL])
def test_apply_with_yes_still_applies_without_a_terminal(tmp_path, stdin):
    home, lab = _home(tmp_path), tmp_path / "lab"
    assert _adopt(home, lab, "--scan", stdin=stdin)[0].returncode == 0
    got, _ = _adopt(home, lab, "--apply", "--yes", "--lead-name", "Ada", stdin=stdin)
    assert got.returncode == 0, got.stderr
    assert (lab / "BUS.md").is_file()

"""Stranger test: fresh clone, lifecycle check, and clean uninstall."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "stranger-test.sh"


@pytest.mark.journey
def test_stranger_install_and_lifecycle():
    # ponytail: slow-but-run test, executes a fresh clone and full lifecycle in <60s.
    if not shutil.which("bash"):
        pytest.skip("bash is missing")
    if not shutil.which("git"):
        pytest.skip("git is missing")

    env = {**os.environ, "PY": sys.executable}
    res = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, f"scripts/stranger-test.sh failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    pass_lines = [line for line in res.stdout.splitlines() if line.startswith("PASS:")]
    assert len(pass_lines) == 4, f"expected 4 PASS lines, got {pass_lines}\n{res.stdout}"
    assert "PASS: a. clone" in pass_lines
    assert "PASS: b. snapshot" in pass_lines
    assert "PASS: c. lifecycle" in pass_lines
    assert "PASS: d. uninstall" in pass_lines

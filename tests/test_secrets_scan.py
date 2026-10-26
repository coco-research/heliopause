import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_history_has_no_secrets():
    # The whole git history, with the repo's config (defaults + the one fake test token).
    done = subprocess.run(
        ["gitleaks", "git", "--no-banner", "--redact", "-c", str(ROOT / ".gitleaks.toml"), str(ROOT)],
        capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr

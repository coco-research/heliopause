"""Public-tree hygiene: no private product or repo names anywhere in the tracked files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Built from pieces so this file does not match its own scan.
_NAMES = tuple(a + b for a, b in (
    ("brain", "-"),
    ("coco-research", "-home"),
    ("coco", "-m0"),
    ("coco", "-courier"),
    ("coco", "-voice"),
    ("coco", "-mind"),
    ("connect", "-kit"),
    ("harbor", "-ng"),
))


def _tracked() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / item for item in out.decode("utf-8").split("\0") if item]


def test_no_private_names_in_tree():
    hits: list[str] = []
    for path in _tracked():
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_bytes().decode("utf-8", "ignore").lower()
        rel = str(path.relative_to(ROOT)).lower()
        for name in _NAMES:
            if name in text or name in rel:
                hits.append(f"{path.relative_to(ROOT)} has a private name")
                break
    assert hits == []

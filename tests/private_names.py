"""Banned-name checks read their names from a private file outside the repo, so the repo itself names nobody.

The file is ~/.config/coco-private/banned-names.txt (or $ATLAS_BANNED_NAMES): one case-insensitive
literal per line, blank lines and '#' comments skipped. A literal matches anywhere in the text (substring),
so short entries match inside longer words too. Without the file, the checks skip and say why.
CI has no such file, so there they always skip: run the suite with the file present by hand before every release.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

DEFAULT = "~/.config/coco-private/banned-names.txt"
# Fixed skip messages: a skip line ends up in CI logs, so it names no path on the runner.
_SKIP_MISSING = "banned-name check skipped: private names file not found (set ATLAS_BANNED_NAMES)"
_SKIP_EMPTY = "banned-name check skipped: private names file has no names"
# Resolved at import, before any test points HOME at a temporary folder.
_PATH = Path(os.environ.get("ATLAS_BANNED_NAMES") or DEFAULT).expanduser()


def path() -> Path:
    return _PATH


def names() -> list[str]:
    """The lowercased names, or a pytest skip with a clear message when the private file is absent or empty."""
    where = path()
    try:
        lines = where.read_text(encoding="utf-8").splitlines()
    except OSError:
        pytest.skip(_SKIP_MISSING)
    found = [line.strip().lower() for line in lines if line.strip() and not line.strip().startswith("#")]
    if not found:
        pytest.skip(_SKIP_EMPTY)
    return found


def hits(text: str, banned: list[str]) -> list[int]:
    """1-based numbers of the banned names found in text. Never the names themselves."""
    low = text.lower()
    return [number for number, name in enumerate(banned, 1) if name in low]

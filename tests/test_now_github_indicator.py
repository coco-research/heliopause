"""Now's github flag must use the same atlas.json gate as the GitHub poller."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas import github, screens


def _lab(path: Path, doc: dict) -> None:
    """Minimal atlas.json, same shape as tests/test_screens.py roles setup."""
    (path / "atlas.json").write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize(
    "doc, expected",
    [
        ({"roles": {"hq": "you", "lab": None, "factory": []}, "github": {"enabled": True}}, True),
        ({"roles": {"hq": "you", "lab": None, "factory": []}, "github": {"enabled": False}}, False),
        ({"roles": {"hq": "you", "lab": None, "factory": []}}, False),
    ],
)
def test_now_github_matches_poller_gate(tmp_path: Path, doc: dict, expected: bool) -> None:
    _lab(tmp_path, doc)
    assert screens.now(tmp_path)["github"] is expected
    assert screens.now(tmp_path)["github"] == github.enabled(tmp_path)

"""Scrub test: ensure shipped files contain no personal paths, keys, or private words."""

from __future__ import annotations

import os
from pathlib import Path
import re
import pytest

REPO = Path(__file__).resolve().parent.parent

_HOME_PATTERN = re.compile(r"/(?:Users|home)/([^/\s]+)")
_KEY_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9_])(?:sk-|xai-|ghp_|github_pat_)[A-Za-z0-9_-]+"),
    re.compile(r"(?<![A-Za-z0-9_])AKIA[0-9A-Z]+"),
    re.compile(r"-----BEGIN"),
]


def _shipped_files() -> list[Path]:
    files: list[Path] = []
    templates = REPO / "atlas" / "templates"
    if templates.is_dir():
        files.extend(sorted(p for p in templates.rglob("*") if p.is_file()))
    readme = REPO / "README.md"
    if readme.is_file():
        files.append(readme)
    routes = REPO / "docs" / "routes.example.json"
    if routes.is_file():
        files.append(routes)
    # Both demo trees ship in the public repo. demo/generic arrives with its own PR.
    for demo in (REPO / "demo" / "home", REPO / "demo" / "generic"):
        if demo.is_dir():
            files.extend(sorted(p for p in demo.rglob("*") if p.is_file()))
    stranger = REPO / "scripts" / "stranger-test.sh"
    if stranger.is_file():
        files.append(stranger)
    return files


def test_shipped_files_scrubbed():
    files = _shipped_files()
    assert files, "no shipped files found to scan"
    failures: list[str] = []
    for path in files:
        content = path.read_text(encoding="utf-8", errors="ignore")
        for m in _HOME_PATTERN.finditer(content):
            if m.group(1) != "<name>":
                failures.append(f"Forbidden home path in {path.relative_to(REPO)}: {m.group(0)}")
        for pat in _KEY_PATTERNS:
            for m in pat.finditer(content):
                failures.append(f"Forbidden key-like string in {path.relative_to(REPO)}: {m.group(0)}")
    assert not failures, "Scrub violations found:\n" + "\n".join(failures)


def test_shipped_files_have_no_private_words():
    # CI has no private words, so this is SKIPPED there, never passed. The maintainer runs it locally.
    raw = os.environ.get("ATLAS_PRIVATE_WORDS", "")
    words = [w.strip() for w in raw.split(",") if w.strip()]
    if not words:
        pytest.skip("ATLAS_PRIVATE_WORDS is not set")
    failures = [
        f"Forbidden private word {word!r} in {path.relative_to(REPO)}"
        for path in _shipped_files()
        for word in words
        if word.lower() in path.read_text(encoding="utf-8", errors="ignore").lower()
    ]
    assert not failures, "\n".join(failures)


def test_scrub_rules():
    # Placeholder allowed, real paths rejected
    m_ok = _HOME_PATTERN.search("/Users/<name>/project/")
    assert m_ok and m_ok.group(1) == "<name>"
    # split so the release scan, which looks for real home paths, does not flag this fake one.
    m_bad_mac = _HOME_PATTERN.search("/Users" + "/someone/project/")
    assert m_bad_mac and m_bad_mac.group(1) != "<name>"
    m_bad_linux = _HOME_PATTERN.search("/home/someone/project/")
    assert m_bad_linux and m_bad_linux.group(1) != "<name>"

    # Key prefixes match keys but not innocent text
    assert any(p.search("sk-abcdef123456") for p in _KEY_PATTERNS)
    assert any(p.search("xai-abcdef123456") for p in _KEY_PATTERNS)
    assert any(p.search("ghp_1234567890abcdef") for p in _KEY_PATTERNS)
    assert any(p.search("github_pat_1234567890abcdef") for p in _KEY_PATTERNS)
    assert any(p.search("AKIAIOSFODNN7EXAMPLE") for p in _KEY_PATTERNS)
    assert any(p.search("-----BEGIN CERTIFICATE-----") for p in _KEY_PATTERNS)

    assert not any(p.search("`sk-`, `ghp_`, `github_pat_`, `xai-`") for p in _KEY_PATTERNS)
    assert not any(p.search('id="ask-$stamp-$n"') for p in _KEY_PATTERNS)

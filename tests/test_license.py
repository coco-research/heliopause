"""Root licence is Apache-2.0; the prior MIT grant and vendored licences stay put."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Verbatim Apache-2.0 text from https://www.apache.org/licenses/LICENSE-2.0.txt
_APACHE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
_APACHE_LINE = "Heliopause by Coco Research. Copyright 2026 Coco Research."
_RELICENSE = (
    "Relicensed from MIT to Apache-2.0 from this commit. "
    "Earlier releases remain under MIT."
)


def test_root_license_is_verbatim_apache_2():
    text = (ROOT / "LICENSE").read_bytes()
    assert hashlib.sha256(text).hexdigest() == _APACHE_SHA256
    assert b"Apache License" in text
    assert b"Version 2.0, January 2004" in text
    assert b"http://www.apache.org/licenses/" in text
    assert b"END OF TERMS AND CONDITIONS" in text


def test_prior_mit_grant_is_kept():
    text = (ROOT / "LICENSE-MIT-PRIOR").read_text(encoding="utf-8")
    assert text.startswith("MIT License\n")
    assert "Copyright (c) 2026 Coco Research\n" in text
    assert "Permission is hereby granted, free of charge" in text
    assert "Apache" not in text


def test_notice_keeps_third_party_lines_and_adds_apache_line():
    text = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert _APACHE_LINE in text
    assert "Coco Research, Inc" not in text
    assert "LLC" not in text
    assert "Copyright (c) 2026 Coco Research" in text
    assert "Apache License, Version 2.0" in text
    assert "Schibsted Grotesk, Copyright 2023 The Schibsted-Grotesk Project Authors," in text
    assert "Geist Mono, Copyright 2024 The Geist Project Authors," in text
    assert "SIL Open Font License 1.1." in text
    assert "see THIRD-PARTY-NOTICES" in text


def test_readme_records_forward_only_relicense():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert 'alt="Licence: Apache-2.0"' in text
    assert "licence-Apache--2.0" in text
    assert "licence-MIT" not in text
    assert _RELICENSE in text
    assert "Apache-2.0. Copyright 2026 Coco Research." in text


def test_licence_fields_name_apache_2():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = {text = "Apache-2.0"}' in pyproject
    plugin = json.loads(
        (ROOT / "plugin/heliopause/.claude-plugin/plugin.json").read_text(encoding="utf-8")
    )
    assert plugin["license"] == "Apache-2.0"
    sbom = json.loads((ROOT / "sbom.cdx.json").read_text(encoding="utf-8"))
    assert sbom["metadata"]["component"]["licenses"][0]["license"]["id"] == "Apache-2.0"
    third_party = [item["licenses"][0]["license"]["id"] for item in sbom["components"]]
    assert third_party == ["OFL-1.1", "OFL-1.1", "MIT"]
    notices = (ROOT / "THIRD-PARTY-NOTICES").read_text(encoding="utf-8")
    assert "SIL OFL 1.1" in notices
    assert "pytest | MIT" in notices
    assert "`LICENSE-MIT-PRIOR`" in notices


def test_vendored_font_licences_unchanged():
    for rel in (
        "atlas/static/signal/fonts/OFL-schibsted-grotesk.txt",
        "atlas/static/signal/fonts/OFL-geist-mono.txt",
        "docs/brand/OFL-schibsted-grotesk.txt",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert text.startswith("Copyright ")
        assert "SIL Open Font License, Version 1.1" in text
        assert "Apache License" not in text

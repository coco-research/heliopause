"""The published first-install check stays aligned with the signer and release script."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SSH_KEYGEN = "/usr/bin/ssh-keygen"
_MARKER = "shasum -a 256 -c --status"
_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.M | re.S)
_HEADINGS = (
    ("README.md", "## Verifying releases"),
    ("SECURITY.md", "## Verifying a release"),
)
# Longer suffixes first so .zip.sha256 is not read as .zip.
_SUFFIXES = (".zip.sha256", ".manifest.sig", ".zip", ".manifest")


def _section(path: Path, heading: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next((i + 1 for i, line in enumerate(lines) if line.strip() == heading), None)
    assert start is not None, f"{path.name} has no {heading!r} heading"
    end = next(
        (j for j in range(start, len(lines)) if re.match(r"#{1,6} ", lines[j])),
        len(lines),
    )
    return "".join(lines[start:end])


def _block(filename: str, heading: str) -> str:
    found = [
        match.group(1)
        for match in _FENCE.finditer(_section(ROOT / filename, heading))
        if match.group(1).startswith(_MARKER)
    ]
    assert len(found) == 1, (
        f"{filename} {heading!r} has {len(found)} fenced blocks starting with {_MARKER!r}"
    )
    return found[0]


def _blocks() -> list[tuple[str, str]]:
    return [(name, _block(name, heading)) for name, heading in _HEADINGS]


def _signer_lines() -> list[str]:
    text = (ROOT / "atlas" / "release_signers").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def _echoed_signer(block: str) -> str:
    lines = block.splitlines()
    assert len(lines) >= 2, "verify block has no line 2"
    match = re.match(r"echo '([^']*)'", lines[1])
    assert match, f"line 2 does not echo a single-quoted signer line: {lines[1]!r}"
    return match.group(1)


def _script() -> str:
    return (ROOT / "scripts" / "release-zip.sh").read_text(encoding="utf-8")


def _signing_namespace(script: str) -> str:
    match = re.search(r"-Y sign -n (\S+)", script)
    assert match, "scripts/release-zip.sh has no '-Y sign -n <ns>'"
    return match.group(1)


def _flag(line: str, letter: str) -> str:
    match = re.search(rf"(?:^|\s)-{letter} (\S+)", line)
    assert match, f"verify line has no -{letter}: {line!r}"
    return match.group(1)


def _version() -> str:
    text = (ROOT / "atlas" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    assert match, "atlas/__init__.py has no __version__"
    return match.group(1)


def _built_names(script: str, version: str) -> set[str]:
    # Filename keeps the tag. version=${tag#v} drops one leading v; slug drops slashes.
    assert re.search(r"^slug=\$\{tag//\\//-\}\s*$", script, re.M), (
        "scripts/release-zip.sh no longer sets slug=${tag//\\//-}"
    )
    assert re.search(r"^version=\$\{tag#v\}\s*$", script, re.M), (
        "scripts/release-zip.sh no longer sets version=${tag#v}"
    )
    name = re.search(r'^name="([^"\n]+)"', script, re.M)
    manifest = re.search(r"^manifest=\$work/(\S+)", script, re.M)
    sig = re.search(r"^sig=\$work/(\S+)", script, re.M)
    checksum = re.search(r"^final_sum=\$\{final_zip\}(\.sha256)\s*$", script, re.M)
    assert name, 'scripts/release-zip.sh has no name="..."'
    assert manifest and sig and checksum, "scripts/release-zip.sh does not name manifest, sig, and .sha256"
    assert "${slug}" in name.group(1), name.group(1)
    slug = ("v" + version).replace("/", "-")
    zip_name = name.group(1).replace("${slug}", slug)
    manifest_name = manifest.group(1).replace("${slug}", slug)
    sig_name = sig.group(1).replace("${slug}", slug)
    assert sig_name == manifest_name + ".sig", f"{sig_name!r} is not {manifest_name}.sig"
    return {zip_name, zip_name + checksum.group(1), manifest_name, sig_name}


def _published_names(block: str, script: str) -> set[str]:
    template = re.search(r'^name="([^"\n]+)"', script, re.M)
    assert template and "${slug}" in template.group(1)
    prefix = template.group(1).split("${slug}", 1)[0]
    found: set[str] = set()
    for token in re.findall(r"[^\s\"'`$<>|&;()]+", block):
        if not token.startswith(prefix):
            continue
        for suffix in _SUFFIXES:
            if token.endswith(suffix):
                found.add(token)
                break
    return found


def test_verify_blocks_are_byte_identical() -> None:
    readme, security = (block for _, block in _blocks())
    assert readme == security, "first-install check differs between README.md and SECURITY.md"


def test_echoed_signer_line_matches_release_signers() -> None:
    keys = _signer_lines()
    assert len(keys) == 1, f"atlas/release_signers has {len(keys)} non-comment key lines"
    for filename, block in _blocks():
        echoed = _echoed_signer(block)
        assert echoed == keys[0], (
            f"{filename} line 2 echoes {echoed!r}; "
            f"atlas/release_signers key line is {keys[0]!r}"
        )


def test_verify_flags_match_signing_namespace() -> None:
    namespace = _signing_namespace(_script())
    for filename, block in _blocks():
        lines = [line for line in block.splitlines() if "-Y verify" in line]
        assert len(lines) == 1, f"{filename} has {len(lines)} ssh-keygen -Y verify lines"
        identity, signed = _flag(lines[0], "I"), _flag(lines[0], "n")
        assert identity == namespace and signed == namespace, (
            f"{filename}: -I {identity!r} -n {signed!r}; "
            f"scripts/release-zip.sh signs with -Y sign -n {namespace!r}"
        )


def _semver(text: str) -> tuple[int, ...]:
    assert re.fullmatch(r"\d+\.\d+\.\d+", text), text
    return tuple(int(part) for part in text.split("."))


def test_published_filenames_match_release_script() -> None:
    # The docs name the release they ship with, which may be the next one (the bump lands in the
    # release PR). So: all four names share one version, the script's pattern builds them, and that
    # version is never older than atlas/__init__.py.
    script = _script()
    current = _version()
    for filename, block in _blocks():
        zips = set(re.findall(r"heliopause-v(\d+\.\d+\.\d+)\.zip\b", block))
        assert len(zips) == 1, f"{filename} names {sorted(zips)} zip versions"
        (version,) = zips
        assert _semver(version) >= _semver(current), f"{filename} documents v{version}, older than {current}"
        expected = _built_names(script, version)
        found = _published_names(block, script)
        assert found == expected, (
            f"{filename} publishes {sorted(found)}; scripts/release-zip.sh builds {sorted(expected)} for v{version}"
        )


@pytest.mark.skipif(not os.path.isfile(SSH_KEYGEN), reason="/usr/bin/ssh-keygen is missing")
def test_readme_fingerprint_matches_release_signers(tmp_path: Path) -> None:
    keys = _signer_lines()
    assert len(keys) == 1, f"atlas/release_signers has {len(keys)} non-comment key lines"
    kind, blob = keys[0].split()[-2:]
    assert kind.startswith("ssh-"), kind
    pub = tmp_path / "release.pub"
    pub.write_text(f"{kind} {blob}\n", encoding="utf-8")
    proc = subprocess.run(
        [SSH_KEYGEN, "-lf", str(pub)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    fields = proc.stdout.split()
    assert len(fields) >= 2 and fields[1].startswith("SHA256:"), proc.stdout
    fingerprint = fields[1]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    printed = re.findall(r"SHA256:[A-Za-z0-9+/]+={0,2}", readme)
    assert printed, "README.md prints no SHA256: fingerprint"
    assert set(printed) == {fingerprint}, (
        f"README.md prints {sorted(set(printed))}; "
        f"ssh-keygen -lf of atlas/release_signers is {fingerprint}"
    )


def test_docs_omit_atlas_version_placeholder() -> None:
    needle = "atlas-<version>"
    for filename in ("README.md", "SECURITY.md"):
        text = (ROOT / filename).read_text(encoding="utf-8")
        assert needle not in text, f"{filename} contains {needle!r}"


def test_security_key_change_line_is_the_approved_text() -> None:
    # Approved wording, kept word for word. 0.7 has no updater command.
    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert (
        "If the signing key is ever lost or stolen, it can't be revoked in copies you already have: "
        "a file signed with the old key still passes a check against the old fingerprint. "
        "If we change the key, the new key ships in a new release. Download it, check the new "
        "fingerprint published in the README and the release notes, then run the check above "
        "before you unzip it."
    ) in text
    for filename in ("README.md", "SECURITY.md", "INSTALL.md"):
        path = ROOT / filename
        if path.exists():
            assert "atlas update" not in path.read_text(encoding="utf-8"), filename

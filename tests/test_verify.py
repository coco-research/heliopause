"""Signed release manifests."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import atlas
import atlas.verify as verify_mod

ROOT = Path(__file__).resolve().parents[1]
_FINGERPRINT = "SHA256:BhX36VYf09D8ozPzKQ76hAQEQjJ75y6pR+O4UNkeQCw"
_KEY_LINE = (
    "atlas-release namespaces=\"atlas-release\" "
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIArkj2zdOYSu0g8yfYV7Vru1PD1bUM/CKUUZ+QX2bXEV"
)


def _y_verify() -> bool:
    exe = "/usr/bin/ssh-keygen"
    if not os.path.isfile(exe):
        return False
    try:
        proc = subprocess.run([exe, "-Y", "verify"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = proc.stderr.decode("utf-8", "replace").lower()
    if "illegal option" in text or "unknown option" in text:
        return False
    return "verify" in text


requires_ssh = pytest.mark.skipif(not _y_verify(), reason="ssh-keygen lacks -Y verify")


def _next(version: str) -> str:
    parts = [int(part) for part in version.split(".")]
    parts[-1] += 1
    return ".".join(str(part) for part in parts)


def _key(tmp: Path, name: str) -> tuple[Path, Path]:
    secret = tmp / name
    subprocess.run(
        ["/usr/bin/ssh-keygen", "-t", "ed25519", "-N", "", "-q", "-f", str(secret), "-C", name],
        check=True,
        capture_output=True,
    )
    kind, blob = (tmp / f"{name}.pub").read_text(encoding="utf-8").split()[:2]
    signers = tmp / f"{name}.signers"
    signers.write_text(
        f'atlas-release namespaces="atlas-release" {kind} {blob}\n',
        encoding="utf-8",
    )
    return secret, signers


def _write(path: Path, entries: list[tuple]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for entry in entries:
            info = zipfile.ZipInfo(entry[0])
            if len(entry) == 3:
                info.external_attr = entry[2]
            zf.writestr(info, entry[1])


def _sign(path: Path, key: Path, namespace: str = "atlas-release") -> None:
    subprocess.run(
        ["/usr/bin/ssh-keygen", "-Y", "sign", "-q", "-f", str(key), "-n", namespace, str(path)],
        check=True,
        capture_output=True,
    )


def _manifest(
    zip_path: Path,
    version: str,
    dest: Path | None = None,
    *,
    size: int | None = None,
    sha: str | None = None,
) -> Path:
    data = zip_path.read_bytes()
    doc = {
        "version": version,
        "filename": zip_path.name,
        "size": len(data) if size is None else size,
        "sha256": hashlib.sha256(data).hexdigest() if sha is None else sha,
        "date": "2026-10-19T15:00:00Z",
    }
    path = dest if dest is not None else zip_path.with_name(zip_path.name + ".manifest")
    path.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    return path


def _verify(
    zip_path: Path,
    manifest: Path,
    *,
    allow: bool = False,
    signers: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [
        str(zip_path),
        "--manifest",
        str(manifest),
        "--sig",
        str(manifest) + ".sig",
    ]
    if allow:
        argv.append("--allow-downgrade")
    stdout, stderr = io.StringIO(), io.StringIO()
    previous = verify_mod.signers_path
    if signers is not None:
        verify_mod.signers_path = lambda: signers
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = verify_mod.main(argv)
    finally:
        verify_mod.signers_path = previous
    return subprocess.CompletedProcess(argv, code, stdout.getvalue(), stderr.getvalue())


@requires_ssh
def test_good_manifest(tmp_path: Path) -> None:
    key, signers = _key(tmp_path, "good")
    path = tmp_path / "atlas-v9.9.9.zip"
    _write(path, [("readme.txt", b"hello\n")])
    version = _next(atlas.__version__)
    manifest = _manifest(path, version)
    _sign(manifest, key)
    proc = _verify(path, manifest, signers=signers)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"OK {version}"


@requires_ssh
def test_refuses_current_or_older(tmp_path: Path) -> None:
    key, signers = _key(tmp_path, "old")
    path = tmp_path / "atlas-v0.0.1.zip"
    _write(path, [("readme.txt", b"hello\n")])
    current = _manifest(path, atlas.__version__)
    _sign(current, key)
    refused = _verify(path, current, signers=signers)
    assert refused.returncode == 6
    assert "OK" not in refused.stdout
    allowed = _verify(path, current, allow=True, signers=signers)
    assert allowed.returncode == 0, allowed.stderr
    assert allowed.stdout.strip() == f"OK {atlas.__version__}"
    lower = _manifest(path, "0.0.0", path.with_name("lower.manifest"))
    _sign(lower, key)
    assert _verify(path, lower, signers=signers).returncode == 6


@requires_ssh
def test_filename_tag_ignored(tmp_path: Path) -> None:
    key, signers = _key(tmp_path, "name")
    future_name = tmp_path / "atlas-v99.0.0.zip"
    _write(future_name, [("readme.txt", b"hello\n")])
    old = _manifest(future_name, atlas.__version__)
    _sign(old, key)
    assert _verify(future_name, old, signers=signers).returncode == 6

    past_name = tmp_path / "atlas-v0.0.1.zip"
    _write(past_name, [("readme.txt", b"hello\n")])
    version = _next(atlas.__version__)
    newer = _manifest(past_name, version)
    _sign(newer, key)
    proc = _verify(past_name, newer, signers=signers)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"OK {version}"


@requires_ssh
def test_zip_signers_ignored(tmp_path: Path) -> None:
    installed_key, installed = _key(tmp_path, "installed")
    planted_key, planted = _key(tmp_path, "planted")
    path = tmp_path / "atlas-v9.zip"
    _write(
        path,
        [
            ("readme.txt", b"hello\n"),
            ("atlas/release_signers", planted.read_bytes()),
        ],
    )
    version = _next(atlas.__version__)
    good = _manifest(path, version)
    _sign(good, installed_key)
    proc = _verify(path, good, signers=installed)
    assert proc.returncode == 0, proc.stderr

    bad = _manifest(path, version, path.with_name("planted.manifest"))
    _sign(bad, planted_key)
    proc = _verify(path, bad, signers=installed)
    assert proc.returncode == 2
    assert "OK" not in proc.stdout


@requires_ssh
def test_wrong_key(tmp_path: Path) -> None:
    key, _signers = _key(tmp_path, "signer")
    _other, other_signers = _key(tmp_path, "other")
    path = tmp_path / "atlas.zip"
    _write(path, [("readme.txt", b"hello\n")])
    manifest = _manifest(path, _next(atlas.__version__))
    _sign(manifest, key)
    assert _verify(path, manifest, signers=other_signers).returncode == 2


@requires_ssh
def test_wrong_namespace(tmp_path: Path) -> None:
    key, signers = _key(tmp_path, "ns")
    path = tmp_path / "atlas.zip"
    _write(path, [("readme.txt", b"hello\n")])
    manifest = _manifest(path, _next(atlas.__version__))
    _sign(manifest, key, namespace="other-namespace")
    assert _verify(path, manifest, signers=signers).returncode == 2


def _files(root: Path) -> set[Path]:
    return set(root.rglob("*"))


@requires_ssh
def test_corrupted_signature(tmp_path: Path) -> None:
    key, signers = _key(tmp_path, "corrupt")
    path = tmp_path / "atlas.zip"
    _write(path, [("readme.txt", b"hello\n")])
    manifest = _manifest(path, _next(atlas.__version__))
    _sign(manifest, key)
    sig = Path(str(manifest) + ".sig")
    lines = sig.read_bytes().splitlines(keepends=True)
    assert lines[0].startswith(b"-----BEGIN ")
    assert lines[-1].startswith(b"-----END ")
    body = bytearray(b"".join(lines[1:-1]))
    for index, byte in enumerate(body):
        if byte not in b"\r\n \t":
            body[index] ^= 0x01
            break
    else:
        raise AssertionError("armored signature body had no byte to flip")
    sig.write_bytes(lines[0] + bytes(body) + lines[-1])
    written = sig.read_bytes().splitlines(keepends=True)
    assert written[0] == lines[0]
    assert written[-1] == lines[-1]
    before = _files(tmp_path)
    proc = _verify(path, manifest, signers=signers)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "OK" not in proc.stdout and "success" not in proc.stdout.lower()
    assert "OK" not in proc.stderr and "success" not in proc.stderr.lower()
    assert _files(tmp_path) == before


@requires_ssh
def test_manifest_edited_after_signing(tmp_path: Path) -> None:
    key, signers = _key(tmp_path, "edited")
    path = tmp_path / "atlas.zip"
    _write(path, [("readme.txt", b"hello\n")])
    manifest = _manifest(path, _next(atlas.__version__))
    _sign(manifest, key)
    data = bytearray(manifest.read_bytes())
    at = bytes(data).find(b"2026-10-19T15:00:00Z")
    assert at != -1
    data[at + 9] = ord("8")
    manifest.write_bytes(data)
    before = _files(tmp_path)
    proc = _verify(path, manifest, signers=signers)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "OK" not in proc.stdout and "success" not in proc.stdout.lower()
    assert "OK" not in proc.stderr and "success" not in proc.stderr.lower()
    assert _files(tmp_path) == before


@requires_ssh
def test_sha_mismatch(tmp_path: Path) -> None:
    key, signers = _key(tmp_path, "sha")
    path = tmp_path / "atlas.zip"
    _write(path, [("readme.txt", b"hello\n")])
    manifest = _manifest(path, _next(atlas.__version__))
    _sign(manifest, key)
    data = bytearray(path.read_bytes())
    data[-1] ^= 0x01
    path.write_bytes(data)
    proc = _verify(path, manifest, signers=signers)
    assert proc.returncode == 3
    assert "OK" not in proc.stdout


@requires_ssh
def test_size_mismatch(tmp_path: Path) -> None:
    key, signers = _key(tmp_path, "size")
    path = tmp_path / "atlas.zip"
    _write(path, [("readme.txt", b"hello\n")])
    manifest = _manifest(path, _next(atlas.__version__), size=path.stat().st_size + 1)
    _sign(manifest, key)
    proc = _verify(path, manifest, signers=signers)
    assert proc.returncode == 3
    assert "OK" not in proc.stdout


@requires_ssh
def test_symlink_archive_is_unsafe(tmp_path: Path) -> None:
    key, signers = _key(tmp_path, "link")
    path = tmp_path / "atlas.zip"
    link = (stat.S_IFLNK | 0o777) << 16
    _write(path, [("link", b"readme.txt", link)])
    manifest = _manifest(path, _next(atlas.__version__))
    _sign(manifest, key)
    assert _verify(path, manifest, signers=signers).returncode == 4


@requires_ssh
def test_signers_env_has_no_effect(tmp_path: Path) -> None:
    key, signers = _key(tmp_path, "env")
    path = tmp_path / "atlas.zip"
    _write(path, [("readme.txt", b"hello\n")])
    manifest = _manifest(path, _next(atlas.__version__))
    _sign(manifest, key)
    env = os.environ.copy()
    env["ATLAS_TEST"] = "1"
    env["ATLAS_RELEASE_SIGNERS"] = str(signers)
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "atlas",
            "verify",
            str(path),
            "--manifest",
            str(manifest),
            "--sig",
            str(manifest) + ".sig",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2
    assert "OK" not in proc.stdout


def test_manifest_and_sig_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "a.zip"
    _write(path, [("readme.txt", b"hi\n")])
    calls: list = []

    def spy(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args[0] if args else [], 1, b"", b"")

    monkeypatch.setattr(verify_mod.subprocess, "run", spy)

    manifest = tmp_path / "at.manifest"
    manifest.write_bytes(b" " * (64 * 1024))
    Path(str(manifest) + ".sig").write_bytes(b"nope")
    at_cap = _verify(path, manifest)
    assert calls, "a manifest of exactly 64 KiB is read and checked"
    assert at_cap.returncode != 0

    calls.clear()
    over = tmp_path / "over.manifest"
    over.write_bytes(b" " * (64 * 1024 + 1))
    Path(str(over) + ".sig").write_bytes(b"nope")
    too_big = _verify(path, over)
    assert too_big.returncode == 1
    assert calls == []

    calls.clear()
    small = _manifest(path, "9.9.9", tmp_path / "small.manifest")
    Path(str(small) + ".sig").write_bytes(b"x" * (8 * 1024 + 1))
    huge_sig = _verify(path, small)
    assert huge_sig.returncode == 2
    assert calls == []
    assert "OK" not in huge_sig.stdout


def test_same_inode_rewrite_during_check_is_caught(tmp_path: Path) -> None:
    path = tmp_path / "race.zip"
    _write(path, [("readme.txt", b"hello\n")])
    good = path.read_bytes()
    other = tmp_path / "evil.zip"
    _write(other, [("readme.txt", b"evil\n")])
    evil = other.read_bytes()
    real = verify_mod._same_bytes

    def rewrite(src_fd: int, digest: str, total: int, limit: int) -> bool:
        writer = os.open(path, os.O_WRONLY | os.O_TRUNC)
        try:
            os.write(writer, evil)
        finally:
            os.close(writer)
        return real(src_fd, digest, total, limit)

    verify_mod._same_bytes = rewrite
    held = None
    try:
        code, held = verify_mod._check_zip(path, len(good), hashlib.sha256(good).hexdigest())
    finally:
        verify_mod._same_bytes = real
        if held is not None:
            os.close(held)
    assert code == 3
    assert held is None
    assert path.read_bytes() == evil


@requires_ssh
def test_release_signers_fingerprint(tmp_path: Path) -> None:
    text = (ROOT / "atlas" / "release_signers").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].startswith("#")
    keys = [line for line in lines if line and not line.startswith("#")]
    assert keys == [_KEY_LINE]
    kind, blob = keys[0].split()[-2:]
    pub = tmp_path / "release.pub"
    pub.write_text(f"{kind} {blob}\n", encoding="utf-8")
    proc = subprocess.run(
        ["/usr/bin/ssh-keygen", "-lf", str(pub)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.stdout.split()[1] == _FINGERPRINT
    assert _FINGERPRINT in (ROOT / "README.md").read_text(encoding="utf-8")
    assert _FINGERPRINT in (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def test_release_zip_refuses_without_pubkey(tmp_path: Path) -> None:
    out = tmp_path / "out"
    env = os.environ.copy()
    env.pop("ATLAS_SIGNING_KEY", None)
    env.pop("ATLAS_SIGNING_PUBKEY", None)
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "release-zip.sh"), "v9.9.9", str(out)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "ATLAS_SIGNING_PUBKEY" in proc.stderr
    assert not out.exists()
    assert proc.stdout == ""


@requires_ssh
def test_release_zip_refuses_without_agent(tmp_path: Path) -> None:
    key = tmp_path / "release-key"
    subprocess.run(
        ["/usr/bin/ssh-keygen", "-t", "ed25519", "-N", "", "-q", "-f", str(key), "-C", "t"],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "out"
    env = os.environ.copy()
    env["ATLAS_SIGNING_PUBKEY"] = str(key) + ".pub"
    env.pop("ATLAS_SIGNING_KEY", None)
    env.pop("SSH_AUTH_SOCK", None)
    env.pop("SSH_AGENT_PID", None)
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "release-zip.sh"), "v9.9.9", str(out)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "ssh-agent" in proc.stderr
    assert not out.exists()
    assert proc.stdout == ""

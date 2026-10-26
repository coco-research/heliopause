"""Check a signed release manifest, then the zip it names. Unpack is separate."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

from atlas import labfs

# Caps apply while scanning and while copying. A stored zip at the cap is the
# largest release this helper will write.
_MAX_ENTRIES = 5000
_MAX_UNCOMPRESSED = 200 * 1024 * 1024
_MAX_MANIFEST = 64 * 1024
_MAX_SIG = 8 * 1024
_SSH_KEYGEN = "/usr/bin/ssh-keygen"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_MESSAGES = {
    1: "atlas: verify failed",
    2: "atlas: bad signature",
    3: "atlas: bad checksum",
    4: "atlas: unsafe archive",
    5: "atlas: ssh-keygen missing or too old (need OpenSSH >= 8.2 with -Y verify)",
    6: "atlas: refused downgrade",
}


class UnsafeArchive(Exception):
    """The zip is not safe to scan."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"atlas: {message}", file=sys.stderr)
        raise SystemExit(1)


def signers_path() -> Path:
    # The shipped file is the only trust anchor. No environment variable and no
    # CLI flag selects another file.
    return Path(__file__).resolve().parent / "release_signers"


def _parse(argv: list[str] | None) -> tuple[Path, Path, Path, bool]:
    parser = _Parser(prog="atlas verify")
    parser.add_argument("zip", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sig", type=Path)
    parser.add_argument("--allow-downgrade", action="store_true")
    args = parser.parse_args(argv)
    sig = args.sig if args.sig is not None else Path(str(args.manifest) + ".sig")
    return args.zip, args.manifest, sig, args.allow_downgrade


def _running_version() -> str:
    from atlas import __version__

    return __version__


def _numbers(text: str) -> tuple[int, ...] | None:
    # ponytail: dotted integers only; "1.2.3-rc1" is not newer. Upgrade path: PEP 440.
    raw = text.strip()
    if len(raw) >= 2 and raw[0] in "vV" and raw[1].isdigit():
        raw = raw[1:]
    parts = raw.split(".")
    numbers = []
    for part in parts:
        if not part.isdigit() or (len(part) > 1 and part.startswith("0")):
            return None
        numbers.append(int(part))
    if not numbers:
        return None
    return tuple(numbers)


def _newer(candidate: str, current: str) -> bool:
    left = _numbers(candidate)
    right = _numbers(current)
    if left is None or right is None:
        return False
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left > right


def _read_capped(path: Path, limit: int) -> bytes | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return None
    if len(data) > limit:
        return None
    return data


def _sig_bounded(path: Path) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_size <= _MAX_SIG


def _ssh_ok() -> bool:
    if not os.path.isfile(_SSH_KEYGEN) or not os.access(_SSH_KEYGEN, os.X_OK):
        return False
    try:
        proc = subprocess.run(
            [_SSH_KEYGEN, "-Y", "verify"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = (proc.stderr + proc.stdout).decode("utf-8", "replace").lower()
    if "illegal option" in text or "unknown option" in text or "verify" not in text:
        return False
    return True


def _sig_ok(manifest: bytes, sig_path: Path, signers: Path) -> bool:
    cmd = [
        _SSH_KEYGEN,
        "-Y",
        "verify",
        "-f",
        str(signers),
        "-I",
        "atlas-release",
        "-n",
        "atlas-release",
        "-s",
        str(sig_path),
    ]
    proc = subprocess.run(
        cmd,
        input=manifest,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    return proc.returncode == 0


def _manifest_doc(data: bytes) -> dict | None:
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    version = doc.get("version")
    filename = doc.get("filename")
    size = doc.get("size")
    sha = doc.get("sha256")
    date = doc.get("date")
    if not isinstance(version, str) or not version.strip():
        return None
    if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename:
        return None
    if type(size) is not int or size < 0:
        return None
    if not isinstance(sha, str):
        return None
    sha = sha.lower()
    if len(sha) != 64 or any(char not in "0123456789abcdef" for char in sha):
        return None
    if not isinstance(date, str) or _DATE.match(date) is None:
        return None
    return {
        "version": version.strip(),
        "filename": filename,
        "size": size,
        "sha256": sha,
        "date": date,
    }


def _parts(name: str) -> list[str] | None:
    if not name or "\x00" in name or "\\" in name:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        return None
    if name.startswith("/") or (len(name) >= 2 and name[0].isalpha() and name[1] == ":"):
        return None
    trimmed = name[:-1] if name.endswith("/") else name
    if not trimmed:
        return None
    parts = trimmed.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return parts


def _mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0xFFFF


def _scan(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > _MAX_ENTRIES:
        raise UnsafeArchive("refusing too many entries")
    total = 0
    for info in infos:
        if _parts(info.filename) is None:
            raise UnsafeArchive("refusing path")
        mode = _mode(info)
        if stat.S_ISLNK(mode):
            raise UnsafeArchive("refusing symlink entry")
        if mode & (stat.S_ISUID | stat.S_ISGID):
            raise UnsafeArchive("refusing setuid or setgid")
        size = info.file_size
        if size < 0 or size > _MAX_UNCOMPRESSED or total > _MAX_UNCOMPRESSED - size:
            raise UnsafeArchive("refusing uncompressed size")
        total += size


def _sha256_fd(fd: int, limit: int | None = None) -> tuple[str, int]:
    os.lseek(fd, 0, os.SEEK_SET)
    hashed = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if limit is not None and total > limit:
            return "", total
        hashed.update(chunk)
    return hashed.hexdigest(), total


def _copy_into(src: int, dst: int, limit: int) -> tuple[str, int] | None:
    hashed = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(src, 1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            return None
        hashed.update(chunk)
        if not labfs.write_fd(dst, chunk):
            raise OSError("short write while sealing the private copy")
    return hashed.hexdigest(), total


def _same_bytes(src_fd: int, digest: str, total: int, limit: int) -> bool:
    """Re-read src_fd. A same-inode rewrite during the check fails here."""
    again, got = _sha256_fd(src_fd, limit)
    if got != total or not again:
        return False
    return hmac.compare_digest(again, digest)


def _seal(src_fd: int, limit: int) -> tuple[int, str, int] | None:
    """Copy src_fd into an unlinked file and hash that copy.

    The returned fd is the only byte stream later code may scan.
    None means the file grew past limit, or the source inode changed mid-read.
    """
    copy_fd = labfs.unlinked_fd()
    try:
        os.lseek(src_fd, 0, os.SEEK_SET)
        copied = _copy_into(src_fd, copy_fd, limit)
        if copied is None or not _same_bytes(src_fd, copied[0], copied[1], limit):
            os.close(copy_fd)
            return None
        os.lseek(copy_fd, 0, os.SEEK_SET)
        return copy_fd, copied[0], copied[1]
    except Exception:
        os.close(copy_fd)
        raise


def _check_zip(path: Path, size: int, digest: str) -> tuple[int, int | None]:
    """Hash, scan, and keep a private copy of path.

    On success the fd is an unlinked copy positioned at 0. The caller closes it.
    The scan uses that copy, not a second open of path.
    """
    if size < 0 or size > _MAX_UNCOMPRESSED:
        return 3, None
    try:
        st = os.lstat(path)
    except OSError:
        return 1, None
    if not stat.S_ISREG(st.st_mode):
        return 1, None
    try:
        fd = labfs.open_read_nofollow(path)
    except OSError:
        return 1, None
    copy_fd = -1
    try:
        st_fd = os.fstat(fd)
        if st_fd.st_dev != st.st_dev or st_fd.st_ino != st.st_ino:
            return 1, None
        if st_fd.st_size != size:
            return 3, None
        sealed = _seal(fd, size)
        if sealed is None:
            return 3, None
        copy_fd, actual, total = sealed
        if total != size or not hmac.compare_digest(actual, digest):
            return 3, None
        scan_fd = os.dup(copy_fd)
        try:
            with labfs.open_zip_read(scan_fd) as zf:
                _scan(zf)
        except zipfile.BadZipFile:
            return 4, None
        except UnsafeArchive:
            return 4, None
        os.lseek(copy_fd, 0, os.SEEK_SET)
        held, copy_fd = copy_fd, -1
        return 0, held
    finally:
        os.close(fd)
        if copy_fd >= 0:
            os.close(copy_fd)


def _verify(argv: list[str] | None) -> tuple[int, str, int | None]:
    """Return (code, version, copy_fd). copy_fd is set only when code is 0.

    The fd is a private copy of the zip. The caller closes it.
    """
    zip_path, manifest_path, sig_path, allow = _parse(argv)
    try:
        if not stat.S_ISREG(os.lstat(zip_path).st_mode):
            return 1, "", None
    except OSError:
        return 1, "", None
    manifest = _read_capped(manifest_path, _MAX_MANIFEST)
    if manifest is None:
        return 1, "", None
    if not _sig_bounded(sig_path):
        return 2, "", None
    if not _ssh_ok():
        return 5, "", None
    try:
        if not _sig_ok(manifest, sig_path, signers_path()):
            return 2, "", None
    except (OSError, subprocess.TimeoutExpired):
        return 1, "", None
    doc = _manifest_doc(manifest)
    if doc is None:
        return 1, "", None
    code, copy_fd = _check_zip(zip_path, doc["size"], doc["sha256"])
    if code != 0:
        return code, "", None
    version = doc["version"]
    if not allow and not _newer(version, _running_version()):
        if copy_fd is not None:
            os.close(copy_fd)
        return 6, "", None
    return 0, version, copy_fd


def main(argv: list[str] | None = None) -> int:
    copy_fd = None
    try:
        code, version, copy_fd = _verify(argv)
    except Exception as err:
        print(f"atlas: verify failed ({type(err).__name__})", file=sys.stderr)
        return 1
    finally:
        if copy_fd is not None:
            os.close(copy_fd)
    if code == 0:
        print(f"OK {version}")
        return 0
    print(_MESSAGES.get(code, _MESSAGES[1]), file=sys.stderr)
    return code if code in _MESSAGES else 1


if __name__ == "__main__":
    sys.exit(main())

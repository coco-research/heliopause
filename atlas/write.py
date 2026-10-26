"""Append-only board writes. Token and actor live in `<root>/.atlas/`."""

from __future__ import annotations

import errno
import fcntl
import hmac
import json
import os
import re
import secrets
import stat
import unicodedata
from datetime import datetime
from pathlib import Path

from atlas import labfs
from atlas.collect import LAB, LAB_LABELS, LEGACY_STATE, NODE_IDS, _NY, _SEP, _STATES, _is_headline, board_path, map_path

_MAX_BODY = 8192
_OPEN = os.O_NOFOLLOW | os.O_CLOEXEC
_JSON = re.compile(r"application/json(?:\s*;\s*charset\s*=\s*utf-8)?$", re.IGNORECASE)
_NAMES: dict[str, str] = {}
if LAB:
    # Board column is the label, same as a non-lab alias. Includes the owner id "owner".
    _NAMES.update(LAB_LABELS)
else:
    for _key, _node in json.loads(map_path("aliases.json").read_text(encoding="utf-8")).items():
        _NAMES.setdefault(_node, _key)  # first aliases.json key for that id is the board name

def _err(code: int, reason: str) -> tuple[int, dict]:
    return code, {"error": reason}

BUSY = "busy, retry"


def busy(err: OSError) -> bool:
    """True when err is the process (EMFILE) or system (ENFILE) running out of file handles: retry, not missing."""
    return getattr(err, "errno", None) in (errno.EMFILE, errno.ENFILE)


def _busy() -> tuple[int, dict]:
    return _err(503, BUSY)  # the server adds Retry-After

def _write_all(fd: int, encoded: bytes) -> bool:
    return labfs.write_fd(fd, encoded)

def _slurp(root: Path, rel: str, limit: int, chmod: bool) -> str:
    # labfs.open_read: no link on the way, O_NOFOLLOW, and O_NONBLOCK so a FIFO cannot block the open.
    fd = labfs.open_read(root, rel)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EPERM, "not a regular file")
        if info.st_nlink != 1:
            raise OSError(errno.EPERM, "hard-linked file refused")
        if chmod:
            os.fchmod(fd, 0o600)
        data = os.read(fd, limit)
        extra = os.read(fd, 1)
    finally:
        os.close(fd)
    if extra or not data:
        return ""
    try:
        return data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return ""

class TokenSetupError(RuntimeError):
    """The write token could not be created or read."""


def _existing_token(root: Path) -> str:
    token = _slurp(root, ".atlas/token", 256, True)
    if not token:  # an empty or unreadable file would leave every write 401 with no reason
        raise OSError(errno.EINVAL, "token file is empty or unreadable; stop Heliopause, delete it, and restart")
    return token


def ensure_token(root: Path) -> str:
    # WHY: .atlas is made and kept 0700, and the token is created 0600 in one exclusive step (labfs: a temp
    # file hard-linked into place, so the name stays absent until the write is done). No link is followed.
    labfs.makedirs(root, ".atlas", 0o700)
    labfs.chmod(root, ".atlas", 0o700, folder=True)
    if labfs.lstat(root, ".atlas/token") is not None:
        return _existing_token(root)
    token = secrets.token_urlsafe(32)
    try:
        labfs.write(root, ".atlas/token", token.encode("ascii"), 0o600, new_only=True)
    except FileExistsError:
        return _existing_token(root)
    except labfs.Refused as err:
        if "appeared" not in err.why:
            raise
        return _existing_token(root)  # another process won the race
    return token

def _actor_id(root: Path) -> str:
    try:
        return _slurp(root, ".atlas/actor", 128, False)
    except FileNotFoundError:
        return "vp"
    except OSError as err:
        if busy(err):
            raise  # out of file handles is not an unknown actor; callers answer busy
        return ""

def _controls(text: str) -> bool:
    return any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp") for char in text)

def _length(headers) -> int | None:
    values = headers.get_all("Content-Length") or []
    if len(values) != 1:
        return None
    text = values[0].strip()
    return int(text) if text.isdecimal() and len(text) <= 10 else None

def _board(root: Path) -> tuple[Path, str] | None:
    """(folder, name) of the board for labfs, or None when it is missing, a link, or not a regular file."""
    path = board_path(root)
    inbox = Path(root) / ".heliopause" / "inbox" / "lab" / "board.md"
    base, rel = (Path(root), ".heliopause/inbox/lab/board.md") if path == inbox else (path.parent, path.name)
    try:
        info = labfs.lstat(base, rel)
    except OSError as err:
        if busy(err):
            raise
        return None
    if info is None or not stat.S_ISREG(info.st_mode):
        return None
    return base, rel

def _needs_prefix(fd: int) -> bool:
    size = os.fstat(fd).st_size
    if size <= 0:
        return False  # an empty board starts with the line, not a blank line
    return os.pread(fd, 1, size - 1) != b"\n"

def _append(root: Path, name: str, state: str, headline: str, details: str) -> tuple[int, dict]:
    try:
        path = _board(root)
    except OSError:
        return _busy()
    if path is None:
        return _err(503, "no board")
    # WHY: the reader keeps column 4 as the headline only when a 5th column is present.
    # A lab board carries no zone label: its times are in the lab's own zone (atlas.json tz).
    stamp = f"{datetime.now(_NY):%Y-%m-%d %H:%M}" + ("" if LAB else " ET")
    line = _SEP.join((stamp, name, state, headline, details))
    try:
        # labfs: no link on the way or at the board, one hard link, O_APPEND (read too, for the torn-line check).
        fd = labfs.open_append(path[0], path[1], create=False, read=True)
    except OSError as err:
        return _busy() if busy(err) else _err(503, "no board")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            encoded = (("\n" if _needs_prefix(fd) else "") + line + "\n").encode()
            if not _write_all(fd, encoded):
                return _err(500, "write failed")
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        return _err(500, "write failed")
    finally:
        os.close(fd)
    return 201, {"ok": True, "line": line}

def _fields(data: dict) -> tuple[int, dict] | tuple[str, str, str]:
    if set(data) - {"state", "headline", "details"}:
        return _err(400, "extra key")
    state, headline = data.get("state"), data.get("headline")
    if isinstance(state, str):
        state = LEGACY_STATE.get(state, state)  # an older word is written as the current one
    if not isinstance(state, str) or _controls(state) or state not in _STATES:
        return _err(400, "bad state")
    if not isinstance(headline, str) or _controls(headline):
        return _err(400, "bad headline")
    text = headline.strip()
    if not 1 <= len(text) <= 60 or _SEP in text:
        return _err(400, "bad headline")
    if not _is_headline(text):
        return _err(400, "bad headline")
    if "details" not in data:
        return _err(400, "bad details")
    details = data["details"]
    if not isinstance(details, str) or _controls(details):
        return _err(400, "bad details")
    detail = details.strip()
    if not detail:
        return _err(400, "bad details")
    if len(detail) > 500:
        return _err(400, "bad details")
    return state, text, detail

def read_body(token: str, headers, rfile) -> tuple[int, dict] | dict:
    """Shared POST gate: origin, token, content type, length, then one JSON object."""
    if "Origin" in headers:
        return _err(403, "origin")
    supplied = headers.get_all("X-Atlas-Token") or []
    if (
        len(supplied) != 1
        or not token
        or not hmac.compare_digest(
            supplied[0].encode("utf-8", "surrogateescape"), token.encode("utf-8")
        )
    ):
        return _err(401, "unauthorized")
    types = headers.get_all("Content-Type") or []
    if len(types) != 1 or not _JSON.match(types[0].strip()):
        return _err(415, "content type")
    length = _length(headers)
    if length is None:
        return _err(411, "length required")
    if length > _MAX_BODY:
        return _err(413, "too large")
    raw = rfile.read(length)
    if len(raw) != length:
        return _err(400, "short body")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        return _err(400, "json")
    if not isinstance(data, dict):
        return _err(400, "not an object")
    return data

def actor_node(root: Path) -> str | None:
    node = _actor_id(root)
    # Same rule as /api/post: a node id with no board name cannot write.
    return node if node in NODE_IDS and node in _NAMES else None

def accept(root: Path, token: str, headers, rfile) -> tuple[int, dict]:
    data = read_body(token, headers, rfile)
    if isinstance(data, tuple):
        return data
    parsed = _fields(data)
    if isinstance(parsed[0], int):
        return parsed
    try:
        node = actor_node(root)
    except OSError:
        return _busy()
    if node is None:
        return _err(403, "unknown actor")
    state, headline, details = parsed
    return _append(root, _NAMES[node], state, headline, details)

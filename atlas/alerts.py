"""Alert lane. Tools append a short alert; Heliopause shows it. Display only: never forwarded."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import stat
import time
import unicodedata
from pathlib import Path

from atlas import labfs
from atlas.collect import redact
from atlas.write import _JSON, _err, _length, _write_all, read_body

# ponytail: one backup (alerts.jsonl.1) is the ceiling; the next rotation drops older history.
ROTATE_BYTES = 1_000_000
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
_ID = re.compile(r"[A-Za-z0-9._-]{1,80}\Z")
_SEVERITY = {"info", "warn", "needs-owner"}
_GH = "https://github.com/coco-research/"
_CONTROL = ("Cc", "Cf", "Cs", "Zl", "Zp")
# ponytail: a UI close sends `{}` and a feedback vote under 215 bytes. Anything past 256 bytes is rejected rather than parsed.
_UI_BODY = 256

def _name(value: object) -> str | None:
    if isinstance(value, str) and 1 <= len(value) <= 40 and _NAME.fullmatch(value):
        return value
    return None

def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        # WHY: the board lane's _controls set; a lone surrogate would also break the UTF-8 write.
        ch for ch in value if unicodedata.category(ch) not in _CONTROL
    )
    # WHY: alerts must carry no secrets; the same redaction as board lines runs before anything is stored.
    collapsed = redact(" ".join(cleaned.split()))
    return collapsed if 1 <= len(collapsed) <= 200 else None

def _link(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 300:
        return None
    if ".." in value or "\\" in value or any(unicodedata.category(ch) in _CONTROL for ch in value):
        return None
    if value.startswith(_GH) or (value.startswith("/") and not value.startswith("//")):
        return redact(value)
    return None

def _fields(data: dict) -> tuple[int, dict] | dict:
    if set(data) - {"source", "kind", "severity", "text", "link", "id"}:
        return _err(400, "extra key")
    source, kind = _name(data.get("source")), _name(data.get("kind"))
    if source is None:
        return _err(400, "bad source")
    if kind is None:
        return _err(400, "bad kind")
    severity = data.get("severity")
    if not isinstance(severity, str) or severity not in _SEVERITY:
        return _err(400, "bad severity")
    text = _text(data.get("text"))
    if text is None:
        return _err(400, "bad text")
    link = None
    if "link" in data:
        link = _link(data["link"])
        if link is None:
            return _err(400, "bad link")
    if "id" in data:
        ident = data["id"]
        if not isinstance(ident, str) or _ID.fullmatch(ident) is None:
            return _err(400, "bad id")
    else:
        ident = hashlib.sha1(f"{source}|{kind}|{severity}|{text}".encode()).hexdigest()[:16]
    return {"id": ident, "source": source, "kind": kind, "severity": severity, "text": text, "link": link}

_LOG = ".atlas/alerts.jsonl"


def _read(root: Path, rel: str) -> list:
    try:
        fd = labfs.open_read(root, rel)  # no link on the way; O_NONBLOCK, so a FIFO cannot block the open
    except OSError:
        return []
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return []
        parts = []
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            parts.append(block)
    finally:
        os.close(fd)
    found = []
    for line in b"".join(parts).decode("utf-8", "replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            found.append(item)
    return found

def _view(row: dict) -> dict | None:
    ts = row.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, int) or not isinstance(row.get("severity"), str) or row.get("severity") not in _SEVERITY:
        return None
    if any(not isinstance(row.get(key), str) or not row[key] for key in ("id", "source", "kind", "text")):
        return None
    item = {key: row[key] for key in ("ts", "id", "source", "kind", "severity", "text")}
    if "link" in row:
        if not isinstance(row["link"], str):
            return None
        item["link"] = row["link"]
    item["state"] = "open"
    return item

def _fold(rows: list) -> list:
    """One row per id, file order. A later open line replaces it; a later done line closes it."""
    by_id: dict = {}
    order: list = []
    for row in rows:
        ident, state = row.get("id"), row.get("state")
        if not isinstance(ident, str) or state not in ("open", "done"):
            continue
        if state == "done":
            if ident in by_id:
                by_id[ident]["state"] = "done"
                closed = row.get("ts")
                # WHY: the closed time is the done line's ts, so the page can show "closed 5m". A bool is an int in Python, so it is barred.
                if isinstance(closed, int) and not isinstance(closed, bool):
                    by_id[ident]["closed_ts"] = closed
            continue
        item = _view(row)
        if item is None:
            continue
        if ident in by_id:
            order.remove(ident)
        order.append(ident)
        by_id[ident] = item
    return [by_id[ident] for ident in order]

def _log(root: Path) -> list:
    return _fold(_read(root, _LOG + ".1") + _read(root, _LOG))

def list_alerts(root: Path) -> dict:
    rows = list(reversed(_log(root)))
    return {"open": [row for row in rows if row["state"] == "open"], "recent": rows[:50]}

def open_alerts(root: Path) -> list:
    return [row for row in _log(root) if row["state"] == "open"]

def _rotate(root: Path, rel: str = _LOG) -> None:
    info = labfs.lstat(root, rel)
    if info is None:
        return
    if not stat.S_ISREG(info.st_mode):
        raise OSError("not a regular file")
    if info.st_size > ROTATE_BYTES:
        labfs.rename(root, rel, rel + ".1", replace=True)

def _append(root: Path, rel: str, record: dict) -> bool:
    """Rotate, then append one JSON line to root/rel at 0600. False when the write came up short."""
    _rotate(root, rel)
    fd = labfs.open_append(root, rel, create=True, mode=0o600, read=True, fix_mode=True)
    try:
        # WHY: a torn last write must not swallow this record (same guard as handoffs.py).
        size = os.fstat(fd).st_size
        torn = size > 0 and os.pread(fd, 1, size - 1) != b"\n"
        blob = ("\n" if torn else "") + json.dumps(record, ensure_ascii=False) + "\n"
        return _write_all(fd, blob.encode())
    finally:
        os.close(fd)

def _write(root: Path, record: dict, ident: str, opening: bool) -> tuple[int, dict]:
    current = {row["id"]: row for row in _fold(_read(root, _LOG + ".1") + _read(root, _LOG))}
    row = current.get(ident)
    if opening and row is not None and row["state"] == "open":
        return 200, {"ok": True, "id": ident, "duplicate": True}
    if not opening and (row is None or row["state"] != "open"):
        return _err(404, "unknown id")
    if not _append(root, _LOG, record):
        return _err(500, "write failed")
    return (201, {"ok": True, "id": ident}) if opening else (200, {"ok": True})

def _locked(root: Path, write) -> tuple[int, dict]:
    """Run write(root) under the <root>/.atlas directory lock. Any OSError is a 500."""
    try:
        # ponytail: flock the directory. The log inode changes on rotation, so the log itself is not the lock.
        lock = labfs.open_dir(root, ".atlas", create=True, mode=0o700)
    except OSError:
        return _err(500, "write failed")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return write(root)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    except OSError:
        return _err(500, "write failed")
    finally:
        os.close(lock)

def _commit(root: Path, record: dict, ident: str, opening: bool) -> tuple[int, dict]:
    return _locked(root, lambda base: _write(base, record, ident, opening))

def _record(fields: dict) -> dict:
    record = {
        "ts": int(time.time()), "id": fields["id"], "source": fields["source"],
        "kind": fields["kind"], "severity": fields["severity"], "text": fields["text"],
    }
    if fields["link"] is not None:
        record["link"] = fields["link"]
    record["state"] = "open"
    return record

def accept_alert(root: Path, token: str, headers, rfile, ring=None) -> tuple[int, dict]:
    data = read_body(token, headers, rfile)
    if isinstance(data, tuple):
        return data
    fields = _fields(data)
    if isinstance(fields, tuple):
        return fields
    record = _record(fields)
    result = _commit(root, record, fields["id"], True)
    # WHY: ring only for a brand-new needs-owner alert; a duplicate (200) must not ring again.
    if ring is not None and result[0] == 201:
        ring(root, record)
    return result

def _finish_close(root: Path, ident: str) -> tuple[int, dict]:
    if not isinstance(ident, str) or _ID.fullmatch(ident) is None:
        return _err(404, "unknown id")
    return _commit(root, {"ts": int(time.time()), "id": ident, "state": "done"}, ident, False)

def close_alert(root: Path, token: str, headers, rfile, ident: str) -> tuple[int, dict]:
    data = read_body(token, headers, rfile)
    if isinstance(data, tuple):
        return data
    if data:
        return _err(400, "extra key")
    return _finish_close(root, ident)

def _ui_match(got: object, want: str) -> bool:
    if not isinstance(got, str) or not isinstance(want, str) or not want:
        return False
    return hmac.compare_digest(got.encode("utf-8", "surrogateescape"), want.encode("utf-8"))

def _ui_gate(nonce: str, headers, rfile, port: int) -> tuple[int, dict] | dict:
    """Page-only POST gate: same-origin, nonce, JSON, a small body. Returns the parsed object, or the error to send."""
    # WHY: the browser never sees the file token. A foreign page cannot read the nonce (no CORS) and cannot mint this Origin.
    origins = headers.get_all("Origin") or []
    allowed = (f"http://127.0.0.1:{port}", f"http://localhost:{port}")
    if len(origins) != 1 or origins[0] not in allowed:
        return _err(403, "origin")
    supplied = headers.get_all("X-Atlas-UI") or []
    if len(supplied) != 1 or not _ui_match(supplied[0], nonce):
        return _err(403, "nonce")
    sites = headers.get_all("Sec-Fetch-Site") or []
    if sites and (len(sites) != 1 or sites[0] != "same-origin"):
        return _err(403, "site")
    types = headers.get_all("Content-Type") or []
    if len(types) != 1 or _JSON.match(types[0].strip()) is None:
        return _err(403, "content type")
    length = _length(headers)
    if length is None or length > _UI_BODY:
        return _err(403, "length")
    raw = rfile.read(length)
    if len(raw) != length:
        return _err(403, "short body")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        return _err(403, "json")
    if not isinstance(data, dict):
        return _err(403, "not an object")
    return data

def close_alert_ui(root: Path, nonce: str, headers, rfile, ident: str, port: int) -> tuple[int, dict]:
    blocked = _ui_gate(nonce, headers, rfile, port)
    if isinstance(blocked, tuple):
        return blocked
    return _finish_close(root, ident)

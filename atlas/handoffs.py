"""Handoff log. Append-only events in `<root>/.heliopause/inbox/atlas/handoffs.jsonl`."""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from atlas import labfs
from atlas.collect import NODE_IDS, _SEP, _is_headline, redact
from atlas.events import _LABELS
from atlas.write import _NAMES, _append, _busy, _controls, _err, _write_all, actor_node, busy, read_body

_ID = re.compile(r"h-[0-9a-f]{8}\Z")
_DIR = ".heliopause/inbox/atlas"
_LOG = f"{_DIR}/handoffs.jsonl"
_NEXT = {"open": ("claim", "done"), "claimed": ("done",)}
_VIEW = ("id", "from", "to", "title", "details", "state", "ts")

def fold(records: list) -> dict[str, dict]:
    """Latest legal state per id. Unknown ids and illegal transitions are skipped."""
    out: dict[str, dict] = {}
    for rec in records:
        if not isinstance(rec, dict) or not isinstance(rec.get("id"), str) or not isinstance(rec.get("by"), str):
            continue
        op, ident, row = rec.get("op"), rec["id"], out.get(rec["id"])
        if op == "handoff" and row is None:
            title, target, ts, details = rec.get("title"), rec.get("to"), rec.get("ts"), rec.get("details", "")
            if isinstance(details, str) and all(isinstance(v, str) and v for v in (title, target, ts)):
                out[ident] = {
                    "id": ident, "from": rec["by"], "to": target, "title": title, "details": details,
                    "state": "open", "ts": ts, "updated": ts,
                }
        elif row is not None and op in _NEXT.get(row["state"], ()):
            row["state"] = "claimed" if op == "claim" else "done"
            if isinstance(rec.get("ts"), str):
                row["updated"] = rec["ts"]
    return out

def _records(fd: int) -> list:
    os.lseek(fd, 0, os.SEEK_SET)
    raw = bytearray()
    while True:
        block = os.read(fd, 65536)
        if not block:
            break
        raw += block
    text = raw.decode("utf-8", "replace")
    text = text if text.endswith("\n") else text.rpartition("\n")[0]
    found = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            found.append(item)
    return found

def load(root: Path) -> list:
    try:
        fd = labfs.open_read(root, _LOG)  # no link on the way; O_NONBLOCK, so a FIFO cannot block the open
    except OSError:
        return []
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return []
        fcntl.flock(fd, fcntl.LOCK_SH)
        try:
            return _records(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

def _clean(op: str, data: dict) -> tuple[int, dict] | dict:
    if set(data) - ({"to", "title", "details"} if op == "handoff" else {"id"}):
        return _err(400, "extra key")
    if op != "handoff":
        ident = data.get("id")
        return {"id": ident} if isinstance(ident, str) and _ID.fullmatch(ident) else _err(400, "bad id")
    target, title = data.get("to"), data.get("title")
    if not isinstance(target, str) or target not in NODE_IDS:
        return _err(400, "bad to")
    if not isinstance(title, str) or _controls(title) or _SEP in title.strip() or not _is_headline(title):
        return _err(400, "bad title")
    details = None
    if "details" in data:
        raw = data["details"]
        if not isinstance(raw, str) or _controls(raw) or not raw.strip() or len(raw.strip()) > 500:
            return _err(400, "bad details")
        details = raw.strip()
    return {"to": target, "title": title.strip(), "details": details}

def _no_inbox(root: Path, err: OSError | str) -> tuple[int, dict]:
    # The reply stays "no inbox"; the server log says which call failed and why, so a 503 can be traced.
    why = err if isinstance(err, str) else f"{type(err).__name__}: {err}"
    print(f"atlas: handoff not recorded under {root}: {why}", file=sys.stderr)
    if isinstance(err, OSError) and busy(err):
        return _busy()  # out of file handles (EMFILE/ENFILE): the inbox is fine, the client should retry
    return _err(503, "no inbox")


def _commit(root: Path, record: dict, guard) -> tuple[int, dict]:
    # ponytail: reread the whole log under the lock on every write. Keep an in-memory fold if it gets large.
    # Every step through labfs: a link planted anywhere on .heliopause/inbox/atlas/handoffs.jsonl is refused.
    creating = record["op"] == "handoff"
    try:
        if not labfs.check(root, ".heliopause/inbox", folder=True):
            return _no_inbox(root, ".heliopause/inbox is missing")
        info = labfs.lstat(root, _DIR)
    except OSError as err:
        return _no_inbox(root, err)
    if info is None:
        if not creating:
            return _err(404, "unknown id")
        try:
            labfs.makedirs(root, _DIR, 0o700)  # a new folder is exactly 0700
        except OSError as err:
            return _no_inbox(root, err)
    elif not stat.S_ISDIR(info.st_mode):
        return _no_inbox(root, f"{_DIR} is not a folder")
    try:
        there = labfs.lstat(root, _LOG)
    except OSError as err:
        return _no_inbox(root, err)
    if there is None and not creating:
        return _err(404, "unknown id")
    if there is not None and not stat.S_ISREG(there.st_mode):  # a FIFO or socket would swallow the record
        return _no_inbox(root, f"{_LOG} is not a regular file")
    try:
        fd = labfs.open_append(root, _LOG, create=creating, mode=0o600, read=True, fix_mode=True)
    except OSError as err:
        return _no_inbox(root, err)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            blocked = guard(fold(_records(fd))) if guard else None
            if blocked:
                return blocked
            size = os.fstat(fd).st_size
            torn = size > 0 and os.pread(fd, 1, size - 1) != b"\n"  # a torn last write must not swallow this record
            blob = (("\n" if torn else "") + json.dumps(record, ensure_ascii=False) + "\n").encode()
            if not _write_all(fd, blob):
                return _err(500, "write failed")
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        return _err(500, "write failed")
    finally:
        os.close(fd)
    return 201, {"ok": True}

def _guard(ident: str, want: tuple):
    def guard(folded):
        row = folded.get(ident)
        if row is None:
            return _err(404, "unknown id")
        return None if row["state"] in want else _err(409, "state")
    return guard

def _headline(node: str) -> str:
    label = "Owner" if node == "owner" else _LABELS.get(node, node)  # the owner is a role on the board, never a name
    text = f"Handed off to {label}"
    return text if len(text) <= 60 and _is_headline(text) else f"Handed off to {node}"

def accept_op(root: Path, token: str, headers, rfile, op: str) -> tuple[int, dict]:
    data = read_body(token, headers, rfile)
    if isinstance(data, tuple):
        return data
    fields = _clean(op, data)
    if isinstance(fields, tuple):
        return fields
    try:
        node = actor_node(root)
    except OSError:
        return _busy()
    if node is None:
        return _err(403, "unknown actor")
    if op == "handoff" and fields["to"] == node:
        return _err(400, "bad to")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if op == "handoff":
        ident = "h-" + secrets.token_hex(4)
        record = {"ts": stamp, "op": op, "id": ident, "by": node, "to": fields["to"], "title": fields["title"]}
        if fields["details"] is not None:
            record["details"] = fields["details"]
        guard = lambda folded: _err(409, "id clash") if ident in folded else None  # 32-bit ids: refuse, never drop
    else:
        ident = fields["id"]
        record = {"ts": stamp, "op": op, "id": ident, "by": node}
        guard = _guard(ident, ("open",) if op == "claim" else ("open", "claimed"))
    code, payload = _commit(root, record, guard)
    if code != 201:
        return code, payload
    if op != "handoff":
        return 200, {"ok": True, "id": ident, "state": "claimed" if op == "claim" else "done"}
    # WHY: the log is the commit. A missing board still leaves the handoff, with line null.
    code, payload = _append(root, _NAMES[node], "working", _headline(fields["to"]), f"{fields['title']} ({ident})")
    return 201, {"ok": True, "id": ident, "line": payload.get("line") if code == 201 else None}

def inbox_for(root: Path, me: str) -> tuple[int, dict]:
    if me not in NODE_IDS:
        return _err(400, "unknown node")
    rows = [
        {key: redact(row[key]) if key in ("title", "details") and row[key] else row[key] for key in _VIEW}
        for row in fold(load(root)).values()
        if row["to"] == me and row["state"] in ("open", "claimed")
    ]
    rows.sort(key=lambda row: row["ts"])
    return 200, {"me": me, "open": rows}

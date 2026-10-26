"""Loop 5 capture: the owner marks an item that waited on him Useful or Not useful. Heliopause only records it."""

from __future__ import annotations

import re
import time
from pathlib import Path

from atlas.alerts import _append, _locked, _read, _ui_gate
from atlas.write import _err

# ponytail: rotation is alerts.py's (1 MB, one backup), so clicks older than ~2 MB (about 20k rows) are dropped. Archive before that if it matters.
_FILE = ".atlas/feedback.jsonl"  # relative to root, written through labfs (alerts._append)
_KEYS = ("kind", "subject", "verdict")
_KIND = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")
_SUBJECT = re.compile(r"[A-Za-z0-9._:#/-]{1,120}")
_VERDICTS = ("useful", "not-useful")
_LATEST = 20

def _fields(data: dict) -> tuple[int, dict] | dict:
    # WHY: ids and kinds only. A free-text key would let a headline, alert text or file content into the file.
    if set(data) - set(_KEYS):
        return _err(400, "extra key")
    for key, pattern in (("kind", _KIND), ("subject", _SUBJECT)):
        value = data.get(key)
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            return _err(400, "bad " + key)
    verdict = data.get("verdict")
    if not isinstance(verdict, str) or verdict not in _VERDICTS:
        return _err(400, "bad verdict")
    return {key: data[key] for key in _KEYS}

def record_feedback(root: Path, nonce: str, headers, rfile, port: int) -> tuple[int, dict]:
    data = _ui_gate(nonce, headers, rfile, port)
    if isinstance(data, tuple):
        return data
    fields = _fields(data)
    if isinstance(fields, tuple):
        return fields
    record = {"ts": int(time.time()), **fields}

    def store(base: Path) -> tuple[int, dict]:
        return (201, {"ok": True}) if _append(base, _FILE, record) else _err(500, "write failed")

    return _locked(root, store)

def _view(row: dict) -> dict | None:
    ts = row.get("ts")
    fields = _fields({key: row.get(key) for key in _KEYS})
    if isinstance(ts, bool) or not isinstance(ts, int) or isinstance(fields, tuple):
        return None
    return {"ts": ts, **fields}

def feedback_summary(root: Path) -> dict:
    """Per kind, how many subjects ended on each verdict, and the last 20 rows. A later click on a subject replaces its earlier one."""
    # ponytail: reads both files whole on every call. Cache by mtime if anything ever polls this.
    latest: dict = {}
    for raw in _read(root, _FILE + ".1") + _read(root, _FILE):
        row = _view(raw)
        if row is not None:
            key = (row["kind"], row["subject"])
            latest.pop(key, None)  # the replacement moves to the end, so the tail is the newest verdicts
            latest[key] = row
    kinds: dict = {}
    for row in latest.values():
        kinds.setdefault(row["kind"], {"useful": 0, "not-useful": 0})[row["verdict"]] += 1
    return {"kinds": kinds, "latest": list(latest.values())[-_LATEST:]}

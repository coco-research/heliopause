"""Owner ring. One macOS notification per needs-owner alert, capped per local day."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from atlas import labfs

MAX_PER_DAY = 6
# ponytail: keep only the last 500 ids; older ids stop deduping and may ring again.
KEEP_IDS = 500


def _day() -> str:
    return datetime.now().strftime("%Y-%m-%d")


_RINGS = ".atlas/rings.json"


def _load(root: Path) -> dict:
    try:
        data = json.loads(labfs.read(root, _RINGS) or b"")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(root: Path, state: dict) -> None:
    # labfs: temp file then rename, 0600, no link on the way or at rings.json followed.
    labfs.write(root, _RINGS, json.dumps(state, ensure_ascii=False).encode(), 0o600)


def _escape(value: str) -> str:
    # WHY: backslash first, or the escapes added for quotes would be doubled.
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _osascript(title: str, text: str) -> None:
    script = f'display notification "{_escape(text)}" with title "{_escape(title)}"'
    # WHY: fire and forget. Waiting here would hold the POST past the sender's 3 s timeout.
    proc = subprocess.Popen(["osascript", "-e", script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=proc.wait, daemon=True).start()  # reap it, so no zombie stays behind


def ring(root: Path, alert: dict, notify=None) -> bool:
    """Ring once for a needs-owner alert. Returns True if it rang."""
    if alert.get("severity") != "needs-owner":
        return False
    ident = alert.get("id")
    if not isinstance(ident, str) or not ident:
        return False
    if notify is None:
        if sys.platform != "darwin":
            return False
        notify = _osascript
    try:
        lock = labfs.open_dir(root, ".atlas", create=True, mode=0o700)
    except OSError:
        return False
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = _load(root)
            today = _day()
            ids = state.get("ids") if isinstance(state.get("ids"), list) else []
            count = state.get("count") if state.get("day") == today else 0
            if not isinstance(count, int) or ident in ids or count >= MAX_PER_DAY:
                return False
            # WHY: reserve first, so a save failure never rings twice; a failed notification gives the slot back.
            _save(root, {"day": today, "count": count + 1, "ids": (ids + [ident])[-KEEP_IDS:]})
            try:
                notify("Heliopause: " + str(alert.get("source")), str(alert.get("text")))
            except OSError:
                _save(root, {"day": today, "count": count, "ids": ids})
                return False
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    except OSError:
        return False
    finally:
        os.close(lock)
    return True

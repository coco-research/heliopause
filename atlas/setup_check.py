"""Queue probe for setup. ``atlas doctor`` never touches the factory inbox, so this does.

One write, read-back, and delete under ``.heliopause/inbox/factory/``, the same relative
path ``roles._queue`` uses. No network. Nothing outside that inbox.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from atlas import labfs
from atlas.roles import _queue

_MEMBER = "Builder"
_PAYLOAD = b"ok\n"


# ponytail: a GET that writes one random probe file in our own inbox and deletes it at once; the server
# rejects foreign Host headers, so only this machine can trigger it. Make it a nonce POST if that ever matters.
def queue_check(lab: Path) -> dict:
    """Return ``{"ok", "ms", "member"}``. ``ok`` is false when the probe cannot be written and removed."""
    start = time.perf_counter()
    ok = _probe(lab)
    ms = int((time.perf_counter() - start) * 1000)
    if ms < 0:
        ms = 0
    return {"ok": ok, "ms": ms, "member": _MEMBER}


def _probe(lab: Path) -> bool:
    rel = _queue("inbox", "factory", "setup-check-" + secrets.token_hex(8))
    try:
        labfs.write(lab, rel, _PAYLOAD, 0o600)
        ok = labfs.read(lab, rel) == _PAYLOAD
    except OSError:
        ok = False
    try:
        labfs.unlink(lab, rel)
    except OSError:
        return False
    try:
        if labfs.lstat(lab, rel) is not None:
            return False
    except OSError:
        return False
    return ok

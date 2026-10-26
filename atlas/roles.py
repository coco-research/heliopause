"""Heliopause messages between the hq, lab, and factory roles.

One JSON file per message at ``<root>/.heliopause/inbox/<role>/<id>.json``.
``ack`` moves it to ``<root>/.heliopause/done/<role>/<id>.json``.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from atlas import labfs

_ROLES = frozenset(("hq", "lab", "factory"))
_KINDS = frozenset(("plan", "job", "report", "ask", "answer"))
_STATES = frozenset(("sent", "picked_up", "done", "failed"))
_CHECK_ORDER = ("tests", "review", "ci")
_CHECK_NAMES = frozenset(_CHECK_ORDER)
_CHECK_VALUES = frozenset(("pending", "pass", "fail", "none"))
_REQUIRED = ("id", "from", "to", "kind", "summary", "repo", "state", "reply_to", "ts")
_FIELDS = frozenset(_REQUIRED) | {"checks"}
_ID = re.compile(r"\Am-[0-9a-f]{12}\Z")
_TS = re.compile(r"\A(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z\Z")
_SUMMARY_MAX = 200


def _is_id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _choice(value: object, allowed: frozenset[str]) -> bool:
    # Unhashable JSON (dict, list) must miss the set, not raise.
    return isinstance(value, str) and value in allowed


def _unknown(value: dict, allowed: frozenset[str]) -> list[object]:
    extra = [name for name in value if name not in allowed]
    extra.sort(key=lambda item: (not isinstance(item, str), str(item)))
    return extra


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    match = _TS.fullmatch(value)
    if match is None:
        return None
    body = match.group(1)
    frac = match.group(2)
    if frac is not None:
        body = f"{body}.{(frac + '000000')[:6]}"
    try:
        parsed = datetime.fromisoformat(body)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _format_ts(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    else:
        when = when.astimezone(timezone.utc)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime")
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _validate_from(value: object, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("from must be an object")
        return
    for name in _unknown(value, frozenset(("role", "tool"))):
        errors.append(f"from has unknown field: {name}")
    if "role" not in value:
        errors.append("from missing field: role")
    elif not _choice(value["role"], _ROLES):
        errors.append("from.role must be hq, lab, or factory")
    if "tool" not in value:
        errors.append("from missing field: tool")
    elif not isinstance(value["tool"], str):
        errors.append("from.tool must be a string")


def _validate_to(value: object, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("to must be an object")
        return
    for name in _unknown(value, frozenset(("role",))):
        errors.append(f"to has unknown field: {name}")
    if "role" not in value:
        errors.append("to missing field: role")
    elif not _choice(value["role"], _ROLES):
        errors.append("to.role must be hq, lab, or factory")


def _validate_checks(value: object, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("checks must be an object")
        return
    for name in _unknown(value, _CHECK_NAMES):
        errors.append(f"checks has unknown field: {name}")
    for name in _CHECK_ORDER:
        if name not in value:
            errors.append(f"checks missing field: {name}")
    for name in _CHECK_ORDER:
        if name in value and not _choice(value[name], _CHECK_VALUES):
            errors.append(f"checks.{name} must be pending, pass, fail, or none")


def validate(msg: object) -> list[str]:
    """Return every problem with ``msg``. An empty list means it is valid."""
    if not isinstance(msg, dict):
        return ["message must be an object"]
    errors: list[str] = []
    for name in _unknown(msg, _FIELDS):
        errors.append(f"unknown field: {name}")
    for name in _REQUIRED:
        if name not in msg:
            errors.append(f"missing field: {name}")
    if "id" in msg and not _is_id(msg["id"]):
        errors.append("id must be m- and 12 lowercase hex digits")
    if "from" in msg:
        _validate_from(msg["from"], errors)
    if "to" in msg:
        _validate_to(msg["to"], errors)
    if "kind" in msg and not _choice(msg["kind"], _KINDS):
        errors.append("kind must be plan, job, report, ask, or answer")
    if "summary" in msg:
        summary = msg["summary"]
        if not isinstance(summary, str):
            errors.append("summary must be a string")
        else:
            if "\n" in summary or "\r" in summary:
                errors.append("summary must be one line")
            if len(summary) > _SUMMARY_MAX:
                errors.append("summary must be at most 200 characters")
    if "repo" in msg and msg["repo"] is not None and not isinstance(msg["repo"], str):
        errors.append("repo must be a string or null")
    if "state" in msg and not _choice(msg["state"], _STATES):
        errors.append("state must be sent, picked_up, done, or failed")
    if "reply_to" in msg:
        reply = msg["reply_to"]
        if reply is not None and not _is_id(reply):
            errors.append("reply_to must be an id or null")
        if reply is not None and reply == msg.get("id"):
            errors.append("reply_to must not point at itself")
    if "ts" in msg and _parse_ts(msg["ts"]) is None:
        errors.append("ts must be ISO-8601 UTC ending in Z")
    kind = msg.get("kind")
    if kind == "job" and "checks" not in msg:
        errors.append("checks is required when kind is job")
    elif "checks" in msg and kind != "job":
        errors.append("checks must be absent unless kind is job")
    elif kind == "job" and "checks" in msg:
        _validate_checks(msg["checks"], errors)
    return errors


def _refuse(errors: list[str]) -> None:
    raise ValueError("; ".join(errors))


def new_message(
    frm_role: str,
    frm_tool: str,
    to_role: str,
    kind: str,
    summary: str,
    repo: str | None = None,
    reply_to: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Build a valid message: fresh id, state ``sent``, timestamp ``now``.

    Jobs start with tests, review, and ci all set to ``none``.
    ``now`` defaults to the current time.
    """
    msg: dict = {
        "id": "m-" + secrets.token_hex(6),
        "from": {"role": frm_role, "tool": frm_tool},
        "to": {"role": to_role},
        "kind": kind,
        "summary": summary,
        "repo": repo,
        "state": "sent",
        "reply_to": reply_to,
        "ts": _format_ts(_as_utc(now)),
    }
    if kind == "job":
        msg["checks"] = {name: "none" for name in _CHECK_ORDER}
    errors = validate(msg)
    if errors:
        _refuse(errors)
    return msg


def _inbox(root: Path | str, role: str) -> Path:
    return Path(root) / ".heliopause" / "inbox" / role


def _done(root: Path | str, role: str) -> Path:
    return Path(root) / ".heliopause" / "done" / role


def _queue(area: str, role: str, name: str) -> str:
    return f".heliopause/{area}/{role}/{name}"


@contextmanager
def _role_lock(root: Path | str, role: str):
    # ponytail: one lock per role queue, finer locks if throughput ever matters
    fd = labfs.open_append(root, _queue("inbox", role, ".lock"), create=True, mode=0o600, make_dirs=True)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _dump(msg: dict) -> bytes:
    return (json.dumps(msg, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _read_json(path: Path) -> tuple[dict | None, bool]:
    """Return ``(object, bad)``. Bad means unreadable or not a JSON object."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None, True
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, True
    if not isinstance(parsed, dict):
        return None, True
    return parsed, False


def send(root: Path | str, msg: dict) -> None:
    """Validate ``msg`` and write it for its ``to.role``.

    An id already in the inbox or in done is a no-op.
    """
    errors = validate(msg)
    if errors:
        _refuse(errors)
    role = msg["to"]["role"]
    with _role_lock(root, role):
        name = f"{msg['id']}.json"
        if (_inbox(root, role) / name).exists() or (_done(root, role) / name).exists():
            return
        labfs.write(root, _queue("inbox", role, name), _dump(msg), 0o600)


def _order_key(msg: dict) -> tuple[int, float]:
    parsed = _parse_ts(msg.get("ts"))
    if parsed is None:
        return (1, 0.0)
    return (0, parsed.timestamp())


def _leased(marker: Path, when: datetime, ttl_s: float) -> bool:
    if not marker.is_file():
        return False
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return False
    taken = _parse_ts(text)
    if taken is None:
        return False
    # Younger than ttl_s stays leased. ttl_s or older is offered again.
    return (when - taken).total_seconds() < ttl_s


def take(root: Path | str, role: str, now: datetime | None = None, ttl_s: float = 90) -> list[dict]:
    """Return ``role``'s inbox messages, oldest first, and lease each one.

    Corrupt JSON is skipped. A lease younger than ``ttl_s`` hides the message;
    an older lease is offered again.
    """
    if role not in _ROLES:
        return []
    folder = _inbox(root, role)
    if not folder.is_dir():
        return []
    with _role_lock(root, role):
        when = _as_utc(now)
        ready: list[tuple[tuple[int, float], str, Path, dict]] = []
        for path in folder.iterdir():
            if not path.is_file() or path.suffix != ".json":
                continue
            msg, bad = _read_json(path)
            if bad or msg is None:
                continue
            if _leased(path.with_name(path.stem + ".inflight"), when, ttl_s):
                continue
            ready.append((_order_key(msg), path.name, path, msg))
        ready.sort()
        payload = (_format_ts(when) + "\n").encode("ascii")
        out: list[dict] = []
        for _, _, path, msg in ready:
            labfs.write(root, _queue("inbox", role, path.stem + ".inflight"), payload, 0o600)
            out.append(msg)
        return out


def ack(root: Path | str, role: str, id: str) -> None:
    """Move ``id`` from the inbox to done and drop its lease.

    An unknown id does nothing. Ack moves the message regardless of who holds
    the lease, including after it expired and another consumer took it.
    """
    if role not in _ROLES or not _is_id(id):
        return
    folder = _inbox(root, role)
    if not folder.is_dir():
        return
    with _role_lock(root, role):
        src = folder / f"{id}.json"
        if not src.is_file():
            return
        labfs.makedirs(root, f".heliopause/done/{role}")
        labfs.rename(root, _queue("inbox", role, f"{id}.json"), _queue("done", role, f"{id}.json"), replace=True)
        labfs.unlink(root, _queue("inbox", role, f"{id}.inflight"))


def _load(root: Path | str, role: str, msg_id: str) -> tuple[Path, dict]:
    if role not in _ROLES or not _is_id(msg_id):
        raise ValueError(f"unknown id: {msg_id}")
    path = _inbox(root, role) / f"{msg_id}.json"
    if not path.is_file():
        raise ValueError(f"unknown id: {msg_id}")
    msg, bad = _read_json(path)
    if bad or msg is None:
        raise ValueError(f"unknown id: {msg_id}")
    return path, msg


def _rewrite(root: Path | str, role: str, msg_id: str, msg: dict) -> None:
    errors = validate(msg)
    if errors:
        _refuse(errors)
    labfs.write(root, _queue("inbox", role, f"{msg_id}.json"), _dump(msg), 0o600)


def set_state(root: Path | str, role: str, id: str, state: str) -> None:
    """Set ``state`` on an inbox message and write it back if it still validates."""
    if role not in _ROLES or not _is_id(id):
        raise ValueError(f"unknown id: {id}")
    with _role_lock(root, role):
        _, msg = _load(root, role, id)
        updated = dict(msg)
        updated["state"] = state
        _rewrite(root, role, id, updated)


def set_check(root: Path | str, role: str, id: str, name: str, value: str) -> None:
    """Set one job check and write the message back if it still validates."""
    if role not in _ROLES or not _is_id(id):
        raise ValueError(f"unknown id: {id}")
    with _role_lock(root, role):
        _, msg = _load(root, role, id)
        updated = dict(msg)
        current = updated.get("checks")
        checks = dict(current) if isinstance(current, dict) else {}
        checks[name] = value
        updated["checks"] = checks
        _rewrite(root, role, id, updated)


def bad_files(root: Path | str, role: str) -> list[Path]:
    """Inbox files for ``role`` that are unreadable or not a JSON object."""
    if role not in _ROLES:
        return []
    folder = _inbox(root, role)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        if path.is_file() and path.suffix == ".json" and _read_json(path)[1]:
            found.append(path)
    found.sort(key=lambda item: item.name)
    return found

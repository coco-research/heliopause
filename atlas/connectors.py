"""One card per coding tool. A hook keeps five fields and never breaks the tool that called it."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from atlas import labfs

TOOLS = ("claude-code", "codex", "cursor", "grok")

_CAP = 1 << 20
_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# (tool, event) → card state. Anything else, including Notification, is not a card.
_CC = {
    "SessionStart": "idle",
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PermissionRequest": "needs_you",
    "Stop": "idle",
    "SessionEnd": "idle",
}
_CURSOR = {
    "sessionStart": "idle",
    "beforeSubmitPrompt": "working",
    "preToolUse": "working",
    "postToolUse": "working",
    "beforeShellExecution": "working",
    "afterFileEdit": "working",
    "stop": "idle",
    "sessionEnd": "idle",
}
_GROK = {name: state for name, state in _CC.items() if name != "PermissionRequest"}
STATE = {
    **{("claude-code", name): state for name, state in _CC.items()},
    **{("codex", name): state for name, state in _CC.items()},
    **{("cursor", name): state for name, state in _CURSOR.items()},
    **{("grok", name): state for name, state in _GROK.items()},
}


def _ident(value: object) -> str | None:
    if isinstance(value, str) and value not in (".", "..") and _ID.fullmatch(value):
        return value
    return None


def _repo(folder: object) -> str | None:
    if not isinstance(folder, str) or not folder:
        return None
    return _ident(os.path.basename(folder.rstrip("/")))


def _or(payload: dict, key: str, fallback: str):
    """key when it is present and non-empty, otherwise fallback."""
    if key in payload and payload[key] not in (None, ""):
        return payload[key]
    return payload.get(fallback)


def strip(tool: str, payload: object) -> dict | None:
    """{"tool","event","session","repo"}, or None. No other key is copied."""
    if tool not in TOOLS or not isinstance(payload, dict):
        return None
    event = payload.get("hookEventName" if tool == "grok" else "hook_event_name")
    if not isinstance(event, str) or (tool, event) not in STATE:
        return None
    if tool == "cursor":
        session = _or(payload, "conversation_id", "session_id")
        roots = payload.get("workspace_roots")
        folder = roots[0] if isinstance(roots, list) and roots else payload.get("cwd")
    elif tool == "grok":
        session = payload.get("sessionId")
        folder = _or(payload, "workspaceRoot", "cwd")
    else:
        session = payload.get("session_id")
        folder = payload.get("cwd")
    session_id = _ident(session)
    repo = _repo(folder)
    if session_id is None or repo is None:
        return None
    return {"tool": tool, "event": event, "session": session_id, "repo": repo}


def _stamp(now: datetime | None) -> str:
    when = datetime.now(timezone.utc) if now is None else now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    else:
        when = when.astimezone(timezone.utc)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def report(lab: Path, tool: str, raw: bytes, now: datetime | None = None) -> bool:
    """Write one card. False writes nothing. Refused and OSError stay inside."""
    if tool not in TOOLS or not isinstance(raw, (bytes, bytearray)) or len(raw) > _CAP:
        return False
    try:
        payload = json.loads(raw)
    except Exception:
        return False
    kept = strip(tool, payload)
    if kept is None:
        return False
    state = STATE.get((tool, kept["event"]))
    if state is None:
        return False
    card = {**kept, "state": state, "ts": _stamp(now)}
    body = (json.dumps(card, sort_keys=True) + "\n").encode("utf-8")
    try:
        labfs.write(Path(lab), f".heliopause/cards/{tool}/{kept['session']}.json", body, 0o600)
    except (labfs.Refused, OSError):
        return False
    return True


class _Quiet(argparse.ArgumentParser):
    """A hook must not see usage text. Bad args are the same as a ignored event."""

    def error(self, message: str) -> None:
        raise SystemExit(0)


def run(argv: list[str] | None = None) -> int:
    """`report --tool T --lab DIR` reads stdin and always exits 0 with no output."""
    parser = _Quiet(prog="atlas connector", add_help=False)
    parser.add_argument("verb")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--lab", required=True)
    try:
        args = parser.parse_args(argv)
        if args.verb == "report":
            report(Path(args.lab), args.tool, sys.stdin.buffer.read(_CAP + 1))
    except (SystemExit, Exception):
        return 0
    return 0

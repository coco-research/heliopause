"""Record one job result. The only way a job's state and its tests check change."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from atlas import roles

_ROLES = ("hq", "lab", "factory")
_OUTCOMES = frozenset(("done", "failed"))
_TEST_RESULTS = frozenset(("pass", "fail"))
_BODY = frozenset(("id", "outcome", "tests", "tool"))
_TOOL = re.compile(r"\A[a-z0-9-]{1,32}\Z")


def _error(code: int, message: str) -> tuple[int, dict]:
    return code, {"error": message}


def job_prompt(msg: dict) -> str:
    """Three lines for the page's Copy job button. Refuses anything but a valid job."""
    if roles.validate(msg) or msg.get("kind") != "job":
        raise ValueError("not a job")
    repo = msg["repo"] or "none"
    command = f"cd ~/Heliopause && python3 -B -m atlas report {msg['id']} done|failed --tests pass|fail --lab ~/atlas-lab"
    return f"Job {msg['id']}: {msg['summary']}\nRepo: {repo}\nWhen done, run: {command}"


def _find(root: Path | str, msg_id: str) -> tuple[str, dict] | None:
    for role in _ROLES:
        path = roles._inbox(root, role) / f"{msg_id}.json"
        if not path.is_file():
            continue
        msg, bad = roles._read_json(path)
        if not bad and msg is not None:
            return role, msg
    return None


def _prepared(role: str, msg: dict, msg_id: str, outcome: str, tests: str | None, tool: str, now):
    """Validate the updated job and build the reply. No writes."""
    updated = dict(msg)
    if tests is not None:
        current = updated.get("checks")
        checks = dict(current) if isinstance(current, dict) else {}
        checks["tests"] = tests
        updated["checks"] = checks
    updated["state"] = outcome
    errors = roles.validate(updated)
    if errors:
        raise ValueError("; ".join(errors))
    summary = f"{msg_id} {outcome}" + (f", tests {tests}" if tests else "")
    return roles.new_message(
        frm_role=role,
        frm_tool=tool,
        to_role=msg["from"]["role"],
        kind="report",
        summary=summary,
        repo=msg.get("repo"),
        reply_to=msg_id,
        now=now,
    )


def report(root, id, outcome, tests=None, tool="you", now=None) -> tuple[int, dict]:
    """Set the job's result and send one report back to whoever sent the job."""
    if not roles._is_id(id):
        return _error(400, "id must be m- and 12 lowercase hex digits")
    # isinstance first: an unhashable JSON value ([] or {}) would raise in the set test.
    if not isinstance(outcome, str) or outcome not in _OUTCOMES:
        return _error(400, "outcome must be done or failed")
    if tests is not None and (not isinstance(tests, str) or tests not in _TEST_RESULTS):
        return _error(400, "tests must be pass or fail")
    found = _find(root, id)
    if found is None:
        return _error(404, "No job with that id.")
    role, msg = found
    if msg.get("kind") != "job":
        return _error(409, "That message is not a job.")
    if msg.get("state") in _OUTCOMES:
        return _error(409, "This job already has a result.")
    # Checks and the reply are built before the first write, so a refusal leaves the lab untouched.
    try:
        reply = _prepared(role, msg, id, outcome, tests, tool, now)
    except (ValueError, OSError, KeyError, TypeError):
        return _error(500, "write failed")
    try:
        # ponytail: two role writes, then the reply. A crash between them can leave the check
        # without the new state; fold into one roles call if that shows up.
        if tests is not None:
            roles.set_check(root, role, id, "tests", tests)
        roles.set_state(root, role, id, outcome)
        roles.send(root, reply)
    except (ValueError, OSError):
        return _error(500, "write failed")
    return 200, {"id": id, "state": outcome, "report": reply["id"]}


def accept_report(root, token: str, headers, rfile) -> tuple[int, dict]:
    """POST /api/report. Token-guarded body, same gate as /api/post."""
    from atlas.write import read_body

    data = read_body(token, headers, rfile)
    if isinstance(data, tuple):
        return data
    if set(data) - _BODY:
        return _error(400, "unknown field")
    tool = data.get("tool", "you")
    if not isinstance(tool, str) or _TOOL.fullmatch(tool) is None:
        return _error(400, "bad tool")
    return report(root, data.get("id"), data.get("outcome"), tests=data.get("tests"), tool=tool)


def _lab_dir(explicit: str | None) -> Path | None:
    raw = explicit or os.environ.get("ATLAS_LAB") or ""
    if not raw.strip():
        return None
    return Path(raw).expanduser()


def run(argv: list[str] | None = None) -> int:
    """`python3 -m atlas report <id> done|failed [--tests pass|fail] [--tool NAME] [--lab DIR]`."""
    parser = argparse.ArgumentParser(prog="atlas report")
    parser.add_argument("id")
    parser.add_argument("outcome")
    parser.add_argument("--tests", default=None)
    parser.add_argument("--tool", default="you")
    parser.add_argument("--lab", default=None)
    args = parser.parse_args(argv)
    lab = _lab_dir(args.lab)
    if lab is None:
        print("atlas report: no lab (use --lab)", file=sys.stderr)
        return 2
    code, payload = report(lab, args.id, args.outcome, tests=args.tests, tool=args.tool)
    if code != 200:
        print(payload["error"], file=sys.stderr)
        return 1
    print(f"{payload['id']} {payload['state']}")
    return 0

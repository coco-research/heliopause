"""Pick a factory lane and run it.

`python3 -m atlas.dispatch <type> [--story TEXT] [--cwd DIR] [--root DIR] -- <task>`

Reads Budget's routes.json, checks gates, runs the first open lane through coco-route
or ocr-stamp (only those two, from ~/.local/bin or $ATLAS_DISPATCH_BIN_DIR), and appends one line to
routes.jsonl. A thin wrapper, not a service.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from atlas import collect, labfs

_NO_LANE = "needs Claude Code: a brain decides whether a Sonnet subagent is warranted"
_REFUSED = frozenset({"shell", "lookup"})
_BINS = frozenset({"coco-route", "ocr-stamp"})
_FALLBACK = frozenset({"capped", "timeout", "worker_failed"})
_DAYS = re.compile(r"(\d+(?:\.\d+)?)")


def _parse(argv: list[str] | None) -> tuple[argparse.Namespace, str]:
    if argv is None:
        argv = sys.argv[1:]
    # Split on the first "--" ourselves. argparse REMAINDER swallows earlier options.
    if "--" in argv:
        cut = argv.index("--")
        head, task_parts = argv[:cut], argv[cut + 1 :]
    else:
        head, task_parts = argv, []
    parser = argparse.ArgumentParser(
        prog="atlas.dispatch",
        description="Pick a factory lane from routes.json and run it.",
    )
    parser.add_argument("type")
    parser.add_argument("--story")
    parser.add_argument("--cwd")
    parser.add_argument("--root", default=str(Path.home()))
    args = parser.parse_args(head)
    task = " ".join(task_parts)
    # stamp takes no task. Every other type must have one, or a lane starts empty.
    if args.type != "stamp" and not task.strip():
        parser.error("missing task after --")
    return args, task


def _load(path: Path) -> tuple[dict | None, str | None]:
    """Parse a JSON object. A missing or unreadable file passes the gate that needs it."""
    raw = collect._read(path)
    if raw is None:
        return None, f"gate unread: {path.name}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, f"gate unread: {path.name}"
    if not isinstance(data, dict):
        return None, f"gate unread: {path.name}"
    return data, None


_ROUTES = ".heliopause/inbox/lab/usage/routes.json"


def _unsafe(st: os.stat_result, uid: int, folder: bool) -> bool:
    """Someone else's (root excepted), or writable by group or others. A root-owned sticky folder like /tmp is fine."""
    if st.st_uid not in (uid, 0):
        return True
    if st.st_mode & 0o022:
        return not (folder and st.st_uid == 0 and st.st_mode & stat.S_ISVTX)
    return False


def _trusted_routes(root: Path) -> str | None:
    """Why routes.json under root must not be used, or None. Its lanes are commands this runs.

    Refused when root is, or is inside, a lab, when root/.heliopause is, is inside or contains a lab (agents
    write labs), when a link on the way from root to routes.json resolves into a lab, when routes.json
    resolves inside a lab, or when it or any folder on its way (as written or resolved) is not yours or is
    writable by other users.
    """
    lab = labfs.lab_around(root)
    if lab is not None:
        return f"--root {root} is inside the lab {lab}; agents can write there"
    # ponytail: "contains a lab" is checked on root/.heliopause, not on root, because the default root is HOME
    # and HOME always contains ~/atlas-lab. Ceiling: a lab elsewhere under root is not refused by itself; a
    # link from the routes path into it is refused by the walk below. Upgrade: require --root to be a project
    # folder, not HOME, then check root itself.
    lab = labfs.lab_near(root / ".heliopause")
    if lab is not None:
        return f"{root / '.heliopause'} overlaps the lab {lab}; agents can write there"
    uid = os.getuid()
    # Every component as written, root -> routes.json: a link must not lead into a lab, nothing may be another
    # user's or writable by others (a link is judged by its owner and by what it resolves to).
    cur = Path(root)
    for part in Path(_ROUTES).parts:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return None  # no routes.json: nothing runs
        except OSError as err:
            return f"{cur} cannot be checked ({err.strerror})"
        if stat.S_ISLNK(st.st_mode):
            if st.st_uid not in (uid, 0):
                return f"{cur} is a link that is not yours"
            lab = labfs.lab_near(os.path.realpath(cur))
            if lab is not None:
                return f"{cur} links into the lab {lab}; agents can write there"
            try:
                st = os.stat(cur)
            except OSError:
                return None  # a dangling link: nothing to read
        if _unsafe(st, uid, stat.S_ISDIR(st.st_mode)):
            return f"{cur} is not yours or is writable by other users"
    real = os.path.realpath(root / _ROUTES)
    lab = labfs.lab_around(real)
    if lab is not None:
        return f"{root / _ROUTES} resolves into the lab {lab}; agents can write there"
    # And the resolved path, up to root, HOME or /.
    stops = {os.path.realpath(root), os.path.realpath(Path.home()), os.sep}
    cur = real
    while True:
        try:
            st = os.stat(cur)
        except OSError:
            st = None
        if st is not None and _unsafe(st, uid, cur != real):
            return f"{cur} is not yours or is writable by other users"
        if cur in stops:
            return None
        cur = os.path.dirname(cur)


def _bin_dir() -> str:
    """The one folder lane tools run from: $ATLAS_DISPATCH_BIN_DIR, else ~/.local/bin. Never PATH, never a lab."""
    return os.path.expanduser(os.environ.get("ATLAS_DISPATCH_BIN_DIR") or str(Path.home() / ".local" / "bin"))


def _tool(name: str) -> tuple[str | None, str]:
    """(real path, "") of an allowlisted lane tool in the trusted folder, or (None, why it may not run)."""
    if name not in _BINS:
        return None, f"{name!r} is not a lane tool (only {', '.join(sorted(_BINS))})"
    folder = _bin_dir()
    if not os.path.isabs(folder):
        return None, f"tool folder {folder} is not an absolute path"
    real = os.path.realpath(os.path.join(folder, name))
    lab = labfs.lab_near(os.path.realpath(folder)) or labfs.lab_around(real)
    if lab is not None:
        return None, f"{name} resolves into the lab {lab}; agents can write there"
    try:
        st = os.stat(real)
    except OSError:
        return None, f"{name} not found in {folder}"
    if not stat.S_ISREG(st.st_mode) or not st.st_mode & 0o111:
        return None, f"{real} is not an executable file"
    uid = os.getuid()
    cur = real
    while True:
        try:
            info = os.stat(cur)
        except OSError as err:
            return None, f"{cur} cannot be checked ({err.strerror})"
        if _unsafe(info, uid, cur != real):
            return None, f"{cur} is not yours or root's, or is writable by other users"
        parent = os.path.dirname(cur)
        if parent == cur:
            return real, ""
        cur = parent


def _table(root: Path) -> dict | None:
    why = _trusted_routes(root)
    if why is not None:
        print(f"atlas dispatch: not reading routes.json: {why}", file=sys.stderr)
        return None
    data, _note = _load(root / ".heliopause" / "inbox" / "lab" / "usage" / "routes.json")
    if not isinstance(data, dict) or data.get("v") != 1:
        return None
    return data


def _max_days(table: dict) -> float:
    value = table.get("glm_pace_max_days_ahead", 1.5)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.5
    return float(value)


def _argv_ok(run: object) -> bool:
    return isinstance(run, list) and bool(run) and all(isinstance(part, str) and part for part in run)


def _paused(entry: object) -> str:
    if isinstance(entry, dict):
        note = entry.get("note")
        if isinstance(note, str) and note.strip():
            return f"paused: {note.strip()}"
        state = entry.get("state")
        if isinstance(state, str) and state.strip():
            return f"paused: {state.strip()}"
    return "paused: missing"


def _number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _xai_open(data: dict) -> bool:
    if data.get("factoryStop") is not False:
        return False
    weekly, limit = data.get("weeklyPct"), data.get("factoryStopPct")
    return _number(weekly) and _number(limit) and weekly < limit


def _grok_gate(root: Path, usage: Path) -> tuple[str | None, list[str]]:
    notes = []
    if collect._read(usage / "grok-build.jsonl") is None:
        notes.append("gate unread: grok-build.jsonl")
    caps_raw = collect._read(usage / "caps.json")
    if caps_raw is None:
        notes.append("gate unread: caps.json")
    if notes:
        return None, notes
    try:
        json.loads(caps_raw)
    except json.JSONDecodeError:
        return None, ["gate unread: caps.json"]
    cap = collect._cap(root)
    # ponytail: no positive int means Budget published no cap. _grok sums today's total_tokens.
    if cap is None:
        return None, []
    tokens = collect._grok(root).get("tokens_today", 0)
    if not _number(tokens):
        tokens = 0
    if tokens >= cap:
        return "grok cap", []
    return None, []


def _pace(root: Path, name: str, max_days: float) -> tuple[str | None, str | None]:
    data, note = _load(root / ".heliopause" / "inbox" / "lab" / "strip" / "usage.json")
    if note:
        return None, note
    pools = data.get("pools") if isinstance(data, dict) else None
    if not isinstance(pools, list):
        return None, "gate unread: usage.json"
    pool = next((item for item in pools if isinstance(item, dict) and item.get("name") == name), None)
    if not isinstance(pool, dict):
        return None, None
    if pool.get("status") == "out":
        return "pace", None
    text = pool.get("pace")
    if isinstance(text, str) and text.startswith("ahead"):
        match = _DAYS.search(text)
        if match and float(match.group(1)) > max_days:
            return "pace", None
    return None, None


def _gates(root: Path, lane: dict, max_days: float) -> tuple[str | None, list[str]]:
    """Check gates in order. The first failure is the skip reason; a dead file passes."""
    notes: list[str] = []
    usage = root / ".heliopause" / "inbox" / "lab" / "usage"
    data, note = _load(usage / "lanes.json")
    if note:
        notes.append(note)
    else:
        lanes = data.get("lanes") if isinstance(data, dict) else None
        if not isinstance(lanes, dict):
            notes.append("gate unread: lanes.json")
        else:
            gate = lane.get("gate")
            entry = lanes.get(gate) if isinstance(gate, str) else None
            state = entry.get("state") if isinstance(entry, dict) else None
            if state != "live":
                return _paused(entry), notes
    if lane.get("xai") is True:
        data, note = _load(usage / "xultra.json")
        if note:
            notes.append(note)
        elif not _xai_open(data):
            return "xai stop", notes
    if lane.get("gate") == "grok-build":
        reason, extra = _grok_gate(root, usage)
        notes.extend(extra)
        if reason:
            return reason, notes
    pace_name = lane.get("pace")
    if isinstance(pace_name, str) and pace_name:
        reason, note = _pace(root, pace_name, max_days)
        if note:
            notes.append(note)
        if reason:
            return reason, notes
    return None, notes


def _command(run: list[str], task: str, tool: str) -> list[str]:
    # tool is the checked real path of run[0] (_tool). routes.json picks which allowlisted tool, never its path.
    cmd = [tool, *run[1:]]
    # ocr-stamp takes no task. Every other lane gets the task after "--".
    if run[0] != "ocr-stamp":
        cmd.extend(["--", task])
    return cmd


def _execute(run: list[str], task: str, cwd: Path, job_id: str, story: str | None, tool: str) -> int:
    env = os.environ.copy()
    env["COCO_JOB_ID"] = job_id
    if story:
        env["COCO_STORY"] = story
    else:
        env.pop("COCO_STORY", None)
    try:
        # stdout and stderr inherit. shell stays off so the task is one argument, not a script.
        return subprocess.run(_command(run, task, tool), cwd=str(cwd), env=env).returncode
    except OSError:
        return 127


def _class_of(root: Path, job_id: str, code: int) -> str:
    raw = collect._read(root / ".heliopause" / "inbox" / "lab" / "usage" / "jobs-end.jsonl")
    found = None
    if raw:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict) or item.get("job") != job_id:
                continue
            outcome = item.get("class")
            if isinstance(outcome, str) and outcome:
                found = outcome
    if found:
        return found
    # ocr-stamp writes no end line. The process status is the whole class.
    return "ok" if code == 0 else "worker_failed"


def _clean(cwd: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and not proc.stdout.strip()


def _again(mode: str, outcome: str, cwd: Path) -> bool:
    # ponytail: one extra lane, then stop. A ranked third try is the P3 scorecard's job.
    if outcome not in _FALLBACK:
        return False
    return mode == "read" or _clean(cwd)


def _mark(row: dict, notes: list[str]) -> dict:
    if notes:
        row["unread"] = notes
    return row


def _record(root: Path, kind: str, story: str | None, job: str, chosen: str | None, tried: list, used: bool) -> None:
    row = {
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "job": job,
        "type": kind,
        "story": story,
        "chosen": chosen,
        "tried": tried,
        "fallback_used": used,
    }
    # labfs: a link anywhere on this path (planted in the lab) is refused, never appended through.
    try:
        labfs.append(root, ".heliopause/inbox/lab/usage/routes.jsonl", (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"), 0o600)
    except OSError as err:
        print(f"atlas dispatch: route not recorded ({err})", file=sys.stderr)


def _reasons(tried: list[dict]) -> None:
    lines = [row["skip"] for row in tried if isinstance(row.get("skip"), str)]
    if lines:
        print("\n".join(lines), file=sys.stderr)


def _go(root: Path, cwd: Path, kind: str, spec: dict, table: dict, task: str, story: str | None) -> int:
    lanes = spec.get("lanes")
    if not isinstance(lanes, list):
        lanes = []
    mode = "read" if spec.get("mode") == "read" else "write"
    max_days = _max_days(table)
    tried: list[dict] = []
    chosen = None
    job = None
    code = 0
    ran = 0
    for lane in lanes:
        if not isinstance(lane, dict) or not _argv_ok(lane.get("run")):
            continue
        run = lane["run"]
        name = " ".join(run)
        tool, why = _tool(run[0])
        if tool is None:
            tried.append({"lane": name, "skip": f"refused: {why}"})
            continue
        reason, notes = _gates(root, lane, max_days)
        if reason:
            tried.append(_mark({"lane": name, "skip": reason}, notes))
            continue
        job_id = str(uuid.uuid4())
        code = _execute(run, task, cwd, job_id, story, tool)
        outcome = _class_of(root, job_id, code)
        tried.append(_mark({"lane": name, "class": outcome}, notes))
        chosen = name
        job = job_id
        ran += 1
        if ran >= 2 or not _again(mode, outcome, cwd):
            break
    if ran == 0:
        _record(root, kind, story, str(uuid.uuid4()), None, tried, False)
        _reasons(tried)
        return 3
    _record(root, kind, story, job, chosen, tried, ran > 1)
    return code


def main(argv: list[str] | None = None) -> int:
    if collect.LAB:
        return 0
    args, task = _parse(argv)
    kind = args.type
    if kind in _REFUSED:
        print(_NO_LANE, file=sys.stderr)
        return 2
    root = Path(args.root).expanduser()
    table = _table(root)
    types = table.get("types") if isinstance(table, dict) else None
    spec = types.get(kind) if isinstance(types, dict) else None
    if not isinstance(spec, dict):
        print(_NO_LANE, file=sys.stderr)
        return 2
    cwd = Path(args.cwd).expanduser() if args.cwd else Path.cwd()
    return _go(root, cwd, kind, spec, table, task, args.story or None)


if __name__ == "__main__":
    raise SystemExit(main())

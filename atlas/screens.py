"""Screen reads, plus the two page actions that approve an ask or mark a job.

Reads stay on labfs (no symlink out of the lab). Writes go through roles.send,
set_state, ack, or job_report.report.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas import github, job_report, labfs, roles, roles_config
from atlas.collect import configured_repos
from atlas.connectors import TOOLS

_ROLES = ("hq", "lab", "factory")
_QUERY = frozenset(("role", "tool", "repo", "state", "thread", "limit"))
_ROLE_OK = frozenset(_ROLES)
_STATE_OK = frozenset(("sent", "picked_up", "done", "failed", "waiting"))
_CARD_STATES = frozenset(("idle", "working", "needs_you"))
_OPEN = frozenset(("sent", "picked_up"))
# ponytail: fixed 10 min; a setting if users ask
_STALE_S = 600
_QUIET = frozenset(("cursor", "grok"))
_FACTORY = ("Builder", "Builder 2", "Builder 3")
_REASONS = {
    "wrong_scope": "Wrong scope",
    "too_big": "Too big",
    "not_now": "Not now",
    "other": "Other",
}
_ID = re.compile(r"\Am-[0-9a-f]{12}\Z")
_LIMIT = re.compile(r"\A[1-9]\d{0,3}\Z")
_TS = re.compile(r"\A(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z\Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)  # WHY: datetime.min overflows when shifted west of UTC


def _clock(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime")
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _generated(now: datetime | None) -> str:
    return _clock(now).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    match = _TS.fullmatch(value)
    if match is None:
        return None
    body, frac = match.group(1), match.group(2)
    if frac is not None:
        body = f"{body}.{(frac + '000000')[:6]}"
    try:
        parsed = datetime.fromisoformat(body)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _when(msg: dict) -> datetime:
    return _parse_ts(msg.get("ts")) or _EPOCH


def _day(when: datetime, zone):
    return when.astimezone(zone).date()


def _json_files(lab: Path, rel: str) -> list[tuple[str, bytes]]:
    """Regular *.json files under lab/rel. Links and hard links are skipped."""
    try:
        dfd = labfs.open_dir(lab, rel)
    except (FileNotFoundError, OSError):
        return []
    try:
        try:
            names = os.listdir(dfd)
        except OSError:
            return []
    finally:
        os.close(dfd)
    found: list[tuple[str, bytes]] = []
    for name in names:
        if isinstance(name, bytes):
            try:
                name = name.decode("utf-8")
            except UnicodeDecodeError:
                continue
        if not name.endswith(".json") or name.startswith(".") or "/" in name:
            continue
        child = f"{rel}/{name}"
        try:
            info = labfs.lstat(lab, child)
        except OSError:
            continue
        if info is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            continue
        try:
            raw = labfs.read(lab, child)
        except OSError:
            continue
        if raw is not None:
            found.append((name, raw))
    return found


def _parse_message(raw: bytes) -> dict | None:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if roles.validate(data):
        return None
    return data


def _load_messages(lab: Path) -> dict[str, dict]:
    """Inbox wins when the same id is also in done."""
    found: dict[str, dict] = {}
    for box in ("done", "inbox"):
        for role in _ROLES:
            for _name, raw in _json_files(lab, f".heliopause/{box}/{role}"):
                msg = _parse_message(raw)
                if msg is not None:
                    found[msg["id"]] = msg
    return found


def _waiting(msg: dict) -> bool:
    to = msg.get("to")
    return (
        msg.get("kind") == "ask"
        and isinstance(to, dict)
        and to.get("role") == "hq"
        and msg.get("state") in _OPEN
    )


def _threads(by_id: dict[str, dict]) -> dict[str, str]:
    cache: dict[str, str] = {}

    def walk(mid: str, stack: tuple[str, ...]) -> str:
        if mid in cache:
            return cache[mid]
        if mid in stack:
            return min(stack)
        msg = by_id.get(mid)
        reply = msg.get("reply_to") if msg else None
        if not isinstance(reply, str):
            cache[mid] = mid
            return mid
        root = walk(reply, stack + (mid,))
        cache[mid] = root
        return root

    for mid in by_id:
        walk(mid, ())
    return {mid: cache[mid] for mid in by_id}


def _view(msg: dict, thread: str) -> dict:
    out = dict(msg)
    out["waiting_on_you"] = _waiting(msg)
    out["thread"] = thread
    return out


def _views(lab: Path) -> list[dict]:
    by_id = _load_messages(lab)
    threads = _threads(by_id)
    return [_view(msg, threads[msg["id"]]) for msg in by_id.values()]


def _flat(query: object) -> dict | None:
    if not isinstance(query, dict):
        return None
    flat: dict = {}
    for key, value in query.items():
        if not isinstance(key, str) or key not in _QUERY:
            return None
        if isinstance(value, list):
            if len(value) != 1:
                return None
            value = value[0]
        if not isinstance(value, str) or value == "":
            return None
        flat[key] = value
    if "role" in flat and flat["role"] not in _ROLE_OK:
        return None
    if "state" in flat and flat["state"] not in _STATE_OK:
        return None
    if "thread" in flat and _ID.fullmatch(flat["thread"]) is None:
        return None
    if "limit" in flat:
        if _LIMIT.fullmatch(flat["limit"]) is None or int(flat["limit"]) > 1000:
            return None
        flat["limit"] = int(flat["limit"])
    else:
        flat["limit"] = 200
    return flat


def _match(msg: dict, flat: dict) -> bool:
    if "role" in flat and flat["role"] not in (msg["from"]["role"], msg["to"]["role"]):
        return False
    if "tool" in flat and msg["from"]["tool"] != flat["tool"]:
        return False
    if "repo" in flat and msg.get("repo") != flat["repo"]:
        return False
    if "state" in flat:
        if flat["state"] == "waiting":
            if not msg["waiting_on_you"]:
                return False
        elif msg["state"] != flat["state"]:
            return False
    if "thread" in flat and msg["thread"] != flat["thread"]:
        return False
    return True


def _to_tool(seats: dict, role: object) -> str | None:
    """The target seat's tool when that seat has exactly one known tool."""
    if role in ("hq", "lab"):
        tool = seats.get(role)
        return tool if tool in TOOLS else None
    if role != "factory":
        return None
    known = [tool for tool in (seats.get("factory") or []) if tool in TOOLS]
    return known[0] if len(known) == 1 else None


def messages(lab, query) -> tuple[int, dict]:
    """GET /api/messages. Read-only: no lease, no ack."""
    flat = _flat(query)
    if flat is None:
        return 400, {"error": "bad query"}
    limit = flat.pop("limit")
    kept = [item for item in _views(Path(lab)) if _match(item, flat)]
    kept.sort(key=lambda item: (_when(item), item["id"]), reverse=True)
    shown = kept[:limit]
    # Response copies only. A stored message has no top-level tool, and to.tool is rejected.
    seats = roles_config.read(Path(lab))["roles"]
    for item in shown:
        if item.get("kind") != "job":
            continue
        to = item.get("to")
        tool = _to_tool(seats, to.get("role") if isinstance(to, dict) else None)
        if tool:
            item["to_tool"] = tool
    return 200, {"generated": _generated(None), "messages": shown}


def _doc(lab: Path) -> dict:
    try:
        raw = labfs.read(lab, "atlas.json")
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _zone(lab: Path):
    # WHY: heliopause users are anywhere. A tz ZoneInfo rejects falls back to the machine zone.
    name = _doc(lab).get("tz")
    if isinstance(name, str):
        try:
            return ZoneInfo(name)
        except (KeyError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo


def _shown_path(path) -> str:
    absolute = Path(path).absolute()
    try:
        relative = absolute.relative_to(Path.home())
    except ValueError:
        shown = str(absolute)
    else:
        shown = "~/" + relative.as_posix()
    if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/~+-" for char in shown):
        return shlex.quote(str(absolute))
    return shown


def _card(tool: str, raw: bytes) -> dict | None:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("tool") != tool:
        return None
    if data.get("state") not in _CARD_STATES:
        return None
    when = _parse_ts(data.get("ts"))
    session = data.get("session")
    if when is None or not isinstance(session, str) or session == "":
        return None
    repo = data.get("repo")
    if repo is not None and not isinstance(repo, str):
        return None
    if repo == "":
        repo = None
    return {"state": data["state"], "ts": data["ts"], "when": when, "session": session, "repo": repo}


def _load_cards(lab: Path) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for tool in TOOLS:
        cards = []
        for _name, raw in _json_files(lab, f".heliopause/cards/{tool}"):
            card = _card(tool, raw)
            if card is not None:
                cards.append(card)
        if cards:
            found[tool] = cards
    return found


def _newest(cards: list[dict]) -> dict:
    return max(cards, key=lambda card: (card["when"], card["session"]))


def _seat_state(tool: str, card: dict | None, clock: datetime) -> str:
    if card is None:
        return "idle"
    state = card["state"]
    if tool in _QUIET and state == "working" and (clock - card["when"]).total_seconds() > _STALE_S:
        return "maybe_waiting"
    return state


def _open_count(msgs: list[dict], role: str) -> int:
    total = 0
    for msg in msgs:
        to = msg.get("to")
        if isinstance(to, dict) and to.get("role") == role and msg.get("state") not in ("done", "failed"):
            total += 1
    return total


def _member(name: str, tool: str | None, state: str, since: str | None, repo: str | None, waiting: int) -> dict:
    return {"name": name, "tool": tool, "state": state, "since": since, "repo": repo, "waiting": waiting}


def _tool_member(name: str, tool: str, cards: dict[str, list[dict]], clock: datetime, waiting: int) -> dict:
    card = _newest(cards[tool]) if tool in cards else None
    return _member(
        name,
        tool,
        _seat_state(tool, card, clock),
        None if card is None else card["ts"],
        None if card is None else card["repo"],
        waiting,
    )


def _seat_name(role: object, tool: object, factory: list[str]) -> str | None:
    """Name for a role, same words Now seats use. An unknown role is None."""
    if role == "hq":
        return "Reviewer"
    if role == "lab":
        return "Planner"
    if role != "factory":
        return None
    if isinstance(tool, str) and tool in factory:
        index = factory.index(tool)
        if index < len(_FACTORY):
            return _FACTORY[index]
    return "Builder"


def _seats(lab: Path, msgs: list[dict], cards: dict[str, list[dict]], clock: datetime, needs: bool) -> dict:
    roles_now = roles_config.read(lab)["roles"]
    factory_tools = roles_now.get("factory") or []
    waiting = {role: _open_count(msgs, role) for role in _ROLES}
    hq = [_member("You", None, "needs_you" if needs else "idle", None, None, waiting["hq"])]
    hq_tool = roles_now.get("hq")
    if hq_tool in TOOLS:
        hq.append(_tool_member(_seat_name("hq", hq_tool, factory_tools), hq_tool, cards, clock, waiting["hq"]))
    lab_seat = []
    lab_tool = roles_now.get("lab")
    if lab_tool in TOOLS:
        lab_seat.append(_tool_member(_seat_name("lab", lab_tool, factory_tools), lab_tool, cards, clock, waiting["lab"]))
    factory = []
    for index, tool in enumerate(factory_tools):
        if tool in TOOLS and index < len(_FACTORY):
            factory.append(_tool_member(_seat_name("factory", tool, factory_tools), tool, cards, clock, waiting["factory"]))
    return {"hq": hq, "lab": lab_seat, "factory": factory}


def _known_repos(lab: Path, msgs: list[dict], cards: dict[str, list[dict]]) -> set[str]:
    """Lab repos from configured_repos, plus any repo named on a queue message or a tool card."""
    names = set(configured_repos(lab / "atlas.json"))
    for msg in msgs:
        repo = msg.get("repo")
        if isinstance(repo, str) and repo:
            names.add(repo)
    for group in cards.values():
        for card in group:
            repo = card.get("repo")
            if isinstance(repo, str) and repo:
                names.add(repo)
    return names


def _counts(lab: Path, msgs: list[dict], cards: dict[str, list[dict]], zone, day) -> dict:
    """The counts block shared by now(), today(), and repos()."""
    return {
        "messages_today": sum(1 for msg in msgs if _day(_when(msg), zone) == day),
        "tools_connected": len(cards),
        "repos": len(_known_repos(lab, msgs, cards)),
    }


def _github_unreadable(github_on: bool, snapshot: dict | None) -> bool:
    """On, and the poller has never returned a good poll, or its last poll failed."""
    if not github_on:
        return False
    snap = snapshot if isinstance(snapshot, dict) else {}
    return snap.get("updated") is None or bool(snap.get("stale"))


def _sentence(asks: int, failed: int, moving: int, messages_today: int, tools_connected: int) -> str:
    # COPY.md "Now" only writes the paired busy line and the plural calm line.
    # The clauses below are those sentences split, with singular agreement.
    if asks and failed:
        ask = "1 ask is waiting on you" if asks == 1 else f"{asks} asks are waiting on you"
        job = "1 job failed" if failed == 1 else f"{failed} jobs failed"
        return f"{ask}, and {job}."
    if asks:
        ask = "1 ask is waiting on you" if asks == 1 else f"{asks} asks are waiting on you"
        return f"{ask}."
    if failed:
        return "1 job failed." if failed == 1 else f"{failed} jobs failed."
    if moving:
        move = "1 job is moving" if moving == 1 else f"{moving} jobs are moving"
        return f"Nothing needs you. {move}."
    if messages_today == 0:
        if tools_connected == 0:
            return "Nothing yet. No tool has checked in."
        return "Nothing yet. Your tools are connected and idle."
    return "Nothing needs you."


def now(lab, now: datetime | None = None) -> dict:
    """GET /api/now. `now` freezes the clock for the lab's day and the 10 min rule."""
    root = Path(lab)
    clock = _clock(now)
    zone = _zone(root)
    today = _day(clock, zone)
    by_id = _load_messages(root)
    threads = _threads(by_id)
    views = [_view(msg, threads[msg["id"]]) for msg in by_id.values()]
    stored = list(by_id.values())
    cards = _load_cards(root)
    needs = [item for item in views if item["waiting_on_you"]]
    needs.sort(key=lambda item: (_when(item), item["id"]))
    moving = [item for item in views if _day(_when(item), zone) == today]
    moving.sort(key=lambda item: (_when(item), item["id"]), reverse=True)
    failed = sum(1 for msg in stored if msg.get("kind") == "job" and msg.get("state") == "failed")
    moving_jobs = sum(1 for msg in stored if msg.get("kind") == "job" and msg.get("state") in _OPEN)
    counts = _counts(root, stored, cards, zone, today)
    return {
        "generated": _generated(clock),
        "sentence": _sentence(len(needs), failed, moving_jobs, counts["messages_today"], counts["tools_connected"]),
        "counts": counts,
        "needs_you": needs,
        "seats": _seats(root, stored, cards, clock, bool(needs)),
        "app_dir": _shown_path(Path(__file__).resolve().parents[1]),
        "moving": moving[:8],
        "github": github.enabled(root),
    }


_JOB_STATES = ("sent", "picked_up", "done", "failed")
_CHECK_KEYS = ("tests", "review", "ci")
_CI_WORD = {
    "green": "green", "red": "red", "pending": "pending", "none": "none",
    "pass": "green", "fail": "red", "failure": "red",
}
_CI_CHECK = {"green": "pass", "red": "fail", "pending": "pending", "none": "none"}


def today(lab, now: datetime, *, github_on: bool, snapshot: dict | None = None) -> dict:
    """GET /api/today. Today's done and failed jobs, plus today's pull requests when GitHub is on and readable."""
    root = Path(lab)
    clock = _clock(now)
    zone = _zone(root)
    day = _day(clock, zone)
    by_id = _load_messages(root)
    stored = list(by_id.values())
    threads = _threads(by_id)
    loaded = _load_cards(root)
    factory = roles_config.read(root)["roles"].get("factory") or []
    doers = _report_tools(stored)
    unreadable = _github_unreadable(github_on, snapshot)
    grouped: dict[str, list[dict]] = {}
    for msg in stored:
        # sent and picked_up belong to Now and Messages.
        if msg.get("kind") != "job" or msg.get("state") not in ("done", "failed") or _day(_when(msg), zone) != day:
            continue
        repo = msg.get("repo")
        if not isinstance(repo, str) or not repo:
            continue
        grouped.setdefault(repo, []).append(_job_row(msg, threads, factory, doers))
    if github_on and not unreadable:
        for repo, row in _pr_rows(snapshot, zone, day, factory):
            grouped.setdefault(repo, []).append(row)
    blocks = []
    for repo, rows in grouped.items():
        rows.sort(key=_row_key, reverse=True)
        blocks.append({"repo": repo, "failed": any(_row_failed(row) for row in rows), "rows": rows})
    blocks.sort(key=lambda block: (not block["failed"], -_when(block["rows"][0]).timestamp(), block["repo"]))
    return {
        "generated": _generated(clock),
        "github": bool(github_on),
        "github_unreadable": unreadable,
        "lab_dir": _shown_path(root),
        "counts": _counts(root, stored, loaded, zone, day),
        "repos": blocks,
    }


def repos(lab, now: datetime, *, github_on: bool, snapshot: dict | None = None) -> dict:
    """GET /api/repos. One card per known repo, including zeros. checks stays empty until repo-standard lines exist."""
    root = Path(lab)
    clock = _clock(now)
    zone = _zone(root)
    day = _day(clock, zone)
    stored = list(_load_messages(root).values())
    loaded = _load_cards(root)
    jobs: dict[str, dict[str, int]] = {}
    for msg in stored:
        if msg.get("kind") != "job" or msg.get("state") not in _JOB_STATES or _day(_when(msg), zone) != day:
            continue
        repo = msg.get("repo")
        if not isinstance(repo, str) or not repo:
            continue
        bucket = jobs.setdefault(repo, {name: 0 for name in _JOB_STATES})
        bucket[msg["state"]] += 1
    unreadable = _github_unreadable(github_on, snapshot)
    prs: dict[str, list[dict]] = {}
    if github_on and not unreadable:
        for item in _pr_items(snapshot):
            card = _pr_card(item)
            if card is None:
                continue
            prs.setdefault(item["repo"], []).append(card)
        for rows in prs.values():
            rows.sort(key=lambda row: row["number"], reverse=True)

    def _problem(repo: str) -> bool:
        failed = (jobs.get(repo) or {}).get("failed", 0)
        return failed > 0 or any(row.get("ci") == "red" for row in prs.get(repo, ()))

    names = sorted(_known_repos(root, stored, loaded) | set(prs), key=lambda repo: (not _problem(repo), repo))
    cards = [{
        "repo": repo,
        "jobs_today": jobs.get(repo) or {name: 0 for name in _JOB_STATES},
        "prs": prs.get(repo, []),
        "checks": [],
    } for repo in names]
    return {
        "generated": _generated(clock),
        "github": bool(github_on),
        "github_unreadable": unreadable,
        "lab_dir": _shown_path(root),
        "counts": _counts(root, stored, loaded, zone, day),
        "repos": cards,
    }


def _report_tools(stored: list[dict]) -> dict[str, str]:
    """Job id to from.tool of the newest report whose reply_to is that job."""
    best: dict[str, tuple[tuple, str]] = {}
    for msg in stored:
        if msg.get("kind") != "report":
            continue
        job_id = msg.get("reply_to")
        if not isinstance(job_id, str):
            continue
        frm = msg.get("from") if isinstance(msg.get("from"), dict) else {}
        tool = frm.get("tool")
        if not isinstance(tool, str) or not tool:
            continue
        key = (_when(msg), msg.get("id") if isinstance(msg.get("id"), str) else "")
        prev = best.get(job_id)
        if prev is None or key > prev[0]:
            best[job_id] = (key, tool)
    return {job_id: tool for job_id, (_key, tool) in best.items()}


def _job_row(msg: dict, threads: dict[str, str], factory: list[str], doers: dict[str, str]) -> dict:
    frm = msg.get("from") if isinstance(msg.get("from"), dict) else {}
    sender = frm.get("tool")
    tool = sender
    role = frm.get("role")
    # A done or failed job names who did it. No report yet: keep the sender.
    if msg.get("state") in ("done", "failed"):
        did = doers.get(msg["id"]) if isinstance(msg.get("id"), str) else None
        if did is not None:
            tool = did
            to = msg.get("to") if isinstance(msg.get("to"), dict) else {}
            role = to.get("role")
    checks = msg.get("checks") if isinstance(msg.get("checks"), dict) else None
    kept = {key: checks[key] for key in _CHECK_KEYS if isinstance(checks, dict) and key in checks} or None
    return {
        "kind": "job",
        "ts": msg["ts"],
        "state": msg["state"],
        "summary": msg["summary"],
        "member": _seat_name(role, tool, factory),
        "tool": tool if isinstance(tool, str) and tool else None,
        "checks": kept,
        "id": msg["id"],
        "thread": threads.get(msg["id"], msg["id"]),
        "pr": None,
        "by_you": sender == "you",
    }


def _pr_items(snapshot: dict | None) -> list[dict]:
    if not isinstance(snapshot, dict):
        return []
    items = snapshot.get("prs")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _pr_rows(snapshot: dict | None, zone, day, factory: list[str]) -> list[tuple[str, dict]]:
    found = []
    for item in _pr_items(snapshot):
        repo = item.get("repo")
        ts = item.get("updated")
        number = item.get("number")
        if not isinstance(repo, str) or not repo or type(number) is not int or not isinstance(ts, str):
            continue
        when = _parse_ts(ts)
        if when is None or _day(when, zone) != day:
            continue
        title = item.get("title") if isinstance(item.get("title"), str) else ""
        tool = item.get("tool") if isinstance(item.get("tool"), str) and item.get("tool") else None
        found.append((repo, {
            "kind": "pr",
            "ts": ts,
            "state": _pr_state(item),
            "summary": title,
            "member": _pr_member(item, factory),
            "tool": tool,
            "checks": _pr_checks(item),
            "id": None,
            "thread": None,
            "pr": number,
            "by_you": _by_you(item),
        }))
    return found


def _pr_card(item: dict) -> dict | None:
    repo = item.get("repo")
    number = item.get("number")
    if not isinstance(repo, str) or not repo or type(number) is not int:
        return None
    title = item.get("title") if isinstance(item.get("title"), str) else ""
    return {"number": number, "title": title, "state": _pr_state(item), "ci": _ci(item)}


def _pr_state(item: dict) -> str:
    if item.get("state") == "merged" or item.get("merged") is True or item.get("merged_at"):
        return "merged"
    return "opened"


def _ci(item: dict) -> str:
    raw = item.get("checks")
    if isinstance(raw, dict):
        raw = raw.get("ci")
    return _CI_WORD.get(raw, "none") if isinstance(raw, str) else "none"


def _pr_checks(item: dict) -> dict:
    # "pass" is only a green word. Missing, "none", and unknown stay "none" (the view's "No CI workflow").
    return {"ci": _CI_CHECK.get(_ci(item), "none")}


def _by_you(item: dict) -> bool:
    flag = item.get("by_you")
    if isinstance(flag, bool):
        return flag
    for key in ("user", "author", "login"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("login")
        if value == "you":
            return True
    return False


def _pr_member(item: dict, factory: list[str]) -> str | None:
    role = item.get("role")
    tool = item.get("tool") if isinstance(item.get("tool"), str) and item.get("tool") else None
    frm = item.get("from") if isinstance(item.get("from"), dict) else None
    if frm is not None:
        if not isinstance(role, str) and isinstance(frm.get("role"), str):
            role = frm["role"]
        if tool is None and isinstance(frm.get("tool"), str) and frm.get("tool"):
            tool = frm["tool"]
    if isinstance(role, str):
        return _seat_name(role, tool, factory)
    for key in ("member", "user", "author"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("login")
        if isinstance(value, str) and value:
            return _seat_name(value, tool, factory) if value in _ROLES else value
    return None


def _row_failed(row: dict) -> bool:
    if row.get("state") == "failed":
        return True
    checks = row.get("checks")
    return isinstance(checks, dict) and any(checks.get(key) == "fail" for key in _CHECK_KEYS)


def _row_key(row: dict) -> tuple:
    return (_when(row), row.get("id") or "", row.get("pr") or 0)


def _read_ask(lab: Path, msg_id: str) -> dict | None:
    try:
        raw = labfs.read(lab, f".heliopause/inbox/hq/{msg_id}.json")
    except OSError:
        return None
    if raw is None:
        return None
    msg = _parse_message(raw)
    if msg is None or msg.get("id") != msg_id:
        return None
    return msg


def approve(lab, body) -> tuple[int, dict]:
    """POST /api/approve. The ask must be waiting on hq. Reason only on a decline."""
    if not isinstance(body, dict):
        return 400, {"error": "not an object"}
    if set(body) - {"id", "answer", "reason"}:
        return 400, {"error": "unknown field"}
    msg_id = body.get("id")
    if not isinstance(msg_id, str) or _ID.fullmatch(msg_id) is None:
        return 400, {"error": "bad id"}
    answer = body.get("answer")
    if answer not in ("approve", "decline"):
        return 400, {"error": "answer must be approve or decline"}
    if answer == "approve":
        if "reason" in body:
            return 400, {"error": "reason refused"}
        summary = "Approved"
    else:
        reason = body.get("reason")
        label = _REASONS.get(reason) if isinstance(reason, str) else None
        if label is None:
            return 400, {"error": "reason required"}
        summary = f"Declined: {label}"
    root = Path(lab)
    ask = _read_ask(root, msg_id)
    if ask is None or not _waiting(ask):
        return 409, {"error": "not waiting on you"}
    try:
        reply = roles.new_message(
            "hq", "you", ask["from"]["role"], "answer", summary,
            repo=ask.get("repo"), reply_to=ask["id"],
        )
        roles.send(root, reply)
        roles.set_state(root, "hq", ask["id"], "done")
        roles.ack(root, "hq", ask["id"])
    except (ValueError, OSError):
        return 500, {"error": "write failed"}
    thread = _threads(_load_messages(root)).get(reply["id"], ask["id"])
    return 200, {"ok": True, "answer": _view(reply, thread)}


def mark(lab, body) -> tuple[int, dict]:
    """POST /api/jobs/mark. Same rules as `heliopause report`, page-shaped result."""
    if not isinstance(body, dict):
        return 400, {"error": "not an object"}
    if set(body) - {"id", "result"}:
        return 400, {"error": "unknown field"}
    code, payload = job_report.report(lab, body.get("id"), body.get("result"))
    if code == 200:
        return 200, {"ok": True}
    return code, payload

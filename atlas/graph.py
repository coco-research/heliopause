"""Graph payload: lanes, node state, and 24 h traffic fibres."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlas.chats import chat_activity
from atlas.collect import (
    LAB, LAB_LABELS, LAB_LANES, LAB_LEAD, LAB_OWNER, NODE_IDS, PRODUCT_IDS, ROLE_IDS, LANES, _NY, _SEP, _run_ts,
)
from atlas.events import _LABELS, _fmt, history

# id, label, kind, members in NODE_IDS order
_LANES = (
    ("owner", "Owner", "OWNER", ("owner",)),
    ("hq", "HQ", "HQ", (*ROLE_IDS, "hq")),
    ("vp", "VP", "LAB", ("vp",)),
    (
        "departments",
        "Departments",
        "DEPT",
        (
            "dept-engineering",
            "dept-design",
            "dept-research",
            "dept-budget",
            "dept-repo-office",
            "dept-lab-ops",
        ),
    ),
    (
        "products",
        "Products",
        "PRODUCT",
        PRODUCT_IDS,
    ),
    (
        "factories",
        "Factories",
        "FACTORY",
        ("factory-queue", "factory-grok", "factory-gemini", "factory-zcode"),
    ),
    ("outside", "Outside", "OUTSIDE", ("github", "users")),
)
if LAB:
    _LANES = LAB_LANES

# needs-owner is listed so the quiet rule matches the spec; rule 1 already took it.
_QUIET = {"working", "blocked", "needs-owner", "needs-vp", "needs-hq", "unknown"}
_DAY = timedelta(hours=24)
_NODE_IDS = set(NODE_IDS)
_HQ = {*ROLE_IDS, "hq"}
_FIBRE_CAP = 12
_FIBRE_BUDGET = 120
# WHY 120 s: Claude Code writes the transcript on each turn, and a long tool call can
# leave it untouched for a minute or so. 2 minutes keeps a chat that is mid-task lit without holding a finished one on.
_ACTIVE_SECONDS = 120
_RECENT_CAP = 300


def _parse(ts: object) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age(delta: timedelta) -> tuple[int, int]:
    hours, _seconds = divmod(int(delta.total_seconds()), 3600)
    return divmod(hours, 24)


def _quiet_word(days: int, hours: int) -> str:
    age = f"{days}d" if hours == 0 else f"{days}d {hours}h"
    return f"Quiet {age}"


def _factory_headline(node_id: str, state: dict) -> str:
    lanes = state.get("bus", {}).get("lanes", {})
    info = lanes.get(node_id) or {}
    spec = LANES.get(node_id, {})
    busy = info.get("busy", 0)
    slots = info.get("slots", spec.get("slots", 0))
    jobs_today = info.get("jobs_today", 0)
    jobs_noun = "job" if jobs_today == 1 else "jobs"
    parts = [
        f"{busy} of {slots} busy",
        f"{jobs_today} {jobs_noun} today",
    ]
    pool = info.get("pool")
    left = pool.get("left") if isinstance(pool, dict) else None
    if pool and pool.get("name") is not None and isinstance(left, (int, float)) and not isinstance(left, bool):
        left_pct = f"{int(round(left * 100))}%"
        resets_val = pool.get("resets")
        resets_dt = _run_ts(resets_val) if resets_val else None
        if resets_dt is not None:
            resets_str = resets_dt.astimezone(_NY).strftime("%a %H:%M")
            parts.append(f"{pool['name']} {left_pct} left, resets {resets_str}")
        else:
            parts.append(f"{pool['name']} {left_pct} left")
    headline = _SEP.join(parts)
    paused = info.get("paused")
    if paused:
        headline = f"Paused: {paused}{_SEP}{headline}"
    return headline


def _view(node_id: str, node: dict, state: dict, now: datetime) -> tuple[str, str, int | None]:
    """Graph state, word, and quiet day-count. First match wins."""
    status = node["status"]
    stamp = _parse(node["ts"])
    if node_id == "owner":
        waiting = sum(1 for item in state["decisions"] if item.get("status") == "needs-owner")
        if waiting:
            word = "1 waits for you" if waiting == 1 else f"{waiting} wait for you"
            return "waits", word, None
    if status == "needs-owner":
        return "waits", "Waits for you", None
    jobq = state["bus"]["queue"]
    if node_id == "factory-queue" and jobq["failed"]:
        failed = jobq["failed"]
        word = "1 job failed" if failed == 1 else f"{failed} jobs failed"
        return "failed", word, None
    if node_id in ("factory-gemini", "factory-zcode"):
        lanes = state.get("bus", {}).get("lanes", {})
        info = lanes.get(node_id)
        if info:
            if info.get("paused"):
                return "paused", "Paused", None
            if info.get("at_cap"):
                return "at_cap", "At cap", None
            if info.get("busy", 0) > 0:
                return "working", "Working", None
    # A live job queue is working, even when the board stamp is a day old.
    busy = node_id == "factory-queue" and (jobq["inbox"] or jobq["running"])
    if status in _QUIET and stamp is not None and now - stamp >= _DAY and not busy:
        days, hours = _age(now - stamp)
        return "quiet", _quiet_word(days, hours), days
    if status == "blocked":
        return "blocked", "Blocked", None
    if status == "working":
        return "working", "Working", None
    if status == "needs-vp":
        word = f"Waiting on {LAB_LABELS[LAB_LEAD]}" if LAB else "Waiting on VP"
        return "waiting", word, None
    if status == "needs-hq":
        return "waiting", "Waiting on HQ", None
    if status == "done":
        return "done", "Done", None
    return "idle", "Idle", None


def _badge(node_id: str, state: dict, quiet_days: int | None) -> dict | None:
    if node_id == "factory-queue":
        jobq = state["bus"]["queue"]
        if jobq["failed"]:
            return {"text": f"{jobq['failed']} failed", "tone": "red"}
        jobs = jobq["inbox"] + jobq["running"]
        if jobs:
            noun = "job" if jobs == 1 else "jobs"
            return {"text": f"{jobs} {noun}", "tone": "ink"}
    elif node_id in ("factory-gemini", "factory-zcode"):
        lanes = state.get("bus", {}).get("lanes", {})
        info = lanes.get(node_id) or {}
        busy = info.get("busy", 0)
        slots = info.get("slots", LANES.get(node_id, {}).get("slots", 0))
        if busy > 0:
            return {"text": f"{busy} of {slots}", "tone": "ink"}
    elif node_id in _HQ:
        # Exactly what the panel's "Asks waiting" counts: hq ask items that name this node.
        asks = sum(1 for item in state["decisions"] if item.get("kind") == "hq" and node_id in item.get("for", ()))
        if asks:
            noun = "ask" if asks == 1 else "asks"
            return {"text": f"{asks} {noun}", "tone": "ink"}
    if quiet_days is not None:
        return {"text": f"quiet {quiet_days}d", "tone": "ink"}
    return None


def _fibres(root: Path, now: datetime) -> tuple[list[dict], int, int, set[str]]:
    """Pair fibres, plus the HQ ids with ask or answer traffic.

    messages and mentions fill at most 12 strands. total_messages and total_mentions are the real 24 h counts.
    """
    messages_n: dict[tuple[str, str], int] = {}
    mentions_n: dict[tuple[str, str], int] = {}
    last: dict[tuple[str, str], str] = {}
    messages = 0
    mentions = 0
    hq_active: set[str] = set()
    for event in history(root, now):
        src, dst = event["from"], event["to"]
        if src not in _NODE_IDS or dst not in _NODE_IDS or src == dst:
            continue
        if event["kind"] in ("ask", "answer"):
            hq_active.update({src, dst} & _HQ)
        key = (src, dst)
        if event["kind"] == "mention":
            mentions += 1
            mentions_n[key] = mentions_n.get(key, 0) + 1
        else:
            messages += 1
            messages_n[key] = messages_n.get(key, 0) + 1
        if key not in last or event["ts"] >= last[key]:
            last[key] = event["ts"]
    # Newest pairs first; past the budget, the oldest pairs drop. Then deal one fibre per pair per round,
    # so every kept pair shows before a busy pair thickens (a greedy fill let 10 pairs eat all 120).
    order = sorted(last, key=lambda item: last[item], reverse=True)[:_FIBRE_BUDGET]
    want = {key: min(messages_n.get(key, 0) + mentions_n.get(key, 0), _FIBRE_CAP) for key in order}
    given = dict.fromkeys(order, 0)
    used = 0
    while used < _FIBRE_BUDGET and any(given[key] < want[key] for key in order):
        for key in order:
            if given[key] < want[key] and used < _FIBRE_BUDGET:
                given[key] += 1
                used += 1
    fibres = []
    for key in order:
        shown_messages = min(messages_n.get(key, 0), given[key])
        fibres.append({
            "from": key[0],
            "to": key[1],
            "count": given[key],
            "messages": shown_messages,
            "mentions": given[key] - shown_messages,
            "total_messages": messages_n.get(key, 0),
            "total_mentions": mentions_n.get(key, 0),
            "last": last[key],
        })
    return fibres, messages, mentions, hq_active


def _recent(root: Path, now: datetime) -> list[dict]:
    """The last hour (history(hours=1) is exactly 60 minutes) of real events for the page's idle replay, oldest first, newest 300."""
    kept = [
        {key: event[key] for key in ("ts", "kind", "from", "to", "headline")}
        for event in history(root, now, hours=1)
        if event["from"] in _NODE_IDS
        and event["to"] in _NODE_IDS
        and event["from"] != event["to"]
    ]
    return kept[-_RECENT_CAP:]


def build(state: dict, root: Path, now: datetime, owner_name: str = "Owner") -> dict:
    """Lanes, node state and 24 h fibres for one snapshot."""
    nodes = {}
    lanes = []
    chats = chat_activity(root)
    fibres, messages, mentions, hq_active = _fibres(root, now)
    for lane_id, label, kind, members in _LANES:
        lanes.append({"id": lane_id, "label": label, "nodes": list(members)})
        for node_id in members:
            node = state["nodes"][node_id]
            view, word, quiet_days = _view(node_id, node, state, now)
            chat = chats.get(node_id)
            headline = (
                _factory_headline(node_id, state)
                if node_id in ("factory-gemini", "factory-zcode")
                else node["headline"]
            )
            nodes[node_id] = {
                "id": node_id,
                "lane": lane_id,
                "kind": kind,
                "label": (LAB_OWNER if LAB else owner_name) if node_id == "owner" else _LABELS[node_id],
                "state": view,
                "word": word,
                "badge": _badge(node_id, state, quiet_days),
                "headline": headline,
                "ts": node["ts"],
                # Extra signal only: never feeds state, word or colour.
                "active": chat is not None and now.timestamp() - chat <= _ACTIVE_SECONDS,
                "chat_ts": None if chat is None else _fmt(datetime.fromtimestamp(chat, timezone.utc)),
                # HQ ids only: true when an ask or answer touched it in the last 24 h.
                "active_24h": node_id in hq_active,
            }
    return {
        "lanes": lanes,
        "nodes": nodes,
        "fibres": fibres,
        "recent": _recent(root, now),
        "messages_24h": messages,
        "mentions_24h": mentions,
    }

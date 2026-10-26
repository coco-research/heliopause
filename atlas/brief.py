"""Agent read verbs over one snapshot: what is on my plate (brief) and who handles a topic (who)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from atlas.collect import map_path
from atlas.events import history
from atlas.problems import detect

# node id -> topic words, checked in by hand. A test pins its keys to NODE_IDS.
_DIRECTORY = json.loads(map_path("directory.json").read_text(encoding="utf-8"))
_MENTIONS = 10
# ponytail: board history is read per request over a 7-day window; a mention older than that is not shown.
_WINDOW_H = 24 * 7


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _view(graph: dict, node_id: str) -> dict:
    node = graph["nodes"][node_id]
    # WHY: the owner's configured name is a personal name. Output says "Owner".
    label = "Owner" if node_id == "owner" else node["label"]
    return {"node": node_id, "label": label, "state": node["state"], "headline": node["headline"], "ts": node["ts"]}


def brief(state: dict, graph: dict, root: Path, now: datetime, me: str, *, disk: bool = True) -> dict | None:
    """What `me` should look at first, or None when `me` is not a node."""
    if me not in graph["nodes"]:
        return None
    mine = _view(graph, me)
    since = state["nodes"][me]["ts"]
    mentions = [
        {"ts": event["ts"], "from": event["from"], "headline": event["headline"]}
        for event in history(root, now, _WINDOW_H)
        # Board lines only: file events (asks, jobs) are not "named on the board". A line with no headline stays, headline null.
        if event["id"].startswith("board:")
        and event["to"] == me
        and event["from"] != me
        and (since is None or event["ts"] > since)
    ]
    return {
        "me": me,
        "label": mine["label"],
        "state": mine["state"],
        "headline": mine["headline"],
        "ts": mine["ts"],
        "problems": [item for item in detect(state, graph, root, now, disk=disk) if item["owner"] == me],
        # Handoffs arrive in row 7.
        "inbox": [],
        "mentions": sorted(mentions, key=lambda item: item["ts"], reverse=True)[:_MENTIONS],
    }


def who(graph: dict, topic: str) -> list[dict]:
    """Up to 3 nodes whose topics or label words match the query, best first, ties by node id."""
    query = _words(topic)
    scored = []
    for node_id, topics in _DIRECTORY.items():
        if node_id not in graph["nodes"]:
            continue
        found = _view(graph, node_id)
        score = len(query & (_words(" ".join(topics)) | _words(found["label"])))
        if score:
            scored.append((-score, node_id, found))
    return [{**found, "score": -rank} for rank, _id, found in sorted(scored, key=lambda row: row[:2])[:3]]

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from atlas.collect import NODE_IDS, snapshot
from atlas.events import Watcher, board_event, history
from atlas.graph import build
from atlas.__main__ import make_server

SEP = " \u00b7 "
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
QUIET_28 = (NOW - timedelta(hours=28)).strftime("%Y-%m-%dT%H:%M:%SZ")
QUIET_24 = (NOW - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
QUIET_48 = (NOW - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
RECENT = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
ALMOST = (NOW - timedelta(hours=23, minutes=59)).strftime("%Y-%m-%dT%H:%M:%SZ")

_HQ_ROLES = {
    "hq-cos": "Chief of Staff", "hq-ceo": "CEO", "hq-cpo": "CPO", "hq-cto": "CTO", "hq-cfo": "CFO",
    "hq-coo": "COO", "hq-cmo": "CMO", "hq-cso": "CSO", "hq-cco": "CCO", "hq-gc": "General Counsel",
}
_LANE_IDS = ["owner", "hq", "vp", "departments", "products", "factories", "outside"]
_LANE_LABELS = ["Owner", "HQ", "VP", "Departments", "Products", "Factories", "Outside"]
_KINDS = {
    "owner": "OWNER",
    "hq": "HQ",
    "vp": "LAB",
    "departments": "DEPT",
    "products": "PRODUCT",
    "factories": "FACTORY",
    "outside": "OUTSIDE",
}
_LABELS = {
    "owner": "Owner",
    **_HQ_ROLES,
    "hq": "HQ",
    "vp": "VP",
    "dept-engineering": "Engineering",
    "dept-design": "Design",
    "dept-research": "Research",
    "dept-budget": "Budget Office",
    "dept-repo-office": "Repo Office",
    "dept-lab-ops": "Lab Ops",
    "product-nimbus": "Nimbus",
    "brain-courier": "CoCo Courier",
    "brain-si": "Coco Mind",
    "brain-teamsng": "Harbor",
    "brain-connect-m0": "Connect + M0",
    "brain-voice": "Coco Voice",
    "brain-atlas": "Atlas",
    "brain-keyring": "Keyring",
    "factory-queue": "Queue Factory",
    "factory-grok": "Grok Build",
    "factory-gemini": "Antigravity",
    "factory-zcode": "Z Code",
    "github": "GitHub",
    "users": "Users",
}
_NODE_KEYS = {"id", "lane", "kind", "label", "state", "word", "badge", "headline", "ts", "active", "chat_ts", "active_24h"}


def _blank(nodes=None, decisions=None, pi=None, hq=None):
    base = {
        node_id: {
            "id": node_id,
            "status": "idle",
            "ts": None,
            "headline": None,
            "details": None,
            "name": None,
        }
        for node_id in NODE_IDS
    }
    for node_id, fields in (nodes or {}).items():
        base[node_id].update(fields)
    bus_pi = {"inbox": 0, "running": 0, "done": 0, "failed": 0}
    bus_pi.update(pi or {})
    bus_hq = {"inbox": 0, "open": 0, "done": 0}
    bus_hq.update(hq or {})
    return {
        "nodes": base,
        "decisions": list(decisions or []),
        "bus": {"queue": bus_pi, "hq": bus_hq, "grok": {}, "lanes": {}},
    }


def _graph(tmp_path, owner_name="Owner", **kwargs):
    return build(_blank(**kwargs), tmp_path, NOW, owner_name)


def _z(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _line(when: datetime, name: str, status: str, fourth: str, fifth: str | None = "note") -> str:
    local = when.astimezone(NY).strftime("%Y-%m-%d %H:%M ET")
    parts = [local, name, status, fourth]
    if fifth is not None:
        parts.append(fifth)
    return SEP.join(parts)


def _board(root: Path, lines: list[str], partial: str | None = None) -> None:
    path = root / ".heliopause" / "inbox" / "lab" / "board.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    if partial is not None:
        body += partial.rstrip("\n")
    path.write_text(body, encoding="utf-8")


def _touch(path: Path, when: datetime, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    when = when.astimezone(timezone.utc).replace(microsecond=0)
    os.utime(path, (when.timestamp(), when.timestamp()))
    return _z(when)


def test_lanes_labels_and_owner(tmp_path):
    graph = _graph(tmp_path)
    assert set(graph) == {"lanes", "nodes", "fibres", "recent", "messages_24h", "mentions_24h"}
    assert graph["mentions_24h"] == 0
    assert [lane["id"] for lane in graph["lanes"]] == _LANE_IDS
    assert [lane["label"] for lane in graph["lanes"]] == _LANE_LABELS
    seen = [node_id for lane in graph["lanes"] for node_id in lane["nodes"]]
    assert seen == list(NODE_IDS)
    assert len(seen) == len(set(seen))
    for lane in graph["lanes"]:
        assert set(lane) == {"id", "label", "nodes"}
        for node_id in lane["nodes"]:
            node = graph["nodes"][node_id]
            assert set(node) == _NODE_KEYS
            assert node["id"] == node_id
            assert node["lane"] == lane["id"]
            assert node["kind"] == _KINDS[lane["id"]]
            assert node["state"] == "idle"
            assert node["word"] == "Idle"
            assert node["badge"] is None
            if node_id == "factory-gemini":
                assert node["headline"] == "0 of 3 busy · 0 jobs today"
            elif node_id == "factory-zcode":
                assert node["headline"] == "0 of 5 busy · 0 jobs today"
            else:
                assert node["headline"] is None
            assert node["ts"] is None
    assert {node_id: graph["nodes"][node_id]["label"] for node_id in NODE_IDS} == _LABELS
    named = _graph(tmp_path, owner_name="Test Owner")
    assert named["nodes"]["owner"]["label"] == "Test Owner"
    assert named["nodes"]["hq"]["label"] == "HQ"


def test_headline_passes_through(tmp_path):
    graph = _graph(
        tmp_path,
        nodes={"vp": {"status": "working", "ts": RECENT, "headline": "Ecosystem ready"}},
    )
    node = graph["nodes"]["vp"]
    assert node["headline"] == "Ecosystem ready"
    assert node["ts"] == RECENT
    assert node["state"] == "working"
    assert node["word"] == "Working"


@pytest.mark.parametrize(
    "node_id, fields, pi, state_name, word",
    [
        ("dept-design", {"status": "needs-owner", "ts": QUIET_28}, None, "waits", "Waits for you"),
        ("dept-design", {"status": "working", "ts": QUIET_28}, None, "quiet", "Quiet 1d 4h"),
        ("dept-design", {"status": "working", "ts": QUIET_24}, None, "quiet", "Quiet 1d"),
        ("dept-design", {"status": "working", "ts": QUIET_48}, None, "quiet", "Quiet 2d"),
        ("dept-design", {"status": "working", "ts": ALMOST}, None, "working", "Working"),
        ("dept-design", {"status": "working", "ts": RECENT}, None, "working", "Working"),
        ("dept-design", {"status": "blocked", "ts": RECENT}, None, "blocked", "Blocked"),
        ("dept-design", {"status": "blocked", "ts": QUIET_28}, None, "quiet", "Quiet 1d 4h"),
        ("dept-research", {"status": "needs-hq", "ts": RECENT}, None, "waiting", "Waiting on HQ"),
        ("dept-research", {"status": "needs-hq", "ts": QUIET_28}, None, "quiet", "Quiet 1d 4h"),
        ("brain-teamsng", {"status": "needs-vp", "ts": RECENT}, None, "waiting", "Waiting on VP"),
        ("brain-teamsng", {"status": "needs-vp", "ts": QUIET_28}, None, "quiet", "Quiet 1d 4h"),
        ("brain-voice", {"status": "unknown", "ts": None}, None, "idle", "Idle"),
        ("brain-voice", {"status": "unknown", "ts": RECENT}, None, "idle", "Idle"),
        ("brain-voice", {"status": "unknown", "ts": QUIET_28}, None, "quiet", "Quiet 1d 4h"),
        ("dept-lab-ops", {"status": "done", "ts": QUIET_48}, None, "done", "Done"),
        ("github", {"status": "idle", "ts": QUIET_48}, None, "idle", "Idle"),
        (
            "factory-queue",
            {"status": "working", "ts": QUIET_28},
            {"failed": 2},
            "failed",
            "2 jobs failed",
        ),
        (
            "factory-queue",
            {"status": "working", "ts": RECENT},
            {"failed": 1},
            "failed",
            "1 job failed",
        ),
        (
            "factory-queue",
            {"status": "working", "ts": QUIET_28},
            {"running": 1},
            "working",
            "Working",
        ),
        (
            "factory-queue",
            {"status": "working", "ts": QUIET_28},
            {"inbox": 2},
            "working",
            "Working",
        ),
        (
            "factory-queue",
            {"status": "needs-owner", "ts": QUIET_28},
            {"failed": 3},
            "waits",
            "Waits for you",
        ),
        ("factory-queue", {"status": "blocked", "ts": QUIET_28}, None, "quiet", "Quiet 1d 4h"),
    ],
)
def test_state_rules(tmp_path, node_id, fields, pi, state_name, word):
    node = _graph(tmp_path, nodes={node_id: fields}, pi=pi)["nodes"][node_id]
    assert node["state"] == state_name
    assert node["word"] == word


def test_owner_wait_count(tmp_path):
    one = _graph(
        tmp_path,
        nodes={"owner": {"status": "idle"}},
        decisions=[{"status": "needs-owner"}, {"status": "needs-vp"}],
    )["nodes"]["owner"]
    assert one["state"] == "waits"
    assert one["word"] == "1 waits for you"
    assert one["badge"] is None
    two = _graph(
        tmp_path,
        nodes={"owner": {"status": "working", "ts": QUIET_28}},
        decisions=[{"status": "needs-owner"}, {"status": "needs-owner"}],
    )["nodes"]["owner"]
    assert two["state"] == "waits"
    assert two["word"] == "2 wait for you"
    own = _graph(tmp_path, nodes={"owner": {"status": "needs-owner", "ts": QUIET_28}})["nodes"]["owner"]
    assert own["state"] == "waits"
    assert own["word"] == "Waits for you"


def test_badges(tmp_path):
    assert _graph(tmp_path, pi={"inbox": 2, "running": 1})["nodes"]["factory-queue"]["badge"] == {
        "text": "3 jobs",
        "tone": "ink",
    }
    assert _graph(tmp_path, pi={"running": 1})["nodes"]["factory-queue"]["badge"] == {
        "text": "1 job",
        "tone": "ink",
    }
    failed = _graph(tmp_path, pi={"failed": 2, "inbox": 4, "running": 1})["nodes"]["factory-queue"]
    assert failed["badge"] == {"text": "2 failed", "tone": "red"}
    assert failed["state"] == "failed"
    assert _graph(tmp_path, pi={"failed": 1})["nodes"]["factory-queue"]["badge"] == {
        "text": "1 failed",
        "tone": "red",
    }
    asks = [{"kind": "hq", "for": ["hq"]}] * 6
    assert _graph(tmp_path, decisions=asks)["nodes"]["hq"]["badge"] == {"text": "6 asks", "tone": "ink"}
    assert _graph(tmp_path, decisions=asks[:1])["nodes"]["hq"]["badge"] == {"text": "1 ask", "tone": "ink"}
    quiet = _graph(tmp_path, nodes={"dept-design": {"status": "working", "ts": QUIET_28}})
    assert quiet["nodes"]["dept-design"]["badge"] == {"text": "quiet 1d", "tone": "ink"}
    both = _graph(
        tmp_path,
        nodes={"hq": {"status": "working", "ts": QUIET_28}},
        decisions=asks,
    )["nodes"]["hq"]
    assert both["state"] == "quiet"
    assert both["word"] == "Quiet 1d 4h"
    assert both["badge"] == {"text": "6 asks", "tone": "ink"}
    idle = _graph(tmp_path)
    assert idle["nodes"]["factory-queue"]["badge"] is None
    assert idle["nodes"]["hq"]["badge"] is None
    assert idle["nodes"]["users"]["badge"] is None


def _chat(root, folder, age_s, name="s.jsonl"):
    path = root / ".claude" / "projects" / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    stamp = NOW.timestamp() - age_s
    os.utime(path, (stamp, stamp))
    return _z(datetime.fromtimestamp(stamp, timezone.utc))


def test_chat_active_is_signal_only(tmp_path):
    base = "-Users-someone-Alex-Doe-Coco-Research"
    fresh = _chat(tmp_path, base + "-products-coco-atlas", 60)
    _chat(tmp_path, base + "-ops-departments-design", 150)
    _chat(tmp_path, base + "-products-keyring", 600)
    plain = _graph(tmp_path / "empty")
    nodes = _graph(tmp_path)["nodes"]
    assert nodes["brain-atlas"]["active"] is True
    assert nodes["brain-atlas"]["chat_ts"] == fresh
    assert nodes["dept-design"]["active"] is False
    assert nodes["dept-design"]["chat_ts"] is not None
    assert nodes["brain-keyring"]["active"] is False
    assert nodes["vp"]["active"] is False
    assert nodes["vp"]["chat_ts"] is None
    for node_id, node in nodes.items():
        assert (node["state"], node["word"]) == (plain["nodes"][node_id]["state"], plain["nodes"][node_id]["word"])


def test_recent_window_order_and_shape(tmp_path):
    _board(tmp_path, [
        _line(NOW - timedelta(minutes=90), "Budget Office", "needs-hq", "Too old"),
        _line(NOW - timedelta(minutes=30), "Design Dept", "working", "Newer step"),
        _line(NOW - timedelta(minutes=10), "Engineering Dept", "working", "Newest step"),
        _line(NOW - timedelta(minutes=20), "Nobody Known", "working", "Unknown endpoint"),
    ])
    recent = _graph(tmp_path)["recent"]
    assert [item["headline"] for item in recent] == ["Newer step", "Newest step"]
    assert all(set(item) == {"ts", "kind", "from", "to", "headline"} for item in recent)
    assert recent[0]["from"] == "dept-design" and recent[0]["to"] == "vp"
    assert recent == sorted(recent, key=lambda item: item["ts"])
    assert "/" not in json.dumps(recent)


def test_recent_cap_keeps_newest(tmp_path):
    # Board stamps have minute resolution: 6 lines a minute, oldest first, the last 58 minutes.
    lines = [_line(NOW - timedelta(minutes=59 - nth // 6), "Design Dept", "working", f"Step {nth}") for nth in range(350)]
    _board(tmp_path, lines)
    recent = _graph(tmp_path)["recent"]
    assert len(recent) == 300
    assert recent[0]["headline"] == "Step 50"
    assert recent[-1]["headline"] == "Step 349"


def test_fibres_window_and_pair_cap(tmp_path):
    edge = NOW - timedelta(hours=24)
    outside = NOW - timedelta(hours=24, minutes=1)
    inside = [NOW - timedelta(minutes=1 + nth) for nth in range(15)]
    lines = [_line(when, "Design Dept", "working", "Ship the atlas page") for when in inside]
    lines.append(_line(edge, "Budget Office", "needs-hq", "Need a budget call"))
    lines.append(_line(outside, "Engineering Dept", "working", "Outside the window"))
    _board(tmp_path, lines, partial=_line(NOW - timedelta(minutes=1), "Design Dept", "working", "Partial line here"))
    graph = _graph(tmp_path)
    assert graph["messages_24h"] == 16
    assert graph["fibres"] == [
        {
            "from": "dept-design",
            "to": "vp",
            "count": 12,
            "messages": 12,
            "mentions": 0,
            "total_messages": 15,
            "total_mentions": 0,
            "last": _z(inside[0]),
        },
        {
            "from": "dept-budget",
            "to": "hq",
            "count": 1,
            "messages": 1,
            "mentions": 0,
            "total_messages": 1,
            "total_mentions": 0,
            "last": _z(edge),
        },
    ]
    assert graph["mentions_24h"] == 0
    assert history(tmp_path, NOW, hours=1)
    assert all(event["from"] != "dept-engineering" for event in history(tmp_path, NOW))
    short = history(tmp_path, NOW, hours=1)
    assert all(event["ts"] >= _z(NOW - timedelta(hours=1)) for event in short)
    assert len(short) < 16


def test_fibres_keep_newest_until_budget(tmp_path):
    pairs = [
        ("Design Dept", "dept-design"),
        ("Engineering Dept", "dept-engineering"),
        ("Budget Office", "dept-budget"),
        ("Repo Office", "dept-repo-office"),
        ("Lab Ops", "dept-lab-ops"),
        ("Nimbus Brain", "product-nimbus"),
        ("CoCo Courier Brain", "brain-courier"),
        ("Coco Mind Brain", "brain-si"),
        ("Harbor Brain", "brain-teamsng"),
        ("Coco Voice Brain", "brain-voice"),
        ("Atlas Brain Agent", "brain-atlas"),
    ]
    lines = []
    for index, (name, _node) in enumerate(pairs):
        count = 15 if index == 0 else 12
        for nth in range(count):
            when = NOW - timedelta(minutes=(index + 1) + nth * 20)
            lines.append(_line(when, name, "working", "Ship the atlas page"))
    lines.append(_line(NOW - timedelta(hours=30), "Keyring Brain Agent", "working", "Old note today"))
    _board(
        tmp_path,
        lines,
        partial=_line(NOW - timedelta(minutes=1), "Design Dept", "working", "Partial line here"),
    )
    graph = _graph(tmp_path)
    assert graph["messages_24h"] == 135
    # Round-robin: all 11 recent pairs show; 120 fibres split 11 x 10 + 10 extra to the newest pairs.
    assert [item["from"] for item in graph["fibres"]] == [node for _name, node in pairs]
    assert [item["count"] for item in graph["fibres"]] == [11] * 10 + [10]
    assert all(item["to"] == "vp" for item in graph["fibres"])
    assert sum(item["count"] for item in graph["fibres"]) == 120
    assert graph["fibres"][0]["last"] == _z(NOW - timedelta(minutes=1))


def test_fibres_drop_self_and_unknown(tmp_path):
    kept = NOW - timedelta(hours=1, minutes=3)
    _board(
        tmp_path,
        [
            _line(NOW - timedelta(hours=1), "Mystery Team", "working", "Hello from outside"),
            _line(NOW - timedelta(hours=1, minutes=1), "VP", "resolved", "VP: closed the loop", None),
            _line(NOW - timedelta(hours=1, minutes=2), "VP", "working", "Checking the board"),
            _line(kept, "Design Dept", "needs-owner", "Need a decision"),
        ],
    )
    events = history(tmp_path, NOW)
    assert any(event["from"] == "other" for event in events)
    assert any(event["from"] == "vp" and event["to"] == "vp" for event in events)
    assert all(event["headline"] != "Checking the board" for event in events)
    graph = _graph(tmp_path)
    assert graph["messages_24h"] == 1
    assert graph["fibres"] == [{
        "from": "dept-design",
        "to": "owner",
        "count": 1,
        "messages": 1,
        "mentions": 0,
        "total_messages": 1,
        "total_mentions": 0,
        "last": _z(kept),
    }]
    assert graph["mentions_24h"] == 0


def test_history_files_and_grok(tmp_path):
    ask_at = NOW - timedelta(hours=2)
    answer_at = NOW - timedelta(hours=3)
    old = NOW - timedelta(hours=30)
    future = NOW + timedelta(hours=1)
    edge = NOW - timedelta(hours=24)
    just_out = NOW - timedelta(hours=24, seconds=1)
    ask_ts = _touch(
        tmp_path / ".heliopause" / "inbox" / "hq" / "inbox" / "ask-0008.md",
        ask_at,
        "---\nfrom: VP\nto: hq\n---\n# Please review the queue\n",
    )
    _touch(
        tmp_path / ".heliopause" / "inbox" / "hq" / "inbox" / "ask-0009.md",
        ask_at,
        "---\nfrom: Design Dept\n---\n# Review /tmp/REPO_SENTINEL now\n",
    )
    _touch(
        tmp_path / ".heliopause" / "inbox" / "hq" / "inbox" / "ask-0099.md",
        old,
        "---\nfrom: VP\n---\n# Too old to show\n",
    )
    answer_ts = _touch(
        tmp_path / ".heliopause" / "inbox" / "hq" / "done" / "ask-0008.md",
        answer_at,
        "---\nto: Design Dept\n---\n# Closed the ask\n",
    )
    job_ts = _touch(
        tmp_path / ".heliopause" / "inbox" / "queue" / "inbox" / "job.md",
        NOW - timedelta(hours=4),
        "---\nfrom: Design Dept\nrepo: /tmp/REPO_SENTINEL\n---\n# Run the nightly pass\nBODY_SENTINEL\n",
    )
    run_ts = _touch(
        tmp_path / ".heliopause" / "inbox" / "queue" / "running" / "run.md",
        NOW - timedelta(hours=5),
        "---\nfrom: Design Dept\nrepo: /tmp/REPO_SENTINEL\n---\n# Running now\n",
    )
    done_ts = _touch(
        tmp_path / ".heliopause" / "inbox" / "queue" / "done" / "fin.md",
        NOW - timedelta(hours=6),
        "---\nfrom: Budget Office\n---\n# Finished the pass\n",
    )
    fail_ts = _touch(
        tmp_path / ".heliopause" / "inbox" / "queue" / "failed" / "bad.md",
        NOW - timedelta(hours=7),
        "---\nfrom: Lab Ops\n---\n# The pass failed\n",
    )
    _touch(
        tmp_path / ".heliopause" / "inbox" / "queue" / "failed" / "old.md",
        old,
        "---\nfrom: Lab Ops\n---\n# Old failure\n",
    )
    grok = tmp_path / ".heliopause" / "inbox" / "lab" / "usage" / "grok-build.jsonl"
    grok.parent.mkdir(parents=True, exist_ok=True)
    inside_ts = _z(NOW - timedelta(hours=8))
    grok.write_text(
        "\n".join((
            json.dumps({"ts": inside_ts, "cwd": "/tmp/REPO_SENTINEL"}),
            json.dumps({"ts": _z(old)}),
            json.dumps({"ts": _z(future)}),
            json.dumps({"ts": _z(edge)}),
            json.dumps({"ts": _z(just_out)}),
            "not json",
            json.dumps({"turns": 1}),
            json.dumps({"ts": "nope"}),
            "{bad",
        ))
        + "\n" + '{"ts": "' + inside_ts + '", "cwd": "/tmp/REPO_SENTINEL"}',
        encoding="utf-8",
    )
    events = history(tmp_path, NOW)
    assert events == history(tmp_path, NOW)
    assert [event["ts"] for event in events] == sorted(event["ts"] for event in events)
    found = {(event["kind"], event["from"], event["to"], event["ts"], event["headline"]) for event in events}
    assert ("ask", "vp", "hq", ask_ts, "Please review the queue") in found
    assert ("ask", "dept-design", "hq", ask_ts, None) in found
    assert ("answer", "hq", "dept-design", answer_ts, None) in found
    assert ("job", "dept-design", "factory-queue", job_ts, None) in found
    assert ("job-start", "factory-queue", "dept-design", run_ts, None) in found
    assert ("job-done", "factory-queue", "dept-budget", done_ts, None) in found
    assert ("job-failed", "factory-queue", "dept-lab-ops", fail_ts, None) in found
    assert ("job-done", "factory-grok", "vp", inside_ts, None) in found
    assert ("job-done", "factory-grok", "vp", _z(edge), None) in found
    assert all(event["ts"] != _z(old) for event in events)
    assert all(event["ts"] != _z(just_out) for event in events)
    assert all(event["ts"] != _z(future) for event in events)
    assert all("/" not in event["id"] for event in events)
    blob = json.dumps(events)
    assert "/" not in blob
    assert "REPO_SENTINEL" not in blob
    assert "BODY_SENTINEL" not in blob
    assert "Too old to show" not in blob
    graph = build(snapshot(tmp_path), tmp_path, NOW)
    rendered = json.dumps(graph)
    assert "/" not in rendered
    assert "REPO_SENTINEL" not in rendered
    assert "BODY_SENTINEL" not in rendered
    for name in ("ask-0008.md", "ask-0009.md", "job.md", "run.md", "fin.md", "bad.md", "grok-build.jsonl"):
        assert name not in rendered
    assert graph["messages_24h"] == len(events)
    assert graph["fibres"]


def test_board_event_matches_watcher(tmp_path):
    rows = [
        ("2026-10-01 09:00 ET", "Design Dept", "working", "Ship the atlas page", "extra context"),
        ("2026-10-01 09:01 ET", "Design Dept", "needs-owner", "Need a decision", "please look"),
        ("2026-10-01 09:02 ET", "VP", "resolved", "Design Dept: no action needed"),
        ("2026-10-01 09:03 ET", "VP", "working", "Checking the board", "quiet"),
        ("2026-10-01 09:04 ET", "Research & Innovation", "needs-hq", "Need a budget call", "please"),
        ("2026-10-01 09:05 ET", "Mystery Team", "working", "Hello from outside", "note"),
        ("2026-10-01 09:06 ET", "VP", "resolved", "Gap is closed", "Design Dept: no action needed"),
        ("2026-10-01 09:07 ET", "Design Dept", "working", "Ship sk-zzzzzzzz today", "extra"),
        ("2026-10-01 09:08 ET", "Design Dept", "working", "See ~/notes/board.md today", "extra"),
    ]
    direct = []
    for parts in rows:
        event = board_event(SEP.join(parts), "board:test")
        if event is not None:
            direct.append(event)
    _board(tmp_path, [])
    watcher = Watcher(tmp_path)
    watcher.poll()
    _board(tmp_path, [SEP.join(parts) for parts in rows])
    watcher.poll()
    live, _seq = watcher.events_after(0)
    assert len(live) == len(direct)
    keys = ("from", "to", "kind", "headline")
    for left, right in zip(direct, live):
        assert {key: left[key] for key in keys} == {key: right[key] for key in keys}
    assert board_event(SEP.join(rows[3]), "board:test") is None
    gap = next(event for event in direct if event["headline"] == "Gap is closed")
    assert gap["from"] == "vp"
    assert gap["to"] == "dept-design"
    assert gap["kind"] == "resolved"
    redacted = next(event for event in direct if event["headline"] and "redacted" in event["headline"])
    assert redacted["headline"] == "Ship [redacted] today"
    assert direct[-1]["headline"] is None


def test_history_read_error_skips(tmp_path):
    missing = history(tmp_path / "nope", NOW)
    assert missing == []
    board = tmp_path / ".heliopause" / "inbox" / "lab" / "board.md"
    _board(tmp_path, [_line(NOW - timedelta(hours=1), "Design Dept", "working", "Ship the atlas page")])
    if os.geteuid() == 0:
        return
    board.chmod(0)
    try:
        assert history(tmp_path, NOW) == []
    finally:
        board.chmod(0o644)


def test_server_graph_owner(tmp_path):
    plain = make_server(tmp_path, 0, watch=False)
    plain.server_close()
    assert plain.owner_name == "Owner"
    server = make_server(tmp_path, 0, watch=False, owner_name="Test Owner")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.owner_name == "Test Owner"
        port = server.server_address[1]
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/state", headers={"Host": f"127.0.0.1:{port}"})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        assert resp.status == 200
        data = json.loads(body)
        assert len(data["graph"]["lanes"]) == 7
        assert [lane["id"] for lane in data["graph"]["lanes"]] == _LANE_IDS
        assert data["graph"]["nodes"]["owner"]["label"] == "Test Owner"
        assert "/" not in json.dumps(data["graph"])
        assert isinstance(data["graph"]["recent"], list)
        assert all("active" in node for node in data["graph"]["nodes"].values())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_fibre_mention_cap(tmp_path):
    lines = [
        _line(NOW - timedelta(minutes=30 + nth), "Design Dept", "working", "Ship the atlas page")
        for nth in range(10)
    ]
    lines.extend(
        _line(NOW - timedelta(minutes=1 + nth), "Design Dept", "needs-owner", "Tell the VP now")
        for nth in range(5)
    )
    _board(tmp_path, lines)
    graph = _graph(tmp_path)
    fibre = next(item for item in graph["fibres"] if item["from"] == "dept-design" and item["to"] == "vp")
    assert fibre["messages"] == 10
    assert fibre["mentions"] == 2
    assert fibre["count"] == 12
    assert fibre["total_messages"] == 10
    assert fibre["total_mentions"] == 5
    assert graph["messages_24h"] == 15
    assert graph["mentions_24h"] == 5
    assert "/" not in json.dumps(graph)


def test_fibre_totals_are_uncapped(tmp_path):
    lines = [
        _line(NOW - timedelta(minutes=1 + nth), "Design Dept", "working", "Ship the atlas page")
        for nth in range(15)
    ]
    lines.extend(
        _line(NOW - timedelta(minutes=20 + nth), "Design Dept", "needs-owner", "Tell the VP now")
        for nth in range(4)
    )
    _board(tmp_path, lines)
    graph = _graph(tmp_path)
    by_pair = {(item["from"], item["to"]): item for item in graph["fibres"]}
    busy = by_pair[("dept-design", "vp")]
    assert busy["count"] == 12
    assert busy["total_messages"] == 15
    assert busy["total_mentions"] == 4
    assert by_pair[("dept-design", "owner")]["total_messages"] == 4
    assert ("dept-research", "vp") not in by_pair
    assert all(item["total_messages"] + item["total_mentions"] >= 1 for item in graph["fibres"])


def _failed_tree(root, recent):
    failed = root / ".heliopause" / "inbox" / "queue" / "failed"
    failed.mkdir(parents=True)
    files = [("bus-a.md", 72), ("bus-a.log", 72), ("job-x.withdrawn.md", 1)]
    if recent:
        files += [("job-real.md", 1), ("job-real.log", 1)]
    for name, hours in files:
        path = failed / name
        path.write_text("---\nid: cc-pi-real-1\n---\n# Job\n", encoding="utf-8")
        stamp = time.time() - hours * 3600
        os.utime(path, (stamp, stamp))


def test_factory_pi_failed_only_for_recent_real_jobs(tmp_path):
    recent = tmp_path / "recent"
    _failed_tree(recent, recent=True)
    node = build(snapshot(recent), recent, NOW, "Owner")["nodes"]["factory-queue"]
    assert (node["state"], node["word"]) == ("failed", "1 job failed")
    assert node["badge"] == {"text": "1 failed", "tone": "red"}
    quiet = tmp_path / "quiet"
    _failed_tree(quiet, recent=False)
    node = build(snapshot(quiet), quiet, NOW, "Owner")["nodes"]["factory-queue"]
    assert node["state"] != "failed"
    assert node["badge"] is None or node["badge"]["tone"] != "red"

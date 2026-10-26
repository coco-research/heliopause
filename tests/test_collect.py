import json
from datetime import date
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import pytest

from atlas.collect import _SENSITIVE, _board_facts, _lanes, configured_repos, redact, snapshot


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    # WHY: fixture runs are dated 2026-10-01 (ET). Without this, "runs today" fails after midnight.
    monkeypatch.setattr("atlas.collect._today", lambda: date(2026, 10, 1))

HOME = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "home"
IDLE = {"status": "idle", "ts": None, "headline": None, "details": None, "name": None}
SENTINELS = (
    "REPO_SENTINEL",
    "BODY_SENTINEL",
    "RUNNING_BODY_SENTINEL",
    "REPLY_SENTINEL",
    "DONE_REPLY_SENTINEL",
    "json-body-sentinel",
    "log-body-sentinel",
    "dotfile-sentinel",
    "THIS PARTIAL MUST NOT APPLY",
    "sk-ABCDEFGH1234",
    "ghp_abcdefghij",
    "gho_abcdefghij",
    "github_pat_abcdefghij",
    "xai-abcdefghij",
    "template example",
    "bad hour",
    "padded early",
    "old other",
    "first line same instant",
    "older written second",
)


def _snap():
    return snapshot(HOME)


def _keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _keys(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _keys(value)


def test_canonical_nodes_default_idle():
    snap = _snap()
    assert list(snap["nodes"]) == [
        "owner", "hq-cos", "hq-ceo", "hq-cpo", "hq-cto", "hq-cfo", "hq-coo", "hq-cmo", "hq-cso",
        "hq-cco", "hq-gc", "hq", "vp", "dept-engineering", "dept-design", "dept-research", "dept-budget",
        "dept-repo-office", "dept-lab-ops",
        "product-nimbus", "brain-courier", "brain-si", "brain-teamsng", "brain-connect-m0",
        "brain-voice", "brain-atlas", "brain-keyring", "factory-queue", "factory-grok",
        "factory-gemini", "factory-zcode", "github", "users",
    ]
    assert all(
        set(node) == {"id", "status", "ts", "headline", "details", "name"}
        for node in snap["nodes"].values()
    )
    assert all(node["id"] == node_id for node_id, node in snap["nodes"].items())
    for node_id in (
        "owner", "hq", "dept-budget", "dept-repo-office", "dept-lab-ops",
        "brain-courier", "brain-si", "brain-connect-m0", "github", "users",
    ):
        assert {k: snap["nodes"][node_id][k] for k in IDLE} == IDLE


def test_edt_september_converts_to_utc():
    assert _snap()["nodes"]["vp"]["ts"] == "2026-10-01T00:56:00Z"


def test_est_january_converts_to_utc():
    assert _snap()["nodes"]["dept-engineering"]["ts"] == "2026-01-15T15:30:00Z"


def test_zone_word_ignored_uses_new_york():
    # 15:00 EST written in September is still EDT (UTC-4), not a fixed UTC-5.
    assert _snap()["nodes"]["brain-keyring"]["ts"] == "2026-09-30T19:00:00Z"
    assert _snap()["nodes"]["brain-keyring"]["name"] == "Keyring Brain Agent"


def test_out_of_order_latest_instant_wins(tmp_path):
    board = tmp_path / ".heliopause" / "inbox" / "lab" / "board.md"
    board.parent.mkdir(parents=True)
    sep = " \u00b7 "
    board.write_text(
        "\n".join((
            sep.join(("2026-09-30 22:00 ET", "Design Dept", "working", "newer written first")),
            sep.join(("2026-09-30 08:00 ET", "Design Dept", "blocked", "older written second")),
        ))
        + "\n",
        encoding="utf-8",
    )
    design = snapshot(tmp_path)["nodes"]["dept-design"]
    assert design["status"] == "working"
    assert design["ts"] == "2026-10-01T02:00:00Z"
    assert design["details"] == "newer written first"


def test_tie_later_line_wins():
    teams = _snap()["nodes"]["brain-teamsng"]
    assert teams["status"] == "needs-vp"
    assert teams["ts"] == "2026-10-01T01:05:00Z"
    assert teams["name"] == "Harbor Brain"


def test_text_keeps_internal_separator():
    snap = _snap()
    teams = snap["nodes"]["brain-teamsng"]
    # "#175" is a ticket, so the separator stays inside details.
    assert teams["headline"] is None
    assert teams["details"] == "PR #175 \u00b7 extra detail"
    vp = snap["nodes"]["vp"]
    assert vp["headline"] == "Ecosystem v1 ready"
    assert vp["details"] == "first money bet Coco Voice"


def test_alias_pair_merges_to_one_node():
    vp = _snap()["nodes"]["vp"]
    assert vp["status"] == "needs-owner"
    assert vp["name"] == "Lead - VP"
    assert "Board opened" not in vp["details"]


def test_role_suffix_maps_before_full_name(tmp_path):
    board = tmp_path / ".heliopause" / "inbox" / "lab" / "board.md"
    board.parent.mkdir(parents=True)
    sep = " \u00b7 "
    board.write_text(
        sep.join(("2026-09-30 19:00 ET", "Someone - VP", "needs-owner", "via role"))
        + "\n"
        + sep.join(("2026-09-30 18:00 ET", "Design Dept", "working", "via full name"))
        + "\n",
        encoding="utf-8",
    )
    snap = snapshot(tmp_path)
    assert snap["nodes"]["vp"]["name"] == "Someone - VP"
    assert snap["nodes"]["vp"]["status"] == "needs-owner"
    assert snap["nodes"]["vp"]["details"] == "via role"
    assert snap["nodes"]["dept-design"]["name"] == "Design Dept"
    assert snap["nodes"]["dept-design"]["status"] == "working"
    assert snap["nodes"]["dept-design"]["details"] == "via full name"


def test_alias_strip_and_casefold():
    snap = _snap()
    assert snap["nodes"]["brain-atlas"]["name"] == "Atlas Brain Agent"
    assert snap["nodes"]["brain-atlas"]["status"] == "working"
    assert snap["nodes"]["product-nimbus"]["status"] == "done"
    assert snap["nodes"]["product-nimbus"]["ts"] == "2026-09-30T20:00:00Z"
    assert snap["nodes"]["product-nimbus"]["details"] == "case fold works"


def test_unknown_state_and_name():
    snap = _snap()
    assert snap["nodes"]["brain-voice"]["status"] == "unknown"
    assert snap["nodes"]["brain-voice"]["details"] == "not a real state"
    assert snap["other"] == [
        {
            "name": "Mystery Person",
            "status": "working",
            "ts": "2026-09-30T22:00:00Z",
            "headline": None,
            "details": "hello from outside",
        },
        {
            "name": "Mystery Team",
            "status": "needs-owner",
            "ts": "2026-09-30T16:30:00Z",
            "headline": None,
            "details": "waiting on a call",
        },
    ]


def test_trailing_parenthetical_strips_before_alias(tmp_path):
    board = tmp_path / ".heliopause" / "inbox" / "lab" / "board.md"
    board.parent.mkdir(parents=True)
    sep = " \u00b7 "
    board.write_text(
        "\n".join((
            sep.join(("2026-09-30 19:00 ET", "Design Dept (via Lead)", "working", "via note")),
            sep.join(("2026-09-30 18:00 ET", "Desk - VP (via Lead)", "needs-owner", "via role")),
        ))
        + "\n",
        encoding="utf-8",
    )
    snap = snapshot(tmp_path)
    assert snap["nodes"]["dept-design"]["name"] == "Design Dept (via Lead)"
    assert snap["nodes"]["dept-design"]["status"] == "working"
    assert snap["nodes"]["dept-design"]["details"] == "via note"
    assert snap["nodes"]["vp"]["name"] == "Desk - VP (via Lead)"
    assert snap["nodes"]["vp"]["status"] == "needs-owner"
    assert snap["other"] == []


def test_other_needs_reaches_decisions(tmp_path):
    board = tmp_path / ".heliopause" / "inbox" / "lab" / "board.md"
    board.parent.mkdir(parents=True)
    sep = " \u00b7 "
    board.write_text(
        sep.join(("2026-09-30 12:30 ET", "Mystery Team", "needs-owner", "waiting on a call")) + "\n",
        encoding="utf-8",
    )
    assert snapshot(tmp_path)["decisions"] == [
        {
            "kind": "board",
            "node": None,
            "name": "Mystery Team",
            "status": "needs-owner",
            "ts": "2026-09-30T16:30:00Z",
            "headline": None,
            "details": "waiting on a call",
        }
    ]


def test_partial_last_line_ignored():
    raw = (HOME / ".heliopause" / "inbox" / "lab" / "board.md").read_bytes()
    assert not raw.endswith(b"\n")
    assert "THIS PARTIAL MUST NOT APPLY" not in json.dumps(_snap())


def test_redaction():
    text = _snap()["nodes"]["dept-engineering"]["details"]
    assert text == "winter freeze [redacted] [redacted] [redacted] [redacted] [redacted] keep sk-abc"
    assert redact("sk-short") == "sk-short"
    assert redact("prefix sk-ABCDEFGH ghp_12345678 tail") == "prefix [redacted] [redacted] tail"
    assert _SENSITIVE.search("call the broker now")


def test_decisions_oldest_first():
    assert _snap()["decisions"] == [
        {
            "kind": "board",
            "node": "dept-research",
            "status": "needs-hq",
            "ts": "2026-09-01T13:00:00Z",
            "headline": None,
            "details": "need a budget call",
        },
        {"kind": "hq", "private": True, "id": "ask-0002", "from": None, "to": None, "for": [],
         "type": None, "priority": None, "title": "private HQ ask \u00b7 ask-0002",
         "created": None, "ts": "2026-09-15T04:00:00Z"},
        {
            "kind": "hq",
            "id": "ask-0004",
            "from": "vp",
            "to": "hq-cos",
            "for": ["hq-cto"],
            "type": "review-merge",
            "priority": "normal",
            "title": "Fresh ordered batch. This replaces the open items of ask-0001.",
            "created": "2026-09-20",
            "ts": "2026-09-20T04:00:00Z",
        },
        {
            "kind": "board",
            "node": None,
            "name": "Mystery Team",
            "status": "needs-owner",
            "ts": "2026-09-30T16:30:00Z",
            "headline": None,
            "details": "waiting on a call",
        },
        {
            "kind": "board",
            "node": "vp",
            "status": "needs-owner",
            "ts": "2026-10-01T00:56:00Z",
            "headline": "Ecosystem v1 ready",
            "details": "first money bet Coco Voice",
        },
        {
            "kind": "board",
            "node": "brain-teamsng",
            "status": "needs-vp",
            "ts": "2026-10-01T01:05:00Z",
            "headline": None,
            "details": "PR #175 \u00b7 extra detail",
        },
    ]


def test_hq_done_closes_by_filename():
    snap = _snap()
    hq_ids = [item["id"] for item in snap["decisions"] if item.get("kind") == "hq"]
    assert "ask-0001" not in hq_ids
    assert "ask-0003" not in hq_ids
    assert hq_ids == ["ask-0002", "ask-0004"]
    hq = snap["bus"]["hq"]
    assert hq == {"inbox": 4, "open": 2, "done": 2}
    assert "none needed" not in json.dumps(snap).casefold()


def test_done_without_inbox_match_changes_nothing(tmp_path):
    root = tmp_path / "home"
    shutil.copytree(HOME, root)
    unmatched = root / ".heliopause" / "inbox" / "hq" / "done" / "ask-0099.md"
    before = snapshot(root)
    unmatched.unlink()
    after = snapshot(root)
    assert before["decisions"] == after["decisions"]
    assert after["bus"]["hq"]["inbox"] == before["bus"]["hq"]["inbox"]
    assert after["bus"]["hq"]["open"] == before["bus"]["hq"]["open"]
    assert after["bus"]["hq"]["done"] == before["bus"]["hq"]["done"] - 1
    assert [item["id"] for item in after["decisions"] if item.get("kind") == "hq"] == ["ask-0002", "ask-0004"]


def test_bus_counts_and_active_jobs():
    bus = _snap()["bus"]
    assert bus["hq"] == {"inbox": 4, "open": 2, "done": 2}
    assert bus["grok"] == {
        "last_run": "2026-10-01T14:56:29Z",
        "turns": 12,
        "tokens": 2500000,
        "runs_today": 1,
        "tokens_today": 2500000,
        "cap": None,
    }
    grok_node = _snap()["nodes"]["factory-grok"]
    assert grok_node["status"] == "idle"
    assert grok_node["ts"] == "2026-10-01T14:56:29Z"
    assert grok_node["name"] is None
    assert grok_node["headline"] is None
    assert grok_node["details"] == "Last run: 12 turns, 2.5M tokens \u00b7 1 runs today"
    assert {k: bus["queue"][k] for k in ("inbox", "running", "done", "failed")} == {
        "inbox": 1, "running": 1, "done": 1, "failed": 0,
    }
    assert bus["queue"]["failed_items"] == []
    assert bus["queue"]["active"] == [
        {"id": "cc-queue-inbox-1", "state": "inbox", "from": "claude", "to": "queue", "model": "grok"},
        {"id": "cc-queue-running-1", "state": "running", "from": "claude", "to": "queue", "model": "deepseek"},
    ]
    assert _snap()["nodes"]["factory-queue"]["status"] == "working"
    assert _snap()["nodes"]["factory-queue"]["details"] is None


def test_no_repo_or_reply_keys_or_bodies():
    snap = _snap()
    assert set(_keys(snap)).isdisjoint({"repo", "reply", "allowed_tools"})
    blob = json.dumps(snap)
    for secret in SENTINELS:
        assert secret not in blob


def test_missing_root_and_shrink(tmp_path):
    missing = snapshot(tmp_path / "nope")
    assert missing["other"] == []
    assert missing["decisions"] == []
    assert missing["bus"] == {
        "queue": {"inbox": 0, "running": 0, "done": 0, "failed": 0, "active": [], "failed_items": []},
        "hq": {"inbox": 0, "open": 0, "done": 0},
        "grok": {},
        "lanes": _lanes(tmp_path / "nope"),
    }
    assert all(node["status"] == "idle" for node in missing["nodes"].values())

    empty = snapshot(tmp_path)
    assert empty["nodes"]["vp"]["status"] == "idle"

    board = tmp_path / ".heliopause" / "inbox" / "lab" / "board.md"
    board.parent.mkdir(parents=True)
    sep = " \u00b7 "
    first = sep.join(("2026-09-30 19:45 ET", "VP", "working", "first"))
    second = sep.join(("2026-09-30 21:00 ET", "VP", "blocked", "second"))
    board.write_text(first + "\n" + second + "\n", encoding="utf-8")
    assert snapshot(tmp_path)["nodes"]["vp"]["details"] == "second"
    board.write_text(first + "\n", encoding="utf-8")
    assert snapshot(tmp_path)["nodes"]["vp"]["status"] == "working"
    assert snapshot(tmp_path)["nodes"]["vp"]["details"] == "first"


def test_malformed_frontmatter_skipped(tmp_path):
    inbox = tmp_path / ".heliopause" / "inbox" / "hq" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "bad.md").write_text("no frontmatter\n# Title\n", encoding="utf-8")
    (inbox / "ok.md").write_text(
        "---\nid: ok-1\nfrom: vp\nto: hq\nfor: c\ntype: t\npriority: p\ncreated: 2026-01-01\nreply: REPLY_SENTINEL\n---\n# Ok title\n",
        encoding="utf-8",
    )
    pi_inbox = tmp_path / ".heliopause" / "inbox" / "queue" / "inbox"
    pi_inbox.mkdir(parents=True)
    (pi_inbox / "bad.md").write_text("not frontmatter\n", encoding="utf-8")
    snap = snapshot(tmp_path)
    assert snap["bus"]["hq"]["inbox"] == 2
    assert [item["id"] for item in snap["decisions"]] == ["ok-1", None]
    assert snap["bus"]["queue"]["active"] == []
    assert snap["nodes"]["factory-queue"]["status"] == "working"
    assert "REPLY_SENTINEL" not in json.dumps(snap)


def test_hq_created_ts_null_sorts_last(tmp_path):
    inbox = tmp_path / ".heliopause" / "inbox" / "hq" / "inbox"
    inbox.mkdir(parents=True)
    # "0" would sort first if the key fell back to created. Null ts must still sort last.
    (inbox / "a.md").write_text("---\nid: early-text\nfrom: vp\nto: hq\ncreated: 0\n---\n# Early text\n", encoding="utf-8")
    (inbox / "b.md").write_text(
        "---\nid: dated\nfrom: vp\nto: hq\ncreated: 2026-08-01\n---\n# Dated\n",
        encoding="utf-8",
    )
    (inbox / "c.md").write_text(
        "---\nid: stamped\nfrom: vp\nto: hq\ncreated: 2026-01-15 10:30 EST\n---\n# Stamped\n",
        encoding="utf-8",
    )
    (inbox / "d.md").write_text("---\nid: unscheduled\nfrom: vp\nto: hq\ncreated: soon\n---\n# Unscheduled\n", encoding="utf-8")
    decisions = snapshot(tmp_path)["decisions"]
    assert [(item["id"], item["ts"], item["created"]) for item in decisions] == [
        ("stamped", "2026-01-15T15:30:00Z", "2026-01-15 10:30 EST"),
        ("dated", "2026-08-01T04:00:00Z", "2026-08-01"),
        ("early-text", None, "0"),
        ("unscheduled", None, "soon"),
    ]


def test_generated_is_utc_z():
    from datetime import datetime

    datetime.strptime(_snap()["generated"], "%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory mode 000")
def test_unreadable_directory_keeps_other_counts(tmp_path):
    root = tmp_path / "home"
    shutil.copytree(HOME, root)
    before = snapshot(root)
    running = root / ".heliopause" / "inbox" / "queue" / "running"
    mode = running.stat().st_mode
    running.chmod(0)
    try:
        snap = snapshot(root)
    finally:
        running.chmod(mode)
    assert snap["bus"]["queue"]["running"] == 0
    assert {key: snap["bus"]["queue"][key] for key in ("inbox", "done", "failed")} == {
        key: before["bus"]["queue"][key] for key in ("inbox", "done", "failed")
    }
    assert snap["bus"]["hq"] == before["bus"]["hq"]


def _board(tmp_path, lines):
    board = tmp_path / ".heliopause" / "inbox" / "lab" / "board.md"
    board.parent.mkdir(parents=True)
    sep = " \u00b7 "
    board.write_text("\n".join(sep.join(parts) for parts in lines) + "\n", encoding="utf-8")
    return snapshot(tmp_path)


def test_new_team_aliases(tmp_path):
    from atlas.collect import map_path

    aliases = json.loads(map_path("aliases.json").read_text(encoding="utf-8"))
    assert aliases["Keyring Brain Agent"] == "brain-keyring"
    assert aliases["Keyring Brain Agent"] == "brain-keyring"
    assert aliases["Repo Office"] == "dept-repo-office"
    assert aliases["Lab Ops"] == "dept-lab-ops"
    snap = _board(tmp_path, [
        ("2026-09-30 12:00 ET", "Keyring Brain Agent", "working", "spelling"),
        ("2026-09-30 12:01 ET", "Repo Office", "blocked", "watching"),
        ("2026-09-30 12:02 ET", "Lab Ops", "done", "shipped"),
    ])
    assert snap["nodes"]["brain-keyring"]["details"] == "spelling"
    assert snap["nodes"]["dept-repo-office"]["status"] == "blocked"
    assert snap["nodes"]["dept-lab-ops"]["details"] == "shipped"


def test_board_names_in_use_today(tmp_path):
    # The board writes these short forms (10-04); without them the rows show stale headlines.
    from atlas.collect import map_path

    aliases = json.loads(map_path("aliases.json").read_text(encoding="utf-8"))
    assert aliases["Design"] == "dept-design"
    assert aliases["Harbor"] == "brain-teamsng"
    assert aliases["Connect + M0 Brain"] == "brain-connect-m0"
    assert aliases["Atlas Brain"] == "brain-atlas"
    snap = _board(tmp_path, [
        ("2026-10-04 18:00 ET", "Design", "done", "design line"),
        ("2026-10-04 18:01 ET", "Harbor", "working", "ng line"),
        ("2026-10-04 18:02 ET", "Connect + M0 Brain", "blocked", "m0 line"),
        ("2026-10-04 18:03 ET", "Atlas Brain", "done", "atlas line"),
    ])
    assert snap["nodes"]["dept-design"]["details"] == "design line"
    assert snap["nodes"]["brain-teamsng"]["details"] == "ng line"
    assert snap["nodes"]["brain-connect-m0"]["status"] == "blocked"
    assert snap["nodes"]["brain-atlas"]["details"] == "atlas line"


def test_resolved_line_closes_gap_and_skips_poster():
    snap = _snap()
    design = snap["nodes"]["dept-design"]
    assert design["status"] == "done"
    assert design["ts"] == "2026-10-01T13:00:00Z"
    assert design["headline"] is None
    assert design["details"] == "Resolved: no action needed"
    assert design["name"] == "Design Dept"
    vp = snap["nodes"]["vp"]
    assert vp["status"] == "needs-owner"
    assert vp["ts"] == "2026-10-01T00:56:00Z"
    assert vp["headline"] == "Ecosystem v1 ready"
    assert vp["details"] == "first money bet Coco Voice"
    assert all(item.get("node") != "dept-design" for item in snap["decisions"])


def test_later_node_line_beats_resolved(tmp_path):
    snap = _board(tmp_path, [
        ("2026-09-30 10:00 ET", "Design Dept", "needs-owner", "help"),
        ("2026-09-30 11:00 ET", "VP", "resolved", "Design Dept: no action needed"),
        ("2026-09-30 12:00 ET", "Design Dept", "working", "back"),
    ])
    design = snap["nodes"]["dept-design"]
    assert design["status"] == "working"
    assert design["details"] == "back"
    assert design["ts"] == "2026-09-30T16:00:00Z"
    assert snap["nodes"]["vp"]["status"] == "idle"


def test_resolved_unknown_target_is_ignored(tmp_path):
    snap = _board(tmp_path, [
        ("2026-09-30 10:00 ET", "VP", "needs-owner", "open"),
        ("2026-09-30 11:00 ET", "VP", "resolved", "No Such Team: ignore me"),
    ])
    assert snap["nodes"]["vp"]["status"] == "needs-owner"
    assert snap["nodes"]["vp"]["details"] == "open"
    assert snap["other"] == []


def test_resolved_leaves_a_working_target(tmp_path):
    snap = _board(tmp_path, [
        ("2026-09-30 10:00 ET", "Design Dept", "working", "busy"),
        ("2026-09-30 11:00 ET", "VP", "resolved", "Design Dept: no action needed"),
    ])
    design = snap["nodes"]["dept-design"]
    assert design["status"] == "working"
    assert design["details"] == "busy"
    assert design["ts"] == "2026-09-30T14:00:00Z"


def test_resolved_blocked_target_uses_alias(tmp_path):
    snap = _board(tmp_path, [
        ("2026-09-30 10:00 ET", "Design Dept", "blocked", "Stuck on review", "waiting"),
        ("2026-09-30 11:00 ET", "VP", "resolved", "Design Dept (via Lead): cleared: really"),
    ])
    design = snap["nodes"]["dept-design"]
    assert design["status"] == "done"
    assert design["headline"] is None
    assert design["details"] == "Resolved: cleared: really"
    assert design["ts"] == "2026-09-30T15:00:00Z"
    assert design["name"] == "Design Dept"


def test_reply_none_closes_ask(tmp_path):
    inbox = tmp_path / ".heliopause" / "inbox" / "hq" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "ask-0003.md").write_text(
        "---\nid: ask-0003\ncreated: 2026-09-10\nreply:  None Needed \n---\n# Status check\n",
        encoding="utf-8",
    )
    (inbox / "ask-0004.md").write_text(
        "---\nid: ask-0004\nfrom: vp\nto: hq\ncreated: 2026-09-16\nreply: noted\n---\n# Stays\n",
        encoding="utf-8",
    )
    snap = snapshot(tmp_path)
    assert snap["bus"]["hq"] == {"inbox": 2, "open": 1, "done": 0}
    assert [item["id"] for item in snap["decisions"]] == ["ask-0004"]
    assert "reply" not in set(_keys(snap))


def test_title_replaces_closes_later_ids_only(tmp_path):
    inbox = tmp_path / ".heliopause" / "inbox" / "hq" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "ask-0001.md").write_text("---\nid: ask-0001\ncreated: 2026-08-01\n---\n# First\n", encoding="utf-8")
    (inbox / "ask-0002.md").write_text("---\nid: ask-0002\ncreated: 2026-08-02\n---\n# Second\n", encoding="utf-8")
    (inbox / "ask-0009.md").write_text("---\nid: ask-0009\nfrom: vp\nto: hq\ncreated: 2026-08-03\n---\n# Before the word\n", encoding="utf-8")
    (inbox / "ask-0004.md").write_text(
        "---\nid: ask-0004\nfrom: vp\nto: hq\ncreated: 2026-09-20\n---\n"
        "# See ask-0009. This replaces the open items of ask-0001 and ask-0002.\n",
        encoding="utf-8",
    )
    snap = snapshot(tmp_path)
    assert [item["id"] for item in snap["decisions"]] == ["ask-0009", "ask-0004"]
    assert snap["bus"]["hq"] == {"inbox": 4, "open": 2, "done": 0}


def test_supersedes_field_matches_id_or_stem(tmp_path):
    inbox = tmp_path / ".heliopause" / "inbox" / "hq" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "notes.md").write_text("---\nid: ask-0001\ncreated: 2026-08-01\n---\n# Notes\n", encoding="utf-8")
    (inbox / "ask-0002.md").write_text("---\ncreated: 2026-08-02\n---\n# No id\n", encoding="utf-8")
    (inbox / "ask-0001.md").write_text(
        "---\nid: stay-open\nfrom: vp\nto: hq\ncreated: 2026-07-01\n---\n# Stem is not the id\n",
        encoding="utf-8",
    )
    (inbox / "ask-0004.md").write_text(
        "---\nid: ask-0004\nfrom: vp\nto: hq\nsupersedes: ask-0001\ncreated: 2026-09-20\n---\n"
        "# This supersedes ask-0004 and ask-0002.\n",
        encoding="utf-8",
    )
    snap = snapshot(tmp_path)
    assert [item["id"] for item in snap["decisions"]] == ["stay-open", "ask-0004"]
    assert snap["bus"]["hq"] == {"inbox": 4, "open": 2, "done": 0}


def test_reply_none_ask_still_supersedes(tmp_path):
    inbox = tmp_path / ".heliopause" / "inbox" / "hq" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "ask-0001.md").write_text("---\nid: ask-0001\ncreated: 2026-08-01\n---\n# First\n", encoding="utf-8")
    (inbox / "ask-0004.md").write_text(
        "---\nid: ask-0004\ncreated: 2026-09-20\nreply: none needed\n---\n"
        "# This replaces ask-0001.\n",
        encoding="utf-8",
    )
    snap = snapshot(tmp_path)
    assert snap["decisions"] == []
    assert snap["bus"]["hq"] == {"inbox": 2, "open": 0, "done": 0}


def test_done_filename_closes_without_reply(tmp_path):
    inbox = tmp_path / ".heliopause" / "inbox" / "hq" / "inbox"
    done = tmp_path / ".heliopause" / "inbox" / "hq" / "done"
    inbox.mkdir(parents=True)
    done.mkdir(parents=True)
    (inbox / "ask-0008.md").write_text("---\nid: ask-0008\ncreated: 2026-08-01\n---\n# Open\n", encoding="utf-8")
    (done / "ask-0008.md").write_text("# Reply\n", encoding="utf-8")
    snap = snapshot(tmp_path)
    assert snap["decisions"] == []
    assert snap["bus"]["hq"] == {"inbox": 1, "open": 0, "done": 1}


def test_snapshot_omits_cwd_and_user_paths():
    raw = (HOME / ".heliopause" / "inbox" / "lab" / "usage" / "grok-build.jsonl").read_text(encoding="utf-8")
    assert len(raw.splitlines()) == 3
    assert '"cwd": "/' in raw
    snap = _snap()
    assert "cwd" not in set(_keys(snap))
    assert "/home" not in json.dumps(snap)


def test_grok_counts_new_york_day_and_skips_bad_lines(tmp_path):
    usage = tmp_path / ".heliopause" / "inbox" / "lab" / "usage"
    usage.mkdir(parents=True)
    (usage / "grok-build.jsonl").write_text(
        "\n".join((
            '{"ts": "2026-09-30T22:00:00-0400", "turns": 1, "usage": {"total_tokens": 1000}}',
            "not-json",
            '{"ts": "2026-10-01T09:00:00-0400", "turns": 2, "usage": {"total_tokens": 2000}, "cwd": "/Users/<name>/cwd"}',
            '{"ts": "2026-10-01T10:56:29-0400", "turns": 12, "usage": {"total_tokens": 1234567}}',
            "{broken",
        ))
        + "\n",
        encoding="utf-8",
    )
    snap = snapshot(tmp_path)
    assert snap["bus"]["grok"] == {
        "last_run": "2026-10-01T14:56:29Z",
        "turns": 12,
        "tokens": 1234567,
        "runs_today": 2,
        "tokens_today": 1236567,
        "cap": None,
    }
    assert snap["nodes"]["factory-grok"]["details"] == "Last run: 12 turns, 1.2M tokens \u00b7 2 runs today"
    assert "/home" not in json.dumps(snap)
    assert "cwd" not in set(_keys(snap))


def test_fixture_snapshot_hides_repo_text_key_and_user_paths():
    snap = _snap()
    assert set(_keys(snap)).isdisjoint({"repo", "text"})
    assert "/home" not in json.dumps(snap)


def test_tokens_today_is_the_new_york_day_only(tmp_path):
    usage = tmp_path / ".heliopause" / "inbox" / "lab" / "usage"
    usage.mkdir(parents=True)
    (usage / "grok-build.jsonl").write_text(
        '{"ts": "2026-09-30T23:30:00-0400", "turns": 1, "usage": {"total_tokens": 7000}}\n'
        '{"ts": "2026-10-01T00:10:00-0400", "turns": 1, "usage": {"total_tokens": 300}}\n'
        '{"ts": "2026-10-01T09:00:00-0400", "turns": 1, "usage": {"total_tokens": 20}}\n',
        encoding="utf-8",
    )
    assert snapshot(tmp_path)["bus"]["grok"]["tokens_today"] == 320


@pytest.mark.parametrize(
    ("caps", "cap"),
    [
        (None, None),
        ("{nope", None),
        ('{"updated": "x"}', None),
        ('{"grok_build_daily_tokens": 0}', None),
        ('{"grok_build_daily_tokens": -5}', None),
        ('{"grok_build_daily_tokens": "190"}', None),
        ('{"grok_build_daily_tokens": 1.5}', None),
        ("[1]", None),
        ('{"grok_build_daily_tokens": 190000000, "note": "n"}', 190000000),
    ],
)
def test_cap_reads_budget_contract(tmp_path, caps, cap):
    usage = tmp_path / ".heliopause" / "inbox" / "lab" / "usage"
    usage.mkdir(parents=True)
    (usage / "grok-build.jsonl").write_text(
        '{"ts": "2026-10-01T09:00:00-0400", "turns": 1, "usage": {"total_tokens": 20}}\n', encoding="utf-8"
    )
    if caps is not None:
        (usage / "caps.json").write_text(caps, encoding="utf-8")
    assert snapshot(tmp_path)["bus"]["grok"]["cap"] == cap


def test_grok_garbage_is_empty(tmp_path):
    usage = tmp_path / ".heliopause" / "inbox" / "lab" / "usage"
    usage.mkdir(parents=True)
    (usage / "grok-build.jsonl").write_text("nope\n{]\n", encoding="utf-8")
    snap = snapshot(tmp_path)
    assert snap["bus"]["grok"] == {}
    assert {key: snap["nodes"]["factory-grok"][key] for key in ("status", "ts", "headline", "details")} == {
        "status": "idle",
        "ts": None,
        "headline": None,
        "details": None,
    }


def test_grok_card_yields_only_to_a_newer_board_line(tmp_path, monkeypatch):
    import atlas.collect as collect

    monkeypatch.setitem(collect._ALIASES, "grok build", "factory-grok")

    def plant(folder, board_line, grok_ts):
        root = tmp_path / folder
        usage = root / ".heliopause" / "inbox" / "lab" / "usage"
        usage.mkdir(parents=True)
        (usage / "grok-build.jsonl").write_text(
            f'{{"ts": "{grok_ts}", "turns": 3, "usage": {{"total_tokens": 1500000}}}}\n',
            encoding="utf-8",
        )
        board = root / ".heliopause" / "inbox" / "lab" / "board.md"
        board.write_text(" \u00b7 ".join(board_line) + "\n", encoding="utf-8")
        return snapshot(root)

    older = plant(
        "older",
        ("2026-10-01 09:00 ET", "Grok Build", "working", "Still running jobs", "old"),
        "2026-10-01T10:56:29-0400",
    )
    assert older["nodes"]["factory-grok"]["status"] == "idle"
    assert older["nodes"]["factory-grok"]["ts"] == "2026-10-01T14:56:29Z"
    assert older["nodes"]["factory-grok"]["headline"] is None
    assert older["nodes"]["factory-grok"]["details"] == "Last run: 3 turns, 1.5M tokens \u00b7 1 runs today"

    same = plant("same", ("2026-10-01 10:56 ET", "Grok Build", "working", "tied"), "2026-10-01T10:56:00-0400")
    assert same["nodes"]["factory-grok"]["status"] == "idle"
    assert same["nodes"]["factory-grok"]["details"].startswith("Last run:")

    newer = plant("newer", ("2026-10-01 11:30 ET", "Grok Build", "working", "still going"), "2026-10-01T10:56:29-0400")
    assert newer["nodes"]["factory-grok"]["status"] == "working"
    assert newer["nodes"]["factory-grok"]["details"] == "still going"
    assert newer["nodes"]["factory-grok"]["ts"] == "2026-10-01T15:30:00Z"
    assert newer["bus"]["grok"]["last_run"] == "2026-10-01T14:56:29Z"


def test_valid_headline_splits_from_details(tmp_path):
    snap = _board(tmp_path, [
        ("2026-09-30 12:00 ET", "Design Dept", "working", "Need a look today", "the rest", "stays whole"),
    ])
    node = snap["nodes"]["dept-design"]
    assert node["headline"] == "Need a look today"
    assert node["details"] == "the rest \u00b7 stays whole"


@pytest.mark.parametrize(
    "fourth",
    [
        pytest.param("~/src/today", id="path"),
        pytest.param("see https://example.com/a", id="url"),
        pytest.param("fix #12 today", id="ticket"),
        pytest.param("build abc1234 today", id="hash"),
        pytest.param("close ask-0013 today", id="ask-id"),
        pytest.param("read notes.md today", id="filename"),
        pytest.param("listen :4302 now", id="port"),
        pytest.param("Working", id="one-word"),
        pytest.param("z" * 30 + " " + "z" * 30, id="61-chars"),
    ],
)
def test_invalid_headline_stays_in_details(tmp_path, fourth):
    snap = _board(tmp_path, [
        ("2026-09-30 12:00 ET", "Design Dept", "working", fourth, "kept in details"),
    ])
    node = snap["nodes"]["dept-design"]
    assert node["headline"] is None
    assert node["details"] == fourth + " \u00b7 kept in details"


def test_sixty_char_headline_is_kept_whole(tmp_path):
    headline = "Ship atlas " + "z" * 49
    assert len(headline) == 60
    snap = _board(tmp_path, [
        ("2026-09-30 12:00 ET", "VP", "working", headline, "and the rest stays"),
    ])
    node = snap["nodes"]["vp"]
    assert node["headline"] == headline
    assert node["details"] == "and the rest stays"


def test_four_field_long_prose_is_not_sliced(tmp_path):
    prose = ("This board line is long prose " + ("with more words " * 8)).strip()
    assert len(prose) > 60
    snap = _board(tmp_path, [
        ("2026-09-30 12:00 ET", "Design Dept", "done", prose),
    ])
    node = snap["nodes"]["dept-design"]
    assert node["headline"] is None
    assert node["details"] == prose


def test_resolved_keeps_a_valid_headline(tmp_path):
    snap = _board(tmp_path, [
        ("2026-09-30 10:00 ET", "Design Dept", "needs-owner", "Need a decision", "waiting on review"),
        ("2026-09-30 11:00 ET", "VP", "resolved", "Gap is closed", "Design Dept: no action needed"),
    ])
    design = snap["nodes"]["dept-design"]
    assert design["status"] == "done"
    assert design["headline"] == "Gap is closed"
    assert design["details"] == "Resolved: no action needed"
    assert snap["nodes"]["vp"]["status"] == "idle"


def test_resolved_directive_in_headline_slot_still_closes(tmp_path):
    snap = _board(tmp_path, [
        ("2026-10-01 10:00 ET", "Design Dept", "needs-owner", "waiting"),
        ("2026-10-01 11:00 ET", "VP", "resolved", "Design Dept: no action needed", "thanks"),
    ])
    design = snap["nodes"]["dept-design"]
    assert design["status"] == "done"
    assert design["headline"] is None
    assert design["details"] == "Resolved: no action needed"


def test_resolved_five_field_keeps_headline_that_is_not_the_directive(tmp_path):
    snap = _board(tmp_path, [
        ("2026-10-01 10:00 ET", "Design Dept", "needs-owner", "waiting"),
        ("2026-10-01 11:00 ET", "VP", "resolved", "Design cleared", "Design Dept: no action needed"),
    ])
    design = snap["nodes"]["dept-design"]
    assert design["status"] == "done"
    assert design["headline"] == "Design cleared"
    assert design["details"] == "Resolved: no action needed"


def test_failed_items_from_markdown_only(tmp_path, monkeypatch):
    failed = tmp_path / ".heliopause" / "inbox" / "queue" / "failed"
    failed.mkdir(parents=True)
    (failed / "a-noise.log").write_text("log-body-sentinel\n", encoding="utf-8")
    (failed / "b-stem.md").write_text(
        "---\nfrom: claude\nto: queue\nrepo: /home/hidden/repo\n---\n# Stem used\n",
        encoding="utf-8",
    )
    (failed / "m-with-id.md").write_text(
        "---\nid: aaa-job\nmodel: grok\nrepo: /home/hidden/repo\n---\n# Queue blew up\nsecret body\n",
        encoding="utf-8",
    )
    os.utime(failed / "b-stem.md", (1_790_000_000, 1_790_000_000))
    os.utime(failed / "m-with-id.md", (1_790_000_500, 1_790_000_500))
    # WHY: pin the window to the fixture mtimes so the expected ts is fixed.
    monkeypatch.setattr(time, "time", lambda: 1_790_001_000)
    snap = snapshot(tmp_path)
    pi = snap["bus"]["queue"]
    assert pi["failed"] == 2
    assert pi["failed_items"] == [
        {"id": "b-stem", "title": "Stem used", "model": None, "owner": "vp", "ts": "2026-09-21T14:13:20Z"},
        {"id": "aaa-job", "title": "Queue blew up", "model": "grok", "owner": "vp", "ts": "2026-09-21T14:21:40Z"},
    ]
    assert "repo" not in set(_keys(snap))
    blob = json.dumps(snap)
    assert "/Users" not in blob
    assert "secret body" not in blob
    assert "log-body-sentinel" not in blob


def _age(path, hours):
    stamp = time.time() - hours * 3600
    os.utime(path, (stamp, stamp))


def _write_aged(directory, name, hours, text="---\nfrom: claude\n---\n# Job\n"):
    path = directory / name
    path.write_text(text, encoding="utf-8")
    _age(path, hours)


def test_failed_counts_only_recent_distinct_real_jobs(tmp_path):
    failed = tmp_path / ".heliopause" / "inbox" / "queue" / "failed"
    failed.mkdir(parents=True)
    for name in ("bus-a", "bus-b"):
        _write_aged(failed, name + ".md", 72)
        _write_aged(failed, name + ".log", 72, "log\n")
    for name in ("job-x", "job-y", "job-z"):
        _write_aged(failed, name + ".withdrawn.md", 1)
    real = "---\nid: cc-pi-real-1\n---\n# Real failure\n"
    _write_aged(failed, "job-real.md", 1, real)
    _write_aged(failed, "job-real.log", 1, "log\n")
    pi = snapshot(tmp_path)["bus"]["queue"]
    assert pi["failed"] == 1
    assert [item["id"] for item in pi["failed_items"]] == ["cc-pi-real-1"]
    _write_aged(failed, "job-real-retry.md", 1, real)
    pi = snapshot(tmp_path)["bus"]["queue"]
    assert pi["failed"] == 1
    assert len(pi["failed_items"]) == 1


def test_failed_job_without_frontmatter_still_counts(tmp_path):
    failed = tmp_path / ".heliopause" / "inbox" / "queue" / "failed"
    failed.mkdir(parents=True)
    (failed / "bare-job.md").write_text("crashed before writing a header\n", encoding="utf-8")
    pi = snapshot(tmp_path)["bus"]["queue"]
    assert pi["failed"] == 1
    (item,) = pi["failed_items"]
    ts = item.pop("ts")
    assert item == {"id": "bare-job", "title": None, "model": None, "owner": "vp"}
    assert datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


def test_failed_item_owner_is_a_known_node_id(tmp_path):
    failed = tmp_path / ".heliopause" / "inbox" / "queue" / "failed"
    failed.mkdir(parents=True)
    for name, sender in (("a", "Harbor Brain"), ("b", "claude"), ("c", "Nobody Known /home/x")):
        (failed / (name + ".md")).write_text(f"---\nid: {name}\nfrom: {sender}\n---\n# J\n", encoding="utf-8")
    items = snapshot(tmp_path)["bus"]["queue"]["failed_items"]
    assert [item["owner"] for item in items] == ["brain-teamsng", "vp", "vp"]


def test_failed_items_oldest_first_by_mtime(tmp_path):
    failed = tmp_path / ".heliopause" / "inbox" / "queue" / "failed"
    failed.mkdir(parents=True)
    now = time.time()
    # Name order is the reverse of time order: the last item must still be the newest failure.
    for name, age in (("a-new.md", 60), ("z-old.md", 3600)):
        path = failed / name
        path.write_text("---\nid: " + name[:-3] + "\n---\n", encoding="utf-8")
        os.utime(path, (now - age, now - age))
    items = snapshot(tmp_path)["bus"]["queue"]["failed_items"]
    assert [item["id"] for item in items] == ["z-old", "a-new"]


def test_first_run_facts(tmp_path):
    # The init line is the lab existing. A "Session started" line is a hook, and any other line is work.
    sep = " \u00b7 "
    board = tmp_path / ".heliopause" / "inbox" / "lab" / "board.md"
    board.parent.mkdir(parents=True)
    board.write_text(
        "# comment\n\n"
        + sep.join(("2026-10-19 09:00 ET", "Lead", "done", "Lab created", "atlas init"))
        + "\nnot a line\n",
        encoding="utf-8",
    )
    assert _board_facts(tmp_path) == {"board_lines": 0, "hook": False}
    assert snapshot(tmp_path)["board_lines"] == 0 and snapshot(tmp_path)["hook"] is False

    board.write_text(
        board.read_text(encoding="utf-8")
        + sep.join(("2026-10-19 09:05 ET", "Lead", "working", "Session started", "atlas"))
        + "\n"
        + sep.join(("2026-10-19 09:06 ET", "Worker", "working", "Echo job started", "hello"))
        + "\n2026-10-19 09:07 ET · Worker · working · Still writing",
        encoding="utf-8",
    )
    assert _board_facts(tmp_path) == {"board_lines": 2, "hook": True}
    snap = snapshot(tmp_path)
    assert snap["board_lines"] == 2 and snap["hook"] is True
    assert snap["repos"] == configured_repos()

    assert configured_repos(tmp_path / "missing.json") == []
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    (tmp_path / "empty.json").write_text("{}\n", encoding="utf-8")
    assert configured_repos(tmp_path / "bad.json") == []
    assert configured_repos(tmp_path / "empty.json") == []
    (tmp_path / "repos.json").write_text(
        '{"org": "acme", "repos": {"b": "lead", "a": "worker", "": "x"}}\n',
        encoding="utf-8",
    )
    assert configured_repos(tmp_path / "repos.json") == ["a", "b"]
    (tmp_path / "list.json").write_text('{"repos": ["a"]}\n', encoding="utf-8")
    assert configured_repos(tmp_path / "list.json") == []


def test_repos_prefer_the_lab_atlas_json(tmp_path, monkeypatch):
    # Lab atlas.json repos (same shape as repos.json) win. None, empty, or bad JSON falls back.
    import atlas.collect as collect
    from atlas.github import load_config

    package = json.loads(collect.map_path("repos.json").read_text(encoding="utf-8"))
    names = sorted(package["repos"])
    monkeypatch.setattr(collect, "LAB", False)
    monkeypatch.setattr(collect, "LAB_DIR", None)
    assert configured_repos() == names
    assert load_config() == package

    monkeypatch.setattr(collect, "LAB", True)
    monkeypatch.setattr(collect, "LAB_DIR", tmp_path)
    (tmp_path / "atlas.json").write_text("{}\n", encoding="utf-8")
    assert configured_repos() == names and load_config() == package
    (tmp_path / "atlas.json").write_text('{"repos": {}}\n', encoding="utf-8")
    assert configured_repos() == names and load_config() == package
    (tmp_path / "atlas.json").write_text("{", encoding="utf-8")
    assert configured_repos() == names and load_config() == package

    (tmp_path / "atlas.json").write_text(
        json.dumps({"v": 1, "org": "lab-org", "repos": {"zeta": "lead", "alpha": "worker", "": "x"}}),
        encoding="utf-8",
    )
    assert configured_repos() == ["alpha", "zeta"]
    got = load_config()
    assert got["org"] == "lab-org" and got["repos"]["zeta"] == "lead" and "coco-atlas" not in got["repos"]
    (tmp_path / "other.json").write_text('{"repos": {"nope": "x"}}\n', encoding="utf-8")
    assert configured_repos(tmp_path / "other.json") == ["nope"]
    (tmp_path / "atlas.json").write_text(json.dumps({"repos": {"only": "lead"}}), encoding="utf-8")
    assert configured_repos() == ["only"]
    with pytest.raises(ValueError):
        load_config()


def test_legacy_words_still_read():
    # Older internal boards and asks keep working through the internal legacy map ({} in a public build).
    from atlas.collect import _board_state, hq_party

    assert _board_state("needs-" + "boss") == "needs-owner"
    assert hq_party("boss", {}) == "owner"


def test_legacy_queue_folder_ids_and_lanes_still_read(tmp_path):
    from atlas.collect import hq_party, inbox_dir, lane_name

    old = tmp_path / ".heliopause" / "inbox" / "pi"
    (old / "inbox").mkdir(parents=True)
    assert inbox_dir(tmp_path, "queue") == old
    (tmp_path / ".heliopause" / "inbox" / "queue").mkdir()
    assert inbox_dir(tmp_path, "queue") == tmp_path / ".heliopause" / "inbox" / "queue"
    assert hq_party("factory-pi", {}) == "factory-queue"
    assert lane_name("pi-deepseek") == "queue-deepseek" and lane_name("pi") == "queue" and lane_name("pick") == "pick"


def test_legacy_map_is_type_and_path_safe(tmp_path):
    from atlas.collect import _legacy

    bad = tmp_path / "legacy.json"
    # Sub-maps of the wrong type, non-string values, and path-like words are all skipped.
    bad.write_text(json.dumps({
        "states": ["needs-x"],
        "ids": "old",
        "pools": {"a-pool": 3, "b-pool": "fine-pool", "Bad": "x"},
        "dirs": {"queue": "../../etc", "jobs": "a/b", "old": "older"},
        "lanes": None,
    }), encoding="utf-8")
    states, ids, pools, dirs, lanes = _legacy(bad)
    assert states == {} and ids == {} and lanes == {}
    assert pools == {"b-pool": "fine-pool"}
    assert dirs == {"old": "older"}
    # A broken or missing file falls back to empty maps instead of failing at import.
    bad.write_text("{not json", encoding="utf-8")
    assert _legacy(bad) == ({}, {}, {}, {}, {})
    assert _legacy(tmp_path / "missing.json") == ({}, {}, {}, {}, {})
    bad.write_text("[]", encoding="utf-8")
    assert _legacy(bad) == ({}, {}, {}, {}, {})


def test_legacy_inbox_dir_stays_inside_the_inbox(tmp_path, monkeypatch):
    from atlas import collect

    base = tmp_path / ".heliopause" / "inbox"
    base.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (base / "older").symlink_to(outside)
    monkeypatch.setattr(collect, "LEGACY_DIR", {"queue": "older"})
    assert collect.inbox_dir(tmp_path, "queue") == base / "queue"
    (base / "older").unlink()
    (base / "older").mkdir()
    assert collect.inbox_dir(tmp_path, "queue") == base / "older"

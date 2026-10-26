"""Tests for factory lanes (Antigravity/gemini and Z Code/zcode)."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from atlas.collect import (
    LANES,
    NODE_IDS,
    _busy,
    _jobs_today,
    _lane_paused,
    _lane_pool,
    _lanes,
    _usage_pools,
    snapshot,
)
from atlas.graph import build

_ORIG_BUSY = _busy

STATIC = Path(__file__).resolve().parents[1] / "atlas" / "static"
JS = (STATIC / "graph.js").read_text(encoding="utf-8")
CSS = (STATIC / "graph.css").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)


def test_jobs_today(tmp_path, monkeypatch):
    # jobs_today counts only today's NY-date lines of the right lane; bad lines skipped.
    monkeypatch.setattr("atlas.collect._today", lambda: date(2026, 10, 7))
    dispatch = tmp_path / ".heliopause" / "inbox" / "lab" / "usage" / "dispatch.jsonl"
    dispatch.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        # Today's lines in NY time
        json.dumps({"lane": "gemini", "ts_utc": "2026-10-07T14:48:35Z"}),
        json.dumps({"lane": "gemini", "ts_utc": "2026-10-07T10:00:00-04:00"}),
        json.dumps({"lane": "zcode", "ts_utc": "2026-10-07T12:00:00Z"}),
        # Yesterday's lines
        json.dumps({"lane": "gemini", "ts_utc": "2026-10-06T23:59:59-04:00"}),
        json.dumps({"lane": "zcode", "ts_utc": "2026-10-06T14:00:00Z"}),
        # Bad lines that must be skipped
        "",
        "   ",
        "not json",
        json.dumps("a string, not a dict"),
        json.dumps(123),
        json.dumps({"ts_utc": "2026-10-07T14:48:35Z"}),  # missing lane
        json.dumps({"lane": 123, "ts_utc": "2026-10-07T14:48:35Z"}),  # lane not str
        json.dumps({"lane": "gemini"}),  # missing ts_utc
        json.dumps({"lane": "gemini", "ts_utc": "invalid-timestamp"}),  # bad ts_utc
    ]
    dispatch.write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts = _jobs_today(tmp_path)
    assert counts["gemini"] == 2
    assert counts["zcode"] == 1
    assert counts.get("queue") is None or counts.get("queue") == 0

    # Also verify via _lanes
    lanes_data = _lanes(tmp_path)
    assert lanes_data["factory-gemini"]["jobs_today"] == 2
    assert lanes_data["factory-zcode"]["jobs_today"] == 1


def test_pool_selection_and_at_cap(tmp_path):
    # pool picks the smaller left of the two pools; at_cap from left <= 0 and from status "out"; missing usage.json → pool None.
    # Missing usage.json
    assert _usage_pools(tmp_path) == []
    pool, at_cap = _lane_pool([], ("Gemini 3.8 week", "Gemini 5 h"))
    assert pool is None
    assert at_cap is False

    # usage.json with both pools: pick smaller left
    pools_data = [
        {"name": "Gemini 3.8 week", "used": 0.3, "resets": "2026-10-10T00:00:00Z", "status": "ok"},
        {"name": "Gemini 5 h", "used": 0.8, "resets": "2026-10-07T20:00:00Z", "status": "ok"},
    ]
    pool, at_cap = _lane_pool(pools_data, ("Gemini 3.8 week", "Gemini 5 h"))
    assert pool == {"name": "Gemini 5 h", "left": 0.2, "resets": "2026-10-07T20:00:00Z"}
    assert at_cap is False

    # at_cap from left <= 0 (used >= 1.0)
    pools_data_cap = [
        {"name": "Gemini 3.8 week", "used": 1.0, "resets": "2026-10-10T00:00:00Z", "status": "ok"},
        {"name": "Gemini 5 h", "used": 0.5, "resets": "2026-10-07T20:00:00Z", "status": "ok"},
    ]
    pool, at_cap = _lane_pool(pools_data_cap, ("Gemini 3.8 week", "Gemini 5 h"))
    assert pool == {"name": "Gemini 3.8 week", "left": 0.0, "resets": "2026-10-10T00:00:00Z"}
    assert at_cap is True

    # at_cap from status == "out" even when left > 0
    pools_data_out = [
        {"name": "GLM week", "used": 0.4, "resets": "2026-10-08T00:00:00Z", "status": "out"},
        {"name": "GLM 5 h", "used": 0.2, "resets": "2026-10-07T20:00:00Z", "status": "ok"},
    ]
    pool, at_cap = _lane_pool(pools_data_out, ("GLM week", "GLM 5 h"))
    assert pool == {"name": "GLM week", "left": 0.6, "resets": "2026-10-08T00:00:00Z"}
    assert at_cap is True

    # week pool left 0.8 with status "out", 5 h pool left 0.3 "ok" → at_cap True
    pools_data_week_out = [
        {"name": "Gemini 3.8 week", "used": 0.2, "resets": "2026-10-10T00:00:00Z", "status": "out"},
        {"name": "Gemini 5 h", "used": 0.7, "resets": "2026-10-07T20:00:00Z", "status": "ok"},
    ]
    pool, at_cap = _lane_pool(pools_data_week_out, ("Gemini 3.8 week", "Gemini 5 h"))
    assert pool == {"name": "Gemini 5 h", "left": 0.3, "resets": "2026-10-07T20:00:00Z"}
    assert at_cap is True

    # Verify through _lanes reading actual file
    usage_file = tmp_path / ".heliopause" / "inbox" / "lab" / "strip" / "usage.json"
    usage_file.parent.mkdir(parents=True, exist_ok=True)
    usage_file.write_text(json.dumps({"pools": pools_data}), encoding="utf-8")
    lanes_data = _lanes(tmp_path)
    assert lanes_data["factory-gemini"]["pool"] == {"name": "Gemini 5 h", "left": 0.2, "resets": "2026-10-07T20:00:00Z"}
    assert lanes_data["factory-gemini"]["at_cap"] is False


def test_gemini_busy(tmp_path, monkeypatch):
    # gemini busy parses pgrep output (monkeypatch subprocess.run) and returns 0 on FileNotFoundError.
    monkeypatch.setattr("atlas.collect._busy", _ORIG_BUSY)
    class DummyRes:
        def __init__(self, stdout, returncode=0):
            self.stdout = stdout
            self.returncode = returncode

    # Multiple pids, digits only
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, capture_output, text, timeout: DummyRes("1234\n5678\n9012\n"),
    )
    assert _ORIG_BUSY("gemini", tmp_path) == 3

    # Mixed output: blank lines and non-digit strings skipped
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, capture_output, text, timeout: DummyRes("1234\n\nnot_digits\n5678\n"),
    )
    assert _ORIG_BUSY("gemini", tmp_path) == 2

    # Non-zero returncode
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, capture_output, text, timeout: DummyRes("", returncode=1),
    )
    assert _ORIG_BUSY("gemini", tmp_path) == 0

    # FileNotFoundError
    def raise_fnf(*args, **kwargs):
        raise FileNotFoundError("pgrep not found")

    monkeypatch.setattr(subprocess, "run", raise_fnf)
    assert _ORIG_BUSY("gemini", tmp_path) == 0

    # TimeoutExpired
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["pgrep"], timeout=2)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    assert _ORIG_BUSY("gemini", tmp_path) == 0


def test_graph_lane_states_and_headlines(tmp_path):
    # graph: zcode → state "paused", word "Paused", headline starts "Paused:";
    # gemini at cap → "paused"/"At cap";
    # gemini busy → "working" with badge "1 of 3";
    # neither paused node counts as blocked

    state = {
        "nodes": {
            node_id: {
                "id": node_id,
                "status": "idle",
                "ts": None,
                "headline": None,
                "details": None,
                "name": None,
            }
            for node_id in NODE_IDS
        },
        "decisions": [],
        "bus": {
            "queue": {"inbox": 0, "running": 0, "done": 0, "failed": 0},
            "hq": {"inbox": 0, "open": 0, "done": 0},
            "grok": {},
            "lanes": {
                "factory-zcode": {
                    "busy": 0,
                    "slots": 5,
                    "jobs_today": 0,
                    "pool": {"name": "GLM week", "left": 0.42, "resets": "2026-10-08T20:29:00-04:00"},
                    "paused": "the CLI billed the Coding Plan, not the free plan",
                    "at_cap": False,
                },
                "factory-gemini": {
                    "busy": 1,
                    "slots": 3,
                    "jobs_today": 1,
                    "pool": {"name": "Gemini 3.8 week", "left": 0.85, "resets": "2026-10-08T20:29:00-04:00"},
                    "paused": None,
                    "at_cap": False,
                },
            },
        },
    }

    g = build(state, tmp_path, NOW)

    # zcode checks
    z_node = g["nodes"]["factory-zcode"]
    assert z_node["state"] == "paused"
    assert z_node["word"] == "Paused"
    assert z_node["headline"].startswith("Paused: the CLI billed the Coding Plan, not the free plan · ")
    assert "0 of 5 busy" in z_node["headline"]
    assert "0 jobs today" in z_node["headline"]
    assert "GLM week 42% left, resets Thu 20:29" in z_node["headline"]
    assert z_node["badge"] is None

    # gemini busy checks
    gem_node = g["nodes"]["factory-gemini"]
    assert gem_node["state"] == "working"
    assert gem_node["word"] == "Working"
    assert gem_node["badge"] == {"text": "1 of 3", "tone": "ink"}
    assert "1 of 3 busy" in gem_node["headline"]
    assert "1 job today" in gem_node["headline"]  # singular
    assert "Gemini 3.8 week 85% left, resets Thu 20:29" in gem_node["headline"]

    # gemini at cap checks
    state_cap = dict(state)
    state_cap["bus"] = dict(state["bus"])
    state_cap["bus"]["lanes"] = dict(state["bus"]["lanes"])
    state_cap["bus"]["lanes"]["factory-gemini"] = {
        "busy": 0,
        "slots": 3,
        "jobs_today": 0,
        "pool": {"name": "Gemini 3.8 week", "left": 0.0, "resets": "2026-10-08T20:29:00-04:00"},
        "paused": None,
        "at_cap": True,
    }
    g_cap = build(state_cap, tmp_path, NOW)
    gem_cap = g_cap["nodes"]["factory-gemini"]
    assert gem_cap["state"] == "at_cap"
    assert gem_cap["word"] == "At cap"
    assert gem_cap["badge"] is None
    assert gem_cap["headline"] == "0 of 3 busy · 0 jobs today · Gemini 3.8 week 0% left, resets Thu 20:29"


def test_paused_not_blocked_in_css_and_js():
    # Assert glyph(), tone(), RING_WIDTH, and CSS styling for paused and at_cap
    assert 'if (state === "quiet" || state === "paused") return "\\u25cb";' in JS
    assert 'if (state === "blocked" || state === "at_cap") return "\\u25c7";' in JS
    assert 'if (state === "blocked" || state === "at_cap") return "amber";' in JS
    assert "paused: 1.5" in JS
    assert "at_cap: 2" in JS
    assert ":is(svg .node, .orb-cell).at_cap circle.ring { stroke: var(--amber); }" in CSS
    assert "svg .node.at_cap .glyph { fill: var(--amber); }" in CSS
    assert ".focus-box.at_cap .fv-glyph, .focus-partner.at_cap .fv-glyph { color: var(--amber); }" in CSS
    assert ":is(svg .node, .orb-cell).paused circle.ring { stroke: var(--ink-3); stroke-opacity: 0.7; stroke-dasharray: 3 3; }" in CSS
    assert "svg .node.paused .glyph { fill: var(--ink-3); }" in CSS
    assert ".focus-box.paused .fv-glyph, .focus-partner.paused .fv-glyph { color: var(--ink-3); }" in CSS


def test_paused_nodes_under_node_not_blocked():
    glyph_fn = JS[JS.index("function glyph"):JS.index("function tone")]
    assert 'if (state === "quiet" || state === "paused") return "\\u25cb";' in glyph_fn
    assert 'if (state === "blocked" || state === "at_cap") return "\\u25c7";' in glyph_fn
    tone_fn = JS[JS.index("function tone"):JS.index("function waiting")]
    assert 'if (state === "blocked" || state === "at_cap") return "amber";' in tone_fn
    assert "paused" not in tone_fn
    # and the blockedNodes() function body checks only state === "blocked"
    blocked_fn = JS[JS.index("function blockedNodes"):JS.index("function paintStatus")]
    assert 'node.state === "blocked"' in blocked_fn
    assert "paused" not in blocked_fn
    assert "at_cap" not in blocked_fn


def test_lanes_json_drives_paused(tmp_path):
    # zcode "paused" with a note -> node state "paused", word "Paused", headline starts "Paused: <note>"
    lanes_file = tmp_path / ".heliopause" / "inbox" / "lab" / "usage" / "lanes.json"
    lanes_file.parent.mkdir(parents=True, exist_ok=True)
    note = "CLI billed the Z.AI Coding Plan, not the free plan"
    lanes_file.write_text(
        json.dumps({
            "updated": "2026-10-07T12:00:00Z",
            "lanes": {
                "zcode": {"state": "paused", "note": note},
                "gemini": {"state": "live"},
            },
        }),
        encoding="utf-8",
    )
    snap = snapshot(tmp_path)
    g = build(snap, tmp_path, NOW)
    z_node = g["nodes"]["factory-zcode"]
    assert z_node["state"] == "paused"
    assert z_node["word"] == "Paused"
    assert z_node["headline"].startswith(f"Paused: {note}")

    # missing lanes.json -> zcode is not paused (state "idle")
    lanes_file.unlink()
    snap_missing = snapshot(tmp_path)
    g_missing = build(snap_missing, tmp_path, NOW)
    z_missing = g_missing["nodes"]["factory-zcode"]
    assert z_missing["state"] == "idle"
    assert z_missing["word"] == "Idle"
    assert not z_missing["headline"].startswith("Paused:")

    # zcode "paused" without a note -> note defaults to "paused"
    lanes_file.write_text(
        json.dumps({
            "lanes": {
                "zcode": {"state": "paused"},
            },
        }),
        encoding="utf-8",
    )
    snap_no_note = snapshot(tmp_path)
    g_no_note = build(snap_no_note, tmp_path, NOW)
    z_no_note = g_no_note["nodes"]["factory-zcode"]
    assert z_no_note["state"] == "paused"
    assert z_no_note["word"] == "Paused"
    assert z_no_note["headline"].startswith("Paused: paused")


def test_lane_paused():
    assert _lane_paused({}, "zcode") is None
    assert _lane_paused({"zcode": {"state": "live"}}, "zcode") is None
    assert _lane_paused({"zcode": {"state": "paused"}}, "zcode") == "paused"
    assert _lane_paused({"zcode": {"state": "paused", "note": "billing issue"}}, "zcode") == "billing issue"
    assert _lane_paused({"zcode": {"state": "paused", "note": "   "}}, "zcode") == "paused"
    assert _lane_paused({"zcode": {"state": "paused", "note": 123}}, "zcode") == "paused"


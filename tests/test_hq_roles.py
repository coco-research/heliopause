"""HQ lane = roles: persona names resolve to role ids on the server and never leave it."""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from atlas.collect import HQ_ROLES, NODE_IDS, ROLE_IDS, hq_end, hq_for, hq_from, hq_roles, hq_visible, lab_personas, snapshot
from atlas.events import board_event, board_mentions, history
from atlas.graph import build
from atlas.problems import detect

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
MAP = {"persona-a": "hq-cto", "persona-b": "hq-cfo", "persona-c": "hq-cos"}
STATIC = Path(__file__).resolve().parents[1] / "atlas" / "static"
JS = (STATIC / "graph.js").read_text(encoding="utf-8")
ORDER = ["hq-cos", "hq-ceo", "hq-cpo", "hq-cto", "hq-cfo", "hq-coo", "hq-cmo", "hq-cso", "hq-cco", "hq-gc"]


def _roles(root, data):
    path = root / ".heliopause" / "inbox" / "lab" / "hq-roles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def _personas(root, data):
    path = root / ".heliopause" / "inbox" / "lab" / "lab-personas.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def _private(ask_id="ask-1", ts=None):
    label = "private HQ ask" if ask_id is None else "private HQ ask \u00b7 " + ask_id
    return {"kind": "hq", "private": True, "id": ask_id, "from": None, "to": None, "for": [],
            "type": None, "priority": None, "title": label, "created": None, "ts": ts}


def _hq_items(state):
    return [item for item in state["decisions"] if item.get("kind") == "hq"]


def _off(root, ts=None, ask_id="ask-1"):
    state, graph = _graph(root)
    assert _hq_items(state) == [_private(ask_id, ts)]
    assert graph["fibres"] == []
    assert [e for e in history(root, NOW) if e["kind"] in ("ask", "answer")] == []


def _file(root, folder, name, text, age_h=1):
    path = root / ".heliopause" / "inbox" / "hq" / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    when = (NOW - timedelta(hours=age_h)).timestamp()
    os.utime(path, (when, when))


def _ask(root, name, target, title="Please look", age_h=1):
    _file(root, "inbox", name, f"---\nid: {name[:-3]}\nfrom: vp\nfor: {target}\ncreated: 2026-10-02\n---\n# {title}\n", age_h)


def _pairs(root):
    return [(e["kind"], e["from"], e["to"]) for e in history(root, NOW) if e["kind"] in ("ask", "answer")]


def test_role_ids_in_spec_order():
    assert [role_id for role_id, _label in HQ_ROLES] == ORDER
    assert list(ROLE_IDS) == ORDER


@pytest.mark.parametrize(
    "value,found",
    [
        ("persona-a", ["hq-cto"]),
        ("Persona-A", ["hq-cto"]),
        ("persona-a, persona-b", ["hq-cto", "hq-cfo"]),
        ("persona-b (money), with persona-a (tech) and persona-b", ["hq-cfo", "hq-cto"]),
        ("someone else", ["hq-cos"]),
        ("", ["hq-cos"]),
        (None, ["hq-cos"]),
    ],
)
def test_hq_for(value, found):
    assert hq_for(value, MAP) == found


def test_hq_for_without_map_is_generic():
    assert hq_for("", {}) == ["hq"]
    assert hq_for("persona-a", {}) == ["hq"]


@pytest.mark.parametrize(
    "value,found",
    [("hq/persona-a", "hq-cto"), ("persona-b", "hq-cfo"), ("HQ/Persona-B", "hq-cfo"), ("hq", "hq"), ("hq/nobody", "hq"), (None, "hq")],
)
def test_hq_from(value, found):
    assert hq_from(value, MAP) == found


@pytest.mark.parametrize(
    "data",
    [None, "not json", "[]", '"x"', {"persona-a": "hq-nope"}, {"persona-a": "hq-cto", "persona-b": "owner"}, {"persona-a": ["hq-cto"]}],
)
def test_bad_or_missing_map_means_generic(tmp_path, data):
    if data is not None:
        _roles(tmp_path, data)
    assert hq_roles(tmp_path) == {}
    _ask(tmp_path, "ask-1.md", "persona-a")
    assert snapshot(tmp_path)["decisions"][0]["for"] == ["hq"]
    assert _pairs(tmp_path) == [("ask", "vp", "hq")]


def test_good_map_is_lowercased(tmp_path):
    _roles(tmp_path, {"Persona-A": "hq-cto"})
    assert hq_roles(tmp_path) == {"persona-a": "hq-cto"}


def test_events_resolve_asks_and_answers(tmp_path):
    _roles(tmp_path, MAP)
    _ask(tmp_path, "ask-1.md", "persona-a")
    _ask(tmp_path, "ask-2.md", "persona-a, persona-b")
    _file(tmp_path, "done", "ask-1.md", "---\nfrom: hq/persona-a\nto: vp\n---\n# ok\n")
    _file(tmp_path, "done", "ask-2.md", "---\nfrom: persona-b\nto: vp\n---\n# ok\n")
    _file(tmp_path, "done", "ask-3.md", "---\nfrom: hq\nto: vp\n---\n# ok\n")
    _file(tmp_path, "done", "ask-4.md", "# no frontmatter\n")
    events = history(tmp_path, NOW)
    ids = {e["id"]: (e["from"], e["to"]) for e in events if e["kind"] in ("ask", "answer")}
    assert ids["hq-inbox:ask-1.md"] == ("vp", "hq-cto")
    assert ids["hq-inbox:ask-2.md:hq-cto"] == ("vp", "hq-cto")
    assert ids["hq-inbox:ask-2.md:hq-cfo"] == ("vp", "hq-cfo")
    assert ids["hq-done:ask-1.md"] == ("hq-cto", "vp")
    assert ids["hq-done:ask-2.md"] == ("hq-cfo", "vp")
    assert ids["hq-done:ask-3.md"] == ("hq", "vp")
    assert "hq-done:ask-4.md" not in ids


def _graph(root):
    state = snapshot(root)
    return state, build(state, root, NOW)


def test_graph_lane_active_badges_and_fibres(tmp_path):
    _roles(tmp_path, MAP)
    _ask(tmp_path, "ask-1.md", "persona-a")
    _ask(tmp_path, "ask-2.md", "persona-a, persona-b", age_h=2)
    _ask(tmp_path, "ask-3.md", "persona-c", age_h=30)
    _ask(tmp_path, "ask-4.md", "nobody known", age_h=30)
    state, graph = _graph(tmp_path)
    lane = next(item for item in graph["lanes"] if item["id"] == "hq")
    assert lane["nodes"] == ORDER + ["hq"]
    active = {node_id for node_id, node in graph["nodes"].items() if node["active_24h"]}
    assert active == {"hq-cto", "hq-cfo"}
    assert graph["nodes"]["hq-cto"]["badge"] == {"text": "2 asks", "tone": "ink"}
    assert graph["nodes"]["hq-cfo"]["badge"] == {"text": "1 ask", "tone": "ink"}
    # ask-3 names the Chief of Staff; ask-4 names nobody in the map, so it lands on the queue too.
    assert graph["nodes"]["hq-cos"]["badge"] == {"text": "2 asks", "tone": "ink"}
    assert graph["nodes"]["hq"]["badge"] is None
    pairs = {(f["from"], f["to"]) for f in graph["fibres"]}
    assert ("vp", "hq-cto") in pairs and ("vp", "hq-cfo") in pairs
    assert graph["nodes"]["hq"]["active_24h"] is False


def test_generic_box_active_only_with_unknown_sender(tmp_path):
    _roles(tmp_path, MAP)
    _file(tmp_path, "done", "ask-1.md", "---\nfrom: hq\nto: vp\n---\n# ok\n")
    _state, graph = _graph(tmp_path)
    assert graph["nodes"]["hq"]["active_24h"] is True
    assert graph["nodes"]["hq-cos"]["active_24h"] is False


def test_non_hq_nodes_are_never_active_24h(tmp_path):
    _ask(tmp_path, "ask-1.md", "x")
    _state, graph = _graph(tmp_path)
    assert graph["nodes"]["vp"]["active_24h"] is False and graph["nodes"]["owner"]["active_24h"] is False


def test_no_persona_names_in_output(tmp_path):
    _roles(tmp_path, MAP)
    _ask(tmp_path, "ask-1.md", "persona-a (tech), persona-b", title="Ask persona-a and Persona-B about it")
    _file(tmp_path, "done", "ask-1.md", "---\nfrom: hq/persona-a\nto: vp\n---\n# persona-a replied\n")
    _file(tmp_path, "inbox", "ask-2.md", "---\nid: ask-2\nfrom: hq/persona-c\nto: hq/persona-b\nfor: persona-c\n---\n# Hi\n")
    state, graph = _graph(tmp_path)
    state["graph"] = graph
    texts = [json.dumps(state), json.dumps(history(tmp_path, NOW))]
    for text in texts:
        assert "persona" not in text.lower()
    assert "Ask CTO and CFO about it" in texts[0]
    ask = next(item for item in state["decisions"] if item["id"] == "ask-2")
    assert (ask["from"], ask["to"], ask["for"]) == ("hq-cos", "hq-cfo", ["hq-cos"])


def test_page_js_hq_lane():
    assert "COO" not in JS
    assert "const HQ_COMPACT_MAX = 8;" in JS  # every role is an orb; past this many they go compact
    assert "innerHTML" not in JS


SEP = " \u00b7 "
LINE = SEP.join(("2026-10-02 07:00 ET", "Design Dept", "needs-hq", "Need a budget call", "please, HQ and VP"))


def _board(root, line=LINE):
    path = root / ".heliopause" / "inbox" / "lab" / "board.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line + "\n", encoding="utf-8")


def test_queue_is_cos_with_a_map_and_generic_without():
    assert board_event(LINE, "b")["to"] == "hq"
    assert board_event(LINE, "b", "hq-cos")["to"] == "hq-cos"
    assert [e["to"] for e in board_mentions(LINE, "b")] == ["vp"]
    assert [e["to"] for e in board_mentions(LINE, "b", "hq-cos")] == ["vp"]
    other = SEP.join(("2026-10-02 07:00 ET", "Design Dept", "working", "Told HQ about it", "HQ knows"))
    assert [e["to"] for e in board_mentions(other, "b")] == ["hq"]
    assert [e["to"] for e in board_mentions(other, "b", "hq-cos")] == ["hq-cos"]


def test_board_needs_hq_and_mentions_follow_the_map(tmp_path):
    _board(tmp_path)
    assert snapshot(tmp_path)["hq_queue"] == "hq"
    assert {e["to"] for e in history(tmp_path, NOW, 24 * 365) if e["from"] == "dept-design"} == {"hq", "vp"}
    _roles(tmp_path, MAP)
    state, graph = _graph(tmp_path)
    assert state["hq_queue"] == "hq-cos"
    assert {e["to"] for e in history(tmp_path, NOW, 24 * 365) if e["from"] == "dept-design"} == {"hq-cos", "vp"}
    # The badge counts hq ask items only, like the panel. A board post is not an ask.
    assert graph["nodes"]["hq-cos"]["badge"] is None and graph["nodes"]["hq"]["badge"] is None


def test_board_needs_hq_is_a_problem_owned_by_the_queue(tmp_path):
    _board(tmp_path)
    later = NOW + timedelta(hours=5)

    def owners():
        state = snapshot(tmp_path)
        found = detect(state, build(state, tmp_path, later), tmp_path, later)
        return [item["owner"] for item in found if item["kind"] == "decision-waiting"]

    assert owners() == ["hq"]
    _roles(tmp_path, MAP)
    assert owners() == ["hq-cos"]


def test_hq_kinds_are_the_lane_kind(tmp_path):
    _state, graph = _graph(tmp_path)
    assert {graph["nodes"][node_id]["kind"] for node_id in [*ORDER, "hq"]} == {"HQ"}


def test_asks_with_no_match_go_to_cos_only_with_a_map(tmp_path):
    _ask(tmp_path, "ask-1.md", "nobody known")
    _file(tmp_path, "inbox", "ask-2.md", "---\nid: ask-2\nfrom: vp\n---\n# No for\n")
    assert _pairs(tmp_path) == [("ask", "vp", "hq"), ("ask", "vp", "hq")]
    _roles(tmp_path, MAP)
    assert _pairs(tmp_path) == [("ask", "vp", "hq-cos"), ("ask", "vp", "hq-cos")]
    assert [item["for"] for item in snapshot(tmp_path)["decisions"]] == [["hq-cos"], ["hq-cos"]]


def test_unknown_done_sender_stays_off_the_graph(tmp_path):
    _roles(tmp_path, MAP)
    _file(tmp_path, "done", "ask-1.md", "---\nfrom: stranger\nto: vp\n---\n# ok\n")
    assert _pairs(tmp_path) == []
    assert snapshot(tmp_path)["decisions"] == []


@pytest.mark.parametrize("sender", ["persona-b", "Persona-B", "hq/persona-b", "HQ/persona-b"])
def test_bare_persona_sender_is_a_role_everywhere(tmp_path, sender):
    _roles(tmp_path, MAP)
    _file(tmp_path, "inbox", "ask-1.md", f"---\nid: ask-1\nfrom: {sender}\nto: persona-a\nfor: persona-c\n---\n# Hi\n")
    state, graph = _graph(tmp_path)
    state["graph"] = graph
    ask = state["decisions"][0]
    assert (ask["from"], ask["to"]) == ("hq-cfo", "hq-cto")
    assert "persona" not in json.dumps(state).lower()
    # the event agrees with the snapshot
    event = next(e for e in history(tmp_path, NOW) if e["kind"] == "ask")
    assert event["from"] == ask["from"]


def test_lab_senders_are_node_ids(tmp_path):
    _roles(tmp_path, MAP)
    _file(tmp_path, "inbox", "ask-1.md", "---\nid: ask-1\nfrom: lab/agent-1\nto: vp\nfor: persona-a\n---\n# Hi\n")
    _file(tmp_path, "inbox", "ask-2.md", "---\nid: ask-2\nfrom: agent-vp (Example Lab)\nto: vp\nfor: persona-a\n---\n# Hi\n")
    state = snapshot(tmp_path)
    blob = json.dumps(state)
    assert "lab/agent-1" not in blob and "agent-vp" not in blob and "Example Lab" not in blob
    assert [(a["from"], a["to"]) for a in state["decisions"]] == [("vp", "vp"), ("vp", "vp")]
    assert all(end in NODE_IDS for row in state["decisions"] for end in (row["from"], row["to"]))
    assert [e["from"] for e in history(tmp_path, NOW) if e["kind"] == "ask"] == ["vp", "vp"]


def test_event_sender_without_a_map_is_off_graph(tmp_path):
    _file(tmp_path, "inbox", "ask-1.md", "---\nid: ask-1\nfrom: persona-b\nfor: x\n---\n# Hi\n")
    assert [e for e in history(tmp_path, NOW) if e["kind"] == "ask"] == []


@pytest.mark.parametrize("key", ["", "   ", "\t"])
def test_empty_key_voids_the_map(tmp_path, key):
    _roles(tmp_path, {"persona-a": "hq-cto", key: "hq-cfo"})
    assert hq_roles(tmp_path) == {}


@pytest.mark.parametrize("to", ["hq/persona-b", "persona-b", "HQ/Persona-B"])
def test_answer_recipient_is_a_role_everywhere(tmp_path, to):
    _roles(tmp_path, MAP)
    _file(tmp_path, "done", "ask-1.md", f"---\nfrom: hq/persona-a\nto: {to}\n---\n# ok\n")
    _file(tmp_path, "inbox", "ask-2.md", f"---\nid: ask-2\nfrom: vp\nto: {to}\nfor: persona-a\n---\n# Hi\n")
    answer = next(e for e in history(tmp_path, NOW) if e["kind"] == "answer")
    assert (answer["from"], answer["to"]) == ("hq-cto", "hq-cfo")
    assert answer["to"] == snapshot(tmp_path)["decisions"][0]["to"]


def test_answer_recipient_without_a_map_is_off_graph(tmp_path):
    _file(tmp_path, "done", "ask-1.md", "---\nfrom: hq\nto: persona-b\n---\n# ok\n")
    assert [e for e in history(tmp_path, NOW) if e["kind"] == "answer"] == []


def test_no_hr_id_in_atlas_source():
    root = Path(__file__).resolve().parents[1] / "atlas"
    paths = [*root.glob("*.py"), *root.glob("*.json"), *(root / "static").rglob("*")]
    # One line may name it: the remap that reads an old map file's hq-hr as the COO.
    hits = [
        path.name for path in paths if path.is_file()
        for line in path.read_bytes().splitlines() if b"hq-hr" in line and b"HR sits under COO" not in line
    ]
    assert hits == []


def test_hr_map_value_is_coo_and_the_rest_still_works(tmp_path):
    _roles(tmp_path, {"bravo": "hq-hr", "alpha": "hq-cto"})
    roles = hq_roles(tmp_path)
    assert roles == {"bravo": "hq-coo", "alpha": "hq-cto"}
    assert hq_end("bravo", roles) == "hq-coo"
    assert hq_end("hq/alpha", roles) == "hq-cto"


def test_role_keys_resolve_without_a_map_file(tmp_path):
    assert hq_roles(tmp_path) == {}
    roles = hq_roles(tmp_path)
    assert hq_end("hq/cto", roles) == "hq-cto"
    assert hq_end("hq/general-counsel", roles) == "hq-gc"
    assert hq_end("hq/hr", roles) == "hq-coo"
    _roles(tmp_path, {"alpha": "hq-cpo"})
    assert hq_end("hq/alpha", hq_roles(tmp_path)) == "hq-cpo"


_HIDDEN = [
    "---\nid: ask-1\nprivate: true\nfrom: vp\nto: hq/cto\n---\n# Merge PR 12\n",
    "---\nid: ask-1\nvisibility: owner\nfrom: vp\nto: hq/cto\n---\n# Merge PR 12\n",
    "---\nid: ask-1\nfrom: vp\nto: hq/cto\n---\n# Rotate credentials\n",
    "---\nid: ask-1\nfrom: vp\nto: hq/cto\n---\n# Renew passport\n",
    "---\nid: ask-1\nfrom: health-bot\nto: hq/cto\n---\n# Merge PR 12\n",
    "---\nid: ask-1\nfrom: vp\nto: legal\n---\n# Merge PR 12\n",
    "---\nid: ask-1\nfrom: hq/cto\nto: owner\n---\n# Merge PR 12\n",
    "---\nid: ask-1\nfrom: someone-new\nto: hq/cto\n---\n# Merge PR 12\n",
]


@pytest.mark.parametrize("body", _HIDDEN)
def test_off_graph_file_emits_nothing(tmp_path, body):
    _file(tmp_path, "inbox", "ask-1.md", body)
    _off(tmp_path)


def test_normal_file_and_cos_to_owner_appear(tmp_path):
    _file(
        tmp_path, "inbox", "ask-1.md",
        "---\nid: ask-1\nfrom: agent-vp (Example Lab)\nto: hq/cto\ncreated: 2026-10-02\n---\n# Merge PR 12\n",
    )
    state, graph = _graph(tmp_path)
    item = next(row for row in state["decisions"] if row.get("kind") == "hq")
    assert (item["from"], item["to"]) == ("vp", "hq-cto") and item["title"] == "Merge PR 12"
    assert "private" not in item
    assert graph["fibres"]
    assert any(e["kind"] == "ask" for e in history(tmp_path, NOW))

    root = tmp_path / "cos"
    _file(root, "inbox", "ask-1.md", "---\nid: ask-1\nfrom: hq/cos\nto: owner\ncreated: 2026-10-02\n---\n# Company call\n")
    state, graph = _graph(root)
    item = next(row for row in state["decisions"] if row.get("kind") == "hq")
    assert (item["from"], item["to"]) == ("hq-cos", "owner")
    assert graph["fibres"]
    assert any(e["kind"] == "ask" for e in history(root, NOW))


def test_unknown_for_hides_a_owner_thread(tmp_path):
    _file(tmp_path, "inbox", "ask-1.md",
          "---\nid: ask-1\nfrom: hq/cos\nto: owner\nfor: nobody\ncreated: 2026-10-02\n---\n# Company call\n")
    _off(tmp_path, ts="2026-10-02T04:00:00Z")


def test_for_third_role_hides_a_owner_thread(tmp_path):
    _file(tmp_path, "inbox", "ask-1.md",
          "---\nid: ask-1\nfrom: hq/cos\nto: owner\nfor: cto\ncreated: 2026-10-02\n---\n# Company call\n")
    _off(tmp_path, ts="2026-10-02T04:00:00Z")
    blob = json.dumps(snapshot(tmp_path)) + json.dumps(history(tmp_path, NOW))
    assert "Company call" not in blob
    assert not any(f["from"] == "hq-cos" and f["to"] == "hq-cto" for f in _graph(tmp_path)[1]["fibres"])


def test_owner_thread_without_for_stays_visible(tmp_path):
    _file(tmp_path, "inbox", "ask-1.md",
          "---\nid: ask-1\nfrom: hq/cos\nto: owner\ncreated: 2026-10-02\n---\n# Company call\n")
    item = next(row for row in snapshot(tmp_path)["decisions"] if row.get("kind") == "hq")
    assert (item["from"], item["to"], item["title"]) == ("hq-cos", "owner", "Company call")
    assert "private" not in item


def test_for_ceo_on_a_owner_thread_stays_visible(tmp_path):
    _file(tmp_path, "inbox", "ask-1.md",
          "---\nid: ask-1\nfrom: hq/cos\nto: owner\nfor: ceo\ncreated: 2026-10-02\n---\n# Company call\n")
    item = next(row for row in snapshot(tmp_path)["decisions"] if row.get("kind") == "hq")
    assert (item["from"], item["to"], item["for"], item["title"]) == ("hq-cos", "owner", ["hq-ceo"], "Company call")
    assert "private" not in item


@pytest.mark.parametrize("sender", [
    "lab/agent-1",
    "lab",
    "agent-vp (Example Lab)",
    "lab-ops (for agent-vp, Example Lab)",
])
def test_real_lab_addresses_stay_on_the_graph(tmp_path, sender):
    # The shapes HQ files really use. The filter must not hide ordinary Lab <-> HQ traffic.
    _file(tmp_path, "inbox", "ask-1.md", f"---\nid: ask-1\nfrom: {sender}\nto: hq/cto\ncreated: 2026-10-02\n---\n# Merge PR 12\n")
    _file(tmp_path, "done", "ask-2.md", f"---\nfrom: hq/cto\nto: {sender}\n---\n# Merged it\n")
    state, _graph_ = _graph(tmp_path)
    item = next(row for row in state["decisions"] if row.get("kind") == "hq")
    assert item["id"] == "ask-1"
    assert (item["from"], item["to"]) == ("vp", "hq-cto")
    blob = json.dumps(state) + json.dumps(history(tmp_path, NOW))
    assert "agent-1" not in blob and "agent-vp" not in blob and "Example Lab" not in blob
    kinds = {e["kind"] for e in history(tmp_path, NOW)}
    assert {"ask", "answer"} <= kinds


def test_bare_persona_with_lab_map_is_vp(tmp_path):
    _personas(tmp_path, {"Alpha": "vp"})
    _file(tmp_path, "inbox", "ask-1.md", "---\nid: ask-1\nfrom: alpha\nto: hq/cto\ncreated: 2026-10-02\n---\n# Merge PR 12\n")
    state, graph = _graph(tmp_path)
    item = next(row for row in state["decisions"] if row.get("kind") == "hq")
    assert item["from"] == "vp" and item["to"] == "hq-cto"
    assert graph["fibres"]
    assert any(e["kind"] == "ask" for e in history(tmp_path, NOW))


_FAIL_CLOSED = [
    "---\nid: ask-1\nfrom: vp\nto: hq/cto\n---\n# Merge PR 12\n\nThe api key stays in the note.\n",
    "---\nid: ask-1\nfrom: vp\nto: hq/cto\n# Merge PR 12\n",
    "---\nid: ask-1\ncreated: 2026-10-02\n---\n# Merge PR 12\n",
    "---\nid: ask-1\nfrom: alpha\nto: hq/cto\ncreated: 2026-10-02\n---\n# Merge PR 12\n",
]


@pytest.mark.parametrize("body", _FAIL_CLOSED)
def test_fail_closed_emits_nothing(tmp_path, body):
    _file(tmp_path, "inbox", "ask-1.md", body)
    _off(tmp_path, ts="2026-10-02T04:00:00Z" if "\ncreated:" in body else None)


def test_hq_role_in_lab_map_turns_the_map_off(tmp_path):
    assert lab_personas(tmp_path) == {}
    _personas(tmp_path, "not json")
    assert lab_personas(tmp_path) == {}
    _personas(tmp_path, {"alpha": "nope"})
    assert lab_personas(tmp_path) == {}
    _personas(tmp_path, {"alpha": "hq-cto", "beta": "vp"})
    assert lab_personas(tmp_path) == {}
    for value in ("hq", "owner"):
        _personas(tmp_path, {"alpha": value})
        assert lab_personas(tmp_path) == {}
    _file(tmp_path, "inbox", "ask-1.md", "---\nid: ask-1\nfrom: alpha\nto: hq/cto\ncreated: 2026-10-02\n---\n# Merge PR 12\n")
    _off(tmp_path, ts="2026-10-02T04:00:00Z")


def test_hq_visible_fails_closed_when_lookup_raises(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("lookup")

    monkeypatch.setattr("atlas.collect._role_id", boom)
    assert hq_visible({"from": "vp", "to": "hq/cto"}, "Merge PR 12", {}) is False
    _file(tmp_path, "inbox", "ask-1.md", "---\nid: ask-1\nfrom: vp\nto: hq/cto\ncreated: 2026-10-02\n---\n# Merge PR 12\n")
    _off(tmp_path, ts="2026-10-02T04:00:00Z")


def test_hq_filter_source_has_no_personal_names():
    # Names to keep out come from the environment, never this file (see test_page_ships_no_private_words).
    # CI has none, so it SKIPS; the Atlas Brain runs it locally with ATLAS_PRIVATE_WORDS set before merge.
    words = [w.strip().lower() for w in os.environ.get("ATLAS_PRIVATE_WORDS", "").split(",") if w.strip()]
    if not words:
        pytest.skip("ATLAS_PRIVATE_WORDS is not set")
    banned = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(w) for w in words))
    root = Path(__file__).resolve().parents[1] / "atlas"
    for name in ("collect.py", "events.py"):
        assert banned.search((root / name).read_text(encoding="utf-8").lower()) is None


def test_directory_sales_customer_and_hr(tmp_path):
    from atlas.brief import who

    _state, graph = _graph(tmp_path)
    assert who(graph, "sales")[0]["node"] == "hq-cso"
    assert who(graph, "customer")[0]["node"] == "hq-cco"
    assert who(graph, "hr")[0]["node"] == "hq-coo"


def test_watchers_skip_lab_inbox():
    from atlas.events import _APPEAR

    assert all(parts != ("lab", "inbox") for _label, parts, _suffix, _kind in _APPEAR)
    root = Path(__file__).resolve().parents[1] / "atlas"
    for name in ("events.py", "collect.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert '("lab", "inbox")' not in text and "lab/inbox" not in text


@pytest.mark.parametrize("value,expected", [
    ("claude/agent-1", "vp"),
    ("hq/cto, hq/cpo", "hq-cto"),
    ("hq/cto, someone-new", None),  # one unknown part hides the whole list: fail closed
    ("hq/cto, for the owner", None),
])
def test_bus_inbox_and_address_lists(value, expected):
    from atlas.collect import hq_party

    assert hq_party(value, {}) == expected


_LEAK = ("Rotate", "zebra", "credentials", "zebra-from", "zebra-to")


def _clean(blob):
    assert not any(word in blob for word in _LEAK)


def test_hidden_ask_is_a_content_free_private_row(tmp_path):
    _file(tmp_path, "inbox", "ask-0099.md",
          "---\nid: ask-0099\nfrom: zebra-from\nto: zebra-to\ncreated: 2026-10-01\n---\n# Rotate the zebra key\ncredentials\n")
    state, graph = _graph(tmp_path)
    assert _hq_items(state) == [_private("ask-0099", "2026-10-01T04:00:00Z")]
    assert graph["fibres"] == [] and [e for e in history(tmp_path, NOW) if e["kind"] in ("ask", "answer")] == []
    _clean(json.dumps(state))
    found = [item for item in detect(state, graph, tmp_path, NOW) if item["kind"] == "hq-unanswered"]
    assert [item["id"] for item in found] == ["hq-unanswered:ask-0099"]
    _clean(json.dumps(found))


@pytest.mark.parametrize("ask_id", ["ask-0099; drop", "LAB-1", "x-1", "lab-" + "1" * 11, "lab-rotate-zebra", "hq-zebra-to", "lab-\u0663"])
def test_unusual_id_is_a_bare_private_row(tmp_path, ask_id):
    _file(tmp_path, "inbox", "ask-0099.md",
          f"---\nid: {ask_id}\nfrom: zebra-from\nto: zebra-to\n---\n# Rotate the zebra key\ncredentials\n")
    state = snapshot(tmp_path)
    assert _hq_items(state) == [_private(None)]
    assert ask_id not in json.dumps(state)
    _clean(json.dumps(state))


def test_closed_superseded_plain_and_open_count(tmp_path):
    hidden = "---\nid: ask-0099\nfrom: zebra-from\nto: zebra-to\n---\n# Rotate the zebra key\ncredentials\n"
    _file(tmp_path, "inbox", "ask-0099.md", hidden)
    _file(tmp_path, "done", "ask-0099.md", "# closed\n")
    assert _hq_items(snapshot(tmp_path)) == []
    other = tmp_path / "s"
    _ask(other, "ask-0001.md", "cto", title="Visible ask")
    _file(other, "inbox", "ask-0099.md", hidden)
    _file(other, "inbox", "ask-0100.md", "---\nid: ask-0100\nfrom: vp\nto: hq\nsupersedes: ask-0099\n---\n# Next\n")
    _file(other, "inbox", "hq-7.md", "no front matter\n# Rotate the zebra key\ncredentials\n")
    state = snapshot(other)
    rows = _hq_items(state)
    # ask-0099 is superseded. Open: the visible ask, the successor, and the plain file.
    assert len(rows) == state["bus"]["hq"]["open"] == 3
    assert [row["id"] for row in rows if row.get("private")] == ["hq-7"]
    shown = next(row for row in rows if row["id"] == "ask-0001")
    assert shown["title"] == "Visible ask" and "private" not in shown
    assert next(row for row in rows if row["id"] == "hq-7") == _private("hq-7")
    _clean(json.dumps(state))


def test_owner_not_first_in_a_party_list_is_private(tmp_path):
    _file(tmp_path, "inbox", "ask-1.md", "---\nid: ask-1\nfrom: vp\nto: hq/cto, owner\n---\n# Merge PR 12\n")
    _off(tmp_path)


def test_marker_in_filename_is_a_content_free_row(tmp_path):
    _file(tmp_path, "inbox", "credentials.md", "---\nid: ask-1\nfrom: vp\nto: hq/cto\ncreated: 2026-10-02\n---\n# Merge PR 12\n")
    state, graph = _graph(tmp_path)
    row = _hq_items(state)[0]
    assert row == _private("ask-1", "2026-10-02T04:00:00Z")
    assert "credentials" not in row["id"] and "credentials" not in row["title"]
    assert graph["fibres"] == [] and "credentials" not in json.dumps(history(tmp_path, NOW))


def test_private_row_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("atlas.collect._created_ts", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("ts")))
    _file(tmp_path, "inbox", "ask-1.md", "---\nid: ask-1\nfrom: vp\nto: hq\ncreated: 2026-10-01\n---\n# credentials\n")
    assert _hq_items(snapshot(tmp_path)) == [_private("ask-1")]  # a bad timestamp keeps the safe id

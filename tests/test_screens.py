"""Screen routes. Fixtures live under tmp_path, never a home directory."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from atlas.roles import ack, new_message, send, set_state, validate
from atlas.screens import approve, mark, messages, now

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
CLOCK = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)
MISSING = "m-" + "f" * 12
_MEMBER = {"name", "tool", "state", "since", "repo", "waiting"}
_NOW = {"generated", "sentence", "counts", "needs_you", "seats", "app_dir", "moving", "github"}
_VIEW = {"id", "from", "to", "kind", "summary", "repo", "state", "reply_to", "ts", "waiting_on_you", "thread", "checks"}


def mid(n: int) -> str:
    return f"m-{n:012x}"


def _msg(n: int, ts: str, **kw: object) -> dict:
    msg: dict = {
        "id": mid(n),
        "from": {"role": kw.get("frm", "lab"), "tool": kw.get("tool", "claude-code")},
        "to": {"role": kw.get("to", "hq")},
        "kind": kw.get("kind", "ask"),
        "summary": kw.get("summary", "hello"),
        "repo": kw["repo"] if "repo" in kw else "api",
        "state": kw.get("state", "sent"),
        "reply_to": kw.get("reply_to"),
        "ts": ts,
    }
    if msg["kind"] == "job":
        msg["checks"] = {"ci": "none", "review": "none", "tests": "none"}
    assert validate(msg) == []
    return msg


def _write(lab: Path, box: str, role: str, msg: dict) -> None:
    folder = lab / ".heliopause" / box / role
    folder.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(msg, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    (folder / f"{msg['id']}.json").write_bytes(raw)


def _snap(root: Path) -> list:
    base = root / ".heliopause"
    rows = []
    if not base.exists():
        return rows
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        for name in list(dirnames) + list(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(base).as_posix()
            if path.is_symlink():
                rows.append((rel, "link", os.readlink(path)))
            elif path.is_file():
                rows.append((rel, "file", path.read_bytes()))
            elif path.is_dir():
                rows.append((rel, "dir"))
    return sorted(rows)


def _roles(lab: Path, hq: str = "you", planner: str | None = None, factory: list | None = None, **extra: object) -> None:
    doc = {"roles": {"hq": hq, "lab": planner, "factory": list(factory or [])}, **extra}
    (lab / "atlas.json").write_text(json.dumps(doc), encoding="utf-8")


def _card(lab: Path, tool: str, session: str, state: str, ts: str, repo: str = "api") -> None:
    folder = lab / ".heliopause" / "cards" / tool
    folder.mkdir(parents=True, exist_ok=True)
    doc = {"repo": repo, "session": session, "state": state, "tool": tool, "ts": ts}
    (folder / f"{session}.json").write_text(json.dumps(doc, sort_keys=True) + "\n", encoding="utf-8")


def _ids(items: list) -> list:
    return [item["id"] for item in items]


def _member(body: dict, role: str, tool: str | None) -> dict:
    for item in body["seats"][role]:
        if item["tool"] == tool:
            return item
    raise AssertionError((role, tool))


def test_messages_newest_first_and_each_filter(tmp_path: Path) -> None:
    specs = [
        (1, "2026-10-22T15:00:00Z", dict(kind="ask", frm="lab", tool="claude-code", to="hq", repo="api")),
        (2, "2026-10-22T16:00:00Z", dict(kind="job", frm="factory", tool="cursor", to="lab", repo="web", state="picked_up")),
        (3, "2026-10-22T16:00:00Z", dict(kind="report", frm="hq", tool="grok", to="factory", repo="api", state="failed")),
        (4, "2026-10-22T14:00:00Z", dict(kind="ask", frm="lab", tool="codex", to="hq", repo="api", state="done")),
        (5, "2026-10-22T17:00:00Z", dict(kind="plan", frm="lab", tool="claude-code", to="hq", repo="other")),
        (6, "2026-10-22T17:30:00Z", dict(kind="job", frm="lab", tool="claude-code", to="factory", repo="api", reply_to=mid(5))),
        (7, "2026-10-22T13:00:00Z", dict(kind="ask", frm="lab", tool="claude-code", to="hq", repo=None, reply_to=MISSING)),
    ]
    for n, ts, kw in specs:
        msg = _msg(n, ts, **kw)
        _write(tmp_path, "done" if n == 4 else "inbox", msg["to"]["role"], msg)
    before = _snap(tmp_path)
    cases = [
        ({}, [6, 5, 3, 2, 1, 4, 7]),
        ({"role": "hq"}, [5, 3, 1, 4, 7]),
        ({"role": "lab"}, [6, 5, 2, 1, 4, 7]),
        ({"role": "factory"}, [6, 3, 2]),
        ({"tool": "cursor"}, [2]),
        ({"tool": "claude-code"}, [6, 5, 1, 7]),
        ({"tool": "codex"}, [4]),
        ({"tool": "grok"}, [3]),
        ({"repo": "api"}, [6, 3, 1, 4]),
        ({"repo": "web"}, [2]),
        ({"repo": "other"}, [5]),
        ({"state": "sent"}, [6, 5, 1, 7]),
        ({"state": "picked_up"}, [2]),
        ({"state": "done"}, [4]),
        ({"state": "failed"}, [3]),
        ({"state": "waiting"}, [1, 7]),
        ({"thread": mid(5)}, [6, 5]),
        ({"thread": MISSING}, [7]),
        ({"limit": "1"}, [6]),
        ({"limit": "2", "role": ["factory"]}, [6, 3]),
    ]
    for query, expect in cases:
        code, body = messages(tmp_path, query)
        assert code == 200, query
        assert set(body) == {"generated", "messages"}
        assert _ids(body["messages"]) == [mid(n) for n in expect], query
    code, body = messages(tmp_path, {})
    by_id = {item["id"]: item for item in body["messages"]}
    ask, job = by_id[mid(1)], by_id[mid(6)]
    assert ask["waiting_on_you"] is True and ask["thread"] == mid(1)
    assert job["waiting_on_you"] is False and job["thread"] == mid(5)
    assert job["checks"] == {"ci": "none", "review": "none", "tests": "none"}
    assert by_id[mid(7)]["thread"] == MISSING and by_id[mid(4)]["waiting_on_you"] is False
    for item in body["messages"]:
        assert set(item) <= _VIEW
        assert ("checks" in item) is (item["kind"] == "job")
    assert _snap(tmp_path) == before
    assert not any(row[0].endswith(".inflight") or row[0].endswith(".lock") for row in before)


def test_job_to_tool_is_the_single_seat_tool(tmp_path: Path) -> None:
    _roles(tmp_path, hq="you", planner="claude-code", factory=["cursor"])
    job = new_message("lab", "claude-code", "factory", "job", "ship the map", repo="api", now=CLOCK)
    send(tmp_path, job)
    path = tmp_path / ".heliopause" / "inbox" / "factory" / f"{job['id']}.json"
    raw = path.read_bytes()

    code, body = messages(tmp_path, {})
    assert code == 200
    shown = {item["id"]: item for item in body["messages"]}
    assert shown[job["id"]]["to_tool"] == "cursor"
    assert path.read_bytes() == raw

    _roles(tmp_path, hq="you", planner="claude-code", factory=["cursor", "codex"])
    code, body = messages(tmp_path, {})
    assert code == 200
    shown = {item["id"]: item for item in body["messages"]}
    assert "to_tool" not in shown[job["id"]]
    assert path.read_bytes() == raw

    ask = new_message("lab", "claude-code", "hq", "ask", "a question", repo="api", now=CLOCK)
    send(tmp_path, ask)
    ask_path = tmp_path / ".heliopause" / "inbox" / "hq" / f"{ask['id']}.json"
    ask_raw = ask_path.read_bytes()
    code, body = messages(tmp_path, {})
    assert code == 200
    shown = {item["id"]: item for item in body["messages"]}
    assert "to_tool" not in shown[ask["id"]]
    assert "to_tool" not in shown[job["id"]]
    assert ask_path.read_bytes() == ask_raw
    assert path.read_bytes() == raw


def test_bad_file_is_skipped_and_inbox_stays_byte_for_byte(tmp_path: Path) -> None:
    good = _msg(1, "2026-10-22T15:00:00Z")
    _write(tmp_path, "inbox", "hq", good)
    inbox = tmp_path / ".heliopause" / "inbox" / "hq"
    (inbox / "garbage.json").write_bytes(b"{")
    (inbox / "shape.json").write_text('{"nope": true}\n', encoding="utf-8")
    (inbox / "list.json").write_text("[1]\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = _msg(2, "2026-10-22T16:00:00Z", summary="linked")
    hard = _msg(3, "2026-10-22T16:30:00Z", summary="hard")
    (outside / "linked.json").write_bytes((json.dumps(linked) + "\n").encode())
    (outside / "hard.json").write_bytes((json.dumps(hard) + "\n").encode())
    (inbox / "linked.json").symlink_to(outside / "linked.json")
    os.link(outside / "hard.json", inbox / "hard.json")
    leaked = outside / "elsewhere"
    leaked.mkdir()
    _write(leaked, "inbox", "hq", _msg(4, "2026-10-22T17:00:00Z", summary="hidden"))
    (tmp_path / ".heliopause" / "inbox" / "lab").symlink_to(leaked / ".heliopause" / "inbox" / "hq")
    before = _snap(tmp_path)
    outside_bytes = (outside / "linked.json").read_bytes(), (outside / "hard.json").read_bytes()
    code, body = messages(tmp_path, {"state": "nope"})
    assert code == 400 and body == {"error": "bad query"}
    code, body = messages(tmp_path, {})
    assert code == 200 and _ids(body["messages"]) == [mid(1)]
    assert _snap(tmp_path) == before
    assert outside_bytes == ((outside / "linked.json").read_bytes(), (outside / "hard.json").read_bytes())


def test_inbox_copy_wins_over_done(tmp_path: Path) -> None:
    _write(tmp_path, "done", "hq", _msg(1, "2026-10-22T15:00:00Z", summary="old"))
    _write(tmp_path, "inbox", "hq", _msg(1, "2026-10-22T15:00:00Z", summary="new"))
    code, body = messages(tmp_path, {})
    assert code == 200 and len(body["messages"]) == 1
    assert body["messages"][0]["summary"] == "new"


@pytest.mark.parametrize(
    "query",
    [
        None, [], "hq", {"role": "boss"}, {"role": ""}, {"role": ["hq", "lab"]}, {"role": [1]},
        {"state": "open"}, {"state": ""}, {"nope": "1"}, {"tool": ""}, {"repo": ""},
        {"limit": "0"}, {"limit": "1001"}, {"limit": "0200"}, {"limit": "01"}, {"limit": "-1"},
        {"limit": "+10"}, {"limit": "1.5"}, {"limit": ["1", "2"]}, {"thread": "nope"},
        {"thread": "m-" + "A" * 12}, {"thread": ""}, {"role": "hq", "state": ["sent", "done"]},
    ],
)
def test_bad_query_is_400(tmp_path: Path, query: object) -> None:
    code, body = messages(tmp_path, query)
    assert code == 400 and body == {"error": "bad query"}
    assert not (tmp_path / ".heliopause").exists()


def test_limit_and_thread_shapes_that_are_valid(tmp_path: Path) -> None:
    for query in ({}, {"limit": "1"}, {"limit": "1000"}, {"state": "waiting"}, {"thread": mid(1)}):
        code, body = messages(tmp_path, query)
        assert code == 200 and body["messages"] == []


def _rich(lab: Path) -> None:
    _roles(
        lab, hq="grok", planner="claude-code", factory=["cursor", "codex"],
        github={"enabled": True}, repos={"api": "lab", "web": "lab"}, tz="America/New_York",
    )
    _card(lab, "grok", "s", "idle", "2026-10-22T15:50:00Z", "api")
    _card(lab, "claude-code", "s", "working", "2026-10-22T15:59:00Z", "web")
    _card(lab, "cursor", "s", "working", "2026-10-22T15:00:00Z", "docs")
    rows = [
        (1, "2026-10-22T03:30:00Z", dict(kind="ask", repo="signup")),
        (2, "2026-10-22T14:00:00Z", dict(kind="ask")),
        (3, "2026-10-22T15:00:00Z", dict(kind="job", to="factory", state="failed")),
        (4, "2026-10-22T15:30:00Z", dict(kind="job", to="factory")),
        (5, "2026-10-22T15:45:00Z", dict(kind="report", frm="factory", tool="cursor", to="lab")),
        (6, "2026-10-22T03:00:00Z", dict(kind="job", to="factory", state="picked_up")),
        (7, "2026-10-22T15:50:00Z", dict(kind="answer", frm="hq", tool="grok", to="hq")),
        (8, "2026-10-22T12:00:00Z", dict(kind="report", to="hq", state="failed")),
        (9, "2026-10-22T04:30:00Z", dict(kind="plan", to="lab")),
    ]
    for n, ts, kw in rows:
        msg = _msg(n, ts, **kw)
        _write(lab, "inbox", msg["to"]["role"], msg)


def test_now_counts_needs_you_and_seats(tmp_path: Path) -> None:
    _rich(tmp_path)
    body = now(tmp_path, now=CLOCK)
    assert set(body) == _NOW
    assert body["generated"] == "2026-10-22T16:00:00Z"
    assert body["github"] is True
    assert body["sentence"] == "2 asks are waiting on you, and 1 job failed."
    assert body["counts"] == {"messages_today": 7, "tools_connected": 3, "repos": 4}
    assert _ids(body["needs_you"]) == [mid(1), mid(2)]
    assert _ids(body["moving"]) == [mid(7), mid(5), mid(4), mid(3), mid(2), mid(8), mid(9)]
    you, reviewer = body["seats"]["hq"]
    assert set(you) == _MEMBER and set(reviewer) == _MEMBER
    assert you == {"name": "You", "tool": None, "state": "needs_you", "since": None, "repo": None, "waiting": 3}
    assert reviewer == {
        "name": "Reviewer", "tool": "grok", "state": "idle",
        "since": "2026-10-22T15:50:00Z", "repo": "api", "waiting": 3,
    }
    planner = body["seats"]["lab"][0]
    assert planner["name"] == "Planner" and planner["tool"] == "claude-code"
    assert planner["state"] == "working" and planner["since"] == "2026-10-22T15:59:00Z"
    assert planner["repo"] == "web" and planner["waiting"] == 2
    builder, second = body["seats"]["factory"]
    assert builder["name"] == "Builder" and builder["tool"] == "cursor" and builder["state"] == "maybe_waiting"
    assert builder["since"] == "2026-10-22T15:00:00Z" and builder["repo"] == "docs" and builder["waiting"] == 2
    assert second == {"name": "Builder 2", "tool": "codex", "state": "idle", "since": None, "repo": None, "waiting": 2}


def test_moving_list_stops_at_eight(tmp_path: Path) -> None:
    _roles(tmp_path, tz="America/New_York")
    for n in range(1, 10):
        _write(tmp_path, "inbox", "lab", _msg(n, f"2026-10-22T14:{n:02d}:00Z", kind="plan", to="lab"))
    body = now(tmp_path, now=CLOCK)
    assert _ids(body["moving"]) == [mid(n) for n in range(9, 1, -1)]
    assert body["sentence"] == "Nothing needs you."
    assert body["counts"]["messages_today"] == 9


@pytest.mark.parametrize(
    ("rows", "sentence"),
    [
        ([], "Nothing yet. No tool has checked in."),
        ([(1, "2026-10-22T15:00:00Z", dict(kind="ask"))], "1 ask is waiting on you."),
        ([(1, "2026-10-22T15:00:00Z", dict(kind="ask")),
          (2, "2026-10-22T15:01:00Z", dict(kind="ask"))], "2 asks are waiting on you."),
        ([(1, "2026-10-22T15:00:00Z", dict(kind="job", to="factory", state="failed"))], "1 job failed."),
        ([(1, "2026-10-22T15:00:00Z", dict(kind="job", to="factory", state="failed")),
          (2, "2026-10-22T15:01:00Z", dict(kind="job", to="factory", state="failed"))], "2 jobs failed."),
        ([(1, "2026-10-22T15:00:00Z", dict(kind="job", to="factory"))], "Nothing needs you. 1 job is moving."),
        ([(1, "2026-10-22T15:00:00Z", dict(kind="job", to="factory")),
          (2, "2026-10-22T15:01:00Z", dict(kind="job", to="factory"))], "Nothing needs you. 2 jobs are moving."),
        ([(1, "2026-10-22T15:00:00Z", dict(kind="ask")),
          (2, "2026-10-22T15:01:00Z", dict(kind="job", to="factory", state="failed")),
          (3, "2026-10-22T15:02:00Z", dict(kind="job", to="factory", state="failed"))],
         "1 ask is waiting on you, and 2 jobs failed."),
        ([(1, "2026-10-22T15:00:00Z", dict(kind="job", to="factory", state="done"))], "Nothing needs you."),
        ([(1, "2026-10-21T03:30:00Z", dict(kind="ask"))], "1 ask is waiting on you."),
    ],
)
def test_status_sentences(tmp_path: Path, rows: list, sentence: str) -> None:
    lab = tmp_path / "lab"
    lab.mkdir()
    _roles(lab, tz="America/New_York")
    for n, ts, kw in rows:
        msg = _msg(n, ts, **kw)
        _write(lab, "inbox", msg["to"]["role"], msg)
    assert now(lab, now=CLOCK)["sentence"] == sentence


def test_today_uses_lab_tz(tmp_path: Path) -> None:
    """23:30 UTC is morning in Tokyo and the previous evening in Los Angeles."""
    fixed = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

    def screen(name: str, doc: dict | None) -> dict:
        lab = tmp_path / name
        lab.mkdir()
        if doc is not None:
            (lab / "atlas.json").write_text(json.dumps(doc), encoding="utf-8")
        _write(lab, "inbox", "hq", _msg(1, "2026-10-22T23:30:00Z"))
        return now(lab, now=fixed)

    tokyo = screen("tokyo", {"tz": "Asia/Tokyo"})
    la = screen("la", {"tz": "America/Los_Angeles"})
    assert tokyo["counts"]["messages_today"] == 1 and _ids(tokyo["moving"]) == [mid(1)]
    assert la["counts"]["messages_today"] == 0 and la["moving"] == []
    local = datetime.now().astimezone().tzinfo
    when = datetime(2026, 10, 22, 23, 30, tzinfo=timezone.utc)
    same = when.astimezone(local).date() == fixed.astimezone(local).date()
    expect_n = 1 if same else 0
    expect_ids = [mid(1)] if same else []
    for name, doc in (("bad", {"tz": "Mars/Base"}), ("missing", {})):
        body = screen(name, doc)
        assert set(body) == _NOW
        assert body["counts"]["messages_today"] == expect_n
        assert _ids(body["moving"]) == expect_ids


def test_empty_day_with_idle_tools(tmp_path: Path) -> None:
    _roles(tmp_path, hq="you", planner="claude-code", factory=["cursor"], repos={"api": "lab", "web": "lab"})
    _card(tmp_path, "cursor", "s", "idle", "2026-10-22T15:59:00Z")
    _card(tmp_path, "claude-code", "s", "idle", "2026-10-22T15:59:00Z")
    body = now(tmp_path, now=CLOCK)
    assert body["sentence"] == "Nothing yet. Your tools are connected and idle."
    assert body["counts"] == {"messages_today": 0, "tools_connected": 2, "repos": 2}
    assert body["needs_you"] == [] and body["moving"] == []
    assert body["seats"]["hq"][0]["state"] == "idle"
    assert body["github"] is False


def test_maybe_waiting_names_and_unseated_cards(tmp_path: Path) -> None:
    def seat(name: str, cards: list, **roles: object) -> dict:
        lab = tmp_path / name
        lab.mkdir()
        _roles(lab, **roles)
        for spec in cards:
            _card(lab, *spec)
        return now(lab, now=CLOCK)

    exact = seat("exact", [("cursor", "s", "working", "2026-10-22T15:50:00Z")], factory=["cursor"])
    assert _member(exact, "factory", "cursor")["state"] == "working"
    stale = seat("stale", [("cursor", "s", "working", "2026-10-22T15:49:59Z")], factory=["cursor"])
    assert _member(stale, "factory", "cursor")["state"] == "maybe_waiting"
    grok = seat("grok", [("grok", "s", "working", "2026-10-22T15:49:59Z")], hq="grok")
    assert _member(grok, "hq", "grok")["state"] == "maybe_waiting"
    loud = seat(
        "loud",
        [("claude-code", "s", "working", "2026-10-22T15:00:00Z"),
         ("codex", "s", "working", "2026-10-22T15:00:00Z")],
        planner="claude-code", factory=["codex"],
    )
    assert _member(loud, "lab", "claude-code")["state"] == "working"
    assert _member(loud, "factory", "codex")["state"] == "working"
    asking = seat("asking", [("cursor", "s", "needs_you", "2026-10-22T15:00:00Z")], factory=["cursor"])
    assert _member(asking, "factory", "cursor")["state"] == "needs_you"
    quiet = seat("quiet", [("cursor", "s", "idle", "2026-10-22T15:00:00Z")], factory=["cursor"])
    assert _member(quiet, "factory", "cursor")["state"] == "idle"
    fresh = seat(
        "fresh",
        [("cursor", "a", "working", "2026-10-22T15:00:00Z"),
         ("cursor", "m", "idle", "2026-10-22T15:59:00Z")],
        factory=["cursor"],
    )
    chosen = _member(fresh, "factory", "cursor")
    assert chosen["state"] == "idle" and chosen["since"] == "2026-10-22T15:59:00Z"
    tie = seat(
        "tie",
        [("cursor", "a", "working", "2026-10-22T15:00:00Z"),
         ("cursor", "b", "needs_you", "2026-10-22T15:00:00Z")],
        factory=["cursor"],
    )
    assert _member(tie, "factory", "cursor")["state"] == "needs_you"
    bare = seat("bare", [("cursor", "s", "working", "2026-10-22T15:00:00Z")])
    assert bare["counts"]["tools_connected"] == 1 and bare["seats"]["factory"] == []
    assert bare["seats"]["hq"][0]["state"] == "idle"
    named = seat(
        "named", [], planner="claude-code", factory=["cursor", "codex", "grok"],
    )
    assert [item["name"] for item in named["seats"]["hq"]] == ["You"]
    assert [item["name"] for item in named["seats"]["lab"]] == ["Planner"]
    assert [item["name"] for item in named["seats"]["factory"]] == ["Builder", "Builder 2", "Builder 3"]
    assert [item["tool"] for item in named["seats"]["factory"]] == ["cursor", "codex", "grok"]


@pytest.mark.parametrize("value, expected", [(False, False), ("yes", False), (1, False)])
def test_github_switch_is_only_true_when_boolean_true(tmp_path: Path, value: object, expected: bool) -> None:
    _roles(tmp_path, github=value)
    assert now(tmp_path, now=CLOCK)["github"] is expected


def test_github_symlink_is_not_followed(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    target.write_text(json.dumps({"github": True, "roles": {"hq": "grok"}}), encoding="utf-8")
    (tmp_path / "atlas.json").symlink_to(target)
    raw = target.read_bytes()
    assert now(tmp_path, now=CLOCK)["github"] is False
    assert target.read_bytes() == raw
    assert now(tmp_path, now=CLOCK)["seats"]["hq"] == [
        {"name": "You", "tool": None, "state": "idle", "since": None, "repo": None, "waiting": 0}
    ]


def _ask(lab: Path, **kw: object) -> dict:
    msg = new_message("lab", "claude-code", "hq", "ask", "Merge the change", repo="api", **kw)
    send(lab, msg)
    return msg


def _stored(lab: Path, role: str, msg_id: str, box: str = "inbox") -> dict:
    path = lab / ".heliopause" / box / role / f"{msg_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_approve_and_decline_write_the_answer_and_finish_the_ask(tmp_path: Path) -> None:
    plan = new_message("lab", "claude-code", "lab", "plan", "Search filters", repo="api")
    send(tmp_path, plan)
    ask = _ask(tmp_path, reply_to=plan["id"])
    code, body = approve(tmp_path, {"id": ask["id"], "answer": "approve"})
    assert code == 200 and body["ok"] is True
    answer = body["answer"]
    assert answer["waiting_on_you"] is False and answer["thread"] == plan["id"]
    stored = _stored(tmp_path, "lab", answer["id"])
    shown = dict(answer)
    assert shown.pop("waiting_on_you") is False and shown.pop("thread") == plan["id"]
    assert stored == shown
    assert stored["from"] == {"role": "hq", "tool": "you"}
    assert stored["to"] == {"role": "lab"} and stored["kind"] == "answer"
    assert stored["summary"] == "Approved" and stored["repo"] == "api"
    assert stored["reply_to"] == ask["id"] and stored["state"] == "sent"
    assert not (tmp_path / ".heliopause" / "inbox" / "hq" / f"{ask['id']}.json").exists()
    assert _stored(tmp_path, "hq", ask["id"], "done")["state"] == "done"

    picked = _ask(tmp_path)
    set_state(tmp_path, "hq", picked["id"], "picked_up")
    code, body = approve(tmp_path, {"id": picked["id"], "answer": "approve"})
    assert code == 200 and body["answer"]["summary"] == "Approved"

    labels = {
        "wrong_scope": "Wrong scope", "too_big": "Too big", "not_now": "Not now", "other": "Other",
    }
    for reason, label in labels.items():
        lab = tmp_path / reason
        lab.mkdir()
        pending = new_message("factory", "cursor", "hq", "ask", "Ship it", repo="web")
        send(lab, pending)
        code, body = approve(lab, {"id": pending["id"], "answer": "decline", "reason": reason})
        assert code == 200, reason
        assert body["answer"]["summary"] == f"Declined: {label}"
        saved = _stored(lab, "factory", body["answer"]["id"])
        assert saved["summary"] == f"Declined: {label}"
        assert saved["to"] == {"role": "factory"} and "waiting_on_you" not in saved
        assert _stored(lab, "hq", pending["id"], "done")["state"] == "done"


def test_approve_rejects_bad_bodies_without_writing(tmp_path: Path) -> None:
    ask = _ask(tmp_path)
    job = new_message("lab", "claude-code", "hq", "job", "Fix the gate", repo="api")
    send(tmp_path, job)
    other = new_message("lab", "claude-code", "lab", "ask", "Not for hq", repo="api")
    send(tmp_path, other)
    done = _ask(tmp_path)
    set_state(tmp_path, "hq", done["id"], "done")
    ack(tmp_path, "hq", done["id"])
    outside = tmp_path / "outside.json"
    planted = _msg(9, "2026-10-22T15:00:00Z")
    outside.write_bytes((json.dumps(planted) + "\n").encode())
    link = tmp_path / ".heliopause" / "inbox" / "hq" / f"{planted['id']}.json"
    link.symlink_to(outside)
    before = _snap(tmp_path)
    outside_raw = outside.read_bytes()
    cases = [
        ({"id": ask["id"], "answer": "decline"}, 400),
        ({"id": ask["id"], "answer": "decline", "reason": "later"}, 400),
        ({"id": ask["id"], "answer": "decline", "reason": []}, 400),
        ({"id": ask["id"], "answer": "decline", "reason": {}}, 400),
        ({"id": ask["id"], "answer": "approve", "reason": "other"}, 400),
        ({"id": ask["id"], "answer": "approve", "reason": None}, 400),
        ({"id": ask["id"], "answer": "approve", "note": "x"}, 400),
        ({"id": "../etc", "answer": "approve"}, 400),
        ({"id": ask["id"], "answer": "yes"}, 400),
        ([], 400),
        ({"id": job["id"], "answer": "approve"}, 409),
        ({"id": other["id"], "answer": "approve"}, 409),
        ({"id": done["id"], "answer": "approve"}, 409),
        ({"id": planted["id"], "answer": "approve"}, 409),
        ({"id": mid(8), "answer": "approve"}, 409),
    ]
    for body, status in cases:
        code, _payload = approve(tmp_path, body)
        assert code == status, body
        assert _snap(tmp_path) == before
    assert outside.read_bytes() == outside_raw


def test_mark_done_and_failed(tmp_path: Path) -> None:
    job = new_message("lab", "claude-code", "factory", "job", "Fix the gate", repo="api")
    send(tmp_path, job)
    code, body = mark(tmp_path, {"id": job["id"], "result": "done"})
    assert code == 200 and body == {"ok": True}
    assert _stored(tmp_path, "factory", job["id"])["state"] == "done"
    reports = list((tmp_path / ".heliopause" / "inbox" / "lab").glob("*.json"))
    assert len(reports) == 1 and json.loads(reports[0].read_text(encoding="utf-8"))["kind"] == "report"

    other = new_message("lab", "claude-code", "factory", "job", "Fix the other gate", repo="api")
    send(tmp_path, other)
    code, body = mark(tmp_path, {"id": other["id"], "result": "failed"})
    assert code == 200 and body == {"ok": True}
    assert _stored(tmp_path, "factory", other["id"])["state"] == "failed"


def test_mark_unknown_not_a_job_and_extra_fields(tmp_path: Path) -> None:
    ask = _ask(tmp_path)
    job = new_message("lab", "claude-code", "factory", "job", "Fix the gate", repo="api")
    send(tmp_path, job)
    before = _snap(tmp_path)
    code, body = mark(tmp_path, {"id": mid(8), "result": "done"})
    assert code == 404 and body == {"error": "No job with that id."}
    code, body = mark(tmp_path, {"id": ask["id"], "result": "failed"})
    assert code == 409 and body == {"error": "That message is not a job."}
    code, body = mark(tmp_path, {"id": job["id"], "result": "done", "tests": "pass"})
    assert code == 400 and body == {"error": "unknown field"}
    code, body = mark(tmp_path, {"id": job["id"], "result": "later"})
    assert code == 400 and body == {"error": "outcome must be done or failed"}
    code, body = mark(tmp_path, {"id": "nope", "result": "done"})
    assert code == 400 and "id" in body["error"]
    assert _snap(tmp_path) == before


_HTTP = r"""
import json, os, threading
from http.client import HTTPConnection
from pathlib import Path
from atlas.__main__ import make_server

lab = Path(os.environ["ATLAS_LAB"])
ask, ask2, job, plan = (os.environ[name] for name in ("ASK", "ASK2", "JOB", "PLAN"))

def snap(root):
    base = root / ".heliopause"
    rows = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        for name in list(dirnames) + list(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(base).as_posix()
            if path.is_symlink():
                rows.append((rel, "link", os.readlink(path)))
            elif path.is_file():
                rows.append((rel, "file", path.read_bytes()))
    return sorted(rows)

server = make_server(lab, 0, watch=False)
threading.Thread(target=server.serve_forever, daemon=True).start()
port, nonce = server.server_address[1], server.ui_nonce
token = (lab / ".atlas" / "token").read_text(encoding="utf-8")

def req(method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()

def post(path, payload, headers):
    return req("POST", path, json.dumps(payload).encode(), headers)

gate = {"Origin": f"http://127.0.0.1:{port}", "X-Atlas-UI": nonce,
        "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}
cli = {"Content-Type": "application/json", "X-Atlas-Token": token}
try:
    before = snap(lab)
    listed = req("GET", "/api/messages")
    waiting = req("GET", "/api/messages?state=waiting")
    limited = req("GET", "/api/messages?limit=1")
    bad = req("GET", "/api/messages?state=nope")
    blank = req("GET", "/api/messages?role=")
    dup = req("GET", "/api/messages?state=sent&state=failed")
    screen = req("GET", "/api/now")
    assert snap(lab) == before
    token_only = post("/api/approve", {"id": ask, "answer": "approve"}, cli)
    wrong = post("/api/approve", {"id": ask, "answer": "approve"}, {**gate, "X-Atlas-UI": "nope"})
    token_mark = post("/api/jobs/mark", {"id": job, "result": "failed"}, cli)
    assert snap(lab) == before
    no_reason = post("/api/approve", {"id": ask, "answer": "decline"}, gate)
    with_reason = post("/api/approve", {"id": ask, "answer": "approve", "reason": "other"}, gate)
    assert snap(lab) == before
    approved = post("/api/approve", {"id": ask, "answer": "approve"}, gate)
    declined = post("/api/approve", {"id": ask2, "answer": "decline", "reason": "wrong_scope"}, gate)
    marked = post("/api/jobs/mark", {"id": job, "result": "done"}, gate)
    missing = post("/api/jobs/mark", {"id": "m-" + "e" * 12, "result": "failed"}, gate)
    not_job = post("/api/jobs/mark", {"id": plan, "result": "done"}, gate)
    assert [item["id"] for item in listed[1]["messages"]] == [ask, job, ask2, plan]
    assert [item["id"] for item in waiting[1]["messages"]] == [ask, ask2]
    assert [item["id"] for item in limited[1]["messages"]] == [ask]
    assert bad == (400, {"error": "bad query"}) and blank[0] == 400 and dup[0] == 400
    assert [item["id"] for item in screen[1]["needs_you"]] == [ask2, ask]
    assert screen[1]["seats"]["hq"][0]["name"] == "You"
    assert screen[1]["seats"]["hq"][0]["state"] == "needs_you"
    assert screen[1]["sentence"] == "2 asks are waiting on you."
    assert token_only[0] == 403 and token_only[1]["error"] == "origin"
    assert wrong[0] == 403 and wrong[1]["error"] == "nonce"
    assert token_mark[0] == 403
    assert no_reason[0] == 400 and with_reason[0] == 400
    assert approved[0] == 200 and approved[1]["answer"]["summary"] == "Approved"
    assert approved[1]["answer"]["waiting_on_you"] is False and approved[1]["answer"]["thread"] == ask
    assert declined[1]["answer"]["summary"] == "Declined: Wrong scope"
    assert marked == (200, {"ok": True}) and missing[0] == 404 and not_job[0] == 409
    print(json.dumps({"approved": approved[1], "declined": declined[1]}))
finally:
    server.shutdown()
    server.server_close()
"""


def _lab_config(lab: Path) -> None:
    doc = {
        "v": 1, "owner": "Owner", "tz": "America/New_York",
        "nodes": [
            {"id": "lead", "label": "Lead", "role": "lab"},
            {"id": "worker", "label": "Worker", "role": "factory"},
        ],
        "roles": {"hq": "you", "lab": "claude-code", "factory": ["cursor"]},
        "github": False,
    }
    (lab / "atlas.json").write_text(json.dumps(doc), encoding="utf-8")


def test_http_lab_routes(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    lab.mkdir()
    _lab_config(lab)
    ask = _msg(2, "2026-10-22T12:00:00Z", frm="lab", tool="claude-code", to="hq")
    ask2 = _msg(4, "2026-10-22T11:00:00Z", frm="factory", tool="cursor", to="hq", summary="second")
    job = _msg(3, "2026-10-22T11:30:00Z", kind="job", frm="lab", tool="claude-code", to="factory")
    plan = _msg(1, "2026-10-22T10:00:00Z", kind="plan", frm="hq", tool="grok", to="lab", summary="plan")
    _write(lab, "inbox", "hq", ask)
    _write(lab, "inbox", "hq", ask2)
    _write(lab, "inbox", "factory", job)
    _write(lab, "inbox", "lab", plan)
    (lab / ".heliopause" / "inbox" / "hq" / "garbage.json").write_bytes(b"{")
    env = os.environ.copy()
    env["ATLAS_LAB"] = str(lab)
    env.update(ASK=ask["id"], ASK2=ask2["id"], JOB=job["id"], PLAN=plan["id"])
    proc = subprocess.run([PY, "-c", _HTTP], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    answer = data["approved"]["answer"]
    stored = _stored(lab, "lab", answer["id"])
    shown = dict(answer)
    assert shown.pop("waiting_on_you") is False and shown.pop("thread") == ask["id"]
    assert stored == shown and stored["summary"] == "Approved"
    assert _stored(lab, "hq", ask["id"], "done")["state"] == "done"
    assert not (lab / ".heliopause" / "inbox" / "hq" / f"{ask['id']}.json").exists()
    declined = data["declined"]["answer"]
    assert _stored(lab, "factory", declined["id"])["summary"] == "Declined: Wrong scope"
    assert _stored(lab, "hq", ask2["id"], "done")["state"] == "done"
    assert _stored(lab, "factory", job["id"])["state"] == "done"
    reports = [json.loads(path.read_text(encoding="utf-8"))
               for path in (lab / ".heliopause" / "inbox" / "lab").glob("*.json")]
    assert any(item["kind"] == "report" and item["reply_to"] == job["id"] for item in reports)


_NO_LAB = r"""
import json, os, threading
from http.client import HTTPConnection
from pathlib import Path
from atlas.__main__ import make_server

root = Path(os.environ["ROOT"])
server = make_server(root, 0, watch=False)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_address[1]

def req(method, path, body=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()

try:
    out = {"messages": req("GET", "/api/messages"), "now": req("GET", "/api/now"),
           "approve": req("POST", "/api/approve", b"{}"), "mark": req("POST", "/api/jobs/mark", b"{}")}
    print(json.dumps(out))
finally:
    server.shutdown()
    server.server_close()
"""


def test_http_without_lab_is_404(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("ATLAS_LAB", None)
    env["ROOT"] = str(tmp_path)
    proc = subprocess.run([PY, "-c", _NO_LAB], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    for key in ("messages", "now", "approve", "mark"):
        assert data[key] == [404, {"error": "no lab"}], key


def test_cli_approve_is_refused() -> None:
    env = os.environ.copy()
    env.pop("ATLAS_LAB", None)
    proc = subprocess.run([PY, "-m", "atlas", "approve"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2

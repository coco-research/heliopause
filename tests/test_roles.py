"""Heliopause role inbox. Messages live under tmp_path, never a home directory."""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import atlas.roles as roles
from atlas.roles import ack, bad_files, new_message, send, set_check, set_state, take, validate

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
ID_A = "m-" + "a" * 12
ID_B = "m-" + "b" * 12
_ID = re.compile(r"\Am-[0-9a-f]{12}\Z")


def _msg(**overrides: object) -> dict:
    msg: dict = {
        "id": ID_A,
        "from": {"role": "lab", "tool": "board"},
        "to": {"role": "hq"},
        "kind": "report",
        "summary": "lane is quiet",
        "repo": None,
        "state": "sent",
        "reply_to": None,
        "ts": "2026-10-21T12:00:00Z",
    }
    msg.update(overrides)
    return msg


def _job(**overrides: object) -> dict:
    msg = _msg(kind="job", to={"role": "factory"})
    msg["checks"] = {"tests": "none", "review": "none", "ci": "none"}
    msg.update(overrides)
    return msg


def _inbox(root: Path, role: str = "hq") -> Path:
    return root / ".heliopause" / "inbox" / role


@pytest.mark.parametrize("msg", [None, [], "x", 1, 1.5])
def test_validate_rejects_non_objects(msg: object) -> None:
    assert validate(msg) == ["message must be an object"]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"id": "nope"}, ["id must be m- and 12 lowercase hex digits"]),
        ({"id": "m-" + "A" * 12}, ["id must be m- and 12 lowercase hex digits"]),
        ({"id": "m-" + "a" * 11}, ["id must be m- and 12 lowercase hex digits"]),
        ({"id": "m-" + "a" * 13}, ["id must be m- and 12 lowercase hex digits"]),
        ({"id": "M-" + "a" * 12}, ["id must be m- and 12 lowercase hex digits"]),
        ({"id": 12}, ["id must be m- and 12 lowercase hex digits"]),
        ({"kind": "note"}, ["kind must be plan, job, report, ask, or answer"]),
        ({"kind": None}, ["kind must be plan, job, report, ask, or answer"]),
        ({"state": "open"}, ["state must be sent, picked_up, done, or failed"]),
        ({"summary": "a\nb"}, ["summary must be one line"]),
        ({"summary": "a\rb"}, ["summary must be one line"]),
        ({"summary": "a\r\nb"}, ["summary must be one line"]),
        ({"summary": "x" * 201}, ["summary must be at most 200 characters"]),
        (
            {"summary": "\n" + ("x" * 200)},
            ["summary must be one line", "summary must be at most 200 characters"],
        ),
        ({"summary": 5}, ["summary must be a string"]),
        ({"summary": None}, ["summary must be a string"]),
        ({"repo": 5}, ["repo must be a string or null"]),
        ({"repo": []}, ["repo must be a string or null"]),
        ({"ts": "2026-10-21T12:00:00+00:00"}, ["ts must be ISO-8601 UTC ending in Z"]),
        ({"ts": "2026-10-21T12:00:00"}, ["ts must be ISO-8601 UTC ending in Z"]),
        ({"ts": "2026-13-01T00:00:00Z"}, ["ts must be ISO-8601 UTC ending in Z"]),
        ({"ts": "2026-02-31T00:00:00Z"}, ["ts must be ISO-8601 UTC ending in Z"]),
        ({"ts": "2026-10-21T12:00:00z"}, ["ts must be ISO-8601 UTC ending in Z"]),
        ({"ts": "2026-10-21t12:00:00Z"}, ["ts must be ISO-8601 UTC ending in Z"]),
        ({"ts": "2026-10-21 12:00:00Z"}, ["ts must be ISO-8601 UTC ending in Z"]),
        ({"ts": None}, ["ts must be ISO-8601 UTC ending in Z"]),
        ({"reply_to": "reply"}, ["reply_to must be an id or null"]),
        ({"reply_to": "m-" + "A" * 12}, ["reply_to must be an id or null"]),
        ({"reply_to": ID_A}, ["reply_to must not point at itself"]),
        ({"from": "lab"}, ["from must be an object"]),
        ({"from": {"role": "boss", "tool": "t"}}, ["from.role must be hq, lab, or factory"]),
        ({"from": {"role": "HQ", "tool": "t"}}, ["from.role must be hq, lab, or factory"]),
        ({"from": {"role": "lab", "tool": 1}}, ["from.tool must be a string"]),
        ({"from": {"role": "lab"}}, ["from missing field: tool"]),
        ({"from": {"tool": "t"}}, ["from missing field: role"]),
        ({"from": {"role": "boss"}}, ["from.role must be hq, lab, or factory", "from missing field: tool"]),
        ({"from": {"role": "lab", "tool": "t", "x": 1}}, ["from has unknown field: x"]),
        ({"to": ["hq"]}, ["to must be an object"]),
        ({"to": {"role": "ops"}}, ["to.role must be hq, lab, or factory"]),
        ({"to": {}}, ["to missing field: role"]),
        ({"to": {"role": "hq", "tool": "t"}}, ["to has unknown field: tool"]),
        (
            {"checks": {"tests": "none", "review": "none", "ci": "none"}},
            ["checks must be absent unless kind is job"],
        ),
        ({"extra": 1}, ["unknown field: extra"]),
    ],
)
def test_validate_rejects(changes: dict, expected: list[str]) -> None:
    msg = _msg()
    msg.update(changes)
    assert validate(msg) == expected


def test_validate_reports_every_problem() -> None:
    msg = _msg(kind="nope", id="nope", reply_to="nope")
    msg["zeta"] = 1
    msg["alpha"] = 1
    assert validate(msg) == [
        "unknown field: alpha",
        "unknown field: zeta",
        "id must be m- and 12 lowercase hex digits",
        "kind must be plan, job, report, ask, or answer",
        "reply_to must be an id or null",
        "reply_to must not point at itself",
    ]


def test_validate_missing_fields_keep_order_and_skip_type_checks() -> None:
    msg = _msg()
    del msg["summary"]
    assert validate(msg) == ["missing field: summary"]
    msg = _msg()
    del msg["repo"]
    del msg["ts"]
    assert validate(msg) == ["missing field: repo", "missing field: ts"]


def test_validate_odd_keys_do_not_crash() -> None:
    msg = _msg()
    msg[1] = True
    msg["from"] = {"role": "lab", "tool": "t", 1: "x"}
    errors = validate(msg)
    assert "unknown field: 1" in errors
    assert "from has unknown field: 1" in errors


def test_job_check_rules() -> None:
    assert validate(_msg(kind="job")) == ["checks is required when kind is job"]
    bad = _job()
    bad["checks"] = None
    assert validate(bad) == ["checks must be an object"]
    bad = _job()
    del bad["checks"]["ci"]
    assert validate(bad) == ["checks missing field: ci"]
    bad = _job()
    bad["checks"]["lint"] = "pass"
    assert validate(bad) == ["checks has unknown field: lint"]
    bad = _job()
    bad["checks"]["tests"] = "skipped"
    assert validate(bad) == ["checks.tests must be pending, pass, fail, or none"]
    bad = _job()
    bad["checks"] = {"tests": "nope"}
    assert validate(bad) == [
        "checks missing field: review",
        "checks missing field: ci",
        "checks.tests must be pending, pass, fail, or none",
    ]


@pytest.mark.parametrize("kind", ["plan", "report", "ask", "answer"])
def test_non_job_kinds(kind: str) -> None:
    assert validate(_msg(kind=kind)) == []


@pytest.mark.parametrize("state", ["sent", "picked_up", "done", "failed"])
def test_states(state: str) -> None:
    assert validate(_msg(state=state)) == []


@pytest.mark.parametrize("role", ["hq", "lab", "factory"])
def test_roles(role: str) -> None:
    msg = _msg()
    msg["from"] = {"role": role, "tool": "bus"}
    msg["to"] = {"role": role}
    assert validate(msg) == []


@pytest.mark.parametrize("value", ["pending", "pass", "fail", "none"])
def test_check_values(value: str) -> None:
    msg = _job()
    msg["checks"] = {"tests": value, "review": value, "ci": value}
    assert validate(msg) == []


def test_validate_accepts_edges() -> None:
    assert validate(_msg(summary="x" * 200)) == []
    assert validate(_msg(summary="")) == []
    assert validate(_msg(summary="é" * 200)) == []
    assert validate(_msg(summary="é" * 201)) == ["summary must be at most 200 characters"]
    assert validate(_msg(repo="atlas")) == []
    assert validate(_msg(repo="")) == []
    assert validate(_msg(ts="2026-10-21T12:00:00.123456Z")) == []
    assert validate(_msg(ts="2026-10-21T12:00:00.1Z")) == []
    assert validate(_msg(ts="2024-02-29T23:59:59Z")) == []
    assert validate(_msg(reply_to=ID_B)) == []
    assert validate(_msg(id="m-0123456789ab")) == []
    msg = _msg()
    msg["from"] = {"role": "lab", "tool": ""}
    assert validate(msg) == []


def test_new_message_job_checks_default_to_none() -> None:
    job = new_message("hq", "planner", "factory", "job", "run tests", repo="atlas", now=NOW)
    assert validate(job) == []
    assert job["checks"] == {"tests": "none", "review": "none", "ci": "none"}
    assert job["state"] == "sent"
    assert job["repo"] == "atlas"
    assert job["reply_to"] is None
    assert job["from"] == {"role": "hq", "tool": "planner"}
    assert job["to"] == {"role": "factory"}
    assert _ID.fullmatch(job["id"])
    stamp = datetime.strptime(job["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    assert stamp == NOW

    ask = new_message("lab", "board", "hq", "ask", "hello", now=NOW)
    assert "checks" not in ask
    assert ask["repo"] is None
    assert validate(ask) == []
    parent = new_message("hq", "planner", "lab", "plan", "draft", now=NOW)
    child = new_message("lab", "board", "hq", "answer", "ship it", reply_to=parent["id"], now=NOW)
    assert child["reply_to"] == parent["id"]
    assert validate(child) == []
    assert new_message("lab", "board", "hq", "ask", "hello", now=NOW)["id"] != ask["id"]


def test_new_message_rejects_a_bad_summary() -> None:
    with pytest.raises(ValueError, match="summary must be one line"):
        new_message("hq", "tool", "lab", "ask", "two\nlines")
    with pytest.raises(ValueError, match="at most 200"):
        new_message("hq", "tool", "lab", "ask", "x" * 201)
    with pytest.raises(ValueError, match="reply_to"):
        new_message("hq", "tool", "lab", "ask", "hi", reply_to="nope")


def test_send_rejects_invalid_without_writing(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        send(tmp_path, _msg(kind="nope"))
    assert not (tmp_path / ".heliopause").exists()


def test_send_take_ack_round_trip(tmp_path: Path) -> None:
    msg = new_message("lab", "board", "hq", "ask", "café status", repo="atlas")
    send(tmp_path, msg)
    assert take(tmp_path, "hq", now=NOW) == [msg]
    marker = _inbox(tmp_path) / f"{msg['id']}.inflight"
    assert marker.read_text(encoding="utf-8") == NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ") + "\n"
    assert take(tmp_path, "lab", now=NOW) == []
    assert take(tmp_path, "factory", now=NOW) == []

    ack(tmp_path, "hq", msg["id"])
    done = tmp_path / ".heliopause" / "done" / "hq" / f"{msg['id']}.json"
    assert json.loads(done.read_text(encoding="utf-8")) == msg
    assert not (_inbox(tmp_path) / f"{msg['id']}.json").exists()
    assert not marker.exists()
    assert take(tmp_path, "hq", now=NOW) == []
    ack(tmp_path, "hq", msg["id"])
    assert done.is_file()


def test_take_returns_oldest_first(tmp_path: Path) -> None:
    send(tmp_path, _msg(id=ID_B, ts="2026-10-21T12:00:02Z", summary="new"))
    send(tmp_path, _msg(id=ID_A, ts="2026-10-21T12:00:01Z", summary="old"))
    assert [item["summary"] for item in take(tmp_path, "hq", now=NOW)] == ["old", "new"]


def test_take_reoffers_after_ttl(tmp_path: Path) -> None:
    send(tmp_path, _msg())
    assert [item["id"] for item in take(tmp_path, "hq", now=NOW, ttl_s=90)] == [ID_A]
    assert take(tmp_path, "hq", now=NOW + timedelta(seconds=89), ttl_s=90) == []
    again = take(tmp_path, "hq", now=NOW + timedelta(seconds=90), ttl_s=90)
    assert [item["id"] for item in again] == [ID_A]
    marker = _inbox(tmp_path) / f"{ID_A}.inflight"
    renewed = (NOW + timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    assert marker.read_text(encoding="utf-8") == renewed + "\n"
    assert take(tmp_path, "hq", now=NOW + timedelta(seconds=90), ttl_s=90) == []


def test_send_same_id_is_a_noop(tmp_path: Path) -> None:
    send(tmp_path, _msg(summary="first"))
    path = _inbox(tmp_path) / f"{ID_A}.json"
    first = path.read_bytes()
    send(tmp_path, _msg(summary="second"))
    assert path.read_bytes() == first
    assert json.loads(first)["summary"] == "first"
    assert list(path.parent.glob("*.json")) == [path]


def test_send_atomic_leaves_no_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_link = os.link
    seen: list[tuple[str, str]] = []

    def spy(src: str, dst: str, *args: object, **kwargs: object) -> None:
        src_fd = kwargs.get("src_dir_fd")
        dst_fd = kwargs.get("dst_dir_fd")
        assert src_fd is not None and src_fd == dst_fd
        seen.append((src, dst))
        real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", spy)
    send(tmp_path, _msg())
    assert len(seen) == 1
    src, dst = seen[0]
    folder = _inbox(tmp_path)
    assert dst == f"{ID_A}.json"
    assert not (folder / src).exists()
    assert sorted(item.name for item in folder.iterdir()) == [".lock", dst]


def test_send_failure_removes_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(src: str, dst: str, *args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "link", boom)
    with pytest.raises(OSError, match="disk full"):
        send(tmp_path, _msg())
    folder = _inbox(tmp_path)
    assert folder.is_dir()
    assert [item.name for item in folder.iterdir()] == [".lock"]


def test_send_refuses_symlinked_role_inbox(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    planted = root / ".heliopause" / "inbox" / "lab"
    planted.parent.mkdir(parents=True)
    planted.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        send(root, _msg(to={"role": "lab"}))
    assert planted.is_symlink()
    assert list(outside.iterdir()) == []


def test_send_file_mode_is_0600(tmp_path: Path) -> None:
    old = os.umask(0)
    try:
        send(tmp_path, _msg())
    finally:
        os.umask(old)
    path = _inbox(tmp_path) / f"{ID_A}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_take_skips_corrupt_json(tmp_path: Path) -> None:
    send(tmp_path, _msg())
    inbox = _inbox(tmp_path)
    (inbox / "broken.json").write_text("{", encoding="utf-8")
    (inbox / "array.json").write_text("[]", encoding="utf-8")
    (inbox / "note.txt").write_text("{", encoding="utf-8")
    assert [item["id"] for item in take(tmp_path, "hq", now=NOW)] == [ID_A]
    assert bad_files(tmp_path, "hq") == [inbox / "array.json", inbox / "broken.json"]
    names = {item.name for item in inbox.iterdir()}
    assert "broken.inflight" not in names
    assert "array.inflight" not in names
    assert bad_files(tmp_path, "lab") == []
    assert take(tmp_path, "boss", now=NOW) == []
    assert bad_files(tmp_path, "boss") == []


def test_ack_unknown_is_noop(tmp_path: Path) -> None:
    ack(tmp_path, "hq", ID_A)
    ack(tmp_path, "hq", "../etc/passwd")
    ack(tmp_path, "nope", ID_A)
    assert not (tmp_path / ".heliopause").exists()


def test_set_state_and_set_check(tmp_path: Path) -> None:
    msg = new_message("hq", "planner", "factory", "job", "build it", repo="atlas")
    send(tmp_path, msg)
    path = _inbox(tmp_path, "factory") / f"{msg['id']}.json"
    set_state(tmp_path, "factory", msg["id"], "picked_up")
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["state"] == "picked_up"
    set_check(tmp_path, "factory", msg["id"], "ci", "pass")
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["checks"] == {"tests": "none", "review": "none", "ci": "pass"}
    before = path.read_bytes()
    with pytest.raises(ValueError):
        set_state(tmp_path, "factory", msg["id"], "lost")
    with pytest.raises(ValueError):
        set_check(tmp_path, "factory", msg["id"], "ci", "skipped")
    with pytest.raises(ValueError):
        set_check(tmp_path, "factory", msg["id"], "lint", "pass")
    assert path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="unknown id"):
        set_state(tmp_path, "hq/../hq", msg["id"], "done")
    with pytest.raises(ValueError, match="unknown id"):
        set_state(tmp_path, "factory", ID_B, "done")


def _together(first, second) -> None:
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run(fn) -> None:
        try:
            barrier.wait(timeout=5)
            fn()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(fn,), daemon=True) for fn in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    assert [thread.is_alive() for thread in threads] == [False, False]


def test_concurrent_take_delivers_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    send(tmp_path, _msg())
    real = roles._leased

    def slow(marker: Path, when: datetime, ttl_s: float) -> bool:
        held = real(marker, when, ttl_s)
        if not held:
            time.sleep(0.05)
        return held

    monkeypatch.setattr(roles, "_leased", slow)
    found: list[list[dict]] = []
    guard = threading.Lock()

    def grab() -> None:
        got = take(tmp_path, "hq", now=NOW)
        with guard:
            found.append(got)

    _together(grab, grab)
    winners = [batch for batch in found if batch]
    assert len(found) == 2
    assert len(winners) == 1
    assert [item["id"] for item in winners[0]] == [ID_A]


def test_concurrent_set_check_keeps_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    send(tmp_path, _job())
    real = roles._read_json

    def slow(path: Path):
        parsed = real(path)
        if path.suffix == ".json":
            time.sleep(0.05)
        return parsed

    monkeypatch.setattr(roles, "_read_json", slow)

    def tests() -> None:
        set_check(tmp_path, "factory", ID_A, "tests", "pass")

    def review() -> None:
        set_check(tmp_path, "factory", ID_A, "review", "fail")

    _together(tests, review)
    stored = json.loads((_inbox(tmp_path, "factory") / f"{ID_A}.json").read_text(encoding="utf-8"))
    assert stored["checks"] == {"tests": "pass", "review": "fail", "ci": "none"}


def test_send_acked_id_creates_no_inbox_file(tmp_path: Path) -> None:
    send(tmp_path, _msg(summary="first"))
    ack(tmp_path, "hq", ID_A)
    done = tmp_path / ".heliopause" / "done" / "hq" / f"{ID_A}.json"
    before = done.read_bytes()
    send(tmp_path, _msg(summary="second"))
    assert not (_inbox(tmp_path) / f"{ID_A}.json").exists()
    assert list(_inbox(tmp_path).glob("*.json")) == []
    assert done.read_bytes() == before


@pytest.mark.parametrize("bad", [None, {"x": 1}, ["hq"]])
def test_validate_enum_fields_never_raise(bad: object) -> None:
    assert "kind must be plan, job, report, ask, or answer" in validate(_msg(kind=bad))
    assert "state must be sent, picked_up, done, or failed" in validate(_msg(state=bad))
    msg = _msg()
    msg["from"] = {"role": bad, "tool": "board"}
    assert "from.role must be hq, lab, or factory" in validate(msg)
    assert "to.role must be hq, lab, or factory" in validate(_msg(to={"role": bad}))
    job = _job()
    job["checks"] = {"tests": bad, "review": bad, "ci": bad}
    errors = validate(job)
    for name in ("tests", "review", "ci"):
        assert f"checks.{name} must be pending, pass, fail, or none" in errors

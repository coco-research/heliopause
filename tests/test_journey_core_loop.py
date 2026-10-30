"""Heliopause user journeys. `pytest -m journey` prints how many of them work.

A new file, not an extension: lifecycle starts the server, job_report plants a job
with no server, and the connector tests stop at one card. None walks all six steps.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from atlas.screens import _STALE_S
from journeys import (
    ROOT,
    atlas,
    card,
    expect_job,
    file_job,
    get,
    hook,
    init_lab,
    install,
    lead_and_builder,
    loop,
    one,
    read_json,
    run_report,
    seat,
    serving,
    stop,
    today_rows,
    wait_until_down,
    wait_until_up,
)

_SENT = re.compile(r"(m-[0-9a-f]{12}) sent\n")
_PAUSE_S = 0.4
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_CURSOR = (
    "cursor-session-start.json",
    "cursor-pre-tool-use.json",
    "cursor-post-tool-use.json",
    "cursor-session-end.json",
)


class _IdleAfterSessionStart(Exception):
    """The Cursor card is still idle after sessionStart. Issue #226."""


def _stamp(when: datetime) -> str:
    return when.strftime(_STAMP)


def _write_card(lab: Path, tool: str, session: str, state: str, ts: str) -> None:
    """The card a hook leaves: one JSON file per session, repo journey."""
    folder = lab / ".heliopause" / "cards" / tool
    folder.mkdir(parents=True, exist_ok=True)
    doc = {"repo": "journey", "session": session, "state": state, "tool": tool, "ts": ts}
    (folder / f"{session}.json").write_text(json.dumps(doc, sort_keys=True) + "\n", encoding="utf-8")


def _post_roles(port: int, factory: list[str]) -> dict:
    """journeys.seat, with the factory tools the test picks."""
    body = json.dumps({"roles": {"hq": "you", "lab": "claude-code", "factory": factory}}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/roles",
        data=body,
        method="POST",
        headers={
            "Origin": f"http://127.0.0.1:{port}",
            "X-Atlas-UI": get(port, "/api/state")["nonce"],
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        },
    )
    return read_json(req)


@contextmanager
def _serving_tree(tree: Path, env: dict[str, str], lab: Path, log_path: Path):
    """journeys.serving, run from a copy of the package: cwd and PYTHONPATH point at the copy."""
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "atlas", "up", "--lab", str(lab), "--port", "0"],
            cwd=tree, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        yield wait_until_up(log_path, time.monotonic() + 8)
    finally:
        stop(proc)


@pytest.mark.journey
def test_journey_core_loop(tmp_path: Path) -> None:
    """J7: Lead → job → Builder → report → Now/Messages/Today."""
    with loop(tmp_path) as box:
        job = lead_and_builder(box)
        reported = run_report(box.env, box.lab, job["id"], "done", "pass")
        assert reported.stdout == f"{job['id']} done\n"
        expect_job(one(get(box.port, "/api/now")["moving"], job["id"]), "done", "pass")
        expect_job(one(get(box.port, "/api/messages")["messages"], job["id"]), "done", "pass")
        expect_job(one(today_rows(box.port), job["id"]), "done", "pass")


@pytest.mark.journey
def test_journey_failed_job(tmp_path: Path) -> None:
    """J12: the Builder reports the job failed with tests fail, on the Lead's thread and Today."""
    with loop(tmp_path) as box:
        job = lead_and_builder(box)
        reported = run_report(box.env, box.lab, job["id"], "failed", "fail")
        assert reported.stdout == f"{job['id']} failed\n"
        thread = get(box.port, f"/api/messages?thread={job['id']}")["messages"]
        expect_job(one(thread, job["id"]), "failed", "fail")
        expect_job(one(today_rows(box.port), job["id"]), "failed", "fail")


@pytest.mark.journey
def test_journey_server_stopped(tmp_path: Path) -> None:
    """J14: hook events and a report while the server is stopped are on Now after atlas up."""
    _home, lab, env = init_lab(tmp_path)
    scripts = install(tmp_path / "install", lab)
    with serving(env, lab, tmp_path / "server-1.log") as port:
        seat(port)
        up = port
    wait_until_down(up, time.monotonic() + 3)
    for name in _CURSOR:
        hook(scripts["cursor-report"], name, env)
    job = file_job(lab)
    reported = run_report(env, lab, job["id"], "done", "pass")
    assert reported.stdout == f"{job['id']} done\n"
    with serving(env, lab, tmp_path / "server-2.log") as port:
        now = get(port, "/api/now")
        builder = now["seats"]["factory"]
        assert len(builder) == 1
        assert builder[0]["name"] == "Builder"
        assert builder[0]["tool"] == "cursor"
        assert builder[0]["repo"] == "journey"
        assert card(lab, "cursor", "builder-1")["event"] == "sessionEnd"
        expect_job(one(now["moving"], job["id"]), "done", "pass")


@pytest.mark.journey
@pytest.mark.xfail(strict=True, reason="#226", raises=_IdleAfterSessionStart)
def test_journey_cursor_thinking(tmp_path: Path) -> None:
    """issue #226: Cursor --print reads working from sessionStart, through the thinking pause, to sessionEnd."""
    with loop(tmp_path) as box:
        hook(box.scripts["cursor-report"], "cursor-session-start.json", box.env)
        builder = get(box.port, "/api/now")["seats"]["factory"]
        assert len(builder) == 1
        assert builder[0]["name"] == "Builder"
        assert builder[0]["tool"] == "cursor"
        assert builder[0]["repo"] == "journey"
        assert card(box.lab, "cursor", "builder-1")["event"] == "sessionStart"
        # Known gap, checked at the end so the rest of the sequence still runs.
        gap = None
        try:
            assert builder[0]["state"] == "working"
        except AssertionError as err:
            gap = err
        held = builder[0]["state"]
        deadline = time.monotonic() + _PAUSE_S
        while True:
            assert card(box.lab, "cursor", "builder-1")["event"] == "sessionStart"
            assert get(box.port, "/api/now")["seats"]["factory"][0]["state"] == held
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        hook(box.scripts["cursor-report"], "cursor-pre-tool-use.json", box.env)
        assert card(box.lab, "cursor", "builder-1")["event"] == "preToolUse"
        assert get(box.port, "/api/now")["seats"]["factory"][0]["state"] == "working"
        hook(box.scripts["cursor-report"], "cursor-post-tool-use.json", box.env)
        assert card(box.lab, "cursor", "builder-1")["event"] == "postToolUse"
        assert get(box.port, "/api/now")["seats"]["factory"][0]["state"] == "working"
        hook(box.scripts["cursor-report"], "cursor-session-end.json", box.env)
        ended = get(box.port, "/api/now")["seats"]["factory"][0]
        assert ended["state"] == "idle" and ended["repo"] == "journey"
        assert card(box.lab, "cursor", "builder-1")["event"] == "sessionEnd"
    if gap is not None:
        raise _IdleAfterSessionStart("working right after sessionStart") from gap


@pytest.mark.journey
@pytest.mark.xfail(strict=True, reason="atlas send not shipped yet")
def test_journey_lead_sends_job(tmp_path: Path) -> None:
    """J7: the Lead files a factory job with atlas send."""
    summary = "File the factory job"
    _home, lab, env = init_lab(tmp_path)
    proc = atlas(env, "send", "--to", "factory", "--kind", "job", summary, "--lab", str(lab), timeout=8)
    assert proc.returncode == 0, proc.stderr
    match = _SENT.fullmatch(proc.stdout)
    assert match, proc.stdout
    job_id = match.group(1)
    with serving(env, lab, tmp_path / "server.log") as port:
        found = one(get(port, "/api/messages")["messages"], job_id)
    assert found["kind"] == "job"
    assert found["summary"] == summary
    assert found["to"]["role"] == "factory"
    assert found["state"] == "sent"


@pytest.mark.journey
def test_journey_tool_goes_quiet(tmp_path: Path) -> None:
    """J13: a Builder card working past the quiet threshold shows maybe_waiting on Now."""
    with loop(tmp_path) as box:
        fresh_at = _stamp(datetime.now(timezone.utc))
        _write_card(box.lab, "cursor", "builder-1", "working", fresh_at)
        fresh = get(box.port, "/api/now")["seats"]["factory"]
        assert len(fresh) == 1, fresh
        assert fresh[0]["tool"] == "cursor", fresh
        assert fresh[0]["state"] == "working", (fresh, fresh_at)

        quiet_at = _stamp(datetime.now(timezone.utc) - timedelta(seconds=_STALE_S + 60))
        _write_card(box.lab, "cursor", "builder-1", "working", quiet_at)
        quiet = get(box.port, "/api/now")["seats"]["factory"]
        assert len(quiet) == 1, quiet
        assert quiet[0]["state"] == "maybe_waiting", (quiet, quiet_at, _STALE_S)
        assert quiet[0]["since"] == quiet_at, quiet
        assert quiet[0]["repo"] == "journey", quiet


@pytest.mark.journey
def test_journey_roles_change_with_jobs_queued(tmp_path: Path) -> None:
    """J16: swapping the factory tool leaves a queued job on Messages and Now, id and state unchanged."""
    with loop(tmp_path) as box:
        job = file_job(box.lab)
        before = one(get(box.port, "/api/messages")["messages"], job["id"])
        assert before["state"] == "sent", before
        saved = _post_roles(box.port, ["codex"])
        assert saved["roles"]["factory"] == ["codex"], saved
        after = one(get(box.port, "/api/messages")["messages"], job["id"])
        assert after["id"] == before["id"], after
        assert after["state"] == before["state"], after
        on_now = one(get(box.port, "/api/now")["moving"], job["id"])
        assert on_now["id"] == before["id"], on_now
        assert on_now["state"] == before["state"], on_now
        factory = get(box.port, "/api/now")["seats"]["factory"]
        assert [member["tool"] for member in factory] == ["codex"], factory


@pytest.mark.journey
@pytest.mark.xfail(strict=True, reason="J16: no move-or-keep confirm yet")
def test_journey_roles_change_offers_move_or_keep(tmp_path: Path) -> None:
    """J16: the roles change should offer to move or keep the jobs already queued."""
    with loop(tmp_path) as box:
        job = file_job(box.lab)
        saved = _post_roles(box.port, ["codex"])
        assert "confirm" in saved, (job["id"], sorted(saved))


@pytest.mark.journey
def test_journey_two_chats_of_one_tool(tmp_path: Path) -> None:
    """J17: two Claude Code sessions of one tool show as the freshest card only."""
    with loop(tmp_path) as box:
        hook(box.scripts["claude-report"], "claude-user-prompt-submit.json", box.env)
        hook(box.scripts["claude-report"], "claude-user-prompt-submit-2.json", box.env)
        first = card(box.lab, "claude-code", "lead-1")
        second = card(box.lab, "claude-code", "lead-2")
        assert first["event"] == "UserPromptSubmit", first
        assert second["event"] == "UserPromptSubmit", second
        seat_now = get(box.port, "/api/now")["seats"]["lab"]
        assert len(seat_now) == 1, seat_now
        assert seat_now[0]["tool"] == "claude-code", seat_now
        assert seat_now[0]["state"] == "working", seat_now
        assert seat_now[0]["since"] == second["ts"], (seat_now, first["ts"], second["ts"])


@pytest.mark.journey
@pytest.mark.xfail(strict=True, reason="J17: one card per tool")
def test_journey_two_chats_both_visible(tmp_path: Path) -> None:
    """J17: both sessions of the one tool should each be visible on Now."""
    with loop(tmp_path) as box:
        for name in ("claude-user-prompt-submit.json", "claude-user-prompt-submit-2.json"):
            hook(box.scripts["claude-report"], name, box.env)
        seat_now = get(box.port, "/api/now")["seats"]["lab"]
        assert len(seat_now) == 2, seat_now


@pytest.mark.journey
def test_journey_update_keeps_state(tmp_path: Path) -> None:
    """J20: a second copy of the package, same lab, keeps roles, the queued job and the card."""
    _home, lab, env = init_lab(tmp_path)
    with serving(env, lab, tmp_path / "server-1.log") as port:
        seat(port)
        job = file_job(lab)
        _write_card(lab, "cursor", "builder-1", "working", _stamp(datetime.now(timezone.utc)))
        before = get(port, "/api/now")
        assert before["seats"]["factory"][0]["state"] == "working", before["seats"]["factory"]

    tree = tmp_path / "update"
    shutil.copytree(ROOT / "atlas", tree / "atlas", ignore=shutil.ignore_patterns("__pycache__"))
    with _serving_tree(tree, dict(env, PYTHONPATH=str(tree)), lab, tmp_path / "server-2.log") as port:
        now = get(port, "/api/now")
        assert str(tree) in now["app_dir"], now["app_dir"]
        assert [member["tool"] for member in now["seats"]["lab"]] == ["claude-code"], now["seats"]["lab"]
        builder = now["seats"]["factory"]
        assert len(builder) == 1, builder
        assert builder[0]["tool"] == "cursor", builder
        assert builder[0]["state"] == "working", builder
        assert builder[0]["repo"] == "journey", builder
        assert one(now["moving"], job["id"])["state"] == "sent", now["moving"]
        assert one(get(port, "/api/messages")["messages"], job["id"])["state"] == "sent"

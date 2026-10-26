"""GET /api/today and GET /api/repos. Messages live under tmp_path, never a home directory."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from atlas.roles import validate
from atlas.screens import now, repos, today

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
NOW = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)  # 11:00 in America/Los_Angeles
_CHECKS = {"tests": "none", "review": "none", "ci": "none"}


def _lab(tmp_path: Path, *, github: bool = False) -> Path:
    lab = tmp_path / "lab"
    lab.mkdir()
    body = {
        "v": 1,
        "owner": "Owner",
        "tz": "America/Los_Angeles",
        "nodes": [
            {"id": "lead", "label": "Lead", "role": "lab"},
            {"id": "worker", "label": "Worker", "role": "factory"},
        ],
    }
    if github:
        body["github"] = {"enabled": True}  # the GitHub switch, as github.enabled() reads it
    (lab / "atlas.json").write_text(json.dumps(body), encoding="utf-8")
    return lab


def _msg(ident: str, ts: str, repo: str | None, state: str, *, kind: str = "job",
         tool: str = "cursor", role: str = "lab", reply_to: str | None = None) -> dict:
    msg = {
        "id": ident,
        "from": {"role": role, "tool": tool},
        "to": {"role": "factory"},
        "kind": kind,
        "summary": kind + " " + ident,
        "repo": repo,
        "state": state,
        "reply_to": reply_to,
        "ts": ts,
    }
    if kind == "job":
        msg["checks"] = dict(_CHECKS)
    assert validate(msg) == []
    return msg


def _plant(root: Path, area: str, role: str, msg: dict) -> None:
    folder = root / ".heliopause" / area / role
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{msg['id']}.json").write_text(json.dumps(msg), encoding="utf-8")


def _world(tmp_path: Path) -> Path:
    """One lab day in America/Los_Angeles. Midnight there is 07:00 UTC."""
    lab = _lab(tmp_path)
    plan = "m-" + "a" * 12
    _plant(lab, "inbox", "lab", _msg(plan, "2026-10-23T15:00:00Z", "api", "sent", kind="plan"))
    _plant(lab, "inbox", "factory", _msg(
        "m-" + "b" * 12, "2026-10-23T16:00:00Z", "api", "failed", reply_to=plan,
    ))
    _plant(lab, "done", "factory", _msg("m-" + "c" * 12, "2026-10-23T17:00:00Z", "api", "done", tool="you"))
    _plant(lab, "inbox", "factory", _msg("m-" + "d" * 12, "2026-10-23T17:30:00Z", "web", "done", tool="grok"))
    _plant(lab, "inbox", "factory", _msg("m-" + "e" * 12, "2026-10-23T15:30:00Z", "web", "sent", tool="you"))
    _plant(lab, "inbox", "factory", _msg("m-" + "1" * 12, "2026-10-23T06:59:59Z", "edge", "done"))
    _plant(lab, "inbox", "factory", _msg("m-" + "2" * 12, "2026-10-23T07:00:00Z", "edge", "picked_up"))
    _plant(lab, "inbox", "factory", _msg("m-" + "3" * 12, "2026-10-24T06:59:59Z", "edge", "sent"))
    _plant(lab, "inbox", "factory", _msg("m-" + "4" * 12, "2026-10-24T07:00:00Z", "edge", "failed"))
    _plant(lab, "inbox", "hq", _msg("m-" + "f" * 12, "2026-10-23T16:30:00Z", "api", "sent", kind="ask"))
    _plant(lab, "inbox", "factory", _msg("m-abcabcabcabc", "2026-10-23T16:30:00Z", None, "done"))
    return lab


def _ids(data: dict) -> list[str]:
    return [row["id"] for block in data["repos"] for row in block["rows"] if row["kind"] == "job"]


def test_failure_blocks_come_first_and_rows_are_newest_first(tmp_path: Path) -> None:
    data = today(_world(tmp_path), NOW, github_on=False)
    assert [block["repo"] for block in data["repos"]] == ["api", "web"]
    assert [block["failed"] for block in data["repos"]] == [True, False]
    assert _ids(data) == [
        "m-" + "c" * 12, "m-" + "b" * 12,
        "m-" + "d" * 12,
    ]
    failed = data["repos"][0]["rows"][1]
    assert failed["thread"] == "m-" + "a" * 12
    assert failed["member"] == "Planner" and failed["tool"] == "cursor" and failed["by_you"] is False
    yours = data["repos"][0]["rows"][0]
    assert yours["id"] == "m-" + "c" * 12 and yours["by_you"] is True and yours["tool"] == "you"
    assert data["generated"] == "2026-10-23T18:00:00Z" and data["github"] is False
    assert data["github_unreadable"] is False


def test_day_boundary_is_the_lab_zone(tmp_path: Path) -> None:
    lab = _world(tmp_path)
    present = set(_ids(today(lab, NOW, github_on=False)))
    assert "m-" + "c" * 12 in present and "m-" + "b" * 12 in present
    assert "m-" + "1" * 12 not in present and "m-" + "4" * 12 not in present
    # sent and picked_up are inside this lab day (the counts below) and absent from Today.
    assert "m-" + "2" * 12 not in present and "m-" + "3" * 12 not in present and "m-" + "e" * 12 not in present
    cards = {card["repo"]: card["jobs_today"] for card in repos(lab, NOW, github_on=False)["repos"]}
    assert cards["edge"] == {"sent": 1, "picked_up": 1, "done": 0, "failed": 0}
    assert cards["api"]["failed"] == 1 and cards["api"]["done"] == 1


def test_github_off_drops_pr_rows(tmp_path: Path) -> None:
    snap = {"prs": [{
        "repo": "api", "number": 42, "title": "Fix login", "updated": "2026-10-23T18:00:00Z",
        "checks": "red", "by_you": True,
    }]}
    data = today(_world(tmp_path), NOW, github_on=False, snapshot=snap)
    assert data["github"] is False
    assert data["github_unreadable"] is False
    assert [row["kind"] for block in data["repos"] for row in block["rows"]] == ["job"] * 3
    cards = repos(tmp_path / "lab", NOW, github_on=False, snapshot={**snap, "updated": None, "stale": True})
    assert cards["github"] is False and cards["github_unreadable"] is False
    assert all(card["prs"] == [] and card["checks"] == [] for card in cards["repos"])


def test_fake_snapshot_adds_pr_rows_with_by_you(tmp_path: Path) -> None:
    lab = _world(tmp_path)
    snap = {"updated": "2026-10-23T18:00:00Z", "stale": False, "prs": [
        {"repo": "web", "number": 42, "title": "Fix login", "updated": "2026-10-23T18:00:00Z",
         "checks": "green", "by_you": True, "state": "opened"},
        {"repo": "web", "number": 7, "title": "Older", "updated": "2026-10-22T18:00:00Z",
         "checks": "pending", "by_you": False},
        {"repo": "merge", "number": 9, "title": "Ship it", "updated": "2026-10-23T17:45:00Z",
         "checks": "red", "state": "merged", "by_you": False, "user": "you"},
    ]}
    data = today(lab, NOW, github_on=True, snapshot=snap)
    assert data["github"] is True and data["github_unreadable"] is False
    web = next(block for block in data["repos"] if block["repo"] == "web")
    opened = web["rows"][0]
    assert opened == {
        "kind": "pr", "ts": "2026-10-23T18:00:00Z", "state": "opened", "summary": "Fix login",
        "member": None, "tool": None, "checks": {"ci": "pass"}, "id": None, "thread": None,
        "pr": 42, "by_you": True,
    }
    assert all(row.get("pr") != 7 for block in data["repos"] for row in block["rows"])
    merged = next(block for block in data["repos"] if block["repo"] == "merge")
    assert merged["failed"] is True
    assert merged["rows"][0]["state"] == "merged" and merged["rows"][0]["by_you"] is False
    assert merged["rows"][0]["checks"] == {"ci": "fail"} and merged["rows"][0]["member"] == "you"
    cards = {card["repo"]: card for card in repos(lab, NOW, github_on=True, snapshot=snap)["repos"]}
    assert cards["web"]["prs"] == [
        {"number": 42, "title": "Fix login", "state": "opened", "ci": "green"},
        {"number": 7, "title": "Older", "state": "opened", "ci": "pending"},
    ]
    assert cards["merge"]["prs"] == [{"number": 9, "title": "Ship it", "state": "merged", "ci": "red"}]
    assert cards["web"]["checks"] == [] and cards["api"]["jobs_today"]["done"] == 1


def test_quiet_repo_is_listed_and_counts_match_now(tmp_path: Path) -> None:
    lab = _world(tmp_path)
    doc = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    doc["repos"] = {"quiet": "lab", "api": "lab"}
    (lab / "atlas.json").write_text(json.dumps(doc), encoding="utf-8")
    _plant(lab, "inbox", "hq", _msg("m-" + "9" * 12, "2026-10-22T16:00:00Z", "asked", "sent", kind="ask"))
    folder = lab / ".heliopause" / "cards" / "cursor"
    folder.mkdir(parents=True)
    (folder / "s.json").write_text(json.dumps({
        "repo": "seated", "session": "s", "state": "idle", "tool": "cursor", "ts": "2026-10-22T16:00:00Z",
    }), encoding="utf-8")
    zeros = {"sent": 0, "picked_up": 0, "done": 0, "failed": 0}
    body = repos(lab, NOW, github_on=False)
    found = {card["repo"]: card for card in body["repos"]}
    assert "quiet" in found and found["quiet"]["jobs_today"] == zeros
    assert found["quiet"]["prs"] == [] and found["quiet"]["checks"] == []
    assert found["asked"]["jobs_today"] == zeros and found["seated"]["jobs_today"] == zeros
    same = now(lab, NOW)["counts"]
    assert body["counts"] == same
    assert today(lab, NOW, github_on=False)["counts"] == same
    assert same["repos"] == len(found)


def test_github_unreadable(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    off = repos(lab, NOW, github_on=False, snapshot={"updated": None, "stale": True, "prs": []})
    good = repos(lab, NOW, github_on=True, snapshot={"updated": "2026-10-23T18:00:00Z", "stale": False, "prs": []})
    never = repos(lab, NOW, github_on=True, snapshot={"updated": None, "stale": False, "prs": []})
    stale = repos(lab, NOW, github_on=True, snapshot={"updated": "2026-10-23T17:00:00Z", "stale": True, "prs": []})
    assert off["github_unreadable"] is False
    assert good["github_unreadable"] is False
    assert never["github_unreadable"] is True and never["repos"] == []
    assert stale["github_unreadable"] is True


def test_unreadable_hides_pr_rows(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    doc = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    doc["repos"] = {"alpha": "lab", "zebra": "lab"}
    (lab / "atlas.json").write_text(json.dumps(doc), encoding="utf-8")
    _plant(lab, "done", "factory", _msg("m-" + "a" * 12, "2026-10-23T16:00:00Z", "zebra", "failed"))
    _plant(lab, "done", "factory", _msg("m-" + "b" * 12, "2026-10-23T17:00:00Z", "alpha", "done"))
    pr = {"repo": "alpha", "number": 8, "title": "Stale", "updated": "2026-10-23T18:00:00Z", "checks": "red"}
    stale = {"updated": "2026-10-23T17:00:00Z", "stale": True, "prs": [pr]}
    never = {"updated": None, "stale": False, "prs": [pr]}
    fresh = {"updated": "2026-10-23T18:00:00Z", "stale": False, "prs": [pr]}
    for snap in (stale, never):
        data = today(lab, NOW, github_on=True, snapshot=snap)
        assert data["github"] is True and data["github_unreadable"] is True
        kinds = [row["kind"] for block in data["repos"] for row in block["rows"]]
        assert kinds == ["job", "job"]
        body = repos(lab, NOW, github_on=True, snapshot=snap)
        assert body["github_unreadable"] is True
        assert all(card["prs"] == [] for card in body["repos"])
        assert [card["repo"] for card in body["repos"]] == ["zebra", "alpha"]
    shown = today(lab, NOW, github_on=True, snapshot=fresh)
    assert shown["github_unreadable"] is False
    assert any(row["kind"] == "pr" for block in shown["repos"] for row in block["rows"])
    fresh_cards = repos(lab, NOW, github_on=True, snapshot=fresh)
    assert fresh_cards["github_unreadable"] is False
    assert [card["repo"] for card in fresh_cards["repos"]] == ["alpha", "zebra"]
    assert fresh_cards["repos"][0]["prs"][0]["ci"] == "red"


def test_today_skips_sent_and_picked_up(tmp_path: Path) -> None:
    data = today(_world(tmp_path), NOW, github_on=False)
    states = [row["state"] for block in data["repos"] for row in block["rows"]]
    assert states and set(states) <= {"done", "failed"}
    assert "sent" not in states and "picked_up" not in states
    cards = {card["repo"]: card["jobs_today"] for card in repos(tmp_path / "lab", NOW, github_on=False)["repos"]}
    assert cards["web"]["sent"] == 1
    assert cards["edge"] == {"sent": 1, "picked_up": 1, "done": 0, "failed": 0}


def test_member_names_match_now_seats(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    doc = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    doc["roles"] = {"hq": "grok", "lab": "claude-code", "factory": ["cursor", "codex"]}
    (lab / "atlas.json").write_text(json.dumps(doc), encoding="utf-8")
    _plant(lab, "done", "lab", _msg("m-" + "a" * 12, "2026-10-23T16:00:00Z", "api", "done", tool="claude-code"))
    _plant(lab, "done", "factory", _msg(
        "m-" + "b" * 12, "2026-10-23T16:10:00Z", "api", "done", tool="cursor", role="factory",
    ))
    _plant(lab, "done", "factory", _msg(
        "m-" + "c" * 12, "2026-10-23T16:20:00Z", "api", "done", tool="codex", role="factory",
    ))
    _plant(lab, "done", "hq", _msg(
        "m-" + "d" * 12, "2026-10-23T16:30:00Z", "api", "done", tool="grok", role="hq",
    ))
    _plant(lab, "done", "factory", _msg(
        "m-" + "e" * 12, "2026-10-23T16:40:00Z", "api", "failed", tool="grok", role="factory",
    ))
    snap = {"updated": "2026-10-23T18:00:00Z", "stale": False, "prs": [
        {"repo": "api", "number": 1, "title": "From lab", "updated": "2026-10-23T18:00:00Z",
         "checks": "green", "role": "lab", "tool": "claude-code"},
        {"repo": "api", "number": 2, "title": "From factory", "updated": "2026-10-23T17:30:00Z",
         "checks": "pending", "role": "factory", "tool": "codex"},
        {"repo": "api", "number": 3, "title": "Unknown", "updated": "2026-10-23T17:00:00Z",
         "checks": "green", "role": "nope"},
    ]}
    data = today(lab, NOW, github_on=True, snapshot=snap)
    jobs = {row["id"]: row for block in data["repos"] for row in block["rows"] if row["kind"] == "job"}
    pulls = {row["pr"]: row for block in data["repos"] for row in block["rows"] if row["kind"] == "pr"}
    seats = now(lab, NOW)["seats"]
    assert jobs["m-" + "a" * 12]["member"] == "Planner" == seats["lab"][0]["name"]
    assert jobs["m-" + "b" * 12]["member"] == "Builder" == seats["factory"][0]["name"]
    assert jobs["m-" + "c" * 12]["member"] == "Builder 2" == seats["factory"][1]["name"]
    assert jobs["m-" + "d" * 12]["member"] == "Reviewer" == seats["hq"][1]["name"]
    assert jobs["m-" + "e" * 12]["member"] == "Builder"
    assert pulls[1]["member"] == "Planner" and pulls[2]["member"] == "Builder 2" and pulls[3]["member"] is None
    assert "lab" not in {row["member"] for row in jobs.values()}
    assert "factory" not in {row["member"] for row in jobs.values()}


def test_done_job_names_who_did_it(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    done = "m-" + "a" * 12
    failed = "m-" + "b" * 12
    raced = "m-" + "c" * 12
    _plant(lab, "done", "factory", _msg(done, "2026-10-23T16:00:00Z", "api", "done", tool="claude-code"))
    _plant(lab, "inbox", "lab", _msg(
        "m-" + "d" * 12, "2026-10-23T16:05:00Z", "api", "sent",
        kind="report", tool="cursor", role="factory", reply_to=done,
    ))
    _plant(lab, "done", "factory", _msg(
        failed, "2026-10-23T16:10:00Z", "api", "failed", tool="claude-code",
    ))
    _plant(lab, "done", "factory", _msg(raced, "2026-10-23T16:20:00Z", "api", "done", tool="claude-code"))
    _plant(lab, "inbox", "lab", _msg(
        "m-" + "e" * 12, "2026-10-23T16:40:00Z", "api", "sent",
        kind="report", tool="codex", role="factory", reply_to=raced,
    ))
    _plant(lab, "inbox", "lab", _msg(
        "m-" + "f" * 12, "2026-10-23T16:30:00Z", "api", "sent",
        kind="report", tool="grok", role="factory", reply_to=raced,
    ))
    jobs = {row["id"]: row for block in today(lab, NOW, github_on=False)["repos"] for row in block["rows"]}
    assert jobs[done]["member"] == "Builder" and jobs[done]["tool"] == "cursor" and jobs[done]["by_you"] is False
    assert jobs[failed]["member"] == "Planner" and jobs[failed]["tool"] == "claude-code"
    assert jobs[raced]["member"] == "Builder" and jobs[raced]["tool"] == "codex"


def test_missing_checks_are_none_not_pass(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    bare = {"repo": "api", "number": 4, "title": "Bare", "updated": "2026-10-23T18:00:00Z"}
    variants = (
        dict(bare),
        {**bare, "checks": None},
        {**bare, "checks": "none"},
        {**bare, "checks": "mystery"},
        {**bare, "checks": {}},
    )
    for item in variants:
        snap = {"updated": "2026-10-23T18:00:00Z", "stale": False, "prs": [item]}
        data = today(lab, NOW, github_on=True, snapshot=snap)
        row = next(row for block in data["repos"] for row in block["rows"] if row["kind"] == "pr")
        assert row["checks"] == {"ci": "none"}
        assert row["checks"]["ci"] != "pass"
        card = repos(lab, NOW, github_on=True, snapshot=snap)["repos"][0]
        assert card["prs"][0]["ci"] == "none"
        assert card["prs"][0]["ci"] != "pass"
    green = {"updated": "2026-10-23T18:00:00Z", "stale": False, "prs": [{**bare, "checks": "green"}]}
    row = next(row for block in today(lab, NOW, github_on=True, snapshot=green)["repos"] for row in block["rows"])
    assert row["checks"] == {"ci": "pass"}


def test_failed_repo_sorts_first(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    doc = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    doc["repos"] = {"alpha": "lab", "calm": "lab", "zebra": "lab"}
    (lab / "atlas.json").write_text(json.dumps(doc), encoding="utf-8")
    _plant(lab, "done", "factory", _msg("m-" + "a" * 12, "2026-10-23T16:00:00Z", "alpha", "done"))
    _plant(lab, "done", "factory", _msg("m-" + "b" * 12, "2026-10-23T16:00:00Z", "zebra", "failed"))
    plain = [card["repo"] for card in repos(lab, NOW, github_on=False)["repos"]]
    assert plain == ["zebra", "alpha", "calm"]
    snap = {"updated": "2026-10-23T18:00:00Z", "stale": False, "prs": [
        {"repo": "calm", "number": 1, "title": "Red", "updated": "2026-10-23T18:00:00Z", "checks": "red"},
        {"repo": "alpha", "number": 2, "title": "Green", "updated": "2026-10-23T18:00:00Z", "checks": "green"},
    ]}
    ordered = [card["repo"] for card in repos(lab, NOW, github_on=True, snapshot=snap)["repos"]]
    assert ordered == ["calm", "zebra", "alpha"]


def test_bad_message_files_are_skipped(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    good = _msg("m-" + "a" * 12, "2026-10-23T16:00:00Z", "api", "done")
    _plant(lab, "inbox", "factory", good)
    folder = lab / ".heliopause" / "inbox" / "factory"
    (folder / "notes.json").write_text("not json", encoding="utf-8")
    (folder / "list.json").write_text("[]", encoding="utf-8")
    (folder / "half.json").write_text("{", encoding="utf-8")
    (folder / "bad.json").write_bytes(b"\xff\xfe")
    (folder / "shape.json").write_text(json.dumps({"id": "nope"}), encoding="utf-8")
    (folder / "skip.txt").write_text("ignore me", encoding="utf-8")
    data = today(lab, NOW, github_on=False)
    assert _ids(data) == ["m-" + "a" * 12]
    assert data["repos"][0]["rows"][0]["summary"] == "job m-" + "a" * 12


def test_lab_only_is_404(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("ATLAS_LAB", None)
    env["ATLAS_ROOT"] = str(tmp_path)
    proc = subprocess.run([PY, "-c", _ABSENT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    found = json.loads(proc.stdout)
    assert found["/api/today"] == [404, {"error": "no lab"}]
    assert found["/api/repos"] == [404, {"error": "no lab"}]


def test_lab_route_uses_the_poller_snapshot(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["ATLAS_LAB"] = str(tmp_path / "lab")
    proc = subprocess.run([PY, "-c", _LIVE], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    found = json.loads(proc.stdout)
    assert found["off"][0] == 200 and found["off"][1]["github"] is False
    assert [row["kind"] for block in found["off"][1]["repos"] for row in block["rows"]] == ["job"]
    assert found["off"][1]["repos"][0]["rows"][0]["id"] == "m-" + "a" * 12
    opened = found["on"][1]
    assert found["on"][0] == 200 and opened["github"] is True
    prs = [row for block in opened["repos"] for row in block["rows"] if row["kind"] == "pr"]
    assert len(prs) == 1 and prs[0]["by_you"] is True and prs[0]["pr"] == 42 and prs[0]["state"] == "opened"
    card = found["repos"][1]
    assert found["repos"][0] == 200 and card["github"] is True and card["github_unreadable"] is False  # gh had a good poll
    assert card["repos"][0]["checks"] == []
    assert card["repos"][0]["prs"] == [{"number": 42, "title": "Fix login", "state": "opened", "ci": "green"}]


_ABSENT = r"""
import json, os, threading
from http.client import HTTPConnection
from pathlib import Path
from atlas.__main__ import make_server

root = Path(os.environ["ATLAS_ROOT"])
server = make_server(root, 0, watch=False)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    out = {}
    for path in ("/api/today", "/api/repos"):
        conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            out[path] = [resp.status, json.loads(resp.read())]
        finally:
            conn.close()
    print(json.dumps(out))
finally:
    server.shutdown()
    server.server_close()
"""


_LIVE = r"""
import json, os, threading
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path

lab = Path(os.environ["ATLAS_LAB"])
lab.mkdir(parents=True)
stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
(lab / "atlas.json").write_text(json.dumps({
    "v": 1, "owner": "Owner", "tz": "America/Los_Angeles",
    "nodes": [{"id": "lead", "label": "Lead", "role": "lab"}],
}), encoding="utf-8")
folder = lab / ".heliopause" / "inbox" / "factory"
folder.mkdir(parents=True)
job = {
    "id": "m-" + "a" * 12,
    "from": {"role": "lab", "tool": "cursor"},
    "to": {"role": "factory"},
    "kind": "job", "summary": "ship the read", "repo": "web", "state": "done",
    "reply_to": None, "ts": stamp,
    "checks": {"tests": "pass", "review": "none", "ci": "none"},
}
(folder / (job["id"] + ".json")).write_text(json.dumps(job), encoding="utf-8")
(folder / "bad.json").write_text("not json", encoding="utf-8")

from atlas.__main__ import make_server

class Fake:
    def snapshot(self):
        return {"updated": stamp, "stale": False, "prs": [{
            "repo": "web", "number": 42, "title": "Fix login", "updated": stamp,
            "checks": "green", "by_you": True,
        }]}

    def stop(self):
        pass

server = make_server(lab, 0, watch=False)
server.poller = Fake()
threading.Thread(target=server.serve_forever, daemon=True).start()

def req(path):
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return [resp.status, json.loads(resp.read())]
    finally:
        conn.close()

try:
    off = req("/api/today")
    body = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    body["github"] = {"enabled": True}
    (lab / "atlas.json").write_text(json.dumps(body), encoding="utf-8")
    print(json.dumps({"off": off, "on": req("/api/today"), "repos": req("/api/repos")}))
finally:
    server.shutdown()
    server.server_close()
"""

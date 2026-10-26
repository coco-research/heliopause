"""Factory job join and GET /api/jobs. Rows live under tmp_path, never a live home."""

import json
import threading
from datetime import date, datetime, timedelta, timezone
from http.client import HTTPConnection
from pathlib import Path

from atlas.__main__ import make_server
from atlas.collect import _NY
from atlas.jobs import jobs
from atlas.problems import detect

NOW = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)
DOT = "\u00b7"
KEYS = {
    "job", "type", "lane", "model", "story", "branch", "state",
    "started", "ended", "minutes", "class", "tried", "stuck",
}
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "font-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
DELIVERED = "11111111-1111-4111-8111-111111111111"
YESTERDAY = "22222222-2222-4222-8222-222222222222"
FAILED = "33333333-3333-4333-8333-333333333333"
PI = "44444444-4444-4444-8444-444444444444"
GROK = "55555555-5555-4555-8555-555555555555"
GEMINI = "66666666-6666-4666-8666-666666666666"
NVIDIA = "77777777-7777-4777-8777-777777777777"
QUEUED = "88888888-8888-4888-8888-888888888888"
NOLANE = "99999999-9999-4999-8999-999999999999"


def iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(root: Path, routes=(), dispatches=(), ends=()) -> None:
    folder = root / ".heliopause" / "inbox" / "lab" / "usage"
    folder.mkdir(parents=True, exist_ok=True)
    for name, rows in (("routes.jsonl", routes), ("dispatch.jsonl", dispatches), ("jobs-end.jsonl", ends)):
        if not rows:
            continue
        lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
        (folder / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _state():
    return {
        "nodes": {},
        "decisions": [],
        "bus": {"queue": {"inbox": 0, "running": 0, "failed_items": []}, "grok": {}, "lanes": {}},
    }


def _request(httpd, method, path, headers=None, body=None):
    port = httpd.server_address[1]
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    found = dict(resp.getheaders())
    assert found["Content-Security-Policy"] == _CSP
    assert found["X-Content-Type-Options"] == "nosniff"
    assert found["Referrer-Policy"] == "no-referrer"
    payload = (resp.status, found, resp.read())
    conn.close()
    return payload


def test_join_states_lanes_secrets_and_problems(tmp_path, monkeypatch):
    monkeypatch.setattr("atlas.collect._today", lambda: date(2026, 10, 13))
    route_at = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)
    end_at = datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc)
    yesterday = datetime(2026, 10, 13, 3, 30, tzinfo=timezone.utc)
    failed_at = datetime(2026, 10, 13, 16, 30, tzinfo=timezone.utc)
    pi_at = datetime(2026, 10, 13, 16, 50, tzinfo=timezone.utc)
    grok_at = datetime(2026, 10, 13, 16, 40, tzinfo=timezone.utc)
    gemini_at = datetime(2026, 10, 13, 17, 45, tzinfo=timezone.utc)
    nvidia_at = datetime(2026, 10, 13, 17, 51, tzinfo=timezone.utc)
    queued_at = datetime(2026, 10, 13, 17, 40, tzinfo=timezone.utc)
    nolane_at = datetime(2026, 10, 13, 17, 55, tzinfo=timezone.utc)
    _write(
        tmp_path,
        routes=[
            "",
            "not json",
            {"ts_utc": iso(route_at), "job": DELIVERED, "type": "review", "chosen": "queue",
             "story": "from the route", "fallback_used": False,
             "tried": [{"lane": "queue", "skip": "busy"}]},
            {"ts_utc": iso(queued_at), "job": QUEUED, "type": "note", "chosen": "ling", "story": "queue this"},
            {"ts_utc": iso(nolane_at), "job": NOLANE, "type": "build", "chosen": None, "story": "nowhere",
             "fallback_used": True, "cwd": "/Users/<name>/route",
             "tried": [
                 {"lane": "grok", "skip": "capped", "cwd": "/Users/<name>/nope"},
                 "bad",
                 {"lane": "gemini", "class": "gated"},
                 {"lane": "queue", "skip": "paused"},
             ]},
        ],
        dispatches=[
            "",
            "   ",
            "{",
            "[]",
            "1",
            '"hi"',
            {"ts_utc": iso(NOW), "lane": "gemini"},
            {"ts_utc": iso(NOW), "job": 5, "cwd": "/Users/<name>/from-bad-job"},
            {"job": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "cwd": "/Users/<name>/no-ts"},
            {"ts_utc": iso(route_at + timedelta(minutes=10)), "job": DELIVERED, "harness": "grok-build",
             "lane": "gemini", "model": "grok-4.7", "cwd": "/Users/<name>/secret",
             "branch": "feat/sk-abcdefghijklmnop", "story": "ship sk-abcdefghijklmnop now",
             "task": "read /Users/<name>/task", "result": "wrote /Users/<name>/out",
             "note": "keep-me-out"},
            {"ts_utc": iso(yesterday), "job": YESTERDAY, "lane": "zcode", "model": "glm",
             "cwd": "/Users/<name>/old", "branch": "old", "story": "old"},
            {"ts_utc": iso(failed_at), "job": FAILED, "lane": "grok-build", "model": "grok-4.7",
             "cwd": "/Users/<name>/fail", "branch": "fix", "story": "broke"},
            {"ts_utc": iso(pi_at), "job": PI, "lane": "queue-deepseek", "model": "deepseek",
             "cwd": "/Users/<name>/pi", "branch": "queue", "story": "queue"},
            {"ts_utc": iso(grok_at), "job": GROK, "lane": "grok-build", "model": "grok-4.7",
             "cwd": "/Users/<name>/grok", "branch": "grok", "story": "grok"},
            {"ts_utc": iso(gemini_at), "job": GEMINI, "lane": "gemini", "model": "gemini",
             "cwd": "/Users/<name>/gem", "branch": "gem", "story": "gem", "note": "keep-me-out"},
            {"ts_utc": iso(nvidia_at), "job": NVIDIA, "lane": "nvidia", "model": "glm",
             "cwd": "/Users/<name>/nv", "branch": "nv", "story": "nv"},
        ],
        ends=[
            {"ts_utc": iso(end_at), "job": DELIVERED, "lane": "gemini", "exit": 0, "minutes": 4.5, "class": "ok"},
            {"ts_utc": iso(yesterday), "job": YESTERDAY, "lane": "zcode", "exit": 0, "minutes": 2, "class": "ok"},
            {"ts_utc": iso(failed_at + timedelta(minutes=20)), "job": FAILED, "lane": "grok-build",
             "exit": 1, "minutes": 12.2, "class": "worker_failed"},
            "not json",
        ],
    )
    data = jobs(tmp_path, NOW)
    assert [row["job"] for row in data["jobs"]] == [
        NOLANE, NVIDIA, GEMINI, QUEUED, PI, GROK, FAILED, DELIVERED, YESTERDAY,
    ]
    by_id = {}
    for row in data["jobs"]:
        assert set(row) == KEYS
        by_id[row["job"]] = row
    assert by_id[DELIVERED] == {
        "job": DELIVERED, "type": "review", "lane": "gemini", "model": "grok-4.7",
        "story": "ship [redacted] now", "branch": "feat/[redacted]", "state": "delivered",
        "started": iso(route_at), "ended": iso(end_at), "minutes": 4.5, "class": "ok",
        "tried": [{"lane": "queue", "skip": "busy"}], "stuck": False,
    }
    assert by_id[YESTERDAY]["state"] == "delivered" and by_id[YESTERDAY]["lane"] == "zcode"
    assert by_id[FAILED]["state"] == "failed" and by_id[FAILED]["class"] == "worker_failed"
    assert by_id[FAILED]["minutes"] == 12.2 and by_id[FAILED]["stuck"] is False
    assert by_id[PI]["state"] == "running" and by_id[PI]["stuck"] is True and by_id[PI]["minutes"] == 70.0
    assert by_id[GROK]["stuck"] is True and by_id[GROK]["minutes"] == 80.0
    assert by_id[GEMINI]["state"] == "running" and by_id[GEMINI]["stuck"] is False and by_id[GEMINI]["minutes"] == 15.0
    assert by_id[NVIDIA]["stuck"] is False and by_id[NVIDIA]["minutes"] == 9.0
    assert by_id[QUEUED] == {
        "job": QUEUED, "type": "note", "lane": "ling", "model": None, "story": "queue this",
        "branch": None, "state": "queued", "started": iso(queued_at), "ended": None,
        "minutes": None, "class": None, "tried": None, "stuck": False,
    }
    assert by_id[NOLANE]["state"] == "no_lane" and by_id[NOLANE]["lane"] is None and by_id[NOLANE]["stuck"] is False
    assert by_id[NOLANE]["tried"] == [
        {"lane": "grok", "skip": "capped"},
        {"lane": "gemini", "class": "gated"},
        {"lane": "queue", "skip": "paused"},
    ]
    assert "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" not in by_id
    assert data["lanes"] == {
        "nvidia": {"running": 1, "delivered_today": 0, "failed_today": 0, "parked": 0, "failing": False},
        "gemini": {"running": 1, "delivered_today": 1, "failed_today": 0, "parked": 0, "failing": False},
        "queue-deepseek": {"running": 1, "delivered_today": 0, "failed_today": 0, "parked": 0, "failing": False},
        "grok-build": {"running": 1, "delivered_today": 0, "failed_today": 1, "parked": 0, "failing": False},
        "ling": {"running": 0, "delivered_today": 0, "failed_today": 0, "parked": 1, "failing": False},
    }
    blob = json.dumps(data)
    assert "/Users/" not in blob
    assert "sk-abcdefghijklmnop" not in blob
    assert "keep-me-out" not in blob
    assert "fallback_used" not in blob
    found = detect(_state(), {"nodes": {}}, tmp_path, NOW, disk=False)
    assert [(item["kind"], item["id"], item["owner"], item["headline"], item["severity"]) for item in found] == [
        ("pipe-parked", "pipe-parked:ling", "vp", f"Pipe parked {DOT} ling: 1 jobs waiting 20m", "high"),
        ("no-lane", f"no-lane:{NOLANE}", "dept-budget", "No lane for build: capped, gated", "high"),
        ("job-stuck", f"job-stuck:{GROK}", "factory-grok", f"Stuck {DOT} grok-build job running 80 min", "medium"),
        ("job-stuck", f"job-stuck:{PI}", "factory-queue", f"Stuck {DOT} queue-deepseek job running 70 min", "medium"),
    ]
    assert found[0]["since"] == iso(queued_at) and found[0]["evidence"] == ""
    assert found[0]["next_action"] == "Check the lane; jobs are waiting with no dispatch."
    assert found[1]["since"] == iso(nolane_at) and found[1]["evidence"] == ""
    assert found[1]["next_action"] == "Free a lane or wait for a cap to reset."
    assert found[2]["since"] == iso(grok_at) and found[3]["since"] == iso(pi_at)
    assert found[2]["next_action"] == "Check the lane; it is past its timeout."
    assert all(FAILED not in item["id"] for item in found)
    assert "paused" not in found[0]["headline"]


def test_stuck_starts_at_dispatch_not_the_route(tmp_path):
    route_at = NOW - timedelta(hours=2)
    disp_at = NOW - timedelta(minutes=5)
    _write(
        tmp_path,
        [{"ts_utc": iso(route_at), "job": "j", "type": "build", "chosen": "gemini", "story": "routed"}],
        [{"ts_utc": iso(disp_at), "job": "j", "lane": "gemini", "model": "gemini",
          "cwd": "/Users/<name>/secret", "branch": "b", "story": "ran"}],
    )
    job = jobs(tmp_path, NOW)["jobs"][0]
    assert job["state"] == "running" and job["stuck"] is False
    assert job["started"] == iso(route_at) and job["minutes"] == 5.0
    assert job["story"] == "ran" and job["lane"] == "gemini"
    assert "/Users/" not in json.dumps(job)


def test_latest_line_wins(tmp_path):
    job = "j"
    first = NOW - timedelta(minutes=30)
    _write(
        tmp_path,
        [
            {"ts_utc": iso(first), "job": job, "type": "old", "chosen": "queue"},
            {"ts_utc": iso(NOW - timedelta(minutes=10)), "job": job, "type": "new", "chosen": None},
        ],
        [
            {"ts_utc": iso(NOW - timedelta(minutes=20)), "job": job, "lane": "gemini", "model": "old-model"},
            {"ts_utc": iso(NOW - timedelta(minutes=5)), "job": job, "lane": "zcode", "model": "new-model"},
        ],
        [
            {"ts_utc": iso(NOW - timedelta(minutes=4)), "job": job, "lane": "zcode", "exit": 0, "minutes": 1, "class": "ok"},
            {"ts_utc": iso(NOW - timedelta(minutes=1)), "job": job, "lane": "zcode", "exit": 1, "minutes": 3.5, "class": "worker_failed"},
        ],
    )
    row = jobs(tmp_path, NOW)["jobs"][0]
    assert row["type"] == "new" and row["lane"] == "zcode" and row["model"] == "new-model"
    assert row["state"] == "failed" and row["class"] == "worker_failed" and row["minutes"] == 3.5
    assert row["ended"] == iso(NOW - timedelta(minutes=1)) and row["started"] == iso(first)
    assert row["stuck"] is False


def test_window_uses_the_first_timestamp(tmp_path):
    edge = NOW - timedelta(hours=24)
    older = edge - timedelta(seconds=1)
    _write(
        tmp_path,
        [
            {"ts_utc": iso(edge), "job": "edge", "type": "build", "chosen": "gemini"},
            {"ts_utc": iso(older), "job": "old", "type": "build", "chosen": "gemini"},
            {"ts_utc": iso(older), "job": "late-end", "type": "build", "chosen": "gemini"},
        ],
        ends=[
            {"ts_utc": iso(NOW - timedelta(minutes=1)), "job": "late-end", "lane": "gemini",
             "exit": 0, "minutes": 1, "class": "ok"},
        ],
    )
    assert [row["job"] for row in jobs(tmp_path, NOW)["jobs"]] == ["edge"]


def test_cap_keeps_newest_200_and_counts_the_rest(tmp_path):
    _write(tmp_path, dispatches=[
        {"ts_utc": iso(NOW - timedelta(minutes=i)), "job": f"cap-{i:03d}", "lane": "cursor",
         "cwd": "/Users/<name>/secret", "story": "", "branch": ""}
        for i in range(201)
    ])
    data = jobs(tmp_path, NOW)
    assert [row["job"] for row in data["jobs"]] == [f"cap-{i:03d}" for i in range(200)]
    assert data["lanes"]["cursor"]["running"] == 201
    assert "/Users/" not in json.dumps(data)


def test_missing_journals_are_empty(tmp_path):
    assert jobs(tmp_path, NOW) == {"jobs": [], "lanes": {}}
    assert jobs(tmp_path / "missing", NOW) == {"jobs": [], "lanes": {}}


def test_timeouts_and_stuck_owners(tmp_path):
    cases = [
        ("gemini", 30, False, None),
        ("gemini", 31, True, "factory-gemini"),
        ("zcode", 30, False, None),
        ("zcode", 31, True, "factory-zcode"),
        ("grok", 60, False, None),
        ("grok", 61, True, "factory-grok"),
        ("grok-build", 60, False, None),
        ("grok-build", 61, True, "factory-grok"),
        ("queue", 60, False, None),
        ("queue-deepseek", 61, True, "factory-queue"),
        ("nvidia", 10, False, None),
        ("nvidia", 11, True, "vp"),
        ("ling", 10, False, None),
        ("ling", 11, True, "vp"),
        ("cursor", 60, False, None),
        ("cursor", 61, True, "vp"),
        ("other", 60, False, None),
        ("other", 61, True, "vp"),
    ]
    for lane, minutes, stuck, owner in cases:
        root = tmp_path / f"{lane}-{minutes}"
        when = NOW - timedelta(minutes=minutes)
        _write(root, dispatches=[{
            "ts_utc": iso(when), "job": "job", "lane": lane, "model": "m",
            "cwd": "/Users/<name>/secret",
        }])
        row = jobs(root, NOW)["jobs"][0]
        assert row["stuck"] is stuck and row["state"] == "running" and row["minutes"] == float(minutes)
        found = [item for item in detect(_state(), {"nodes": {}}, root, NOW, disk=False) if item["kind"] == "job-stuck"]
        if stuck:
            assert len(found) == 1
            assert found[0]["owner"] == owner and found[0]["severity"] == "medium"
            assert found[0]["headline"] == f"Stuck {DOT} {lane} job running {minutes} min"
            assert found[0]["since"] == iso(when)
        else:
            assert found == []
        assert "/Users/" not in json.dumps(jobs(root, NOW))


def test_api_jobs(monkeypatch, tmp_path):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)

    monkeypatch.setattr("atlas.__main__.datetime", _Clock)
    monkeypatch.setattr("atlas.collect._today", lambda: date(2026, 10, 13))
    _write(tmp_path, dispatches=[{
        "ts_utc": iso(NOW - timedelta(minutes=5)),
        "job": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "lane": "gemini", "model": "gemini",
        "cwd": "/Users/<name>/secret", "branch": "b", "story": "s",
    }])
    server = make_server(tmp_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = _request(server, "GET", "/api/jobs")
        assert status == 200
        assert headers["Content-Type"] == "application/json"
        assert [key for key in headers if key.lower().startswith("access-control-")] == []
        data = json.loads(body)
        assert data == jobs(tmp_path, NOW.astimezone(_NY))
        assert data["jobs"][0]["state"] == "running" and data["lanes"]["gemini"]["running"] == 1
        assert "/Users/" not in body.decode()
        head_status, head_headers, head_body = _request(server, "HEAD", "/api/jobs")
        assert (head_status, head_body) == (200, b"")
        assert int(head_headers["Content-Length"]) == len(body)
        bad, _headers, _body = _request(server, "GET", "/api/jobs", {"Host": "evil.example"})
        assert bad == 403
        post, _headers, _body = _request(server, "POST", "/api/jobs", body=b"{}")
        assert post == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _route(job, lane, when):
    return {"ts_utc": iso(when), "job": job, "type": "build", "chosen": lane, "story": "s"}


def _disp(job, lane, when):
    return {"ts_utc": iso(when), "job": job, "lane": lane, "model": "m", "story": "s"}


def _end(job, lane, when, ok):
    return {
        "ts_utc": iso(when), "job": job, "lane": lane, "exit": 0 if ok else 1,
        "minutes": 1, "class": "ok" if ok else "worker_failed",
    }


def test_pipe_parked_failing_healthy_and_recovered(tmp_path):
    def at(minutes):
        return NOW - timedelta(minutes=minutes)

    _write(
        tmp_path,
        routes=[
            _route("park-old", "cursor", at(70)),
            _route("park-mid", "cursor", at(15)),
            _route("park-edge", "cursor", at(10)),
            _route("park-fresh", "cursor", at(9)),
            _route("fail-ok", "zcode", at(300)),
            _route("fail-1", "zcode", at(130)),
            _route("fail-2", "zcode", at(70)),
            _route("fail-3", "zcode", at(30)),
            _route("rec-1", "queue-deepseek", at(180)),
            _route("rec-2", "queue-deepseek", at(120)),
            _route("rec-ok", "queue-deepseek", at(40)),
            _route("health-ok", "gemini", at(60)),
            _route("health-fail", "gemini", at(90)),
            _route("health-q", "gemini", at(9)),
        ],
        dispatches=[
            _disp("stuck-cursor", "cursor", at(90)),
            _disp("stuck-zcode", "zcode", at(40)),
            _disp("stuck-gemini", "gemini", at(40)),
            _disp("fail-ok", "zcode", at(290)),
            _disp("fail-1", "zcode", at(125)),
            _disp("fail-2", "zcode", at(65)),
            _disp("fail-3", "zcode", at(25)),
            _disp("rec-1", "queue-deepseek", at(175)),
            _disp("rec-2", "queue-deepseek", at(115)),
            _disp("rec-ok", "queue-deepseek", at(35)),
            _disp("health-ok", "gemini", at(55)),
            _disp("health-fail", "gemini", at(85)),
        ],
        ends=[
            _end("fail-ok", "zcode", at(280), True),
            _end("fail-1", "zcode", at(120), False),
            _end("fail-2", "zcode", at(60), False),
            _end("fail-3", "zcode", at(20), False),
            _end("rec-1", "queue-deepseek", at(170), False),
            _end("rec-2", "queue-deepseek", at(110), False),
            _end("rec-ok", "queue-deepseek", at(30), True),
            _end("health-ok", "gemini", at(50), True),
            _end("health-fail", "gemini", at(80), False),
        ],
    )
    data = jobs(tmp_path, NOW)
    assert data["lanes"]["cursor"]["parked"] == 2 and data["lanes"]["cursor"]["failing"] is False
    assert data["lanes"]["zcode"]["failing"] is True
    assert data["lanes"]["gemini"]["parked"] == 0 and data["lanes"]["gemini"]["failing"] is False
    assert data["lanes"].get("queue-deepseek", {}).get("failing") is not True
    state = _state()
    state["bus"]["queue"]["failed_items"] = [
        {"id": "fail-3", "title": None, "model": None, "owner": "vp", "ts": iso(at(20))},
        {"id": "fail-2", "title": None, "model": None, "owner": "vp", "ts": iso(at(60))},
        {"id": "fail-1", "title": None, "model": None, "owner": "vp", "ts": iso(at(120))},
        {"id": "other-job", "title": None, "model": None, "owner": "brain-atlas", "ts": iso(at(10))},
    ]
    found = detect(state, {"nodes": {}}, tmp_path, NOW, disk=False)
    pipes = [item for item in found if item["kind"] in ("pipe-parked", "pipe-failing")]
    assert [(item["kind"], item["id"], item["owner"], item["headline"]) for item in pipes] == [
        ("pipe-failing", "pipe-failing:zcode", "factory-zcode", f"Pipe failing {DOT} zcode: last 3 jobs failed"),
        ("pipe-parked", "pipe-parked:cursor", "vp", f"Pipe parked {DOT} cursor: 2 jobs waiting 1h"),
    ]
    assert pipes[0]["since"] == iso(at(120)) and pipes[0]["severity"] == "high"
    assert pipes[1]["since"] == iso(at(70)) and pipes[1]["severity"] == "high"
    assert [item["id"] for item in found if item["kind"] == "job-stuck"] == ["job-stuck:stuck-gemini"]
    failed = {item["id"]: item.get("pipe") for item in found if item["kind"] == "job-failed"}
    assert failed == {
        "job-failed:fail-1": "zcode",
        "job-failed:fail-2": "zcode",
        "job-failed:fail-3": "zcode",
        "job-failed:other-job": None,
    }
    assert "/Users/" not in json.dumps(data) and "/Users/" not in json.dumps(found)


def test_failing_window_is_last_three_inside_six_hours(tmp_path):
    def ends(lane, newest):
        jobs = [f"{lane}-{i}" for i in range(3)]
        return (
            [_disp(job, lane, newest - timedelta(minutes=5 + i)) for i, job in enumerate(jobs)],
            [_end(job, lane, newest - timedelta(minutes=i * 10), False) for i, job in enumerate(jobs)],
        )

    inside = tmp_path / "inside"
    disp, ended = ends("nvidia", NOW - timedelta(hours=6))
    _write(inside, dispatches=disp, ends=ended)
    assert jobs(inside, NOW)["lanes"]["nvidia"]["failing"] is True

    outside = tmp_path / "outside"
    disp, ended = ends("ling", NOW - timedelta(hours=6, seconds=1))
    _write(outside, dispatches=disp, ends=ended)
    assert jobs(outside, NOW)["lanes"].get("ling", {}).get("failing") is not True


def test_state_exposes_lane_flags(monkeypatch, tmp_path):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)

    monkeypatch.setattr("atlas.__main__.datetime", _Clock)
    parked_at = NOW - timedelta(minutes=20)
    fail_at = NOW - timedelta(minutes=30)
    _write(
        tmp_path,
        routes=[_route("waiting", "cursor", parked_at)],
        dispatches=[_disp(f"bad-{i}", "zcode", fail_at - timedelta(minutes=i)) for i in range(3)],
        ends=[_end(f"bad-{i}", "zcode", fail_at - timedelta(minutes=i), False) for i in range(3)],
    )
    server = make_server(tmp_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _headers, body = _request(server, "GET", "/api/state")
        assert status == 200
        lanes = json.loads(body)["bus"]["lanes"]
        assert lanes["cursor"] == {"parked": 1, "failing": False}
        assert lanes["zcode"]["parked"] == 0 and lanes["zcode"]["failing"] is True
        assert "/Users/" not in body.decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

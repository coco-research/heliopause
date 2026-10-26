"""Budget collector and GET /api/budget. Rows live under tmp_path, never a live home."""

import json
import threading
from datetime import datetime, timedelta
from http.client import HTTPConnection

from atlas.__main__ import make_server
from atlas.budget import budget
from atlas.collect import _NY

NOW = datetime(2026, 10, 6, 20, 0, tzinfo=_NY)
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "font-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_IDS = ["claude", "grok", "glm", "deepseek", "apikeyfan", "default-pool"]


def _ts(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=_NY).timestamp()


def _line(stamp, **fields):
    return json.dumps({"ts": stamp, **fields})


def _caps(**pools):
    return {"allowances": {"pools": pools}}


def _save(root, lines, caps=None, now=NOW):
    usage = root / ".heliopause" / "inbox" / "lab" / "usage"
    usage.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    (usage / "meters.jsonl").write_text((text + "\n") if text else "", encoding="utf-8")
    if caps is not None:
        (usage / "caps.json").write_text(json.dumps(caps), encoding="utf-8")
    return budget(root, now)


def _pool(data, pool_id):
    return next(row for row in data["pools"] if row["id"] == pool_id)


def test_claude_baseline_counts(tmp_path):
    data = _save(tmp_path, [
        _line(_ts(6, 19), source="get_usage", claude={"weeklyPct": 18, "weeklyResetsAt": "2026-10-11T00:00:00Z"}, mystery=1),
        _line(_ts(5, 12), source="note", claude={"weeklyResetsAt": "early"}),
        _line(_ts(5, 18), source="get_usage", claude={"weeklyPct": 10, "weeklyResetsAt": "2026-10-04"}),
        _line(_ts(6, 10), source="vendor", claude={"weeklyPct": 14}),
        _line(_ts(6, 15), source="get_usage", claude={"weeklyResetsAt": ""}),
    ], _caps(claude={"daily": 100}))
    row = _pool(data, "claude")
    assert row["used"] == 8  # (14 - 10) + (18 - 14); the day-before baseline counts
    assert row["allowance"] == 100 and row["over"] is False and row["old"] is False
    assert row["as_of"] == json.loads(json.dumps(_ts(6, 19)))
    assert row["name"] == "Claude" and row["unit"] == "pts"
    assert row["extra"] == {"week_pct": 18, "resets_at": "2026-10-11T00:00:00Z"}


def test_counter_reset_adds_the_new_reading(tmp_path):
    data = _save(tmp_path, [
        _line(_ts(5, 18), source="vendor", claude={"weeklyPct": 40}),
        _line(_ts(6, 10), source="vendor", claude={"weeklyPct": 55}),
        _line(_ts(6, 15), source="get_usage", claude={"weeklyPct": 8}),
    ], _caps(claude={"daily": 1000}))
    assert _pool(data, "claude")["used"] == 23  # 15, then the drop adds 8


def test_remaining_spend_and_refill(tmp_path):
    data = _save(tmp_path, [
        _line(_ts(5, 18), providers={"commandcode1": {"monthlyRemaining": 50}}),
        _line(_ts(6, 9), providers={"commandcode1": {"monthlyRemaining": 40}}),
        _line(_ts(6, 12), providers={"commandcode1": {"monthlyRemaining": 60}}),
        _line(_ts(6, 18), providers={"commandcode1": {"monthlyRemaining": 45}}),
    ])
    row = _pool(data, "deepseek")
    assert row["used"] == 25  # 10 spent, refill adds 0, then 15
    assert row["unit"] == "usd"


def test_grok_tokens_and_target(tmp_path):
    data = _save(tmp_path, [
        _line(_ts(5, 18), providers={"grok": {"usedPercent": 1}}),
        _line(_ts(6, 10), providers={"grok": {"usedPercent": 2}}),
        _line(_ts(6, 11), source="vendor", supergrok={"pct": 3}),
        _line(_ts(6, 12), source="vendor", providers={"grok": {"usedPercent": 3.5}}, supergrok={"pct": 9}),
    ], _caps(grok={"daily_ceiling": 100_000_000, "daily_target_grok_build": 30}))
    row = _pool(data, "grok")
    assert row["used"] == int(2.5 * 12_700_000)  # 1 + 1 + 0.5 points; providers beat supergrok
    assert row["extra"] == {"target": 30}
    assert row["allowance"] == 100_000_000 and row["over"] is False


def test_glm_five_hour_full_is_over_while_daily_is_under(tmp_path):
    data = _save(tmp_path, [
        _line(_ts(5, 18), providers={"zai": {"windows": [
            {"label": "weekly", "used": 10}, {"label": "5-hour", "used": 1, "of": 10},
        ]}}),
        _line(_ts(6, 12), providers={"zai": {"windows": [
            {"label": "weekly", "used": 40},
            {"label": "other", "used": 99},
            {"label": "5-hour", "used": 8, "of": 8},
        ]}}),
    ], _caps(glm={"daily": 100}))
    row = _pool(data, "glm")
    assert row["used"] == 30 and row["allowance"] == 100 and row["over"] is True
    assert row["extra"] == {"five_hour": {"used": 8, "of": 8}}


def test_deepseek_daily_after(tmp_path):
    caps = _caps(deepseek_cc1={"daily": 2.5, "daily_after": {
        "2026-10-06T18:00:00-04:00": 4,
        "2026-10-07T09:00:00-04:00": 9,
    }})
    _save(tmp_path, [], caps)
    before = _pool(budget(tmp_path, datetime(2026, 10, 6, 12, tzinfo=_NY)), "deepseek")
    current = _pool(budget(tmp_path, NOW), "deepseek")
    after = _pool(budget(tmp_path, datetime(2026, 10, 7, 12, tzinfo=_NY)), "deepseek")
    assert (before["allowance"], before["extra"]["allowance_until"]) == (2.5, "2026-10-06T18:00:00-04:00")
    assert (current["allowance"], current["extra"]["allowance_until"]) == (4, "2026-10-07T09:00:00-04:00")
    assert after["allowance"] == 9 and after["extra"]["allowance_until"] is None


def test_usd_tolerance(tmp_path):
    def spend(delta):
        root = tmp_path / str(delta)
        data = _save(root, [
            _line(_ts(5, 18), providers={"apikeyfan": {"monthSpend": 0}}),
            _line(_ts(6, 12), providers={"apikeyfan": {"monthSpend": delta}}),
        ], _caps(apikeyfan={"daily": 0}))
        return _pool(data, "apikeyfan")

    low, high = spend(0.004), spend(0.01)
    assert low["used"] == round(0.004, 2) and low["over"] is False
    assert high["used"] == 0.01 and high["over"] is True


def test_default_pool_level_is_never_over(tmp_path):
    data = _save(tmp_path, [
        _line(_ts(6, 10), source="vendor", **{"default-pool": {"pct": 95}}),
        _line(_ts(6, 19), providers={"default-pool": {"usedPercent": 40}}),
    ], _caps(**{"default-pool": {"daily": 1}}))
    row = _pool(data, "default-pool")
    assert row["used"] == 40 and row["allowance"] is None and row["over"] is False
    assert row["unit"] == "weekly_pct" and row["extra"] == {} and row["name"] == "Default pool"


def test_legacy_grokbot_meters_and_caps_still_read(tmp_path):
    # Configs written before 0.6.1 name the pool "oldpool"; they keep working as default-pool.
    data = _save(tmp_path, [
        _line(_ts(6, 10), source="vendor", oldpool={"pct": 95}),
        _line(_ts(6, 19), providers={"oldpool": {"usedPercent": 40}}),
    ], _caps(oldpool={"daily": 1}))
    row = _pool(data, "default-pool")
    assert row["used"] == 40 and row["over"] is False
    assert "oldpool" not in [r["id"] for r in data["pools"]]


def test_no_readings(tmp_path):
    data = _save(tmp_path, [_line(_ts(6, 12), source="agy", note="nothing")])
    assert [row["id"] for row in data["pools"]] == _IDS
    for row in data["pools"]:
        assert row["used"] is None and row["as_of"] is None and row["old"] is False and row["over"] is False
    assert _pool(data, "claude")["extra"] == {"week_pct": None, "resets_at": None}


def test_old_when_latest_reading_is_over_two_hours(tmp_path):
    stale = (NOW - timedelta(seconds=7201)).timestamp()
    fresh = (NOW - timedelta(seconds=7200)).timestamp()
    old = _pool(_save(tmp_path, [_line(stale, source="vendor", claude={"weeklyPct": 3})]), "claude")
    assert old["used"] == 0 and old["old"] is True
    assert old["as_of"] == json.loads(json.dumps(stale))
    same = _pool(_save(tmp_path, [_line(fresh, source="vendor", claude={"weeklyPct": 3})]), "claude")
    assert same["old"] is False


def test_skips_bad_lines_unknown_keys_and_bools(tmp_path):
    raw = "\n".join([
        "",
        "   ",
        "not json",
        "[1, 2]",
        "null",
        _line(_ts(6, 11), source="get_usage", claude={"weeklyPct": True}, extra_junk={"a": 1}),
        _line(_ts(5, 18), source="vendor", claude={"weeklyPct": 10}, mystery="x"),
        _line(_ts(6, 16), source="get_usage", claude={"weeklyPct": 12}),
    ]) + "\n"
    usage = tmp_path / ".heliopause" / "inbox" / "lab" / "usage"
    usage.mkdir(parents=True)
    (usage / "meters.jsonl").write_text(raw, encoding="utf-8")
    assert _pool(budget(tmp_path, NOW), "claude")["used"] == 2


def test_missing_caps_means_no_allowance(tmp_path):
    data = _save(tmp_path, [
        _line(_ts(5, 18), source="vendor", claude={"weeklyPct": 1}),
        _line(_ts(6, 12), source="vendor", claude={"weeklyPct": 50}),
    ])
    row = _pool(data, "claude")
    assert row["used"] == 49 and row["allowance"] is None and row["over"] is False
    assert [row["id"] for row in data["pools"]] == _IDS
    assert all(item["allowance"] is None and item["over"] is False for item in data["pools"])
    (tmp_path / ".heliopause" / "inbox" / "lab" / "usage" / "caps.json").write_text("{", encoding="utf-8")
    again = budget(tmp_path, NOW)
    assert [row["id"] for row in again["pools"]] == _IDS
    assert all(item["allowance"] is None and item["over"] is False for item in again["pools"])


def test_listed_caps_keys_limit_rows(tmp_path):
    data = _save(tmp_path, [], _caps(claude={"daily": 10}, grok={"daily_ceiling": 1}))
    assert [row["id"] for row in data["pools"]] == ["claude", "grok"]


def test_missing_usage_dir(tmp_path):
    for root in (tmp_path, tmp_path / "missing"):
        data = budget(root, NOW)
        assert [row["id"] for row in data["pools"]] == _IDS
        assert [row["used"] for row in data["pools"]] == [None] * 6


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


def test_api_budget(monkeypatch, tmp_path):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            base = datetime(2026, 10, 6, 20, 0, tzinfo=_NY)
            return base if tz is None else base.astimezone(tz)

    monkeypatch.setattr("atlas.__main__.datetime", _Clock)
    _save(tmp_path, [
        _line(_ts(5, 18), source="get_usage", claude={"weeklyPct": 10}),
        _line(_ts(6, 19), source="vendor", claude={"weeklyPct": 14}),
    ], _caps(claude={"daily": 100}))
    server = make_server(tmp_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = _request(server, "GET", "/api/budget")
        assert status == 200
        assert headers["Content-Type"] == "application/json"
        assert [key for key in headers if key.lower().startswith("access-control-")] == []
        data = json.loads(body)
        assert data == budget(tmp_path, NOW)
        assert [row["id"] for row in data["pools"]] == ["claude"]
        head_status, head_headers, head_body = _request(server, "HEAD", "/api/budget")
        assert (head_status, head_body) == (200, b"")
        assert int(head_headers["Content-Length"]) == len(body)
        bad, _headers, _body = _request(server, "GET", "/api/budget", {"Host": "evil.example"})
        assert bad == 403
        post, _headers, _body = _request(server, "POST", "/api/budget", body=b"{}")
        assert post == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

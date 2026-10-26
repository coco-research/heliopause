"""Roadmap collector and GET /api/roadmap. Fixtures only."""

import json
import shutil
import threading
from datetime import date, datetime, timezone
from http.client import HTTPConnection
from pathlib import Path

from atlas.collect import redact
from atlas.__main__ import make_server
from atlas.roadmap import roadmap

HOME = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "home"
ROAD = HOME / ".heliopause" / "inbox" / "lab" / "roadmap"
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "font-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _data(today):
    found = roadmap(HOME, today)
    return found, {row["name"]: row for row in found["rows"]}


def test_week_counts_ignore_outside_and_uppercase():
    _found, rows = _data(date(2026, 10, 5))
    row = rows["Nimbus"]
    assert row["week1"] == {"done": 2, "total": 5}
    assert row["week2"] == {"done": 1, "total": 4}
    assert (row["done"], row["total"]) == (3, 9)
    assert rows["Engineering"]["week1"] == {"done": 0, "total": 1}
    assert rows["Engineering"]["week2"] == {"done": 1, "total": 1}
    # Table rows count like checkboxes; week 1 mixes both styles.
    assert rows["M0"]["week1"] == {"done": 1, "total": 3}
    assert rows["M0"]["week2"] == {"done": 0, "total": 0}


def test_table_rows_skip_header_separator_and_count_data():
    _found, rows = _data(date(2026, 10, 5))
    # Header and |---| separator are not items; statuses decide done.
    assert rows["M0"]["done"] == 1 and rows["M0"]["total"] == 3


def test_demo_line_allows_text_before_the_dot():
    _found, rows = _data(date(2026, 10, 5))
    assert rows["M0"]["next_demo"] == {"date": "2026-10-09", "text": "bake-off report", "past": False}
    _found, later = _data(date(2026, 10, 10))
    assert later["M0"]["next_demo"] == {
        "date": "2026-10-10",
        "text": "opens the dashboard",
        "past": False,
    }


def test_next_demo_future_today_past_and_none():
    _found, early = _data(date(2026, 10, 1))
    assert early["Nimbus"]["next_demo"] == {"date": "2026-10-08", "text": "first demo", "past": False}
    _found, on_day = _data(date(2026, 10, 13))
    assert on_day["Nimbus"]["next_demo"] == {"date": "2026-10-13", "text": "panel demo", "past": False}
    _found, late = _data(date(2026, 10, 20))
    assert late["Nimbus"]["next_demo"] == {"date": "2026-10-18", "text": "last demo", "past": True}
    assert early["Engineering"]["next_demo"] is None


def test_priority_is_first_whole_word_else_other():
    _found, rows = _data(date(2026, 10, 5))
    # "Nimbusx" is an earlier P0 cell. The whole word "nimbus" is the P2 row.
    assert rows["Nimbus"]["priority"] == "P2"
    assert rows["Engineering"]["priority"] == "Other"


def test_node_mapping():
    _found, rows = _data(date(2026, 10, 5))
    assert rows["Nimbus"]["node"] == "product-nimbus"
    assert rows["Engineering"]["node"] == "dept-engineering"


def test_sort_priority_then_name():
    found, _rows = _data(date(2026, 10, 5))
    assert [(row["priority"], row["name"]) for row in found["rows"]] == [
        ("P0", "M0"),
        ("P1", "Coco Mind Brain"),
        ("P1", "Coco Voice brain"),
        ("P1", "Design Dept"),
        ("P1", "Research & Innovation"),
        ("P2", "Atlas"),
        ("P2", "CoCo Courier"),
        ("P2", "Keyring"),
        ("P2", "Nimbus"),
        ("P2", "Nimbus Brain"),
        ("Other", "Engineering"),
    ]


def test_real_headers_priority_node_date_and_skip():
    found, rows = _data(date(2026, 10, 5))
    assert rows["Coco Voice brain"]["priority"] == "P1"
    assert rows["Coco Voice brain"]["node"] == "brain-voice"
    assert rows["Coco Voice brain"]["name"] == "Coco Voice brain"
    assert rows["Coco Mind Brain"]["priority"] == "P1"
    assert rows["Coco Mind Brain"]["node"] == "brain-si"
    assert rows["M0"]["priority"] == "P0"
    assert rows["M0"]["node"] == "brain-connect-m0"
    assert rows["M0"]["next_demo"] == {"date": "2026-10-09", "text": "bake-off report", "past": False}
    assert rows["M0"]["done"] == 1 and rows["M0"]["total"] == 3
    assert rows["Design Dept"]["priority"] == "P1"
    assert rows["Design Dept"]["node"] == "dept-design"
    assert rows["Research & Innovation"]["priority"] == "P1"
    assert rows["Research & Innovation"]["node"] == "dept-research"
    assert rows["Atlas"]["priority"] == "P2"
    assert rows["Atlas"]["node"] == "brain-atlas"
    assert rows["Keyring"]["priority"] == "P2"
    assert rows["Keyring"]["node"] == "brain-keyring"
    assert rows["CoCo Courier"]["priority"] == "P2"
    assert rows["CoCo Courier"]["node"] == "brain-courier"
    assert rows["Nimbus Brain"]["priority"] == "P2"
    assert rows["Nimbus Brain"]["node"] == "product-nimbus"
    files = [row["file"] for row in found["rows"]]
    assert "budget-check.md" not in files
    assert "m0-state.md" not in files
    assert "m0-merge-order.md" not in files


def test_mmdd_uses_today_year_iso_keeps_header_year():
    _found, rows = _data(date(2027, 3, 1))
    assert rows["M0"]["next_demo"]["date"] == "2027-10-09"
    assert rows["Nimbus"]["next_demo"]["date"] == "2026-10-18"


def test_skips_template_lab_and_other_markdown():
    found, _rows = _data(date(2026, 10, 5))
    assert [row["file"] for row in found["rows"]] == [
        "m0.md",
        "coco-mind.md",
        "coco-voice.md",
        "design.md",
        "research.md",
        "atlas.md",
        "courier.md",
        "keyring.md",
        "product-nimbus.md",
        "product-nimbus-brain.md",
        "engineering.md",
    ]
    raw = (ROAD / "ROADMAP.md").read_text(encoding="utf-8")
    assert found["lab"] == {"file": "ROADMAP.md", "text": redact(raw)[:16000]}


def test_missing_directory(tmp_path):
    assert roadmap(tmp_path, date(2026, 10, 5)) == {"rows": [], "lab": None}
    assert roadmap(tmp_path / "missing", date(2026, 10, 5)) == {"rows": [], "lab": None}


def test_no_roadmap_md_is_other(tmp_path):
    folder = tmp_path / ".heliopause" / "inbox" / "lab" / "roadmap"
    folder.mkdir(parents=True)
    shutil.copy(ROAD / "engineering.md", folder / "engineering.md")
    data = roadmap(tmp_path, date(2026, 10, 5))
    assert data["lab"] is None
    assert data["rows"][0]["priority"] == "Other"
    assert data["rows"][0]["name"] == "Engineering"


def test_redacts_token_in_text():
    _found, rows = _data(date(2026, 10, 5))
    raw = (ROAD / "product-nimbus.md").read_text(encoding="utf-8")
    assert rows["Nimbus"]["text"] == redact(raw)[:16000]
    assert "sk-abcdefghijkl" not in rows["Nimbus"]["text"]
    assert "[redacted]" in rows["Nimbus"]["text"]


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


def test_api_roadmap(monkeypatch):
    # 03:30 UTC is still the 13th in New York, and the 14th in UTC. Those dates pick different demos.
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            base = datetime(2026, 10, 14, 3, 30, tzinfo=timezone.utc)
            return base if tz is None else base.astimezone(tz)

    monkeypatch.setattr("atlas.__main__.datetime", _Clock)
    server = make_server(HOME, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = _request(server, "GET", "/api/roadmap")
        assert status == 200
        assert headers["Content-Type"] == "application/json"
        assert [key for key in headers if key.lower().startswith("access-control-")] == []
        data = json.loads(body)
        assert data == roadmap(HOME, date(2026, 10, 13))
        nimbus_row = next(row for row in data["rows"] if row["name"] == "Nimbus")
        assert nimbus_row["next_demo"]["date"] == "2026-10-13"
        assert nimbus_row["next_demo"]["past"] is False
        head_status, head_headers, head_body = _request(server, "HEAD", "/api/roadmap")
        assert (head_status, head_body) == (200, b"")
        assert int(head_headers["Content-Length"]) == len(body)
        bad, _headers, _body = _request(server, "GET", "/api/roadmap", {"Host": "evil.example"})
        assert bad == 403
        post, _headers, _body = _request(server, "POST", "/api/roadmap", body=b"{}")
        assert post == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

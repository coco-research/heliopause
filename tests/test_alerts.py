import json
import stat

from test_write import HTTPConnection, call, served, token_of

BASE = {"source": "lab", "kind": "disk", "severity": "info", "text": "Disk is full"}


def post(server, token, payload, path="/api/alerts"):
    status, raw = call(server, path, token=token, body=json.dumps(payload).encode())
    return status, json.loads(raw)


def get(server, path):
    with HTTPConnection("127.0.0.1", server.server_address[1], timeout=5) as conn:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()


def test_auth(tmp_path):
    raw = json.dumps(BASE).encode()
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        assert call(server, "/api/alerts", body=raw)[0] == 401
        assert call(server, "/api/alerts", token="nope", body=raw)[0] == 401
        assert call(server, "/api/alerts", token=token, body=raw, headers={"Origin": "https://evil.test"})[0] == 403


def test_rejects_bad_alerts(tmp_path):
    bad = (
        {**BASE, "severity": "panic"},
        {**BASE, "severity": "needs-someone"},
        {**BASE, "severity": []},
        {**BASE, "link": "//evil.example/x"},
        {**BASE, "link": "/a\udcffb"},
        {**BASE, "nope": 1},
        {**BASE, "text": "a" * 201},
        {**BASE, "text": " \x00\t"},
        {**BASE, "source": "Bad"},
        {**BASE, "link": "http://evil"},
        {**BASE, "link": "/a/../b"},
    )
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        assert all(post(server, token, payload)[0] == 400 for payload in bad)
        assert not (tmp_path / ".atlas" / "alerts.jsonl").exists()


def test_stores_one_validated_line(tmp_path):
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        payload = {**BASE, "severity": "needs-owner", "text": "a\x00b  c", "link": "/var/atlas"}
        status, body = post(server, token, payload)
        assert status == 201 and body["ok"] is True
        path = tmp_path / ".atlas" / "alerts.jsonl"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        line = json.loads(path.read_text(encoding="utf-8"))
        assert set(line) == {"ts", "id", "source", "kind", "severity", "text", "link", "state"}
        assert line["severity"] == "needs-owner" and line["text"] == "ab c" and line["state"] == "open"
        assert line["link"] == "/var/atlas" and body["id"] == line["id"]
        assert token not in path.read_text(encoding="utf-8")
        again = post(server, token, payload)
        assert again == (200, {"ok": True, "id": line["id"], "duplicate": True})
        assert path.read_text(encoding="utf-8").splitlines() == [json.dumps(line, ensure_ascii=False)]


def test_done_closes_once(tmp_path):
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        status, body = post(server, token, BASE)
        assert status == 201
        ident = body["id"]
        assert post(server, token, {}, f"/api/alerts/{ident}/done") == (200, {"ok": True})
        assert post(server, token, {}, f"/api/alerts/{ident}/done")[0] == 404
        listed = json.loads(get(server, "/api/alerts")[1])
        assert listed["open"] == []
        assert listed["recent"][0]["id"] == ident and listed["recent"][0]["state"] == "done"


def test_list_open_and_recent(tmp_path):
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        assert post(server, token, {**BASE, "text": "First alert text"})[0] == 201
        assert post(server, token, {**BASE, "text": "Second alert text"})[0] == 201
        listed = json.loads(get(server, "/api/alerts")[1])
        assert [row["text"] for row in listed["open"]] == ["Second alert text", "First alert text"]
        assert [row["state"] for row in listed["recent"]] == ["open", "open"]


def test_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr("atlas.alerts.ROTATE_BYTES", 1)
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        ids = []
        for text in ("note one", "note two", "note three"):
            status, body = post(server, token, {**BASE, "text": text})
            assert status == 201
            ids.append(body["id"])
        directory = tmp_path / ".atlas"
        assert (directory / "alerts.jsonl.1").is_file()
        kept = (directory / "alerts.jsonl").read_text(encoding="utf-8")
        kept += (directory / "alerts.jsonl.1").read_text(encoding="utf-8")
        assert ids[0] not in kept and ids[1] in kept and ids[2] in kept
        listed = json.loads(get(server, "/api/alerts")[1])
        assert {row["id"] for row in listed["recent"]} == {ids[1], ids[2]}


def test_needs_owner_is_a_problem(tmp_path):
    link = "https://github.com/coco-research/atlas/pull/1"
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        status, body = post(server, token, {
            "source": "lab", "kind": "review", "severity": "needs-owner",
            "text": "Owner should see this", "link": link,
        })
        assert status == 201
        assert post(server, token, {**BASE, "severity": "warn", "text": "Just a warning"})[0] == 201
        problems = json.loads(get(server, "/api/problems")[1])
        alerts = [item for item in problems if item["kind"] == "alert"]
        assert len(alerts) == 1
        item = alerts[0]
        assert item["owner"] == "owner" and item["id"] == f"alert:{body['id']}"
        assert item["headline"] == "Owner should see this"
        assert item["evidence"] == "Owner should see this" and item["next_action"] == link
        assert all("Just a warning" not in json.dumps(row) for row in problems)
        long = "A scheduled task has waited on one approval click for fourteen minutes now"
        assert post(server, token, {**BASE, "severity": "needs-owner", "text": long})[0] == 201
        heads = [p["headline"] for p in json.loads(get(server, "/api/problems")[1]) if p["kind"] == "alert"]
        assert "A scheduled task has waited on one approval click for\u2026" in heads
        brief = json.loads(get(server, "/api/brief?me=owner")[1])
        assert any(row["id"] == item["id"] for row in brief["problems"])


def test_token_shaped_text_is_redacted(tmp_path):
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        status, _ = post(server, token, {**BASE, "text": "runner key sk-abcdefghijklmnop leaked"})
        assert status == 201
        stored = (tmp_path / ".atlas" / "alerts.jsonl").read_text(encoding="utf-8")
        assert "sk-abcdefghijklmnop" not in stored and "[redacted]" in stored


def test_torn_last_line_does_not_swallow_the_next_alert(tmp_path):
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        (tmp_path / ".atlas").mkdir(exist_ok=True)
        path = tmp_path / ".atlas" / "alerts.jsonl"
        path.write_text('{"ts": 1, "id": "torn"', encoding="utf-8")
        assert post(server, token, BASE)[0] == 201
        status, raw = get(server, "/api/alerts")
        assert status == 200 and [a["text"] for a in json.loads(raw)["open"]] == ["Disk is full"]


def _nonce(server):
    status, raw = get(server, "/api/alerts")
    body = json.loads(raw)
    assert status == 200 and body["nonce"] == server.ui_nonce and body["nonce"]
    return body


def _ui(server, ident, nonce, **headers):
    port = server.server_address[1]
    hdrs = {
        "Origin": f"http://127.0.0.1:{port}",
        "X-Atlas-UI": nonce,
        "Sec-Fetch-Site": "same-origin",
    }
    hdrs.update(headers)
    status, raw = call(server, f"/api/alerts/{ident}/close", body=b"{}", headers=hdrs)
    return status, json.loads(raw)


def test_get_returns_a_fresh_nonce(tmp_path):
    with served(tmp_path) as server:
        first = _nonce(server)["nonce"]
        assert _nonce(server)["nonce"] == first
    with served(tmp_path) as server:
        assert _nonce(server)["nonce"] != first


def test_ui_close_drops_the_open_alert(tmp_path):
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        port = server.server_address[1]
        nonce = _nonce(server)["nonce"]
        for origin, text in (
            (f"http://127.0.0.1:{port}", "Disk is full"),
            (f"http://localhost:{port}", "Disk is fuller"),
        ):
            status, body = post(server, token, {**BASE, "text": text})
            assert status == 201
            closed, payload = _ui(server, body["id"], nonce, Origin=origin)
            assert closed == 200 and payload == {"ok": True}
        listed = _nonce(server)
        assert listed["open"] == []
        assert {row["state"] for row in listed["recent"]} == {"done"}


def test_ui_close_rejects_and_token_route_still_works(tmp_path):
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        status, body = post(server, token, BASE)
        assert status == 201
        ident, nonce = body["id"], _nonce(server)["nonce"]
        bare = call(
            server, f"/api/alerts/{ident}/close", token=token, body=b"{}",
            headers={"X-Atlas-UI": nonce},
        )
        assert bare[0] == 403 and json.loads(bare[1])["error"]
        assert _ui(server, ident, nonce, Origin="https://evil.test")[0] == 403
        assert _ui(server, ident, "not-the-nonce")[0] == 403
        assert _ui(server, ident, nonce, **{"Sec-Fetch-Site": "cross-site"})[0] == 403
        missing, payload = _ui(server, "missing", nonce)
        assert missing == 404 and payload["error"]
        assert [row["id"] for row in _nonce(server)["open"]] == [ident]
        assert post(server, token, {}, f"/api/alerts/{ident}/done") == (200, {"ok": True})
        assert _nonce(server)["open"] == []


def test_link_secret_is_redacted(tmp_path):
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        link = "https://github.com/coco-research/x?t=ghp_abcdefghijklmnop"
        assert post(server, token, {**BASE, "link": link})[0] == 201
        assert "ghp_abcdefghijklmnop" not in (tmp_path / ".atlas" / "alerts.jsonl").read_text(encoding="utf-8")


def test_closed_ts_is_the_done_lines_ts(tmp_path):
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        status, body = post(server, token, {**BASE, "text": "Stamp me when closed"})
        assert status == 201
        ident = body["id"]
        assert "closed_ts" not in json.loads(get(server, "/api/alerts")[1])["open"][0]
        assert post(server, token, {}, f"/api/alerts/{ident}/done") == (200, {"ok": True})
        done = json.loads((tmp_path / ".atlas" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        assert done["id"] == ident and done["state"] == "done"
        row = json.loads(get(server, "/api/alerts")[1])["recent"][0]
        assert row["state"] == "done" and row["closed_ts"] == done["ts"]
        assert isinstance(row["closed_ts"], int) and not isinstance(row["closed_ts"], bool)


def test_fold_stamps_a_closed_ts_only_from_a_real_done_ts():
    from atlas.alerts import _fold

    base = {"ts": 10, "id": "a", "source": "lab", "kind": "disk", "severity": "info", "text": "x", "state": "open"}
    assert "closed_ts" not in _fold([base])[0]
    assert _fold([base, {"ts": 99, "id": "a", "state": "done"}])[0]["closed_ts"] == 99
    assert "closed_ts" not in _fold([base, {"ts": True, "id": "a", "state": "done"}])[0]


def test_backslash_link_cf_text_and_severity_id(tmp_path):
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        assert post(server, token, {**BASE, "link": "/a\\b"})[0] == 400
        status, body = post(server, token, {**BASE, "text": "a\u202eb\u200bc"})
        assert status == 201
        path = tmp_path / ".atlas" / "alerts.jsonl"
        line = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        assert line["text"] == "abc" and body["id"] == line["id"]
        status, body = post(server, token, {**BASE, "text": "x\udcff\x9by"})
        assert status == 201 and body["id"]
        line = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        assert line["text"] == "xy"
        first = post(server, token, {**BASE, "severity": "info", "text": "same text here"})
        second = post(server, token, {**BASE, "severity": "warn", "text": "same text here"})
        assert first[0] == 201 and second[0] == 201 and first[1]["id"] != second[1]["id"]

import json
import shutil
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from atlas.__main__ import main, make_server

HOME = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "home"
SIGNAL = Path(__file__).resolve().parents[1] / "atlas" / "static" / "signal"
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "font-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


@pytest.fixture(scope="module")
def httpd():
    server = make_server(HOME, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _request(httpd, method, path, headers=None, body=None):
    port = httpd.server_address[1]
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    headers = resp.getheaders()
    found = dict(headers)
    assert found["Content-Security-Policy"] == _CSP
    assert found["X-Content-Type-Options"] == "nosniff"
    assert found["Referrer-Policy"] == "no-referrer"
    payload = (resp.status, headers, resp.read())
    conn.close()
    return payload


def _cors(headers):
    return [key for key, _value in headers if key.lower().startswith("access-control-")]


def test_get_state(httpd):
    status, headers, body = _request(httpd, "GET", "/api/state")
    assert status == 200
    assert dict(headers)["Content-Type"] == "application/json"
    data = json.loads(body)
    assert data["nodes"]["vp"]["status"] == "needs-owner"
    assert data["bus"]["hq"] == {"inbox": 4, "open": 2, "done": 2}
    assert _cors(headers) == []


def test_localhost_host_allowed(httpd):
    port = httpd.server_address[1]
    status, _headers, body = _request(httpd, "GET", "/api/state", {"Host": f"localhost:{port}"})
    assert status == 200
    assert json.loads(body)["nodes"]["factory-queue"]["status"] == "working"
    upper, _headers, _body = _request(httpd, "GET", "/api/state", {"Host": f"LOCALHOST:{port}"})
    assert upper == 200


def test_bad_host_403(httpd):
    status, headers, _body = _request(httpd, "GET", "/api/state", {"Host": "evil.example"})
    assert status == 403
    assert _cors(headers) == []
    wrong, _headers, _body = _request(httpd, "GET", "/api/state", {"Host": "127.0.0.1:1"})
    assert wrong == 403


def test_post_405(httpd):
    status, headers, _body = _request(httpd, "POST", "/api/state", body=b"{}")
    assert status == 405
    assert _cors(headers) == []


def test_options_405(httpd):
    status, headers, _body = _request(httpd, "OPTIONS", "/api/state")
    assert status == 405
    assert "Access-Control-Allow-Origin" not in {key for key, _value in headers}


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_other_writes_405(httpd, method):
    status, headers, _body = _request(httpd, method, "/api/state")
    assert status == 405
    assert _cors(headers) == []


def test_unknown_path_404(httpd):
    status, headers, _body = _request(httpd, "GET", "/nope")
    assert status == 404
    assert _cors(headers) == []


def test_index(httpd):
    status, headers, body = _request(httpd, "GET", "/map")
    assert status == 200
    assert dict(headers)["Content-Type"] == "text/html; charset=utf-8"
    assert b'class="brand">Heliopause</span>' in body and b'id="lede"' in body
    assert b"<h1>Atlas</h1>" not in body
    assert b"Coco Atlas v0" not in body


def test_head_has_no_body(httpd):
    status, headers, body = _request(httpd, "HEAD", "/api/state")
    assert status == 200
    assert body == b""
    assert int(dict(headers)["Content-Length"]) > 0
    assert _cors(headers) == []


@pytest.mark.parametrize(
    "path,content_type,rel",
    [
        ("/signal/fonts.css", "text/css; charset=utf-8", "fonts.css"),
        ("/signal/layout.css", "text/css; charset=utf-8", "layout.css"),
        ("/signal/signal.css", "text/css; charset=utf-8", "signal.css"),
        ("/signal/color.css", "text/css; charset=utf-8", "color.css"),
        ("/signal/fonts/schibsted-grotesk-latin-wght-normal.woff2", "font/woff2", "fonts/schibsted-grotesk-latin-wght-normal.woff2"),
        ("/signal/fonts/geist-mono-latin-wght-normal.woff2", "font/woff2", "fonts/geist-mono-latin-wght-normal.woff2"),
    ],
)
def test_signal_asset(httpd, path, content_type, rel):
    status, headers, body = _request(httpd, "GET", path)
    assert status == 200
    assert dict(headers)["Content-Type"] == content_type
    assert body == (SIGNAL / rel).read_bytes()
    assert b"Boss" not in body  # roles only in shipped assets (CTO review)
    assert _cors(headers) == []


def test_status_js(httpd):
    status, headers, body = _request(httpd, "GET", "/status.js")
    assert status == 200
    assert dict(headers)["Content-Type"] == "text/javascript; charset=utf-8"
    assert body == (Path(__file__).resolve().parents[1] / "atlas" / "static" / "status.js").read_bytes()
    missing, _headers, _body = _request(httpd, "GET", "/status.js/../collect.py")
    assert missing == 404


@pytest.mark.parametrize("path", ["/signal/x.css", "/signal/extra.css", "/signal/../collect.py", "/signal/fonts/"])
def test_signal_unknown_404(httpd, path):
    status, headers, _body = _request(httpd, "GET", path)
    assert status == 404
    assert _cors(headers) == []


def test_problems_and_owner_filter(httpd, monkeypatch):
    monkeypatch.setattr("atlas.problems.shutil.disk_usage", lambda _root: shutil._ntuple_diskusage(10**12, 0, 500 * 10**9))
    status, headers, body = _request(httpd, "GET", "/api/problems")
    assert status == 200
    assert dict(headers)["Content-Type"] == "application/json"
    assert _cors(headers) == []
    found = json.loads(body)
    assert "decision-waiting:vp" in [item["id"] for item in found]
    _status, _headers, body = _request(httpd, "GET", "/api/problems?owner=owner")
    mine = json.loads(body)
    assert mine and {item["owner"] for item in mine} == {"owner"}
    assert mine == [item for item in found if item["owner"] == "owner"]
    _status, _headers, body = _request(httpd, "GET", "/api/problems?owner=nobody")
    assert json.loads(body) == []
    head_status, _headers, head_body = _request(httpd, "HEAD", "/api/problems")
    assert (head_status, head_body) == (200, b"")
    bad, _headers, _body = _request(httpd, "GET", "/api/problems", {"Host": "evil.example"})
    assert bad == 403
    post, _headers, _body = _request(httpd, "POST", "/api/problems", body=b"{}")
    assert post == 405


def test_brief_shape_and_errors(httpd):
    status, headers, body = _request(httpd, "GET", "/api/brief?me=owner")
    assert status == 200
    assert dict(headers)["Content-Type"] == "application/json"
    assert _cors(headers) == []
    data = json.loads(body)
    assert data["me"] == "owner" and data["label"] == "Owner"
    assert data["inbox"] == []
    assert {item["owner"] for item in data["problems"]} == {"owner"}
    for path in ("/api/brief?me=nobody", "/api/brief"):
        status, headers, body = _request(httpd, "GET", path)
        assert status == 404
        assert dict(headers)["Content-Type"] == "application/json"
        assert json.loads(body) == {"error": "unknown node"}
    head_status, _headers, head_body = _request(httpd, "HEAD", "/api/brief?me=vp")
    assert (head_status, head_body) == (200, b"")
    bad, _headers, _body = _request(httpd, "GET", "/api/brief?me=vp", {"Host": "evil.example"})
    assert bad == 403
    post, _headers, _body = _request(httpd, "POST", "/api/brief", body=b"{}")
    assert post == 405


def test_who_scores_and_empty(httpd):
    _status, _headers, body = _request(httpd, "GET", "/api/who?q=Design+signal%21")
    found = json.loads(body)
    assert found[0]["node"] == "dept-design" and found[0]["score"] == 2
    assert set(found[0]) == {"node", "label", "state", "headline", "ts", "score"}
    for path in ("/api/who", "/api/who?q=", "/api/who?q=zzzz"):
        assert json.loads(_request(httpd, "GET", path)[2]) == []
    bad, _headers, _body = _request(httpd, "GET", "/api/who?q=ci", {"Host": "evil.example"})
    assert bad == 403


def test_directory_covers_every_node_id():
    from atlas.collect import NODE_IDS, map_path

    topics = json.loads(map_path("directory.json").read_text(encoding="utf-8"))
    assert set(topics) == set(NODE_IDS)
    assert all(words and all(word == word.lower() for word in words) for words in topics.values())


def test_demo_skips_the_poller(tmp_path, monkeypatch):
    created = []

    class Spy:
        def __init__(self, *args, **kwargs):
            created.append("init")

        def start(self):
            created.append("start")

        def stop(self):
            pass

    monkeypatch.setattr("atlas.__main__.Poller", Spy)
    monkeypatch.setattr(
        "atlas.problems.shutil.disk_usage",
        lambda _root: shutil._ntuple_diskusage(10**12, 0, 5 * 10**9),
    )
    server = make_server(tmp_path, 0, demo=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert created == []
        assert server.poller is None
        status, _headers, body = _request(server, "GET", "/api/state")
        assert status == 200
        assert json.loads(body)["github"] == {"updated": None, "stale": False, "prs": []}
        status, _headers, body = _request(server, "GET", "/api/problems")
        assert status == 200
        assert all(item["kind"] != "disk-low" for item in json.loads(body))
    finally:
        server.shutdown()
        server.server_close()


def test_demo_brief_skips_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "atlas.problems.shutil.disk_usage",
        lambda _root: shutil._ntuple_diskusage(10**12, 0, 5 * 10**9),
    )
    server = make_server(tmp_path, 0, demo=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _headers, body = _request(server, "GET", "/api/brief?me=dept-engineering")
        assert status == 200
        assert all(item["kind"] != "disk-low" for item in json.loads(body)["problems"])
    finally:
        server.shutdown()
        server.server_close()


def test_main_passes_demo(tmp_path, monkeypatch):
    seen = {}

    def spy(root, port, watch=True, owner_name="Owner", demo=False, ring=False):
        seen["demo"] = demo
        seen["ring"] = ring
        seen["port"] = port
        httpd = make_server(root, 0, watch=False, owner_name=owner_name, demo=True)
        httpd.serve_forever = lambda *args, **kwargs: None
        seen["httpd"] = httpd
        return httpd

    monkeypatch.setattr("atlas.__main__.make_server", spy)
    try:
        main(["--port", "4390", "--root", str(tmp_path), "--demo"])
        assert seen["demo"] is True
        assert seen["port"] == 4390
        assert seen["ring"] is False  # a demo never rings
    finally:
        seen["httpd"].server_close()

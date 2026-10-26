import json
import os
import socket
import stat
import threading
from contextlib import contextmanager
from datetime import datetime
from http.client import HTTPConnection as _HTTPConnection
from zoneinfo import ZoneInfo

import pytest

from atlas.__main__ import main, make_server
from atlas.collect import snapshot
from atlas.write import _append, ensure_token


class HTTPConnection(_HTTPConnection):
    # stdlib HTTPConnection is not a context manager. These two close the socket on every path.
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

NY = ZoneInfo("America/New_York")
SEP = " \u00b7 "
LINES = (
    f"2026-10-01 09:00 ET{SEP}VP{SEP}working{SEP}One\n"
    f"2026-10-01 09:01 ET{SEP}VP{SEP}done{SEP}Two\n"
).encode()
OK = json.dumps({"state": "working", "headline": "Hello there", "details": "more here"}).encode()

def board(root, raw):
    path = root / ".heliopause" / "inbox" / "lab" / "board.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    return path

@contextmanager
def served(root):
    server = make_server(root, 0, watch=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

def call(server, path="/api/post", *, token=None, body=None, headers=None):
    with HTTPConnection("127.0.0.1", server.server_address[1], timeout=5) as conn:
        hdrs = {}
        if token is not None:
            hdrs["X-Atlas-Token"] = token
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        if headers:
            hdrs.update(headers)
        conn.request("POST", path, body=body, headers=hdrs)
        resp = conn.getresponse()
        payload, status = resp.read(), resp.status
        return status, payload

def token_of(root):
    return (root / ".atlas" / "token").read_text(encoding="utf-8")

def test_auth_matrix(tmp_path):
    path = board(tmp_path, LINES)
    with served(tmp_path) as server:
        token = token_of(tmp_path)

        def expect(status, **kwargs):
            got, body = call(server, token=kwargs.pop("token", token), **kwargs)
            assert got == status and token.encode() not in body
            return body

        expect(401, token=None, body=OK)
        expect(401, token="wrong-token", body=OK)
        expect(403, body=OK, headers={"Origin": "http://127.0.0.1"})
        expect(403, body=OK, headers={"Host": "evil.example"})
        expect(415, body=OK, headers={"Content-Type": "text/plain"})
        # http.client adds Content-Length: 0 on a body-less POST, so this one goes out raw.
        port = server.server_address[1]
        raw = (
            f"POST /api/post HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"X-Atlas-Token: {token}\r\nContent-Type: application/json\r\n\r\n"
        ).encode()
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(raw)
            sock.shutdown(socket.SHUT_WR)
            data = b"".join(iter(lambda: sock.recv(4096), b""))
        assert int(data.split(b" ", 2)[1]) == 411 and token.encode() not in data
        expect(413, body=b"x" * 8193)
        expect(400, body=b"x" * 8192)
        assert json.loads(expect(400, body=b"[]"))["error"]
        bad = (
            {"state": "nope", "headline": "Hello there", "details": "more here"},
            {"state": "working", "headline": "", "details": "more here"},
            {"state": "working", "headline": "x" * 61, "details": "more here"},
            {"state": "working", "headline": f"bad{SEP}sep", "details": "more here"},
            {"state": "working", "headline": "Hello there", "details": "has\nnewline"},
            {"state": "working", "headline": "Hello\u202e there", "details": "more here"},
            {"state": "working", "headline": "Hello there", "details": "more here", "extra": 1},
        )
        for payload in bad:
            err = json.loads(expect(400, body=json.dumps(payload).encode()))
            assert set(err) == {"error"} and err["error"]
        assert path.read_bytes() == LINES
        (tmp_path / ".atlas" / "actor").write_text("not-a-node\n", encoding="utf-8")
        expect(403, body=OK)
        assert path.read_bytes() == LINES
        expect(405, path="/api/state", body=OK)

def test_append_and_missing_newline(tmp_path):
    path = board(tmp_path, LINES)
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        status, body = call(server, token=token, body=OK)
        data = json.loads(body)
        line = data["line"]
        assert status == 201 and data["ok"] is True and token not in body.decode()
        assert path.read_bytes() == LINES + (line + "\n").encode()
        stamp, name, state, headline, details = line.split(SEP)
        assert [name, state, headline, details] == ["VP", "working", "Hello there", "more here"]
        got = datetime.strptime(stamp, "%Y-%m-%d %H:%M ET").replace(tzinfo=NY)
        assert abs((datetime.now(NY) - got).total_seconds()) < 120
        sixty = "Ship atlas " + "z" * 49
        status, body = call(
            server, token=token,
            body=json.dumps({"state": "done", "headline": sixty, "details": "more here"}).encode(),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        line2 = json.loads(body)["line"]
        assert status == 201 and line2.split(SEP)[1:] == ["VP", "done", sixty, "more here"]
        assert path.read_bytes() == LINES + (line + "\n").encode() + (line2 + "\n").encode()
    original = LINES[:-1]
    path.write_bytes(original)
    with served(tmp_path) as server:
        status, body = call(server, token=token_of(tmp_path), body=OK)
        line = json.loads(body)["line"]
        assert status == 201
        assert path.read_bytes() == original + b"\n" + (line + "\n").encode()
    path.write_bytes(b"")
    with served(tmp_path) as server:
        status, body = call(server, token=token_of(tmp_path), body=OK)
        assert status == 201 and path.read_bytes() == (json.loads(body)["line"] + "\n").encode()

def test_short_write_appends_the_full_line(tmp_path, monkeypatch):
    path = board(tmp_path, LINES)
    real = os.write

    def short(fd, data):
        return real(fd, memoryview(data)[:7])

    monkeypatch.setattr("atlas.write.os.write", short)
    status, body = _append(tmp_path, "VP", "working", "Hello there", "more here")
    line = body["line"]
    assert status == 201 and len(line.encode()) > 7
    assert path.read_bytes() == LINES + (line + "\n").encode()

def test_missing_board_503(tmp_path):
    with served(tmp_path) as server:
        status, body = call(server, token=token_of(tmp_path), body=OK)
        assert status == 503 and json.loads(body)["error"]
        assert not (tmp_path / ".heliopause" / "inbox" / "lab" / "board.md").exists()

def test_concurrent_appends(tmp_path):
    path = board(tmp_path, LINES)
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        codes = []

        def one(i):
            status, _body = call(server, token=token, body=json.dumps(
                {"state": "working", "headline": f"note {i:02d}", "details": "batch"}).encode())
            codes.append(status)

        threads = [threading.Thread(target=one, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        raw = path.read_bytes()
        assert codes == [201] * 20 and raw.startswith(LINES)
        found = []
        for line in raw[len(LINES):].decode().splitlines():
            stamp, name, state, headline, details = line.split(SEP)
            assert name == "VP" and state == "working" and details == "batch"
            datetime.strptime(stamp, "%Y-%m-%d %H:%M ET")
            found.append(headline)
        assert sorted(found) == [f"note {i:02d}" for i in range(20)]

def test_token_mode_survives_restart(tmp_path):
    board(tmp_path, LINES)
    server = make_server(tmp_path, 0, watch=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        token_path = tmp_path / ".atlas" / "token"
        token = token_path.read_text(encoding="utf-8")
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700
        for kwargs in ({"token": "nope", "body": b"{}"}, {"token": token, "body": b"[]"}):
            assert token.encode() not in call(server, **kwargs)[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    again = make_server(tmp_path, 0, watch=False)
    try:
        assert token_of(tmp_path) == token
        assert stat.S_IMODE((tmp_path / ".atlas" / "token").stat().st_mode) == 0o600
    finally:
        again.server_close()

def _raw_post(port, headers, body=b""):
    with HTTPConnection("127.0.0.1", port, timeout=5) as conn:
        conn.putrequest("POST", "/api/post")
        for name, value in headers:
            conn.putheader(name, value)
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        return resp.status, resp.read()

def test_latin1_token_byte_is_401(tmp_path):
    board(tmp_path, LINES)
    with served(tmp_path) as server:
        port = server.server_address[1]
        # putheader takes the raw latin-1 byte; a str would be the same bug.
        status, body = _raw_post(port, [
            ("X-Atlas-Token", b"\xff"),
            ("Content-Type", "application/json"),
        ], OK)
        assert status == 401 and token_of(tmp_path).encode() not in body

def test_superscript_content_length_is_411(tmp_path):
    board(tmp_path, LINES)
    with served(tmp_path) as server:
        port = server.server_address[1]
        token = token_of(tmp_path)
        with HTTPConnection("127.0.0.1", port, timeout=5) as conn:
            conn.putrequest("POST", "/api/post")
            conn.putheader("X-Atlas-Token", token)
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", "²".encode("latin-1"))
            conn.endheaders()
            resp = conn.getresponse()
            status, body = resp.status, resp.read()
        assert status == 411 and token.encode() not in body

def test_atlas_file_reports_token_setup(tmp_path, capsys):
    (tmp_path / ".atlas").write_bytes(b"x")
    try:
        main(["--root", str(tmp_path), "--port", "0"])
    except SystemExit as err:
        assert err.code == 1
    else:
        raise AssertionError("main returned")
    err_text = capsys.readouterr().err
    assert "can't listen" not in err_text
    assert err_text.startswith(f"atlas: cannot set up the write token in {tmp_path / '.atlas'}: ")
    assert err_text.endswith(".atlas is not a folder\n")  # labfs names the path and the reason

def test_surrogate_and_deep_json_are_400(tmp_path):
    path = board(tmp_path, LINES)
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        lone = json.dumps({"state": "working", "headline": "bad \ud800 byte", "details": "more here"}).encode()
        assert call(server, token=token, body=lone)[0] == 400
        assert call(server, token=token, body=b"[" * 8000)[0] == 400
    assert path.read_bytes() == LINES

def test_details_required_and_headline_must_be_prose(tmp_path):
    path = board(tmp_path, LINES)
    with served(tmp_path) as server:
        token = token_of(tmp_path)

        def reason(payload):
            status, body = call(server, token=token, body=json.dumps(payload).encode())
            assert status == 400 and token.encode() not in body
            return json.loads(body)["error"]

        base = {"state": "working", "headline": "Hello there"}
        assert reason(base) == "bad details"
        assert reason({**base, "details": ""}) == "bad details"
        assert reason({**base, "headline": "Hello", "details": "more here"}) == "bad headline"
        assert reason({**base, "headline": "see ~/src today", "details": "more here"}) == "bad headline"
    assert path.read_bytes() == LINES

def test_posted_line_round_trips_through_collect(tmp_path):
    board(tmp_path, b"")
    headline, details = "Hello there", "more here"
    with served(tmp_path) as server:
        status, _body = call(server, token=token_of(tmp_path), body=json.dumps(
            {"state": "working", "headline": headline, "details": details}).encode())
        assert status == 201
    node = snapshot(tmp_path)["nodes"]["vp"]
    assert node["headline"] == headline and node["details"] == details

def test_empty_token_file_refuses_to_start(tmp_path):
    (tmp_path / ".atlas").mkdir(mode=0o700)
    (tmp_path / ".atlas" / "token").write_bytes(b"")
    with pytest.raises(OSError, match="empty or unreadable"):
        ensure_token(tmp_path)

def test_ensure_token_leaves_only_the_token_file(tmp_path):
    token = ensure_token(tmp_path)
    atlas = tmp_path / ".atlas"
    assert sorted(p.name for p in atlas.iterdir()) == ["token"]
    assert (atlas / "token").read_text(encoding="utf-8") == token

def test_link_race_returns_the_peer_token(tmp_path, monkeypatch):
    peer = "peer-token-value"

    def lose(_src, dst, **_fds):
        (tmp_path / ".atlas" / dst).write_text(peer, encoding="utf-8")
        raise FileExistsError

    monkeypatch.setattr("atlas.labfs.os.link", lose)
    assert ensure_token(tmp_path) == peer
    assert sorted(p.name for p in (tmp_path / ".atlas").iterdir()) == ["token"]

def test_actor_fifo_is_403(tmp_path):
    path = board(tmp_path, LINES)
    with served(tmp_path) as server:
        os.mkfifo(tmp_path / ".atlas" / "actor")
        status, body = call(server, token=token_of(tmp_path), body=OK)
        assert status == 403 and token_of(tmp_path).encode() not in body
    assert path.read_bytes() == LINES

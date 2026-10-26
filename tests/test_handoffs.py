import json
import os
import stat
from http.client import HTTPConnection

from test_write import board, call, served, token_of

from atlas.collect import _SEP, snapshot
from atlas.handoffs import fold

def get(server, path):
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()

def post(server, token, op, payload):
    status, body = call(server, f"/api/{op}", token=token, body=json.dumps(payload).encode())
    return status, json.loads(body)

def test_auth_matrix(tmp_path):
    body = json.dumps({"to": "hq", "title": "Hello there"}).encode()
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        assert call(server, "/api/handoff", body=body)[0] == 401
        assert call(server, "/api/handoff", token=token, body=body, headers={"Origin": "http://127.0.0.1"})[0] == 403
        assert call(server, "/api/handoff", token=token, body=body, headers={"Content-Type": "text/plain"})[0] == 415
        assert not (tmp_path / ".heliopause" / "inbox").exists()

def test_validation(tmp_path):
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        bad = (
            {"to": "nope", "title": "Hello there"},
            {"to": "vp", "title": "Hello there"},
            {"to": "hq", "title": "Hello"},
            {"to": "hq", "title": f"Hello{_SEP}there"},
            {"to": "hq", "title": "Hello there", "extra": 1},
        )
        assert all(post(server, token, "handoff", payload)[0] == 400 for payload in bad)
        assert not (tmp_path / ".heliopause" / "inbox").exists()

def test_transitions_mirror_and_modes(tmp_path):
    path = board(tmp_path, b"")
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        log = tmp_path / ".heliopause" / "inbox" / "atlas" / "handoffs.jsonl"
        status, data = post(server, token, "handoff", {"to": "dept-engineering", "title": "Ship the queue", "details": "notes"})
        ident = data["id"]
        parts = data["line"].split(_SEP)
        assert status == 201 and len(parts) == 5 and token not in json.dumps(data)
        assert parts[1:] == ["VP", "working", "Handed off to Engineering", f"Ship the queue ({ident})"]
        assert path.read_bytes() == (data["line"] + "\n").encode()
        assert snapshot(tmp_path)["nodes"]["vp"]["headline"] == "Handed off to Engineering"
        assert stat.S_IMODE(log.stat().st_mode) == 0o600 and stat.S_IMODE(log.parent.stat().st_mode) == 0o700
        previous = b""

        def grew():
            nonlocal previous
            raw = log.read_bytes()
            assert raw.startswith(previous) and len(raw) > len(previous)
            previous = raw

        grew()
        assert post(server, token, "claim", {"id": ident})[1] == {"ok": True, "id": ident, "state": "claimed"}
        grew()
        assert post(server, token, "done", {"id": ident})[1] == {"ok": True, "id": ident, "state": "done"}
        grew()
        frozen = log.read_bytes()
        assert post(server, token, "claim", {"id": ident}) == (409, {"error": "state"})
        assert post(server, token, "claim", {"id": "h-00000000"}) == (404, {"error": "unknown id"})
        assert log.read_bytes() == frozen
        second = post(server, token, "handoff", {"to": "hq", "title": "Route the queue"})[1]["id"]
        assert post(server, token, "claim", {"id": second})[0] == 200
        assert post(server, token, "claim", {"id": second})[0] == 409
        third = post(server, token, "handoff", {"to": "dept-design", "title": "Close the queue"})[1]["id"]
        assert post(server, token, "done", {"id": third})[1]["state"] == "done"

def test_missing_inbox_and_board(tmp_path):
    body = {"to": "hq", "title": "Hello there"}
    with served(tmp_path) as server:
        assert post(server, token_of(tmp_path), "handoff", body)[0] == 503
        assert not (tmp_path / ".heliopause" / "inbox").exists()
    (tmp_path / ".heliopause" / "inbox").mkdir(parents=True)
    with served(tmp_path) as server:
        status, data = post(server, token_of(tmp_path), "handoff", body)
        saved = json.loads((tmp_path / ".heliopause" / "inbox" / "atlas" / "handoffs.jsonl").read_text(encoding="utf-8"))
        assert status == 201 and data["line"] is None and saved["id"] == data["id"] and saved["op"] == "handoff"
        assert not (tmp_path / ".heliopause" / "inbox" / "lab" / "board.md").exists()

def test_fold_ignores_illegal_records():
    hand = {
        "ts": "2026-10-03T00:00:00Z", "op": "handoff", "id": "h-aaaabbbb",
        "by": "vp", "to": "hq", "title": "Ship the queue", "details": "notes",
    }
    claim = {"ts": "2026-10-03T00:01:00Z", "op": "claim", "id": "h-aaaabbbb", "by": "hq"}
    done = {"ts": "2026-10-03T00:02:00Z", "op": "done", "id": "h-aaaabbbb", "by": "hq"}
    assert fold([hand])["h-aaaabbbb"]["state"] == "open"
    assert fold([hand, claim])["h-aaaabbbb"]["state"] == "claimed"
    assert fold([hand, done])["h-aaaabbbb"]["state"] == "done"
    assert fold([hand, claim, done, claim])["h-aaaabbbb"]["state"] == "done"
    assert fold([hand, claim, claim])["h-aaaabbbb"]["state"] == "claimed"
    assert fold([claim, {"op": "nope"}, {"id": "x"}, hand])["h-aaaabbbb"]["to"] == "hq"
    assert fold([hand, dict(hand, title="Other queue item", to="vp")])["h-aaaabbbb"]["title"] == "Ship the queue"
    assert fold([{"op": "claim", "id": "h-00000000", "by": "vp", "ts": "t"}]) == {}

def test_inbox_lists_open_and_claimed(tmp_path):
    board(tmp_path, b"")
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        first = post(server, token, "handoff", {"to": "hq", "title": "First queue item", "details": "notes"})[1]["id"]
        second = post(server, token, "handoff", {"to": "hq", "title": "Second queue item"})[1]["id"]
        other = post(server, token, "handoff", {"to": "dept-engineering", "title": "Other queue item"})[1]["id"]
        status, raw = get(server, "/api/inbox?me=hq")
        data = json.loads(raw)
        assert status == 200 and [row["id"] for row in data["open"]] == [first, second]
        assert data["open"][0]["details"] == "notes" and data["open"][1]["details"] == ""
        assert set(data["open"][0]) == {"id", "from", "to", "title", "details", "state", "ts"}
        assert data["open"][0]["from"] == "vp" and data["open"][0]["to"] == "hq"
        post(server, token, "claim", {"id": second})
        post(server, token, "done", {"id": first})
        assert [(row["id"], row["state"]) for row in json.loads(get(server, "/api/inbox?me=hq")[1])["open"]] == [(second, "claimed")]
        assert [row["id"] for row in json.loads(get(server, "/api/inbox?me=dept-engineering")[1])["open"]] == [other]
        assert get(server, "/api/inbox?me=nope")[0] == 400

def _fds():
    import resource

    try:
        count = len(os.listdir("/dev/fd"))
    except OSError:
        count = -1
    return {"open_fds": count, "nofile": resource.getrlimit(resource.RLIMIT_NOFILE)}


def _more_fds():
    """Raise the soft open-file limit toward the hard one for one test; returns the old limits to restore.

    WHY: a launchd-started macOS runner has a soft limit of 256, and 20 requests at once plus the rest of the
    suite's descriptors can hit EMFILE. That is a busy server (tested below), not what this test is about.
    """
    import resource

    old = resource.getrlimit(resource.RLIMIT_NOFILE)
    soft, hard = old
    want = 4096 if hard == resource.RLIM_INFINITY else min(hard, 4096)
    if soft != resource.RLIM_INFINITY and soft < want:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
        except (ValueError, OSError):
            pass
    return old


def test_concurrent_handoffs(tmp_path, capfd):
    import resource
    import threading

    old = _more_fds()
    try:
        _concurrent(tmp_path, capfd)
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, old)


def _concurrent(tmp_path, capfd):
    (tmp_path / ".heliopause" / "inbox").mkdir(parents=True)
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        codes = []
        replies = []

        def one(i):
            status, body = call(server, "/api/handoff", token=token, body=json.dumps({"to": "hq", "title": f"note {i:02d}"}).encode())
            codes.append(status)
            replies.append((status, body))

        import threading

        threads = [threading.Thread(target=one, args=(i,)) for i in range(20)]
        for item in threads:
            item.start()
        for item in threads:
            item.join()
        raw = (tmp_path / ".heliopause" / "inbox" / "atlas" / "handoffs.jsonl").read_bytes()
        titles = [json.loads(line)["title"] for line in raw.decode().splitlines()]
        failed = [reply for reply in replies if reply[0] != 201]
        # On failure, say why: each non-201 reply and the server's own log line (atlas.handoffs._no_inbox).
        assert codes == [201] * 20, (failed, capfd.readouterr().err, _fds())
        assert raw.endswith(b"\n") and sorted(titles) == [f"note {i:02d}" for i in range(20)]


def _raw_post(server, token, path, payload):
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        conn.request("POST", path, body=json.dumps(payload).encode(),
                     headers={"X-Atlas-Token": token, "Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Retry-After"), json.loads(resp.read())
    finally:
        conn.close()


def _out_of_handles(monkeypatch, code, match):
    import errno

    from atlas import labfs

    real = labfs.open_append

    def fake(base, rel, *args, **kwargs):
        if str(rel).endswith(match):
            raise OSError(getattr(errno, code), os.strerror(getattr(errno, code)))
        return real(base, rel, *args, **kwargs)

    monkeypatch.setattr(labfs, "open_append", fake)


def test_out_of_file_handles_is_busy_not_no_inbox(tmp_path, monkeypatch, capfd):
    # F1: EMFILE before the lock used to answer 503 "no inbox", as if the inbox were missing.
    (tmp_path / ".heliopause" / "inbox").mkdir(parents=True)
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        for code in ("EMFILE", "ENFILE"):
            with monkeypatch.context() as patch:
                _out_of_handles(patch, code, "handoffs.jsonl")
                status, retry, body = _raw_post(server, token, "/api/handoff", {"to": "hq", "title": "Retry this handoff"})
            assert (status, retry, body) == (503, "1", {"error": "busy, retry"})
        assert "Too many open files" in capfd.readouterr().err  # the server log names the errno
        status, retry, body = _raw_post(server, token, "/api/handoff", {"to": "hq", "title": "After the busy spell"})
        assert status == 201 and retry is None


def test_other_open_errors_still_say_no_inbox(tmp_path, monkeypatch):
    (tmp_path / ".heliopause" / "inbox").mkdir(parents=True)
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        _out_of_handles(monkeypatch, "EACCES", "handoffs.jsonl")
        status, retry, body = _raw_post(server, token, "/api/handoff", {"to": "hq", "title": "Inbox cannot open"})
    assert (status, retry, body) == (503, None, {"error": "no inbox"})


def test_out_of_handles_reading_the_actor_is_busy_not_unknown_actor(tmp_path, monkeypatch):
    import errno

    import atlas.write as write

    (tmp_path / ".heliopause" / "inbox").mkdir(parents=True)
    with served(tmp_path) as server:
        token = token_of(tmp_path)

        def fake(*args, **kwargs):
            raise OSError(errno.EMFILE, "Too many open files")

        monkeypatch.setattr(write, "_slurp", fake)
        status, retry, body = _raw_post(server, token, "/api/handoff", {"to": "hq", "title": "Who is writing this"})
    assert (status, retry, body) == (503, "1", {"error": "busy, retry"})


def test_out_of_handles_on_the_board_is_busy_not_no_board(tmp_path, monkeypatch):
    board(tmp_path, b"")
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        _out_of_handles(monkeypatch, "EMFILE", "board.md")
        status, retry, body = _raw_post(server, token, "/api/post", {"state": "working", "headline": "Hello there", "details": "more here"})
    assert (status, retry, body) == (503, "1", {"error": "busy, retry"})


def test_handoff_to_the_owner_names_the_role():
    from atlas.handoffs import _headline

    assert _headline("owner") == "Handed off to Owner"

def test_fifo_log_is_refused_and_never_blocks(tmp_path):
    import os
    from atlas.handoffs import load
    log = tmp_path / ".heliopause" / "inbox" / "atlas" / "handoffs.jsonl"
    log.parent.mkdir(parents=True, mode=0o700)
    os.mkfifo(log)
    assert load(tmp_path) == []  # O_NONBLOCK: returns instead of waiting for a writer
    with served(tmp_path) as server:
        assert post(server, token_of(tmp_path), "handoff", {"to": "hq", "title": "Hello there"})[0] == 503

def test_torn_tail_redaction_and_id_clash(tmp_path, monkeypatch):
    board(tmp_path, b"")
    log = tmp_path / ".heliopause" / "inbox" / "atlas" / "handoffs.jsonl"
    secret = "ghp_" + "x" * 20
    with served(tmp_path) as server:
        token = token_of(tmp_path)
        first = post(server, token, "handoff", {"to": "hq", "title": "First queue item", "details": f"key {secret}"})[1]["id"]
        with open(log, "ab") as handle:
            handle.write(b'{"ts": "2026')  # a write killed halfway: no trailing newline
        second = post(server, token, "handoff", {"to": "hq", "title": "Second queue item"})[1]["id"]
        rows = json.loads(get(server, "/api/inbox?me=hq")[1])["open"]
        assert [row["id"] for row in rows] == [first, second]
        assert secret not in json.dumps(rows) and "[redacted]" in rows[0]["details"]
        monkeypatch.setattr("atlas.handoffs.secrets.token_hex", lambda n: second[2:])
        frozen = log.read_bytes()
        assert post(server, token, "handoff", {"to": "hq", "title": "Third queue item"}) == (409, {"error": "id clash"})
        assert log.read_bytes() == frozen

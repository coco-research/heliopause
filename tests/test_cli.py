import json
import shutil
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from atlas import cli
from atlas.collect import NODE_IDS
from atlas.__main__ import make_server

NY = ZoneInfo("America/New_York")


def line(hours_ago, who, state, text):
    when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).astimezone(NY)
    return f"{when:%Y-%m-%d %H:%M} ET · {who} · {state} · {text}"


def board_for(root, lines):
    path = root / ".heliopause" / "inbox" / "lab" / "board.md"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def url(tmp_path_factory):
    root = tmp_path_factory.mktemp("home")
    board_for(root, [
        line(8, "Design Dept", "working", "Waiting on Engineering for runners"),
        line(6, "Engineering Dept", "working", "CI rework"),
        line(6, "Lab Ops", "needs-owner", "Owner call needed on the night queue \u00b7 more"),
        line(4, "Design Dept", "working", "Engineering please check the runner PR \u00b7 more"),
        line(3, "Research & Innovation", "working", "Engineering tooling scouted with Design"),  # no headline
        line(2, "Budget Office", "needs-vp", "Engineering costs spike \u00b7 more"),
        line(1, "Engineering Dept", "working", "Mentions Design and Budget Office, not itself \u00b7 more"),
    ])
    server = make_server(root, 0, watch=False, owner_name="Alex Doe")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture(autouse=True)
def env(url, monkeypatch):
    monkeypatch.setenv("ATLAS_URL", url)
    monkeypatch.setattr("atlas.problems.shutil.disk_usage", lambda _root: shutil._ntuple_diskusage(10**12, 0, 500 * 10**9))


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_brief_mentions_only_after_my_last_line(capsys):
    code, out, _err = run(capsys, "brief", "--me", "dept-engineering", "--json")
    assert code == 0
    data = json.loads(out)
    assert set(data) == {"me", "label", "state", "headline", "ts", "problems", "inbox", "mentions"}
    assert data["me"] == "dept-engineering" and data["label"] == "Engineering"
    assert data["inbox"] == [] and data["problems"] == []
    # Every line naming Engineering is older than my own last line (1h ago).
    assert data["mentions"] == []


def test_brief_mentions_when_my_last_line_is_older(capsys):
    # Design's last line is 4h old; two later lines name it.
    code, out, _err = run(capsys, "brief", "--me", "dept-design", "--json")
    data = json.loads(out)
    assert code == 0
    # Newest first; a line with no separate headline still counts, headline null.
    assert [(m["from"], m["headline"]) for m in data["mentions"]] == [
        ("dept-engineering", "Mentions Design and Budget Office, not itself"),
        ("dept-research", None),
    ]
    assert all(set(m) == {"ts", "from", "headline"} for m in data["mentions"])


def test_brief_owner_label_and_problems(capsys):
    code, out, _err = run(capsys, "brief", "--me", "owner", "--json")
    data = json.loads(out)
    assert code == 0 and data["label"] == "Owner"
    assert "Alex" not in out
    assert [item["id"] for item in data["problems"]] == ["decision-waiting:dept-lab-ops"]
    assert [m["from"] for m in data["mentions"]] == ["dept-lab-ops"]
    code, text, _err = run(capsys, "brief", "--me", "owner")
    assert code == 0 and text.splitlines()[0].startswith("Owner · ")
    assert "▲ high · owner owner" in text and "@ " in text


def test_brief_mentions_capped_at_ten(capsys, tmp_path, monkeypatch):
    board_for(tmp_path, [line(30 - n, "Design Dept", "working", f"Engineering item {n} \u00b7 more") for n in range(12)])
    server = make_server(tmp_path, 0, watch=False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("ATLAS_URL", f"http://127.0.0.1:{server.server_address[1]}")
    _code, out, _err = run(capsys, "brief", "--me", "dept-engineering", "--json")
    server.shutdown()
    server.server_close()
    heads = [m["headline"] for m in json.loads(out)["mentions"]]
    assert len(heads) == 10 and heads[0] == "Engineering item 11"


def test_who_scores_and_order(capsys):
    code, out, _err = run(capsys, "who", "design", "signal", "--json")
    found = json.loads(out)
    assert code == 0
    assert found[0]["node"] == "dept-design" and found[0]["score"] == 2
    assert set(found[0]) == {"node", "label", "state", "headline", "ts", "score"}
    scores = [item["score"] for item in found]
    assert scores == sorted(scores, reverse=True) and len(found) <= 3
    # Ties break by node id.
    _code, out, _err = run(capsys, "who", "repos", "--json")
    assert [item["node"] for item in json.loads(out)] == ["dept-engineering", "dept-repo-office", "github"]
    code, text, _err = run(capsys, "who", "design")
    assert code == 0 and text.startswith("Design · ")


def test_who_empty_query(capsys):
    code, out, _err = run(capsys, "who", "")
    assert (code, out) == (0, "\n")
    assert run(capsys, "who", "nothingmatchesthis")[1] == "\n"


def test_problems_text_and_json(capsys):
    code, out, _err = run(capsys, "problems")
    assert code == 0
    assert out.splitlines()[0].startswith("▲ high · owner owner · ")
    assert "Lab Ops waits on the Owner" in out and "— Answer or resolve it." in out
    code, out, _err = run(capsys, "problems", "--owner", "owner", "--json")
    assert code == 0 and {item["owner"] for item in json.loads(out)} == {"owner"}


def test_state_all_and_one(capsys):
    code, out, _err = run(capsys, "state")
    lines = out.splitlines()
    assert code == 0 and len(lines) == len(NODE_IDS) and lines[0].startswith("Owner · ")
    assert "Alex" not in out
    code, out, _err = run(capsys, "state", "dept-design")
    assert code == 0 and "label: Design" in out and "state: working" in out
    code, out, _err = run(capsys, "state", "dept-design", "--json")
    assert json.loads(out)["id"] == "dept-design"
    code, out, _err = run(capsys, "state", "--json")
    assert code == 0 and "Alex" not in out
    code, out, _err = run(capsys, "state", "owner", "--json")
    assert json.loads(out)["label"] == "Owner"


def test_unknown_node_exits_1(capsys):
    code, out, err = run(capsys, "brief", "--me", "nobody")
    assert (code, out) == (1, "") and "unknown node" in err and "404" in err
    code, out, err = run(capsys, "state", "nobody")
    assert (code, out) == (1, "") and "unknown node" in err


def test_not_running_exits_3(capsys, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("ATLAS_URL", f"http://127.0.0.1:{port}")
    code, out, err = run(capsys, "state")
    assert code == 3 and out == ""
    assert err == f"atlas: not running at http://127.0.0.1:{port}; read ~/.heliopause/inbox/lab/board.md instead\n"


def test_age_reads_minutes_under_an_hour():
    from datetime import datetime, timedelta, timezone

    from atlas.cli import _age

    def iso(delta):
        return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")

    assert _age(iso(timedelta(seconds=20))) == "now"
    assert _age(iso(timedelta(minutes=9, seconds=5))) == "9m"
    assert _age(iso(timedelta(hours=3, minutes=1))) == "3h"
    assert _age(None) == "-"


def test_post_prints_line_and_bad_token_exits_1(capsys, tmp_path, monkeypatch):
    board_for(tmp_path, [line(1, "VP", "working", "Existing")])
    server = make_server(tmp_path, 0, watch=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("ATLAS_URL", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / ".atlas" / "token"))
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")  # a proxy would get the token
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        code, out, err = run(capsys, "post", "working", "Hello there", "more here")
        assert code == 0 and err == ""
        assert out.rstrip("\n").split(" \u00b7 ")[1:] == ["VP", "working", "Hello there", "more here"]
        token = (tmp_path / ".atlas" / "token").read_text(encoding="utf-8")
        assert token not in out
        (tmp_path / "bad-token").write_text("not-the-token", encoding="utf-8")
        monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / "bad-token"))
        code, out, err = run(capsys, "post", "working", "Hello there", "more here")
        assert code == 1 and out == "" and "401" in err
        assert token not in err
        (tmp_path / "bad-utf8").write_bytes(b"\xff")
        monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / "bad-utf8"))
        code, out, err = run(capsys, "post", "working", "Hello there", "more here")
        assert code == 1 and out == "" and err == "atlas: unauthorized (HTTP 401)\n"
        assert "Traceback" not in err
        (tmp_path / "non-ascii").write_text("\u65e5\u672c", encoding="utf-8")
        monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / "non-ascii"))
        code, out, err = run(capsys, "post", "working", "Hello there", "more here")
        assert code == 1 and out == "" and err == "atlas: unauthorized (HTTP 401)\n"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_post_refuses_non_loopback(capsys, monkeypatch):
    monkeypatch.setenv("ATLAS_URL", "http://example.com/atlas")
    monkeypatch.setenv("ATLAS_TOKEN_FILE", "/no/such/atlas-token")
    code, out, err = run(capsys, "post", "working", "Hello there", "more here")
    assert code == 1 and out == ""
    assert err == "atlas: post only talks to a loopback Heliopause\n"
    assert "Traceback" not in err


def test_post_does_not_follow_redirect(capsys, monkeypatch):
    hits = []

    class Sink(BaseHTTPRequestHandler):
        def handle_one_request(self):
            super().handle_one_request()
            hits.append(self.raw_requestline)

        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    class Bounce(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_address[1]}/stolen")
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    sink = HTTPServer(("127.0.0.1", 0), Sink)
    bounce = HTTPServer(("127.0.0.1", 0), Bounce)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (sink, bounce)]
    for thread in threads:
        thread.start()
    try:
        monkeypatch.setenv("ATLAS_URL", f"http://127.0.0.1:{bounce.server_address[1]}")
        monkeypatch.setenv("ATLAS_TOKEN_FILE", "/no/such/atlas-token")
        code, out, err = run(capsys, "post", "working", "Hello there", "more here")
        assert code == 1 and out == "" and hits == []
        assert "Traceback" not in err and "302" in err
    finally:
        sink.shutdown()
        bounce.shutdown()
        sink.server_close()
        bounce.server_close()
        for thread in threads:
            thread.join(timeout=2)


@pytest.mark.parametrize("token", ["ab\tcd", "ab\x00cd"])
def test_post_refuses_a_control_char_token(capsys, tmp_path, monkeypatch, fake_atlas, token):
    seen = fake_atlas(401, {"error": "unauthorized"})
    (tmp_path / "token").write_text(token + "\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / "token"))
    code, out, err = run(capsys, "post", "working", "Hello there", "more here")
    assert code == 1 and out == "" and err == "atlas: unauthorized (HTTP 401)\n"
    assert seen == [""]  # an empty token went out, never the control-char one
    assert token not in out + err


SECRET = "t0ken-AbC_123-xyz"


@pytest.mark.parametrize(
    "body, reason",
    [({"error": SECRET}, None), ({"error": ["x", SECRET]}, None), ({}, SECRET)],
    ids=["error-text", "not-a-str", "status-line"],
)
def test_http_error_never_echoes_the_token(capsys, tmp_path, monkeypatch, fake_atlas, body, reason):
    seen = fake_atlas(400, body, reason)
    (tmp_path / "token").write_text(SECRET + "\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / "token"))
    code, out, err = run(capsys, "post", "working", "Hello there", "more here")
    assert seen == [SECRET]  # the real token was sent, so what came back is a true echo
    assert (code, out) == (1, "") and err.endswith(" (HTTP 400)\n")
    assert "[REDACTED]" in err and SECRET not in out + err


@pytest.mark.parametrize(
    "argv, body, shown",
    [
        (("post", "working", "Hello there", "more here"), {"line": f"a {SECRET} b"}, "a [REDACTED] b"),
        (("claim", "h-1"), {"state": SECRET}, "[REDACTED]"),
    ],
    ids=["line", "state"],
)
def test_success_never_echoes_the_token(capsys, tmp_path, monkeypatch, fake_atlas, argv, body, shown):
    seen = fake_atlas(200, body)
    (tmp_path / "token").write_text(SECRET + "\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / "token"))
    code, out, err = run(capsys, *argv)
    assert seen == [SECRET]  # the real token was sent, so what came back is a true echo
    assert (code, out, err) == (0, shown + "\n", "")


def test_non_http_reply_exits_1_without_the_token(capsys, tmp_path, monkeypatch, fake_atlas):
    seen = fake_atlas(raw=f"{SECRET}\r\n".encode("ascii"))  # http.client raises BadStatusLine; its text is this first line
    (tmp_path / "token").write_text(SECRET + "\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / "token"))
    code, out, err = run(capsys, "post", "working", "Hello there", "more here")
    assert seen == [SECRET]
    assert (code, out) == (1, "") and err.startswith("atlas: bad reply from http://127.0.0.1:")
    assert err.endswith(" (BadStatusLine)\n") and "Traceback" not in err and SECRET not in out + err


def test_short_error_body_still_reports_the_http_error(capsys, tmp_path, monkeypatch, fake_atlas):
    fake_atlas(raw=b"HTTP/1.0 400 Bad\r\nContent-Length: 50\r\n\r\nshort")  # IncompleteRead while reading the body
    monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / "token"))
    assert run(capsys, "post", "working", "Hello there", "more here") == (1, "", "atlas: Bad (HTTP 400)\n")


def test_post_surrogate_detail_exits_1(capsys, tmp_path, monkeypatch):
    board_for(tmp_path, [line(1, "VP", "working", "Existing line")])
    server = make_server(tmp_path, 0, watch=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("ATLAS_URL", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / ".atlas" / "token"))
        code, out, err = run(capsys, "post", "working", "Hello there", "bad \udcff byte")
        assert code == 1 and out == "" and "Traceback" not in err and "400" in err
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_handoff_inbox_claim_done(capsys, tmp_path, monkeypatch):
    board_for(tmp_path, [line(1, "VP", "working", "Existing line")])
    server = make_server(tmp_path, 0, watch=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("ATLAS_URL", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / ".atlas" / "token"))
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        code, out, err = run(capsys, "handoff", "dept-engineering", "Ship the queue", "--details", "notes")
        assert code == 0 and err == ""
        parts = out.rstrip("\n").split(" \u00b7 ")
        assert parts[1:4] == ["VP", "working", "Handed off to Engineering"]
        ident = parts[4].removeprefix("Ship the queue (").removesuffix(")")
        code, out, err = run(capsys, "inbox", "--me", "dept-engineering")
        assert code == 0 and out == f"{ident} \u00b7 vp \u00b7 open \u00b7 Ship the queue\n"
        code, out, err = run(capsys, "claim", ident)
        assert code == 0 and out == "claimed\n"
        code, out, err = run(capsys, "done", ident)
        assert code == 0 and out == "done\n"
        (tmp_path / "bad-token").write_text("not-the-token", encoding="utf-8")
        monkeypatch.setenv("ATLAS_TOKEN_FILE", str(tmp_path / "bad-token"))
        code, out, err = run(capsys, "claim", ident)
        assert code == 1 and out == "" and "401" in err
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_who_matches_hyphenated_topics():
    from atlas.brief import _DIRECTORY, who

    graph = {"nodes": {key: {"label": key, "state": "idle", "headline": None, "ts": None} for key in _DIRECTORY}}
    assert who(graph, "cross-project")[0]["node"] == "vp"

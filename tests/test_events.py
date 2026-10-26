import json
import re
import threading
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path

from atlas.__main__ import make_server
from atlas.events import Watcher, board_event, board_mentions, history

SEP = " \u00b7 "
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "font-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _line(*parts: str) -> str:
    return SEP.join(parts) + "\n"


def _board(root: Path) -> Path:
    path = root / ".heliopause" / "inbox" / "lab" / "board.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _assert_clean(events: list[dict]) -> None:
    blob = json.dumps(events)
    assert "/" not in blob
    assert "REPO_SENTINEL" not in blob
    for event in events:
        assert set(event) == {"id", "ts", "kind", "from", "to", "headline"}
        assert _TS.match(event["ts"])


def test_boot_is_silent(tmp_path):
    board = _board(tmp_path)
    board.write_text(
        _line("2026-10-01 09:00 ET", "Design Dept", "working", "Already on the board", "prior"),
        encoding="utf-8",
    )
    inbox = tmp_path / ".heliopause" / "inbox" / "hq" / "inbox"
    done = tmp_path / ".heliopause" / "inbox" / "hq" / "done"
    inbox.mkdir(parents=True)
    done.mkdir(parents=True)
    (inbox / "ask-0001.md").write_text("---\nfrom: VP\n---\n# Already asked\n", encoding="utf-8")
    (done / "ask-0001.md").write_text("---\nto: VP\n---\n# Already closed\n", encoding="utf-8")
    pi = tmp_path / ".heliopause" / "inbox" / "queue" / "inbox"
    running = tmp_path / ".heliopause" / "inbox" / "queue" / "running"
    pi.mkdir(parents=True)
    running.mkdir(parents=True)
    (pi / "job.md").write_text("---\nfrom: Design Dept\nrepo: /tmp/REPO_SENTINEL\n---\n", encoding="utf-8")
    (running / "other.md").write_text("---\nfrom: claude\n---\n", encoding="utf-8")
    usage = tmp_path / ".heliopause" / "inbox" / "lab" / "usage" / "grok-build.jsonl"
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text('{"ts": "2026-10-01T14:00:00Z", "cwd": "/tmp/REPO_SENTINEL"}\n', encoding="utf-8")

    watcher = Watcher(tmp_path)
    watcher.start()
    try:
        events, seq = watcher.events_after(0)
        assert events == []
        assert seq == 0
        assert watcher.latest_seq() == 0
    finally:
        watcher.stop()

    again = Watcher(tmp_path)
    again.poll()
    assert again.events_after(0) == ([], 0)


def test_board_append(tmp_path):
    board = _board(tmp_path)
    board.write_text("", encoding="utf-8")
    watcher = Watcher(tmp_path)
    watcher.poll()

    board.write_text(
        _line("2026-10-01 09:00 ET", "Design Dept", "working", "Ship the atlas page", "extra context"),
        encoding="utf-8",
    )
    watcher.poll()
    events, seq = watcher.events_after(0)
    assert len(events) == 1
    event = events[0]
    assert event["kind"] == "post"
    assert event["from"] == "dept-design"
    assert event["to"] == "vp"
    assert event["headline"] == "Ship the atlas page"
    assert event["ts"] == "2026-10-01T13:00:00Z"
    assert event["id"].startswith("board:")
    _assert_clean(events)

    with board.open("a", encoding="utf-8") as handle:
        handle.write("2026-10-01 09:01 ET · Design Dept · working · Still typing")
    watcher.poll()
    events, seq = watcher.events_after(seq)
    assert events == []

    with board.open("a", encoding="utf-8") as handle:
        handle.write(" · done now\n")
    watcher.poll()
    events, seq = watcher.events_after(seq)
    assert len(events) == 1
    assert events[0]["kind"] == "post"
    assert events[0]["from"] == "dept-design"
    assert events[0]["to"] == "vp"
    assert events[0]["headline"] == "Still typing"
    assert events[0]["ts"] == "2026-10-01T13:01:00Z"

    with board.open("a", encoding="utf-8") as handle:
        handle.write(_line("2026-10-01 09:02 ET", "Engineering Dept", "blocked", "four fields only here"))
    watcher.poll()
    events, seq = watcher.events_after(seq)
    assert len(events) == 1
    assert events[0]["headline"] is None
    assert events[0]["from"] == "dept-engineering"
    assert events[0]["to"] == "vp"
    assert events[0]["kind"] == "post"

    with board.open("a", encoding="utf-8") as handle:
        handle.write(_line("2026-10-01 09:03 ET", "Design Dept", "working", "See ~/notes/board.md today", "extra"))
    watcher.poll()
    events, _seq = watcher.events_after(seq)
    assert len(events) == 1
    assert events[0]["headline"] is None
    assert events[0]["kind"] == "post"
    _assert_clean(events)


def test_board_states(tmp_path):
    board = _board(tmp_path)
    board.write_text("", encoding="utf-8")
    watcher = Watcher(tmp_path)
    watcher.poll()
    with board.open("a", encoding="utf-8") as handle:
        handle.write(
            "".join((
                _line("2026-10-01 09:00 ET", "Design Dept", "needs-owner", "Need a decision", "please look"),
                _line("2026-10-01 09:01 ET", "VP", "working", "Checking the board", "quiet"),
                _line("2026-10-01 09:02 ET", "VP", "resolved", "Design Dept: no action needed"),
                _line("2026-10-01 09:03 ET", "VP", "needs-owner", "Need a call", "today"),
                _line("2026-10-01 09:04 ET", "VP", "resolved", "No Such Team: ignore me"),
                _line("2026-10-01 09:05 ET", "Mystery Team", "working", "Hello from outside", "note"),
                _line("2026-10-01 09:06 ET", "Research & Innovation", "needs-hq", "Need a budget call", "please"),
            ))
        )
    watcher.poll()
    events, _seq = watcher.events_after(0)
    assert [(event["kind"], event["from"], event["to"], event["headline"]) for event in events] == [
        ("post", "dept-design", "owner", "Need a decision"),
        ("resolved", "vp", "dept-design", None),
        ("post", "vp", "owner", "Need a call"),
        ("post", "other", "vp", "Hello from outside"),
        ("post", "dept-research", "hq", "Need a budget call"),
    ]
    _assert_clean(events)


def test_rebaseline_is_silent(tmp_path):
    board = _board(tmp_path)
    board.write_text(
        _line("2026-10-01 09:00 ET", "Design Dept", "working", "Old baseline line", "stay"),
        encoding="utf-8",
    )
    watcher = Watcher(tmp_path)
    watcher.poll()

    board.write_text("", encoding="utf-8")
    watcher.poll()
    assert watcher.events_after(0) == ([], 0)

    board.write_text(
        _line("2026-10-01 09:01 ET", "Design Dept", "working", "After truncate", "visible"),
        encoding="utf-8",
    )
    watcher.poll()
    events, seq = watcher.events_after(0)
    assert [event["headline"] for event in events] == ["After truncate"]

    fresh = _line("2026-10-01 09:02 ET", "Design Dept", "working", "Replaced baseline", "silent")
    replacement = board.with_name("board.new")
    replacement.write_text(fresh, encoding="utf-8")
    replacement.replace(board)
    watcher.poll()
    events, seq = watcher.events_after(seq)
    assert events == []

    with board.open("a", encoding="utf-8") as handle:
        handle.write(_line("2026-10-01 09:03 ET", "Design Dept", "working", "After replace", "visible"))
    watcher.poll()
    events, _seq = watcher.events_after(seq)
    assert [event["headline"] for event in events] == ["After replace"]
    _assert_clean(events)


def test_hq_files(tmp_path):
    inbox = tmp_path / ".heliopause" / "inbox" / "hq" / "inbox"
    done = tmp_path / ".heliopause" / "inbox" / "hq" / "done"
    inbox.mkdir(parents=True)
    done.mkdir(parents=True)
    watcher = Watcher(tmp_path)
    watcher.poll()

    (inbox / "ask-0008.md").write_text(
        "---\nfrom: VP\nto: hq\n---\n# Please review the queue\n",
        encoding="utf-8",
    )
    watcher.poll()
    events, seq = watcher.events_after(0)
    assert len(events) == 1
    assert events[0]["kind"] == "ask"
    assert events[0]["from"] == "vp"
    assert events[0]["to"] == "hq"
    assert events[0]["headline"] == "Please review the queue"
    assert events[0]["id"] == "hq-inbox:ask-0008.md"
    watcher.poll()
    assert watcher.events_after(seq)[0] == []

    (done / "ask-0008.md").write_text("---\nto: VP\n---\n# Closed the ask\n", encoding="utf-8")
    watcher.poll()
    events, _seq = watcher.events_after(seq)
    assert len(events) == 1
    assert events[0]["kind"] == "answer"
    assert events[0]["from"] == "hq"
    assert events[0]["to"] == "vp"
    assert events[0]["headline"] is None
    assert events[0]["id"] == "hq-done:ask-0008.md"
    _assert_clean(events)


def test_pi_move(tmp_path):
    inbox = tmp_path / ".heliopause" / "inbox" / "queue" / "inbox"
    running = tmp_path / ".heliopause" / "inbox" / "queue" / "running"
    failed = tmp_path / ".heliopause" / "inbox" / "queue" / "failed"
    for path in (inbox, running, failed):
        path.mkdir(parents=True)
    watcher = Watcher(tmp_path)
    watcher.poll()

    job = inbox / "job.md"
    job.write_text(
        "---\nfrom: Design Dept\nrepo: /tmp/REPO_SENTINEL\n---\n# Run the nightly pass\n",
        encoding="utf-8",
    )
    watcher.poll()
    events, seq = watcher.events_after(0)
    assert len(events) == 1
    assert events[0]["kind"] == "job"
    assert events[0]["from"] == "dept-design"
    assert events[0]["to"] == "factory-queue"
    assert events[0]["id"] == "queue-inbox:job.md"
    watcher.poll()
    assert watcher.events_after(seq)[0] == []

    moved = running / "job.md"
    job.rename(moved)
    watcher.poll()
    events, seq = watcher.events_after(seq)
    assert len(events) == 1
    assert events[0]["kind"] == "job-start"
    assert events[0]["from"] == "factory-queue"
    assert events[0]["to"] == "dept-design"
    assert events[0]["id"] == "queue-running:job.md"

    moved.rename(failed / "job.md")
    watcher.poll()
    events, _seq = watcher.events_after(seq)
    assert len(events) == 1
    assert events[0]["kind"] == "job-failed"
    assert events[0]["from"] == "factory-queue"
    assert events[0]["to"] == "dept-design"
    assert events[0]["id"] == "queue-failed:job.md"
    _assert_clean(events)


def test_grok_tail(tmp_path):
    path = tmp_path / ".heliopause" / "inbox" / "lab" / "usage" / "grok-build.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    watcher = Watcher(tmp_path)
    watcher.poll()
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "2026-10-01T10:56:29-0400", "cwd": "/tmp/REPO_SENTINEL"}\n')
        handle.write("not json\n")
        handle.write("{bad\n")
    watcher.poll()
    events, _seq = watcher.events_after(0)
    assert len(events) == 1
    assert events[0]["kind"] == "job-done"
    assert events[0]["from"] == "factory-grok"
    assert events[0]["to"] == "vp"
    assert events[0]["ts"] == "2026-10-01T14:56:29Z"
    assert events[0]["headline"] is None
    assert events[0]["id"].startswith("grok:")
    blob = json.dumps(events)
    assert "cwd" not in blob
    _assert_clean(events)


def test_bad_bytes_are_skipped(tmp_path):
    board = _board(tmp_path)
    board.write_text("", encoding="utf-8")
    watcher = Watcher(tmp_path)
    watcher.poll()
    good = _line("2026-10-01 09:00 ET", "Design Dept", "working", "Ship the atlas page", "extra")
    with board.open("ab") as handle:
        handle.write(b"\xff\xfe garbage\n")
        handle.write(good.encode("utf-8"))
    watcher.poll()
    events, _seq = watcher.events_after(0)
    assert len(events) == 1
    assert events[0]["headline"] == "Ship the atlas page"
    assert events[0]["from"] == "dept-design"


def test_ring_keeps_last_and_ids_are_unique(tmp_path):
    board = _board(tmp_path)
    board.write_text("", encoding="utf-8")
    watcher = Watcher(tmp_path, size=2)
    watcher.poll()
    with board.open("a", encoding="utf-8") as handle:
        handle.write(
            "".join((
                _line("2026-10-01 09:00 ET", "Design Dept", "working", "Alpha one", "detail"),
                _line("2026-10-01 09:01 ET", "Design Dept", "working", "Alpha two", "detail"),
                _line("2026-10-01 09:02 ET", "Design Dept", "working", "Alpha three", "detail"),
            ))
        )
    watcher.poll()
    events, seq = watcher.events_after(0)
    assert [event["headline"] for event in events] == ["Alpha two", "Alpha three"]
    assert len({event["id"] for event in events}) == len(events)
    assert watcher.events_after(seq) == ([], seq)
    assert watcher.latest_seq() == seq

    with board.open("a", encoding="utf-8") as handle:
        handle.write(_line("2026-10-01 09:03 ET", "Design Dept", "working", "Alpha four", "detail"))
    watcher.poll()
    newer, seq2 = watcher.events_after(seq)
    assert [event["headline"] for event in newer] == ["Alpha four"]
    assert newer[0]["id"] not in {event["id"] for event in events}
    assert seq2 > seq
    assert len({event["id"] for event in (*events, *newer)}) == 3
    _assert_clean([*events, *newer])


def test_paths_never_leave_the_source(tmp_path):
    board = _board(tmp_path)
    board.write_text("", encoding="utf-8")
    inbox = tmp_path / ".heliopause" / "inbox" / "hq" / "inbox"
    inbox.mkdir(parents=True)
    pi = tmp_path / ".heliopause" / "inbox" / "queue" / "inbox"
    pi.mkdir(parents=True)
    usage = tmp_path / ".heliopause" / "inbox" / "lab" / "usage" / "grok-build.jsonl"
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text("", encoding="utf-8")
    watcher = Watcher(tmp_path)
    watcher.poll()

    with board.open("a", encoding="utf-8") as handle:
        handle.write(_line(
            "2026-10-01 09:00 ET",
            "Design Dept",
            "working",
            "See ~/notes/REPO_SENTINEL.md today",
            "extra",
        ))
    (inbox / "ask-0009.md").write_text(
        "---\nfrom: VP\n---\n# Review /tmp/REPO_SENTINEL now\n",
        encoding="utf-8",
    )
    (pi / "job.md").write_text(
        "---\nfrom: claude\nrepo: /tmp/REPO_SENTINEL\n---\nBODY_SENTINEL\n",
        encoding="utf-8",
    )
    with usage.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "2026-10-01T10:56:29-0400", "cwd": "/tmp/REPO_SENTINEL"}\n')
    watcher.poll()
    events, _seq = watcher.events_after(0)
    assert {event["kind"] for event in events} == {"post", "ask", "job", "job-done"}
    assert all(event["headline"] is None for event in events)
    _assert_clean(events)


def _read_frame(resp) -> str:
    lines = []
    while True:
        line = resp.fp.readline()
        if not line:
            break
        lines.append(line)
        if line in (b"\n", b"\r\n"):
            break
    return b"".join(lines).decode()


def _parse_frame(frame: str) -> tuple[str, str, dict]:
    ident = kind = None
    data = None
    for line in frame.splitlines():
        if line.startswith("id: "):
            ident = line[4:]
        elif line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
    assert ident and kind and isinstance(data, dict)
    return ident, kind, data


def test_server_events(tmp_path):
    board = _board(tmp_path)
    board.write_text("# board\n", encoding="utf-8")
    server = make_server(tmp_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = None
    try:
        port = server.server_address[1]
        host = f"127.0.0.1:{port}"

        head = HTTPConnection("127.0.0.1", port, timeout=3)
        head.request("HEAD", "/api/events", headers={"Host": host})
        resp = head.getresponse()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "text/event-stream"
        assert resp.getheader("Cache-Control") == "no-store"
        assert resp.getheader("Content-Length") is None
        assert resp.getheader("Content-Security-Policy") == _CSP
        assert resp.getheader("X-Content-Type-Options") == "nosniff"
        assert resp.getheader("Referrer-Policy") == "no-referrer"
        assert resp.read() == b""
        head.close()

        bad = HTTPConnection("127.0.0.1", port, timeout=3)
        bad.request("GET", "/api/events", headers={"Host": "evil.example"})
        denied = bad.getresponse()
        assert denied.status == 403
        denied.read()
        bad.close()

        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request(
            "GET",
            "/api/events",
            headers={"Host": host, "Last-Event-ID": "board:1:1"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "text/event-stream"
        assert resp.getheader("Content-Length") is None
        with board.open("a", encoding="utf-8") as handle:
            handle.write(
                _line("2026-10-01 09:00 ET", "Design Dept", "working", "Ship the atlas page", "extra context")
            )
        server.watcher.poll()
        ident, kind, data = _parse_frame(_read_frame(resp))
        assert kind == "post"
        assert data["kind"] == "post"
        assert data["from"] == "dept-design"
        assert data["to"] == "vp"
        assert data["headline"] == "Ship the atlas page"
        assert data["ts"] == "2026-10-01T13:00:00Z"
        assert data["id"] == ident
        assert "/" not in json.dumps(data)
    finally:
        if conn is not None:
            conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _prose(*parts: str) -> str:
    return SEP.join(parts)


def _mention_to(line: str) -> list[str]:
    return [event["to"] for event in board_mentions(line, "board:t")]


def test_board_mentions_order_and_longest_name():
    line = _prose(
        "2026-10-01 09:00 ET",
        "Atlas Brain Agent",
        "working",
        "Handing the sketch to Design and Grok Build",
        "Design and Design again",
    )
    event = board_event(line, "board:t")
    mentions = board_mentions(line, "board:t")
    assert [(item["from"], item["to"]) for item in mentions] == [
        ("brain-atlas", "dept-design"),
        ("brain-atlas", "factory-grok"),
    ]
    assert [item["id"] for item in mentions] == [
        "board:t:m:dept-design",
        "board:t:m:factory-grok",
    ]
    assert all(
        item["kind"] == "mention" and item["ts"] == event["ts"] and item["headline"] == event["headline"]
        for item in mentions
    )
    assert _mention_to(_prose(
        "2026-10-01 09:01 ET", "Atlas Brain Agent", "working", "Ask Grok today", "please",
    )) == ["factory-grok"]


def test_board_mentions_skip_self_dest_unknown_and_resolved():
    assert _mention_to(_prose(
        "2026-10-01 09:00 ET",
        "Design Dept",
        "working",
        "Design revises the sketch for Engineering",
        "today",
    )) == ["dept-engineering"]
    assert _mention_to(_prose(
        "2026-10-01 09:01 ET", "Engineering Dept", "working", "Ask the VP today", "please",
    )) == []
    assert _mention_to(_prose(
        "2026-10-01 09:02 ET", "Engineering Dept", "needs-owner", "Ask the Owner today", "please",
    )) == []
    assert _mention_to(_prose(
        "2026-10-01 09:03 ET", "Design Dept", "working", "Ask the Owner today", "please",
    )) == ["owner"]
    assert _mention_to(_prose(
        "2026-10-01 09:04 ET", "Engineering Dept", "needs-owner", "Design should review", "please",
    )) == ["dept-design"]
    mystery = _prose(
        "2026-10-01 09:05 ET", "Mystery Team", "working", "Design should look", "please",
    )
    assert board_event(mystery, "board:t")["from"] == "other"
    assert board_mentions(mystery, "board:t") == []
    resolved = _prose("2026-10-01 09:06 ET", "VP", "resolved", "Design Dept: no action needed")
    assert board_event(resolved, "board:t")["kind"] == "resolved"
    assert board_mentions(resolved, "board:t") == []


def test_board_mentions_company_case_and_word_boundary():
    assert _mention_to(_prose(
        "2026-10-01 09:00 ET",
        "Atlas Brain Agent",
        "working",
        "Coco Research asked Design",
        "today",
    )) == ["dept-design"]
    assert _mention_to(_prose(
        "2026-10-01 09:01 ET", "Atlas Brain Agent", "working", "Send the design review", "research notes",
    )) == []
    assert _mention_to(_prose(
        "2026-10-01 09:02 ET", "Atlas Brain Agent", "working", "The Designer met the Pilot", "once",
    )) == []
    assert _mention_to(_prose(
        "2026-10-01 09:03 ET", "Atlas Brain Agent", "working", "Ask Queue Factory today", "please",
    )) == ["factory-queue"]


def test_mention_watcher_matches_history(tmp_path):
    board = _board(tmp_path)
    board.write_text("", encoding="utf-8")
    watcher = Watcher(tmp_path)
    watcher.poll()
    line = _line(
        "2026-10-01 09:00 ET",
        "Atlas Brain Agent",
        "working",
        "Handing the sketch to Design and Grok Build",
        "Design and Design again",
    )
    board.write_text(line, encoding="utf-8")
    watcher.poll()
    live, seq = watcher.events_after(0)
    assert [event["kind"] for event in live] == ["post", "mention", "mention"]
    assert [(event["from"], event["to"]) for event in live] == [
        ("brain-atlas", "vp"),
        ("brain-atlas", "dept-design"),
        ("brain-atlas", "factory-grok"),
    ]
    assert watcher.events_after(seq) == ([], seq)
    now = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)
    past = history(tmp_path, now)
    keys = ("kind", "from", "to", "headline", "ts")
    live_mentions = [{key: event[key] for key in keys} for event in live if event["kind"] == "mention"]
    past_mentions = [{key: event[key] for key in keys} for event in past if event["kind"] == "mention"]
    assert live_mentions == past_mentions
    assert live_mentions
    _assert_clean(live)


def test_pi_pulses_only_for_real_md_jobs(tmp_path):
    base = tmp_path / ".heliopause" / "inbox" / "queue"
    failed, done = base / "failed", base / "done"
    for path in (failed, done):
        path.mkdir(parents=True)
    watcher = Watcher(tmp_path)
    watcher.poll()
    (failed / "job.log").write_text("log\n", encoding="utf-8")
    (failed / "job.withdrawn.md").write_text("---\nfrom: Lab Ops\n---\n# Pulled\n", encoding="utf-8")
    (done / "job.log").write_text("log\n", encoding="utf-8")
    watcher.poll()
    assert watcher.events_after(0)[0] == []
    (failed / "job.md").write_text("---\nfrom: Lab Ops\n---\n# Boom\n", encoding="utf-8")
    watcher.poll()
    events, _seq = watcher.events_after(0)
    assert [event["kind"] for event in events] == ["job-failed"]

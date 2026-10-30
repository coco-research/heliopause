"""Stdio MCP server against a real loopback Atlas."""

import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from atlas.__main__ import make_server
from atlas.mcp import _PROTOCOL

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 30  # a ceiling, not a wait: the shared self-hosted runner can take >10 s to start and exit a child under load (#75)


def board_for(root, lines):
    path = root / ".heliopause" / "inbox" / "lab" / "board.md"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def running(tmp_path):
    board = board_for(tmp_path, ["2026-10-02 09:00 ET · VP · working · Existing line · already here"])
    server = make_server(tmp_path, 0, watch=False, owner_name="Alex Doe")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    yield url, tmp_path, board
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _env(url, root):
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root),
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "ATLAS_URL": url,
        "ATLAS_TOKEN_FILE": str(Path(root) / ".atlas" / "token"),
    }


def exchange(env, messages, timeout=TIMEOUT):
    proc = subprocess.Popen(
        [sys.executable, "-m", "atlas.mcp"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
    )
    try:
        out, err = proc.communicate("\n".join(messages) + "\n", timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    assert proc.returncode == 0, err
    assert "Traceback" not in err
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def rpc(req_id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _body(reply):
    return json.loads(reply["result"]["content"][0]["text"])


def test_session_lists_calls_and_posts(running):
    url, root, board = running
    assert (root / ".atlas" / "token").is_file()
    before = board.read_text(encoding="utf-8").splitlines()
    replies = exchange(_env(url, root), [
        rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        rpc(2, "tools/list"),
        rpc(3, "tools/call", {"name": "atlas_state", "arguments": {}}),
        rpc(4, "tools/call", {"name": "atlas_post", "arguments": {
            "state": "working", "headline": "Hello there", "details": "more here",
        }}),
    ])
    assert [item["id"] for item in replies] == [1, 2, 3, 4]  # the notification got no reply
    assert replies[0]["result"]["protocolVersion"] == _PROTOCOL
    assert replies[0]["result"]["capabilities"] == {"tools": {}}
    assert replies[0]["result"]["serverInfo"] == {"name": "atlas", "version": "0.1"}
    assert [tool["name"] for tool in replies[1]["result"]["tools"]] == [
        "atlas_state", "atlas_problems", "atlas_who", "atlas_brief", "atlas_post",
    ]
    nodes = _body(replies[2])["graph"]["nodes"]
    assert nodes["owner"]["label"] == "Owner" and "Alex" not in replies[2]["result"]["content"][0]["text"]  # masked, like the CLI
    posted = _body(replies[3])
    assert replies[3]["result"].get("isError") is not True
    after = board.read_text(encoding="utf-8").splitlines()
    assert after[:-1] == before and after[-1] == posted["line"]
    assert "Hello there" in posted["line"] and "more here" in posted["line"]
    bad = exchange(_env(url, root), [
        rpc(9, "tools/call", {"name": "atlas_post", "arguments": {
            "state": "working", "headline": "Nope", "details": "more here",
        }}),
    ])
    assert bad[0]["result"]["isError"] is True
    assert bad[0]["result"]["content"][0]["text"] == "bad headline"
    assert board.read_text(encoding="utf-8").splitlines() == after


def test_private_hq_ask_comes_back_content_free(running):
    url, root, _board = running
    path = root / ".heliopause" / "inbox" / "hq" / "inbox" / "ask-1.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nid: ask-1\nprivate: true\nfrom: vp\nto: hq/cto\n---\n# Merge PR 12\n",
        encoding="utf-8",
    )
    replies = exchange(_env(url, root), [
        rpc(1, "tools/call", {"name": "atlas_state", "arguments": {}}),
        rpc(2, "tools/call", {"name": "atlas_who", "arguments": {"topic": "Merge"}}),
    ])
    raw = "\n".join(reply["result"]["content"][0]["text"] for reply in replies)
    assert "Merge PR 12" not in raw and "hq/cto" not in raw
    rows = [item for item in _body(replies[0])["decisions"] if item.get("kind") == "hq"]
    assert rows == [{
        "kind": "hq", "private": True, "id": "ask-1", "from": None, "to": None, "for": [],
        "type": None, "priority": None, "title": "private HQ ask · ask-1",
        "created": None, "ts": None,
    }]


@pytest.mark.parametrize("url", (
    "http://example.invalid:4300",
    "http://10.0.0.1:4300",
    "example.com:80",
    "http://example.com",
))
@pytest.mark.parametrize("name,arguments", [
    ("atlas_state", {}),
    ("atlas_problems", {}),
    ("atlas_who", {"topic": "queue"}),
    ("atlas_brief", {"me": "vp"}),
    ("atlas_post", {"state": "working", "headline": "Hello there", "details": "more here"}),
])
def test_non_loopback_refused_before_any_request(tmp_path, url, name, arguments):
    # The refusal text proves no request went out: a real attempt fails with the "Atlas is down" text instead.
    replies = exchange(_env(url, tmp_path), [
        rpc(1, "tools/call", {"name": name, "arguments": arguments}),
    ])
    result = replies[0]["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"] == "atlas: only talks to a loopback Heliopause"


def test_initialize_reports_own_protocol(tmp_path):
    replies = exchange(_env("http://127.0.0.1:9", tmp_path), [
        rpc(1, "initialize", {"protocolVersion": _PROTOCOL, "capabilities": {}}),
    ])
    assert "error" not in replies[0]
    assert replies[0]["result"]["protocolVersion"] == _PROTOCOL


def test_ping_returns_empty_result(tmp_path):
    replies = exchange(_env("http://127.0.0.1:9", tmp_path), [rpc(1, "ping")])
    assert "error" not in replies[0]
    assert replies[0]["result"] == {}


@pytest.mark.parametrize("form", ("localhost:{port}", "127.0.0.1:{port}"))
def test_schemeless_loopback_reaches_the_server(running, form):
    url, root, _board = running
    port = url.rsplit(":", 1)[1]
    replies = exchange(_env(form.format(port=port), root), [
        rpc(1, "tools/call", {"name": "atlas_state", "arguments": {}}),
    ])
    assert "error" not in replies[0]
    assert replies[0]["result"].get("isError") is not True
    assert _body(replies[0])["graph"]["nodes"]["owner"]["label"] == "Owner"


def test_down_is_error(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))  # bound, never listening: refused for the whole exchange, and nobody else can take it
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        replies = exchange(_env(url, tmp_path), [
            rpc(1, "tools/call", {"name": "atlas_state", "arguments": {}}),
        ])
    result = replies[0]["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"] == (
        f"Heliopause is not running on {url}; read .heliopause/inbox/lab/board.md instead."
    )


SECRET = "t0ken-AbC_123-xyz"


@pytest.mark.parametrize(
    "status, body, reason, shown",
    [
        (400, {"error": f"bad {SECRET}"}, None, "bad [REDACTED]"),
        (400, {}, SECRET, "[REDACTED]"),
        (200, {"line": f"ok {SECRET}"}, None, '{"line":"ok [REDACTED]"}'),
    ],
    ids=["error-text", "status-line", "success"],
)
def test_tool_result_never_echoes_the_token(tmp_path, fake_atlas, status, body, reason, shown):
    (tmp_path / ".atlas").mkdir()
    (tmp_path / ".atlas" / "token").write_text(SECRET + "\n", encoding="utf-8")
    seen = fake_atlas(status, body, reason)
    replies = exchange(_env(os.environ["ATLAS_URL"], tmp_path), [
        rpc(1, "tools/call", {"name": "atlas_post", "arguments": {
            "state": "working", "headline": "Hello there", "details": "more here",
        }}),
    ])
    assert seen == [SECRET]  # the real token was sent, so what came back is a true echo
    result = replies[0]["result"]
    assert result.get("isError", False) is (status != 200) and result["content"][0]["text"] == shown


def test_unknown_method_and_garbage_keeps_the_loop(tmp_path):
    replies = exchange(_env("http://127.0.0.1:9", tmp_path), [
        "{",
        "[" * 100000,  # deep nesting must not end the loop
        rpc(5, "nope"),
        rpc(6, "initialize"),
    ])
    assert replies[0]["id"] is None and replies[0]["error"]["code"] == -32700
    assert replies[1]["id"] is None and replies[1]["error"]["code"] == -32700
    assert replies[2]["id"] == 5 and replies[2]["error"]["code"] == -32601
    assert replies[3]["id"] == 6 and replies[3]["result"]["protocolVersion"] == "2025-06-18"

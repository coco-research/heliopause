"""Stdio MCP server. `python3 -m atlas.mcp`. Same verbs as the CLI, one JSON-RPC object per line."""

from __future__ import annotations

import json
import sys
from urllib.error import HTTPError
from urllib.parse import urlparse

from atlas.cli import _base, _get, _mask_owner, _scrub, _send as _http_send, _token  # mcp.py has its own _send for JSON-RPC replies

_PROTOCOL = "2025-06-18"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# ponytail: one JSON object per line and required-string checks only. Not Content-Length
# framing, batches, or a full JSON Schema validator — add those if a client needs them.
_SPEC = (
    ("atlas_state", "Node states. Optional node id returns that one node.", {"node": {"type": "string"}}, ()),
    ("atlas_problems", "Open problems. Optional owner id filters them.", {"owner": {"type": "string"}}, ()),
    ("atlas_who", "Up to three nodes matching a topic.", {"topic": {"type": "string"}}, ("topic",)),
    ("atlas_brief", "Brief for one node: its state, problems, and mentions.", {"me": {"type": "string"}}, ("me",)),
    (
        "atlas_post",
        'Append a board line. Headline is short prose with a space, ≤60 chars, no " · ". Details are 1–500 characters.',
        {
            "state": {"type": "string"},
            "headline": {"type": "string", "description": 'Short prose with a space, ≤60 chars, no " · ".'},
            "details": {"type": "string", "description": "1–500 characters."},
        },
        ("state", "headline", "details"),
    ),
)


def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(req_id, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _text(text: str, is_error: bool = False) -> dict:
    body: dict = {"content": [{"type": "text", "text": text}]}
    if is_error:
        body["isError"] = True
    return body


def _tools() -> list[dict]:
    listed = []
    for name, description, properties, required in _SPEC:
        schema: dict = {"type": "object", "properties": properties}
        if required:
            schema["required"] = list(required)
        listed.append({"name": name, "description": description, "inputSchema": schema})
    return listed


def _args_ok(properties: dict, required: tuple, args: object) -> bool:
    if not isinstance(args, dict):
        return False
    if any(not isinstance(args.get(key), str) for key in required):
        return False
    return all(not (key in args and not isinstance(args[key], str)) for key in properties)


def _reason(err: HTTPError) -> str:
    try:
        body = json.load(err)
    except (ValueError, AttributeError, OSError):
        return str(err.reason)
    if isinstance(body, dict) and isinstance(body.get("error"), str) and body["error"]:
        return body["error"]
    return str(err.reason)


def _call(base: str, name: str, args: dict) -> dict:
    if urlparse(base).hostname not in _LOOPBACK:
        return _text("atlas: only talks to a loopback Heliopause", True)
    if name == "atlas_post":
        data = _http_send(base, "/api/post", {key: args[key] for key in ("state", "headline", "details")})
    else:
        path, query = "/api/state", {}
        if name == "atlas_problems":
            path = "/api/problems"
            if args.get("owner"):
                query["owner"] = args["owner"]
        elif name == "atlas_who":
            path, query = "/api/who", {"q": args["topic"]}
        elif name == "atlas_brief":
            path, query = "/api/brief", {"me": args["me"]}
        data = _get(base, path, **query)
        if name == "atlas_state":
            # Same as the CLI: agents see "Owner", never the owner's configured name.
            nodes = _mask_owner(data["graph"]["nodes"])
            data = {**data, "graph": {**data["graph"], "nodes": nodes}}
            if args.get("node"):
                found = nodes.get(args["node"])
                if found is None:
                    return _text("unknown node", True)
                data = found
    return _text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _handle(msg: object) -> None:
    if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
        if not isinstance(msg, dict) or "id" in msg:
            _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
        return
    if "id" not in msg:
        return
    req_id, method = msg["id"], msg["method"]
    if "params" in msg and not isinstance(msg["params"], dict):
        _error(req_id, -32602, "Invalid params")
        return
    params = msg.get("params") or {}
    if method == "initialize":
        _result(req_id, {
            "protocolVersion": _PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "atlas", "version": "0.1"},
        })
        return
    if method == "ping":
        _result(req_id, {})
        return
    if method == "tools/list":
        _result(req_id, {"tools": _tools()})
        return
    if method != "tools/call":
        _error(req_id, -32601, "Method not found")
        return
    name, args = params.get("name"), params.get("arguments", {})
    spec = next((row for row in _SPEC if row[0] == name), None)
    if not isinstance(name, str) or spec is None or not _args_ok(spec[2], spec[3], args):
        _error(req_id, -32602, "Invalid params")
        return
    base = _base()
    token = _token() if name == "atlas_post" else ""  # only the write tool sends it
    try:
        result = _call(base, name, args)
    except HTTPError as err:
        result = _text(_reason(err), True)
    except OSError:
        result = _text(f"Heliopause is not running on {base}; read .heliopause/inbox/lab/board.md instead.", True)
    # Whatever answered may echo the token (body, error text or status line); never relay it.
    # ponytail: matches the text as relayed, so a token with a quote or backslash (escaped inside JSON) gets through; Heliopause tokens are urlsafe.
    result["content"][0]["text"] = _scrub(result["content"][0]["text"], token)
    _result(req_id, result)


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            msg = json.loads(text)
        except (ValueError, RecursionError):  # JSONDecodeError is a ValueError; deep nesting recurses
            _error(None, -32700, "Parse error")
            continue
        try:
            _handle(msg)
        except Exception:
            if isinstance(msg, dict) and "id" in msg:
                _error(msg.get("id"), -32603, "Internal error")
            print("atlas mcp: request failed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Agent CLI. `python3 -m atlas.cli state|problems|who|brief|post <state> <headline> <details>`. Talks to a running Heliopause over HTTP."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from atlas.graph import _parse
from atlas.problems import _ago

_SEVERITY = {"high": "▲", "medium": "◆", "low": "▽"}


def _age(ts: str | None) -> str:
    stamp = _parse(ts)
    if stamp is None:
        return "-"
    delta = datetime.now(timezone.utc) - stamp
    # Under an hour reads in minutes ("now" under one), like the page; "0h" says nothing.
    minutes = int(delta.total_seconds() // 60)
    return _ago(delta) if minutes >= 60 else ("now" if minutes < 1 else f"{minutes}m")


def _base() -> str:
    # WHY: urlparse("localhost:4300") reads "localhost" as the scheme, so the host is None.
    raw = os.environ.get("ATLAS_URL", "http://127.0.0.1:4300")
    if "://" not in raw:
        raw = "http://" + raw
    return raw.rstrip("/")


def _get(base: str, path: str, **query: str) -> object:
    url = f"{base}{path}" + (f"?{urlencode(query)}" if query else "")
    with build_opener(ProxyHandler({})).open(url, timeout=10) as resp:  # loopback reads never go via a proxy
        return json.load(resp)


def _token() -> str:
    path = Path(os.environ.get("ATLAS_TOKEN_FILE", Path.home() / ".atlas" / "token")).expanduser()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    # Server tokens are printable ASCII. A control char (CR/LF split the headers; tab/NUL slip past http.client, and an
    # error that repr-escapes them defeats redaction) or non-ASCII (not sendable) makes the file unusable. Same as atlas-alert.
    return "" if not token.isascii() or not token.isprintable() else token


def _scrub(text: str, token: str) -> str:
    # Whatever answers on the port may echo the token back (body, status line, error text); never show it. No token: unchanged.
    # ponytail: verbatim match only; a listener that re-encodes or splits the echo gets through. Non-HTTP replies show no server text.
    return text.replace(token, "[REDACTED]") if token else text


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _send(base: str, path: str, payload: dict) -> object:
    # No Origin header: the server rejects any Origin as CSRF.
    req = Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Atlas-Token": _token()},
    )
    # ProxyHandler({}): an http_proxy env var must never carry the token off the machine.
    with build_opener(ProxyHandler({}), _NoRedirect).open(req, timeout=10) as resp:
        return json.load(resp)


def _body(args) -> dict:
    if args.verb == "post":
        return {"state": args.state, "headline": args.headline, "details": args.details}
    if args.verb == "handoff":
        payload = {"to": args.to, "title": args.title}
        if args.details is not None:
            payload["details"] = args.details
        return payload
    return {"id": args.id}


def _node_line(item: dict) -> str:
    return f"{item['label']} · {item['state']} · {_age(item['ts'])} · {item['headline'] or '-'}"


def _problem_line(item: dict) -> str:
    return (
        f"{_SEVERITY[item['severity']]} {item['severity']} · owner {item['owner']} · "
        f"{_age(item['since'])} · {item['headline']} — {item['next_action']}"
    )


def _mask_owner(nodes: dict) -> dict:
    # WHY: the graph carries the owner's configured name for the page. CLI output (text and --json) says "Owner".
    return {key: {**item, "label": "Owner" if key == "owner" else item["label"]} for key, item in nodes.items()}


def _text(verb: str, data: object, node: str | None) -> list[str]:
    if verb == "state":
        nodes = data["graph"]["nodes"]
        if node is None:
            return [_node_line(item) for item in nodes.values()]
        return [f"{key}: {value}" for key, value in nodes[node].items()]
    if verb == "problems":
        return [_problem_line(item) for item in data]
    if verb == "who":
        return [f"{_node_line(item)} · score {item['score']}" for item in data]
    lines = [_node_line(data), *(_problem_line(item) for item in data["problems"])]
    return lines + [f"@ {_age(item['ts'])} · {item['from']} · {item['headline'] or '-'}" for item in data["mentions"]]


def _role_lab(raw: Path | None) -> Path | None:
    if raw is not None:
        path = raw.expanduser()
        if not path.is_absolute():
            path = path.resolve()
        return path if (path / "atlas.json").is_file() else None
    from atlas.plugin import _find_lab

    return _find_lab()


def _lab_data(lab: Path) -> dict:
    try:
        data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _role_brief(args) -> int:
    lab = _role_lab(args.lab)
    if lab is None:
        print("brief: no lab folder found", file=sys.stderr)
        return 1
    data = _lab_data(lab)
    from atlas.role_brief import edit, render, reset, status_lines

    if args.status:
        lines = status_lines(lab, data)
        if lines:
            print("\n".join(lines))
        return 0
    if args.reset:
        return reset(lab, args.role, data)
    if args.edit:
        return edit(lab, args.role, data)
    print(render(lab, args.role, data))
    return 0


def _approve(args) -> int:
    from atlas.plugin import _find_lab
    from atlas.role_brief import approve_refusal

    # Read the lab, if there is one, to see whether K7 is on. Never write.
    lab = _find_lab()
    data = _lab_data(lab) if lab is not None else {}
    print(approve_refusal(data, args.actor), file=sys.stderr)
    return 3


def _brief_epilog() -> str:
    from atlas.role_brief import goal_help

    lab = _role_lab(None)  # same path as `brief` when --lab is omitted
    return 'Project goal ("goal" in the lab file): ' + goal_help({} if lab is None else _lab_data(lab))


def main(argv: list[str] | None = None, prog: str = "heliopause") -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print the raw JSON")
    parser = argparse.ArgumentParser(prog=prog, description="Ask a running Heliopause (default http://127.0.0.1:4300).")
    verbs = parser.add_subparsers(dest="verb", required=True)
    verbs.add_parser("state", parents=[common]).add_argument("node", nargs="?")
    verbs.add_parser("problems", parents=[common]).add_argument("--owner")
    verbs.add_parser("who", parents=[common]).add_argument("topic", nargs="+")
    brief = verbs.add_parser(
        "brief",
        parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_brief_epilog(),
    )
    brief.add_argument("--me", help="node id, as today")
    brief.add_argument("--role", choices=("hq", "lab", "factory"), help="print that role's brief")
    brief.add_argument("--edit", action="store_true", help="write the brief into the lab and open it")
    brief.add_argument("--reset", action="store_true", help="delete the edited brief and print the default")
    brief.add_argument("--status", action="store_true", help="print Default or Edited for each seat")
    brief.add_argument("--lab", type=Path, help="lab folder (default: the folder the session hook uses)")
    approve = verbs.add_parser("approve")
    approve.add_argument("id")
    answer = approve.add_mutually_exclusive_group(required=True)
    answer.add_argument("--yes", action="store_true")
    answer.add_argument("--no", action="store_true")
    approve.add_argument("--reason")
    approve.add_argument("--as", dest="actor", choices=("planner", "builder", "reviewer", "user"))
    post = verbs.add_parser("post")
    post.add_argument("state")
    post.add_argument("headline")
    post.add_argument("details")
    handoff = verbs.add_parser("handoff")
    handoff.add_argument("to")
    handoff.add_argument("title")
    handoff.add_argument("--details")
    verbs.add_parser("claim").add_argument("id")
    verbs.add_parser("done").add_argument("id")
    verbs.add_parser("inbox").add_argument("--me", required=True)
    args = parser.parse_args(argv)
    if args.verb == "approve":
        return _approve(args)
    if args.verb == "brief" and args.status:
        if args.me or args.role or args.edit or args.reset:
            print("brief: --status stands alone", file=sys.stderr)
            return 1
        return _role_brief(args)
    if args.verb == "brief" and (args.role or args.edit or args.reset):
        if args.me:
            print("brief: pass --me or --role, not both", file=sys.stderr)
            return 1
        if args.edit and args.reset:
            print("brief: pass --edit or --reset, not both", file=sys.stderr)
            return 1
        if not args.role:
            need = "--edit" if args.edit else "--reset"
            print(f"brief: {need} needs --role", file=sys.stderr)
            return 1
        return _role_brief(args)
    if args.verb == "brief" and not args.me:
        print("brief: pass --me or --role", file=sys.stderr)
        return 2
    base = _base()
    writes = {"post", "handoff", "claim", "done"}
    if args.verb in writes and urlparse(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        print(f"atlas: {args.verb} only talks to a loopback Heliopause", file=sys.stderr)
        return 1
    node = getattr(args, "node", None)
    path, query = f"/api/{args.verb}", {}
    if args.verb == "problems" and args.owner:
        query = {"owner": args.owner}
    elif args.verb == "who":
        query = {"q": " ".join(args.topic)}
    elif args.verb in ("brief", "inbox"):
        query = {"me": args.me}
    token = _token() if args.verb in writes else ""  # only writes send the token; reads never touch the file
    try:
        if args.verb in writes:
            data = _send(base, f"/api/{args.verb}", _body(args))
        else:
            data = _get(base, path, **query)
    except HTTPError as err:
        try:
            message = json.load(err).get("error", "")
        except (ValueError, AttributeError, OSError, HTTPException):  # a reset or short read of the error body is still an HTTP error
            message = ""
        print(f"atlas: {_scrub(str(message or err.reason), token)} (HTTP {err.code})", file=sys.stderr)
        return 1
    except OSError:  # URLError, refused, timeout
        print(f"atlas: not running at {base}; read ~/.heliopause/inbox/lab/board.md instead", file=sys.stderr)
        return 3
    except HTTPException as err:  # answered, but not in HTTP. (A bare hang-up is RemoteDisconnected, an OSError: handled above.)
        # Name the failure only: BadStatusLine's text is the raw first line, which may be the token echoed back.
        print(f"atlas: bad reply from {base} ({type(err).__name__})", file=sys.stderr)
        return 1
    if args.verb in writes:
        text = data["line"] if args.verb in ("post", "handoff") else data["state"]
        print("" if text is None else _scrub(str(text), token))
        return 0
    if args.verb == "inbox":
        print("\n".join(
            f"{item['id']} \u00b7 {item['from']} \u00b7 {item['state']} \u00b7 {item['title']}" for item in data["open"]
        ))
        return 0
    if node is not None and node not in data["graph"]["nodes"]:
        print(f"atlas: unknown node {node}", file=sys.stderr)
        return 1
    if args.verb == "state":
        data = {**data, "graph": {**data["graph"], "nodes": _mask_owner(data["graph"]["nodes"])}}
    if args.json:
        print(json.dumps(data if node is None else data["graph"]["nodes"][node], ensure_ascii=False))
    else:
        print("\n".join(_text(args.verb, data, node)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

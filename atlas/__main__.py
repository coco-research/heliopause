"""Loopback server. `python3 -m atlas [--port 4300] [--root ~] [--owner-name Owner] [--demo] [--lab DIR]`."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socketserver
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


_imported = False


def _attach_lane_flags(state: dict, root: Path, now: datetime) -> None:
    """Parked and failing ride the lanes dict /api/state already sends."""
    bus = state.get("bus")
    lanes = bus.get("lanes") if isinstance(bus, dict) else None
    if not isinstance(lanes, dict):
        return
    try:
        found = jobs(root, now).get("lanes") or {}
    except (OSError, ValueError):
        return
    for name, row in found.items():
        if not isinstance(name, str) or not isinstance(row, dict):
            continue
        flag = {"parked": row.get("parked", 0), "failing": row.get("failing") is True}
        slot = lanes.get(name)
        if isinstance(slot, dict):
            slot.update(flag)
        else:
            lanes[name] = flag


def _modules():
    """Import after --lab sets ATLAS_LAB. collect reads that env once, at import.

    Once per process: importing again would rebind Poller and wipe a monkeypatch.
    """
    global brief, who, _NY, snapshot, Watcher, Poller, empty, build, detect
    global accept_op, inbox_for, budget, jobs, roadmap, TokenSetupError, accept, ensure_token, LAB, LAB_DIR
    global accept_alert, close_alert, close_alert_ui, list_alerts, _ui_gate
    global brief, who, _NY, snapshot, Watcher, Poller, empty, enabled, build, detect
    global accept_op, inbox_for, budget, jobs, roadmap, TokenSetupError, accept, ensure_token, LAB, LAB_DIR
    global accept_alert, close_alert, close_alert_ui, list_alerts
    global feedback_summary, record_feedback
    global roles_read, set_roles, set_switch
    global _imported
    if _imported:
        return
    from atlas.brief import brief, who
    from atlas.collect import LAB, LAB_DIR, _NY, snapshot
    from atlas.events import Watcher
    from atlas.github import Poller, empty, enabled
    from atlas.graph import build
    from atlas.problems import detect
    from atlas.handoffs import accept_op, inbox_for
    from atlas.budget import budget
    from atlas.jobs import jobs
    from atlas.roadmap import roadmap
    from atlas.write import TokenSetupError, accept, ensure_token
    from atlas.alerts import _ui_gate, accept_alert, close_alert, close_alert_ui, list_alerts
    from atlas.feedback import feedback_summary, record_feedback
    from atlas.roles_config import read as roles_read, set_roles, set_switch
    _imported = True


def _roles_post(handler, path: str) -> tuple[int, dict]:
    """Page POST. No lab is 404 before the nonce gate used by /api/feedback."""
    if not LAB or LAB_DIR is None:
        return 404, {"error": "no lab"}
    data = _ui_gate(handler.server.ui_nonce, handler.headers, handler.rfile, handler.server.server_address[1])
    if isinstance(data, tuple):
        return data
    if path == "/api/roles":
        if "roles" not in data:
            return 400, {"error": "Expected a roles object."}
        goal = None
        if "goal" in data:
            if not isinstance(data["goal"], str):
                return 400, {"error": "goal must be text."}
            goal = data["goal"]
        return set_roles(LAB_DIR, data["roles"], goal)
    if any(key not in data for key in ("tool", "name", "value")):
        return 400, {"error": "Expected tool, name, and value."}
    return set_switch(LAB_DIR, data["tool"], data["name"], data["value"])


_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "font-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
# WHY: allowlist, not a directory join. /graph.js/../collect.py and /signal/../collect.py must 404.
_STATIC = Path(__file__).resolve().parent / "static"
_PAGE = {
    "/": (_STATIC / "app.html", "text/html; charset=utf-8"),
    "/map": (_STATIC / "index.html", "text/html; charset=utf-8"),
    "/app.css": (_STATIC / "app.css", "text/css; charset=utf-8"),
    "/app.js": (_STATIC / "app.js", "text/javascript; charset=utf-8"),
    "/views/now.js": (_STATIC / "views" / "now.js", "text/javascript; charset=utf-8"),
    "/views/messages.js": (_STATIC / "views" / "messages.js", "text/javascript; charset=utf-8"),
    "/views/messages.css": (_STATIC / "views" / "messages.css", "text/css; charset=utf-8"),
    "/views/setup.js": (_STATIC / "views" / "setup.js", "text/javascript; charset=utf-8"),
    "/views/setup.css": (_STATIC / "views" / "setup.css", "text/css; charset=utf-8"),
    "/views/today.js": (_STATIC / "views" / "today.js", "text/javascript; charset=utf-8"),
    "/views/today.css": (_STATIC / "views" / "today.css", "text/css; charset=utf-8"),
    "/views/repos.js": (_STATIC / "views" / "repos.js", "text/javascript; charset=utf-8"),
    "/views/repos.css": (_STATIC / "views" / "repos.css", "text/css; charset=utf-8"),
    "/graph.css": (_STATIC / "graph.css", "text/css; charset=utf-8"),
    "/graph.js": (_STATIC / "graph.js", "text/javascript; charset=utf-8"),
    "/status.js": (_STATIC / "status.js", "text/javascript; charset=utf-8"),
    "/atlas-theme.js": (_STATIC / "atlas-theme.js", "text/javascript; charset=utf-8"),
}
_SIGNAL_ROOT = _STATIC / "signal"
_SIGNAL = {
    "/signal/fonts.css": _SIGNAL_ROOT / "fonts.css",
    "/signal/layout.css": _SIGNAL_ROOT / "layout.css",
    "/signal/signal.css": _SIGNAL_ROOT / "signal.css",
    "/signal/color.css": _SIGNAL_ROOT / "color.css",
    "/signal/fonts/schibsted-grotesk-latin-wght-normal.woff2": _SIGNAL_ROOT / "fonts" / "schibsted-grotesk-latin-wght-normal.woff2",
    "/signal/fonts/geist-mono-latin-wght-normal.woff2": _SIGNAL_ROOT / "fonts" / "geist-mono-latin-wght-normal.woff2",
}
_SIGNAL_TYPE = {".css": "text/css; charset=utf-8", ".woff2": "font/woff2"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        self._respond(head=False)

    def do_HEAD(self):
        self._respond(head=True)

    def _method_not_allowed(self):
        if not self._host_ok():
            return
        self._send(405, b"", "text/plain")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed

    def do_POST(self):
        self.close_connection = True  # a rejected body must not be parsed as a second request
        if not self._host_ok():
            return
        path = urlsplit(self.path).path
        if path == "/api/post":
            code, payload = accept(self.server.root, self.server.token, self.headers, self.rfile)
        elif path in ("/api/handoff", "/api/claim", "/api/done"):
            code, payload = accept_op(
                self.server.root, self.server.token, self.headers, self.rfile, path.rsplit("/", 1)[1]
            )
        elif path == "/api/alerts":
            code, payload = accept_alert(
                self.server.root, self.server.token, self.headers, self.rfile, ring=self.server.ring
            )
        elif path.startswith("/api/alerts/") and path.endswith("/close"):
            ident = path[len("/api/alerts/"):-len("/close")]
            code, payload = close_alert_ui(
                self.server.root, self.server.ui_nonce, self.headers, self.rfile, ident,
                self.server.server_address[1],
            )
        elif path.startswith("/api/alerts/") and path.endswith("/done"):
            ident = path[len("/api/alerts/"):-len("/done")]
            code, payload = close_alert(self.server.root, self.server.token, self.headers, self.rfile, ident)
        elif path == "/api/feedback":
            code, payload = record_feedback(
                self.server.root, self.server.ui_nonce, self.headers, self.rfile, self.server.server_address[1],
            )
        elif path in ("/api/roles", "/api/roles/switch"):
            code, payload = _roles_post(self, path)
        elif path in ("/api/approve", "/api/jobs/mark"):
            # Page gate only. A CLI token is not enough, and it is not consulted.
            if not LAB or LAB_DIR is None:
                code, payload = 404, {"error": "no lab"}
            else:
                data = _ui_gate(
                    self.server.ui_nonce, self.headers, self.rfile, self.server.server_address[1]
                )
                if isinstance(data, tuple):
                    code, payload = data
                else:
                    from atlas.screens import approve, mark

                    code, payload = (approve if path == "/api/approve" else mark)(LAB_DIR, data)
        elif path == "/api/report":
            from atlas.job_report import accept_report

            # The route exists only in lab mode. Elsewhere it is missing, same as an unknown GET.
            if not LAB or LAB_DIR is None:
                self._send(404, b"", "text/plain")
                return
            code, payload = accept_report(LAB_DIR, self.server.token, self.headers, self.rfile)
        else:
            self._send(405, b"", "text/plain")
            return
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        busy = code == 503 and isinstance(payload, dict) and payload.get("error") == "busy, retry"
        self._send(code, body, "application/json", retry=busy)

    def _host_ok(self) -> bool:
        # WHY: DNS rebinding. A public name can resolve to loopback; only these Host values are this process.
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        if host.lower() in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return True
        self._send(403, b"", "text/plain")
        return False

    def _respond(self, head: bool):
        if not self._host_ok():
            return
        path = urlsplit(self.path).path
        if path == "/api/events":
            self._events(head)
        elif path == "/api/roadmap":
            payload = roadmap(self.server.root, datetime.now(_NY).date())
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json", head=head)
        elif path == "/api/budget":
            payload = budget(self.server.root, datetime.now(_NY))
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json", head=head)
        elif path == "/api/jobs":
            payload = jobs(self.server.root, datetime.now(_NY))
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json", head=head)
        elif path == "/api/alerts":
            payload = list_alerts(self.server.root)
            payload["nonce"] = self.server.ui_nonce
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json", head=head)
        elif path == "/api/feedback":
            body = json.dumps(feedback_summary(self.server.root), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json", head=head)
        elif path == "/api/roles":
            code, payload = ((200, roles_read(LAB_DIR)) if LAB and LAB_DIR is not None
                             else (404, {"error": "no lab"}))
            self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json", head=head)
        elif path in ("/api/messages", "/api/now"):
            if not LAB or LAB_DIR is None:
                code, payload = 404, {"error": "no lab"}
            else:
                from atlas.screens import messages, now as screen_now

                if path == "/api/now":
                    code, payload = 200, screen_now(LAB_DIR)
                else:
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                    code, payload = messages(LAB_DIR, query)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json", head=head)
        elif path in ("/api/today", "/api/repos"):
            if not LAB or LAB_DIR is None:
                code, payload = 404, {"error": "no lab"}
            else:
                import atlas.github as ghmod
                from atlas.screens import repos as screen_repos, today as screen_today

                poller = self.server.poller
                snap = poller.snapshot() if poller else empty()
                clock = datetime.now(timezone.utc)
                on = ghmod.enabled(Path(LAB_DIR))
                if path == "/api/today":
                    payload = screen_today(LAB_DIR, clock, github_on=on, snapshot=snap)
                else:
                    # ponytail: github_unreadable is read off this snapshot (updated is null, or stale). No extra gh call.
                    payload = screen_repos(LAB_DIR, clock, github_on=on, snapshot=snap)
                code = 200
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json", head=head)
        elif path == "/api/repo-check":
            from atlas.repocheck import check

            body = json.dumps(check(LAB_DIR if LAB else None), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json", head=head)
        elif path == "/api/hooks":
            from atlas.hookcheck import list_hooks
            body = json.dumps({"hooks": list_hooks(Path.home(), LAB_DIR if LAB else None)}, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json", head=head)
        elif path == "/api/first-finding":
            from atlas.first_finding import NONE, top

            body = json.dumps(
                {"finding": top(LAB_DIR if LAB else None, Path.home()), "none_line": NONE},
                ensure_ascii=False,
            ).encode("utf-8")
            self._send(200, body, "application/json", head=head)
        elif path == "/api/folders":
            from atlas.adopt import folder_view

            body = json.dumps(folder_view(self.server.root), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json", head=head)
        elif path.startswith("/projects/"):
            from atlas.adopt import start_here_bytes

            body = start_here_bytes(self.server.root, path)
            if body is None:
                self._send(404, b"", "text/plain", head=head)
            else:
                self._send(200, body, "text/plain; charset=utf-8", head=head)
        elif path == "/api/inbox":
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            code, payload = inbox_for(self.server.root, query.get("me", ""))
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json", head=head)
        elif path == "/api/setup/tools":
            if not LAB or LAB_DIR is None:
                code, payload = 404, {"error": "no lab"}
            else:
                from atlas.detect import SUPPORTED, find_tools, suggest_seats

                rows = find_tools(os.environ.get("PATH", ""), Path.home())
                code, payload = 200, {
                    "tools": rows,
                    "supported": list(SUPPORTED),
                    "suggested": suggest_seats(rows),
                }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json", head=head)
        elif path == "/api/setup/check":
            if not LAB or LAB_DIR is None:
                code, payload = 404, {"error": "no lab"}
            else:
                from atlas.setup_check import queue_check

                code, payload = 200, queue_check(LAB_DIR)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json", head=head)
        elif path == "/api/setup":
            from atlas.kit import setup_status

            body = json.dumps(setup_status(self.server.root), ensure_ascii=True, sort_keys=True).encode("utf-8")
            self._send(200, body, "application/json", head=head)
        elif path == "/api/setup/dry-run":
            from atlas.kit import dry_run_text

            body = dry_run_text(self.server.root).encode("utf-8")
            self._send(200, body, "text/plain; charset=utf-8", head=head)
        elif path in ("/api/state", "/api/problems", "/api/brief", "/api/who"):
            now = datetime.now(timezone.utc)
            state = snapshot(self.server.root)
            poller = self.server.poller
            state["github"] = poller.snapshot() if poller else empty()
            state["graph"] = build(state, self.server.root, now, self.server.owner_name)
            _attach_lane_flags(state, self.server.root, now)
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            code, payload = 200, state
            if path == "/api/problems":
                payload = detect(state, state["graph"], self.server.root, now, disk=not self.server.demo)
                if query.get("owner"):
                    payload = [item for item in payload if item["owner"] == query["owner"]]
            elif path == "/api/brief":
                payload = brief(state, state["graph"], self.server.root, now, query.get("me", ""), disk=not self.server.demo)
                if payload is None:
                    code, payload = 404, {"error": "unknown node"}
            elif path == "/api/who":
                payload = who(state["graph"], query.get("q", ""))
            if path == "/api/state" and isinstance(payload, dict):
                payload = dict(payload)
                payload["nonce"] = self.server.ui_nonce
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json", head=head)
        elif path in _PAGE:
            asset, content_type = _PAGE[path]
            self._send(200, asset.read_bytes(), content_type, head=head)
        elif path in _SIGNAL:
            asset = _SIGNAL[path]
            self._send(200, asset.read_bytes(), _SIGNAL_TYPE[asset.suffix], head=head)
        else:
            self._send(404, b"", "text/plain", head=head)

    def _send(self, code: int, body: bytes, content_type: str, head: bool = False, retry: bool = False):
        self.send_response(code)
        if retry:
            self.send_header("Retry-After", "1")  # out of file handles for a moment, not a missing inbox
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _events(self, head: bool):
        watcher = getattr(self.server, "watcher", None)
        if watcher is None:
            self._send(404, b"", "text/plain", head=head)
            return
        # WHY: a reconnect must not replay history. Ignore Last-Event-ID and start at now.
        seq = 0 if head else watcher.latest_seq()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if head:
            return
        try:
            self.wfile.flush()
            while not watcher.stopped:
                watcher.wait(seq, 15)
                if watcher.stopped:
                    break
                events, seq = watcher.events_after(seq)
                if not events:
                    self.wfile.write(b": hb\n\n")
                else:
                    for event in events:
                        payload = json.dumps(event, ensure_ascii=False)
                        frame = f"id: {event['id']}\nevent: {event['kind']}\ndata: {payload}\n\n"
                        self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


class _Server(ThreadingHTTPServer):
    # WHY: an open SSE stream is a request thread. Daemons let shutdown finish.
    daemon_threads = True
    watcher = None
    poller = None
    demo = False
    ring = None

    def server_bind(self):
        # WHY: HTTPServer.server_bind asks DNS for the bound address's name (socket.getfqdn). Where reverse
        # DNS is slow, every start waits on it. We bind 127.0.0.1 only and never read server_name.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def server_close(self):
        if self.poller is not None:
            self.poller.stop()
        watcher = self.watcher
        self.watcher = None
        if watcher is not None:
            watcher.stop()
        super().server_close()


def make_server(
    root: Path,
    port: int,
    watch: bool = True,
    owner_name: str = "Owner",
    demo: bool = False,
    ring: bool = False,
) -> ThreadingHTTPServer:
    _modules()
    root = Path(root)
    try:
        token = ensure_token(root)
    except OSError as err:
        raise TokenSetupError(
            f"atlas: cannot set up the write token in {root / '.atlas'}: {err.strerror}"
        ) from err
    httpd = _Server(("127.0.0.1", port), Handler)
    httpd.root = root
    httpd.owner_name = owner_name
    httpd.token = token
    httpd.ui_nonce = secrets.token_urlsafe(24)
    httpd.demo = demo
    if ring:
        from atlas.ring import ring as _ring

        httpd.ring = _ring
    if watch:
        httpd.watcher = Watcher(httpd.root)
        httpd.watcher.start()
        # WHY: GitHub is opt-in via the lab atlas.json. Off and --demo never construct the poller, so they cannot reach gh.
        # A bad repos.json must not stop the dashboard.
        lab_dir = LAB_DIR if LAB and LAB_DIR is not None else root
        if demo or not enabled(lab_dir):
            httpd.poller = None
        else:
            try:
                httpd.poller = Poller()
            except (OSError, ValueError):
                httpd.poller = None
            else:
                httpd.poller.start()
    return httpd


def main(argv: list[str] | None = None, prog: str = "heliopause") -> None:
    args_list = sys.argv[1:] if argv is None else list(argv)
    if args_list and args_list[0] == "demo-path":
        # Installed tree: demo/generic sits next to the package, not in the caller's cwd.
        print(Path(__file__).resolve().parent.parent / "demo" / "generic")
        return
    if args_list and args_list[0] == "init":
        from atlas import init

        # A declined or non-interactive init must exit non-zero.
        raise SystemExit(init.run(args_list[1:]))
    if args_list and args_list[0] == "hooks":
        from atlas import hooks

        # A declined or non-interactive hooks run must exit non-zero.
        raise SystemExit(hooks.run(args_list[1:]))
    if args_list and args_list[0] == "adopt":
        from atlas import adopt

        raise SystemExit(adopt.run(args_list[1:]))
    if args_list and args_list[0] == "hook":
        from atlas.plugin import hook

        raise SystemExit(hook(args_list[1:]))
    if args_list and args_list[0] == "plugin":
        from atlas.plugin import run as plugin_run

        raise SystemExit(plugin_run(args_list[1:]))
    if args_list and args_list[0] in ("up", "doctor", "uninstall"):
        from atlas import lifecycle

        raise SystemExit(getattr(lifecycle, args_list[0])(args_list[1:]))
    if args_list and args_list[0] == "bus":
        from atlas.bus import run as bus_run

        raise SystemExit(bus_run(args_list[1:]))
    if args_list and args_list[0] == "connector":
        from atlas.connectors import run as connector_run

        raise SystemExit(connector_run(args_list[1:]))
    if args_list and args_list[0] == "report":
        from atlas.job_report import run as report_run

        raise SystemExit(report_run(args_list[1:]))
    if args_list and args_list[0] == "hooks-list":
        from atlas.hookcheck import run as hooks_list
        raise SystemExit(hooks_list(args_list[1:]))
    if args_list and args_list[0] in ("memory", "decide", "recall"):
        from atlas import memory

        fn = memory.run if args_list[0] == "memory" else getattr(memory, args_list[0])
        raise SystemExit(fn(args_list[1:]))
    if args_list and args_list[0] == "verify":
        from atlas.verify import main as verify_main

        raise SystemExit(verify_main(args_list[1:]))
    if args_list and args_list[0] in ("tools", "export", "diff"):
        from atlas.kit import main as kit_main

        raise SystemExit(kit_main(args_list))
    if args_list and args_list[0] in ("brief", "approve"):
        from atlas.cli import main as cli_main

        raise SystemExit(cli_main(args_list, prog=prog))

    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--port", type=int, default=4300)
    parser.add_argument("--root", type=Path, default=Path.home())
    parser.add_argument("--owner-name", default="Owner")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--no-ring", action="store_true")
    parser.add_argument("--lab", type=Path)
    args = parser.parse_args(argv)
    if args.lab and args.demo:
        print("atlas: --lab and --demo are mutually exclusive", file=sys.stderr)
        raise SystemExit(2)
    if args.lab:
        from atlas import labfs

        labfs.refuse_lab(args.lab, "atlas")
        # Absolute, before make_server imports collect. collect reads ATLAS_LAB at import.
        os.environ["ATLAS_LAB"] = str(args.lab.expanduser().resolve())
    try:
        httpd = make_server(
            args.root.expanduser(),
            args.port,
            owner_name=args.owner_name,
            demo=args.demo,
            ring=not args.no_ring and not args.demo and sys.platform == "darwin",
        )
    except OSError as err:
        raise SystemExit(f"atlas: can't listen on 127.0.0.1:{args.port} ({err.strerror}). Is Heliopause already running? Try --port.")
    except Exception as err:
        if type(err).__name__ == "LabConfigError":
            print(err, file=sys.stderr)
            raise SystemExit(2) from None
        if type(err).__name__ == "TokenSetupError":
            print(err, file=sys.stderr)
            raise SystemExit(1) from None
        raise
    print(f"Heliopause on http://127.0.0.1:{httpd.server_address[1]}/api/state (Ctrl-C stops)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return


# Tests import this module with ATLAS_LAB already final (unset, or set in the environment).
# `python -m atlas` may still pass --lab; main() exports that before make_server imports.
if __name__ != "__main__":
    _modules()


if __name__ == "__main__":
    main()

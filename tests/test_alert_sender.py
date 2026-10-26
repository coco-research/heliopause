from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import subprocess
import sys
import threading

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "atlas-alert"


def test_alert_success(tmp_path: Path) -> None:
    token_dir = tmp_path / ".atlas"
    token_dir.mkdir(parents=True, exist_ok=True)
    (token_dir / "token").write_text("token-abc-123\n", encoding="utf-8")

    recorded: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            recorded.append({
                "path": self.path,
                "headers": dict(self.headers),
                "body": json.loads(body.decode("utf-8")),
            })
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true, "id": "alert123"}')

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        env = {
            "HOME": str(tmp_path),
            "ATLAS_URL": url,
            "ATLAS_ALERT_SOURCE": "test-suite",
        }
        cmd = [
            sys.executable,
            str(SCRIPT),
            "warn",
            "ci-red",
            "CI failed on main",
            "https://github.com/coco-research/heliopause/actions/runs/12345",
        ]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        assert res.returncode == 0
        assert len(recorded) == 1
        req = recorded[0]
        assert req["path"] == "/api/alerts"
        assert req["headers"].get("X-Atlas-Token") == "token-abc-123"
        assert req["headers"].get("Content-Type") == "application/json"
        assert req["body"] == {
            "source": "test-suite",
            "kind": "ci-red",
            "severity": "warn",
            "text": "CI failed on main",
            "link": "https://github.com/coco-research/heliopause/actions/runs/12345",
        }

        # Also test without optional link and default source "lab"
        env_default = {"HOME": str(tmp_path), "ATLAS_URL": url}
        cmd2 = [sys.executable, str(SCRIPT), "info", "ping", "Ping text"]
        res2 = subprocess.run(cmd2, env=env_default, capture_output=True, text=True)
        assert res2.returncode == 0
        assert len(recorded) == 2
        req2 = recorded[1]
        assert req2["body"] == {
            "source": "lab",
            "kind": "ping",
            "severity": "info",
            "text": "Ping text",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_server_down_closed_port(tmp_path: Path) -> None:
    token_dir = tmp_path / ".atlas"
    token_dir.mkdir(parents=True, exist_ok=True)
    token = "secret-token-down-999"
    (token_dir / "token").write_text(token + "\n", encoding="utf-8")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    env = {"HOME": str(tmp_path), "ATLAS_URL": f"http://127.0.0.1:{port}"}
    cmd = [sys.executable, str(SCRIPT), "warn", "ci-red", "CI red on main"]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)

    assert res.returncode == 0
    assert token not in res.stderr
    assert token not in res.stdout
    assert "atlas-alert:" in res.stderr


def test_server_answers_500(tmp_path: Path) -> None:
    token_dir = tmp_path / ".atlas"
    token_dir.mkdir(parents=True, exist_ok=True)
    token = "secret-token-500-xxx"
    (token_dir / "token").write_text(token + "\n", encoding="utf-8")

    recorded: list[str] = []

    class Handler500(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            recorded.append(self.path)
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "internal error"}')

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler500)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        env = {"HOME": str(tmp_path), "ATLAS_URL": url}
        cmd = [sys.executable, str(SCRIPT), "needs-owner", "review", "Need review"]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)

        assert res.returncode == 0
        assert len(recorded) == 1  # exactly one request (no retry)
        assert token not in res.stderr
        assert token not in res.stdout
        assert "atlas-alert:" in res.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_non_loopback_url(tmp_path: Path) -> None:
    token_dir = tmp_path / ".atlas"
    token_dir.mkdir(parents=True, exist_ok=True)
    (token_dir / "token").write_text("secret-token-loopback\n", encoding="utf-8")

    for non_loopback in ("http://example.com:4300", "http://192.168.1.50:4300", "http://10.0.0.1:4300", "file://localhost/etc/hosts", "ftp://127.0.0.1/x"):
        env = {"HOME": str(tmp_path), "ATLAS_URL": non_loopback}
        cmd = [sys.executable, str(SCRIPT), "info", "ping", "Ping"]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        assert res.returncode == 0
        assert res.stdout == ""
        assert "not sent" in res.stderr and "secret-token-loopback" not in res.stderr


def test_bad_args(tmp_path: Path) -> None:
    bad_cases = [
        [],
        ["warn"],
        ["warn", "kind"],
        ["warn", "kind", "text", "link", "extra"],
        ["invalid-severity", "kind", "text"],
    ]
    for args in bad_cases:
        cmd = [sys.executable, str(SCRIPT)] + args
        res = subprocess.run(cmd, env={"HOME": str(tmp_path)}, capture_output=True, text=True)
        assert res.returncode == 2
        assert "usage: atlas-alert <severity> <kind> <text> [link]" in res.stderr


def test_missing_token_file(tmp_path: Path) -> None:
    # No .atlas/token created
    env = {"HOME": str(tmp_path), "ATLAS_URL": "http://127.0.0.1:4300"}
    cmd = [sys.executable, str(SCRIPT), "info", "test", "Text"]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert "atlas-alert:" in res.stderr


def _serve(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_redirect_never_carries_the_token(tmp_path: Path) -> None:
    (tmp_path / ".atlas").mkdir()
    (tmp_path / ".atlas" / "token").write_text("redirect-secret-77\n", encoding="utf-8")
    caught: list[str] = []

    class Catch(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            caught.append(self.headers.get("X-Atlas-Token", ""))
            self.send_response(201)
            self.end_headers()

        do_GET = do_POST

        def log_message(self, *args: object) -> None:
            pass

    other = _serve(Catch)

    class Bounce(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{other.server_address[1]}/steal")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    first = _serve(Bounce)
    try:
        env = {"HOME": str(tmp_path), "ATLAS_URL": f"http://127.0.0.1:{first.server_address[1]}"}
        res = subprocess.run([sys.executable, str(SCRIPT), "info", "ping", "Ping"], env=env, capture_output=True, text=True)
        assert res.returncode == 0 and caught == []
        assert "302" in res.stderr
        assert "redirect-secret-77" not in res.stderr
    finally:
        for server in (first, other):
            server.shutdown()
            server.server_close()


def test_bad_token_file_is_never_echoed(tmp_path: Path) -> None:
    (tmp_path / ".atlas").mkdir()
    (tmp_path / ".atlas" / "token").write_text("bad\r\nInjected: 1\n", encoding="utf-8")
    env = {"HOME": str(tmp_path), "ATLAS_URL": "http://127.0.0.1:9"}
    res = subprocess.run([sys.executable, str(SCRIPT), "info", "ping", "Ping"], env=env, capture_output=True, text=True)
    assert res.returncode == 0 and "Injected" not in res.stderr and "invalid token file" in res.stderr


def test_token_file_with_control_char_is_never_echoed(tmp_path: Path) -> None:
    (tmp_path / ".atlas").mkdir()
    token = "ab\tcd"
    (tmp_path / ".atlas" / "token").write_text(token + "\n", encoding="utf-8")
    env = {"HOME": str(tmp_path), "ATLAS_URL": "http://127.0.0.1:9"}
    res = subprocess.run([sys.executable, str(SCRIPT), "info", "ping", "Ping"], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert "invalid token file" in res.stderr
    assert token not in res.stderr and "ab\tcd" not in res.stderr


def test_http_error_reason_redacts_the_token(tmp_path: Path) -> None:
    (tmp_path / ".atlas").mkdir()
    token = "secret-token-error-echo"
    (tmp_path / ".atlas" / "token").write_text(token + "\n", encoding="utf-8")

    class HandlerEcho(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": token}).encode("utf-8"))

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = _serve(HandlerEcho)
    try:
        env = {"HOME": str(tmp_path), "ATLAS_URL": f"http://127.0.0.1:{server.server_address[1]}"}
        res = subprocess.run([sys.executable, str(SCRIPT), "info", "ping", "Ping"], env=env, capture_output=True, text=True)
        assert res.returncode == 0
        assert token not in res.stderr
        assert "[REDACTED]" in res.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_malformed_url_not_sent(tmp_path: Path) -> None:
    (tmp_path / ".atlas").mkdir()
    (tmp_path / ".atlas" / "token").write_text("secret-token-malformed\n", encoding="utf-8")
    for url in ("http://[::1", "http://[abc]/"):
        env = {"HOME": str(tmp_path), "ATLAS_URL": url}
        res = subprocess.run([sys.executable, str(SCRIPT), "info", "ping", "Ping"], env=env, capture_output=True, text=True)
        assert res.returncode == 0
        assert "not sent" in res.stderr
        assert "Traceback" not in res.stderr


def test_against_real_atlas_server(tmp_path: Path) -> None:
    from test_write import served

    with served(tmp_path) as server:  # root = tmp_path, so the token is at $HOME/.atlas/token
        env = {"HOME": str(tmp_path), "ATLAS_URL": f"http://127.0.0.1:{server.server_address[1]}"}
        ok = subprocess.run([sys.executable, str(SCRIPT), "needs-owner", "approval-wait", "A task waits"], env=env, capture_output=True, text=True)
        assert ok.returncode == 0 and ok.stderr == ""
        bad = subprocess.run([sys.executable, str(SCRIPT), "info", "Bad", "x"], env=env, capture_output=True, text=True)
        assert bad.returncode == 0 and "HTTP 400 bad kind" in bad.stderr
        stored = (tmp_path / ".atlas" / "alerts.jsonl").read_text(encoding="utf-8")
        assert '"source": "lab"' in stored and "A task waits" in stored and "Bad" not in stored

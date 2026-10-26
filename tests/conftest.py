import os
from pathlib import Path

# WHY: the public build ships the maps as {}; tests use their own copy.
os.environ.setdefault("ATLAS_MAPS_DIR", str(Path(__file__).parent / "fixtures" / "maps"))

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from atlas import collect, github


@pytest.fixture(autouse=True)
def no_real_gh(monkeypatch):
    # WHY: make_server(watch=True) starts the poller; tests must never reach the real gh.
    monkeypatch.setattr(github.Poller, "start", lambda self: None)


@pytest.fixture(autouse=True)
def no_real_busy(monkeypatch):
    monkeypatch.setattr(collect, "_busy", lambda lane, root: 0)


@pytest.fixture
def fake_atlas(monkeypatch):
    """Loopback server that answers every POST with `status`, a JSON `body` and an optional status-line `reason`.

    `raw` bytes replace the whole reply instead (not necessarily HTTP). Calling it starts one, points ATLAS_URL at it and
    returns the X-Atlas-Token values it receives. Stopped at teardown.
    """
    started = []

    def start(status=200, body=None, reason=None, raw=None):
        seen = []

        class Reply(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                seen.append(self.headers.get("X-Atlas-Token"))
                if raw is not None:
                    self.wfile.write(raw)
                    return
                data = json.dumps(body).encode("utf-8")
                self.send_response(status, reason)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt, *args):
                return

        server = HTTPServer(("127.0.0.1", 0), Reply)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        monkeypatch.setenv("ATLAS_URL", f"http://127.0.0.1:{server.server_address[1]}")
        return seen

    yield start
    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

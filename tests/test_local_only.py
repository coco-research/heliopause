"""AC-net: nothing leaves the machine. The server binds 127.0.0.1."""

from __future__ import annotations

import ast
import re
import socket
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from atlas.__main__ import make_server
from atlas.connectors import TOOLS, report

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
FIX = ROOT / "tests" / "fixtures" / "connectors"
_LOOP = {"127.0.0.1", "::1", "localhost"}
_NET = (
    "socket", "ssl", "http.client", "http.server", "urllib.request", "urllib3",
    "requests", "httpx", "smtplib", "ftplib", "telnetlib", "xmlrpc.client", "websocket",
)
_CHILD = {("http", "client"), ("http", "server"), ("urllib", "request"), ("xmlrpc", "client")}
_ALLOW_NET = {
    "atlas/__main__.py": frozenset({"http.server"}),  # http.server for the 127.0.0.1 server
    "atlas/cli.py": frozenset({"http.client", "urllib.request"}),  # urllib.request to the local server; http.client is HTTPException only
    "atlas/lifecycle.py": frozenset({"http.client", "socket"}),  # socket/http.client probe of 127.0.0.1
}
_ALLOW_CMD: dict[str, frozenset[str]] = {}  # no curl, wget, nc, scp, or "ssh " (ssh-keygen is exempt)
_CMD = re.compile(r"(?<![A-Za-z0-9_-])(curl|wget|nc|scp)(?![A-Za-z0-9_-])")
_SSH = re.compile(r"(?<![A-Za-z0-9_-])ssh ")
_PATHS = (
    "/", "/api/state", "/api/events", "/api/problems", "/api/brief?me=lead",
    "/api/roadmap", "/api/budget", "/api/jobs", "/api/alerts", "/api/feedback",
)


def _norm(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    return host.strip("[]").lower() if isinstance(host, str) else host


def _ip(addr):
    # AF_UNIX is a path, not an outbound IP connection.
    return _norm(addr[0]) if isinstance(addr, tuple) and addr else None


@pytest.fixture
def ac_net(monkeypatch):
    """Refuse and record any connect or getaddrinfo whose host is not loopback."""
    refused: list = []
    real = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)

    def refuse(item):
        refused.append(item)
        raise OSError("AC-net: outbound connection refused")

    def guard(addr):
        host = _ip(addr)
        if host is not None and host not in _LOOP:
            refuse(addr)

    def connect(self, addr):
        guard(addr)
        return real[0](self, addr)

    def connect_ex(self, addr):
        guard(addr)
        return real[1](self, addr)

    def create_connection(address, *args, **kwargs):
        guard(address)
        return real[2](address, *args, **kwargs)

    def getaddrinfo(host, port, *args, **kwargs):
        name = _norm(host)
        if name not in (None, "") and name not in _LOOP:
            refuse(("getaddrinfo", host, port))
        return real[3](host, port, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return refused


def _get(port: int, path: str) -> int:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    try:
        if path == "/api/events":
            # will_close hands the socket to the response; read a little, then close.
            resp.fp.raw._sock.settimeout(0.2)
            try:
                resp.read(8)
            except OSError:
                pass
        else:
            resp.read()
        return resp.status
    finally:
        conn.close()


def test_runtime_stays_on_loopback(ac_net, tmp_path):
    server = make_server(HOME, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.server_address[0] == "127.0.0.1"
        got = {path: _get(server.server_address[1], path) for path in _PATHS}
        assert all(100 <= code <= 599 for code in got.values()), got
        lab = tmp_path / "lab"
        lab.mkdir()
        for tool in TOOLS:
            assert report(lab, tool, sorted(FIX.glob(tool + "-*.json"))[0].read_bytes()) is True
    except OSError as err:
        if str(err) == "AC-net: outbound connection refused":
            pytest.fail(f"outbound connection: {ac_net}")
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert ac_net == [], ac_net


def _files():
    return sorted((ROOT / "atlas").rglob("*.py"))


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _imports(path: Path) -> frozenset[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        mods, names = [], []
        if isinstance(node, ast.Import):
            mods = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods, names = [node.module], [alias.name for alias in node.names]
            if node.module == "asyncio" and "open_connection" in names:
                found.add("asyncio")
        for mod in mods:
            found.update(net for net in _NET if mod == net or mod.startswith(net + "."))
        if len(mods) == 1:
            found.update(f"{mods[0]}.{child}" for child in names if (mods[0], child) in _CHILD)
        called = (
            isinstance(node, ast.Attribute) and node.attr == "open_connection"
            and isinstance(node.value, ast.Name) and node.value.id == "asyncio"
        )
        if called:
            found.add("asyncio")
    return frozenset(found)


def test_network_imports_match_allowlist():
    found = {_rel(path): mods for path in _files() if (mods := _imports(path))}
    new = sorted(set(found) - set(_ALLOW_NET))
    assert not new, "new module imports a network library: " + ", ".join(new)
    assert found == _ALLOW_NET


def _commands() -> dict[str, frozenset[str]]:
    found = {}
    for path in _files():
        hits: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            texts = []
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
                raw = node.value
                texts.append(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
            elif isinstance(node, ast.JoinedStr):
                texts.append("".join(
                    part.value for part in node.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                ))
            for text in texts:
                text = text.replace("ssh-keygen", "")
                hits.update(_CMD.findall(text))
                if _SSH.search(text):
                    hits.add("ssh ")
        if hits:
            found[_rel(path)] = frozenset(hits)
    return found


def test_no_new_network_command():
    found = _commands()
    new = sorted(set(found) - set(_ALLOW_CMD))
    assert not new, "new network command string: " + ", ".join(new)
    assert found == _ALLOW_CMD

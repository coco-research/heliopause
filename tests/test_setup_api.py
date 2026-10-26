"""GET /api/setup/tools suggestions, and the project line on POST /api/roles."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from atlas.detect import find_tools, suggest_seats
from atlas.roles_config import read, set_roles
from test_alerts import get
from test_write import served

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "e.g. A small web shop: api-server is the backend, web-app is the storefront."
_ORDER = ("claude-code", "cursor", "codex", "grok", "gemini", "opencode", "aider", "copilot")
_SUPPORTED = ["claude-code", "cursor", "codex", "grok"]


def _rows(found):
    return [
        {"id": ident, "name": ident, "found": ident in found, "cli": None, "app": None,
         "config_dir": None, "install_url": "https://example.test/" + ident}
        for ident in _ORDER
    ]


@pytest.mark.parametrize("found,want", [
    (("claude-code", "cursor", "grok"), {"hq": "you", "lab": "claude-code", "factory": ["cursor"]}),
    (("claude-code",), {"hq": "you", "lab": "claude-code", "factory": ["claude-code"]}),
    (("cursor",), {"hq": "you", "lab": "cursor", "factory": ["cursor"]}),
    ((), None),
    (("opencode", "aider"), None),
    (("cursor", "codex"), {"hq": "you", "lab": "codex", "factory": ["cursor"]}),
    (("codex", "grok"), {"hq": "you", "lab": "codex", "factory": ["grok"]}),
    (("claude-code", "cursor", "codex", "grok"), {"hq": "you", "lab": "claude-code", "factory": ["cursor"]}),
])
def test_suggest_seats(found, want):
    assert suggest_seats(_rows(found)) == want


def test_one_tool_can_sit_in_lab_and_factory(tmp_path):
    code, body = set_roles(tmp_path, {"hq": "you", "lab": "cursor", "factory": ["cursor"]})
    assert code == 200
    assert body["roles"] == {"hq": "you", "lab": "cursor", "factory": ["cursor"]}
    assert read(tmp_path)["roles"]["factory"] == ["cursor"]


def test_goal_stores_text_and_drops_empty_or_placeholder(tmp_path):
    path = tmp_path / "atlas.json"
    path.write_text('{"v": 1, "note": "keep"}\n', encoding="utf-8")
    roles = {"hq": "you", "lab": "claude-code", "factory": ["cursor"]}
    assert set_roles(tmp_path, roles, goal="  A shop  ")[0] == 200
    assert json.loads(path.read_text(encoding="utf-8"))["goal"] == "A shop"
    assert set_roles(tmp_path, roles)[0] == 200
    assert json.loads(path.read_text(encoding="utf-8"))["goal"] == "A shop"
    assert set_roles(tmp_path, roles, goal=" \n ")[0] == 200
    assert "goal" not in json.loads(path.read_text(encoding="utf-8"))
    assert set_roles(tmp_path, roles, goal=PLACEHOLDER)[0] == 200
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "goal" not in saved and saved["note"] == "keep" and PLACEHOLDER not in path.read_text(encoding="utf-8")


def test_tools_route_without_lab_is_404(tmp_path):
    with served(tmp_path) as server:
        status, raw = get(server, "/api/setup/tools")
        assert status == 404 and json.loads(raw) == {"error": "no lab"}


_HTTP = r"""
import json, os, threading, subprocess as sp
from http.client import HTTPConnection
from pathlib import Path
from atlas import detect
from atlas.detect import find_tools as real_find
from atlas.__main__ import make_server

def boom(*_a, **_k):
    raise RuntimeError("do not run a binary")

lab = Path(os.environ["ATLAS_LAB"])
server = make_server(lab, 0, watch=False)
threading.Thread(target=server.serve_forever, daemon=True).start()
port, nonce = server.server_address[1], server.ui_nonce

def req(method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()

for name in ("run", "Popen", "call", "check_call", "check_output"):
    setattr(sp, name, boom)
raised = False
try:
    sp.run(["true"])
except RuntimeError:
    raised = True

def tool(ident, found):
    return {"id": ident, "name": ident, "found": found, "cli": "/x" if found else None,
            "app": None, "config_dir": None, "install_url": "https://example.test/" + ident}

order = ["claude-code", "cursor", "codex", "grok", "gemini", "opencode", "aider", "copilot"]
def rows(found):
    return [tool(ident, ident in found) for ident in order]

out = {"raised": raised}
def hit(name, found):
    payload = rows(found)
    detect.find_tools = lambda *_a, **_k: payload
    status, body = req("GET", "/api/setup/tools")
    out[name] = {"status": status, "body": body, "rows": payload}

try:
    hit("trio", {"claude-code", "cursor", "grok"})
    hit("one", {"claude-code"})
    hit("none", set())
    hit("unsup", {"opencode", "aider"})
    home, apps = Path(os.environ["EMPTY_HOME"]), Path(os.environ["EMPTY_APPS"])
    detect.find_tools = lambda *_a, **_k: real_find("", home, apps)
    out["real"] = req("GET", "/api/setup/tools")
    gate = {"Origin": "http://127.0.0.1:%s" % port, "X-Atlas-UI": nonce,
            "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}
    j2 = {"roles": {"hq": "you", "lab": "cursor", "factory": ["cursor"]}}
    out["j2"] = req("POST", "/api/roles", json.dumps(dict(j2, goal="")).encode(), gate)
    out["bad"] = req("POST", "/api/roles", json.dumps(dict(j2, goal=1)).encode(), gate)
    out["goal"] = req("POST", "/api/roles", json.dumps(dict(j2, goal="  A shop  ")).encode(), gate)
    print(json.dumps(out))
finally:
    server.shutdown()
    server.server_close()
"""


def test_tools_route_suggestions_and_no_binary(tmp_path):
    doc = {"v": 1, "owner": "Owner", "tz": "America/New_York",
           "nodes": [{"id": "lead", "label": "Lead", "role": "lab"}], "note": "keep"}
    (tmp_path / "atlas.json").write_text(json.dumps(doc) + "\n", encoding="utf-8")
    empty_home, empty_apps = tmp_path / "home", tmp_path / "apps"
    empty_home.mkdir()
    empty_apps.mkdir()
    env = os.environ.copy()
    env["ATLAS_LAB"] = str(tmp_path)
    env["EMPTY_HOME"] = str(empty_home)
    env["EMPTY_APPS"] = str(empty_apps)
    proc = subprocess.run([sys.executable, "-c", _HTTP], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["raised"] is True
    trio = data["trio"]
    assert trio["status"] == 200 and trio["body"]["tools"] == trio["rows"]
    assert trio["body"]["supported"] == _SUPPORTED
    assert trio["body"]["suggested"] == {"hq": "you", "lab": "claude-code", "factory": ["cursor"]}
    assert data["one"]["body"]["suggested"] == {"hq": "you", "lab": "claude-code", "factory": ["claude-code"]}
    assert data["none"]["body"]["suggested"] is None
    assert data["unsup"]["body"]["suggested"] is None
    assert any(row["id"] == "opencode" and row["found"] for row in data["unsup"]["body"]["tools"])
    status, body = data["real"]
    assert status == 200 and body["tools"] == find_tools("", empty_home, empty_apps)
    assert body["suggested"] is None and body["supported"] == _SUPPORTED
    assert data["j2"][0] == 200 and data["j2"][1]["roles"] == {"hq": "you", "lab": "cursor", "factory": ["cursor"]}
    assert data["bad"] == [400, {"error": "goal must be text."}]
    assert data["goal"][0] == 200
    saved = json.loads((tmp_path / "atlas.json").read_text(encoding="utf-8"))
    assert saved["goal"] == "A shop" and saved["note"] == "keep"

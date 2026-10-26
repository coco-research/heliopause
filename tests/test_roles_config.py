"""Lab roles and switches: atlas.json seats, the gate, and the loopback routes."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from atlas.roles_config import GATED, read, set_roles, set_switch
from test_alerts import get
from test_write import call, served

ROOT = Path(__file__).resolve().parents[1]
_NAMES = ("self_start", "answer_asks", "approve", "receive_in_session")
_BAD = [
    {"hq": "nope"}, {"lab": "nope"}, {"factory": ["nope"]}, {"factory": ["codex", "codex"]},
    {"hq": "claude-code", "lab": "claude-code"}, {"hq": "claude-code", "factory": ["claude-code"]},
    {"lab": "codex", "factory": ["grok", "codex"]}, {"factory": ["claude-code", "codex", "cursor", "grok"]},
    {"lab": "you"}, {"factory": ["you"]}, {"hq": "you", "factory": ["codex", "you"]},
    None, [], "claude-code", {"hq": 1}, {"hq": True}, {"lab": ["codex"]}, {"factory": "codex"},
    {"factory": None}, {"factory": [1]}, {"boss": "claude-code"}, {"hq": "you", "extra": 1},
]
_OFF = "This switch is off until the next release."


def _seed(lab: Path, **extra) -> Path:
    doc = {"v": 1, "owner": "Owner", "tz": "America/New_York",
           "nodes": [{"id": "lead", "label": "Lead", "role": "lab"}], "note": "keep", **extra}
    path = lab / "atlas.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def test_read_missing_or_garbage(tmp_path):
    empty = {"roles": {"hq": None, "lab": None, "factory": []}, "switches": {}, "gated": sorted(GATED)}
    assert read(tmp_path) == empty
    for text in ("not json", "", "[]", "{"):
        (tmp_path / "atlas.json").write_text(text, encoding="utf-8")
        assert read(tmp_path) == empty


def test_read_ignores_invalid_values(tmp_path):
    _seed(tmp_path, roles={"hq": "nope", "lab": 3, "factory": ["codex", "codex", "you", "nope", "claude-code"]},
          switches={"codex": {"self_start": "yes", "receive_in_session": True},
                    "claude-code": {"approve": True}, "ghost": {"answer_asks": True}})
    got = read(tmp_path)
    assert got["roles"] == {"hq": None, "lab": None, "factory": ["codex", "claude-code"]}
    assert got["switches"]["codex"]["receive_in_session"] is True
    assert got["switches"]["codex"]["self_start"] is False
    assert got["switches"]["claude-code"]["approve"] is False and "ghost" not in got["switches"]


def test_set_roles_keeps_other_keys_and_mode(tmp_path):
    path = _seed(tmp_path, repos={"coco": "lead"})
    os.chmod(path, 0o640)
    before = json.loads(path.read_text(encoding="utf-8"))
    code, body = set_roles(tmp_path, {"hq": "you", "lab": "claude-code", "factory": ["codex", "cursor", "grok"]})
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert code == 200 and {key: saved[key] for key in before} == before
    assert stat.S_IMODE(path.stat().st_mode) == 0o640 and "you" not in body["switches"]
    assert set(body["switches"]) == {"claude-code", "codex", "cursor", "grok"}
    assert all(flags == {name: False for name in _NAMES} for flags in body["switches"].values())


@pytest.mark.parametrize("roles", _BAD)
def test_set_roles_rejects(tmp_path, roles):
    path = _seed(tmp_path)
    raw = path.read_bytes()
    code, body = set_roles(tmp_path, roles)
    assert code == 400 and body.get("error") and path.read_bytes() == raw


def test_gates_block_on_and_allow_off(tmp_path):
    path = _seed(tmp_path)
    assert set_roles(tmp_path, {"hq": "claude-code", "lab": "codex", "factory": ["cursor"]})[0] == 200
    pairs = (("self_start", "cursor"), ("answer_asks", "claude-code"),
             ("approve", "claude-code"), ("receive_in_session", "codex"))
    for name, tool in pairs:
        raw = path.read_bytes()
        assert set_switch(tmp_path, tool, name, True) == (403, {"error": _OFF}) and path.read_bytes() == raw
        assert set_switch(tmp_path, tool, name, False)[0] == 200


def test_approve_needs_answer_asks(tmp_path, monkeypatch):
    monkeypatch.setattr("atlas.roles_config.GATED", frozenset())
    path = _seed(tmp_path)
    assert set_roles(tmp_path, {"hq": "claude-code"})[0] == 200
    raw = path.read_bytes()
    assert set_switch(tmp_path, "claude-code", "approve", True)[0] == 400 and path.read_bytes() == raw
    assert set_switch(tmp_path, "claude-code", "answer_asks", True)[0] == 200
    assert set_switch(tmp_path, "claude-code", "approve", True)[0] == 200
    assert read(tmp_path)["switches"]["claude-code"]["approve"] is True


def test_switch_must_match_the_seat(tmp_path, monkeypatch):
    monkeypatch.setattr("atlas.roles_config.GATED", frozenset())
    path = _seed(tmp_path)
    assert set_roles(tmp_path, {"hq": "claude-code", "lab": "codex", "factory": ["cursor"]})[0] == 200
    raw = path.read_bytes()
    bad = (("claude-code", "self_start", True), ("codex", "answer_asks", True), ("cursor", "answer_asks", True),
           ("nope", "self_start", True), ("cursor", "nope", True), ("cursor", "self_start", 1))
    assert all(set_switch(tmp_path, *args)[0] == 400 for args in bad) and path.read_bytes() == raw
    assert set_switch(tmp_path, "cursor", "self_start", True)[0] == 200


def test_removed_seat_drops_switches(tmp_path, monkeypatch):
    monkeypatch.setattr("atlas.roles_config.GATED", frozenset())
    path = _seed(tmp_path)
    assert set_roles(tmp_path, {"hq": "claude-code", "lab": "grok", "factory": ["codex"]})[0] == 200
    assert set_switch(tmp_path, "claude-code", "answer_asks", True)[0] == 200
    assert set_switch(tmp_path, "codex", "self_start", True)[0] == 200
    assert set_switch(tmp_path, "grok", "receive_in_session", True)[0] == 200
    code, body = set_roles(tmp_path, {"lab": "grok"})
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert code == 200 and set(body["switches"]) == set(saved["switches"]) == {"grok"}
    assert saved["switches"]["grok"]["receive_in_session"] is True and saved["note"] == "keep" and saved["v"] == 1


def test_symlink_atlas_json_is_refused(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    target = tmp_path / "real.json"
    target.write_text('{"v": 1, "roles": {"hq": "grok"}, "keep": 1}\n', encoding="utf-8")
    raw = target.read_bytes()
    (lab / "atlas.json").symlink_to(target)
    assert read(lab)["roles"]["hq"] is None and set_roles(lab, {"hq": "claude-code"})[0] == 500
    assert target.read_bytes() == raw and (lab / "atlas.json").is_symlink()


def test_http_without_lab(tmp_path):
    with served(tmp_path) as server:
        status, raw = get(server, "/api/roles")
        assert status == 404 and json.loads(raw) == {"error": "no lab"}
        for path in ("/api/roles", "/api/roles/switch"):
            status, raw = call(server, path, body=b"{}")
            assert status == 404 and json.loads(raw) == {"error": "no lab"}


_HTTP = r"""
import json, os, threading
from http.client import HTTPConnection
from pathlib import Path
from atlas.__main__ import make_server
lab = Path(os.environ["ATLAS_LAB"])
server = make_server(lab, 0, watch=False)
threading.Thread(target=server.serve_forever, daemon=True).start()
port, nonce = server.server_address[1], server.ui_nonce
def req(method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()
try:
    gate = {"Origin": f"http://127.0.0.1:{port}", "X-Atlas-UI": nonce,
            "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}
    roles = json.dumps({"roles": {"hq": "you", "lab": "claude-code", "factory": ["codex"]}}).encode()
    on = json.dumps({"tool": "codex", "name": "self_start", "value": True}).encode()
    off = json.dumps({"tool": "codex", "name": "self_start", "value": False}).encode()
    print(json.dumps({"get": req("GET", "/api/roles"),
        "denied": req("POST", "/api/roles", roles, {**gate, "X-Atlas-UI": "nope"}),
        "post": req("POST", "/api/roles", roles, gate),
        "on": req("POST", "/api/roles/switch", on, gate),
        "off": req("POST", "/api/roles/switch", off, gate)}))
finally:
    server.shutdown(); server.server_close()
"""


def test_http_lab_roles(tmp_path):
    _seed(tmp_path)
    env = os.environ.copy()
    env["ATLAS_LAB"] = str(tmp_path)
    proc = subprocess.run([sys.executable, "-c", _HTTP], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["get"][0] == 200 and data["get"][1]["roles"]["hq"] is None and data["denied"][0] == 403
    assert data["post"][0] == 200 and data["post"][1]["roles"]["factory"] == ["codex"]
    assert data["on"] == [403, {"error": _OFF}]
    assert data["off"][0] == 200 and data["off"][1]["switches"]["codex"]["self_start"] is False
    saved = json.loads((tmp_path / "atlas.json").read_text(encoding="utf-8"))
    assert saved["v"] == 1 and saved["note"] == "keep" and saved["roles"]["hq"] == "you"
    assert saved["switches"]["codex"]["self_start"] is False

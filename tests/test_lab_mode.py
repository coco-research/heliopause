"""Lab mode reads nodes from ATLAS_LAB. The mode is fixed at import, so these tests use a subprocess."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "solo-lab"
PY = sys.executable
FORBIDDEN = ("brain-", "dept-", "hq-cto", "factory-queue", "Courier", "Chief of Staff")

_STATE = """
import json, os
from datetime import datetime, timezone
from pathlib import Path
from atlas.collect import NODE_IDS, snapshot
from atlas.github import empty
from atlas.graph import build
from atlas.problems import detect

root = Path(os.environ["ATLAS_LAB"])
now = datetime.now(timezone.utc)
state = snapshot(root)
state["github"] = empty()
state["graph"] = build(state, root, now)
problems = detect(state, state["graph"], root, now)
print(json.dumps({"ids": list(NODE_IDS), "state": state, "problems": problems}))
"""


def _run(args, env):
    return subprocess.run(
        [PY, *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
    )


def test_lab_state_has_only_configured_nodes():
    env = os.environ.copy()
    env["ATLAS_LAB"] = str(FIXTURE)
    proc = _run(["-c", _STATE], env)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert set(data["ids"]) == {"owner", "lead", "worker"}
    assert set(data["state"]["nodes"]) == {"owner", "lead", "worker"}
    assert set(data["state"]["graph"]["nodes"]) == {"owner", "lead", "worker"}
    blob = json.dumps(data["state"]) + json.dumps(data["problems"])
    for needle in FORBIDDEN:
        assert needle not in blob, needle


def test_lab_bare_stamps_and_owner_state():
    env = os.environ.copy()
    env["ATLAS_LAB"] = str(FIXTURE)
    proc = _run(["-c", _STATE], env)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    lead = data["state"]["graph"]["nodes"]["lead"]
    assert lead["state"] == "waits"
    assert lead["word"] == "Waits for you"
    assert lead["headline"] == "Need the owner"
    assert lead["ts"] == "2026-10-14T19:02:00Z"
    assert data["state"]["nodes"]["lead"]["status"] == "needs-owner"
    worker = data["state"]["nodes"]["worker"]
    assert worker["status"] == "done"
    assert worker["details"] == "Resolved: closed the loop"
    assert worker["ts"] == "2026-10-14T18:50:00Z"
    assert data["state"]["nodes"]["owner"]["status"] == "needs-owner"
    assert data["state"]["nodes"]["owner"]["ts"] == "2026-10-14T13:20:00Z"


_BARE = """
import json, os
from pathlib import Path
from atlas.collect import LAB, snapshot
root = Path(os.environ["ATLAS_ROOT"])
node = snapshot(root)["nodes"]["vp"]
print(json.dumps({"lab": LAB, "status": node["status"], "ts": node["ts"], "details": node["details"]}))
"""


def test_bare_stamp_ignored_outside_lab(tmp_path):
    board = tmp_path / ".heliopause" / "inbox" / "lab"
    board.mkdir(parents=True)
    (board / "board.md").write_text(
        "2026-10-14 09:00 ET · VP · done · Labeled still counts\n"
        "2026-10-14 15:02 · VP · working · Should be ignored\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("ATLAS_LAB", None)
    env["ATLAS_ROOT"] = str(tmp_path)
    proc = _run(["-c", _BARE], env)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data == {
        "lab": False,
        "status": "done",
        "ts": "2026-10-14T13:00:00Z",
        "details": "Labeled still counts",
    }


_WAIT = """
import json, os
from datetime import datetime, timezone
from pathlib import Path
from atlas.collect import LAB_LEAD, snapshot
from atlas.github import empty
from atlas.graph import build
from atlas.problems import detect

root = Path(os.environ["ATLAS_LAB"])
now = datetime.now(timezone.utc)
state = snapshot(root)
state["github"] = empty()
graph = build(state, root, now)
problems = detect(state, graph, root, now)
print(json.dumps({
    "lead": LAB_LEAD,
    "word": graph["nodes"]["worker"]["word"],
    "status": state["nodes"]["worker"]["status"],
    "owner": graph["nodes"]["owner"]["label"],
    "owners": [item["owner"] for item in problems if item["id"] == "decision-waiting:worker"],
}))
"""


def _waiting_lab(tmp_path, name, owner, nodes):
    directory = tmp_path / name
    directory.mkdir()
    body = {"v": 1, "owner": owner, "tz": "America/New_York", "nodes": nodes}
    (directory / "atlas.json").write_text(json.dumps(body), encoding="utf-8")
    stamp = (datetime.now(ZoneInfo("America/New_York")) - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")
    line = f"{stamp} \u00b7 Worker \u00b7 needs-lead \u00b7 Waiting on the lead\n"
    (directory / "board.md").write_text(line, encoding="utf-8")
    return directory


def _wait(directory):
    env = os.environ.copy()
    env["ATLAS_LAB"] = str(directory)
    proc = _run(["-c", _WAIT], env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_needs_lead_waits_on_the_first_lab_node(tmp_path):
    padded = _waiting_lab(tmp_path, "padded", "  Owner  ", [
        {"id": "lead", "label": " Lead ", "role": "lab"},
        {"id": "worker", "label": " Worker ", "role": "factory"},
    ])
    data = _wait(padded)
    assert data["lead"] == "lead"
    assert data["status"] == "needs-vp"
    assert data["word"] == "Waiting on Lead"
    assert data["owner"] == "Owner"
    assert data["owners"] == ["lead"]

    no_lead = _waiting_lab(tmp_path, "nolead", "Owner", [
        {"id": "worker", "label": "Worker", "role": "factory"},
    ])
    data = _wait(no_lead)
    assert data["lead"] == "owner"
    assert data["word"] == "Waiting on Owner"
    assert data["owners"] == ["owner"]


_POST = """
import json, os, threading
from http.client import HTTPConnection
from pathlib import Path
from atlas.__main__ import make_server

root = Path(os.environ["ATLAS_ROOT"])
lab = Path(os.environ["ATLAS_LAB"])
server = make_server(root, 0, watch=False)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    (root / ".atlas" / "actor").write_text("lead", encoding="utf-8")
    token = (root / ".atlas" / "token").read_text(encoding="utf-8")
    body = json.dumps({"state": "working", "headline": "Hello there", "details": "more here"}).encode()
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request("POST", "/api/post", body=body, headers={
        "X-Atlas-Token": token,
        "Content-Type": "application/json",
    })
    resp = conn.getresponse()
    payload = json.loads(resp.read())
    conn.close()
    board = (lab / "board.md").read_text(encoding="utf-8")
    if resp.status != 201 or payload.get("line") not in board:
        raise SystemExit(f"{resp.status} {payload}")
    parts = payload["line"].split(" \u00b7 ")
    if parts[1:5] != ["Lead", "working", "Hello there", "more here"]:
        raise SystemExit(repr(payload["line"]))
finally:
    server.shutdown()
    server.server_close()
"""


def test_lab_post_appends_as_lead(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "atlas.json").write_text((FIXTURE / "atlas.json").read_text(encoding="utf-8"), encoding="utf-8")
    before = (FIXTURE / "board.md").read_text(encoding="utf-8")
    (lab / "board.md").write_text(before, encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    env = os.environ.copy()
    env["ATLAS_LAB"] = str(lab)
    env["ATLAS_ROOT"] = str(root)
    proc = _run(["-c", _POST], env)
    assert proc.returncode == 0, proc.stderr
    text = (lab / "board.md").read_text(encoding="utf-8")
    assert text.startswith(before)
    assert "\u00b7 Lead \u00b7 working \u00b7 Hello there \u00b7 more here" in text
    assert " ET " not in text.splitlines()[-1]  # a lab board has no zone label


def test_bad_atlas_json_exits_2(tmp_path):
    cases = (
        ("dup", [
            {"id": "lead", "label": "Lead", "role": "lab"},
            {"id": "lead", "label": "Other", "role": "factory"},
        ], "duplicate id"),
        ("role", [
            {"id": "lead", "label": "Lead", "role": "nope"},
        ], "bad role"),
    )
    env = os.environ.copy()
    env.pop("ATLAS_LAB", None)
    for name, nodes, needle in cases:
        directory = tmp_path / name
        directory.mkdir()
        body = {"v": 1, "owner": "Owner", "tz": "America/New_York", "nodes": nodes}
        (directory / "atlas.json").write_text(json.dumps(body), encoding="utf-8")
        proc = _run(["-m", "atlas", "--lab", str(directory), "--port", "0"], env)
        assert proc.returncode == 2, proc.stderr
        assert needle in proc.stderr


def test_lab_board_lines_go_to_the_lead():
    # A worker's line is a message to the lab's lead, not to the built-in "vp".
    env = os.environ.copy()
    env["ATLAS_LAB"] = str(FIXTURE)
    code = (
        "import json; from atlas.events import board_event as b; "
        "print(json.dumps([b('2026-10-14 14:41 \\u00b7 Worker \\u00b7 working \\u00b7 Building it', 'a'), "
        "b('2026-10-14 14:42 \\u00b7 Lead \\u00b7 working \\u00b7 Planning it', 'b')]))"
    )
    proc = _run(["-c", code], env)
    assert proc.returncode == 0, proc.stderr
    worker, lead = json.loads(proc.stdout)
    assert (worker["from"], worker["to"]) == ("worker", "lead")
    assert lead is None

"""Shared lab, server, and hook setup for the Heliopause journey tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from atlas.job_report import job_prompt
from atlas.roles import new_message, send

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "journeys"
SUMMARY = "Wire the core loop"
JOB_FIELDS = ("id", "from", "to", "kind", "summary", "repo", "state", "reply_to", "ts", "checks")
_PORT = re.compile(r"http://127\.0\.0\.1:(\d+)/api/state")


def child_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for key in ("ATLAS_LAB", "ATLAS_URL", "ATLAS_TOKEN_FILE"):
        env.pop(key, None)
    for key in list(env):
        if key.startswith("GROK_"):
            del env[key]
    return env


def init_lab(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    home = tmp_path / "home"
    home.mkdir()
    lab = tmp_path / "lab"
    env = child_env(home)
    subprocess.run(
        [sys.executable, "-m", "atlas", "init", "--yes", "--dir", str(lab)],
        cwd=ROOT, env=env, check=True, capture_output=True, timeout=15,
    )
    return home, lab, env


def atlas(env: dict[str, str], *args: str, timeout: float = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "atlas", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )


def read_json(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=2) as res:
            return json.load(res)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        raise AssertionError(f"{err.code} {detail}") from err


def get(port: int, path: str) -> dict:
    return read_json(urllib.request.Request(f"http://127.0.0.1:{port}{path}"))


def wait_until_up(log_path: Path, deadline: float) -> int:
    """Poll the server's own listen line, then /api/state. Sleep only inside the deadline."""
    port = None
    while time.monotonic() < deadline:
        text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        match = _PORT.search(text)
        if match:
            port = int(match.group(1))
            break
        time.sleep(0.05)
    assert port is not None, log_path.read_text(encoding="utf-8") if log_path.exists() else "no server log"
    while time.monotonic() < deadline:
        try:
            get(port, "/api/state")
            return port
        except (OSError, AssertionError):
            time.sleep(0.05)
    raise AssertionError(log_path.read_text(encoding="utf-8"))


def wait_until_down(port: int, deadline: float) -> None:
    """Poll until 127.0.0.1:`port` refuses a connection, or the deadline passes."""
    while time.monotonic() < deadline:
        sock = socket.socket()
        sock.settimeout(0.2)
        try:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return
        finally:
            sock.close()
        time.sleep(0.05)
    raise AssertionError(f"127.0.0.1:{port} still accepts")


def seat(port: int) -> None:
    body = json.dumps({"roles": {"hq": "you", "lab": "claude-code", "factory": ["cursor"]}}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/roles",
        data=body,
        method="POST",
        headers={
            "Origin": f"http://127.0.0.1:{port}",
            "X-Atlas-UI": get(port, "/api/state")["nonce"],
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        },
    )
    saved = read_json(req)
    assert saved["roles"]["lab"] == "claude-code"
    assert saved["roles"]["factory"] == ["cursor"]


def install(base: Path, lab: Path) -> dict[str, Path]:
    """Copy the shipped hook scripts into tmp. Modes match what the scripts themselves require."""
    hooks = base / "hooks"
    conn = base / "connectors"
    hooks.mkdir(parents=True)
    (conn / "cursor").mkdir(parents=True)
    shutil.copy(ROOT / "plugin" / "heliopause" / "hooks" / "session-start.sh", hooks / "session-start.sh")
    shutil.copy(ROOT / "plugin" / "heliopause" / "hooks" / "report.sh", hooks / "report.sh")
    shutil.copy(ROOT / "connectors" / "report-common.sh", conn / "report-common.sh")
    shutil.copy(ROOT / "connectors" / "cursor" / "report.sh", conn / "cursor" / "report.sh")
    for dirpath, dirnames, _names in os.walk(base):
        os.chmod(dirpath, 0o700)
        dirnames[:] = [name for name in dirnames if not os.path.islink(os.path.join(dirpath, name))]
    scripts = {
        "session-start": hooks / "session-start.sh",
        "claude-report": hooks / "report.sh",
        "cursor-report": conn / "cursor" / "report.sh",
    }
    for script in (*scripts.values(), conn / "report-common.sh"):
        os.chmod(script, 0o755)
    runtime = f"{os.path.abspath(sys.executable)}\n{ROOT}\n{lab}\n"
    for path in (hooks / "runtime", conn / "runtime"):
        path.write_text(runtime, encoding="utf-8")
        os.chmod(path, 0o600)
    return scripts


def hook(script: Path, name: str, env: dict[str, str]) -> None:
    proc = subprocess.run([str(script)], input=(FIX / name).read_bytes(), capture_output=True, env=env, timeout=8)
    assert proc.returncode == 0, (script.name, proc.returncode, proc.stderr)


def card(lab: Path, tool: str, session: str) -> dict:
    return json.loads((lab / ".heliopause" / "cards" / tool / f"{session}.json").read_text(encoding="utf-8"))


def one(items: list[dict], job_id: str) -> dict:
    found = [item for item in items if item.get("id") == job_id]
    assert len(found) == 1, items
    return found[0]


def expect_job(item: dict, state: str, tests: str) -> None:
    assert item["state"] == state
    assert item["checks"]["tests"] == tests


def today_rows(port: int) -> list[dict]:
    body = get(port, "/api/today")
    return [row for block in body["repos"] for row in block["rows"]]


def file_job(lab: Path, summary: str = SUMMARY) -> dict:
    # No shipped command writes a kind=job message. The queue is files, so this works with the server stopped.
    job = new_message("lab", "claude-code", "factory", "job", summary, repo="journey")
    send(lab, job)
    return job


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@contextmanager
def serving(env: dict[str, str], lab: Path, log_path: Path):
    """Start `atlas up` on 127.0.0.1 and stop it when the block ends."""
    # The child keeps its own copy of the fd, so the parent's handle can close right after the spawn.
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "atlas", "up", "--lab", str(lab), "--port", "0"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        yield wait_until_up(log_path, time.monotonic() + 8)
    finally:
        stop(proc)


@dataclass
class Loop:
    home: Path
    lab: Path
    env: dict[str, str]
    port: int
    scripts: dict[str, Path]


@contextmanager
def loop(tmp_path: Path):
    """Init a temporary lab, serve it, seat the roles, and install the hook scripts."""
    _home, lab, env = init_lab(tmp_path)
    with serving(env, lab, tmp_path / "server.log") as port:
        seat(port)
        yield Loop(_home, lab, env, port, install(tmp_path / "install", lab))


def lead_and_builder(box: Loop) -> dict:
    """J7 steps through the Builder's sessionEnd. Returns the job the Lead filed."""
    hook(box.scripts["session-start"], "claude-session-start.json", box.env)
    assert not (box.lab / ".heliopause" / "cards" / "claude-code").exists()
    hook(box.scripts["claude-report"], "claude-user-prompt-submit.json", box.env)
    lead = get(box.port, "/api/now")["seats"]["lab"]
    assert len(lead) == 1
    assert lead[0]["tool"] == "claude-code"
    assert lead[0]["state"] == "working"
    assert lead[0]["repo"] == "journey"
    assert card(box.lab, "claude-code", "lead-1")["event"] == "UserPromptSubmit"

    job = file_job(box.lab)
    stored = one(get(box.port, "/api/messages")["messages"], job["id"])
    text = job_prompt({key: stored[key] for key in JOB_FIELDS})
    assert job["id"] in text
    assert f"atlas report {job['id']}" in text

    for name in ("cursor-session-start.json", "cursor-pre-tool-use.json", "cursor-post-tool-use.json"):
        hook(box.scripts["cursor-report"], name, box.env)
    assert card(box.lab, "cursor", "builder-1")["event"] == "postToolUse"
    builder = get(box.port, "/api/now")["seats"]["factory"]
    assert len(builder) == 1
    assert builder[0]["name"] == "Builder"
    assert builder[0]["tool"] == "cursor"
    assert builder[0]["state"] == "working"
    assert builder[0]["repo"] == "journey"
    hook(box.scripts["cursor-report"], "cursor-session-end.json", box.env)
    ended = get(box.port, "/api/now")["seats"]["factory"][0]
    assert ended["state"] == "idle" and ended["repo"] == "journey"
    assert card(box.lab, "cursor", "builder-1")["event"] == "sessionEnd"
    return job


def run_report(env: dict[str, str], lab: Path, job_id: str, outcome: str, tests: str) -> subprocess.CompletedProcess[str]:
    proc = atlas(env, "report", job_id, outcome, "--tests", tests, "--lab", str(lab))
    assert proc.returncode == 0, proc.stderr
    return proc

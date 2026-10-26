"""Connector cards keep five fields. Prompts, commands and emails never land in the lab."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from atlas.connectors import TOOLS, report

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "connectors"
SENTINEL = b"SENTINEL-PROMPT-7f3a"
KEYS = {"tool", "event", "state", "session", "repo", "ts"}
EXPECT = {
    ("claude-code", "PermissionRequest"): "needs_you",
    ("claude-code", "UserPromptSubmit"): "working",
    ("codex", "PermissionRequest"): "needs_you",
    ("cursor", "beforeSubmitPrompt"): "working",
    ("grok", "PreToolUse"): "working",
}


def _tool(name):
    return next(tool for tool in TOOLS if name.startswith(tool + "-"))


def _cli(lab, tool, raw):
    return subprocess.run(
        [sys.executable, "-m", "atlas", "connector", "report", "--tool", tool, "--lab", str(lab)],
        cwd=ROOT, input=raw, capture_output=True, timeout=30,
    )


def _silent(proc):
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b"" and proc.stderr == b""


def _files(lab):
    found = []
    for dirpath, dirnames, names in os.walk(lab):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in names:
            path = os.path.join(dirpath, name)
            if not os.path.islink(path):
                found.append(path)
    return found


def test_fixtures_write_the_mapped_state_and_drop_the_sentinel(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    now = datetime(2026, 10, 21, 15, 4, 5, tzinfo=timezone.utc)
    for path in sorted(FIX.glob("*.json")):
        tool = _tool(path.name)
        event = path.name[len(tool) + 1:-len(".json")]
        raw = path.read_bytes()
        assert SENTINEL in raw
        assert report(lab, tool, raw, now) is True
        card = json.loads((lab / ".heliopause" / "cards" / tool / f"{event}.json").read_text())
        assert card["state"] == EXPECT[(tool, event)]
        assert card["session"] == event and card["repo"] == tool
        assert card["ts"] == "2026-10-21T15:04:05Z"
    for path in _files(lab):
        blob = Path(path).read_bytes()
        assert SENTINEL not in blob and SENTINEL not in os.fsencode(path)
        body = json.loads(blob)
        assert set(body) == KEYS
        assert Path(path).stat().st_mode & 0o777 == 0o600


def test_cli_writes_silently(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    raw = (FIX / "claude-code-PermissionRequest.json").read_bytes()
    _silent(_cli(lab, "claude-code", raw))
    card = lab / ".heliopause" / "cards" / "claude-code" / "PermissionRequest.json"
    assert json.loads(card.read_text())["state"] == "needs_you"


def test_j5_rejects_and_cli_stays_silent(tmp_path):
    stop = {"hook_event_name": "Stop", "session_id": "ok", "cwd": "/src/repo"}
    huge = json.dumps({**stop, "prompt": "SENTINEL-PROMPT-7f3a" + ("p" * (1 << 20))}).encode()
    assert len(huge) > 1 << 20
    cases = [
        ("claude-code", b"{"),
        ("claude-code", b"[]"),
        ("claude-code", huge),
        ("no-such", json.dumps(stop).encode()),
        ("claude-code", json.dumps({**stop, "hook_event_name": "Notification"}).encode()),
        ("claude-code", json.dumps({**stop, "session_id": "a/b"}).encode()),
        ("claude-code", json.dumps({**stop, "session_id": ".."}).encode()),
        ("claude-code", json.dumps({"hook_event_name": "Stop", "session_id": "ok"}).encode()),
    ]
    for i, (tool, raw) in enumerate(cases):
        lab = tmp_path / f"lab{i}"
        lab.mkdir()
        assert report(lab, tool, raw) is False, (tool, raw[:48])
        assert not (lab / ".heliopause").exists()
        cli = tmp_path / f"cli{i}"
        cli.mkdir()
        _silent(_cli(cli, tool, raw))
        assert not (cli / ".heliopause").exists()


def test_second_event_replaces_the_card(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()

    def raw(event):
        body = {"hook_event_name": event, "session_id": "same", "cwd": "/src/repo"}
        return json.dumps(body).encode()

    assert report(lab, "claude-code", raw("PermissionRequest")) is True
    card = lab / ".heliopause" / "cards" / "claude-code" / "same.json"
    assert json.loads(card.read_text())["state"] == "needs_you"
    assert report(lab, "claude-code", raw("PostToolUse")) is True
    body = json.loads(card.read_text())
    assert body["event"] == "PostToolUse" and body["state"] == "working"
    assert list(card.parent.glob("*.json")) == [card]


def test_symlinked_heliopause_is_not_followed(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (lab / ".heliopause").symlink_to(outside)
    raw = (FIX / "grok-PreToolUse.json").read_bytes()
    assert report(lab, "grok", raw) is False
    assert list(outside.rglob("*")) == []
    assert not any(os.path.isfile(p) for p in _files(lab))

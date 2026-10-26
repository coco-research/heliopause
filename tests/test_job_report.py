"""Job results. One report call is the only way a job's state and tests check change."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from atlas.job_report import job_prompt, report
from atlas.roles import new_message, send, set_check

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def _plant(root: Path, **kwargs: object) -> dict:
    msg = new_message("lab", "board", "factory", "job", "Fix the gate", **kwargs)
    send(root, msg)
    return msg


def _load(root: Path, role: str, msg_id: str) -> dict:
    path = root / ".heliopause" / "inbox" / role / f"{msg_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _inbox(root: Path, role: str) -> list[dict]:
    folder = root / ".heliopause" / "inbox" / role
    if not folder.is_dir():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(folder.glob("*.json"))
    ]


def _snapshot(root: Path) -> list[tuple[str, bytes]]:
    return [
        (str(path.relative_to(root)), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def test_job_prompt_is_three_lines() -> None:
    job = new_message("lab", "board", "factory", "job", "Fix the gate", repo="atlas")
    assert job_prompt(job) == (
        f"Job {job['id']}: Fix the gate\n"
        "Repo: atlas\n"
        f"When done, run: cd ~/Heliopause && python3 -B -m atlas report {job['id']} done|failed --tests pass|fail --lab ~/atlas-lab"
    )
    bare = new_message("lab", "board", "factory", "job", "Quiet")
    assert job_prompt(bare) == (
        f"Job {bare['id']}: Quiet\n"
        "Repo: none\n"
        f"When done, run: cd ~/Heliopause && python3 -B -m atlas report {bare['id']} done|failed --tests pass|fail --lab ~/atlas-lab"
    )
    ask = new_message("lab", "board", "factory", "ask", "not a job")
    with pytest.raises(ValueError):
        job_prompt(ask)
    with pytest.raises(ValueError):
        job_prompt({"kind": "job"})


def test_report_done_records_tests_and_replies(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    job = _plant(lab, repo="atlas")
    code, body = report(lab, job["id"], "done", tests="pass")
    assert code == 200
    assert body["id"] == job["id"] and body["state"] == "done"
    saved = _load(lab, "factory", job["id"])
    assert saved["state"] == "done"
    assert saved["checks"]["tests"] == "pass"
    notes = _inbox(lab, "lab")
    assert len(notes) == 1
    note = notes[0]
    assert note["id"] == body["report"]
    assert note["kind"] == "report"
    assert note["reply_to"] == job["id"]
    assert note["summary"] == f"{job['id']} done, tests pass"
    assert note["repo"] == "atlas"
    assert note["from"] == {"role": "factory", "tool": "you"}
    assert note["to"] == {"role": "lab"}


def test_report_failed_without_tests_leaves_the_check(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    job = _plant(lab)
    set_check(lab, "factory", job["id"], "tests", "pending")
    code, body = report(lab, job["id"], "failed")
    assert code == 200 and body["state"] == "failed"
    saved = _load(lab, "factory", job["id"])
    assert saved["state"] == "failed"
    assert saved["checks"]["tests"] == "pending"
    note = _inbox(lab, "lab")[0]
    assert note["kind"] == "report"
    assert note["reply_to"] == job["id"]
    assert note["summary"] == f"{job['id']} failed"


def test_unknown_id_is_404(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    _plant(lab)
    code, body = report(lab, "m-" + "ab" * 6, "done")
    assert (code, body) == (404, {"error": "No job with that id."})


def test_non_job_is_409(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    job = _plant(lab)
    ask = new_message("hq", "you", "lab", "ask", "question")
    send(lab, ask)
    before = _snapshot(lab)
    code, body = report(lab, ask["id"], "done", tests="pass")
    assert (code, body) == (409, {"error": "That message is not a job."})
    assert _snapshot(lab) == before
    assert _load(lab, "factory", job["id"])["state"] == "sent"


def test_second_report_is_409_and_changes_nothing(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    job = _plant(lab)
    assert report(lab, job["id"], "done", tests="pass")[0] == 200
    before = _snapshot(lab)
    code, body = report(lab, job["id"], "failed", tests="fail")
    assert (code, body) == (409, {"error": "This job already has a result."})
    assert _snapshot(lab) == before
    saved = _load(lab, "factory", job["id"])
    assert saved["state"] == "done" and saved["checks"]["tests"] == "pass"


def test_bad_input_is_400_and_byte_identical(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    job = _plant(lab)
    before = _snapshot(lab)
    cases = (
        ("nope", "done", None),
        ("m-" + "A" * 12, "done", None),
        ("m-" + "a" * 11, "done", None),
        (job["id"], "nope", None),
        (job["id"], "DONE", None),
        (job["id"], "done", "maybe"),
        (job["id"], "done", "pending"),
        (job["id"], "done", ""),
        (12, "done", None),
    )
    for ident, outcome, tests in cases:
        code, body = report(lab, ident, outcome, tests=tests)
        assert code == 400 and body.get("error"), (ident, outcome, tests, body)
    assert _snapshot(lab) == before


def test_report_rejects_unhashable_values(tmp_path: Path) -> None:
    cases = (
        report(tmp_path, "m-0123456789ab", [], None),
        report(tmp_path, "m-0123456789ab", "done", {}),
        report(tmp_path, "m-0123456789ab", "done", []),
        report(tmp_path, {}, "done"),
    )
    for code, body in cases:
        assert code == 400, body
    assert list(tmp_path.iterdir()) == []


def test_cli_report(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    job = _plant(lab)
    proc = subprocess.run(
        [PY, "-m", "atlas", "report", job["id"], "done", "--lab", str(lab)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"{job['id']} done\n"
    assert _load(lab, "factory", job["id"])["state"] == "done"

    env = os.environ.copy()
    env.pop("ATLAS_LAB", None)
    missing = subprocess.run(
        [PY, "-m", "atlas", "report", job["id"], "done"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert missing.returncode == 2
    assert missing.stderr == "atlas report: no lab (use --lab)\n"

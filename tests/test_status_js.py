"""status.js: the status line counts the same rows the lists render."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "atlas" / "static" / "status.js"
FIX = ROOT / "tests" / "fixtures" / "status"

_HARNESS = r"""
const fs = require("fs");
const path = require("path");
const status = require(process.argv[1]);
const dir = process.argv[2];
const out = {};
for (const name of fs.readdirSync(dir).filter((n) => n.endsWith(".json")).sort()) {
  const data = JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
  const now = typeof data.nowMs === "number" ? data.nowMs : 0;
  out[name] = {
    needsYou: status.needsYou(data.state, data.problems, data.alerts, now),
    fyi: status.fyiGroups(data.problems, data.alerts),
    line: status.statusLine(data.state, data.problems, data.alerts, now)
  };
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def ran():
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    proc = subprocess.run(
        ["node", "-e", _HARNESS, str(JS), str(FIX)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def _needs_said(head, n):
    if n == 0:
        assert head.startswith("Nothing needs you")
    elif n == 1:
        assert head.startswith("1 thing needs you")
    else:
        assert head.startswith(f"{n} things need you")


def _counts_said(head):
    if head == "Nothing needs you, and nothing is broken.":
        return 0, 0
    needs = 0 if head.startswith("Nothing needs you") else int(head.split(" ", 1)[0])
    if "nothing is broken" in head.lower():
        return needs, 0
    broken = int(head.split(". ")[-1].split(" ", 1)[0]) if "broken" in head else 0
    return needs, broken


def test_source_is_pure():
    src = JS.read_text()
    assert "document" not in src and "fetch(" not in src
    assert "window.AtlasStatus" in src and "module.exports" in src
    assert src.count("\n") < 250


def test_counts_match_needs_you(ran):
    for name, got in ran.items():
        head = got["line"]["head"]
        sub = got["line"]["sub"]
        blob = json.dumps(got)
        assert "NaN" not in blob and "undefined" not in blob, name
        assert "NaN" not in head and "undefined" not in head, name
        assert "NaN" not in sub and "undefined" not in sub, name
        _needs_said(head, len(got["needsYou"]))
        needs, broken = _counts_said(head)
        assert got["line"]["needs"] == needs == len(got["needsYou"])
        assert got["line"]["broken"] == broken


def test_red_ci_is_broken(ran):
    got = ran["red-ci.json"]
    head = got["line"]["head"]
    assert "nothing is broken" not in head.lower()
    assert head == "Nothing needs you. 26 things are broken."
    assert got["line"]["needs"] == 0 and got["line"]["broken"] == 26
    assert len(got["needsYou"]) == 0
    assert got["fyi"] == [{"label": "26 red CI on mobile-app", "count": 26, "worst": "high"}]
    assert got["line"]["sub"] == "1 agent working \u00b7 1 idle \u00b7 12 messages in the last 24 h"


def test_singular_and_plural(ran):
    assert ran["one-needs.json"]["line"]["head"] == "1 thing needs you."
    assert ran["one-broken.json"]["line"]["head"] == "Nothing needs you. 1 thing is broken."
    assert "nothing is broken" not in ran["one-broken.json"]["line"]["head"].lower()
    assert ran["needs-and-broken.json"]["line"]["head"] == "3 things need you. 2 things are broken."
    assert ran["calm.json"]["line"]["head"] == "Nothing needs you, and nothing is broken."
    assert ran["agents.json"]["line"]["sub"] == "1 agent working \u00b7 2 idle \u00b7 1 message in the last 24 h"
    assert ran["agents-many.json"]["line"]["sub"] == "2 agents working \u00b7 0 idle \u00b7 41 messages in the last 24 h"
    assert ran["messages-only.json"]["line"]["sub"] == "3 messages in the last 24 h"
    assert ran["messages-only.json"]["line"]["head"] == "Nothing needs you, and nothing is broken."


def test_oldest_first(ran):
    titles = [row["title"] for row in ran["oldest.json"]["needsYou"]]
    assert titles == ["oldest", "middle", "late", "newest", "untimed"]
    assert ran["oldest.json"]["needsYou"][-1]["ts"] is None


def test_no_duplicate_when_alert_and_problem_match(ran):
    rows = ran["dup.json"]["needsYou"]
    assert len(rows) == 2
    assert [row["kind"] for row in rows] == ["alert", "decision"]
    assert rows[0]["id"] == "abc"
    assert rows[0]["from"] == "queue"
    assert rows[0]["title"] == "Please approve the deploy"
    assert rows[1]["id"] == "vp"
    assert rows[1]["title"] == "VP waits"
    assert ran["dup.json"]["fyi"] == []


def test_missing_fields(ran):
    got = ran["missing.json"]
    assert got["line"]["head"] == "1 thing needs you. 1 thing is broken."
    assert got["line"]["sub"] == "0 agents working \u00b7 0 idle"
    assert got["needsYou"][0]["title"] == "An agent is waiting"
    assert "nothing is broken" not in got["line"]["head"].lower()


def test_failed_jobs_count_once(ran):
    assert ran["failed.json"]["line"]["head"] == "Nothing needs you. 2 things are broken."
    assert "nothing is broken" not in ran["failed-row.json"]["line"]["head"].lower()
    assert ran["failed-row.json"]["line"]["head"] == "Nothing needs you. 1 thing is broken."
    assert ran["failed.json"]["fyi"] == [{"label": "1 failed job on factory-queue", "count": 1, "worst": "high"}]


def test_fyi_worst_first(ran):
    assert ran["fyi.json"]["fyi"] == [
        {"label": "6 red CI on mobile-app", "count": 6, "worst": "high"},
        {"label": "1 red CI on web", "count": 1, "worst": "high"},
        {"label": "1 warning on lab", "count": 1, "worst": "warn"},
        {"label": "2 stale PRs on mobile-app", "count": 2, "worst": "low"},
    ]
    assert len(ran["fyi.json"]["needsYou"]) == 1
    assert ran["fyi.json"]["needsYou"][0]["kind"] == "alert"


def test_failing_pipe_counts_once(ran):
    # One pipe-failing row plus two of its own job-failed rows is one break; a lone job-failed row is another.
    got = ran["pipe-failing.json"]["line"]
    assert got["broken"] == 2
    assert got["head"] == "Nothing needs you. 2 things are broken."

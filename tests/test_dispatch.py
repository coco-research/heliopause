"""atlas.dispatch picks a lane, checks gates, and appends one routes.jsonl line.

The workers are fake scripts under tmp_path. Nothing here calls the real coco-route or ocr-stamp.
"""

import json
import os
import stat
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from atlas.dispatch import main
from atlas.jobs import jobs

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "routes.example.json"
NEEDS_BRAIN = "needs Claude Code: a brain decides whether a Sonnet subagent is warranted"
_GATES = ("grok-build", "cursor", "gemini", "deepseek", "glm", "nvidia", "ling")
FAKE = r"""
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path

log = Path(os.environ["ATLAS_FAKE_LOG"])
root = Path(os.environ["ATLAS_FAKE_ROOT"])
spec = json.loads(Path(os.environ["ATLAS_FAKE_SPEC"]).read_text(encoding="utf-8"))
calls = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
calls.append({
    "argv": sys.argv[1:],
    "job": os.environ.get("COCO_JOB_ID"),
    "story": os.environ.get("COCO_STORY"),
    "bin": os.environ.get("ATLAS_FAKE_BIN", ""),
})
log.write_text(json.dumps(calls), encoding="utf-8")
item = spec[len(calls) - 1] if spec else {}
if not isinstance(item, dict):
    item = {}
code = item.get("exit", 0)
if isinstance(code, bool) or not isinstance(code, int):
    code = 0
cls = item.get("class")
if isinstance(cls, str) and cls:
    end = root / ".heliopause" / "inbox" / "lab" / "usage" / "jobs-end.jsonl"
    end.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "job": os.environ.get("COCO_JOB_ID"),
        "lane": "fake",
        "exit": code,
        "minutes": 0.1,
        "class": cls,
    }
    with end.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")
sys.exit(code)
"""


class Box:
    def __init__(self, root: Path):
        self.root = root
        self.cwd = root / "work"
        self.cwd.mkdir()
        self.usage = root / ".heliopause" / "inbox" / "lab" / "usage"
        self.log = root / "calls.json"
        self.spec_path = root / "spec.json"
        self.spec_path.write_text("[]", encoding="utf-8")

    def ready(self, lanes=None, xultra=None, caps=None, grok_tokens=1000, usage=None):
        self.usage.mkdir(parents=True, exist_ok=True)
        table = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        (self.usage / "routes.json").write_text(json.dumps(table), encoding="utf-8")
        live = {name: {"state": "live"} for name in _GATES}
        if lanes:
            live.update(lanes)
        (self.usage / "lanes.json").write_text(json.dumps({"lanes": live}), encoding="utf-8")
        ultra = {"factoryStop": False, "weeklyPct": 10, "factoryStopPct": 70}
        if xultra:
            ultra.update(xultra)
        (self.usage / "xultra.json").write_text(json.dumps(ultra), encoding="utf-8")
        cap = {"grok_build_daily_tokens": 5000000}
        if caps:
            cap.update(caps)
        (self.usage / "caps.json").write_text(json.dumps(cap), encoding="utf-8")
        journal = {
            "ts": "2026-10-13T15:00:00-04:00",
            "turns": 1,
            "usage": {"total_tokens": grok_tokens},
        }
        (self.usage / "grok-build.jsonl").write_text(json.dumps(journal) + "\n", encoding="utf-8")
        pools = usage or {"pools": [{"name": "GLM week", "status": "ok", "pace": "behind 1 day"}]}
        strip = self.root / ".heliopause" / "inbox" / "lab" / "strip"
        strip.mkdir(parents=True, exist_ok=True)
        (strip / "usage.json").write_text(json.dumps(pools), encoding="utf-8")

    def reset(self):
        self.log.unlink(missing_ok=True)
        for name in ("routes.jsonl", "jobs-end.jsonl"):
            (self.usage / name).unlink(missing_ok=True)

    def invoke(self, kind, task="do the work", spec=None, story=None):
        payload = spec if spec is not None else [{"class": "ok", "exit": 0}]
        self.spec_path.write_text(json.dumps(payload), encoding="utf-8")
        argv = [kind, "--root", str(self.root), "--cwd", str(self.cwd)]
        if story is not None:
            argv.extend(["--story", story])
        argv.extend(["--", task])
        return main(argv)

    def calls(self):
        if not self.log.exists():
            return []
        return json.loads(self.log.read_text(encoding="utf-8"))

    def routes(self):
        path = self.usage / "routes.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setattr("atlas.collect._today", lambda: date(2026, 10, 13))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("coco-route", "ocr-stamp"):
        shim = bin_dir / name
        shim.write_text(
            "#!/bin/sh\n"
            'export ATLAS_FAKE_BIN="$0"\n'
            f'exec "{sys.executable}" "{fake}" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ATLAS_DISPATCH_BIN_DIR", str(bin_dir))
    monkeypatch.setenv("ATLAS_FAKE_LOG", str(tmp_path / "calls.json"))
    monkeypatch.setenv("ATLAS_FAKE_ROOT", str(tmp_path))
    monkeypatch.setenv("ATLAS_FAKE_SPEC", str(tmp_path / "spec.json"))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return Box(tmp_path)


def _git(cwd: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=cwd, check=True, capture_output=True)


def _dirty(cwd: Path) -> None:
    _git(cwd)
    (cwd / "leftover.txt").write_text("wip\n", encoding="utf-8")


def _runs(types, kind):
    return [
        (lane["run"], lane["gate"], lane.get("xai", False), lane.get("pace"))
        for lane in types[kind]["lanes"]
    ]


def test_example_table_is_the_signed_order():
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert data["v"] == 1
    assert data["glm_pace_max_days_ahead"] == 1.5
    types = data["types"]
    assert list(types) == ["build", "review", "security", "stamp", "read", "text", "image"]
    assert "shell" not in types and "lookup" not in types
    assert types["build"]["mode"] == "write"
    assert _runs(types, "build") == [
        (["coco-route", "grok"], "grok-build", True, None),
        (["coco-route", "cursor", "--write", "--model", "composer"], "cursor", False, None),
        (["coco-route", "cursor", "--write", "--model", "grok"], "cursor", True, None),
        (["coco-route", "gemini", "--write"], "gemini", False, None),
    ]
    assert types["review"]["mode"] == "read"
    assert _runs(types, "review") == [
        (["coco-route", "grok", "--read"], "grok-build", True, None),
        (["coco-route", "gemini", "--read"], "gemini", False, None),
    ]
    assert types["security"]["mode"] == "read"
    assert _runs(types, "security") == [
        (["coco-route", "grok", "--read"], "grok-build", True, None),
    ]
    assert types["stamp"]["mode"] == "write"
    assert _runs(types, "stamp") == [
        (["ocr-stamp", "--provider", "hub", "--model", "deepseek"], "deepseek", False, None),
        (["ocr-stamp", "--provider", "hub", "--model", "GLM-5.3-Flash"], "glm", False, "GLM week"),
    ]
    assert types["read"]["mode"] == "read"
    assert _runs(types, "read") == [
        (["coco-route", "gemini", "--read"], "gemini", False, None),
        (["coco-route", "grok", "--read"], "grok-build", True, None),
    ]
    assert types["text"]["mode"] == "read"
    assert _runs(types, "text") == [
        (["coco-route", "nvidia"], "nvidia", False, None),
        (["coco-route", "ling"], "ling", False, None),
        (["coco-route", "gemini", "--read"], "gemini", False, None),
    ]
    assert types["image"]["mode"] == "write"
    assert _runs(types, "image") == [
        (["coco-route", "imagine"], "grok-build", True, None),
    ]


def test_first_live_lane_is_chosen(box):
    box.ready()
    assert box.invoke("build", task="ship the button") == 0
    call = box.calls()[0]
    row = box.routes()[0]
    assert len(box.calls()) == 1 and len(box.routes()) == 1
    assert call["argv"] == ["grok", "--", "ship the button"]
    assert call["bin"].endswith("coco-route")
    assert row["chosen"] == "coco-route grok"
    assert row["fallback_used"] is False
    assert row["tried"] == [{"lane": "coco-route grok", "class": "ok"}]
    assert row["story"] is None


def test_paused_lane_is_skipped_with_its_reason(box):
    box.ready(lanes={"grok-build": {"state": "paused", "note": "holding for budget"}})
    assert box.invoke("build") == 0
    row = box.routes()[0]
    assert row["tried"][0] == {"lane": "coco-route grok", "skip": "paused: holding for budget"}
    assert row["chosen"] == "coco-route cursor --write --model composer"
    assert box.calls()[0]["argv"][:4] == ["cursor", "--write", "--model", "composer"]


def test_xai_stop(box):
    box.ready(xultra={"factoryStop": True, "weeklyPct": 10, "factoryStopPct": 70})
    assert box.invoke("build") == 0
    assert box.routes()[0]["tried"][0]["skip"] == "xai stop"
    assert box.calls()[0]["argv"][0] == "cursor"
    box.reset()
    box.ready(xultra={"factoryStop": False, "weeklyPct": 70, "factoryStopPct": 70})
    assert box.invoke("build") == 0
    assert box.routes()[0]["tried"][0]["skip"] == "xai stop"
    assert box.routes()[0]["chosen"] == "coco-route cursor --write --model composer"


def test_grok_cap(box):
    box.ready(grok_tokens=100, caps={"grok_build_daily_tokens": 100})
    assert box.invoke("build") == 0
    row = box.routes()[0]
    assert row["tried"][0] == {"lane": "coco-route grok", "skip": "grok cap"}
    assert row["chosen"] == "coco-route cursor --write --model composer"
    assert len(box.calls()) == 1


def test_pace_gate(box):
    paused = {"state": "paused", "note": "down"}
    box.ready(
        lanes={"deepseek": paused, "glm": {"state": "live"}},
        usage={"pools": [{"name": "GLM week", "status": "out", "pace": "behind 1 day"}]},
    )
    assert box.invoke("stamp", task="nope") == 3
    assert [row["skip"] for row in box.routes()[0]["tried"]] == ["paused: down", "pace"]
    assert box.calls() == []
    box.reset()
    box.ready(
        lanes={"deepseek": paused, "glm": {"state": "live"}},
        usage={"pools": [{"name": "GLM week", "status": "ok", "pace": "ahead 2 days"}]},
    )
    assert box.invoke("stamp") == 3
    assert box.routes()[0]["tried"][1]["skip"] == "pace"
    assert box.routes()[0]["chosen"] is None
    box.reset()
    box.ready(
        lanes={"deepseek": paused, "glm": {"state": "live"}},
        usage={"pools": [{"name": "GLM week", "status": "ok", "pace": "ahead 1.5 days"}]},
    )
    assert box.invoke("stamp", task="UNIQUE_TASK_DO_NOT_LOG") == 0
    assert box.routes()[0]["chosen"] == "ocr-stamp --provider hub --model GLM-5.3-Flash"
    assert box.calls()[0]["argv"] == ["--provider", "hub", "--model", "GLM-5.3-Flash"]


def test_missing_gate_file_passes_and_is_recorded(box):
    box.ready()
    (box.usage / "lanes.json").unlink()
    assert box.invoke("build", task="still runs") == 0
    row = box.routes()[0]
    assert row["chosen"] == "coco-route grok"
    assert row["tried"][0]["class"] == "ok"
    assert row["tried"][0]["unread"] == ["gate unread: lanes.json"]
    assert len(box.calls()) == 1


def test_all_lanes_gated_exits_3(box, capsys):
    box.ready(lanes={
        "grok-build": {"state": "paused", "note": "grok down"},
        "cursor": {"state": "paused", "note": "cursor down"},
        "gemini": {"state": "paused", "note": "gemini down"},
    })
    assert box.invoke("build", task="UNIQUE_TASK_DO_NOT_LOG") == 3
    row = box.routes()[0]
    assert row["chosen"] is None and row["fallback_used"] is False
    assert [item["skip"] for item in row["tried"]] == [
        "paused: grok down", "paused: cursor down", "paused: cursor down", "paused: gemini down",
    ]
    assert box.calls() == []
    assert "UNIQUE_TASK_DO_NOT_LOG" not in (box.usage / "routes.jsonl").read_text(encoding="utf-8")
    assert capsys.readouterr().err == (
        "paused: grok down\npaused: cursor down\npaused: cursor down\npaused: gemini down\n"
    )
    found = jobs(box.root, datetime.now(timezone.utc))["jobs"]
    assert len(found) == 1 and found[0]["state"] == "no_lane" and found[0]["job"] == row["job"]


def test_fallback_on_capped_for_a_read_type(box):
    box.ready()
    _dirty(box.cwd)
    code = box.invoke("review", spec=[{"class": "capped", "exit": 9}, {"class": "ok", "exit": 0}])
    assert code == 0
    calls = box.calls()
    assert len(calls) == 2 and calls[0]["job"] != calls[1]["job"]
    assert calls[0]["argv"][0] == "grok" and calls[1]["argv"][:2] == ["gemini", "--read"]
    row = box.routes()[0]
    assert len(box.routes()) == 1
    assert row["fallback_used"] is True
    assert row["chosen"] == "coco-route gemini --read"
    assert row["job"] == calls[1]["job"]
    assert [item["class"] for item in row["tried"]] == ["capped", "ok"]


def test_no_fallback_when_write_tree_is_dirty(box):
    box.ready()
    _dirty(box.cwd)
    code = box.invoke("build", spec=[{"class": "worker_failed", "exit": 1}, {"class": "ok", "exit": 0}])
    assert code == 1
    assert len(box.calls()) == 1
    row = box.routes()[0]
    assert row["fallback_used"] is False
    assert row["chosen"] == "coco-route grok"
    assert row["tried"] == [{"lane": "coco-route grok", "class": "worker_failed"}]


def test_clean_write_falls_back_once(box):
    box.ready()
    _git(box.cwd)
    code = box.invoke("build", spec=[
        {"class": "worker_failed", "exit": 4},
        {"class": "worker_failed", "exit": 5},
        {"class": "ok", "exit": 0},
    ])
    assert code == 5
    calls = box.calls()
    assert [call["argv"][0] for call in calls] == ["grok", "cursor"]
    assert calls[1]["argv"][1:4] == ["--write", "--model", "composer"]
    row = box.routes()[0]
    assert row["fallback_used"] is True
    assert row["chosen"] == "coco-route cursor --write --model composer"
    assert [item["class"] for item in row["tried"]] == ["worker_failed", "worker_failed"]


def test_shell_lookup_and_unknown_exit_2(box, capsys):
    box.ready()
    for kind in ("shell", "lookup", "nope"):
        box.reset()
        assert box.invoke(kind, task="do not run") == 2
        assert box.calls() == []
        assert not (box.usage / "routes.jsonl").exists()
    assert capsys.readouterr().err == (NEEDS_BRAIN + "\n") * 3


def test_job_id_is_passed_and_task_stays_out_of_the_journal(box):
    box.ready()
    task = "UNIQUE_TASK_DO_NOT_LOG"
    assert box.invoke("text", task=task, story="story-7") == 0
    call = box.calls()[0]
    row = box.routes()[0]
    assert call["job"] == row["job"] and call["story"] == "story-7"
    assert row["story"] == "story-7" and row["type"] == "text"
    blob = (box.usage / "routes.jsonl").read_text(encoding="utf-8")
    assert task not in blob
    assert call["argv"] == ["nvidia", "--", task]


def test_missing_or_blank_task_exits_2(box):
    box.ready()
    cases = (
        ["build", "--root", str(box.root), "--cwd", str(box.cwd)],
        ["build", "--root", str(box.root), "--cwd", str(box.cwd), "--", "   "],
    )
    for argv in cases:
        box.reset()
        with pytest.raises(SystemExit) as raised:
            main(argv)
        assert raised.value.code == 2
        assert box.calls() == []
        assert not (box.usage / "routes.jsonl").exists()


def test_stamp_without_a_task_still_runs(box):
    box.ready()
    assert main(["stamp", "--root", str(box.root), "--cwd", str(box.cwd)]) == 0
    assert len(box.calls()) == 1
    assert box.calls()[0]["bin"].endswith("ocr-stamp")
    assert len(box.routes()) == 1


def test_stamp_passes_no_double_dash(box):
    box.ready()
    task = "UNIQUE_TASK_DO_NOT_LOG"
    assert box.invoke("stamp", task=task) == 0
    call = box.calls()[0]
    assert call["bin"].endswith("ocr-stamp")
    assert call["argv"] == ["--provider", "hub", "--model", "deepseek"]
    assert task not in (box.usage / "routes.jsonl").read_text(encoding="utf-8")


def test_jobs_view_picks_up_the_route(box):
    box.ready()
    box.invoke("text", spec=[{"exit": 0}])
    now = datetime.now(timezone.utc)
    queued = jobs(box.root, now)["jobs"]
    assert len(queued) == 1
    assert queued[0]["state"] == "queued" and queued[0]["class"] is None
    assert queued[0]["job"] == box.routes()[0]["job"]
    assert queued[0]["lane"] == "coco-route nvidia" and queued[0]["type"] == "text"
    box.reset()
    box.invoke("text", spec=[{"class": "ok", "exit": 0}])
    delivered = jobs(box.root, datetime.now(timezone.utc))["jobs"]
    assert len(delivered) == 1
    assert delivered[0]["state"] == "delivered" and delivered[0]["class"] == "ok"
    assert delivered[0]["job"] == box.routes()[0]["job"]
    assert delivered[0]["lane"] == "coco-route nvidia"

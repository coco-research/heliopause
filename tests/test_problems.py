import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from atlas import problems
from atlas.collect import NODE_IDS, _NY
from atlas.events import _LABELS
from atlas.problems import (
    BUDGET_HIGH,
    BUDGET_WARN,
    DECISION_WAIT_H,
    DISK_FLOOR_GB,
    HQ_WAIT_H,
    PR_STALE_DAYS,
    QUIET_H,
    budget_near_cap,
    budget_over,
    ci_red,
    decision_waiting,
    detect,
    disk_low,
    hq_unanswered,
    job_failed,
    pr_stale,
    quiet,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
GB = 10**9
ROOT = Path("/sandbox-root/home")
OWNER_NAME = "Alex Doe"


def ago(hours=0, minutes=0):
    return (NOW - timedelta(hours=hours, minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_state(nodes=None, decisions=(), failed=(), pi=None):
    base = {
        node_id: {"id": node_id, "status": "idle", "ts": None, "headline": None, "details": None, "name": None}
        for node_id in NODE_IDS
    }
    for node_id, fields in (nodes or {}).items():
        base[node_id].update(fields)
    bus = {"inbox": 0, "running": 0, "done": 0, "failed": len(failed), "active": [], "failed_items": list(failed)}
    bus.update(pi or {})
    return {
        "generated": ago(),
        "nodes": base,
        "decisions": list(decisions),
        "bus": {"queue": bus, "hq": {}, "grok": {}, "lanes": {}},
    }


def make_graph():
    labels = dict(_LABELS, owner=OWNER_NAME)
    return {"nodes": {node_id: {"label": label} for node_id, label in labels.items()}}


GRAPH = make_graph()


@pytest.fixture(autouse=True)
def big_disk(monkeypatch):
    monkeypatch.setattr(problems.shutil, "disk_usage", lambda _root: shutil._ntuple_diskusage(10**12, 0, 500 * GB))


def one(found):
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize(
    "status,owner,severity",
    [("needs-owner", "owner", "high"), ("needs-vp", "vp", "medium"), ("needs-hq", "hq", "medium")],
)
def test_decision_waiting_fires(status, owner, severity):
    state = make_state({"brain-atlas": {"status": status, "ts": ago(hours=5), "headline": "Need a call on scope"}})
    problem = one(decision_waiting(state, GRAPH, NOW))
    assert problem["id"] == "decision-waiting:brain-atlas"
    assert (problem["owner"], problem["severity"], problem["since"]) == (owner, severity, ago(hours=5))
    assert problem["next_action"] == "Answer or resolve it."
    assert problem["evidence"] == "Need a call on scope"


def test_decision_waiting_threshold_edge():
    at_edge = make_state({"vp": {"status": "needs-owner", "ts": ago(hours=DECISION_WAIT_H)}})
    assert decision_waiting(at_edge, GRAPH, NOW) == []
    past = make_state({"vp": {"status": "needs-owner", "ts": ago(hours=DECISION_WAIT_H, minutes=1)}})
    assert one(decision_waiting(past, GRAPH, NOW))["id"] == "decision-waiting:vp"
    working = make_state({"vp": {"status": "working", "ts": ago(hours=9)}})
    assert decision_waiting(working, GRAPH, NOW) == []


def test_job_failed_fires_for_owner_and_falls_back():
    items = [
        {"id": "job-1", "title": "Build it", "model": "grok", "owner": "brain-atlas", "ts": ago(hours=3)},
        {"id": "job-2", "title": None, "model": None, "owner": "vp", "ts": ago(hours=1)},
    ]
    found = job_failed(make_state(failed=items), GRAPH, NOW)
    assert [(p["id"], p["owner"], p["severity"]) for p in found] == [
        ("job-failed:job-1", "brain-atlas", "high"),
        ("job-failed:job-2", "vp", "high"),
    ]
    assert [p["since"] for p in found] == [ago(hours=3), ago(hours=1)]
    assert [p["headline"] for p in found] == ["1 factory job failed", "1 factory job failed"]
    assert found[0]["next_action"] == "Re-run or withdraw the job."
    assert job_failed(make_state(), GRAPH, NOW) == []


def _ask(ask_id, hours, **extra):
    return {"kind": "hq", "id": ask_id, "ts": ago(hours=hours), "title": "Pick a launch date", **extra}


def test_hq_unanswered_threshold_edge():
    state = make_state(decisions=[_ask("ask-1", HQ_WAIT_H), _ask("ask-2", HQ_WAIT_H, id=None)])
    assert hq_unanswered(state, GRAPH, NOW) == []
    late = make_state(decisions=[{**_ask("ask-3", HQ_WAIT_H), "ts": ago(hours=HQ_WAIT_H, minutes=1)}])
    problem = one(hq_unanswered(late, GRAPH, NOW))
    assert (problem["id"], problem["owner"], problem["severity"]) == ("hq-unanswered:ask-3", "vp", "medium")
    assert problem["evidence"] == "Pick a launch date"


def test_hq_unanswered_ignores_board_rows():
    board = {"kind": "board", "node": "vp", "status": "needs-hq", "ts": ago(hours=99), "id": None}
    assert hq_unanswered(make_state(decisions=[board]), GRAPH, NOW) == []


def test_quiet_threshold_edge():
    state = make_state({
        "brain-atlas": {"status": "working", "ts": ago(hours=QUIET_H)},
        "brain-voice": {"status": "blocked", "ts": ago(hours=QUIET_H, minutes=-1)},
        "brain-si": {"status": "done", "ts": ago(hours=99)},
    })
    problem = one(quiet(state, GRAPH, NOW))
    assert (problem["id"], problem["owner"], problem["severity"]) == ("quiet:brain-atlas", "brain-atlas", "low")
    assert problem["next_action"] == "Post your state or hand the work off."
    unknown = make_state({"brain-si": {"status": "unknown", "ts": ago(hours=QUIET_H)}})
    assert one(quiet(unknown, GRAPH, NOW))["id"] == "quiet:brain-si"


def test_quiet_skips_a_busy_pi_queue():
    stale = {"factory-queue": {"status": "working", "ts": ago(hours=30)}}
    assert quiet(make_state(stale, pi={"inbox": 1}), GRAPH, NOW) == []
    assert one(quiet(make_state(stale), GRAPH, NOW))["owner"] == "factory-queue"


def test_quiet_skips_paused_at_cap_or_busy_lane():
    # zcode with a 3-day-old "working" board line and paused → no quiet problem
    stale = {"factory-zcode": {"status": "working", "ts": ago(hours=72)}}
    state = make_state(stale)
    state["bus"]["lanes"] = {
        "factory-zcode": {"paused": "CLI billed coding plan", "at_cap": False, "busy": 0}
    }
    assert quiet(state, GRAPH, NOW) == []

    # unpaused and not at cap or busy → quiet problem fires
    state["bus"]["lanes"]["factory-zcode"]["paused"] = None
    assert one(quiet(state, GRAPH, NOW))["owner"] == "factory-zcode"

    # at_cap → no quiet problem
    state["bus"]["lanes"]["factory-zcode"]["at_cap"] = True
    assert quiet(state, GRAPH, NOW) == []

    # busy > 0 → no quiet problem
    state["bus"]["lanes"]["factory-zcode"]["at_cap"] = False
    state["bus"]["lanes"]["factory-zcode"]["busy"] = 1
    assert quiet(state, GRAPH, NOW) == []


def test_disk_low_threshold_edge():
    assert disk_low(DISK_FLOOR_GB * GB, NOW) == []
    medium = one(disk_low(DISK_FLOOR_GB * GB - 1, NOW))
    assert (medium["id"], medium["owner"], medium["severity"]) == ("disk-low:home", "dept-engineering", "medium")
    assert one(disk_low(10 * GB - 1, NOW))["severity"] == "high"
    assert disk_low(10 * GB, NOW)[0]["severity"] == "medium"


def grok_state(used, cap=190_000_000):
    state = make_state()
    state["bus"]["grok"] = {"tokens_today": used, "cap": cap}
    return state


def test_budget_near_cap_thresholds():
    cap = 190_000_000
    assert budget_near_cap(grok_state(int(cap * BUDGET_WARN) - 1), GRAPH, NOW) == []
    warn = one(budget_near_cap(grok_state(int(cap * BUDGET_WARN)), GRAPH, NOW))
    assert (warn["id"], warn["severity"], warn["owner"]) == ("budget-near-cap:grok-build", "medium", "dept-budget")
    assert warn["headline"] == "Grok Build used 80% of today's cap"
    assert warn["evidence"] == "152M of 190M tokens"
    assert warn["next_action"] == "Move new jobs to the queue factory or wait for the reset."
    # NOW is 08:00 ET on 2026-10-02, so since is that day's ET midnight.
    assert warn["since"] == "2026-10-02T04:00:00Z"
    assert one(budget_near_cap(grok_state(int(cap * BUDGET_HIGH) - 1), GRAPH, NOW))["severity"] == "medium"
    assert one(budget_near_cap(grok_state(int(cap * BUDGET_HIGH)), GRAPH, NOW))["severity"] == "high"


@pytest.mark.parametrize("cap", [None, 0])
def test_budget_near_cap_off_without_a_cap(cap):
    assert budget_near_cap(grok_state(10**12, cap), GRAPH, NOW) == []
    state = make_state()
    assert budget_near_cap(state, GRAPH, NOW) == []  # bus.grok is {} before any run
    assert "budget-near-cap:grok-build" in [p["id"] for p in detect(grok_state(10**9), GRAPH, ROOT, NOW)]


def test_detect_reads_the_disk(monkeypatch):
    monkeypatch.setattr(problems.shutil, "disk_usage", lambda _root: shutil._ntuple_diskusage(10**12, 0, 5 * GB))
    assert [p["id"] for p in detect(make_state(), GRAPH, ROOT, NOW)] == ["disk-low:home"]

    def missing(_root):
        raise OSError("gone")

    monkeypatch.setattr(problems.shutil, "disk_usage", missing)
    assert detect(make_state(), GRAPH, ROOT, NOW) == []


def test_detect_disk_false_never_returns_disk_low(monkeypatch):
    def low(_root):
        raise AssertionError("disk check ran")

    monkeypatch.setattr(problems.shutil, "disk_usage", low)
    assert detect(make_state(), GRAPH, ROOT, NOW, disk=False) == []
    found = detect(busy_state(), GRAPH, ROOT, NOW, disk=False)
    assert "disk-low" not in {item["kind"] for item in found}
    assert "job-failed:job-1" in [item["id"] for item in found]


def busy_state():
    return make_state(
        {
            "brain-atlas": {"status": "needs-vp", "ts": ago(hours=30)},
            "brain-voice": {"status": "needs-owner", "ts": ago(hours=6)},
            "brain-si": {"status": "needs-owner", "ts": ago(hours=10)},
            "brain-courier": {"status": "working", "ts": ago(hours=50)},
        },
        decisions=[_ask("ask-9", 40)],
        failed=[{"id": "job-1", "title": "Build it", "model": None, "owner": "vp", "ts": ago(hours=12)}],
    )


def test_job_failed_sorts_ahead_of_an_older_high():
    state = with_github(pr(7, "red", days=2))
    state["bus"]["queue"]["failed_items"] = [
        {"id": "job-9", "title": "Build it", "model": None, "owner": "vp", "ts": ago(hours=1)},
    ]
    found = detect(state, GRAPH, ROOT, NOW)
    assert [p["id"] for p in found] == ["job-failed:job-9", "ci-red:coco-atlas#7"]
    assert found[0]["headline"] == "1 factory job failed"
    assert found[0]["since"] > found[1]["since"]


def test_detect_sorts_severity_then_oldest():
    found = detect(busy_state(), GRAPH, ROOT, NOW)
    assert [p["id"] for p in found] == [
        "job-failed:job-1",
        "decision-waiting:brain-si",
        "decision-waiting:brain-voice",
        "hq-unanswered:ask-9",
        "decision-waiting:brain-atlas",
        "quiet:brain-courier",
    ]
    assert [p["severity"] for p in found] == ["high", "high", "high", "medium", "medium", "low"]


def test_ids_stable_across_calls():
    first = [p["id"] for p in detect(busy_state(), GRAPH, ROOT, NOW)]
    second = [p["id"] for p in detect(busy_state(), GRAPH, ROOT, NOW + timedelta(minutes=5))]
    assert first == second and len(set(first)) == len(first)


def test_problem_shape_and_no_leaks(monkeypatch):
    monkeypatch.setattr(problems.shutil, "disk_usage", lambda _root: shutil._ntuple_diskusage(10**12, 0, 5 * GB))
    state = busy_state()
    state["nodes"]["brain-atlas"]["headline"] = "Review coco-research/coco#277 now"  # path-like: evidence drops it
    state["nodes"]["brain-voice"]["headline"] = "key sk-ABCDEFGH1234 leaked"
    found = detect(state, GRAPH, ROOT, NOW)
    assert {p["kind"] for p in found} == {"decision-waiting", "job-failed", "hq-unanswered", "quiet", "disk-low"}
    for problem in found:
        assert set(problem) == {"id", "kind", "severity", "owner", "since", "headline", "next_action", "evidence"}
        assert problem["severity"] in ("high", "medium", "low")
        assert problem["owner"] in NODE_IDS
        assert len(problem["headline"]) <= 60
        for value in problem.values():
            assert "/" not in value and "~/" not in value and "sandbox-root" not in value
            assert OWNER_NAME not in value and "sk-ABCDEFGH1234" not in value
    assert json.dumps(found)


def pr(number=7, checks="green", days=0, title="Add thing", repo="coco-atlas", node="brain-atlas"):
    return {"repo": repo, "node": node, "number": number, "title": title, "checks": checks,
            "updated": (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")}


def with_github(*prs, stale=False):
    state = make_state()
    state["github"] = {"updated": ago(), "stale": stale, "prs": list(prs)}
    return state


def test_ci_red_fires_only_on_red():
    state = with_github(pr(7, "red"), pr(8, "green"), pr(9, "pending"), pr(10, "none"))
    problem = one(ci_red(state, GRAPH, NOW))
    assert problem["id"] == "ci-red:coco-atlas#7"
    assert (problem["owner"], problem["severity"], problem["since"]) == ("brain-atlas", "high", pr()["updated"])
    assert problem["headline"] == "CI red on coco-atlas #7" and len(problem["headline"]) <= 60
    assert problem["next_action"] == "Fix or re-run the failing check."
    assert problem["evidence"] == "Add thing"


def test_pr_stale_edge():
    assert pr_stale(with_github(pr(days=PR_STALE_DAYS)), GRAPH, NOW) == []
    state = with_github(pr(days=PR_STALE_DAYS, title="Old"))
    state["github"]["prs"][0]["updated"] = ago(hours=PR_STALE_DAYS * 24 + 1)
    problem = one(pr_stale(state, GRAPH, NOW))
    assert problem["id"] == "pr-stale:coco-atlas#7"
    assert (problem["owner"], problem["severity"]) == ("brain-atlas", "low")
    assert problem["headline"] == "coco-atlas #7 untouched for 3d"
    assert problem["next_action"] == "Review, merge or close it."


def test_github_detectors_silent_without_data():
    for state in (make_state(), {**make_state(), "github": {"updated": None, "stale": False, "prs": []}}):
        assert ci_red(state, GRAPH, NOW) == [] and pr_stale(state, GRAPH, NOW) == []


def test_github_stale_cache_still_reports_and_detect_includes_both():
    state = with_github(pr(7, "red", days=5), stale=True)
    kinds = {item["kind"] for item in detect(state, GRAPH, ROOT, NOW)}
    assert {"ci-red", "pr-stale"} <= kinds


def test_budget_over_units_and_not_over():
    expected_since = "2026-10-02T04:00:00Z"
    pools = [
        {"id": "claude", "name": "Claude", "unit": "pts", "used": 4.0, "allowance": 10.5, "over": True, "as_of": None, "old": False, "extra": {}},
        {"id": "grok", "name": "Grok", "unit": "tokens", "used": 142_000_000, "allowance": 130_000_000, "over": True, "as_of": None, "old": False, "extra": {}},
        {"id": "glm", "name": "GLM", "unit": "credits", "used": 3700, "allowance": 3600, "over": True, "as_of": None, "old": False, "extra": {}},
        {"id": "deepseek", "name": "DeepSeek", "unit": "usd", "used": 1.20, "allowance": 0.00, "over": True, "as_of": None, "old": False, "extra": {}},
    ]
    found = budget_over(pools, NOW)
    assert len(found) == 4
    for p in found:
        assert p["kind"] == "budget-over"
        assert p["severity"] == "high"
        assert p["owner"] == "dept-budget"
        assert p["since"] == expected_since
        assert p["next_action"] == "Pause new jobs on this pool until it resets, or ask whoever manages its budget."
        assert p["evidence"] == ""

    assert found[0]["id"] == "budget-over:claude"
    assert found[0]["headline"] == "Over · Claude 4.0 of 10.5 pts"

    assert found[1]["id"] == "budget-over:grok"
    assert found[1]["headline"] == "Over · Grok 142M of 130M ceiling"

    assert found[2]["id"] == "budget-over:glm"
    assert found[2]["headline"] == "Over · GLM 3,700 of 3,600 credits"

    assert found[3]["id"] == "budget-over:deepseek"
    assert found[3]["headline"] == "Over · DeepSeek $1.20 of $0.00"

    other = [{"id": "other", "name": "Other", "unit": "custom", "used": 10, "allowance": 5, "over": True, "as_of": None, "old": False, "extra": {}}]
    assert one(budget_over(other, NOW))["headline"] == "Over · Other 10 of 5"

    not_over = [
        {"id": "claude", "name": "Claude", "unit": "pts", "used": 4.0, "allowance": 10.5, "over": False, "as_of": None, "old": False, "extra": {}},
        {"id": "grok", "name": "Grok", "unit": "tokens", "used": 142_000_000, "allowance": 130_000_000, "over": False, "as_of": None, "old": False, "extra": {}},
    ]
    assert budget_over(not_over, NOW) == []


def test_budget_over_allowance_none_at_limit():
    pools = [
        {"id": "claude", "name": "Claude", "unit": "pts", "used": 4.0, "allowance": None, "over": True, "as_of": None, "old": False, "extra": {}},
    ]
    problem = one(budget_over(pools, NOW))
    assert problem["id"] == "budget-over:claude"
    assert problem["headline"] == "Over · Claude at its limit"


def test_budget_over_glm_five_hour_window_full():
    pools = [
        {
            "id": "glm",
            "name": "GLM",
            "unit": "credits",
            "used": 900,
            "allowance": None,
            "over": True,
            "as_of": None,
            "old": False,
            "extra": {"five_hour": {"used": 100, "of": 100}},
        },
    ]
    problem = one(budget_over(pools, NOW))
    assert problem["id"] == "budget-over:glm"
    assert problem["headline"] == "Over · GLM 5 h window full"


def test_detect_budget_over_with_usage(tmp_path):
    # detect() still works when the usage dir is missing
    assert [p["id"] for p in detect(make_state(), GRAPH, tmp_path, NOW)] == []
    assert [p["id"] for p in detect(make_state(), GRAPH, tmp_path / "missing", NOW)] == []

    # Write meters.jsonl and caps.json making grok over
    usage = tmp_path / ".heliopause" / "inbox" / "lab" / "usage"
    usage.mkdir(parents=True)
    ts_yesterday = datetime(2026, 10, 1, 18, 0, tzinfo=_NY).timestamp()
    ts_today = datetime(2026, 10, 2, 7, 0, tzinfo=_NY).timestamp()
    lines = [
        json.dumps({"ts": ts_yesterday, "providers": {"grok": {"usedPercent": 0}}}),
        json.dumps({"ts": ts_today, "providers": {"grok": {"usedPercent": 20}}}),
    ]
    (usage / "meters.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    caps = {"allowances": {"pools": {"grok": {"daily_ceiling": 100_000_000}}}}
    (usage / "caps.json").write_text(json.dumps(caps), encoding="utf-8")

    problems_found = detect(make_state(), GRAPH, tmp_path, NOW)
    problem = one([p for p in problems_found if p["kind"] == "budget-over"])
    assert problem["id"] == "budget-over:grok"
    assert problem["severity"] == "high"
    assert problem["owner"] == "dept-budget"
    assert problem["headline"] == "Over · Grok 254M of 100M ceiling"
    assert problem["next_action"] == "Pause new jobs on this pool until it resets, or ask whoever manages its budget."


def test_detect_budget_exception_handled(monkeypatch, tmp_path):
    def bad_budget(_root, _now):
        raise OSError("read error")

    monkeypatch.setattr(problems, "budget", bad_budget)
    assert detect(make_state(), GRAPH, tmp_path, NOW) == []

    def val_err_budget(_root, _now):
        raise ValueError("invalid value")

    monkeypatch.setattr(problems, "budget", val_err_budget)
    assert detect(make_state(), GRAPH, tmp_path, NOW) == []

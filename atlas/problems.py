"""Problems engine: small pure detectors over one snapshot. Computed per request, no state kept."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlas.alerts import open_alerts
from atlas.budget import budget
from atlas.collect import LAB, LAB_LABELS, LAB_LEAD, _NOT_HEADLINE, _NY, redact
from atlas.graph import _parse
from atlas.jobs import PARK_MIN, jobs as job_view

DECISION_WAIT_H = 4
HQ_WAIT_H = 24
QUIET_H = 24
PR_STALE_DAYS = 3
DISK_FLOOR_GB = 20
DISK_HIGH_GB = 10
BUDGET_WARN = 0.80
BUDGET_HIGH = 0.95
_GB = 10**9
_RANK = {"high": 0, "medium": 1, "low": 2}
# Who a waiting state waits on (node ids).
_WAITS_ON = {"needs-owner": ("owner", "the Owner"), "needs-vp": ("vp", "the VP"), "needs-hq": ("hq", "HQ")}
_FIELDS = ("id", "kind", "severity", "owner", "since", "headline", "next_action", "evidence")


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    return f"{hours}h" if hours < 48 else f"{hours // 24}d"


def _label(graph: dict, node_id: str) -> str:
    # WHY: the owner's configured name is a personal name. Problems say "Owner".
    return "Owner" if node_id == "owner" else graph["nodes"][node_id]["label"]


def _evidence(text: object) -> str:
    # WHY: free text from the board or a job can hold paths, ids or hashes. Anything that does is dropped, not trimmed.
    if not isinstance(text, str):
        return ""
    clean = redact(text.strip())
    return clean[:120] if clean and _NOT_HEADLINE.search(clean) is None else ""


def _problem(kind, subject, severity, owner, since, headline, next_action, evidence="") -> dict:
    return dict(zip(_FIELDS, (
        f"{kind}:{subject}", kind, severity, owner, since, headline[:60], next_action, _evidence(evidence),
    )))


def decision_waiting(state: dict, graph: dict, now: datetime) -> list[dict]:
    found = []
    for node_id, node in state["nodes"].items():
        target = _WAITS_ON.get(node["status"])
        stamp = _parse(node["ts"])
        if target is None or stamp is None or now - stamp <= timedelta(hours=DECISION_WAIT_H):
            continue
        owner, who = target
        if owner == "hq":
            owner = state.get("hq_queue", "hq")
        # Lab boards say needs-lead, which collect maps to needs-vp. The lead is the first role-"lab" node.
        if LAB and node["status"] == "needs-vp":
            owner = LAB_LEAD
            who = "the Owner" if owner == "owner" else LAB_LABELS[owner]
        severity = "high" if owner == "owner" else "medium"
        found.append(_problem(
            "decision-waiting", node_id, severity, owner, node["ts"],
            f"{_label(graph, node_id)} waits on {who} for {_ago(now - stamp)}",
            "Answer or resolve it.", node["headline"],
        ))
    return found


def job_failed(state: dict, graph: dict, now: datetime) -> list[dict]:
    return [
        _problem(
            "job-failed", item["id"], "high", item["owner"], item["ts"],
            "1 factory job failed", "Re-run or withdraw the job.", item["title"],
        )
        for item in state["bus"]["queue"]["failed_items"]
    ]


def hq_unanswered(state: dict, graph: dict, now: datetime) -> list[dict]:
    found = []
    for ask in state["decisions"]:
        stamp = _parse(ask.get("ts"))
        # An ask with no id has no stable subject; collect leaves it out of the id too.
        if ask.get("kind") != "hq" or not ask.get("id") or stamp is None or now - stamp <= timedelta(hours=HQ_WAIT_H):
            continue
        found.append(_problem(
            "hq-unanswered", ask["id"], "medium", "vp", ask["ts"],
            f"An HQ ask has waited {_ago(now - stamp)}", "Chase it in the 8am brief.", ask.get("title"),
        ))
    return found


def quiet(state: dict, graph: dict, now: datetime) -> list[dict]:
    # ponytail: the "open handoffs" half of this rule waits for handoffs (row 7).
    jobq = state["bus"]["queue"]
    lanes = state.get("bus", {}).get("lanes", {})
    found = []
    for node_id, node in state["nodes"].items():
        stamp = _parse(node["ts"])
        # WHY: at 24 h exactly and for the same states as the page's quiet mark (needs-* go to decision-waiting).
        busy = node_id == "factory-queue" and (jobq["inbox"] or jobq["running"])
        if node_id in ("factory-gemini", "factory-zcode"):
            lane = lanes.get(node_id, {})
            if lane.get("paused") or lane.get("at_cap") or lane.get("busy", 0) > 0:
                continue
        if node["status"] not in ("working", "blocked", "unknown") or stamp is None or busy or now - stamp < timedelta(hours=QUIET_H):
            continue
        found.append(_problem(
            "quiet", node_id, "low", node_id, node["ts"],
            f"{_label(graph, node_id)} has been quiet for {_ago(now - stamp)}",
            "Post your state or hand the work off.", node["headline"],
        ))
    return found


def disk_low(free_bytes: int, now: datetime) -> list[dict]:
    if free_bytes >= DISK_FLOOR_GB * _GB:
        return []
    severity = "high" if free_bytes < DISK_HIGH_GB * _GB else "medium"
    return [_problem(
        "disk-low", "home", severity, "dept-engineering", _iso(now),
        f"Only {free_bytes / _GB:.0f} GB free on the home disk",
        "Free space or move caches off the home volume.",
    )]


def budget_near_cap(state: dict, graph: dict, now: datetime) -> list[dict]:
    grok = state["bus"]["grok"]
    cap, used = grok.get("cap"), grok.get("tokens_today", 0)
    if not cap or used / cap < BUDGET_WARN:
        return []
    severity = "high" if used / cap >= BUDGET_HIGH else "medium"
    # WHY: since is today's ET midnight (stable all day); the cap resets on the ET day.
    midnight = now.astimezone(_NY).replace(hour=0, minute=0, second=0, microsecond=0)
    return [_problem(
        "budget-near-cap", "grok-build", severity, "dept-budget", _iso(midnight),
        f"Grok Build used {used * 100 // cap}% of today's cap",
        "Move new jobs to the queue factory or wait for the reset.",
        f"{used / 1e6:.0f}M of {cap / 1e6:.0f}M tokens",
    )]


def _budget_sentence(unit: str | None, used: object, allowance: object) -> str:
    if unit == "pts":
        u = f"{used:.1f}" if isinstance(used, (int, float)) else str(used)
        a = f"{allowance:.1f}" if isinstance(allowance, (int, float)) else str(allowance)
        return f"{u} of {a} pts"
    if unit == "tokens":
        u = f"{round(used / 1e6)}M" if isinstance(used, (int, float)) else str(used)
        a = f"{round(allowance / 1e6)}M" if isinstance(allowance, (int, float)) else str(allowance)
        return f"{u} of {a} ceiling"
    if unit == "credits":
        u = f"{int(used):,}" if isinstance(used, (int, float)) else str(used)
        a = f"{int(allowance):,}" if isinstance(allowance, (int, float)) else str(allowance)
        return f"{u} of {a} credits"
    if unit == "usd":
        u = f"${used:.2f}" if isinstance(used, (int, float)) else f"${used}"
        a = f"${allowance:.2f}" if isinstance(allowance, (int, float)) else f"${allowance}"
        return f"{u} of {a}"
    return f"{used} of {allowance}"


def budget_over(pools: list[dict], now: datetime) -> list[dict]:
    midnight = now.astimezone(_NY).replace(hour=0, minute=0, second=0, microsecond=0)
    since = _iso(midnight)
    found = []
    for pool in pools:
        if not pool.get("over"):
            continue
        name = pool["name"]
        if pool.get("allowance") is None:
            if (pool.get("id") == "glm" or name == "GLM") and (pool.get("extra") or {}).get("five_hour"):
                headline = "Over · GLM 5 h window full"
            else:
                headline = f"Over · {name} at its limit"
        else:
            sentence = _budget_sentence(pool.get("unit"), pool.get("used"), pool.get("allowance"))
            headline = f"Over · {name} {sentence}"
        found.append(_problem(
            "budget-over",
            pool["id"],
            "high",
            "dept-budget",
            since,
            headline,
            "Pause new jobs on this pool until it resets, or ask whoever manages its budget.",
            "lab/usage/meters.jsonl",
        ))
    return found


def _prs(state: dict):
    # WHY: a stale cache still holds the last good PRs; report from them.
    return (state.get("github") or {}).get("prs", [])


def ci_red(state: dict, graph: dict, now: datetime) -> list[dict]:
    return [
        _problem(
            "ci-red", f"{pr['repo']}#{pr['number']}", "high", pr["node"], pr["updated"],
            f"CI red on {pr['repo']} #{pr['number']}", "Fix or re-run the failing check.", pr["title"],
        )
        for pr in _prs(state)
        if pr["checks"] == "red"
    ]


def pr_stale(state: dict, graph: dict, now: datetime) -> list[dict]:
    found = []
    for pr in _prs(state):
        stamp = _parse(pr["updated"])
        if stamp is None or now - stamp <= timedelta(days=PR_STALE_DAYS):
            continue
        found.append(_problem(
            "pr-stale", f"{pr['repo']}#{pr['number']}", "low", pr["node"], pr["updated"],
            f"{pr['repo']} #{pr['number']} untouched for {(now - stamp).days}d",
            "Review, merge or close it.", pr["title"],
        ))
    return found


def _lane_owner(lane: object) -> str:
    if not isinstance(lane, str):
        return "vp"
    if lane == "grok" or lane.startswith("grok-"):
        return "factory-grok"
    if lane.startswith("queue"):
        return "factory-queue"
    if lane == "gemini":
        return "factory-gemini"
    if lane == "zcode":
        return "factory-zcode"
    return "vp"


def _skip_reasons(tried: object) -> list[str]:
    if not isinstance(tried, list):
        return []
    found = []
    for item in tried:
        if not isinstance(item, dict):
            continue
        reason = item.get("skip")
        if not isinstance(reason, str) or not reason:
            reason = item.get("class")
        if isinstance(reason, str) and reason:
            found.append(reason)
        if len(found) == 2:
            break
    return found


def _parked_age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes}m"
    return _ago(delta)


def _bad_lanes(view: dict) -> set[str]:
    bad = set()
    for lane, row in (view.get("lanes") or {}).items():
        if not isinstance(row, dict) or not isinstance(lane, str):
            continue
        parked = row.get("parked")
        if isinstance(parked, int) and not isinstance(parked, bool) and parked > 0:
            bad.add(lane)
        if row.get("failing") is True:
            bad.add(lane)
    return bad


def _pipe_problems(view: dict, now: datetime) -> list[dict]:
    # ponytail: since comes from the capped job list. The parked count and failing flag use the full 24h window.
    found = []
    jobs_list = view.get("jobs") or []
    for lane, row in (view.get("lanes") or {}).items():
        if not isinstance(row, dict) or not isinstance(lane, str):
            continue
        parked = row.get("parked")
        if isinstance(parked, int) and not isinstance(parked, bool) and parked > 0:
            oldest = None
            for job in jobs_list:
                if job.get("lane") != lane or job.get("state") != "queued":
                    continue
                stamp = _parse(job.get("started"))
                if stamp is None or now - stamp <= timedelta(minutes=PARK_MIN):
                    continue
                if oldest is None or stamp < oldest:
                    oldest = stamp
            since = oldest or now
            found.append(_problem(
                "pipe-parked", lane, "high", _lane_owner(lane), _iso(since),
                f"Pipe parked \u00b7 {lane}: {parked} jobs waiting {_parked_age(now - since)}",
                "Check the lane; jobs are waiting with no dispatch.",
            ))
        if row.get("failing") is True:
            ended = []
            for job in jobs_list:
                if job.get("lane") != lane or job.get("state") not in ("delivered", "failed"):
                    continue
                stamp = _parse(job.get("ended"))
                if stamp is not None:
                    ended.append((stamp, job.get("state")))
            ended.sort(key=lambda item: item[0], reverse=True)
            last = ended[:3]
            since = last[-1][0] if len(last) == 3 else now
            found.append(_problem(
                "pipe-failing", lane, "high", _lane_owner(lane), _iso(since),
                f"Pipe failing \u00b7 {lane}: last 3 jobs failed",
                "Check the lane; its last jobs failed.",
            ))
    return found


def _failing_jobs(view: dict) -> dict[str, str]:
    failing = {
        lane for lane, row in (view.get("lanes") or {}).items()
        if isinstance(row, dict) and row.get("failing") is True
    }
    found = {}
    for job in view.get("jobs") or []:
        lane = job.get("lane")
        ident = job.get("job")
        if lane in failing and job.get("state") == "failed" and isinstance(ident, str):
            found[ident] = lane
    return found


def _journal(root: Path, now: datetime) -> tuple[list[dict], dict[str, str]]:
    view = job_view(root, now)
    bad = _bad_lanes(view)
    found = _pipe_problems(view, now)
    for job in view["jobs"]:
        if job.get("stuck"):
            lane = job.get("lane") if isinstance(job.get("lane"), str) and job.get("lane") else "unknown"
            if lane in bad:
                continue
            minutes = job.get("minutes")
            whole = int(minutes) if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) else 0
            found.append(_problem(
                "job-stuck", job["job"], "medium", _lane_owner(job.get("lane")), job["started"],
                f"Stuck \u00b7 {lane} job running {whole} min",
                "Check the lane; it is past its timeout.",
            ))
        elif job.get("state") == "no_lane":
            label = job.get("type") if isinstance(job.get("type"), str) and job.get("type") else "job"
            reasons = _skip_reasons(job.get("tried"))
            headline = f"No lane for {label}: {', '.join(reasons)}" if reasons else f"No lane for {label}"
            found.append(_problem(
                "no-lane", job["job"], "high", "dept-budget", job["started"],
                headline, "Free a lane or wait for a cap to reset.",
            ))
    return found, _failing_jobs(view)


def journal_problems(root: Path, now: datetime) -> list[dict]:
    """Pipe health, stuck runs, and routes that picked no lane. A failed job is only a lane count."""
    found, _piped = _journal(root, now)
    return found


def alerts_waiting(root: Path, now: datetime) -> list[dict]:
    """Open needs-owner alerts, one row each. warn and info are not problems."""
    found = []
    for row in open_alerts(root):
        if row["severity"] != "needs-owner":
            continue
        try:
            when = datetime.fromtimestamp(row["ts"], timezone.utc)
        except (OverflowError, OSError, ValueError):
            continue
        text = row["text"]
        # WHY: the row already shows the age; the headline is the alert itself, clipped on a word.
        headline = text if len(text) <= 60 else text[:59].rsplit(" ", 1)[0] + "…"
        item = _problem(
            "alert", row["id"], "high", "owner", _iso(when),
            headline,
            row["link"] if "link" in row else "Answer or resolve it.",
            text,
        )
        found.append(item)
    return found


def detect(state: dict, graph: dict, root: Path, now: datetime, *, disk: bool = True) -> list[dict]:
    """All problems for one snapshot: severity, then failed jobs, then oldest."""
    try:
        pools = budget(root, now)["pools"]
    except (OSError, ValueError):
        pools = []
    try:
        factory, piped = _journal(root, now)
    except (OSError, ValueError):
        factory, piped = [], {}
    try:
        waiting_alerts = alerts_waiting(root, now)
    except (OSError, ValueError, OverflowError):
        waiting_alerts = []
    found = [
        *decision_waiting(state, graph, now),
        *waiting_alerts,
        *job_failed(state, graph, now),
        *factory,
        *hq_unanswered(state, graph, now),
        *quiet(state, graph, now),
        *budget_near_cap(state, graph, now),
        *budget_over(pools, now),
        *ci_red(state, graph, now),
        *pr_stale(state, graph, now),
    ]
    # --demo passes disk=False so a recording never shows this machine's free space.
    if disk and not LAB:
        try:
            found += disk_low(shutil.disk_usage(root).free, now)
        except OSError:
            pass
    # A failing lane's job-failed rows stay as evidence. The status line skips any row that names its pipe.
    for item in found:
        if item["kind"] != "job-failed":
            continue
        lane = piped.get(item["id"].split(":", 1)[-1])
        if lane:
            item["pipe"] = lane
    # Failed jobs lead their severity band; the rest stay oldest-first.
    return sorted(found, key=lambda item: (
        _RANK[item["severity"]], 0 if item["kind"] == "job-failed" else 1, item["since"],
    ))

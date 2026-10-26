"""Factory jobs for the last 24 hours, joined from the usage journals."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlas import collect

# ponytail: fixed per-lane timeouts. Read them from a config when routes.json carries them.
_LANE_MINUTES = {
    "gemini": 30,
    "zcode": 30,
    "grok": 60,
    "queue": 60,
    "nvidia": 10,
    "ling": 10,
    "cursor": 60,
}
_DEFAULT_MINUTES = 60
# ponytail: newest 200 only. Lane counts still use the full 24h window.
_CAP = 200
# ponytail: parked after 10 min with no dispatch; failing is the last 3 ended jobs all failed, newest within 6 h.
# Raise either window when a lane's normal queue or retry gap is longer.
PARK_MIN = 10
_FAIL_LAST = 3
_FAIL_H = 6
_FILES = (("route", "routes.jsonl"), ("dispatch", "dispatch.jsonl"), ("end", "jobs-end.jsonl"))


def _iso(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _limit(lane: str | None) -> int:
    # grok-build and queue-* share the grok / queue minutes. Unknown lanes use the default.
    if not lane:
        return _DEFAULT_MINUTES
    if lane in _LANE_MINUTES:
        return _LANE_MINUTES[lane]
    return _LANE_MINUTES.get(lane.split("-", 1)[0], _DEFAULT_MINUTES)


def _load(root: Path, name: str) -> list[tuple[str, datetime, int, dict]]:
    raw = collect._read(root / ".heliopause" / "inbox" / "lab" / "usage" / name)
    if not raw:
        return []
    found = []
    for seq, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        job = item.get("job")
        # WHY: cwd is a home path, and task/result text stays in the journal. Neither is copied out.
        if not isinstance(job, str) or not job:
            continue
        stamp = collect._run_ts(item.get("ts_utc"))
        if stamp is None:
            continue
        found.append((job, stamp, seq, item))
    return found


def _name(item: dict | None, key: str) -> str | None:
    if not item:
        return None
    value = item.get(key)
    return value if isinstance(value, str) and value else None


def _redacted(item: dict | None, key: str) -> str | None:
    value = _name(item, key)
    return collect.redact(value) if value is not None else None


def _number(value: object):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _tried(item: dict | None):
    if not item or not isinstance(item.get("tried"), list):
        return None
    rows = []
    for entry in item["tried"]:
        if not isinstance(entry, dict):
            continue
        row = {}
        lane = collect.lane_name(entry.get("lane"))
        if isinstance(lane, str):
            row["lane"] = lane
        for key in ("skip", "class"):
            text = entry.get(key)
            if isinstance(text, str):
                row[key] = collect.redact(text)
        if row:
            rows.append(row)
    return rows


def _blank_lane() -> dict:
    return {"running": 0, "delivered_today": 0, "failed_today": 0, "parked": 0, "failing": False}


def _lane_health(records: list, lanes: dict[str, dict], now: datetime) -> None:
    parked: dict[str, int] = {}
    ended: dict[str, list[tuple[datetime, str]]] = {}
    for row, ended_at in records:
        lane = row.get("lane")
        if not isinstance(lane, str) or not lane:
            continue
        if row["state"] == "queued":
            try:  # written by _iso above; guarded so one odd row never breaks the lane counts
                started = datetime.strptime(row["started"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if now - started > timedelta(minutes=PARK_MIN):
                parked[lane] = parked.get(lane, 0) + 1
        elif ended_at is not None and row["state"] in ("delivered", "failed"):
            ended.setdefault(lane, []).append((ended_at, row["state"]))
    for name, count in parked.items():
        lanes.setdefault(name, _blank_lane())["parked"] = count
    window = timedelta(hours=_FAIL_H)
    for name, rows in ended.items():
        rows.sort(key=lambda item: item[0], reverse=True)
        last = rows[:_FAIL_LAST]
        failing = (
            len(last) == _FAIL_LAST
            and all(state == "failed" for _when, state in last)
            and now - last[0][0] <= window
        )
        if failing or name in lanes:
            lanes.setdefault(name, _blank_lane())["failing"] = failing


def _latest(rows: list[tuple[datetime, int, dict]]) -> tuple[datetime, dict] | None:
    if not rows:
        return None
    stamp, _seq, item = max(rows, key=lambda row: (row[0], row[1]))
    return stamp, item


def jobs(root: Path, now: datetime) -> dict:
    """Jobs whose first journal line is in the last 24 hours, newest first."""
    if collect.LAB:
        return {"jobs": [], "lanes": {}}
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=24)
    grouped: dict[str, dict[str, list]] = {}
    for kind, name in _FILES:
        for job, stamp, seq, item in _load(Path(root), name):
            grouped.setdefault(job, {"route": [], "dispatch": [], "end": []})[kind].append((stamp, seq, item))
    day = collect._today()
    records = []
    for job, parts in grouped.items():
        events = [row for rows in parts.values() for row in rows]
        first = min(events, key=lambda row: (row[0], row[1]))[0]
        if first < cutoff:
            continue
        route = _latest(parts["route"])
        disp = _latest(parts["dispatch"])
        end = _latest(parts["end"])
        route_row = route[1] if route else None
        disp_row = disp[1] if disp else None
        end_row = end[1] if end else None
        ended_at = end[0] if end else None
        lane = collect.lane_name(_name(disp_row, "lane") or _name(route_row, "chosen"))
        kind_name = _name(route_row, "type")
        cls = _redacted(end_row, "class")  # written outside Heliopause: redact like every other journal text
        if end_row is not None:
            state = "delivered" if cls == "ok" else "failed"
        elif disp_row is not None:
            state = "running"
        elif lane:
            state = "queued"
        else:
            state = "no_lane"
        minutes = _number(end_row.get("minutes")) if end_row else None
        # Running time starts at dispatch. `started` stays the first line, for the 24h window.
        if minutes is None and state == "running" and disp is not None:
            minutes = round((now - disp[0]).total_seconds() / 60, 1)
        stuck = state == "running" and disp is not None and (now - disp[0]) > timedelta(minutes=_limit(lane))
        records.append(({
            "job": job,
            "type": kind_name,
            "lane": lane,
            "model": _name(disp_row, "model"),
            "story": _redacted(disp_row, "story") or _redacted(route_row, "story"),
            "branch": _redacted(disp_row, "branch"),
            "state": state,
            "started": _iso(first),
            "ended": _iso(ended_at) if ended_at else None,
            "minutes": minutes,
            "class": cls,
            "tried": _tried(route_row),
            "stuck": stuck,
        }, ended_at))
    lanes: dict[str, dict] = {}

    def bump(name: str | None, key: str) -> None:
        if not name:
            return
        row = lanes.setdefault(name, _blank_lane())
        row[key] += 1

    for row, ended_at in records:
        if row["state"] == "running":
            bump(row["lane"], "running")
        elif ended_at is not None and ended_at.astimezone(collect._NY).date() == day:
            if row["state"] == "delivered":
                bump(row["lane"], "delivered_today")
            elif row["state"] == "failed":
                bump(row["lane"], "failed_today")
    _lane_health(records, lanes, now)
    records.sort(key=lambda pair: pair[0]["job"])
    records.sort(key=lambda pair: pair[0]["started"], reverse=True)
    return {"jobs": [row for row, _ended in records[:_CAP]], "lanes": lanes}

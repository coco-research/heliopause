"""Read-only budget pools from <root>/.heliopause/inbox/lab/usage."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from atlas.collect import LAB, LAB_DIR, LEGACY_POOL, _NY, _read

# ponytail: ~12.7M tokens per xAI point, from caps.json's note.
_POINT = 12_700_000
_POOLS = (
    ("claude", "Claude", "pts", "counter"),
    ("grok", "Grok", "tokens", "counter"),
    ("glm", "GLM", "credits", "counter"),
    ("deepseek", "DeepSeek", "usd", "remaining"),
    ("apikeyfan", "apikey.fan", "usd", "counter"),
    ("default-pool", "Default pool", "weekly_pct", "level"),
)
# Older pool names (current id -> old id) come from the internal legacy map ({} in a public build).
_LEGACY = LEGACY_POOL


def _caps_key(pool_id):
    # deepseek's allowance is stored under deepseek_cc1; the other pools use their id.
    return "deepseek_cc1" if pool_id == "deepseek" else pool_id


def _num(value):
    return None if isinstance(value, bool) or not isinstance(value, (int, float)) else value


def _dig(obj, *keys):
    for key in keys:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def _readings(row):
    source, weekly, five, seen = row.get("source"), None, None, False
    windows = _dig(row, "providers", "zai", "windows")
    for item in windows if isinstance(windows, list) else ():
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if label == "weekly" and not seen:
            weekly, seen = _num(item.get("used")), True
        elif label == "5-hour":
            used, limit = _num(item.get("used")), _num(item.get("of"))
            five = {"used": used, "of": limit} if used is not None and limit is not None else five
    grok = _num(_dig(row, "providers", "grok", "usedPercent"))
    if grok is None and source == "vendor":
        grok = _num(_dig(row, "supergrok", "pct"))
    bot = None
    if source == "vendor":
        bot = _num(_dig(row, "default-pool", "pct"))
        old = _LEGACY.get("default-pool")
        bot = _num(_dig(row, old, "pct")) if bot is None and old else bot
    if bot is None:
        bot = _num(_dig(row, "providers", "default-pool", "usedPercent"))
    if bot is None and _LEGACY.get("default-pool"):
        bot = _num(_dig(row, "providers", _LEGACY["default-pool"], "usedPercent"))
    found = {
        "claude": _num(_dig(row, "claude", "weeklyPct")) if source in ("get_usage", "vendor") else None,
        "grok": grok,
        "glm": weekly,
        "deepseek": _num(_dig(row, "providers", "commandcode1", "monthlyRemaining")),
        "apikeyfan": _num(_dig(row, "providers", "apikeyfan", "monthSpend")),
        "default-pool": bot,
    }
    return found, five


def _load(root: Path):
    # ponytail: reads all of meters.jsonl per request; tail-read it when the file passes ~50k lines.
    usage = Path(root) / ".heliopause" / "inbox" / "lab" / "usage"
    rows = []
    for line in (_read(usage / "meters.jsonl") or "").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        stamp = _num(item.get("ts")) if isinstance(item, dict) else None
        if stamp is not None:
            rows.append((float(stamp), item))
    rows.sort(key=lambda pair: pair[0])
    # gate is false when caps.json is missing, unreadable, or has no allowances.pools dict.
    gate, caps = False, {}
    try:
        found = json.loads(_read(usage / "caps.json") or "")["allowances"]["pools"]
    except (json.JSONDecodeError, KeyError, TypeError):
        found = None
    if isinstance(found, dict):
        gate, caps = True, found
    return rows, caps, gate


def _field(caps, pool, key):
    spec = caps.get(pool)
    return _num(spec.get(key)) if isinstance(spec, dict) else None


def _deepseek(spec, now):
    if not isinstance(spec, dict):
        return None, None
    allowance, parsed = _num(spec.get("daily")), []
    after = spec.get("daily_after")
    if isinstance(after, dict):
        for key, value in after.items():
            number = _num(value)
            try:
                stamp = datetime.fromisoformat(key) if isinstance(key, str) else None
            except ValueError:
                stamp = None
            if stamp is not None and stamp.tzinfo and number is not None:
                parsed.append((stamp, key, number))
    past = [item for item in parsed if item[0] <= now]
    future = [item for item in parsed if item[0] > now]
    if past:
        allowance = max(past)[2]
    return allowance, min(future)[1] if future else None


def _spend(points, kind, now):
    local = now.astimezone(_NY)
    cut = datetime(local.year, local.month, local.day, tzinfo=_NY).timestamp()
    before = [value for stamp, value in points if stamp < cut]
    series = ([before[-1]] if before else []) + [value for stamp, value in points if stamp >= cut]
    if len(series) < 2:
        return 0
    total = 0
    for left, right in zip(series, series[1:]):
        grow = right >= left if kind == "counter" else left >= right
        total += (right - left if kind == "counter" else left - right) if grow else (right if kind == "counter" else 0)
    return round(total, 2)


def budget(root: Path, now: datetime) -> dict:
    """Pools listed in caps.json. Missing or unreadable caps keeps every pool."""
    if LAB:
        # ponytail: a lab has no meters yet, so its pools come ready-made from DIR/usage/budget.json
        # (the same shape as below). A fresh lab has none. Add a meter adapter when labs track spend.
        try:
            data = json.loads(_read(LAB_DIR / "usage" / "budget.json") or "{}") if LAB_DIR else {}
        except ValueError:
            data = {}
        pools = data.get("pools") if isinstance(data, dict) else None
        return {"pools": [p for p in pools if isinstance(p, dict)] if isinstance(pools, list) else []}
    rows, caps, gate = _load(root)
    series = {pool_id: [] for pool_id, *_rest in _POOLS}
    five = resets = None
    for stamp, row in rows:
        values, window = _readings(row)
        five = window if window is not None else five
        text = _dig(row, "claude", "weeklyResetsAt")
        resets = text if isinstance(text, str) and text else resets
        for pool_id, _name, _unit, _kind in _POOLS:
            if values[pool_id] is not None:
                series[pool_id].append((stamp, values[pool_id]))
    deep, until = _deepseek(caps.get("deepseek_cc1"), now)
    allow = {
        "claude": _field(caps, "claude", "daily"),
        "grok": _field(caps, "grok", "daily_ceiling"),
        "glm": _field(caps, "glm", "daily"),
        "deepseek": deep,
        "apikeyfan": _field(caps, "apikeyfan", "daily"),
    }
    target = _field(caps, "grok", "daily_target_grok_build")
    pools = []
    for pool_id, name, unit, kind in _POOLS:
        if gate and _caps_key(pool_id) not in caps and _LEGACY.get(pool_id) not in caps:
            continue
        points = series[pool_id]
        as_of = points[-1][0] if points else None
        used = None if not points else points[-1][1] if kind == "level" else _spend(points, kind, now)
        if points and pool_id == "grok":
            used = int(round(used * _POINT))
        allowance = None if kind == "level" else allow[pool_id]
        over = False
        if points and allowance is not None and kind != "level":
            over = used > allowance + (0.005 if unit == "usd" else 1e-9)
        if points and pool_id == "glm" and five and five["used"] >= five["of"]:
            over = True
        extra = {
            "claude": {"week_pct": None if not points else points[-1][1], "resets_at": resets},
            "grok": {"target": target},
            "glm": {"five_hour": five},
            "deepseek": {"allowance_until": until},
        }.get(pool_id, {})
        pools.append({
            "id": pool_id, "name": name, "unit": unit, "used": used, "allowance": allowance,
            "over": over, "as_of": as_of, "old": as_of is not None and now.timestamp() - as_of > 7200,
            "extra": extra,
        })
    return {"pools": pools}

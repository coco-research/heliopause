"""Tool seats and switches in atlas.json. GATED names refuse to turn on; off is allowed."""

from __future__ import annotations

import json
from pathlib import Path

from atlas import labfs
from atlas.alerts import _locked
from atlas.connectors import TOOLS

_FILE = "atlas.json"
_SEATS = ("hq", "lab", "factory")
_NAMES = ("self_start", "answer_asks", "approve", "receive_in_session")
# Refuse "on" until the next release. Off is always allowed.
GATED = frozenset(_NAMES)
# The field's own hint. Storing it would read back as the user's project.
_GOAL_PLACEHOLDER = "e.g. A small web shop: api-server is the backend, web-app is the storefront."

def _fail(code: int, message: str) -> tuple[int, dict]:
    return code, {"error": message}

def _parse(raw: bytes | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}

def _load(lab: Path) -> dict:
    try:
        return _parse(labfs.read(lab, _FILE))
    except OSError:
        return {}

def _roles_of(data: dict) -> dict:
    raw = data.get("roles")
    raw = raw if isinstance(raw, dict) else {}
    hq = raw.get("hq")
    hq = hq if isinstance(hq, str) and (hq == "you" or hq in TOOLS) else None
    lab = raw.get("lab")
    lab = lab if isinstance(lab, str) and lab in TOOLS and lab != hq else None
    items = raw.get("factory")
    # J2: one tool plays Lab and Factory as two chats. It still cannot also be HQ.
    if isinstance(lab, str) and isinstance(items, list) and items == [lab]:
        return {"hq": hq, "lab": lab, "factory": [lab]}
    taken = {seat for seat in (hq, lab) if seat and seat != "you"}
    factory: list[str] = []
    if isinstance(items, list):
        for item in items:
            if item in TOOLS and item not in taken and item not in factory:
                factory.append(item)
                taken.add(item)
                if len(factory) == 3:
                    break
    return {"hq": hq, "lab": lab, "factory": factory}

def _seated(roles: dict) -> list[str]:
    found: list[str] = []
    for tool in (roles["hq"], roles["lab"], *roles["factory"]):
        if tool and tool != "you" and tool not in found:
            found.append(tool)
    return found

def _fits(name: str, tool: str, roles: dict) -> bool:
    if name == "self_start":
        return tool in roles["factory"]
    if name in ("answer_asks", "approve"):
        return roles["hq"] == tool
    return name == "receive_in_session" and tool in _seated(roles)

def _switches(data: dict, roles: dict) -> dict:
    stored = data.get("switches")
    stored = stored if isinstance(stored, dict) else {}
    out = {}
    for tool in _seated(roles):
        blob = stored.get(tool)
        blob = blob if isinstance(blob, dict) else {}
        flags = {name: blob.get(name) is True and _fits(name, tool, roles) for name in _NAMES}
        if flags["approve"] and not flags["answer_asks"]:
            flags["approve"] = False
        out[tool] = flags
    return out

def read(lab: Path) -> dict:
    data = _load(lab)
    roles = _roles_of(data)
    return {"roles": roles, "switches": _switches(data, roles), "gated": sorted(GATED)}

def _validate(roles: object) -> tuple[str | None, dict | None]:
    if not isinstance(roles, dict):
        return "Roles must be an object.", None
    if set(roles) - set(_SEATS):
        return "Unknown key in roles.", None
    hq, lab = roles.get("hq"), roles.get("lab")
    if hq is not None and (not isinstance(hq, str) or (hq != "you" and hq not in TOOLS)):
        return ("hq must be a tool or you." if not isinstance(hq, str) else "Unknown tool."), None
    if lab == "you":
        return "you can only sit in hq.", None
    if lab is not None and (not isinstance(lab, str) or lab not in TOOLS):
        return ("lab must be a tool." if not isinstance(lab, str) else "Unknown tool."), None
    factory = roles.get("factory", [])
    if not isinstance(factory, list):
        return "Factory must be a list of tools.", None
    clean: list[str] = []
    for item in factory:
        if item == "you":
            return "you can only sit in hq.", None
        if not isinstance(item, str):
            return "Factory must be a list of tools.", None
        if item not in TOOLS:
            return "Unknown tool.", None
        if item in clean:
            return "A tool is listed twice.", None
        clean.append(item)
    if len(clean) > 3:
        return "Factory holds at most 3 tools.", None
    taken = [seat for seat in (None if hq == "you" else hq, lab) if isinstance(seat, str)]
    # J2: factory may be exactly [lab]. A longer list that also includes lab stays illegal.
    j2 = clean == [lab] and isinstance(lab, str)
    if (not j2 and any(item in taken for item in clean)) or len(taken) != len(set(taken)):
        return "A tool cannot sit in two seats.", None
    return None, {"hq": hq if isinstance(hq, str) else None,
                  "lab": lab if isinstance(lab, str) else None, "factory": clean}

def _save(root: Path, data: dict) -> None:
    labfs.write(root, _FILE, (json.dumps(data, indent=2) + "\n").encode("utf-8"))

def set_roles(lab: Path, roles: object, goal: str | None = None) -> tuple[int, dict]:
    problem, clean = _validate(roles)
    if problem or clean is None:
        return _fail(400, problem or "Roles must be an object.")

    def apply(root: Path) -> tuple[int, dict]:
        data = _parse(labfs.read(root, _FILE))
        kept = _switches(data, _roles_of(data))
        data["roles"] = clean
        data["switches"] = _switches({"switches": kept}, clean)
        if goal is not None:
            text = goal.strip()
            if text and text != _GOAL_PLACEHOLDER:
                data["goal"] = text
            else:
                data.pop("goal", None)
        _save(root, data)
        return 200, read(root)

    return _locked(Path(lab), apply)

def set_switch(lab: Path, tool: object, name: object, value: object) -> tuple[int, dict]:
    if not isinstance(value, bool):
        return _fail(400, "value must be true or false.")
    if not isinstance(tool, str) or tool not in TOOLS:
        return _fail(400, "Unknown tool.")
    if not isinstance(name, str) or name not in _NAMES:
        return _fail(400, "Unknown switch.")

    def apply(root: Path) -> tuple[int, dict]:
        data = _parse(labfs.read(root, _FILE))
        roles = _roles_of(data)
        if not _fits(name, tool, roles):
            return _fail(400, "That switch is not allowed for this seat.")
        if value is True and name in GATED:
            return _fail(403, "This switch is off until the next release.")
        switches = _switches(data, roles)
        if name == "approve" and value is True and not switches[tool]["answer_asks"]:
            return _fail(400, "approve needs answer_asks on.")
        switches[tool][name] = value
        if name == "answer_asks" and value is False:
            switches[tool]["approve"] = False
        data["roles"] = roles
        data["switches"] = switches
        _save(root, data)
        return 200, read(root)

    return _locked(Path(lab), apply)

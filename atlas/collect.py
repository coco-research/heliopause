"""Read-only snapshot of the lab board, HQ queue, and the factory job queue."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas import labfs

def map_path(name: str) -> Path:
    """Package map file, or ATLAS_MAPS_DIR/name when that variable is set and non-empty."""
    override = os.environ.get("ATLAS_MAPS_DIR")
    if override:
        return Path(override) / name
    return Path(__file__).with_name(name)


# HQ lane, in spec order. The generic "hq" box follows them in NODE_IDS.
HQ_ROLES = (
    ("hq-cos", "Chief of Staff"),
    ("hq-ceo", "CEO"),
    ("hq-cpo", "CPO"),
    ("hq-cto", "CTO"),
    ("hq-cfo", "CFO"),
    ("hq-coo", "COO"),
    ("hq-cmo", "CMO"),
    ("hq-cso", "CSO"),
    ("hq-cco", "CCO"),
    ("hq-gc", "General Counsel"),
)
ROLE_LABELS = dict(HQ_ROLES)
ROLE_IDS = tuple(ROLE_LABELS)
_NOT_LAB = {*ROLE_LABELS, "hq", "owner"}  # a Lab persona can never map to HQ or to the owner
# Address tokens. "hr" is the COO: HR sits under COO and is not its own box.
ROLE_KEYS = (
    ("cos", "hq-cos"),
    ("chief-of-staff", "hq-cos"),
    ("ceo", "hq-ceo"),
    ("cpo", "hq-cpo"),
    ("cto", "hq-cto"),
    ("cfo", "hq-cfo"),
    ("coo", "hq-coo"),
    ("hr", "hq-coo"),
    ("cmo", "hq-cmo"),
    ("cso", "hq-cso"),
    ("cco", "hq-cco"),
    ("gc", "hq-gc"),
    ("general-counsel", "hq-gc"),
)
_ROLE_KEY = dict(ROLE_KEYS)
# Off the graph: whole word or phrase, case-insensitive. A topic we cannot classify must not pulse.
# Neutral categories only: credentials, financial, health, legal, government ID, contact.
_SENSITIVE = re.compile(
    r"\b(?:credentials?|passwords?|keychain|sign-in|signin|login|secrets?|api key"  # credentials
    r"|bank account|broker(?:age)?|gardening|credit card|salary|payroll|tax return"  # financial
    r"|health|medical|diagnosis"  # health
    r"|legal|lawsuit|litigation"  # legal
    r"|passport|ssn|social security|national id|driver'?s licen[cs]e"  # government ID
    r"|phone number|home address|contact details)\b",  # contact
    re.IGNORECASE,
)
_GATE = ("private", "visibility", "audience", "scope")
_NODE_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
# Product nodes. The internal map nodes.json ({} in a public build) replaces this neutral default.
_DEFAULT_PRODUCTS = (("product-a", "Product A"), ("product-b", "Product B"), ("product-c", "Product C"))


def _products() -> tuple:
    raw = json.loads(map_path("nodes.json").read_text(encoding="utf-8"))
    found = tuple(
        (key, label.strip()) for key, label in raw.items()
        if isinstance(key, str) and _NODE_ID.fullmatch(key) and isinstance(label, str) and label.strip()
    ) if isinstance(raw, dict) else ()
    return found or _DEFAULT_PRODUCTS


PRODUCTS = _products()
PRODUCT_IDS = tuple(node_id for node_id, _label in PRODUCTS)
_OPEN = {"", "lab", "hq"}
NODE_IDS = (
    "owner",
    *ROLE_IDS,
    "hq",
    "vp",
    "dept-engineering",
    "dept-design",
    "dept-research",
    "dept-budget",
    "dept-repo-office",
    "dept-lab-ops",
    *PRODUCT_IDS,
    "factory-queue",
    "factory-grok",
    "factory-gemini",
    "factory-zcode",
    "github",
    "users",
)

_STATES = {"working", "blocked", "done", "needs-owner", "needs-vp", "needs-hq"}
_DECISIONS = {"needs-owner", "needs-vp", "needs-hq"}
_RESOLVE = _DECISIONS | {"blocked"}
_NY = ZoneInfo("America/New_York")
_SEP = " \u00b7 "
_TOKEN = re.compile(r"(?:sk-|ghp_|gho_|github_pat_|xai-)[A-Za-z0-9_-]{8,}")
_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})(?: (ET|EST|EDT))?$")
# Lab boards say needs-lead. Every later check still says needs-vp.
_LAB_STATE = {"needs-lead": "needs-vp"}


_LEGACY_WORD = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def _legacy(path: Path | None = None) -> tuple[dict, dict, dict, dict, dict]:
    """Read-only aliases (old board word or id -> current one) for older configs. An internal map: {} in a public build.
    A missing or bad file, a sub-map that is not an object, and any key or value that is not a short lowercase word
    (no "/", no "..") are skipped, so a bad file can never take a command down or point a path outside its folder."""
    try:
        raw = json.loads((path or map_path("legacy.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    def pick(key: str) -> dict:
        sub = raw.get(key)
        if not isinstance(sub, dict):
            return {}
        return {k: v for k, v in sub.items() if isinstance(k, str) and isinstance(v, str) and _LEGACY_WORD.match(k) and _LEGACY_WORD.match(v)}

    return pick("states"), pick("ids"), pick("pools"), pick("dirs"), pick("lanes")


LEGACY_STATE, LEGACY_ID, LEGACY_POOL, LEGACY_DIR, LEGACY_LANE = _legacy()


def inbox_dir(root: Path, name: str) -> Path:
    """<root>/.heliopause/inbox/<name>, or its older folder name when only that one exists."""
    base = Path(root) / ".heliopause" / "inbox"
    old = LEGACY_DIR.get(name)
    if old and not (base / name).exists() and (base / old).is_dir():
        # The older folder must still sit inside the inbox once links are resolved.
        if (base / old).resolve().is_relative_to(base.resolve()):
            return base / old
    return base / name


def lane_name(lane: object) -> object:
    """A lane under its current name: an older prefix ("<old>" or "<old>-x") is read as the new one."""
    if isinstance(lane, str):
        for old, new in LEGACY_LANE.items():
            if lane == old or lane.startswith(old + "-"):
                return new + lane[len(old):]
    return lane
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SUPER = re.compile(r"\b(?:replaces|supersedes)\b", re.IGNORECASE)
# WHY: a failure older than a day is history, not an alarm; withdrawn jobs were pulled on purpose.
FAILED_WINDOW_SECONDS = 24 * 3600
WITHDRAWN_SUFFIX = ".withdrawn.md"
_LAB_ID = re.compile(r"(?:ask|lab)-\d{4}")  # ask-NNNN; lab-NNNN is the older spelling
_SAFE_ID = re.compile(r"(?:ask|lab|hq)-[0-9]{1,10}")  # digits only: a word slug in an id would leak content
# Headline reject: path (~ / \ `), url (http), ticket (# + digit), a 7+ hex word,
# ask-<digits> or lab-<digits>, a filename extension, loopback, or a 4-5 digit port.
# ponytail: an English word of 7+ hex letters (defaced) is rejected too. A word list is the upgrade.
_NOT_HEADLINE = re.compile(
    r"/|~|\\|`|http|#\d|\b[0-9a-fA-F]{7,}\b|\bask-\d+|ask-\d+"
    r"|\b[\w.-]+\.(?:md|py|json|sh|html|css|js|yaml|toml)\b"
    r"|127\.0\.0\.1|:\d{4,5}(?!\d)"
)
# ponytail: one trailing non-nested group. Nested or mid-name notes need a real parser.
_TRAIL_PAREN = re.compile(r"\s*\([^()]*\)\s*$")
_ALIASES = {
    key.strip().casefold(): node_id
    for key, node_id in json.loads(
        map_path("aliases.json").read_text(encoding="utf-8")
    ).items()
}
_LAB_ROLES = ("hq", "lab", "factory")


class LabConfigError(Exception):
    """Bad ATLAS_LAB/atlas.json. atlas.__main__ turns this into exit code 2."""


def _lab_fail(path: Path, problem: str) -> None:
    raise LabConfigError(f"atlas: {path}: {problem}")


def load_lab(directory: Path) -> dict:
    """Read and check <dir>/atlas.json. Raises LabConfigError naming the problem."""
    path = Path(directory) / "atlas.json"
    if not path.is_file():
        _lab_fail(path, "missing atlas.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        _lab_fail(path, f"invalid JSON ({err})")
    if not isinstance(data, dict) or type(data.get("v")) is not int or data.get("v") != 1:
        _lab_fail(path, "needs v 1")
    owner = data.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        _lab_fail(path, "needs owner")
    owner = owner.strip()
    tz = data.get("tz")
    if not isinstance(tz, str) or not tz.strip():
        _lab_fail(path, "needs tz")
    try:
        zone = ZoneInfo(tz)
    except (KeyError, ValueError):
        _lab_fail(path, f"bad timezone {tz!r}")
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        _lab_fail(path, "needs nodes")
    seen: set[str] = set()
    clean = []
    for item in nodes:
        if not isinstance(item, dict):
            _lab_fail(path, "bad node")
        node_id, label, role = item.get("id"), item.get("label"), item.get("role")
        if not isinstance(node_id, str) or _NODE_ID.fullmatch(node_id) is None:
            _lab_fail(path, f"bad id {node_id!r}")
        if node_id == "owner":
            _lab_fail(path, "id 'owner' is reserved for you, the owner")
        if node_id in seen:
            _lab_fail(path, f"duplicate id {node_id!r}")
        seen.add(node_id)
        if role not in _LAB_ROLES:
            _lab_fail(path, f"bad role {role!r}")
        if not isinstance(label, str) or not label.strip():
            _lab_fail(path, f"bad label for {node_id!r}")
        clean.append({"id": node_id, "label": label.strip(), "role": role})
    return {"path": path, "owner": owner, "zone": zone, "nodes": clean}


# ponytail: the mode is chosen once per process via ATLAS_LAB, so tests that need it use a subprocess.
LAB = False
LAB_DIR: Path | None = None
LAB_OWNER = "Owner"
LAB_LABELS: dict[str, str] = {}
LAB_LANES: tuple = ()
# Non-lab needs-vp still waits on "vp". Lab mode points this at the first role-"lab" node, or owner.
LAB_LEAD = "vp"


def _activate_lab() -> None:
    global LAB, LAB_DIR, LAB_OWNER, LAB_LABELS, LAB_LANES, LAB_LEAD, _NY, _ALIASES
    global NODE_IDS, HQ_ROLES, ROLE_LABELS, ROLE_IDS, ROLE_KEYS, _ROLE_KEY, _NOT_LAB
    raw = os.environ.get("ATLAS_LAB")
    if not raw:
        return
    cfg = load_lab(Path(raw))
    nodes = cfg["nodes"]
    labels: dict[str, str] = {}
    for item in nodes:
        key = item["label"].casefold()
        if key in labels:
            _lab_fail(cfg["path"], f"duplicate label {item['label']!r}")
        labels[key] = item["id"]
    owner_key = cfg["owner"].casefold()
    if owner_key in labels:
        _lab_fail(cfg["path"], f"owner label {cfg['owner']!r} matches a node")
    labels[owner_key] = "owner"
    groups: dict[str, list[str]] = {"hq": [], "lab": [], "factory": []}
    for item in nodes:
        groups[item["role"]].append(item["id"])
    lanes = [("owner", "Owner", "OWNER", ("owner",))]
    for key, label, kind in (("hq", "HQ", "HQ"), ("lab", "Lab", "LAB"), ("factory", "Factories", "FACTORY")):
        if groups[key]:
            lanes.append((key, label, kind, tuple(groups[key])))
    LAB = True
    LAB_DIR = Path(raw)
    LAB_OWNER = cfg["owner"]
    LAB_LABELS = {"owner": cfg["owner"], **{item["id"]: item["label"] for item in nodes}}
    LAB_LEAD = next((item["id"] for item in nodes if item["role"] == "lab"), "owner")
    LAB_LANES = tuple(lanes)
    NODE_IDS = ("owner", *(item["id"] for item in nodes))
    HQ_ROLES = ()
    ROLE_LABELS = {}
    ROLE_IDS = ()
    ROLE_KEYS = ()
    _ROLE_KEY = {}
    _NOT_LAB = {"hq", "owner"}
    _NY = cfg["zone"]
    _ALIASES = labels


_activate_lab()


def _board_state(raw: str) -> str:
    """Map a lab board word onto the state the rest of Heliopause checks. Other modes stay as written."""
    raw = LEGACY_STATE.get(raw, raw)
    return _LAB_STATE.get(raw, raw) if LAB else raw


def board_path(root: Path) -> Path:
    """Lab mode uses <ATLAS_LAB>/board.md. Otherwise the board under root."""
    if LAB and LAB_DIR is not None:
        return LAB_DIR / "board.md"
    return Path(root) / ".heliopause" / "inbox" / "lab" / "board.md"


def _json_file(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _repos_present(data: object) -> bool:
    """A repos map with at least one name. Empty, missing, or the wrong shape is none."""
    repos = data.get("repos") if isinstance(data, dict) else None
    return isinstance(repos, dict) and any(isinstance(name, str) and name for name in repos)


def repos_config_path(package: Path) -> Path:
    """Lab atlas.json when its repos map is non-empty, else the package repos.json.

    The map is the repos.json shape: repo name to the agent that owns it. org sits beside it.
    collect cannot import github (github imports collect); both callers use this.
    """
    if LAB and LAB_DIR is not None:
        lab = Path(LAB_DIR) / "atlas.json"
        if _repos_present(_json_file(lab)):
            return lab
    return Path(package)


def configured_repos(path: Path | None = None) -> list[str]:
    """Repo names from the lab atlas.json, else atlas/repos.json. Missing or empty is none, never an error.

    An explicit path is that file alone. The default prefers the lab.
    """
    if path is None:
        path = repos_config_path(map_path("repos.json"))
    else:
        path = Path(path)
    data = _json_file(path)
    repos = data.get("repos") if isinstance(data, dict) else None
    if not isinstance(repos, dict):
        return []
    return sorted(name for name in repos if isinstance(name, str) and name)


def _board_facts(root: Path) -> dict:
    """Work lines on the board, and whether a Claude Code hook has written.

    ponytail: the solo template's one "Lab created / atlas init" line is the lab existing, not work.
    A later line, including the hook's "Session started", counts. Upgrade: a real event kind.
    """
    raw = _read(board_path(root)) or ""
    lines = 0
    hook = False
    for line in _complete(raw).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(_SEP)
        if len(parts) < 4 or _iso(parts[0]) is None:
            continue
        headline, details = _split_prose(parts)
        if headline == "Session started":
            hook = True
        if headline == "Lab created" and details == "atlas init":
            continue
        lines += 1
    return {"board_lines": lines, "hook": hook}


def redact(value: str) -> str:
    """Replace token-shaped substrings. Short prefixes (under 8 chars) stay."""
    return _TOKEN.sub("[redacted]", value)


def _redact(obj):
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [_redact(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _redact(val) for key, val in obj.items()}
    return obj


def _read(path: Path) -> str | None:
    """A file's text, or None. Never through a symlink or a hard-linked file (labfs); in lab mode no folder
    inside the lab on the way may be a link either. Agents write the lab, so a planted link there is skipped."""
    try:
        data = labfs.read_path(path, LAB_DIR)
    except (OSError, ValueError):
        return None
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _complete(raw: str) -> str:
    # WHY: a writer may still be appending the last line. Consume only through the last newline.
    if raw.endswith("\n"):
        return raw
    head, sep, _rest = raw.rpartition("\n")
    return head + sep if sep else ""


def _iso(stamp: str) -> str | None:
    # WHY: ET/EST/EDT only validate the label. The instant is _NY (New York, or the lab zone).
    # A stamp with no label is that same zone, and only while lab mode is on.
    match = _TS.match(stamp.strip())
    if not match or (match.group(3) is None and not LAB):
        return None
    try:
        local = datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return local.replace(tzinfo=_NY).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _files(directory: Path, suffix: str | None = None) -> list[Path]:
    # WHY: iterdir and is_file raise OSError if the dir is unreadable or vanishes mid-scan.
    try:
        if not directory.is_dir():
            return []
        entries = list(directory.iterdir())
    except OSError:
        return []
    found = []
    for path in entries:
        try:
            ok = path.is_file()
        except OSError:
            continue
        if ok and not path.name.startswith(".") and (suffix is None or path.suffix == suffix):
            found.append(path)
    found.sort(key=lambda path: path.name)
    return found


def _frontmatter(text: str):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields: dict[str, str] = {}
    index = 1
    closed = False
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == "---":
            closed = True
            index += 1
            break
        if stripped:
            if ":" not in lines[index]:
                return None
            key, value = lines[index].split(":", 1)
            key = key.strip()
            if not key:
                return None
            fields[key] = value.strip()
        index += 1
    if not closed:
        return None
    title = next((line[2:].strip() for line in lines[index:] if line.startswith("# ")), None)
    return fields, title


def _resolve_text(text: str) -> tuple[str, str] | None:
    target, sep, why = text.partition(":")
    if not sep:
        return None
    target = target.strip()
    if not target:
        return None
    return target, why.strip()


def _known_resolve(text: str, nodes: dict) -> tuple[str, str] | None:
    parsed = _resolve_text(text)
    if parsed is None:
        return None
    target, why = parsed
    node_id = _alias(target)
    if node_id not in nodes:
        return None
    return node_id, why


def _alias(name: str) -> str | None:
    # WHY: "Name - Role" resolves on the role, so aliases.json needs no personal names.
    # A trailing "(via Lead)" is a note, not part of the team or role.
    bare = _TRAIL_PAREN.sub("", name).strip()
    if " - " in bare:
        found = _ALIASES.get(bare.rsplit(" - ", 1)[1].strip().casefold())
        if found is not None:
            return found
    return _ALIASES.get(bare.casefold())


_WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def hq_roles(root: Path) -> dict[str, str]:
    """Persona -> role node id, from lab/hq-roles.json. Missing, bad, or any unknown id: {} (all HQ is generic)."""
    if LAB:
        return {}
    try:
        raw = json.loads(_read(Path(root) / ".heliopause" / "inbox" / "lab" / "hq-roles.json") or "")
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    found: dict[str, str] = {}
    for key, value in raw.items():
        # An empty key would make scrub's pattern match the empty string everywhere.
        if not isinstance(value, str) or not str(key).strip():
            return {}
        value = "hq-coo" if value == "hq-hr" else value  # HR sits under COO (old map files still say hq-hr)
        if value not in ROLE_LABELS:
            return {}
        found[str(key).strip().casefold()] = value
    return found


def lab_personas(root: Path) -> dict[str, str]:
    """Bare name -> node id, from lab/lab-personas.json. Missing, bad, or any HQ-role value: {} (map off)."""
    if LAB:
        return {}
    try:
        raw = json.loads(_read(Path(root) / ".heliopause" / "inbox" / "lab" / "lab-personas.json") or "")
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    found: dict[str, str] = {}
    for key, value in raw.items():
        # One bad value turns the whole map off. An empty key would match every address.
        if not isinstance(value, str) or not str(key).strip() or value not in NODE_IDS or value in _NOT_LAB:
            return {}
        found[str(key).strip().casefold()] = value
    return found


def hq_queue(roles: dict[str, str]) -> str:
    """Where HQ's queue lands: the Chief of Staff runs it. With no map, the generic box (today's behaviour)."""
    return "hq-cos" if roles else "hq"


def _role_id(token: str, roles: dict[str, str]) -> str | None:
    """Role key first, then the persona map. No generic fallback."""
    return _ROLE_KEY.get(token) or roles.get(token)


def hq_for(value: object, roles: dict[str, str]) -> list[str]:
    """Role ids named by a `for:` value (role keys, then personas). None match: the queue."""
    found: list[str] = []
    for word in _WORD.findall(str(value or "").lower()):
        role = _role_id(word, roles)
        if role and role not in found:
            found.append(role)
    return found or [hq_queue(roles)]


def hq_end(value: object, roles: dict[str, str]) -> str | None:
    """Role id for a role key, "hq/<persona>" or a bare persona. "hq/<unknown>" is the generic box; else None."""
    if not isinstance(value, str):
        return None
    name = value.strip().lower()
    bare = name[3:] if name.startswith("hq/") else name
    role = _role_id(bare, roles)
    if role:
        return role
    return "hq" if name.startswith("hq/") else None


def hq_from(value: object, roles: dict[str, str]) -> str:
    """Role id of an HQ sender: role key, "hq/<persona>", "<persona>" or "hq"."""
    name = str(value or "").strip().lower()
    if name.startswith("hq/"):
        name = name[3:]
    return _role_id(name, roles) or "hq"


def hq_party(value: object, roles: dict[str, str], personas: dict[str, str] | None = None) -> str | None:
    """Node id an address names, or None when it cannot be classified. Blank is None.

    Bare "hq" is the generic box. A role key beats the persona map. "hq/<unknown>" does not.
    "lab", "lab/<x>" and "lab-<x>" are the VP. A bare name resolves only through `personas`.
    """
    if not isinstance(value, str):
        return None
    name = _TRAIL_PAREN.sub("", value).strip()  # a "(for x, y)" note goes first: its commas are not a list
    if "," in name:
        # A list ("hq/a, hq/b") is classified only when every part is; it pulses as its first part.
        parts = [hq_party(part, roles, personas) for part in name.split(",")]
        return parts[0] if None not in parts else None
    if not name:
        return None
    folded = name.casefold()
    if folded in NODE_IDS:
        return folded
    if LEGACY_ID.get(folded) in NODE_IDS:
        return LEGACY_ID[folded]
    bare = folded[3:] if folded.startswith("hq/") else folded
    role = _role_id(bare, roles)
    if role:
        return role
    found = _alias(name)
    if found in NODE_IDS:
        return found
    # HQ talks to the Lab only through the VP. "claude/<x>" is the Lab's own inbox on the bus.
    if folded in ("lab", "claude") or folded.startswith(("lab/", "lab-", "claude/")):
        return "vp"
    # ponytail: "name-role" is the board's "Name - Role". Last hyphen only; put real slugs in aliases.json if that collides.
    if "-" in folded and not folded.startswith("hq/"):
        head, _, tail = folded.rpartition("-")
        found = _alias(f"{head} - {tail}")
        if found in NODE_IDS:
            return found
    # ponytail: fibres still fall back to vp. A persona mapped elsewhere is right on the rail only.
    mapped = (personas or {}).get(folded)
    if mapped in NODE_IDS and mapped not in _NOT_LAB:
        return mapped
    return None


def hq_visible(fields: dict, title: str, roles: dict[str, str], text: str = "", personas: dict[str, str] | None = None, filename: str = "") -> bool:
    """False when an HQ file stays off the graph. The file text and its name are both marker-scanned."""
    try:
        if not isinstance(fields, dict):
            fields = {}
        for key in _GATE:
            if key in fields and str(fields[key]).strip().casefold() not in _OPEN:
                return False
        blob = text if isinstance(text, str) and text else "\n".join((*(str(v) for v in fields.values()), title or ""))
        if _SENSITIVE.search(filename) or _SENSITIVE.search(blob):
            return False
        parties = []
        for key in ("from", "to"):
            raw = fields.get(key)
            if not isinstance(raw, str) or not raw.strip():
                continue
            # Commas inside a trailing "(for x, y)" are not a list; hq_party still pulses the first part.
            listed = _TRAIL_PAREN.sub("", raw).strip()
            parts = listed.split(",") if listed else []
            if not parts:
                return False
            for part in parts:
                party = hq_party(part, roles, personas)
                if party not in NODE_IDS:
                    return False
                parties.append(party)
        # A present `for` is another party. Resolved like hq_for (role key, then the persona map).
        # ponytail: an unknown word fails closed only when owner is already a party. Drop that guard if every unknown `for` should hide the file; today a queue fallback ("nobody known") stays visible.
        raw_for = fields.get("for")
        if isinstance(raw_for, str) and raw_for.strip() and "owner" in parties:
            words = _WORD.findall(raw_for.lower())
            if not words or any(_role_id(word, roles) not in NODE_IDS for word in words):
                return False
            parties.extend(hq_for(raw_for, roles))
        if not parties:
            return False
        others = [party for party in parties if party != "owner"]
        return "owner" not in parties or (bool(others) and all(party in ("hq-cos", "hq-ceo") for party in others))
    except Exception:
        # ponytail: one blanket catch. Name the error types if a real bug starts hiding in here.
        return False


def scrub(text: str | None, roles: dict[str, str]) -> str | None:
    """Replace persona words with their role label, so no output carries a persona name."""
    if not roles or not text:
        return text
    names = "|".join(re.escape(name) for name in sorted(roles, key=len, reverse=True))
    return re.sub(
        rf"(?<![\w-])(?:{names})(?![\w-])",
        lambda found: ROLE_LABELS[roles[found.group(0).casefold()]],
        text,
        flags=re.IGNORECASE,
    )


def _created_ts(created: str | None) -> str | None:
    """Normalise an HQ created stamp. Date-only is midnight America/New_York."""
    if not isinstance(created, str):
        return None
    stamp = created.strip()
    if _DATE.match(stamp):
        try:
            local = datetime.strptime(stamp, "%Y-%m-%d")
        except ValueError:
            return None
        return local.replace(tzinfo=_NY).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _iso(stamp)


def _blank_nodes() -> dict:
    return {
        node_id: {
            "id": node_id,
            "status": "idle",
            "ts": None,
            "headline": None,
            "details": None,
            "name": None,
        }
        for node_id in NODE_IDS
    }


def _is_headline(value: str) -> bool:
    """True when the field is short prose. Longer prose stays whole in details."""
    text = value.strip()
    if not 1 <= len(text) <= 60 or " " not in text[:60]:
        return False
    return _NOT_HEADLINE.search(text) is None


def _split_prose(parts: list[str]) -> tuple[str | None, str]:
    if len(parts) >= 5 and _is_headline(parts[3]):
        return parts[3].strip(), _SEP.join(parts[4:]).strip()
    return None, _SEP.join(parts[3:]).strip()


def _parse_board(root: Path, nodes: dict, other: dict) -> None:
    raw = _read(board_path(root))
    if raw is None:
        return
    resolutions = []
    for line in _complete(raw).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(_SEP)
        if len(parts) < 4:
            continue
        instant = _iso(parts[0])
        name = parts[1].strip()
        if instant is None or not name or name.startswith("<"):
            continue
        state = _board_state(parts[2].strip())
        headline, details = _split_prose(parts)
        if state == "resolved":
            # WHY: "<Name>: why" can land in the headline slot, so details alone misses it.
            found = _known_resolve(details, nodes)
            directive = details
            if found is None:
                directive = parts[3].strip()
                found = _known_resolve(directive, nodes)
            if found is None:
                directive = _SEP.join(parts[3:]).strip()
                found = _known_resolve(directive, nodes)
            if found is None:
                continue
            node_id, why = found
            # A resolved headline sticks only when it is real prose and not the directive.
            if headline == directive:
                headline = None
            resolutions.append((instant, node_id, why, headline))
            continue
        status = state if state in _STATES else "unknown"
        node_id = _alias(name)
        if node_id in nodes:
            current = nodes[node_id]
            if current["ts"] is None or instant >= current["ts"]:
                current.update(
                    status=status, ts=instant, headline=headline, details=details, name=name
                )
            continue
        previous = other.get(name.casefold())
        if previous is None or instant >= previous["ts"]:
            other[name.casefold()] = {
                "name": name,
                "status": status,
                "ts": instant,
                "headline": headline,
                "details": details,
            }
    # Own lines are folded first, so a later line from the node still wins on timestamp.
    owned = {node_id: (node["status"], node["ts"]) for node_id, node in nodes.items()}
    for instant, node_id, why, headline in resolutions:
        status, ts = owned[node_id]
        current = nodes[node_id]
        if status not in _RESOLVE or ts is None or ts > instant:
            continue
        if current["ts"] is not None and instant < current["ts"]:
            continue
        current["status"] = "done"
        current["ts"] = instant
        current["headline"] = headline
        current["details"] = "Resolved: " + why


def _ask_id(fields: dict | None, path: Path) -> str:
    if fields:
        raw = fields.get("id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return path.stem


def _reply_closes(fields: dict | None) -> bool:
    if not fields:
        return False
    reply = fields.get("reply")
    # WHY: the word "none" ("none needed"), not any reply that starts with those letters ("nonetheless").
    return isinstance(reply, str) and re.match(r"none\b", reply.strip(), re.IGNORECASE) is not None


def _listed_ids(value: str) -> list[str]:
    return [part for part in re.split(r"[\s,]+", value.strip()) if part]


def _title_ids(title: str | None) -> list[str]:
    if not title:
        return []
    found = _SUPER.search(title)
    if not found:
        return []
    return _LAB_ID.findall(title[found.end():])


def _private_ask(fields, path: Path) -> dict:
    # ponytail: blanket catches, each failing to "no value". Name the error types if a real bug hides here.
    try:
        raw = _ask_id(fields, path) if fields else path.stem
        ask_id = raw if isinstance(raw, str) and _SAFE_ID.fullmatch(raw) else None
    except Exception:
        ask_id = None
    try:
        ts = _created_ts(fields.get("created")) if fields else None
    except Exception:
        ts = None
    label = "private HQ ask" if ask_id is None else "private HQ ask" + _SEP + ask_id
    return {"kind": "hq", "private": True, "id": ask_id, "from": None, "to": None, "for": [],
            "type": None, "priority": None, "title": label, "created": None, "ts": ts}


def _hq(root: Path) -> tuple[dict, list]:
    if LAB:
        return {"inbox": 0, "open": 0, "done": 0}, []
    base = root / ".heliopause" / "inbox" / "hq"
    roles = hq_roles(root)
    personas = lab_personas(root)
    inbox = _files(base / "inbox", ".md")
    done = _files(base / "done", ".md")
    # WHY: HQ leaves the ask in inbox and closes it by writing done/<same file name>.md.
    # Done files have no frontmatter, so the match is the file name only.
    done_names = {path.name for path in done}
    rows = []
    for path in inbox:
        text = _read(path) or ""
        parsed = _frontmatter(text)
        if not parsed:
            rows.append((path, None, None, path.stem, ""))
            continue
        fields, title = parsed
        rows.append((path, fields, title, _ask_id(fields, path), text))
    superseded: set[str] = set()
    for _path, fields, title, ask_id, _text in rows:
        if not fields:
            continue
        for item in _listed_ids(fields.get("supersedes", "")):
            if item != ask_id:
                superseded.add(item)
        for item in _title_ids(title):
            if item != ask_id:
                superseded.add(item)
    open_rows = []
    for path, fields, title, ask_id, text in rows:
        if path.name in done_names or _reply_closes(fields) or ask_id in superseded:
            continue
        open_rows.append((path, fields, title, text))
    items = []
    for path, fields, title, text in open_rows:
        if not fields or not hq_visible(fields, title or "", roles, text, personas, path.name):
            items.append(_private_ask(fields, path))
            continue
        # A present end is a node id. Anything else stays null, never the raw address.
        ends = {}
        for key in ("from", "to"):
            party = hq_party(fields.get(key), roles, personas)
            ends[key] = party if party in NODE_IDS else None
        items.append(
            {
                "kind": "hq",
                "id": fields.get("id"),
                "from": ends["from"],
                "to": ends["to"],
                "for": hq_for(fields.get("for"), roles),
                "type": fields.get("type"),
                "priority": fields.get("priority"),
                "title": scrub(title, roles),
                "created": fields.get("created"),
                "ts": _created_ts(fields.get("created")),
            }
        )
    return {"inbox": len(inbox), "open": len(open_rows), "done": len(done)}, items


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _queue(root: Path, nodes: dict) -> dict:
    if LAB:
        return {"inbox": 0, "running": 0, "done": 0, "failed": 0, "active": [], "failed_items": []}
    base = inbox_dir(root, "queue")
    counts = {state: len(_files(base / state)) for state in ("inbox", "running", "done")}
    active = []
    for state in ("inbox", "running"):
        for path in _files(base / state, ".md"):
            parsed = _frontmatter(_read(path) or "")
            if not parsed:
                continue
            fields, _title = parsed
            active.append(
                {
                    "id": fields.get("id"),
                    "state": state,
                    "from": fields.get("from"),
                    "to": fields.get("to"),
                    "model": fields.get("model"),
                }
            )
    failed_items = []
    seen_ids = set()
    now = time.time()
    # Oldest first, so the last item is the newest failure (the page draws the broken wire to its owner).
    for path in sorted(_files(base / "failed", ".md"), key=_mtime):
        if path.name.endswith(WITHDRAWN_SUFFIX):
            continue
        try:
            mtime = path.stat().st_mtime
            if now - mtime > FAILED_WINDOW_SECONDS:
                continue
        except OSError:
            continue
        # A failed job with no frontmatter still failed: count it under its file stem.
        fields, title = _frontmatter(_read(path) or "") or ({}, None)
        job_id = _ask_id(fields, path)
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)
        model = fields.get("model")
        # WHY: a failure belongs to the job's owner. Only a known node id leaves here, never a raw name.
        owner = _alias(str(fields.get("from") or ""))
        failed_items.append(
            {
                "id": job_id,
                "title": title,
                "model": model if model else None,
                "owner": owner if owner in nodes else "vp",
                "ts": datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    counts["failed"] = len(failed_items)
    nodes["factory-queue"]["status"] = "working" if counts["inbox"] or counts["running"] else "idle"
    return {**counts, "active": active, "failed_items": failed_items}


def _run_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif len(text) >= 5 and text[-5] in "+-" and text[-4:].isdigit():
        text = text[:-2] + ":" + text[-2:]
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return stamp


def _today():
    """The America/New_York date. Tests freeze it, so 'runs today' doesn't depend on the real clock."""
    return datetime.now(_NY).date()


def _cap(root: Path) -> int | None:
    # Budget's contract: anything but a positive int means no cap is published.
    try:
        value = json.loads(_read(root / ".heliopause" / "inbox" / "lab" / "usage" / "caps.json") or "")["grok_build_daily_tokens"]
    except (ValueError, KeyError, TypeError):
        return None
    return value if type(value) is int and value > 0 else None


def _grok(root: Path) -> dict:
    # WHY: cwd is a path. The card only publishes the run counts.
    if LAB:
        return {}
    raw = _read(root / ".heliopause" / "inbox" / "lab" / "usage" / "grok-build.jsonl")
    if raw is None:
        return {}
    runs = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        stamp = _run_ts(item.get("ts"))
        turns = item.get("turns")
        usage = item.get("usage")
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        if stamp is None or type(turns) is not int or type(tokens) is not int:
            continue
        runs.append((stamp, turns, tokens))
    if not runs:
        return {}
    stamp, turns, tokens = max(runs, key=lambda run: run[0])
    day = _today()
    todays = [tokens for when, _turns, tokens in runs if when.astimezone(_NY).date() == day]
    return {
        "last_run": stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "turns": turns,
        "tokens": tokens,
        "runs_today": len(todays),
        "tokens_today": sum(todays),
        "cap": _cap(root),
    }


def _apply_grok(nodes: dict, grok: dict) -> None:
    if not grok:
        return
    node = nodes["factory-grok"]
    last = grok["last_run"]
    # A board line strictly newer than the last run stays. Otherwise the card is the run.
    if node["ts"] is not None and node["ts"] > last:
        return
    node["status"] = "idle"
    node["ts"] = last
    node["headline"] = None
    node["details"] = (
        f"Last run: {grok['turns']} turns, {grok['tokens'] / 1_000_000:.1f}M tokens"
        f" \u00b7 {grok['runs_today']} runs today"
    )


# ponytail: fixed slot counts per lane. Read them from a config when routes.json carries them.
LANES = {
    "factory-gemini": {
        "lane": "gemini",
        "slots": 3,
        "pools": ("Gemini 3.8 week", "Gemini 5 h"),
    },
    "factory-zcode": {
        "lane": "zcode",
        "slots": 5,
        "pools": ("GLM week", "GLM 5 h"),
    },
}


def _busy(lane: str, root: Path) -> int:
    if lane == "gemini":
        try:
            res = subprocess.run(["pgrep", "-x", "agy"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout:
                return sum(1 for line in res.stdout.splitlines() if line.strip().isdigit())
            return 0
        except (OSError, subprocess.SubprocessError):
            return 0
    if lane == "zcode":
        # ponytail: no per-lane marker in the shared GLM slots; add one if Z Code goes live
        return 0
    return 0


def _jobs_today(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    raw = _read(Path(root) / ".heliopause" / "inbox" / "lab" / "usage" / "dispatch.jsonl")
    if not raw:
        return counts
    day = _today()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        lane = item.get("lane")
        if not isinstance(lane, str):
            continue
        ts_utc = item.get("ts_utc")
        stamp = _run_ts(ts_utc)
        if stamp is None:
            continue
        if stamp.astimezone(_NY).date() == day:
            counts[lane] = counts.get(lane, 0) + 1
    return counts


def _usage_pools(root: Path) -> list[dict]:
    raw = _read(Path(root) / ".heliopause" / "inbox" / "lab" / "strip" / "usage.json")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    pools = data.get("pools")
    return pools if isinstance(pools, list) else []


def _lane_pool(pools_data: list[dict], pool_names: tuple[str, ...]) -> tuple[dict | None, bool]:
    candidates = []
    for entry in pools_data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name not in pool_names:
            continue
        used = entry.get("used")
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            continue
        left = 1.0 - used
        candidates.append((left, entry))
    if not candidates:
        return None, False
    best_left, best_entry = min(candidates, key=lambda c: c[0])
    clamped_left = round(max(0.0, min(1.0, float(best_left))), 2)
    at_cap = any(
        left <= 0 or round(max(0.0, min(1.0, float(left))), 2) <= 0 or entry.get("status") == "out"
        for left, entry in candidates
    )
    pool_result = {
        "name": best_entry.get("name"),
        "left": clamped_left,
        "resets": best_entry.get("resets"),
    }
    return pool_result, at_cap


def _lanes_config(root: Path) -> dict:
    raw = _read(Path(root) / ".heliopause" / "inbox" / "lab" / "usage" / "lanes.json")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    lanes = data.get("lanes")
    return lanes if isinstance(lanes, dict) else {}


def _lane_paused(lanes: dict, lane: str) -> str | None:
    entry = lanes.get(lane) if isinstance(lanes, dict) else None
    if not isinstance(entry, dict):
        return None
    if entry.get("state") == "paused":
        note = entry.get("note")
        return str(note) if isinstance(note, str) and note.strip() else "paused"
    return None


def _lanes(root: Path) -> dict:
    if LAB:
        return {}
    root = Path(root)
    jobs = _jobs_today(root)
    pools_data = _usage_pools(root)
    lanes_data = _lanes_config(root)
    result = {}
    for node_id, spec in LANES.items():
        lane = spec["lane"]
        busy = _busy(lane, root)
        slots = spec["slots"]
        jobs_today = jobs.get(lane, 0)
        pool, at_cap = _lane_pool(pools_data, spec["pools"])
        result[node_id] = {
            "busy": busy,
            "slots": slots,
            "jobs_today": jobs_today,
            "pool": pool,
            "paused": _lane_paused(lanes_data, lane),
            "at_cap": at_cap,
        }
    return result


def snapshot(root: Path) -> dict:
    """Build one normalised snapshot. Missing directories are empty, never an error."""
    root = Path(root)
    nodes = _blank_nodes()
    other: dict = {}
    _parse_board(root, nodes, other)
    grok_bus = _grok(root)
    _apply_grok(nodes, grok_bus)
    hq_bus, hq_items = _hq(root)
    queue_bus = _queue(root, nodes)
    lanes_bus = _lanes(root)
    decisions = [
        {
            "kind": "board",
            "node": node["id"],
            "status": node["status"],
            "ts": node["ts"],
            "headline": node["headline"],
            "details": node["details"],
        }
        for node in nodes.values()
        if node["status"] in _DECISIONS
    ]
    decisions.extend(
        {
            "kind": "board",
            "node": None,
            "name": entry["name"],
            "status": entry["status"],
            "ts": entry["ts"],
            "headline": entry["headline"],
            "details": entry["details"],
        }
        for entry in other.values()
        if entry["status"] in _DECISIONS
    )
    decisions.extend(hq_items)
    # ponytail: lexical order of zero-padded UTC stamps. Null ts sorts last. Parse if a source drops the padding.
    decisions.sort(key=lambda item: (item.get("ts") is None, item.get("ts") or ""))
    facts = _board_facts(root)
    return _redact(
        {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "nodes": nodes,
            "hq_queue": hq_queue(hq_roles(root)),
            "other": list(other.values()),
            "decisions": decisions,
            "bus": {"queue": queue_bus, "hq": hq_bus, "grok": grok_bus, "lanes": lanes_bus},
            "lead": LAB_LEAD,
            "board_lines": facts["board_lines"],
            "hook": facts["hook"],
            "repos": configured_repos(),
        }
    )

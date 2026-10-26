"""Read-only setup catalogue: `atlas tools`, `atlas export`, and `atlas diff`.

The lock is built from an allow-list. Reference-only catalogue items are never
installable. This module never installs anything.

ponytail: a skill counts as installed only when its directory sits directly under
a fixed list of skill roots; nested plugin caches show as missing.
ponytail: a command version is the first N.N or N.N.N token from --version.
ponytail: plugin lines are `name@version`, `name version`, or JSON name+version.
A host list command that hangs is skipped after 5s, and that host is absent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from atlas import labfs

_HEADER = (
    "# atlas.lock\n"
    "# Allow-list export: catalogue tools and versions, plugin names and versions,\n"
    "# skill and agent names, MCP server names, listed repo checkouts, lab settings.\n"
)
_EDITIONS = {"work-ok", "personal-only", "reference-only"}
_UPDATES = {"off"}
_PROFILES = {"work", "personal"}
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}\Z")
_VER = re.compile(r"v?\d+\.\d+(?:\.\d+)?\Z")
_VERSION_B = re.compile(br"v?\d+\.\d+(?:\.\d+)?")
_LEAD = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .'_-]{0,40}\Z")
_REMOTE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?\Z")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}\Z")
_FOLDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_PLUGIN_LINE = re.compile(
    r"([A-Za-z][A-Za-z0-9._-]{0,63})(?:@|[ \t]+)(v?\d+\.\d+(?:\.\d+)?)\Z"
)
# A key-shaped token must never be copied, even if it sits in a name field.
_SECRETISH = re.compile(
    r"(?<![A-Za-z0-9])(?:sk-|ghp_|github_pat_|xox[baprs]-|AKIA)|-----BEGIN"
)
_HOME_MARKS = ("/Users/", "/home/", "~/")
_CHILD_KEYS = ("PATH", "HOME", "LANG", "USER", "LOGNAME", "TMPDIR")
_SKILL_ROOTS = (
    ".claude/skills",
    ".agents/skills",
    ".cursor/skills",
    ".grok/skills",
    ".codex/skills",
)
_AGENT_ROOTS = (
    ".claude/agents",
    ".cursor/agents",
    ".grok/agents",
    ".codex/agents",
)
_MCP_FILES = (
    ".claude.json",
    ".claude/settings.json",
    ".claude/mcp.json",
    ".cursor/mcp.json",
    ".grok/settings.json",
    ".grok/mcp.json",
    ".codex/mcp.json",
    ".codex/config.json",
)
_PLUGIN_CMDS = (
    ("claude", ("claude", "plugin", "list")),
    ("cursor", ("cursor-agent", "plugin", "list")),
    ("grok", ("grok", "plugin", "list")),
)


def _catalogue() -> list:
    path = Path(__file__).with_name("catalogue.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("bad catalogue")
    return data


def _keep(value: object, pattern: re.Pattern) -> str | None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        return None
    if _SECRETISH.search(value) or any(mark in value for mark in _HOME_MARKS):
        return None
    return value


def _child_env() -> dict:
    # Allow-list: a host CLI must not be handed key-bearing variables it could echo.
    env = {key: os.environ[key] for key in _CHILD_KEYS if key in os.environ}
    env.setdefault("PATH", "")
    env.setdefault("LANG", "C")
    return env


def _run(argv: tuple) -> str:
    binary = shutil.which(argv[0])
    if not binary:
        return ""
    try:
        proc = subprocess.run(
            [binary, *argv[1:]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            env=_child_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout[:65536].decode("utf-8", "replace")


def _command_version(name: str) -> str | None:
    """None when the binary is absent. Empty string when it is present and the version is unknown."""
    binary = shutil.which(name)
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            env=_child_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    match = _VERSION_B.search(proc.stdout[:4096])
    return match.group(0).decode("ascii") if match else ""


def _version_file(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > 32:
            return ""
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""
    return text if _VER.fullmatch(text) else ""


def _skill_version(home: Path, name: str) -> str | None:
    found = False
    version = ""
    for root in _SKILL_ROOTS:
        directory = home / root / name
        if not directory.is_dir() or not (directory / "SKILL.md").is_file():
            continue
        found = True
        text = _version_file(directory / "version")
        if text:
            version = text
            break
    if not found:
        return None
    return version


def _visible(items: list, edition: str) -> list:
    allow = {"work-ok"} if edition == "work" else _EDITIONS
    return [item for item in items if item.get("edition") in allow]


def _status(item: dict, home: Path) -> dict:
    row = {
        "edition": item.get("edition"),
        "name": item.get("name"),
        "status": "missing",
        "version": None,
    }
    if item.get("edition") == "reference-only" or item.get("install") == "never" or not item.get("probe"):
        row["status"] = "reference-only"
        return row
    probe = item["probe"]
    if not isinstance(probe, dict):
        row["status"] = "reference-only"
        return row
    kind, target = probe.get("kind"), probe.get("name")
    if kind == "command" and isinstance(target, str):
        found = _command_version(target)
    elif kind == "skill" and isinstance(target, str):
        found = _skill_version(home, target)
    else:
        row["status"] = "reference-only"
        return row
    if found is None:
        return row
    row["status"] = "installed"
    row["version"] = found or None
    return row


def _rows(edition: str, home: Path) -> list:
    rows = [_status(item, home) for item in _visible(_catalogue(), edition)]
    rows.sort(key=lambda row: str(row["name"]).casefold())
    return rows


def _names_under(home: Path, roots: tuple, kind: str) -> list:
    names = set()
    for root in roots:
        base = home / root
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if kind == "skill":
                if not child.is_dir() or not (child / "SKILL.md").is_file():
                    continue
                kept = _keep(child.name, _NAME)
            else:
                if not child.is_file() or child.suffix != ".md":
                    continue
                kept = _keep(child.stem, _NAME)
            if kept:
                names.add(kept)
    return sorted(names, key=str.casefold)


def _mcp_walk(node: object, names: set) -> None:
    if isinstance(node, dict):
        servers = node.get("mcpServers")
        if isinstance(servers, dict):
            for key in servers:
                kept = _keep(key, _NAME)
                if kept:
                    names.add(kept)
        for key, value in node.items():
            if key == "mcpServers":
                continue
            _mcp_walk(value, names)
    elif isinstance(node, list):
        for item in node:
            _mcp_walk(item, names)


def _mcp(home: Path) -> list:
    names: set = set()
    for rel in _MCP_FILES:
        path = home / rel
        try:
            if not path.is_file() or path.stat().st_size > 262144:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        _mcp_walk(data, names)
    return sorted(names, key=str.casefold)


def _plugin_json(host: str, data: object) -> list:
    if isinstance(data, dict):
        for key in ("plugins", "items"):
            if key in data:
                data = data[key]
                break
        else:
            data = [data] if "name" in data else []
    if not isinstance(data, list):
        return []
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = _keep(item.get("name"), _NAME)
        version = _keep(item.get("version"), _VER)
        if name and version:
            rows.append({"host": host, "name": name, "version": version})
    return rows


def _plugins() -> list:
    rows = []
    for host, argv in _PLUGIN_CMDS:
        text = _run(argv).strip()
        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                rows.extend(_plugin_json(host, parsed))
                continue
        for line in text.splitlines():
            match = _PLUGIN_LINE.fullmatch(line.strip())
            if match is None:
                continue
            name = _keep(match.group(1), _NAME)
            version = _keep(match.group(2), _VER)
            if name and version:
                rows.append({"host": host, "name": name, "version": version})
    unique = {(row["host"], row["name"], row["version"]): row for row in rows}
    return [unique[key] for key in sorted(unique)]


def _lead(nodes: list) -> str | None:
    label = next((node["label"] for node in nodes if node["id"] == "lead"), None)
    if label is None:
        label = next((node["label"] for node in nodes if node["role"] == "lab"), None)
    return _keep(label, _LEAD)


def _repos(raw: dict) -> list:
    items = raw.get("repos")
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        remote = _keep(item.get("remote"), _REMOTE)
        branch = _keep(item.get("branch"), _BRANCH)
        folder = _keep(item.get("folder"), _FOLDER)
        if not remote or not branch or not folder or ".." in branch:
            continue
        out.append({"branch": branch, "folder": folder, "remote": remote})
    out.sort(key=lambda row: (row["remote"], row["folder"], row["branch"]))
    return out


def _choice(raw: dict, keys: tuple, allowed: set) -> str | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value in allowed:
            return value
    return None


def _payload(home: Path, lab: Path) -> dict:
    from atlas.collect import load_lab

    loaded = load_lab(lab)
    raw = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("bad atlas.json")
    profile = _choice(raw, ("profile",), _PROFILES) or "work"
    atlas = {"profile": profile}
    update = _choice(raw, ("update", "update_mode"), _UPDATES)
    if update:
        atlas["update"] = update
    lead = _lead(loaded["nodes"])
    if lead:
        atlas["lead"] = lead
    tools = []
    for row in _rows(profile, home):
        if row["status"] == "reference-only":
            continue
        tools.append({"name": row["name"], "version": row["version"]})
    return {
        "agents": _names_under(home, _AGENT_ROOTS, "agent"),
        "atlas": atlas,
        "mcp": _mcp(home),
        "plugins": _plugins(),
        "repos": _repos(raw),
        "skills": _names_under(home, _SKILL_ROOTS, "skill"),
        "tools": tools,
    }


def _render(home: Path, lab: Path) -> str:
    text = _HEADER + json.dumps(_payload(home, lab), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if _SECRETISH.search(text) or any(mark in text for mark in _HOME_MARKS):
        raise ValueError("refusing to write a lock that holds a key or a home path")
    return text


def _tool_map(rows: object) -> dict:
    found: dict = {}
    if not isinstance(rows, list):
        return found
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _keep(row.get("name"), _NAME)
        if not name:
            continue
        version = row.get("version")
        found[name] = _keep(version, _VER) if isinstance(version, str) else None
    return found


def compare_tools(live_rows: object, lock_rows: object) -> dict:
    """Tools only. A version of None means not installed. ponytail: plugins and repos are out of this diff."""
    live, lock = _tool_map(live_rows), _tool_map(lock_rows)
    missing, extra, drift = [], [], []
    for name in sorted(set(live) | set(lock), key=str.casefold):
        here, there = live.get(name), lock.get(name)
        if there and not here:
            missing.append(name)
        elif here and not there:
            extra.append(name)
        elif here and there and here != there:
            drift.append({"here": here, "lock": there, "name": name})
    return {"drift": drift, "extra": extra, "missing": missing}


def read_lock(path: Path) -> dict:
    # ponytail: one read, capped at 1 MiB. A bigger file is not an atlas.lock.
    if path.stat().st_size > 1048576:
        raise ValueError("bad lock")
    text = path.read_text(encoding="utf-8")
    start = text.find("{")
    if start < 0:
        raise ValueError("bad lock")
    data = json.loads(text[start:])
    if not isinstance(data, dict):
        raise ValueError("bad lock")
    return data


def _lab_path(lab: Path | None) -> Path:
    if lab is not None:
        return Path(lab).expanduser()
    raw = os.environ.get("ATLAS_LAB")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / "atlas-lab"


def page_lock(root: Path) -> Path | None:
    path = Path(root) / ".atlas" / "atlas.lock"
    return path if path.is_file() else None


def _default_lock() -> Path:
    cwd_lock = Path("atlas.lock")
    if cwd_lock.is_file():
        return cwd_lock
    return Path.home() / ".atlas" / "atlas.lock"


_LIVE_TTL_S = 600
_LIVE_WAIT_S = 20
_live_guard = threading.Lock()
_live_cache: dict = {}


def _probe_tools(home: Path, lab_path: Path) -> list | None:
    box: dict = {}

    def work() -> None:
        try:
            found = _payload(home, lab_path).get("tools")
        except Exception:
            box["tools"] = None
            return
        box["tools"] = found if isinstance(found, list) else []

    # ponytail: a probe still running after 20s is abandoned and its result is dropped. Upgrade: publish that result when the CLIs return.
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(_LIVE_WAIT_S)
    if worker.is_alive():
        return None
    return box.get("tools")


def _live_tools(root: Path, lab: Path | None = None) -> list | None:
    """Tool rows from one live export. None means the probe failed or did not finish."""
    home = Path(root)
    lab_path = _lab_path(lab)
    key = (str(home), str(lab_path))
    now = time.monotonic()
    with _live_guard:
        hit = _live_cache.get(key)
        if hit is not None and now - hit[0] < _LIVE_TTL_S:
            return hit[1]
        tools = _probe_tools(home, lab_path)
        # A miss is cached too, so a hung or broken host is not probed on every request.
        _live_cache[key] = (time.monotonic(), tools)
        return tools


def setup_status(root: Path, lab: Path | None = None) -> dict:
    empty = {"drift": [], "extra": [], "lock_age": None, "missing": []}
    path = page_lock(root)
    if path is None:
        return empty
    try:
        locked = read_lock(path)
        age = max(0, int(time.time() - path.stat().st_mtime))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return empty
    live = _live_tools(root, lab)
    if live is None:
        return {"drift": [], "extra": [], "lock_age": age, "missing": []}
    report = compare_tools(live, locked.get("tools"))
    report["lock_age"] = age
    return report


def dry_run_text(root: Path, lab: Path | None = None) -> str:
    """S3's install pass is not built. This lists the missing tools and runs nothing."""
    status = setup_status(root, lab)
    known = {item.get("name"): item for item in _catalogue() if isinstance(item, dict)}
    lines = ["dry run"]
    for name in status["missing"]:
        item = known.get(name) or {}
        install = item.get("install") if isinstance(item.get("install"), str) else ""
        sha = item.get("sha") if isinstance(item.get("sha"), str) else ""
        lines.append(("{0}  {1}  {2}".format(name, install, sha)).rstrip())
    lines.append("nothing installed")
    return "\n".join(lines) + "\n"


def _format_diff(report: dict) -> str:
    lines = []
    if report["missing"]:
        lines.append("missing here:")
        lines.extend(report["missing"])
    if report["extra"]:
        lines.append("extra here:")
        lines.extend(report["extra"])
    if report["drift"]:
        lines.append("version drift:")
        for row in report["drift"]:
            lines.append("{0}  {1} -> {2}".format(row["name"], row["lock"], row["here"]))
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def diff(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas diff")
    parser.add_argument("lock", nargs="?")
    parser.add_argument("--lab", default=os.environ.get("ATLAS_LAB", "~/atlas-lab"))
    args = parser.parse_args(argv)
    path = Path(args.lock).expanduser() if args.lock else _default_lock()
    if not path.is_file():
        print("atlas diff: no lock", file=sys.stderr)
        return 2
    try:
        locked = read_lock(path)
        live = _payload(Path.home(), Path(args.lab).expanduser())["tools"]
    except Exception as err:
        if type(err).__name__ != "LabConfigError":
            print("atlas diff: {0}".format(err), file=sys.stderr)
            return 2
        print(err, file=sys.stderr)
        return 2
    text = _format_diff(compare_tools(live, locked.get("tools")))
    if not text:
        return 0
    print(text, end="")
    return 1


def tools(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas tools")
    parser.add_argument("--edition", choices=tuple(sorted(_PROFILES)), default="work")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        rows = _rows(args.edition, Path.home())
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as err:
        print(f"atlas tools: catalogue unreadable ({err})", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rows, ensure_ascii=True, sort_keys=True))
        return 0
    for row in rows:
        if row["status"] == "installed" and row["version"]:
            print(f"{row['name']}  installed  {row['version']}")
        else:
            print(f"{row['name']}  {row['status']}")
    return 0


def _write_lock(dest: Path, lab: Path, data: bytes) -> None:
    """Write a lock. Inside the lab, every folder below the lab is checked. Otherwise the parent is the root: the user named that file."""
    try:
        rel = labfs.rel_to(lab, dest)
    except labfs.Refused:
        rel = ""
    if rel and rel != ".":
        labfs.write(lab, rel, data)
        return
    labfs.write(dest.parent, dest.name, data)


def export(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas export")
    parser.add_argument("--out", default="atlas.lock")
    parser.add_argument("--lab", default=os.environ.get("ATLAS_LAB", "~/atlas-lab"))
    args = parser.parse_args(argv)
    dest = Path(args.out).expanduser()
    lab = Path(args.lab).expanduser()
    if not dest.parent.is_dir():
        print(f"atlas export: cannot write {dest}", file=sys.stderr)
        return 1
    try:
        text = _render(Path.home(), lab)
    except Exception as err:
        if type(err).__name__ != "LabConfigError":
            print(f"atlas export: {err}", file=sys.stderr)
            return 1
        print(err, file=sys.stderr)
        return 2
    try:
        _write_lock(dest, lab, text.encode("utf-8"))
    except OSError as err:
        print(f"atlas export: cannot write {dest}: {err.strerror}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("tools", "export", "diff"):
        print("atlas: expected tools, export or diff", file=sys.stderr)
        return 2
    if args[0] == "tools":
        return tools(args[1:])
    if args[0] == "export":
        return export(args[1:])
    return diff(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())

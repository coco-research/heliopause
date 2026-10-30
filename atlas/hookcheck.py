"""Installed-hook metadata. Local files only: no command line, URL, env, or payload."""
from __future__ import annotations

import json
import os
import re
import shlex
import stat
from pathlib import Path
from urllib.parse import urlsplit

_MAX = 1024 * 1024
_SOURCES = (
    ("claude-code", ".claude/settings.json", False),
    ("claude-code", ".atlas/marketplace/atlas/hooks/hooks.json", True),
    ("claude-code", "Heliopause/marketplace/heliopause/hooks/hooks.json", True),
    ("codex", ".codex/hooks.json", False),
    ("cursor", ".cursor/hooks.json", False),
)
_MARK = (" -m atlas connector ", " -m heliopause connector ")
_NAME, _EVENT, _HOST, _V6, _EQ = map(re.compile, (
    r"^[A-Za-z0-9._+-]{1,60}$", r"^[A-Za-z][A-Za-z0-9_]{0,63}$", r"^[A-Za-z0-9.-]{1,253}$",
    r"^[0-9A-Fa-f:]{2,39}$", r"^[A-Za-z_][A-Za-z0-9_]*=",
))
_KINDS = {"command", "http", "mcp_tool", "prompt", "agent"}

def list_hooks(home: Path, lab: Path | None = None) -> list[dict]:
    try:
        rows = [row for tool, rel, ours in _SOURCES for row in _one(home / rel, home, tool, ours)]
        rows.extend(_grok(home))
        # Cursor CLI reads <lab>/.cursor/hooks.json, not the home file. Skip when lab is home:
        # that path is already the ".cursor/hooks.json" source above.
        if lab is not None and not _same_dir(Path(lab), home):
            rows.extend(_one(Path(lab) / ".cursor" / "hooks.json", home, "cursor", False))
        rows.sort(key=lambda row: (row["tool"], row["file"], row["event"] or "", row["name"] or ""))
        return rows
    except Exception:
        return []

def run(_argv: list[str] | None = None) -> int:
    for row in list_hooks(Path.home()):
        if row.get("problem"):
            print(f"{row['tool']}  {row['file']}  couldn't read")
        else:
            flag = "ours" if row["ours"] else "not ours"
            print(f"{row['tool']}  {row['file']}  {row['event']}  {row['name']}  {flag}")
    return 0

def _bad(tool: str, file: str) -> dict:
    return {"tool": tool, "file": file, "event": None, "name": None, "ours": False, "problem": "couldn't read"}

def _shown(path: Path, home: Path) -> str:
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return os.path.abspath(path)

def _same_dir(left: Path, right: Path) -> bool:
    if os.path.normpath(left) == os.path.normpath(right):
        return True
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False

def _one(path: Path, home: Path, tool: str, file_ours: bool) -> list[dict]:
    shown = None
    try:
        shown = _shown(path, home)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return []
        except OSError:
            return [_bad(tool, shown)]
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > _MAX:
            return [_bad(tool, shown)]
        data = _json(path)
        rows = None if data is None else _walk(data, tool, shown, file_ours)
        return [_bad(tool, shown)] if rows is None else rows
    except Exception:
        return [_bad(tool, shown)] if shown else []

def _json(path: Path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX:
            return None
        blob = bytearray()
        while len(blob) < info.st_size and (chunk := os.read(fd, info.st_size - len(blob))):
            blob += chunk
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        return json.loads(bytes(blob).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

def _grok(home: Path) -> list[dict]:
    directory = home / ".grok" / "hooks"
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return []
    try:
        names = []
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            names = sorted(name for name in os.listdir(fd) if name.endswith(".json"))
    except OSError:
        return []
    finally:
        os.close(fd)
    return [row for name in names for row in _one(directory / name, home, "grok", name == "heliopause.json")]

def _walk(data, tool: str, shown: str, file_ours: bool):
    hooks = data.get("hooks", {}) if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return None
    rows = []
    for event, entries in hooks.items():
        if not isinstance(event, str) or not isinstance(entries, list):
            return None
        for entry in entries:
            found = _entries(entry)
            if found is None:
                return None
            for hook in found:
                name, ours = _meta(hook, file_ours)
                rows.append({"tool": tool, "file": shown, "event": event if _EVENT.match(event) else "?", "name": name, "ours": ours})
    return rows

def _entries(entry):
    if not isinstance(entry, dict):
        return None
    if "hooks" in entry:
        inner = entry["hooks"]
        return inner if isinstance(inner, list) and all(isinstance(item, dict) for item in inner) else None
    if "command" in entry or "url" in entry or "type" in entry:
        return [entry]
    return None

def _connector(token: str) -> bool:
    # plugin install writes the absolute path of Heliopause/connectors/<tool>/report.sh.
    # Its name is report.sh, so the atlas/heliopause basename check misses it.
    for name in ("cursor", "codex", "grok"):
        suffix = f"Heliopause/connectors/{name}/report.sh"
        if token == suffix or token.endswith("/" + suffix):
            return True
    return False

def _meta(hook: dict, file_ours: bool) -> tuple[str, bool]:
    command = hook.get("command") if isinstance(hook.get("command"), str) else None
    url = hook.get("url") if isinstance(hook.get("url"), str) else None
    kind = hook.get("type") if isinstance(hook.get("type"), str) else None
    token = _token(command) if command else ""
    base = _base(token) if command else ""
    ours = file_ours or base in ("atlas", "heliopause") or _connector(token) or any(bool(command) and mark in command for mark in _MARK)
    if kind == "http" or (kind is None and url is not None and command is None):
        return "http " + _host(url), ours
    if kind == "command" or command is not None:
        return (base or "?"), ours
    return kind if kind in _KINDS else "?", ours

def _host(url: str | None) -> str:
    try:
        host = urlsplit(url).hostname if url else None
    except ValueError:
        return "?"
    if host and (_HOST.match(host) or (":" in host and _V6.match(host))):
        return host
    return "?"

def _token(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    i = 0
    while i < len(parts) and _EQ.match(parts[i]):
        i += 1
    if i < len(parts) and parts[i] == "env":
        i += 1
        while i < len(parts) and (parts[i][:1] == "-" or _EQ.match(parts[i])):
            flag = parts[i]
            i += 1
            if flag in ("-u", "--unset") and i < len(parts) and parts[i][:1] != "-" and not _EQ.match(parts[i]):
                i += 1
    return parts[i] if i < len(parts) else ""

def _base(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        token = token[1:-1]
    name = token[token.rfind("/") + 1:]
    return name if _NAME.match(name) else "?"

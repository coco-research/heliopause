"""Connect and disconnect Heliopause hook entries for Cursor.

Every function takes the lab folder it may touch. The hook file is
<lab>/.cursor/hooks.json. That lab is the only folder written: a base equal
to the user's home folder is refused, and nothing is written. A symlink at
the hook file, its backup, or a folder above either is refused: nothing is
written through it. Codex and Grok come later in their own PRs. Claude Code
is connected by its plugin, not here.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from atlas import labfs
from atlas.connectors import _CURSOR

# Cursor is a flat list of {"command": ...}. Its events are the card states, in that order.
_EVENTS = tuple(_CURSOR)
_BAK = re.compile(r"^hooks\.json\.heliopause-bak-(\d{8}T\d{6}\d{6}Z)(?:-(\d+))?$")


def _check(tool: str) -> str:
    if tool != "cursor":
        raise ValueError(f"unsupported tool: {tool}")
    return tool


def _not_home(base: Path) -> None:
    """The base is the lab. The user's home folder is not a place this module writes."""
    if labfs.real_root(base) == labfs.real_root(Path.home()):
        raise ValueError(str(base))


# Cursor runs hook commands through a shell, so this tilde expands there.
# An absolute home path would be committed if the lab's hooks file is.
CURSOR_COMMAND = "~/Heliopause/connectors/cursor/report.sh"


def _command() -> str:
    return CURSOR_COMMAND


def _path(base: Path) -> Path:
    return base / ".cursor" / "hooks.json"


def _load(path: Path) -> tuple[str | None, dict | None]:
    """Return (text, object). Missing file is (None, None).

    A file that is not a JSON object raises ValueError carrying the path.
    The caller must not overwrite it.
    """
    if not path.is_file():
        if path.exists():
            raise ValueError(str(path))
        return None, None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        raise ValueError(str(path)) from err
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError(str(path)) from err
    if not isinstance(data, dict):
        raise ValueError(str(path))
    return text, data


def _dump(data: dict) -> str:
    return json.dumps(data, indent=2) + "\n"


def _real(path: Path) -> Path:
    """Resolve once. Backup, temp file, and replace all use this path."""
    return Path(os.path.realpath(path))


def _shown(path: Path) -> str:
    """The path the user knows, plus the real target when the file is a link."""
    shown = str(path)
    if not path.is_symlink():
        return shown
    real = str(_real(path))
    if real == shown:
        return shown
    return f"{shown} -> {real}"


def _diff(before: str | None, after: str, path: Path) -> str:
    if before == after or (before is None and after == ""):
        return ""
    # WHY: difflib glues the next line onto a last line that has no newline. Display only.
    old_text = "" if before is None else before
    new_text = after
    if old_text and not old_text.endswith("\n"):
        old_text += "\n"
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    old = [] if before is None else old_text.splitlines(keepends=True)
    new = [] if after == "" else new_text.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            old,
            new,
            fromfile="/dev/null" if before is None else str(path),
            tofile="/dev/null" if after == "" else str(path),
        )
    )


def _plan(path: Path, before: str | None, after: str) -> dict:
    return {"path": _shown(path), "before": before, "after": after, "diff": _diff(before, after, path)}


def _ours(entry: object, cmd: str) -> bool:
    return isinstance(entry, dict) and entry.get("command") == cmd


def _hooks(data: dict, path: Path) -> dict:
    hooks = data.get("hooks")
    if hooks is None:
        data["hooks"] = {}
        return data["hooks"]
    if not isinstance(hooks, dict):
        raise ValueError(str(path))
    return hooks


def _event_list(hooks: dict, event: str, path: Path) -> list | None:
    if event not in hooks:
        return None
    items = hooks[event]
    if not isinstance(items, list):
        raise ValueError(str(path))
    return items


def _has(items: list, cmd: str) -> bool:
    return any(_ours(item, cmd) for item in items)


def _entry(cmd: str) -> dict:
    return {"command": cmd}


def _add(data: dict, path: Path) -> bool:
    hooks = _hooks(data, path)
    cmd = _command()
    changed = False
    for event in _EVENTS:
        items = _event_list(hooks, event, path)
        if items is None:
            hooks[event] = [_entry(cmd)]
            changed = True
        elif not _has(items, cmd):
            items.append(_entry(cmd))
            changed = True
    return changed


def _strip_entries(data: dict, path: Path) -> bool:
    """Drop entries whose command is ours.

    An event key is removed only when our entries were in it and that left
    it empty. An empty event we did not change stays.
    """
    hooks = data.get("hooks")
    if hooks is None:
        return False
    if not isinstance(hooks, dict):
        raise ValueError(str(path))
    cmd = _command()
    removed = False
    for event in list(hooks):
        items = hooks[event]
        if not isinstance(items, list):
            continue
        kept = [item for item in items if not _ours(item, cmd)]
        if len(kept) == len(items):
            continue
        removed = True
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if removed and not hooks:
        data.pop("hooks", None)
    return removed


def plan(tool: str, base: Path) -> dict:
    """Describe connecting Cursor under `base`. Reads that one file and writes nothing."""
    _check(tool)
    _not_home(base)
    path = _path(base)
    before, data = _load(path)
    if data is None:
        # version is part of Cursor's file shape. Only a new file gets it;
        # an existing file keeps whatever version (or none) it already has.
        data = {"version": 1}
        _add(data, path)
        after = _dump(data)
    else:
        changed = _add(data, path)
        after = _dump(data) if changed else before
    planned = _plan(path, before, after)
    # Chosen once, here, and written under this exact name. A later clock read would not match the line we print.
    planned["backup"] = _next_backup(base, path) if before is not None and before != after else None
    return planned


def _newest_backup(base: Path) -> str | None:
    """Relative path of the newest hooks.json.heliopause-bak-* next to the hook file."""
    names = labfs.listdir(base, ".cursor")
    if not names:
        return None
    found: list[tuple[str, int, str]] = []
    for name in names:
        match = _BAK.fullmatch(name)
        if match:
            found.append((match.group(1), int(match.group(2) or 0), name))
    if not found:
        return None
    found.sort()
    return ".cursor/" + found[-1][2]


def _restored(base: Path, data: dict) -> str | None:
    """Exact text of the newest backup when it parses to the same JSON as `data`."""
    rel = _newest_backup(base)
    if rel is None:
        return None
    try:
        text, parsed = _hook_text(base, base / rel)
    except (ValueError, labfs.Refused):
        return None
    if text is None or parsed != data:
        return None
    return text


def _removal_plan(tool: str, base: Path) -> dict:
    _check(tool)
    _not_home(base)
    path = _path(base)
    before, data = _hook_text(base, path)
    if data is None:
        return _plan(path, None, "")
    removed = _strip_entries(data, path)
    if not removed:
        after = before
    else:
        # WHY: install re-serialises. The backup is the user's bytes. When it
        # is the same JSON, write those bytes back. No backup means this
        # install created the file; {"version": 1} alone is an empty shell.
        restored = _restored(base, data)
        if restored is not None:
            after = restored
        elif data == {"version": 1} and _newest_backup(base) is None:
            after = ""
        else:
            after = _dump(data)
    planned = _plan(path, before, after)
    planned["kept_backup"] = _kept_backup(base)
    # Rewrite-without-a-matching-original still keeps a copy. A restore already has one, and a delete must not make one.
    if removed and restored is None and before is not None and after not in ("", before):
        planned["backup"] = _next_backup(base, path)
    else:
        planned["backup"] = None
    return planned


def _hook_text(base: Path, path: Path) -> tuple[str | None, dict | None]:
    """Read one hook file through labfs. Missing is (None, None).

    A file that is not a JSON object raises ValueError carrying the path.
    """
    try:
        raw = labfs.read(base, labfs.rel_to(base, path))
    except labfs.Refused as err:
        # Same refusal _load gives a directory sitting where the hook file should be.
        if err.why == "it is not a regular file":
            raise ValueError(str(path)) from err
        raise
    if raw is None:
        return None, None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise ValueError(str(path)) from err
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError(str(path)) from err
    if not isinstance(data, dict):
        raise ValueError(str(path))
    return text, data


def _put(base: Path, rel: str, data: bytes, mode: int | None) -> None:
    labfs.write(base, rel, data, mode)


def _remove(base: Path, rel: str) -> None:
    if not labfs.unlink(base, rel):
        raise FileNotFoundError(rel)


def _backup_suffix(stamp: str, extra: int) -> str:
    return f".heliopause-bak-{stamp}" if extra == 0 else f".heliopause-bak-{stamp}-{extra}"


def _next_backup(base: Path, path: Path) -> str:
    """Absolute path of the backup a later write will create. The name is free at this moment."""
    rel = labfs.rel_to(base, path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    extra = 0
    while True:
        suffix = _backup_suffix(stamp, extra)
        if labfs.lstat(base, f"{rel}{suffix}") is None:
            return str(path.parent / (path.name + suffix))
        extra += 1


def _kept_backup(base: Path) -> str | None:
    """Absolute path of the newest install-time backup, or None when there is none."""
    rel = _newest_backup(base)
    if rel is None:
        return None
    return str(_path(base).parent / rel.rsplit("/", 1)[-1])


def _backup(base: Path, rel: str, dest_abs: str) -> None:
    """Copy the hook file to the path chosen in the plan. A taken name is an error, not a new name."""
    data = labfs.read(base, rel)
    if data is None:
        raise FileNotFoundError(rel)
    st = labfs.lstat(base, rel)
    if st is None:
        raise FileNotFoundError(rel)
    dest_rel = labfs.rel_to(base, Path(dest_abs))
    if labfs.lstat(base, dest_rel) is not None:
        raise FileExistsError(dest_abs)
    _put(base, dest_rel, data, stat.S_IMODE(st.st_mode))


def _commit(base: Path, path: Path, before: str | None, after: str, backup: str | None = None) -> None:
    _not_home(base)
    if before == after or (before is None and after == ""):
        return
    rel = labfs.rel_to(base, path)
    # WHY: backing up a file we are about to delete leaves a shell that the next
    # uninstall treats as the user's. A restore already has the install-time copy,
    # so `backup` is None then. `backup` is set only when a new copy should exist.
    if before is not None and after != "" and backup:
        _backup(base, rel, backup)
    if after == "":
        _remove(base, rel)
        return
    if before is None:
        mode: int | None = 0o600
    else:
        st = labfs.lstat(base, rel)
        if st is None:
            raise FileNotFoundError(rel)
        mode = stat.S_IMODE(st.st_mode)
    _put(base, rel, after.encode("utf-8"), mode)


def connect(tool: str, base: Path, yes: bool, planned: dict | None = None) -> dict:
    """Return the connect plan. Write it only when `yes` is true.

    Pass the plan already shown to the user. Its backup path is the file written.
    """
    current = plan(tool, base) if planned is None else planned
    if planned is not None:
        _check(tool)
        _not_home(base)
    if yes:
        _commit(base, _path(base), current["before"], current["after"], current.get("backup"))
    return current


def disconnect(tool: str, base: Path, yes: bool) -> dict:
    """Return the disconnect plan. Write it only when `yes` is true.

    Removes entries whose command is ours. Drops an event key only when
    those entries were removed and it is now empty. Puts the user's file
    back from its newest matching backup when there is one.
    """
    planned = _removal_plan(tool, base)
    if yes:
        _commit(base, _path(base), planned["before"], planned["after"], planned.get("backup"))
    return planned


def is_connected(tool: str, base: Path) -> bool:
    """True when every Cursor event already contains our command."""
    _check(tool)
    _not_home(base)
    path = _path(base)
    _before, data = _load(path)
    if data is None:
        return False
    hooks = data.get("hooks")
    if hooks is None:
        return False
    if not isinstance(hooks, dict):
        raise ValueError(str(path))
    cmd = _command()
    for event in _EVENTS:
        items = hooks.get(event)
        if items is None:
            return False
        if not isinstance(items, list):
            raise ValueError(str(path))
        if not _has(items, cmd):
            return False
    return True

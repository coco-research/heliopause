"""Which Lab chats wrote a transcript recently: the newest top-level .jsonl mtime per node."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from atlas.collect import LAB, LAB_DIR, LAB_LEAD, map_path

_BASE = "-Coco-Research"
_MAP = json.loads(map_path("chats.json").read_text(encoding="utf-8"))


def _node(folder: str) -> str | None:
    for suffix, node_id in _MAP.items():
        # Exact endswith: a folder name must not match a longer name that merely starts with it.
        if folder.endswith(f"{_BASE}-{suffix}" if suffix else _BASE):
            return node_id
    return None


def _scan_file(root: Path) -> Path | None:
    candidates = [Path(root) / ".atlas" / "adopt-scan.json"]
    if LAB and LAB_DIR is not None:
        candidates.insert(0, Path(LAB_DIR) / ".atlas" / "adopt-scan.json")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _epoch(stamp: object) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.timestamp()


def _ignored(scan: Path, data: dict) -> set[str]:
    # The file the user edited wins. Until they have one, the scan's suggestion applies.
    ignore_file = scan.parent / "ignore"
    found: set[str] = set()
    if ignore_file.is_file():
        try:
            text = ignore_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return found
        for line in text.splitlines():
            path = line.split("\t", 1)[0].strip()
            if path and not path.startswith("#"):
                found.add(path)
        return found
    for item in data.get("suggest_ignore") or []:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            found.add(item["path"])
    return found


def _from_scan(root: Path) -> dict[str, float] | None:
    """Lead activity from the scan's folder map. None when no scan exists (chats.json fallback).

    ponytail: every kept folder collapses onto the lead node. Per-folder boxes are the Folders view.
    """
    scan = _scan_file(root)
    if scan is None:
        return None
    try:
        data = json.loads(scan.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("folders"), dict):
        return None
    if not LAB:
        return {}
    skip = _ignored(scan, data)
    newest: float | None = None
    for path, info in data["folders"].items():
        if not isinstance(path, str) or path in skip or not isinstance(info, dict):
            continue
        stamp = _epoch(info.get("last_active"))
        if stamp is None:
            continue
        if newest is None or stamp > newest:
            newest = stamp
    if newest is None:
        return {}
    return {LAB_LEAD: newest}


def chat_activity(root: Path) -> dict[str, float]:
    """node id -> newest transcript mtime (epoch seconds).

    WHY: transcripts hold private conversation bodies. This reads ONLY
    directory listings and os.stat mtimes of top-level *.jsonl files. It never opens, reads or parses a
    transcript, and it ignores subfolders (e.g. subagents/), so no message content can reach the page.
    A scan's folder map replaces the chats.json suffixes. chats.json is used only when no scan exists.
    """
    scanned = _from_scan(root)
    if scanned is not None:
        return scanned
    if LAB:
        return {}
    found: dict[str, float] = {}
    try:
        folders = list(os.scandir(Path(root) / ".claude" / "projects"))
    except OSError:
        return found
    for folder in folders:
        node_id = _node(folder.name)
        if node_id is None:
            continue
        try:
            files = list(os.scandir(folder.path))
        except OSError:
            continue
        for entry in files:
            if not entry.name.endswith(".jsonl"):
                continue
            try:
                if not entry.is_file():
                    continue
                stamp = entry.stat().st_mtime
            except OSError:
                continue
            if stamp > found.get(node_id, 0.0):
                found[node_id] = stamp
    return found

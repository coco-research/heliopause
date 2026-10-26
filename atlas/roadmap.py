"""Read-only brain roadmaps from <root>/.heliopause/inbox/lab/roadmap (or DIR/roadmap in lab mode)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from atlas.collect import LAB, LAB_DIR, NODE_IDS, _alias, _files, _read, redact

_CAP = 16000
# Group 3 is the optional trailing parenthetical, kept as a note for the node lookup.
_HEADER = re.compile(
    r"^# (.+?) roadmap, (\d{4}-\d{2}-\d{2}|\d{2}-\d{2})\b[^\n]*?(?:\(([^)]*)\))?\s*$"
)
_WEEK = re.compile(r"^## Week ([12])\b")
_BOX = re.compile(r"^- \[([ xX])\]")
_DEMO = re.compile(r"^- (\d{2}-\d{2})\b[^\u00b7]*\u00b7 (.*)$")
_DEMO_HEAD = re.compile(r"\bsees it running\b", re.IGNORECASE)
_SEP = re.compile(r"[-: ]*")
_DONE = ("done", "merged", "shipped", "\u2705")
_RANK = re.compile(r"^(P\d)\b")
_NORM_TAIL = re.compile(r"\s+(?:brain agent|brain|dept)$", re.IGNORECASE)
_NOTE_BRAIN = re.compile(r"\s+brain$", re.IGNORECASE)
_ORDER = {"P0": 0, "P1": 1, "P2": 2, "Other": 3}


def _rank(cell: str) -> str | None:
    # Leading P\d. "P1 (plan only)" is still P1.
    match = _RANK.match(cell)
    if match is None or match.group(1) not in _ORDER:
        return None
    return match.group(1)


def _priorities(text: str) -> list[tuple[str, str, str]]:
    # ponytail: split on "|". A pipe inside a cell needs a real markdown parser.
    found = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [part.strip() for part in stripped.strip("|").split("|")]
        rank = _rank(cells[0]) if len(cells) >= 3 else None
        if rank is not None:
            found.append((rank, cells[1], cells[2]))
    return found


def _normalize(name: str) -> str:
    # Display keeps a trailing "Brain" or "Dept". Matching does not.
    return _NORM_TAIL.sub("", name).strip()


def _candidates(name: str) -> list[str]:
    norm = _normalize(name)
    if not norm:
        return []
    found = [norm]
    if norm.casefold().startswith("coco "):
        rest = norm[5:].strip()
        if rest:
            found.append(rest)
    words = norm.split()
    # ponytail: first word only. "Research & Innovation" hits any earlier "Research" cell.
    if len(words) >= 2 and words[0].casefold() != "coco":
        found.append(words[0])
    return found


def _word(cell: str, name: str) -> bool:
    if not name:
        return False
    pattern = rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])"
    return re.search(pattern, cell, re.IGNORECASE) is not None


def _priority(name: str, rows: list[tuple[str, str, str]]) -> str:
    needles = _candidates(name)
    for rank, theme, owner in rows:
        if any(_word(theme, needle) or _word(owner, needle) for needle in needles):
            return rank
    return "Other"


def _year(stamp: str, today: date) -> int:
    # YYYY-MM-DD carries its year. MM-DD uses today.
    return int(stamp[:4]) if len(stamp) == 10 else today.year


def _node(name: str, note: str | None) -> str | None:
    norm = _normalize(name)
    tries: list[str] = []
    for base in (name, norm):
        tries.extend((base, f"{base} Brain", f"{base} Brain Agent", f"{base} Dept"))
    if note:
        # Some names are only in the header note, written as "<name> brain".
        tries.append(_NOTE_BRAIN.sub("", note).strip())
    for item in tries:
        found = _alias(item)
        if found:
            return found
    return None


def _scan(lines: list[str], year: int) -> tuple[dict, dict, list[tuple[date, str]]]:
    counts = {"1": [0, 0], "2": [0, 0]}
    section = None
    header = False
    demos: list[tuple[date, str]] = []
    collecting = False
    for raw in lines:
        line = raw.strip()
        if collecting:
            if not line:
                continue
            demo = _DEMO.match(line)
            if demo is None:
                collecting = False
            else:
                month_s, day_s = demo.group(1).split("-")
                try:
                    when = date(year, int(month_s), int(day_s))
                except ValueError:
                    continue
                demos.append((when, demo.group(2).strip()))
                continue
        if _DEMO_HEAD.search(line):
            collecting = True
            continue
        week = _WEEK.match(line)
        if week:
            section = week.group(1)
            header = True
            continue
        if line.startswith("## "):
            section = None
            continue
        box = _BOX.match(line) if section else None
        if box is not None:
            counts[section][1] += 1
            if box.group(1) in "xX":
                counts[section][0] += 1
            continue
        if section is None or not line.startswith("|"):
            continue
        # ponytail: split on "|". A pipe inside a cell needs a real markdown parser.
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if header:
            header = False
            continue
        if all(_SEP.fullmatch(cell) for cell in cells):
            continue
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        counts[section][1] += 1
        if cells[-1].strip("*`").lower().startswith(_DONE):
            counts[section][0] += 1
    week1 = {"done": counts["1"][0], "total": counts["1"][1]}
    week2 = {"done": counts["2"][0], "total": counts["2"][1]}
    return week1, week2, demos


def _next(demos: list[tuple[date, str]], today: date) -> dict | None:
    if not demos:
        return None
    upcoming = [item for item in demos if item[0] >= today]
    when, text = (
        min(upcoming, key=lambda item: item[0]) if upcoming else max(demos, key=lambda item: item[0])
    )
    return {"date": when.isoformat(), "text": redact(text), "past": when < today}


def _row(filename: str, text: str, priorities: list[tuple[str, str, str]], today: date) -> dict | None:
    lines = text.splitlines()
    header = _HEADER.match(lines[0]) if lines else None
    if header is None:
        return None
    name = header.group(1).strip()
    if not name:
        return None
    note = (header.group(3) or "").strip() or None
    week1, week2, demos = _scan(lines, _year(header.group(2), today))
    if LAB:
        stem = filename[:-3] if filename.endswith(".md") else filename
        node = stem if stem in NODE_IDS else None
    else:
        node = _node(name, note)
    return {
        "file": redact(filename),
        "name": redact(name),
        "node": redact(node) if node else None,
        "priority": _priority(name, priorities),
        "week1": week1,
        "week2": week2,
        "done": week1["done"] + week2["done"],
        "total": week1["total"] + week2["total"],
        "next_demo": _next(demos, today),
        "text": redact(text)[:_CAP],
    }


def roadmap(root: Path, today: date) -> dict:
    """Brain rows plus the lab file. A missing directory is empty, never an error."""
    if LAB and LAB_DIR is not None:
        directory = LAB_DIR / "roadmap"
    else:
        directory = Path(root) / ".heliopause" / "inbox" / "lab" / "roadmap"
    try:
        if not directory.is_dir():
            return {"rows": [], "lab": None}
    except OSError:
        return {"rows": [], "lab": None}
    lab_text = _read(directory / "ROADMAP.md")
    lab = None if lab_text is None else {"file": "ROADMAP.md", "text": redact(lab_text)[:_CAP]}
    priorities = _priorities(lab_text or "")
    rows = []
    for path in _files(directory, ".md"):
        # The blank form matches the header regex. It is not a brain.
        if path.name in ("ROADMAP.md", "TEMPLATE.md"):
            continue
        if LAB and path.stem not in NODE_IDS:
            continue
        text = _read(path)
        if not text:
            continue
        row = _row(path.name, text, priorities, today)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda row: (_ORDER[row["priority"]], row["name"].casefold()))
    return {"rows": rows, "lab": lab}

"""Turn changes under .heliopause/inbox into in-memory events. Boot and reconnect stay silent."""

from __future__ import annotations

import json
import re
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlas import collect, labfs
from atlas.collect import (
    LAB,
    LAB_LABELS,
    LAB_LEAD,
    NODE_IDS,
    PRODUCTS,
    ROLE_LABELS,
    board_path,
    inbox_dir,
    WITHDRAWN_SUFFIX,
    _SEP,
    _alias,
    _blank_nodes,
    _complete,
    _files,
    _frontmatter,
    _is_headline,
    _board_state,
    _iso,
    _known_resolve,
    _read,
    _run_ts,
    _split_prose,
    hq_end,
    hq_for,
    hq_from,
    hq_queue,
    hq_roles,
    hq_visible,
    lab_personas,
    map_path,
    redact,
    scrub,
)

# label, path under .heliopause/inbox, suffix (None = any file), kind
_APPEAR = (
    ("hq-inbox", ("hq", "inbox"), ".md", "ask"),
    ("hq-done", ("hq", "done"), ".md", "answer"),
    ("queue-inbox", ("queue", "inbox"), ".md", "job"),
    ("queue-running", ("queue", "running"), ".md", "job-start"),
    ("queue-done", ("queue", "done"), ".md", "job-done"),
    ("queue-failed", ("queue", "failed"), ".md", "job-failed"),
)


def _appear_files(label: str, directory: Path, suffix: str | None) -> list[Path]:
    # WHY: queue folders hold .md + .log pairs and withdrawn jobs; only real jobs pulse, once.
    paths = _files(directory, suffix)
    if label.startswith("queue-"):
        paths = [path for path in paths if not path.name.endswith(WITHDRAWN_SUFFIX)]
    return paths


_READ_ERRORS = (OSError, UnicodeError, json.JSONDecodeError, ValueError)

# Node labels live here so the mention vocabulary can be built without importing graph.
_LABELS = {
    **ROLE_LABELS,
    "hq": "HQ",
    "vp": "VP",
    "dept-engineering": "Engineering",
    "dept-design": "Design",
    "dept-research": "Research",
    "dept-budget": "Budget Office",
    "dept-repo-office": "Repo Office",
    "dept-lab-ops": "Lab Ops",
    **dict(PRODUCTS),
    "factory-queue": "Queue Factory",
    "factory-grok": "Grok Build",
    "factory-gemini": "Antigravity",
    "factory-zcode": "Z Code",
    "github": "GitHub",
    "users": "Users",
}
if LAB:
    _LABELS = dict(LAB_LABELS)
_NODE_IDS = set(NODE_IDS)


def _mention_vocab() -> tuple[re.Pattern[str], dict[str, str | None]]:
    """Case-sensitive names, longest first. None consumes a name and emits nothing."""
    found: dict[str, str | None] = {}
    for node_id, label in _LABELS.items():
        # Users is not a part anyone mentions. The owner is the word "Owner", added below.
        # HQ roles are not mentioned either: "HR" or "CEO" in prose is not a message to them.
        if node_id in ("users", "owner") or node_id in ROLE_LABELS or label in ("Users", "Owner"):
            continue
        found[label] = node_id
    aliases = json.loads(map_path("aliases.json").read_text(encoding="utf-8"))
    for key, node_id in aliases.items():
        found.setdefault(key, node_id)
    found["Grok"] = "factory-grok"
    found["Owner"] = "owner"
    # WHY: "Coco Research" is the company. Consume it so "Research" is not the department.
    found["Coco Research"] = None
    names = sorted(found, key=len, reverse=True)
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(name) for name in names) + r")\b")
    return pattern, found


_MENTION_RE, _MENTION_OF = _mention_vocab()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(ident: str, ts: str, kind: str, src: str, dst: str, headline: str | None) -> dict:
    return {
        "id": ident,
        "ts": ts,
        "kind": kind,
        "from": src,
        "to": dst,
        "headline": headline,
    }


def _clean(headline: str | None) -> str | None:
    if headline is None:
        return None
    return redact(headline)


def _node(name: str, fallback: str) -> str:
    found = _alias(name)
    if found in NODE_IDS:
        return found
    return fallback


class Watcher:
    """Poll the lab inbox. The first scan is a silent baseline; later scans append events."""

    def __init__(self, root: Path, interval: float = 1.0, size: int = 500):
        self.root = Path(root)
        self.interval = interval
        self._buf: deque[tuple[int, dict]] = deque(maxlen=size)
        self._seq = 0
        self._cond = threading.Condition()
        self._scan = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready = False
        self._board: tuple[int, int] | None = None
        self._grok: tuple[int, int] | None = None
        self._seen: dict[str, set[str]] = {}

    def start(self) -> None:
        """Baseline once, then poll from a daemon thread."""
        if not self._ready:
            self.poll()
        with self._scan:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="atlas-events", daemon=True)
            thread = self._thread
        thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def poll(self) -> None:
        """Scan once. Safe to call from tests; the thread calls this too."""
        with self._scan:
            emit = self._ready
            try:
                self._board = self._tail(
                    board_path(self.root),
                    self._board,
                    emit,
                    self._board_line,
                )
            except _READ_ERRORS:
                pass
            if not LAB:
                try:
                    self._dirs(emit)
                except _READ_ERRORS:
                    pass
                try:
                    self._grok = self._tail(
                        self.root / ".heliopause" / "inbox" / "lab" / "usage" / "grok-build.jsonl",
                        self._grok,
                        emit,
                        self._grok_line,
                    )
                except _READ_ERRORS:
                    pass
            self._ready = True

    def events_after(self, seq: int) -> tuple[list[dict], int]:
        """Events whose in-memory sequence is greater than `seq`, plus the high-water mark."""
        with self._cond:
            found = [event for number, event in self._buf if number > seq]
            return found, self._seq

    def latest_seq(self) -> int:
        """Sequence a new subscriber should start from. History before this is not replayed."""
        with self._cond:
            return self._seq

    def wait(self, seq: int, timeout: float) -> None:
        """Block until a newer event is stored, the watcher stops, or `timeout` seconds pass."""
        with self._cond:
            deadline = time.monotonic() + timeout
            while self._seq <= seq and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self._cond.wait(remaining)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.poll()
            except _READ_ERRORS:
                continue

    def _add(self, event: dict) -> None:
        with self._cond:
            self._seq += 1
            self._buf.append((self._seq, event))
            self._cond.notify_all()

    def _tail(self, path: Path, state: tuple[int, int] | None, emit: bool, parse) -> tuple[int, int] | None:
        # labfs: never through a symlink at the file or a hard-linked file, and in lab mode no linked folder in
        # the lab on the way; agents write the lab, so a planted board.md link is skipped, not tailed.
        try:
            fd = labfs.open_path(path, collect.LAB_DIR)
        except (OSError, ValueError):
            return state
        try:
            info = os.fstat(fd)
            # A replace or a shrink is a new file. Re-read it as a baseline so old lines never animate.
            reset = state is not None and (info.st_ino != state[0] or info.st_size < state[1])
            offset = 0 if state is None or reset else state[1]
            os.lseek(fd, offset, os.SEEK_SET)
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
        except OSError:
            return state
        finally:
            os.close(fd)
        if reset or not emit:
            newline = data.rfind(b"\n")
            consumed = offset + newline + 1 if newline >= 0 else offset
            return (info.st_ino, consumed)
        consumed = offset
        pending = data
        while True:
            newline = pending.find(b"\n")
            if newline < 0:
                break
            raw = pending[: newline + 1]
            pending = pending[newline + 1 :]
            consumed += len(raw)
            try:
                line = raw.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError:
                continue
            try:
                event = parse(line, info.st_ino, consumed)
            except _READ_ERRORS:
                continue
            if event is not None:
                self._add(event)
                # Bound methods are new objects on each access, so compare the function.
                if getattr(parse, "__func__", None) is Watcher._board_line:
                    for mention in board_mentions(line, event["id"], hq_queue(hq_roles(self.root))):
                        self._add(mention)
        return (info.st_ino, consumed)

    def _board_line(self, line: str, inode: int, end: int) -> dict | None:
        return board_event(line, f"board:{inode}:{end}", hq_queue(hq_roles(self.root)))

    def _grok_line(self, line: str, inode: int, end: int) -> dict | None:
        return _grok_event(line, f"grok:{inode}:{end}", _now())

    def _dirs(self, emit: bool) -> None:
        for label, parts, suffix, kind in _APPEAR:
            directory = inbox_dir(self.root, parts[0]).joinpath(*parts[1:])
            try:
                paths = _appear_files(label, directory, suffix)
            except _READ_ERRORS:
                continue
            names = {path.name for path in paths}
            prev = self._seen.get(label)
            if not emit or prev is None:
                self._seen[label] = names
                continue
            seen: set[str] = set()
            for path in paths:
                if path.name in prev:
                    seen.add(path.name)
                    continue
                try:
                    events = self._file_event(label, kind, path)
                except _READ_ERRORS:
                    continue
                seen.add(path.name)
                for event in events:
                    self._add(event)
            self._seen[label] = seen

    def _file_event(self, label: str, kind: str, path: Path) -> list[dict]:
        return _appear_events(label, kind, path, _now(), hq_roles(self.root), lab_personas(self.root))


def _resolved(parts: list[str], details: str) -> tuple[str, str] | None:
    nodes = _blank_nodes()
    found = _known_resolve(details, nodes)
    directive = details
    if found is None:
        directive = parts[3].strip()
        found = _known_resolve(directive, nodes)
    if found is None:
        directive = _SEP.join(parts[3:]).strip()
        found = _known_resolve(directive, nodes)
    if found is None:
        return None
    return found[0], directive


def _field(path: Path, key: str) -> str:
    if path.suffix != ".md":
        return ""
    parsed = _frontmatter(_read(path) or "")
    return parsed[0].get(key, "") if parsed else ""


def _mapped(path: Path, key: str) -> str:
    return _node(_field(path, key), "vp")


def _owner(path: Path) -> str:
    # ponytail: owner from frontmatter `from:`; jobs from plain "claude" land on the VP until jobs carry their brain.
    return _mapped(path, "from")


def _ask(path: Path, roles: dict[str, str]) -> tuple[str, str | None, list[str]]:
    text = _read(path) or ""
    parsed = _frontmatter(text)
    fields: dict[str, str] = {}
    title = None
    if parsed:
        fields, title = parsed
    else:
        title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), None)
    headline = None
    if isinstance(title, str) and _is_headline(title):
        headline = redact(scrub(title.strip(), roles))
    sender = fields.get("from", "")
    return hq_end(sender, roles) or _node(sender, "vp"), headline, hq_for(fields.get("for"), roles)


# Board lines go to the lead: the VP here, the first role-"lab" node in a lab (or the owner if none).
_LEAD = LAB_LEAD if LAB else "vp"


def board_event(line: str, ident: str, queue: str = "hq") -> dict | None:
    """Map one complete board line to an event. `ident` is the caller-chosen id; `queue` is where needs-hq goes."""
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    parts = line.split(_SEP)
    if len(parts) < 4:
        return None
    instant = _iso(parts[0])
    name = parts[1].strip()
    if instant is None or not name or name.startswith("<"):
        return None
    state = _board_state(parts[2].strip())
    headline, details = _split_prose(parts)
    if state == "resolved":
        found = _resolved(parts, details)
        if found is None:
            return None
        target, directive = found
        if headline == directive:
            headline = None
        return _event(ident, instant, "resolved", _LEAD, target, _clean(headline))
    src = _node(name, "other")
    if state == "needs-owner":
        dst = "owner"
    elif state == "needs-hq":
        dst = queue
    elif src == _LEAD:
        # A normal lead line is addressed to the lead. That is not a message.
        return None
    else:
        dst = _LEAD
    return _event(ident, instant, "post", src, dst, _clean(headline))


def board_mentions(line: str, ident: str, queue: str = "hq") -> list[dict]:
    """One mention per named part in the prose, in first-appearance order.

    Resolved lines, unknown posters, the poster, and the line's own recipient emit nothing.
    """
    event = board_event(line, ident, queue)
    if event is None or event["kind"] == "resolved" or event["from"] not in _NODE_IDS:
        return []
    poster = event["from"]
    dest = event["to"]
    parts = line.split(_SEP)
    text = _SEP.join(parts[3:]).strip()
    seen: set[str] = set()
    found = []
    for match in _MENTION_RE.finditer(text):
        node = _MENTION_OF[match.group(0)]
        if node == "hq":
            node = queue
        if not node or node in seen or node == poster or node == dest:
            continue
        seen.add(node)
        found.append(_event(f"{ident}:m:{node}", event["ts"], "mention", poster, node, event["headline"]))
    return found


def _grok_event(line: str, ident: str, now: str | None = None) -> dict | None:
    # WHY: cwd is a path. Only the timestamp is read; the event never carries a directory.
    # Live tail passes `now` so a line with no parseable ts still emits. History omits it.
    if not line.strip():
        return None
    try:
        item = json.loads(line)
    except json.JSONDecodeError:
        return None
    ts = now
    if isinstance(item, dict):
        stamp = _run_ts(item.get("ts"))
        if stamp is not None:
            ts = stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if ts is None:
        return None
    return _event(ident, ts, "job-done", "factory-grok", "vp", None)


def _doc(path: Path) -> tuple[dict, str, str] | None:
    """Fields, title, and the full file. None when frontmatter was opened and did not parse."""
    text = _read(path) or ""
    parsed = _frontmatter(text)
    if parsed:
        fields, title = parsed
        return fields, title or "", text
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        return None
    title = next((line[2:].strip() for line in lines if line.startswith("# ")), "")
    return {}, title, text


def _for_visibility(kind: str, path: Path, fields: dict) -> dict:
    # A done file with no from takes the matching inbox ask's to, so it stays classifiable.
    if kind != "answer" or str(fields.get("from") or "").strip():
        return fields
    inherited = _field(path.parent.parent / "inbox" / path.name, "to")
    return {**fields, "from": inherited} if inherited.strip() else fields


def _appear_events(label: str, kind: str, path: Path, ts: str, roles: dict[str, str], personas: dict[str, str] | None = None) -> list[dict]:
    """One event per file; an ask for several roles is one event per role."""
    if kind in ("ask", "answer"):
        parsed = _doc(path)
        if parsed is None:
            return []
        fields, title, text = parsed
        if not hq_visible(_for_visibility(kind, path, fields), title, roles, text, personas, path.name):
            return []
    ident = f"{label}:{path.name}"
    if kind == "ask":
        src, headline, targets = _ask(path, roles)
        return [
            _event(ident if len(targets) == 1 else f"{ident}:{role}", ts, kind, src, role, headline)
            for role in targets
        ]
    if kind == "answer":
        src = hq_from(_field(path, "from"), roles)
        dst = hq_end(_field(path, "to"), roles) or _mapped(path, "to")
    elif kind == "job":
        src, dst = _owner(path), "factory-queue"
    else:
        src, dst = "factory-queue", _owner(path)
    return [_event(ident, ts, kind, src, dst, None)]


def _fmt(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _within(ts: str, start: datetime, now: datetime) -> bool:
    # Zero-padded UTC stamps from _fmt/_iso order as strings, so nothing here can raise mid-scan.
    return _fmt(start) <= ts <= _fmt(now)


def _history_board(root: Path, start: datetime, now: datetime, found: list[dict]) -> None:
    raw = _read(board_path(root))
    if not raw:
        return
    queue = hq_queue(hq_roles(root))
    for index, line in enumerate(_complete(raw).splitlines()):
        event = board_event(line, f"board:{index}", queue)
        if event is not None and _within(event["ts"], start, now):
            found.append(event)
            found.extend(board_mentions(line, f"board:{index}", queue))


def _history_files(root: Path, start: datetime, now: datetime, found: list[dict]) -> None:
    roles = hq_roles(root)
    personas = lab_personas(root)
    for label, parts, suffix, kind in _APPEAR:
        try:
            paths = _appear_files(label, inbox_dir(root, parts[0]).joinpath(*parts[1:]), suffix)
        except _READ_ERRORS:
            continue
        for path in paths:
            try:
                stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            except _READ_ERRORS:
                continue
            if stamp < start or stamp > now:
                continue
            try:
                found.extend(_appear_events(label, kind, path, _fmt(stamp), roles, personas))
            except _READ_ERRORS:
                continue


def _history_grok(root: Path, start: datetime, now: datetime, found: list[dict]) -> None:
    raw = _read(root / ".heliopause" / "inbox" / "lab" / "usage" / "grok-build.jsonl")
    if not raw:
        return
    for index, line in enumerate(_complete(raw).splitlines()):
        try:
            event = _grok_event(line, f"grok:{index}")
        except _READ_ERRORS:
            continue
        if event is not None and _within(event["ts"], start, now):
            found.append(event)


def history(root: Path, now: datetime, hours: int = 24) -> list[dict]:
    """Events the watcher would have emitted for changes inside the window, oldest first.

    File events use mtime. Grok lines need a parseable ts. Read errors are skipped.
    """
    root = Path(root)
    start = now - timedelta(hours=hours)
    found: list[dict] = []
    scans = (_history_board,) if LAB else (_history_board, _history_files, _history_grok)
    for scan in scans:
        try:
            scan(root, start, now, found)
        except _READ_ERRORS:
            continue
    found.sort(key=lambda event: event["ts"])
    return found

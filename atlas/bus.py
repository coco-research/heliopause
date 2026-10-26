"""Lab bus commands. `python3 -m atlas bus ask|check|install`."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
import time
from pathlib import Path

from atlas import labfs

_BIN = Path(__file__).resolve().parent / "templates" / "solo" / "bin"


def _lab(raw: str | None) -> Path:
    chosen = raw or os.environ.get("ATLAS_LAB") or "~/atlas-lab"
    path = Path(chosen).expanduser()
    return path if path.is_absolute() else path.resolve()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# `atlas bus ask/close/check/look` are package code: they read and write the lab only through labfs and never run
# anything from DIR/bin/. Those scripts are for agents inside their sandbox; the owner's CLI does not trust them.
_ID = re.compile(r"[A-Za-z0-9._-]{1,200}")
_TYPES = ("decision", "review", "fyi", "idea", "job")
_PRIORITIES = ("low", "normal", "high")
_STATES = ("working", "blocked", "done", "needs-owner", "needs-lead", "resolved")
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}")
_FEED = re.compile(r"## [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} · .+")
# C0 and C1 control characters except tab and newline, and DEL: lab text must not drive the terminal.
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _shown(text: object) -> str:
    """text as printed: agents write the lab, so escape sequences in it are dropped, not passed to the terminal."""
    return _CONTROLS.sub("", str(text))


def _say(message: str) -> int:
    print(f"atlas bus: {message}", file=sys.stderr)
    return 2


def _is_dir(lab: Path, rel: str) -> bool:
    st = labfs.lstat(lab, rel)
    return st is not None and stat.S_ISDIR(st.st_mode)


def _text(lab: Path, rel: str) -> str | None:
    data = labfs.read(lab, rel, cap=8 << 20)
    return None if data is None else data.decode("utf-8", "replace")


def _ask(lab: Path, to: str, text: str, kind: str = "decision", priority: str = "normal", sender: str | None = None) -> int:
    sender = sender or os.environ.get("LAB_AGENT_NAME") or "lead"
    if not (to and text and sender):
        return _say("ask needs a recipient, a title and a sender")
    if kind not in _TYPES:
        return _say(f"bad type {kind!r}")
    if priority not in _PRIORITIES:
        return _say(f"bad priority {priority!r}")
    if any(c in value for value in (to, sender, text) for c in "\r\n"):
        return _say("no line breaks in fields")
    try:
        if not _is_dir(lab, "inbox"):
            return _say(f"no inbox at {lab / 'inbox'}")
        now = time.localtime()
        stamp = time.strftime("%Y%m%d-%H%M%S", now)
        header = "---\nid: {id}\nfrom: %s\nto: %s\ntype: %s\npriority: %s\ncreated: %s\ntitle: %s\n---\n" % (
            sender, to, kind, priority, time.strftime("%Y-%m-%d %H:%M", now), text)
        for n in range(1000):
            ask_id = f"ask-{stamp}-{n}"
            if any(labfs.lstat(lab, rel) is not None for rel in (f"done/{ask_id}.md", f"done/{ask_id}.ask.md")):
                continue
            try:
                # Temp file + link, new name only: never replaces an ask, never follows a link at inbox/ or the name.
                labfs.write(lab, f"inbox/{ask_id}.md", header.replace("{id}", ask_id, 1).encode("utf-8"), 0o644, new_only=True)
            except FileExistsError:
                continue
            print(lab / "inbox" / f"{ask_id}.md")
            return 0
        return _say("no free ask id")
    except OSError as err:
        print(f"atlas bus: cannot write the ask: {err}", file=sys.stderr)
        return 1


def _close(lab: Path, ask_id: str) -> int:
    if not _ID.fullmatch(ask_id):
        return _say("bad id")
    try:
        if labfs.lstat(lab, f"inbox/{ask_id}.md") is None:
            return _say(f"no such ask: {ask_id}")
        if not _is_dir(lab, "done"):
            return _say(f"no done/ folder at {lab / 'done'}")
        labfs.rename(lab, f"inbox/{ask_id}.md", f"done/{ask_id}.ask.md")
    except FileExistsError:
        return _say(f"done/{ask_id}.ask.md already exists")
    except OSError as err:
        return _say(f"could not close {ask_id}: {err}")
    print(f"closed {ask_id}")
    return 0


def _header(text: str, key: str) -> str:
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return ""
    for line in lines[1:]:
        if line == "---":
            break
        name, colon, value = line.partition(":")
        if colon and name == key:
            return value.lstrip(" ")
    return ""


def _board_problems(text: str) -> list[str]:
    bad = []
    for number, line in enumerate(text.split("\n")[:-1] if text.endswith("\n") else text.split("\n"), 1):
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split(" · ")
        if not _STAMP.fullmatch(fields[0]):
            bad.append(f"{number}: bad timestamp")
        elif len(fields) < 4:
            bad.append(f"{number}: need name, state, headline")
        elif not fields[1] or not fields[3]:
            bad.append(f"{number}: empty name or headline")
        elif fields[2] not in _STATES:
            bad.append(f"{number}: bad state")
        elif len(fields[3]) > 60:
            bad.append(f"{number}: headline over 60 chars")
    return bad


def _md_files(lab: Path, folder: str) -> list[str]:
    names = labfs.listdir(lab, folder) or []
    return [name for name in names if name.endswith(".md") and not name.startswith(".")]


class _Report:
    def __init__(self) -> None:
        self.errors = 0
        self.warnings = 0
        self.lines: list[str] = []

    def ok(self, text: str) -> None:
        self.lines.append(f"ok    {text}")

    def fail(self, text: str) -> None:
        self.lines.append(f"FAIL  {text}")
        self.errors += 1

    def warn(self, text: str) -> None:
        self.lines.append(f"WARN  {text}")
        self.warnings += 1


def _queue(lab: Path, report: _Report, folder: str, keys: tuple[str, ...]) -> None:
    if not _is_dir(lab, folder):
        return
    count = 0
    for name in _md_files(lab, folder):
        count += 1
        shown = f"{folder}/{name}"
        try:
            text = _text(lab, shown)
        except OSError as err:
            report.fail(f"{shown}: not read: {getattr(err, 'reason', None) or err.strerror or err}")
            continue
        if text is None:
            continue
        ask_id = _header(text, "id")
        if not ask_id:
            report.fail(f"{shown}: no header with an id")
            continue
        if not _ID.fullmatch(ask_id):
            report.fail(f"{shown}: bad id characters")
        if name[:-3] not in (ask_id, f"{ask_id}.ask"):
            report.fail(f"{shown}: id '{ask_id}' does not match the filename")
        for key in keys:
            if not _header(text, key):
                report.fail(f"{shown}: missing '{key}'")
        if folder == "briefs":
            branch = _header(text, "branch")
            if branch and branch != f"factory/{ask_id}":
                report.fail(f"{shown}: branch must be 'factory/{ask_id}'")
            if len(ask_id) > 30:
                report.fail(f"{shown}: id is longer than 30 characters")
    report.ok(f"{folder}/: {count} file(s) checked")


def _validate(lab: Path) -> _Report:
    """bin/bus-check's checks, read-only, through labfs: a linked or hard-linked file is reported, never read."""
    report = _Report()
    for rel in ("BUS.md", "board.md", "feed.md", "inbox", "done", "briefs"):
        try:
            st = labfs.lstat(lab, rel)
        except OSError as err:
            report.fail(f"{rel}: not read: {getattr(err, 'reason', None) or err}")
            continue
        if st is None:
            report.fail(f"missing {lab / rel}")
        elif stat.S_ISLNK(st.st_mode):
            report.fail(f"{rel} is a symlink; not followed")
        else:
            report.ok(f"{rel} exists")
    for rel, check in (("board.md", "board"), ("feed.md", "feed")):
        try:
            text = _text(lab, rel)
        except OSError:
            continue  # already reported above (a link) or unreadable
        if text is None:
            continue
        if check == "board":
            bad = _board_problems(text)
            if bad:
                for line in bad:
                    report.fail(f"board.md line {line}")
            else:
                report.ok("board.md lines valid")
        else:
            bad_feed = [n for n, line in enumerate(text.split("\n"), 1) if line.startswith("##") and not _FEED.fullmatch(line)]
            if bad_feed:
                for n in bad_feed:
                    report.fail(f"feed.md line {n}: bad entry header")
            else:
                report.ok("feed.md headers valid")
    for folder, keys in (("inbox", ("id", "from", "to", "type", "created")),
                         ("done", ("id", "from", "to", "type", "created")),
                         ("briefs", ("id", "repo", "branch", "model"))):
        try:
            _queue(lab, report, folder, keys)
        except OSError as err:
            report.fail(f"{folder}/: not read: {getattr(err, 'reason', None) or err}")
    try:
        for name in _md_files(lab, "inbox") if _is_dir(lab, "inbox") else []:
            if labfs.lstat(lab, f"done/{name}") is not None:
                report.warn(f"inbox/{name} has a reply in done/: close it with atlas bus close {name[:-3]}")
    except OSError:
        pass
    return report


def _check(lab: Path) -> int:
    report = _validate(lab)
    for line in report.lines:
        print(_shown(line))
    print(f"bus-check: {report.errors} error(s), {report.warnings} warning(s)")
    return 0 if report.errors == 0 else 1


def _look(lab: Path) -> int:
    """bin/lab-ops-check as package code: open asks, who is stuck, the last board lines. Read-only."""
    try:
        present = labfs.listdir(lab) is not None
    except OSError:
        present = False
    if not present:
        print(f"lab: no lab at {lab}")
        return 0
    try:
        asks = len(_md_files(lab, "inbox")) if _is_dir(lab, "inbox") else 0
    except OSError:
        asks = 0
    try:
        board = _text(lab, "board.md") or ""
    except OSError as err:
        board = ""
        print(_shown(f"ATTENTION board.md not read: {getattr(err, 'reason', None) or err}"))
    lines = [line for line in board.split("\n") if line and not line.startswith("#") and line.strip()]
    state: dict[str, tuple[str, str]] = {}
    for line in lines:
        fields = line.split(" · ")
        if len(fields) < 4:
            continue
        name, current, headline = fields[1], fields[2], fields[3]
        if current == "resolved":
            state.pop(name, None)
            state.pop(headline.split(":", 1)[0], None)
        else:
            state[name] = (current, headline)
    print(f"lab: {asks} open ask(s) in inbox/")
    for name, (current, headline) in sorted(state.items()):
        if current in ("blocked", "needs-owner", "needs-lead"):
            print(_shown(f"ATTENTION {name} is {current}: {headline}"))
    if _validate(lab).errors:
        print("ATTENTION bus check found problems; run atlas bus check")
    if lines:
        print("recent board:")
        for line in lines[-5:]:
            print(_shown(line))
    return 0


def _shipped() -> list[Path]:
    return sorted((p for p in _BIN.iterdir() if p.is_file()), key=lambda p: p.name)


def _install(lab: Path) -> int:
    if not (lab / "atlas.json").is_file():
        print(f"atlas bus: run atlas init first ({lab / 'atlas.json'} not found)", file=sys.stderr)
        return 2
    copying: list[Path] = []
    skipped: list[str] = []
    for src in _shipped():
        rel = f"bin/{src.name}"
        try:
            # lstat, not exists(): a dangling link planted at bin/<name> counts as present and is never followed.
            if labfs.lstat(lab, rel) is None:
                copying.append(src)
                continue
            data = labfs.read(lab, rel)
            changed = data is None or hashlib.sha256(data).hexdigest() != _sha(src)
        except OSError:
            changed = True
        if changed:
            skipped.append(src.name)
    for name in skipped:
        print(f"skip {name}: differs from the shipped script, left unchanged")
    if not copying:
        print("atlas bus: nothing to install")
        return 0
    print("install these?")
    for src in copying:
        print(f"  {src.name}")
    try:
        answer = input("[y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "y":
        print("atlas bus: not installed")
        return 0
    for src in copying:
        try:
            # New files only, created in place: a link at bin/ or bin/<name> is refused (labfs), never followed.
            labfs.write(lab, f"bin/{src.name}", src.read_bytes(), 0o755, new_only=True)
        except OSError as err:
            print(f"atlas bus: {src.name} not installed: {err}", file=sys.stderr)
            return 1
        print(f"installed {src.name}")
    return 0


def run(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lab", help="lab folder (default: $ATLAS_LAB or ~/atlas-lab)")
    parser = argparse.ArgumentParser(prog="atlas bus", description="Use the lab bus for this lab.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    ask = verbs.add_parser("ask", parents=[common], help="write an ask into inbox/")
    ask.add_argument("--type", default="decision", choices=_TYPES)
    ask.add_argument("--priority", default="normal", choices=_PRIORITIES)
    ask.add_argument("--from", dest="sender", help="the asker's name (default: $LAB_AGENT_NAME or lead)")
    ask.add_argument("to")
    ask.add_argument("text")
    close = verbs.add_parser("close", parents=[common], help="move an answered ask to done/<id>.ask.md")
    close.add_argument("id")
    verbs.add_parser("check", parents=[common], help="validate the lab bus (read-only)")
    verbs.add_parser("look", parents=[common], help="a quick read-only morning look at the lab")
    verbs.add_parser("install", parents=[common], help="copy missing bus scripts into the lab (for agents)")
    args = parser.parse_args(argv)
    lab = _lab(args.lab)
    labfs.refuse_lab(lab, "atlas bus")
    if args.verb == "ask":
        return _ask(lab, args.to, args.text, args.type, args.priority, args.sender)
    if args.verb == "close":
        return _close(lab, args.id)
    if args.verb == "check":
        return _check(lab)
    if args.verb == "look":
        return _look(lab)
    return _install(lab)

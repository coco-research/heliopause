"""Public demo server. Stdlib only. Writes only under --root.

    python3 scripts/demo_serve.py --port 4390

Copies demo/home into --root (a new temp directory when omitted), fills every
template token in America/New_York, then runs ``python -m atlas --demo``
and appends one fictional board line every 1/rate seconds. Ctrl+C stops both.

Tokens, filled relative to ``now``:

    {{now-5m}}  {{now-2h}}  {{now+3d}}  {{now-30s}}   wall clock, YYYY-MM-DD HH:MM ET
    {{today 09:10}}  {{today+2d 09:10}}              that NY clock (today is midnight)
    {{date today+14d}}                              YYYY-MM-DD
    {{mmdd today+2d}}                               MM-DD
    {{epoch now-20m}}                               unix seconds, unquoted in JSON
    {{iso now-40m}}  {{iso today+6d 00:00}}          YYYY-MM-DDTHH:MM:SS±HH:MM

``now`` is the instant. ``today`` is that instant's NY midnight. The offset
(s, m, h, d) is applied first, then the optional HH:MM replaces the clock.

ponytail: a roadmap MM-DD uses the header date's year, so a January demo under
a December header looks past. Fine unless the recording is on Dec 30.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "demo" / "home"
GENERIC_SRC = ROOT / "demo" / "generic"
NY = ZoneInfo("America/New_York")
SEP = " \u00b7 "
_TOKEN = re.compile(r"\{\{([^{}]+)\}\}")
_KINDS = {"epoch", "iso", "date", "mmdd", "time"}
_WHEN = re.compile(r"^(now|today)([+-]\d+[smhd])?(?: (\d{2}):(\d{2}))?$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}

# WHY: Dept: Design's needs-lead line is the only problem. A newer line from that part would clear it.
GENERIC_POSTERS = (
    "HQ Assistant",
    "Product A",
    "Product B",
    "Product C",
    "Product D",
    "Product E",
    "Dept: Research",
    "Dept: Ops",
    "Factory A",
    "Factory B",
    "Factory C",
)
GENERIC_HEADLINES = (
    "Asks Product A to refresh the search index",
    "Sends Product B a calmer empty state",
    "Hands Product C the green build results",
    "Tells Product D the question is small",
    "Asks Factory A to warm the index",
    "Sends Factory B a short sketch",
    "Asks Dept: Research for the benchmark data",
    "Hands Dept: Ops a clearer deployment hint",
)

# WHY: Design Dept's needs-vp line is the only problem. A newer line from that part would clear it.
POSTERS = (
    "Nimbus Brain Agent",
    "Engineering Dept",
    "CoCo Courier Brain",
    "Coco Mind Brain",
    "Harbor Brain",
    "Connect + M0",
    "Coco Voice Brain",
    "Budget Office",
    "Repo Office",
    "Lab Ops",
    "Atlas Brain Agent",
    "Keyring Brain Agent",
    "Research & Innovation",
    "VP",
)
STATES = ("working", "done")
HEADLINES = (
    "Asks Atlas to refresh the search",
    "Sends Design a calmer empty state",
    "Hands Engineering the green build",
    "Tells Research the question is small",
    "Asks Queue Factory to warm the index",
    "Sends Grok Build a short sketch",
    "Tells Harbor the groups feel right",
    "Asks Coco Voice for a softer intro",
    "Hands Keyring a clearer lock hint",
    "Tells Lab Ops the queue is light",
    "Asks Repo Office to watch the gate",
    "Sends Budget Office the quiet numbers",
    "Tells Nimbus the filters stuck",
    "Asks CoCo Courier for a shorter digest",
    "Hands Coco Mind the next pack note",
    "Sends Connect + M0 a sync ping",
    "Tells Atlas the map looks calm",
    "Asks Design about the onboarding tone",
    "Hands Queue Factory and Grok Build the next pass",
    "Sends Engineering and Design the sketch",
    "Tells Keyring and Repo Office it is quiet",
    "Asks Lab Ops and Budget Office to glance",
    "Shares the voice clip with Coco Voice",
    "Passes the layout notes to Harbor",
    "Flags Research about the open question",
    "Tells Nimbus and Atlas the view held",
    "Asks Connect + M0 to keep the sync",
    "Sends CoCo Courier the morning line",
    "Hands Coco Mind a smaller decision",
    "Tells Queue Factory the job is still running",
)
DETAILS = ("for review", "on the board", "this hour", "for the beta")


def _clock(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(NY)
    if now.tzinfo is None:
        return now.replace(tzinfo=NY)
    return now.astimezone(NY)


def _when(spec: str, now: datetime) -> datetime:
    match = _WHEN.match(spec.strip())
    if match is None:
        raise ValueError(f"bad template time: {spec}")
    base, offset, hour, minute = match.groups()
    stamp = now if base == "now" else now.replace(hour=0, minute=0, second=0, microsecond=0)
    if offset:
        amount = int(offset[1:-1])
        if offset[0] == "-":
            amount = -amount
        stamp += timedelta(**{_UNITS[offset[-1]]: amount})
    if hour is not None:
        stamp = stamp.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    return stamp


def fill(text: str, now: datetime | None = None, zone: bool = True) -> str:
    """Replace every ``{{...}}`` token. Unknown tokens raise ValueError."""
    moment = _clock(now)

    def repl(match: re.Match) -> str:
        body = match.group(1).strip()
        head, _, rest = body.partition(" ")
        if head in _KINDS:
            if not rest.strip():
                raise ValueError(f"bad template token: {body}")
            kind, spec = head, rest.strip()
        elif body:
            kind, spec = "stamp", body
        else:
            raise ValueError("bad template token")
        stamp = _when(spec, moment)
        if kind == "epoch":
            return str(int(stamp.timestamp()))
        if kind == "iso":
            return stamp.isoformat(timespec="seconds")
        if kind == "date":
            return stamp.strftime("%Y-%m-%d")
        if kind == "mmdd":
            return stamp.strftime("%m-%d")
        if kind == "time" or not zone:
            return stamp.strftime("%Y-%m-%d %H:%M")
        return stamp.strftime("%Y-%m-%d %H:%M ET")

    return _TOKEN.sub(repl, text)


def fill_tree(root: Path, now: datetime | None = None, zone: bool = True) -> None:
    """Fill tokens in files under root. A symlink that leaves root is refused."""
    root = root.resolve()
    moment = _clock(now)
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if not path.resolve().is_relative_to(root):
            raise SystemExit("refusing write outside --root")
        text = path.read_text(encoding="utf-8")
        filled = fill(text, moment, zone=zone)
        if filled != text:
            path.write_text(filled, encoding="utf-8")


def _guard(root: Path) -> Path:
    """Refuse home, the real inbox, both template trees, and an inbox that escapes root."""
    root = root.expanduser().resolve()
    home = Path.home().resolve()
    real_inbox = (home / ".heliopause" / "inbox").resolve()
    if root == home or root == real_inbox or home.is_relative_to(root):
        raise SystemExit("refusing --root")
    for src in (SRC.resolve(), GENERIC_SRC.resolve()):
        if root == src or root.is_relative_to(src) or src.is_relative_to(root):
            raise SystemExit("refusing --root")
    link = root / ".heliopause" / "inbox"
    if link.exists() and not link.resolve().is_relative_to(root):
        raise SystemExit("refusing --root")
    return root


def prepare(root: Path, now: datetime | None = None, generic: bool = False) -> Path:
    """Copy template into root and fill tokens there. The template tree stays untouched."""
    src = GENERIC_SRC if generic else SRC
    dest = _guard(Path(root))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True)
    dest = _guard(dest)
    fill_tree(dest, now, zone=not generic)
    return dest


def _append(root: Path, path: Path, line: str) -> None:
    if not path.resolve().is_relative_to(root):
        raise SystemExit("refusing write outside --root")
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.is_file():
        with path.open("rb") as handle:
            handle.seek(0, 2)
            if handle.tell():
                handle.seek(-1, 2)
                if handle.read(1) != b"\n":
                    prefix = "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(prefix + line + "\n")


def _line(index: int, now: datetime, generic: bool = False) -> str:
    posters = GENERIC_POSTERS if generic else POSTERS
    headlines = GENERIC_HEADLINES if generic else HEADLINES
    poster = posters[index % len(posters)]
    state = STATES[index % len(STATES)]
    headline = headlines[index % len(headlines)]
    if poster in headline:
        # A line never addresses its own sender.
        headline = headlines[(index + 1) % len(headlines)]
    detail = DETAILS[index % len(DETAILS)]
    if generic:
        stamp = now.astimezone(NY).strftime("%Y-%m-%d %H:%M")
    else:
        stamp = now.astimezone(NY).strftime("%Y-%m-%d %H:%M ET")
    return SEP.join((stamp, poster, state, headline, detail))


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def serve(root: Path, port: int, rate: float, generic: bool = False) -> int:
    """Run atlas and append board lines until Ctrl+C or the child exits."""
    print(f"demo root: {root}", flush=True)
    if generic:
        cmd = [sys.executable, "-m", "atlas", "--lab", str(root), "--port", str(port)]
        board = root / "board.md"
    else:
        cmd = [sys.executable, "-m", "atlas", "--port", str(port), "--root", str(root), "--demo"]
        board = root / ".heliopause" / "inbox" / "lab" / "board.md"
    proc = subprocess.Popen(cmd, cwd=ROOT)
    posters = GENERIC_POSTERS if generic else POSTERS
    index = 0
    try:
        while True:
            code = proc.poll()
            if code is not None:
                return code
            line = _line(index, datetime.now(NY), generic=generic)
            _append(root, board, line)
            print(f"{posters[index % len(posters)]} {STATES[index % len(STATES)]}", flush=True)
            index += 1
            time.sleep(1.0 / rate)
    except KeyboardInterrupt:
        return 0
    finally:
        _stop(proc)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="demo_serve")
    parser.add_argument("--port", type=int, default=4390)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--generic", action="store_true")
    args = parser.parse_args(argv)
    if args.rate <= 0:
        raise SystemExit("rate must be > 0")
    dest = args.root if args.root is not None else Path(tempfile.mkdtemp(prefix="atlas-demo-"))
    dest = prepare(dest, generic=args.generic)
    code = serve(dest, args.port, args.rate, generic=args.generic)
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()

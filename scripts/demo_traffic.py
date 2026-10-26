"""Demo traffic for the Design sign-off. Stdlib only. Writes only under --root.

calm: python3 -m atlas --port 4321 --root /tmp/atlas-demo (after one demo run; wait 60 s)
busy: python3 scripts/demo_traffic.py --root /tmp/atlas-demo --rate 1.5  (in another shell, with the server running)
offline: stop the server; the strip appears within 9 s; start it again; the strip clears.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas.collect import map_path  # noqa: E402
from atlas.events import board_event, board_mentions  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "home"
ALIASES = json.loads(map_path("aliases.json").read_text(encoding="utf-8"))
POSTERS = list(ALIASES)
BRAINS = [name for name, node in ALIASES.items() if str(node).startswith("brain-")]
SEP = " \u00b7 "
NY = ZoneInfo("America/New_York")
FALLBACK_POSTER = "Atlas Brain Agent"
FALLBACK_HEADLINE = "Sends Design the sketch for review"
FALLBACK_DETAIL = "for the sign-off"

# Role names only. Each line names one or two other parts and stays under 60 characters.
HEADLINES = (
    "Sends Design the sketch for review",
    "Asks Engineering to check the build",
    "Hands Research the open question",
    "Flags Budget Office about the cap",
    "Asks Repo Office to watch the branch",
    "Tells Lab Ops the queue is clear",
    "Sends Atlas the latest state",
    "Asks Queue Factory to pick up the job",
    "Hands Grok Build a short run",
    "Updates Nimbus on the handoff",
    "Asks CoCo Courier for a pass",
    "Sends Coco Mind the decision note",
    "Tells Harbor the plan is ready",
    "Asks Connect + M0 to sync",
    "Sends Coco Voice the script",
    "Asks Keyring to check the lock",
    "Tells GitHub the branch is ready",
    "Asks Design and Engineering to look",
    "Sends Research and Design the notes",
    "Hands Queue Factory and Grok Build the queue",
    "Asks Lab Ops and Repo Office to watch",
    "Tells Nimbus and Atlas the state",
    "Sends HQ a short status note",
    "Needs Design to review the type",
    "Shares the sketch with Engineering",
    "Passes the notes to Research",
    "Asks Budget Office for a ceiling",
    "Tells Repo Office the gate is green",
    "Sends Lab Ops the night queue",
    "Asks Keyring and GitHub to confirm",
)
DETAILS = ("for the sign-off", "on the board", "this hour", "for review")
_SEQ = 0


def _guard(root: Path) -> Path:
    """Refuse home, anything above home, the real inbox, and an inbox that escapes root."""
    root = root.expanduser().resolve()
    home = Path.home().resolve()
    real_inbox = (home / ".heliopause" / "inbox").resolve()
    if root == home or root == real_inbox or home.is_relative_to(root):
        raise SystemExit("refusing --root")
    link = root / ".heliopause" / "inbox"
    if link.exists() and not link.resolve().is_relative_to(root):
        raise SystemExit("refusing --root")
    return root


def _under(root: Path, path: Path) -> None:
    if not path.resolve().is_relative_to(root):
        raise SystemExit("refusing write outside --root")
    path.parent.mkdir(parents=True, exist_ok=True)


def _append(root: Path, path: Path, line: str) -> None:
    """One write. A file that does not end in a newline gets that newline in the same write."""
    _under(root, path)
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


def _write(root: Path, path: Path, text: str) -> None:
    _under(root, path)
    path.write_text(text, encoding="utf-8")


def _move(root: Path, src: Path, dest: Path) -> None:
    _under(root, dest)
    if not src.resolve().is_relative_to(root):
        raise SystemExit("refusing write outside --root")
    src.replace(dest)


def _stamp() -> str:
    return datetime.now(NY).strftime("%Y-%m-%d %H:%M ET")


def _next_id() -> int:
    global _SEQ
    _SEQ += 1
    return _SEQ


def _first(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    files = sorted(
        path for path in directory.iterdir() if path.is_file() and not path.name.startswith(".")
    )
    return files[0] if files else None


def _board_line() -> tuple[str, str, str]:
    for _ in range(24):
        poster = random.choice(POSTERS)
        state = random.choices(
            ["working", "done", "needs-owner", "blocked"],
            weights=[8, 6, 1, 1],
        )[0]
        if ALIASES[poster] == "vp":
            state = "needs-owner"
        headline = random.choice(HEADLINES)
        line = SEP.join((_stamp(), poster, state, headline, random.choice(DETAILS)))
        event = board_event(line, "demo")
        if event and event["kind"] == "post" and board_mentions(line, "demo"):
            return line, poster, state
    line = SEP.join((_stamp(), FALLBACK_POSTER, "working", FALLBACK_HEADLINE, FALLBACK_DETAIL))
    return line, FALLBACK_POSTER, "working"


def _act_board(root: Path) -> None:
    line, poster, state = _board_line()
    _append(root, root / ".heliopause" / "inbox" / "lab" / "board.md", line)
    print(f"board {poster} {state}", flush=True)


def _act_pi_new(root: Path) -> None:
    name = f"demo-{_next_id()}.md"
    alias = random.choice(BRAINS)
    body = f"---\nfrom: {alias}\nto: queue\n---\n# Review the sketch today\n"
    _write(root, root / ".heliopause" / "inbox" / "queue" / "inbox" / name, body)
    print(f"queue inbox {name}", flush=True)


def _act_pi_move(root: Path) -> None:
    base = root / ".heliopause" / "inbox" / "queue"
    running = _first(base / "running")
    inbox = _first(base / "inbox")
    if running is not None:
        label = "failed" if random.random() < 0.12 else "done"
        _move(root, running, base / label / running.name)
        print(f"queue {running.name} {label}", flush=True)
        return
    if inbox is not None:
        _move(root, inbox, base / "running" / inbox.name)
        print(f"queue {inbox.name} running", flush=True)
        return
    _act_pi_new(root)


def _act_hq(root: Path) -> None:
    name = f"demo-{_next_id()}.md"
    who = random.choice(POSTERS)
    if random.random() < 0.5:
        body = f"---\nfrom: {who}\n---\n# Needs a review today\n"
        _write(root, root / ".heliopause" / "inbox" / "hq" / "inbox" / name, body)
        print("hq ask", flush=True)
        return
    body = f"---\nto: {who}\n---\n# Here is the answer\n"
    _write(root, root / ".heliopause" / "inbox" / "hq" / "done" / name, body)
    print("hq answer", flush=True)


def _act_grok(root: Path) -> None:
    payload = {
        "ts": datetime.now(NY).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "turns": random.randint(1, 12),
        "usage": {"total_tokens": random.randint(1000, 20000)},
    }
    _append(
        root,
        root / ".heliopause" / "inbox" / "lab" / "usage" / "grok-build.jsonl",
        json.dumps(payload, separators=(",", ":")),
    )
    print("grok", flush=True)


def _ensure(root: Path) -> None:
    if root.exists():
        if not root.is_dir():
            raise SystemExit("root is not a directory")
        return
    shutil.copytree(FIXTURE, root)
    # WHY: the fixture board ends on a deliberate partial line; the first append would complete it into a fake line.
    board = root / ".heliopause" / "inbox" / "lab" / "board.md"
    if board.exists():
        text = board.read_text(encoding="utf-8")
        board.write_text(text[: text.rfind("\n") + 1], encoding="utf-8")


def _run(root: Path, rate: float, seconds: float) -> None:
    actions = (_act_board, _act_pi_new, _act_pi_move, _act_hq, _act_grok)
    end = None if seconds == 0 else time.monotonic() + seconds
    while True:
        random.choices(actions, weights=[16, 1, 1, 1, 1])[0](root)
        if end is None:
            time.sleep(random.uniform(0.75, 1.25) / rate)
            continue
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, random.uniform(0.75, 1.25) / rate))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write demo inbox traffic under --root.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--seconds", type=float, default=0)
    args = parser.parse_args(argv)
    if args.rate <= 0:
        raise SystemExit("rate must be positive")
    if args.seconds < 0:
        raise SystemExit("seconds must be >= 0")
    root = _guard(args.root)
    _ensure(root)
    root = _guard(root)
    try:
        _run(root, args.rate, args.seconds)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

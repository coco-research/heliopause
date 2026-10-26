"""python3 -m harness run --name claude|cursor --cwd DIR --timeout 1800 [--model M] [--force] [--lock-dir DIR]"""

from __future__ import annotations

import argparse
import math
import sys
from typing import List, Optional

import harness
from harness import run

# outcome → process status. "error" is the CLI's own code, handled below.
_OUTCOME_EXIT = {
    "ok": 0,
    "busy": 75,
    "harness_missing": 127,
    "timeout": 124,
    "not_signed_in": 77,
}


def _emit(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="harness")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("run")
    cmd.add_argument("--name", required=True, choices=("claude", "cursor"))
    cmd.add_argument("--cwd", required=True)
    cmd.add_argument("--timeout", required=True, type=float)
    cmd.add_argument("--model", default=None)
    cmd.add_argument("--force", action="store_true")
    cmd.add_argument("--lock-dir", default=None)
    args = parser.parse_args(argv)
    if args.force and args.name != "cursor":
        sys.stderr.write("harness: --force is only valid with --name cursor\n")
        return 2
    if not math.isfinite(args.timeout) or not args.timeout > 0 or args.timeout > harness._MAX_TIMEOUT_S:
        sys.stderr.write(
            "harness: timeout must be finite, > 0, and at most %s seconds\n" % (harness._MAX_TIMEOUT_S,)
        )
        return 2
    raw = sys.stdin.buffer.read(harness._MAX_PROMPT_BYTES + 1)
    if len(raw) > harness._MAX_PROMPT_BYTES:
        sys.stderr.write("harness: prompt exceeds %s bytes\n" % (harness._MAX_PROMPT_BYTES,))
        return 2
    try:
        result = run(
            args.name,
            raw.decode("utf-8", "replace"),
            args.cwd,
            args.timeout,
            model=args.model,
            on_delta=_emit,
            force=args.force,
            lock_dir=args.lock_dir,
        )
    except OSError as exc:
        sys.stderr.write("harness: %s\n" % (exc,))
        return 127
    except ValueError as exc:
        sys.stderr.write("harness: %s\n" % (exc,))
        return 2
    sys.stderr.write("outcome: %s\n" % (result.outcome,))
    mapped = _OUTCOME_EXIT.get(result.outcome)
    if mapped is None:
        return result.exit_code or 1
    return mapped


if __name__ == "__main__":
    sys.exit(main())

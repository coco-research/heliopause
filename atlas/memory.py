"""Memory lite is an optional add-on. A build without it says so, writes nothing and exits cleanly."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

NOT_INCLUDED = "memory lite is not included in this build"
# The recall hook for a build without the add-on: it does nothing.
_NOOP_HOOK = "#!/usr/bin/env bash\nexit 0\n"

try:
    from atlas import memory_lite as _lite
except ImportError:  # a public build leaves the add-on out
    _lite = None


def __getattr__(name: str):
    # Helpers of the add-on (env, _loopback, _SRC) stay reachable here when it is present.
    if _lite is None or name.startswith("__"):
        raise AttributeError(name)
    return getattr(_lite, name)


def available() -> bool:
    return _lite is not None and _lite.available()


def installed(lab: Path) -> bool:
    return _lite is not None and _lite.installed(lab)


def recall_hook(lab_dir: str) -> str:
    """Text of <lab>/bin/atlas-recall-hook. lab_dir is already shell-quoted."""
    return _NOOP_HOOK if _lite is None else _lite.recall_hook(lab_dir)


def install(lab: Path) -> bool:
    """Copy the add-on into DIR/bin. False (and a message) when this build has none."""
    if _lite is None:
        print(f"atlas memory: {NOT_INCLUDED}", file=sys.stderr)
        return False
    return _lite.install(lab)


def run(argv: list | None = None) -> int:
    if _lite is None:
        parser = argparse.ArgumentParser(prog="atlas memory")
        parser.add_argument("--lab")
        parser.parse_args(argv)
        # Not an error: this build ships without memory lite, so there is nothing to install.
        print(f"atlas memory: {NOT_INCLUDED}", file=sys.stderr)
        return 0
    return _lite.run(argv)


def _missing() -> None:
    print(f"atlas: {NOT_INCLUDED}", file=sys.stderr)
    raise SystemExit(2)


def decide(argv: list | None = None) -> int:
    if _lite is None:
        _missing()
    return _lite.decide(argv)


def recall(argv: list | None = None) -> int:
    if _lite is None:
        _missing()
    return _lite.recall(argv)

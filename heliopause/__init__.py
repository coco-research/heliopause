"""Heliopause. The same program as the `atlas` package: `python3 -m heliopause` is `python3 -m atlas`."""

from __future__ import annotations

import runpy

from atlas import __version__

__all__ = ["__version__", "main"]


def main() -> None:
    """Console-script entry (`heliopause`, `atlas`). Runs atlas.__main__ as __main__, so --lab is read before collect imports."""
    runpy.run_module("atlas.__main__", run_name="__main__", alter_sys=True)

"""These are cross-tool conditions 2 and 3 and K7 (d) from Tess's ask-0456 terms read."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Always out of the shipped scan, plus whatever .public-exclude drops or stubs.
_ALWAYS = ("vendor", "tests", "docs")

_MODE = ("setMode", "bypassPermissions")

_CREDENTIALS = (
    ".credentials.json",
    "auth.json",
    ".gemini",
    "CODEX_API_KEY",
    "XAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
)

# Exact source lines that name a credential needle only to block it.
# (relative path, exact line, one-line reason). A read of a credential is never an entry.
# adopt.py blocks ~/keys and ~/.secrets; those names are not in _CREDENTIALS.
_CREDENTIAL_ALLOW: tuple[tuple[str, str, str], ...] = (
    (
        "atlas/detect.py",
        '        "config": (".gemini",),',
        "existence check only: find_tools asks whether the ~/.gemini folder exists (as for .claude/.codex/.grok); nothing in it is opened",
    ),
)

_TRUST = (
    "--dangerously-bypass-hook-trust",
    "--skip-trust",
    "GEMINI_CLI_TRUST_WORKSPACE",
    "--approve-mcps",
    "--dangerously-load-development-channels",
)
_TRUST_FLAG = re.compile(r"(?<![\w-])--trust(?![\w-])")


def _exclude(text: str) -> tuple[list[str], list[str]]:
    """Same parser as tests/test_release_zip.py. Drops are omitted; stubs ship as '{}'."""
    drops: list[str] = []
    stubs: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().rstrip("/")
        if not line:
            continue
        if line.endswith("=> {}"):
            stubs.append(line[: -len("=> {}")].strip().rstrip("/"))
        else:
            drops.append(line)
    return drops, stubs


def _banned(rel: str, names: list[str]) -> bool:
    for name in names:
        if rel == name or rel.startswith(name + "/"):
            return True
    return False


def _shipped() -> list[Path]:
    drops, stubs = _exclude((ROOT / ".public-exclude").read_text(encoding="utf-8"))
    names = [*_ALWAYS, *drops, *stubs]
    found: list[Path] = []

    def keep(path: Path) -> None:
        if not path.is_file() or path.suffix == ".pyc":
            return
        if any(part == "__pycache__" for part in path.parts):
            return
        rel = path.relative_to(ROOT).as_posix()
        if not _banned(rel, names):
            found.append(path)

    atlas = ROOT / "atlas"
    for path in atlas.rglob("*.py"):
        keep(path)
    static = atlas / "static"
    if static.is_dir():
        for path in static.rglob("*.js"):
            keep(path)
    for folder in ("plugin", "connectors"):
        base = ROOT / folder
        if base.is_dir():
            for path in base.rglob("*"):
                keep(path)
    scripts = ROOT / "scripts"
    if scripts.is_dir():
        for path in scripts.glob("*.sh"):
            keep(path)
    return sorted(found)


def _matched(line: str, needles: tuple[str, ...], flag: re.Pattern[str] | None) -> list[str]:
    names = [needle for needle in needles if needle in line]
    if flag is not None and flag.search(line):
        names.append("--trust")
    return names


def _hits(
    needles: tuple[str, ...],
    flag: re.Pattern[str] | None = None,
    allow: tuple[tuple[str, str, str], ...] = (),
) -> list[str]:
    allowed = {(path, line) for path, line, _reason in allow}
    found: list[str] = []
    for path in _shipped():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_bytes().decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if (rel, line) in allowed:
                continue
            names = _matched(line, needles, flag)
            if names:
                found.append(f"{rel}:{number}: {', '.join(names)}: {line.strip()}")
    return found


def _check(hits: list[str]) -> None:
    if not hits:
        return
    text = "\n".join(hits)
    print(text, flush=True)
    raise AssertionError(text)


def test_no_hook_output_sets_permission_mode() -> None:
    _check(_hits(_MODE))


def test_never_reads_tool_credentials() -> None:
    for rel, line, reason in _CREDENTIAL_ALLOW:
        assert reason.strip(), rel
        source = (ROOT / rel).read_text(encoding="utf-8").splitlines()
        assert line in source, rel
        assert any(needle in line for needle in _CREDENTIALS), rel
    _check(_hits(_CREDENTIALS, allow=_CREDENTIAL_ALLOW))


def test_never_bypasses_tool_trust() -> None:
    _check(_hits(_TRUST, _TRUST_FLAG))

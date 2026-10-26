"""Which coding tools are on this machine.

PATH lookup and stat only. Never launches a program and never reads a file.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# ponytail: eight known tools, fixed order. A new tool is a new row, not a scanner.
# "agent" counts only when it resolves to a file named cursor-agent or lives inside a Cursor.app bundle.
_TOOLS = (
    {
        "id": "claude-code",
        "name": "Claude Code",
        "clis": ("claude",),
        "app": "Claude.app",
        "config": (".claude",),
        "config_is_dir": True,
        "install_url": "https://docs.claude.com/en/docs/claude-code/setup",
    },
    {
        "id": "cursor",
        "name": "Cursor",
        "clis": ("cursor-agent", "agent"),
        "app": "Cursor.app",
        "config": (".cursor",),
        "config_is_dir": True,
        "install_url": "https://cursor.com/downloads",
    },
    {
        "id": "codex",
        "name": "Codex",
        "clis": ("codex",),
        "app": "Codex.app",
        "config": (".codex",),
        "config_is_dir": True,
        "install_url": "https://developers.openai.com/codex/cli",
    },
    {
        "id": "grok",
        "name": "Grok Build",
        "clis": ("grok",),
        "app": None,
        "config": (".grok",),
        "config_is_dir": True,
        "install_url": "https://docs.x.ai/build/overview",
    },
    {
        "id": "gemini",
        "name": "Gemini CLI",
        "clis": ("gemini",),
        "app": None,
        "config": (".gemini",),
        "config_is_dir": True,
        "install_url": "https://github.com/google-gemini/gemini-cli",
    },
    {
        "id": "opencode",
        "name": "OpenCode",
        "clis": ("opencode",),
        "app": None,
        "config": (".config", "opencode"),
        "config_is_dir": True,
        "install_url": "https://opencode.ai",
    },
    {
        "id": "aider",
        "name": "Aider",
        "clis": ("aider",),
        "app": None,
        "config": (".aider.conf.yml",),
        "config_is_dir": False,
        "install_url": "https://aider.chat/docs/install.html",
    },
    {
        "id": "copilot",
        "name": "Copilot CLI",
        "clis": ("copilot",),
        "app": None,
        "config": (".copilot",),
        "config_is_dir": True,
        "install_url": "https://docs.github.com/copilot/how-tos/set-up/install-copilot-cli",
    },
)


def _is_cursor(real: str) -> bool:
    parts = [part.casefold() for part in Path(real).parts]
    return parts[-1] == "cursor-agent" or "cursor.app" in parts


def _cli(names: tuple[str, ...], env_path: str) -> str | None:
    for name in names:
        found = shutil.which(name, path=env_path)
        if not found:
            continue
        # The bare `agent` binary is Cursor's only when the file it resolves to says so.
        if name == "agent" and not _is_cursor(os.path.realpath(found)):
            continue
        return found
    return None


def _exists(path: Path, directory: bool) -> str | None:
    if directory:
        there = path.is_dir()
    else:
        there = path.is_file()
    if not there:
        return None
    return str(path)


def find_tools(env_path: str, home: Path, apps_dir: Path = Path("/Applications")) -> list[dict]:
    """One dict per known tool, in table order. `found` when a CLI, app, or config exists."""
    rows = []
    for tool in _TOOLS:
        cli = _cli(tool["clis"], env_path)
        app = _exists(apps_dir / tool["app"], True) if tool["app"] else None
        config = _exists(home.joinpath(*tool["config"]), tool["config_is_dir"])
        rows.append(
            {
                "id": tool["id"],
                "name": tool["name"],
                "found": cli is not None or app is not None or config is not None,
                "cli": cli,
                "app": app,
                "config_dir": config,
                "install_url": tool["install_url"],
            }
        )
    return rows


# Seats Heliopause can assign on the first run. Grok is never suggested as HQ.
SUPPORTED = ("claude-code", "cursor", "codex", "grok")


def suggest_seats(rows: list[dict]) -> dict | None:
    """You are HQ. One supported tool plays both Lab and Factory. None means no suggestion."""
    found: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ident = row.get("id")
        if row.get("found") is True and ident in SUPPORTED and ident not in found:
            found.append(ident)
    if not found:
        return None
    if len(found) == 1:
        only = found[0]
        return {"hq": "you", "lab": only, "factory": [only]}
    lab = "claude-code" if "claude-code" in found else next(item for item in found if item != "cursor")
    if "cursor" in found and lab != "cursor":
        factory = ["cursor"]
    else:
        factory = [item for item in found if item != lab][:1]
    return {"hq": "you", "lab": lab, "factory": factory}

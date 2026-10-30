"""One finding for the first session. Read-only. A source that raises is skipped."""

from __future__ import annotations

import json
from pathlib import Path

from atlas import github, hookcheck, repocheck

NONE = "I didn't find anything to flag in Hooks & checks or Repo standard."

# Display names for the tools whose hook files hookcheck reads.
_LABEL = {
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "codex": "Codex",
    "grok": "Grok",
}
_MISSING = "{name} is connected but has no Heliopause hook, so its work won't show here."
# Appended to Cursor's missing-hook line. Backticks mark the command for the setup page.
_CURSOR_FIX = "Run `atlas plugin install --host cursor` to add it."
_UNREADABLE = "Heliopause couldn't read {name}'s hook file ({file}), so its hooks aren't listed."
_REPO = (
    ("protection", "{repo}: Anyone with write access can push to main without a pull request."),
    ("ci", "{repo}: No workflow in .github/workflows."),
    ("readme", "{repo}: No README file at the top of the repo."),
    ("license", "{repo}: No LICENSE file. Heliopause doesn't choose a licence for you."),
)


def top(lab, home, run=None) -> dict | None:
    """The first finding, or None. Hook missing, then unreadable, then a failed repo check."""
    for source in (_missing, _unreadable, _repo):
        try:
            found = source(lab, home, run)
        except Exception:
            continue
        if found:
            return found
    return None


def _known() -> set[str]:
    # grok's files live in ~/.grok/hooks/*.json, which is not a _SOURCES row.
    return {tool for tool, _rel, _ours in hookcheck._SOURCES} | {"grok"}


def _connected(lab) -> list[str]:
    if lab is None:
        return []
    data = json.loads((Path(lab) / "atlas.json").read_text(encoding="utf-8"))
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict):
        return []
    raw = []
    for key in ("hq", "lab"):
        item = roles.get(key)
        if isinstance(item, str):
            raw.append(item)
    factory = roles.get("factory")
    if isinstance(factory, list):
        raw.extend(item for item in factory if isinstance(item, str))
    known = _known()
    found = []
    for item in raw:
        if item in known and item not in found:
            found.append(item)
    return found


def _name(tool: str) -> str:
    return _LABEL.get(tool, tool)


def _missing(lab, home, _run) -> dict | None:
    rows = hookcheck.list_hooks(Path(home), lab)
    ours = {row["tool"] for row in rows if row.get("ours") is True}
    for tool in _connected(lab):
        if tool in ours:
            continue
        line = _MISSING.format(name=_name(tool))
        if tool == "cursor":
            line = f"{line} {_CURSOR_FIX}"
        return {
            "kind": "hook_missing",
            "tool": tool,
            "line": line,
            "link": "Show the change",
        }
    return None


def _unreadable(lab, home, _run) -> dict | None:
    for row in hookcheck.list_hooks(Path(home), lab):
        if not row.get("problem"):
            continue
        return {
            "kind": "hook_unreadable",
            "line": _UNREADABLE.format(name=_name(row["tool"]), file=row["file"]),
            "link": "Open Hooks & checks",
        }
    return None


def _repo(lab, _home, run) -> dict | None:
    if not github.enabled(lab):
        return None
    report = repocheck.check(lab, run)
    repos = report.get("repos") if isinstance(report, dict) else None
    if not isinstance(repos, list):
        return None
    for row in repos:
        if not isinstance(row, dict):
            continue
        repo = row.get("repo")
        checks = row.get("checks")
        if not isinstance(repo, str) or not isinstance(checks, dict):
            continue
        for key, template in _REPO:
            if checks.get(key) == "failed":
                return {"kind": "repo", "line": template.format(repo=repo), "link": "Open Repos"}
    return None

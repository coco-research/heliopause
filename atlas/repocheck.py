"""On-demand read-only check: README, LICENSE, a workflow file, branch protection."""

from __future__ import annotations

from atlas.github import _gh_absent, allowed, enabled, load_config

_PASS = "passed"
_FAIL = "failed"
_NOT = "not checked"
_BLANK = {"readme": _NOT, "license": _NOT, "ci": _NOT, "protection": _NOT}


def check(lab_dir, run=None) -> dict:
    """Four lines per configured repo. `run(path, config)` defaults to the 404-aware gh call."""
    # ponytail: on request, no cache. Ceiling is 4 gh calls per repo per request; workflows are the first page only.
    on = enabled(lab_dir)
    try:
        config = load_config()
    except (OSError, ValueError):
        return {"enabled": on, "repos": []}
    gh = run if run is not None else _gh_absent
    rows = [{"repo": name, "checks": dict(_BLANK) if not on else _repo(gh, config, name)} for name in config["repos"]]
    return {"enabled": on, "repos": rows}


def _repo(run, config: dict, repo: str) -> dict:
    base = f"repos/{config['org']}/{repo}"
    try:
        meta = _call(run, config, base)
    except Exception:
        return dict(_BLANK)
    if not isinstance(meta, dict):
        return dict(_BLANK)
    branch = meta.get("default_branch")
    protection = _NOT
    if isinstance(branch, str):
        protection = _line(run, config, f"{base}/branches/{branch}", _protected)
    return {
        "readme": _line(run, config, f"{base}/readme", _readme),
        "license": _license(meta),
        "ci": _line(run, config, f"{base}/contents/.github/workflows", _ci),
        "protection": protection,
    }


def _call(run, config: dict, path: str):
    if not allowed(path, config):
        raise ValueError("refused gh path")
    return run(path, config)


def _line(run, config: dict, path: str, grade) -> str:
    try:
        return grade(_call(run, config, path))
    except Exception:
        return _NOT


def _readme(body) -> str:
    return _FAIL if body is None else _PASS


def _license(meta: dict) -> str:
    if "license" not in meta:
        return _NOT
    value = meta["license"]
    if value is None:
        return _FAIL
    return _PASS if isinstance(value, dict) else _NOT


def _ci(body) -> str:
    if body is None:
        return _FAIL
    if not isinstance(body, list):
        return _NOT
    for item in body:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name.endswith((".yml", ".yaml")):
            return _PASS
    return _FAIL


def _protected(body) -> str:
    if not isinstance(body, dict):
        return _NOT
    flag = body.get("protected")
    if flag is True:
        return _PASS
    if flag is False:
        return _FAIL
    return _NOT

"""Read-only GitHub poller: open PRs and their check state, via `gh api`, kept in memory only."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from atlas.collect import _NOT_HEADLINE, _json_file, map_path, redact, repos_config_path

POLL_S = 60
# A settled result is re-checked after this, so a re-run (red -> green) clears.
CHECK_TTL_S = 600
_TIMEOUT_S = 10
_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
_RED = {"failure", "timed_out", "action_required", "startup_failure"}
_PULLS = "repos/{org}/{repo}/pulls?state=open&per_page=50"
_CHECKS = "repos/{org}/{repo}/commits/{sha}/check-runs?per_page=100"
_PATH = re.compile(
    r"^repos/([^/]+)/([^/]+)(?:"
    r"/pulls\?state=open&per_page=50"
    r"|/commits/([^/?]+)/check-runs\?per_page=100"
    r"|/readme"
    r"|/contents/\.github/workflows"
    r"|/branches/(.+)"
    r")?$"
)
_EMPTY = {"updated": None, "stale": False, "prs": []}


def enabled(lab_dir: Path | None) -> bool:
    """True only when <lab>/atlas.json has {"github": {"enabled": true}}."""
    # WHY: missing, malformed, or anything but a JSON boolean true stays off, and a bad file must not raise.
    if lab_dir is None:
        return False
    data = _json_file(Path(lab_dir) / "atlas.json")
    block = data.get("github") if isinstance(data, dict) else None
    return isinstance(block, dict) and block.get("enabled") is True


def load_config() -> dict:
    path = repos_config_path(map_path("repos.json"))
    config = json.loads(path.read_text(encoding="utf-8"))
    # WHY: a wrong shape raises here, where make_server's guard turns GitHub off instead of breaking requests.
    if not isinstance(config, dict) or not isinstance(config.get("org"), str):
        raise ValueError("bad repos.json shape")
    repos = config.get("repos")
    if not isinstance(repos, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in repos.items()):
        raise ValueError("bad repos.json shape")
    return config


def allowed(path: str, config: dict) -> bool:
    """The one gate: listed read-only templates, org/repo from the config, a 40-hex sha, a safe branch."""
    match = _PATH.fullmatch(path)
    if match is None:
        return False
    org, repo, sha, branch = match.groups()
    if org != config["org"] or repo not in config["repos"] or _NAME.fullmatch(org) is None or _NAME.fullmatch(repo) is None:
        return False
    if sha is not None and _SHA.fullmatch(sha) is None:
        return False
    # ".." is in the branch alphabet, so the class alone would allow it.
    if branch is not None and (".." in branch or _BRANCH.fullmatch(branch) is None):
        return False
    return True


def _gh(path: str, config: dict):
    if not allowed(path, config):
        raise ValueError("refused gh path")
    done = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=_TIMEOUT_S)
    if done.returncode != 0:
        raise ValueError("gh failed")
    return json.loads(done.stdout)


def _gh_absent(path: str, config: dict):
    """Like _gh, but stderr containing HTTP 404 means the thing is absent (None)."""
    if not allowed(path, config):
        raise ValueError("refused gh path")
    done = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=_TIMEOUT_S)
    if done.returncode != 0:
        if "HTTP 404" in done.stderr:
            return None
        raise ValueError("gh failed")
    return json.loads(done.stdout)


def _checks(runs: object) -> str:
    # ponytail: any failing run counts, required or not. Required-only needs the branch-protection API.
    runs = runs if isinstance(runs, list) else []
    if any(run.get("conclusion") in _RED for run in runs):
        return "red"
    if not runs:
        return "none"
    return "pending" if any(run.get("status") != "completed" for run in runs) else "green"


def _title(text: object) -> str:
    clean = redact(text.strip())[:80] if isinstance(text, str) else ""
    return "" if _NOT_HEADLINE.search(clean) else clean


def _utc(stamp: str) -> str:
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Poller:
    """Poll open PRs per repo. A failure keeps the last good data and marks the cache stale."""

    def __init__(self, config: dict | None = None, interval: float = POLL_S):
        self.config = config if config is not None else load_config()
        self.interval = interval
        self._lock = threading.Lock()
        self._by_repo: dict[str, list[dict]] = {}
        self._cache: dict[tuple[str, str], tuple[str, float]] = {}  # (repo, head sha) -> (settled state, fetched at)
        self._updated: str | None = None
        self._stale = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="atlas-github", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    def _repo(self, repo: str, seen: set[tuple[str, str]]) -> list[dict]:
        org = self.config["org"]
        pulls = _gh(_PULLS.format(org=org, repo=repo), self.config)
        found = []
        for pull in pulls:
            sha = pull["head"]["sha"]
            key = (repo, sha)
            seen.add(key)
            cached = self._cache.get(key)
            checks = cached[0] if cached and time.monotonic() - cached[1] < CHECK_TTL_S else None
            if checks is None:
                checks = _checks(_gh(_CHECKS.format(org=org, repo=repo, sha=sha), self.config).get("check_runs"))
                # ponytail: only settled shas are cached, for CHECK_TTL_S (a pending or empty one is refetched each cycle); pruned to open heads.
                if checks in ("red", "green"):
                    self._cache[key] = (checks, time.monotonic())
            found.append({
                "repo": repo, "node": self.config["repos"][repo], "number": pull["number"],
                "title": _title(pull.get("title")), "updated": _utc(pull["updated_at"]), "checks": checks,
            })
        return found

    def poll_once(self) -> None:
        """One cycle over every repo. Never raises."""
        seen: set[tuple[str, str]] = set()
        fresh: dict[str, list[dict]] = {}
        ok = True
        for repo in self.config["repos"]:
            try:
                fresh[repo] = self._repo(repo, seen)
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
                ok = False
        with self._lock:
            self._by_repo.update(fresh)
            self._stale = not ok
            if ok:
                self._updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                self._cache = {key: value for key, value in self._cache.items() if key in seen}

    def snapshot(self) -> dict:
        with self._lock:
            prs = [dict(pr) for repo in self.config["repos"] for pr in self._by_repo.get(repo, [])]
            return {"updated": self._updated, "stale": self._stale, "prs": prs}


def empty() -> dict:
    return dict(_EMPTY, prs=[])

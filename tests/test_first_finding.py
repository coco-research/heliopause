"""The first finding. Tmp home and lab only. gh is a fake run, never the network."""

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from atlas import repocheck
from atlas.__main__ import make_server
from atlas.first_finding import NONE, top
from atlas.plugin import _first_run

ROOT = Path(__file__).resolve().parents[1]
_CURSOR_FIX = "Run `atlas plugin install --host cursor` to add it."
_MISSING_LINE = (
    "Cursor is connected but has no Heliopause hook, so its work won't show here. " + _CURSOR_FIX
)
_WITH = (
    "Open with exactly one sentence: "
    f"'Here's one thing I found in your setup: {_MISSING_LINE}' "
    "Then ask: 'Which project should I watch first?' Don't list folders."
)
_WITHOUT = (
    "Open with exactly one sentence: "
    f"'{NONE}' "
    "Then ask: 'Which project should I watch first?' Don't list folders."
)
_FOREIGN = {"version": 1, "hooks": {"beforeSubmit": [{"command": "/opt/cursor/foreign.sh"}]}}


def _lab(tmp_path, roles=None, github=False):
    lab = tmp_path / "lab"
    lab.mkdir(parents=True)
    data = {"v": 1, "owner": "Owner", "tz": "UTC"}
    if roles is not None:
        data["roles"] = roles
    if github:
        data["github"] = {"enabled": True}
    (lab / "atlas.json").write_text(json.dumps(data), encoding="utf-8")
    return lab


def _home(tmp_path, body=None, raw=None):
    home = tmp_path / "home"
    if body is None and raw is None:
        home.mkdir()
        return home
    path = home / ".cursor" / "hooks.json"
    path.parent.mkdir(parents=True)
    if raw is not None:
        path.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
    else:
        path.write_text(json.dumps(body), encoding="utf-8")
    return home


def _grades(monkeypatch, repos, outcomes):
    monkeypatch.setattr(repocheck, "load_config", lambda: {"org": "acme", "repos": repos})
    seen = []

    def run(path, config):
        seen.append(path)
        repo = path.split("/")[2]
        rest = path.split("/", 3)[3] if path.count("/") > 2 else ""
        row = outcomes[repo]
        if rest == "":
            return {"license": row.get("license", {"spdx_id": "MIT"}), "default_branch": "main"}
        if rest == "readme":
            return row.get("readme", {"name": "README.md"})
        if rest == "contents/.github/workflows":
            return row.get("ci", [{"name": "ci.yml"}])
        if rest == "branches/main":
            return {"protected": row.get("protected", True)}
        raise AssertionError(path)

    return seen, run


def test_connected_cursor_without_ours_is_hook_missing(tmp_path):
    lab = _lab(tmp_path, roles={"lab": "cursor"})
    home = _home(tmp_path, _FOREIGN)
    found = top(lab, home, run=lambda path, config: (_ for _ in ()).throw(AssertionError(path)))
    assert found == {
        "kind": "hook_missing",
        "tool": "cursor",
        "line": _MISSING_LINE,
        "link": "Show the change",
    }


def test_unreadable_hook_file(tmp_path):
    lab = _lab(tmp_path)
    home = _home(tmp_path, raw="{")
    found = top(lab, home)
    assert found == {
        "kind": "hook_unreadable",
        "line": "Heliopause couldn't read Cursor's hook file (~/.cursor/hooks.json), so its hooks aren't listed.",
        "link": "Open Hooks & checks",
    }


def test_github_off_never_looks_up_repos(tmp_path, monkeypatch):
    lab = _lab(tmp_path)
    home = _home(tmp_path)
    seen = []
    looked = []
    monkeypatch.setattr(repocheck, "check", lambda *args, **kwargs: looked.append(args))
    assert top(lab, home, run=lambda path, config: seen.append(path)) is None
    assert seen == [] and looked == []


def test_protection_failed_when_github_is_on(tmp_path, monkeypatch):
    lab = _lab(tmp_path, github=True)
    home = _home(tmp_path)
    seen, run = _grades(
        monkeypatch,
        {"clean": "a", "open": "b"},
        {"clean": {}, "open": {"protected": False}},
    )
    found = top(lab, home, run=run)
    assert found == {
        "kind": "repo",
        "line": "open: Anyone with write access can push to main without a pull request.",
        "link": "Open Repos",
    }
    assert seen


def test_ci_failed_when_protection_passed(tmp_path, monkeypatch):
    lab = _lab(tmp_path, github=True)
    home = _home(tmp_path)
    _seen, run = _grades(monkeypatch, {"bare": "a"}, {"bare": {"ci": []}})
    found = top(lab, home, run=run)
    assert found == {
        "kind": "repo",
        "line": "bare: No workflow in .github/workflows.",
        "link": "Open Repos",
    }


def test_readme_line_when_earlier_checks_passed(tmp_path, monkeypatch):
    lab = _lab(tmp_path, github=True)
    home = _home(tmp_path)
    _seen, run = _grades(monkeypatch, {"docs": "a"}, {"docs": {"readme": None}})
    found = top(lab, home, run=run)
    assert found == {
        "kind": "repo",
        "line": "docs: No README file at the top of the repo.",
        "link": "Open Repos",
    }


def test_license_line_when_earlier_checks_passed(tmp_path, monkeypatch):
    lab = _lab(tmp_path, github=True)
    home = _home(tmp_path)
    _seen, run = _grades(monkeypatch, {"docs": "a"}, {"docs": {"license": None}})
    found = top(lab, home, run=run)
    assert found == {
        "kind": "repo",
        "line": "docs: No LICENSE file. Heliopause doesn't choose a licence for you.",
        "link": "Open Repos",
    }


def test_protection_wins_over_readme(tmp_path, monkeypatch):
    lab = _lab(tmp_path, github=True)
    home = _home(tmp_path)
    _seen, run = _grades(
        monkeypatch,
        {"docs": "a"},
        {"docs": {"protected": False, "readme": None}},
    )
    found = top(lab, home, run=run)
    assert found["line"] == "docs: Anyone with write access can push to main without a pull request."


def test_nothing(tmp_path):
    assert top(_lab(tmp_path), _home(tmp_path), run=lambda path, config: None) is None


def test_hook_missing_beats_repo(tmp_path, monkeypatch):
    lab = _lab(tmp_path, roles={"hq": "cursor"}, github=True)
    home = _home(tmp_path, _FOREIGN)
    seen, run = _grades(monkeypatch, {"open": "b"}, {"open": {"protected": False}})
    found = top(lab, home, run=run)
    assert found["kind"] == "hook_missing" and found["line"] == _MISSING_LINE
    assert seen == []


def test_first_run_text(tmp_path, monkeypatch):
    home = _home(tmp_path, _FOREIGN)
    monkeypatch.setenv("HOME", str(home))
    found = _first_run(_lab(tmp_path, roles={"lab": "cursor"}))
    assert found == _WITH
    quiet_home = tmp_path / "empty-home"
    quiet_home.mkdir()
    monkeypatch.setenv("HOME", str(quiet_home))
    assert _first_run(_lab(tmp_path / "quiet")) == _WITHOUT


def test_plugin_has_no_folder_list():
    text = (ROOT / "atlas" / "plugin.py").read_text(encoding="utf-8")
    assert text.count("10 most recent folders") == 0


def _claude_ours(home):
    path = home / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": {"SessionStart": [{"command": "/usr/bin/atlas"}]}}), encoding="utf-8")


def test_lab_cursor_file_is_not_hook_missing(tmp_path):
    home = tmp_path / "home"
    lab = _lab(home, roles={"hq": "claude-code", "lab": "claude-code", "factory": ["cursor"]})
    _claude_ours(home)
    path = lab / ".cursor" / "hooks.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "hooks": {"sessionStart": [
        {"command": f"{home}/Heliopause/connectors/cursor/report.sh"},
    ]}}), encoding="utf-8")
    found = top(lab, home)
    assert not (found and found.get("kind") == "hook_missing" and found.get("tool") == "cursor")
    assert found is None


def test_cursor_missing_line_names_the_install(tmp_path):
    home = tmp_path / "home"
    lab = _lab(home, roles={"hq": "claude-code", "lab": "claude-code", "factory": ["cursor"]})
    _claude_ours(home)
    found = top(lab, home)
    assert found["kind"] == "hook_missing" and found["tool"] == "cursor"
    assert found["line"] == _MISSING_LINE
    assert found["line"].endswith(" " + _CURSOR_FIX)


def test_other_tool_missing_line_is_unchanged(tmp_path):
    found = top(_lab(tmp_path, roles={"lab": "codex"}), _home(tmp_path))
    assert found["line"] == "Codex is connected but has no Heliopause hook, so its work won't show here."


def test_api_shape(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    sample = {
        "kind": "hook_missing",
        "tool": "cursor",
        "line": _MISSING_LINE,
        "link": "Show the change",
    }
    state = {"finding": None}
    monkeypatch.setattr("atlas.first_finding.top", lambda lab, home, run=None: state["finding"])
    server = make_server(tmp_path, 0, watch=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.request("GET", "/api/first-finding")
        resp = conn.getresponse()
        empty = json.loads(resp.read())
        assert resp.status == 200
        state["finding"] = sample
        conn.request("GET", "/api/first-finding")
        resp = conn.getresponse()
        filled = json.loads(resp.read())
        assert resp.status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert empty == {"finding": None, "none_line": NONE}
    assert filled == {"finding": sample, "none_line": NONE}

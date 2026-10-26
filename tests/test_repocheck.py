"""Repo standard check: four lines, listed repos, gh only when the switch is on."""

import json
import stat
import sys
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from atlas import github
from atlas.__main__ import LAB, LAB_DIR, make_server
from atlas.github import allowed
from atlas.repocheck import check

CONFIG = {"org": "acme", "repos": {"good": "brain-atlas", "bad": "brain-si", "down": "vp", "odd": "brain-voice"}}
BLANK = {"readme": "not checked", "license": "not checked", "ci": "not checked", "protection": "not checked"}
ROOT = Path(__file__).resolve().parents[1]
GH_PY = """import sys
from pathlib import Path
here = Path(__file__).resolve().parent
with (here / "ran").open("a", encoding="utf-8") as log:
    log.write(" ".join(sys.argv[1:]) + chr(10))
args = sys.argv[1:]
if args == ["api", "repos/acme/good/readme"]:
    sys.stderr.write("gh: Not Found (HTTP 404)")
    raise SystemExit(1)
if args == ["api", "repos/acme/good"]:
    print('{"license": null, "default_branch": "main"}')
    raise SystemExit(0)
sys.stderr.write("HTTP 500")
raise SystemExit(1)
"""


def _words(payload):
    blob = (ROOT / "atlas" / "repocheck.py").read_text(encoding="utf-8") + json.dumps(payload)
    assert "secure" not in blob and "compliant" not in blob


def _on(tmp_path, monkeypatch):
    (tmp_path / "atlas.json").write_text('{"github": {"enabled": true}}\n', encoding="utf-8")
    monkeypatch.setattr("atlas.repocheck.load_config", lambda: CONFIG)


def test_off_never_calls_gh(tmp_path, monkeypatch):
    monkeypatch.setattr("atlas.repocheck.load_config", lambda: CONFIG)
    marker = tmp_path / "marker"
    stub = tmp_path / "gh"
    stub.write_text(f'#!/bin/sh\n: > "{marker}"\nexit 1\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    seen = []
    got = check(tmp_path, run=lambda path, config: seen.append(path))
    assert got["enabled"] is False
    assert [row["repo"] for row in got["repos"]] == list(CONFIG["repos"])
    assert all(row["checks"] == BLANK for row in got["repos"])
    assert seen == [] and not marker.exists()
    _words(got)


def test_on_grades_each_line(tmp_path, monkeypatch):
    _on(tmp_path, monkeypatch)
    seen = []

    def run(path, config):
        seen.append(path)
        repo = path.split("/")[2]
        rest = path.split("/", 3)[3] if path.count("/") > 2 else ""
        if repo == "down":
            raise ValueError("gh failed")
        if rest == "":
            return {"license": None if repo == "bad" else {"spdx_id": "MIT"}, "default_branch": "my branch" if repo == "odd" else "main"}
        if rest == "readme":
            return None if repo == "bad" else {"name": "README.md"}
        if rest == "contents/.github/workflows":
            return [] if repo == "bad" else [{"name": "notes.txt"}, {"name": "ci.yml"}]
        if rest == "branches/main":
            return {"protected": repo != "bad"}
        raise ValueError("unexpected " + path)

    got = check(tmp_path, run=run)
    by = {row["repo"]: row["checks"] for row in got["repos"]}
    assert got["enabled"] is True
    assert by["good"] == {"readme": "passed", "license": "passed", "ci": "passed", "protection": "passed"}
    assert by["bad"] == {"readme": "failed", "license": "failed", "ci": "failed", "protection": "failed"}
    assert by["down"] == BLANK
    assert by["odd"] == {"readme": "passed", "license": "passed", "ci": "passed", "protection": "not checked"}
    assert sum(path.startswith("repos/acme/good") for path in seen) == 4
    assert sum(path.startswith("repos/acme/bad") for path in seen) == 4
    assert seen.count("repos/acme/down") == 1
    assert all(allowed(path, CONFIG) for path in seen)
    assert not any(path.startswith("repos/acme/down/") or ".." in path or " " in path for path in seen)
    _words(got)


def test_allowed_refuses_branch_and_other_paths():
    assert allowed("repos/acme/good/branches/" + "a" * 100, CONFIG)
    assert allowed("repos/acme/good/branches/feature/name", CONFIG)
    refused = [
        "repos/acme/good/branches/..",
        "repos/acme/good/branches/a..b",
        "repos/acme/good/branches/my branch",
        "repos/acme/good/branches/" + "a" * 101,
        "repos/acme/good/issues",
        "repos/org/repo/issues",
        "repos/acme/nope",
        "repos/acme/nope/readme",
    ]
    assert not any(allowed(path, CONFIG) for path in refused)


def test_bad_config_is_empty(tmp_path, monkeypatch):
    def boom():
        raise ValueError("bad repos.json shape")

    monkeypatch.setattr("atlas.repocheck.load_config", boom)
    seen = []
    (tmp_path / "atlas.json").write_text('{"github": {"enabled": true}}\n', encoding="utf-8")
    assert check(tmp_path, run=lambda path, config: seen.append(path)) == {"enabled": True, "repos": []}
    assert check(None, run=lambda path, config: seen.append(path)) == {"enabled": False, "repos": []}
    assert seen == []


def test_gh_absent_404_and_nothing_else(tmp_path, monkeypatch):
    (tmp_path / "gh.py").write_text(GH_PY, encoding="utf-8")
    stub = tmp_path / "gh"
    stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tmp_path}/gh.py" "$@"\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    with pytest.raises(ValueError):
        github._gh_absent("repos/acme/nope", CONFIG)
    assert not (tmp_path / "ran").exists()
    assert github._gh_absent("repos/acme/good/readme", CONFIG) is None
    assert github._gh_absent("repos/acme/good", CONFIG) == {"license": None, "default_branch": "main"}
    with pytest.raises(ValueError):
        github._gh_absent("repos/acme/good/branches/main", CONFIG)
    ran = (tmp_path / "ran").read_text(encoding="utf-8").splitlines()
    assert ran == ["api repos/acme/good/readme", "api repos/acme/good", "api repos/acme/good/branches/main"]


def test_route(tmp_path, monkeypatch):
    seen = {}

    def fake(lab_dir, run=None):
        seen["args"] = (lab_dir, run)
        return {"enabled": False, "repos": []}

    monkeypatch.setattr("atlas.repocheck.check", fake)
    server = make_server(tmp_path, 0, watch=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.request("GET", "/api/repo-check")
        resp = conn.getresponse()
        body = json.loads(resp.read())
        assert resp.status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert body == {"enabled": False, "repos": []}
    assert seen["args"] == (LAB_DIR if LAB else None, None)

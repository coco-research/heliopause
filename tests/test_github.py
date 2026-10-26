import json
import stat
import sys

import pytest

from atlas import github
from atlas.__main__ import make_server
from atlas.collect import NODE_IDS
from atlas.github import Poller, allowed, load_config

SHA = "a" * 40
SHA2 = "b" * 40
CONFIG = {"org": "acme", "repos": {"alpha": "brain-atlas"}}

FAKE_GH = """import json, sys
from pathlib import Path
here = Path(__file__).parent
with open(here / "calls.log", "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
data = json.loads((here / "data.json").read_text())
if sys.argv[1] != "api" or len(sys.argv) != 3 or sys.argv[2] not in data:
    sys.exit(1)
print(json.dumps(data[sys.argv[2]]))
"""
PULLS = "repos/acme/alpha/pulls?state=open&per_page=50"


def checks_path(sha):
    return f"repos/acme/alpha/commits/{sha}/check-runs?per_page=100"


def pull(number, sha, title="Add thing", updated="2026-10-01T10:00:00Z"):
    return {"number": number, "title": title, "updated_at": updated, "head": {"sha": sha}}


def runs(*pairs):
    return {"check_runs": [{"status": s, "conclusion": c} for s, c in pairs]}


@pytest.fixture
def gh(tmp_path, monkeypatch):
    (tmp_path / "gh.py").write_text(FAKE_GH)
    script = tmp_path / "gh"
    # WHY: a shebang can't hold a path with spaces, so a sh shim runs the interpreter.
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tmp_path}/gh.py" "$@"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")

    def serve(data):
        (tmp_path / "data.json").write_text(json.dumps(data))

    def calls():
        log = tmp_path / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    serve.calls = calls
    return serve


def test_states_and_shape(gh):
    shas = [str(n) * 40 for n in range(1, 6)]
    gh({
        PULLS: [pull(n, sha) for n, sha in enumerate(shas, 1)],
        checks_path(shas[0]): runs(("completed", "success"), ("completed", "timed_out")),
        checks_path(shas[1]): runs(("completed", "success"), ("completed", "skipped")),
        checks_path(shas[2]): runs(("completed", "success"), ("in_progress", None)),
        checks_path(shas[3]): runs(),
        checks_path(shas[4]): runs(("in_progress", None), ("completed", "failure")),
    })
    poller = Poller(CONFIG)
    poller.poll_once()
    snap = poller.snapshot()
    assert [pr["checks"] for pr in snap["prs"]] == ["red", "green", "pending", "none", "red"]
    assert snap["prs"][0] == {
        "repo": "alpha", "node": "brain-atlas", "number": 1, "title": "Add thing",
        "updated": "2026-10-01T10:00:00Z", "checks": "red",
    }
    assert snap["stale"] is False and snap["updated"]
    assert len(gh.calls()) == 6  # 1 pulls + 5 check-runs


def test_updated_is_utc():
    assert github._utc("2026-10-01T10:00:00+02:00") == "2026-10-01T08:00:00Z"


def test_settled_sha_is_not_refetched(gh):
    gh({PULLS: [pull(1, SHA), pull(2, SHA2)], checks_path(SHA): runs(("completed", "success")), checks_path(SHA2): runs(("queued", None))})
    poller = Poller(CONFIG)
    poller.poll_once()
    poller.poll_once()
    calls = gh.calls()
    assert calls.count(f"api {checks_path(SHA)}") == 1
    assert calls.count(f"api {checks_path(SHA2)}") == 2  # pending is refetched until it settles
    assert calls.count(f"api {PULLS}") == 2


def test_rerun_clears_red_after_ttl(gh, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(github.time, "monotonic", lambda: clock[0])
    gh({PULLS: [pull(1, SHA)], checks_path(SHA): runs(("completed", "failure"))})
    poller = Poller(CONFIG)
    poller.poll_once()
    gh({PULLS: [pull(1, SHA)], checks_path(SHA): runs(("completed", "success"))})
    clock[0] += github.CHECK_TTL_S - 1
    poller.poll_once()
    assert poller.snapshot()["prs"][0]["checks"] == "red"  # still cached
    clock[0] += 2
    poller.poll_once()
    assert poller.snapshot()["prs"][0]["checks"] == "green"  # re-checked after the TTL


def test_cache_is_per_repo(gh):
    other = "repos/acme/beta/pulls?state=open&per_page=50"
    gh({
        PULLS: [pull(1, SHA)], checks_path(SHA): runs(("completed", "success")),
        other: [pull(2, SHA)], f"repos/acme/beta/commits/{SHA}/check-runs?per_page=100": runs(("completed", "failure")),
    })
    poller = Poller({"org": "acme", "repos": {"alpha": "brain-atlas", "beta": "brain-si"}})
    poller.poll_once()
    assert {pr["repo"]: pr["checks"] for pr in poller.snapshot()["prs"]} == {"alpha": "green", "beta": "red"}

def test_failure_keeps_last_good_and_marks_stale(gh):
    gh({PULLS: [pull(1, SHA)], checks_path(SHA): runs(("completed", "failure"))})
    poller = Poller(CONFIG)
    poller.poll_once()
    good = poller.snapshot()
    gh({})  # the fake now exits 1 for everything
    poller.poll_once()
    snap = poller.snapshot()
    assert snap["stale"] is True
    assert snap["prs"] == good["prs"]
    assert snap["updated"] == good["updated"]


def test_first_poll_failure_and_missing_gh(gh, monkeypatch, tmp_path):
    gh({})
    poller = Poller(CONFIG)
    poller.poll_once()
    assert poller.snapshot() == {"updated": None, "stale": True, "prs": []}
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    poller.poll_once()  # gh missing: no raise
    assert poller.snapshot()["stale"] is True


def test_bad_json_is_a_failure(gh):
    gh({PULLS: "not a list"})
    poller = Poller(CONFIG)
    poller.poll_once()
    assert poller.snapshot()["stale"] is True


def test_allowlist():
    ok = [PULLS, checks_path(SHA)]
    assert all(allowed(path, CONFIG) for path in ok)
    bad = [
        PULLS + "\n",
        checks_path(SHA) + "\n",
        "repos/acme/evil/pulls?state=open&per_page=50",
        "repos/other/alpha/pulls?state=open&per_page=50",
        "repos/acme/../alpha/pulls?state=open&per_page=50",
        "repos/acme/alpha/pulls?state=open&per_page=50&x=1",
        "repos/acme/alpha/pulls?state=all&per_page=50",
        checks_path("abc123"),
        checks_path("A" * 40),
        checks_path("../" + "a" * 37),
        checks_path(SHA + "a"),
        "user",
        "repos/acme/alpha/issues",
    ]
    assert not any(allowed(path, CONFIG) for path in bad)


def test_refused_path_never_runs_gh(gh):
    with pytest.raises(ValueError):
        github._gh("repos/acme/evil/pulls?state=open&per_page=50", CONFIG)
    assert gh.calls() == []


def test_title_redacted_capped_or_dropped(gh):
    token = "ghp_" + "x" * 20
    gh({
        PULLS: [
            pull(1, SHA, title=f"Use {token} here " + "y" * 100),
            pull(2, SHA2, title="Fix src/app.py crash"),
        ],
        checks_path(SHA): runs(),
        checks_path(SHA2): runs(),
    })
    poller = Poller(CONFIG)
    poller.poll_once()
    first, second = poller.snapshot()["prs"]
    assert token not in first["title"] and "[redacted]" in first["title"] and len(first["title"]) <= 80
    assert second["title"] == ""


def test_repos_json():
    config = load_config()
    assert config["org"] == "coco-research"
    for repo, node in config["repos"].items():
        assert github._NAME.match(repo) and node in NODE_IDS


def test_server_wiring(tmp_path):
    import threading
    from http.client import HTTPConnection

    for watch in (False, True):
        server = make_server(tmp_path, 0, watch=watch)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            assert server.poller is None
            conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", "/api/state")
            assert json.loads(conn.getresponse().read())["github"] == {"updated": None, "stale": False, "prs": []}
        finally:
            server.shutdown()
            server.server_close()


def test_bad_repos_json_keeps_the_server_up(tmp_path, monkeypatch):
    def broken():
        raise ValueError("bad json")

    monkeypatch.setattr("atlas.__main__.LAB", False)
    monkeypatch.setattr("atlas.__main__.LAB_DIR", None)
    (tmp_path / "atlas.json").write_text('{"github": {"enabled": true}}\n', encoding="utf-8")
    monkeypatch.setattr(github, "load_config", broken)
    httpd = make_server(tmp_path, 0)
    try:
        assert httpd.poller is None
    finally:
        httpd.server_close()


@pytest.mark.parametrize("raw", ["{}", "[]", '{"org": "acme"}', '{"org": "acme", "repos": ["alpha"]}', '{"repos": {"alpha": "brain-atlas"}}'])
def test_load_config_rejects_bad_shapes(tmp_path, monkeypatch, raw):
    (tmp_path / "repos.json").write_text(raw)
    monkeypatch.setenv("ATLAS_MAPS_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        github.load_config()

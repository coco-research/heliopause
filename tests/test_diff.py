"""atlas diff and the setup banner. Homes are pytest tmp dirs. Loopback only."""

from __future__ import annotations

import json
import re
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from atlas.__main__ import main, make_server
from atlas.kit import compare_tools

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "locks"
INDEX = (ROOT / "atlas" / "static" / "index.html").read_text(encoding="utf-8")
INDEX_BYTES = (ROOT / "atlas" / "static" / "index.html").read_bytes()
JS = (ROOT / "atlas" / "static" / "graph.js").read_text(encoding="utf-8")
CSS = (ROOT / "atlas" / "static" / "graph.css").read_text(encoding="utf-8")
CASES = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
MIXED = FIXTURES / "mixed.lock"


def _run(argv):
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    return exc.value.code


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _machine(tmp_path, monkeypatch):
    home = tmp_path / "home"
    bindir = home / "bin"
    bindir.mkdir(parents=True)
    log = tmp_path / "uv.log"
    _stub(bindir, "uv", "#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + json.dumps(str(log)) + "\necho 'uv 0.12.22'\n")
    skill = home / ".claude" / "skills" / "ponytail"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# ponytail\n", encoding="utf-8")
    (skill / "version").write_text("4.10.1\n", encoding="utf-8")
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "atlas.json").write_text(json.dumps({
        "v": 1,
        "owner": "Owner",
        "tz": "UTC",
        "profile": "work",
        "nodes": [{"id": "lead", "label": "Lead", "role": "lab"}],
    }), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    monkeypatch.chdir(tmp_path)
    return home, lab, log


def _get(server, path):
    port = server.server_address[1]
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers={"Host": "127.0.0.1:{0}".format(port)})
    resp = conn.getresponse()
    body = resp.read()
    status = resp.status
    conn.close()
    return status, body


@pytest.mark.parametrize("case", CASES, ids=lambda case: ",".join(case["missing"] + case["extra"] + [row["name"] for row in case["drift"]]) or "clean")
def test_diff_cases_from_fixtures(case):
    report = compare_tools(case["live"], case["lock"])
    assert report == {"drift": case["drift"], "extra": case["extra"], "missing": case["missing"]}


def test_diff_drops_secret_shaped_names():
    planted = "s" + "k-atlas-PLANTED"
    report = compare_tools(
        [{"name": "uv", "version": "0.12.22"}],
        [{"name": planted, "version": "1.2.3"}, {"name": "uv", "version": "0.12.22"}],
    )
    assert report == {"drift": [], "extra": [], "missing": []}
    assert planted not in json.dumps(report)


def test_diff_cli_reports_missing_extra_and_drift(tmp_path, monkeypatch, capsys):
    home, lab, _log = _machine(tmp_path, monkeypatch)
    assert _run(["diff", str(MIXED), "--lab", str(lab)]) == 1
    out, err = capsys.readouterr()
    assert err == ""
    assert out == (
        "missing here:\n"
        "github-cli\n"
        "version drift:\n"
        "ponytail  9.9.9 -> 4.10.1\n"
    )
    assert "extra here:" not in out
    assert _run(["export", "--out", str(home / "same.lock"), "--lab", str(lab)]) == 0
    assert _run(["diff", str(home / "same.lock"), "--lab", str(lab)]) == 0
    assert capsys.readouterr().out == ""


def test_diff_without_a_path_uses_the_last_export(tmp_path, monkeypatch, capsys):
    home, lab, _log = _machine(tmp_path, monkeypatch)
    last = home / ".atlas" / "atlas.lock"
    last.parent.mkdir()
    assert _run(["export", "--out", str(last), "--lab", str(lab)]) == 0
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    assert _run(["diff", "--lab", str(lab)]) == 0
    assert capsys.readouterr().out == ""


def test_diff_exit_2_without_a_lock(tmp_path, monkeypatch, capsys):
    _machine(tmp_path, monkeypatch)
    assert _run(["diff"]) == 2
    assert "no lock" in capsys.readouterr().err
    bad = tmp_path / "bad.lock"
    bad.write_text("not a lock\n", encoding="utf-8")
    assert _run(["diff", str(bad)]) == 2


def test_banner_slot_is_filled_only_by_js():
    header = INDEX.index("</header>\n", INDEX.index('<header class="page-head">')) + len("</header>\n")
    slot = '  <p id="setup-banner" class="setup" role="status" hidden></p>\n'
    assert INDEX.startswith(slot, header)
    assert "This Mac is missing" not in INDEX
    kit = (ROOT / "atlas" / "kit" / "__init__.py").read_text(encoding="utf-8")
    server = (ROOT / "atlas" / "__main__.py").read_text(encoding="utf-8")
    assert "page_bytes" not in kit and "page_bytes" not in server
    assert "This Mac is missing" not in kit and "This Mac is missing" not in server
    paint = JS[JS.index("function paintSetup("):JS.index("function loadSetup(")]
    load = JS[JS.index("function loadSetup("):JS.index("function heartbeat(")]
    assert "document.createElement" in paint and "textContent" in paint
    assert "innerHTML" not in paint and "innerHTML" not in load
    assert 'lead.textContent = "This Mac is missing " + n + " " + word + " \\u00b7 "' in paint
    assert 'n === 1 ? "tool" : "tools"' in paint
    assert 'link.textContent = "See what\\u2019s missing"' in paint
    assert 'mark.className = "setup-mark"' in paint
    assert 'mark.textContent = "\\u25c7 "' in paint
    assert 'link.setAttribute("href", "/api/setup/dry-run")' in paint
    assert "status.lock_age === null || status.lock_age === undefined || missing.length === 0" in paint
    assert 'fetch("/api/setup", { cache: "no-store", credentials: "omit" })' in load
    assert re.search(r"^  const SETUP_POLL_MS = 600000;$", JS, re.M)
    assert "loadSetup();" in JS and "setInterval(loadSetup, SETUP_POLL_MS);" in JS
    block = CSS[CSS.index(".setup {"):CSS.index("}", CSS.index(".setup {")) + 1]
    assert "var(--amber)" in block and "var(--gap-wash)" in block
    assert "min-width" not in block


def test_api_setup_shape_and_banner(tmp_path, monkeypatch):
    home, _lab, log = _machine(tmp_path, monkeypatch)
    server = make_server(home, 0, watch=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(server, "/api/setup")
        assert status == 200
        empty = json.loads(body.decode("utf-8"))
        assert empty == {"drift": [], "extra": [], "lock_age": None, "missing": []}
        page_status, page = _get(server, "/map")
        assert page_status == 200 and page == INDEX_BYTES

        (home / ".atlas" / "atlas.lock").write_bytes(MIXED.read_bytes())
        status, body = _get(server, "/api/setup")
        got = json.loads(body.decode("utf-8"))
        assert status == 200
        assert set(got) == {"missing", "extra", "drift", "lock_age"}
        assert got["missing"] == ["github-cli"] and got["extra"] == []
        assert got["drift"] == [{"here": "4.10.1", "lock": "9.9.9", "name": "ponytail"}]
        assert isinstance(got["lock_age"], int) and got["lock_age"] >= 0
        assert log.read_text(encoding="utf-8").strip().splitlines() == ["--version"]
        _status, page = _get(server, "/map")
        assert page == INDEX_BYTES

        log.write_text("", encoding="utf-8")
        status, body = _get(server, "/api/setup/dry-run")
        dry = body.decode("utf-8")
        assert status == 200
        assert dry.startswith("dry run\n") and dry.endswith("nothing installed\n")
        assert "github-cli  brew  fc4b137cdef0a6bd28fd461b7cf9c84a5812a8cd\n" in dry
        assert log.read_text(encoding="utf-8") == ""

        assert _run(["export", "--out", str(home / ".atlas" / "atlas.lock"), "--lab", str(_lab)]) == 0
        status, body = _get(server, "/api/setup")
        matched = json.loads(body.decode("utf-8"))
        assert status == 200 and matched["missing"] == [] and isinstance(matched["lock_age"], int)
        _status, page = _get(server, "/map")
        assert page == INDEX_BYTES
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_setup_cache_skips_subprocess_within_ttl(tmp_path, monkeypatch):
    home, _lab, _log = _machine(tmp_path, monkeypatch)
    calls = []

    class _Done:
        stdout = b"uv 0.12.22\n"
        returncode = 0

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return _Done()

    monkeypatch.setattr("atlas.kit.subprocess.run", fake_run)
    server = make_server(home, 0, watch=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        (home / ".atlas" / "atlas.lock").write_bytes(MIXED.read_bytes())
        status, body = _get(server, "/api/setup")
        assert status == 200 and calls
        first = len(calls)
        got = json.loads(body.decode("utf-8"))
        assert got["missing"] == ["github-cli"]
        status, again_body = _get(server, "/api/setup")
        assert status == 200 and len(calls) == first
        again = json.loads(again_body.decode("utf-8"))
        assert again["missing"] == got["missing"] and again["drift"] == got["drift"] and again["extra"] == got["extra"]
        status, dry = _get(server, "/api/setup/dry-run")
        assert status == 200 and len(calls) == first
        assert dry.startswith(b"dry run\n") and dry.endswith(b"nothing installed\n")
        assert b"github-cli  brew  fc4b137cdef0a6bd28fd461b7cf9c84a5812a8cd\n" in dry
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

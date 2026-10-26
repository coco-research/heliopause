import json
import stat
import sys
import threading
from contextlib import contextmanager

from atlas.__main__ import make_server
from atlas.ring import _osascript, ring
from test_alerts import post
from test_write import token_of


def _alert(ident, severity="needs-owner", text="Needs you", source="lab"):
    return {"id": ident, "severity": severity, "text": text, "source": source}


def _fake():
    calls = []
    return calls, (lambda title, text: calls.append((title, text)))


@contextmanager
def _served(root):
    server = make_server(root, 0, watch=False, ring=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_one_ring_per_id(tmp_path):
    calls, notify = _fake()
    assert ring(tmp_path, _alert("a1"), notify=notify) is True
    assert ring(tmp_path, _alert("a1"), notify=notify) is False
    assert len(calls) == 1


def test_seventh_distinct_does_not_ring(tmp_path):
    calls, notify = _fake()
    for i in range(6):
        assert ring(tmp_path, _alert(f"id{i}"), notify=notify) is True
    assert ring(tmp_path, _alert("id6"), notify=notify) is False
    assert len(calls) == 6


def test_info_and_warn_never_ring(tmp_path):
    calls, notify = _fake()
    assert ring(tmp_path, _alert("i1", "info"), notify=notify) is False
    assert ring(tmp_path, _alert("i2", "warn"), notify=notify) is False
    assert calls == []


def test_escaping_and_argv(tmp_path, monkeypatch):
    seen = []
    class _Proc:
        def wait(self):
            seen.append("reaped")

    monkeypatch.setattr("atlas.ring.subprocess.Popen", lambda argv, **kwargs: seen.append((argv, kwargs)) or _Proc())
    assert ring(
        tmp_path,
        {"id": "q1", "severity": "needs-owner", "source": 'a"b\\c', "text": 'say "hi" \\ bye'},
        notify=_osascript,
    ) is True
    argv, kwargs = seen[0]
    assert argv[0] == "osascript"
    assert kwargs.get("shell") is not True
    script = argv[2]
    assert script.startswith('display notification "') and '\\"' in script and "\\\\" in script
    assert script == 'display notification "say \\"hi\\" \\\\ bye" with title "Heliopause: a\\"b\\\\c"'


def test_new_day_resets_count(tmp_path):
    calls, notify = _fake()
    for i in range(6):
        assert ring(tmp_path, _alert(f"d{i}"), notify=notify) is True
    path = tmp_path / ".atlas" / "rings.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["day"] = "2000-01-01"
    path.write_text(json.dumps(state), encoding="utf-8")
    assert ring(tmp_path, _alert("d7"), notify=notify) is True
    assert json.loads(path.read_text(encoding="utf-8"))["count"] == 1


def test_rings_file_is_0600(tmp_path):
    calls, notify = _fake()
    assert ring(tmp_path, _alert("m1"), notify=notify) is True
    assert stat.S_IMODE((tmp_path / ".atlas" / "rings.json").stat().st_mode) == 0o600


def test_server_rings_once_and_not_on_duplicate(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("atlas.ring._osascript", lambda title, text: calls.append((title, text)))
    with _served(tmp_path) as server:
        token = token_of(tmp_path)
        payload = {"source": "lab", "kind": "disk", "severity": "needs-owner", "text": "Owner please"}
        status, _body = post(server, token, payload)
        assert status == 201 and len(calls) == 1
        again, body = post(server, token, payload)
        assert again == 200 and body.get("duplicate") is True and len(calls) == 1


def test_failed_notification_keeps_the_slot(tmp_path):
    def broken(title, text):
        raise OSError("no osascript")

    assert ring(tmp_path, _alert("f1"), notify=broken) is False
    calls, fake = _fake()
    assert ring(tmp_path, _alert("f1"), notify=fake) is True
    assert len(calls) == 1

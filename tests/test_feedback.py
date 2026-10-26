import json
import stat

from test_alerts import BASE, get, post
from test_write import call, served, token_of

GOOD = {"kind": "decision-waiting", "subject": "decision-waiting:brain-atlas", "verdict": "useful"}


def send(server, body, **extra):
    port = server.server_address[1]
    hdrs = {"Origin": f"http://127.0.0.1:{port}", "X-Atlas-UI": server.ui_nonce, "Sec-Fetch-Site": "same-origin", **extra}
    status, raw = call(server, "/api/feedback", body=json.dumps(body).encode(), headers={k: v for k, v in hdrs.items() if v})
    return status, json.loads(raw)


def vote(server, kind="decision-waiting", subject="decision-waiting:brain-atlas", verdict="useful", **extra):
    return send(server, {"kind": kind, "subject": subject, "verdict": verdict}, **extra)


def summary(server):
    return json.loads(get(server, "/api/feedback")[1])


def stored(root):
    path = root / ".atlas" / "feedback.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_gate(tmp_path):
    with served(tmp_path) as server:
        for extra in (
            {"Origin": None}, {"Origin": "https://evil.test"}, {"Origin": "http://127.0.0.1:1"}, {"X-Atlas-UI": "not-the-nonce"},
            {"X-Atlas-UI": None}, {"Sec-Fetch-Site": "cross-site"}, {"Content-Type": "text/plain"},
        ):
            assert send(server, GOOD, **extra)[0] == 403, extra
        # The file token is not a way in: this route is for the page only.
        assert call(server, "/api/feedback", token=token_of(tmp_path), body=json.dumps(GOOD).encode())[0] == 403
        assert stored(tmp_path) == []
        assert vote(server, Origin=f"http://localhost:{server.server_address[1]}")[0] == 201


def test_rejects_bad_bodies(tmp_path):
    bad = [
        {"kind": "Alert"}, {"kind": ""}, {"kind": "-x"}, {"kind": "a" * 41}, {"kind": "a b"}, {"kind": 7}, {"kind": None},
        {"subject": ""}, {"subject": "a" * 121}, {"subject": "a b"}, {"subject": "a?b"}, {"subject": "café"}, {"subject": ["x"]},
        {"verdict": "maybe"}, {"verdict": "Useful"}, {"verdict": ""}, {"verdict": 1}, {"verdict": None}, {"verdict": ["useful"]},
    ]
    with served(tmp_path) as server:
        assert [send(server, {**GOOD, **patch})[0] for patch in bad] == [400] * len(bad)
        short = [{k: GOOD[k] for k in keys} for keys in ((), ("kind",), ("kind", "subject"), ("kind", "verdict"))]
        assert [send(server, body)[0] for body in short] == [400] * len(short)
        assert stored(tmp_path) == []
        assert vote(server, kind="a" * 40, subject=("Ab0._:#/-" * 14)[:120], verdict="not-useful")[0] == 201  # longest, fits the gate
        assert vote(server, kind="0.x_y-z", subject="s")[0] == 201


def test_text_is_never_stored(tmp_path):
    with served(tmp_path) as server:
        status, alert = post(server, token_of(tmp_path), {**BASE, "severity": "needs-owner", "text": "Secret headline"})
        assert status == 201
        subject = "alert:" + alert["id"]
        for key in ("text", "headline", "ts", "state"):
            assert send(server, {"kind": "alert", "subject": subject, "verdict": "useful", key: "Secret headline"})[0] == 400, key
        assert stored(tmp_path) == []
        assert vote(server, "alert", subject)[0] == 201
        assert "Secret headline" not in (tmp_path / ".atlas" / "feedback.jsonl").read_text(encoding="utf-8")
        assert "Secret headline" not in json.dumps(summary(server))


def test_stores_exactly_four_fields_at_0600(tmp_path):
    with served(tmp_path) as server:
        assert vote(server, "ci-red", "ci-red:coco-atlas#108", "not-useful") == (201, {"ok": True})
        assert stat.S_IMODE((tmp_path / ".atlas" / "feedback.jsonl").stat().st_mode) == 0o600
        (row,) = stored(tmp_path)
        assert list(row) == ["ts", "kind", "subject", "verdict"]
        assert isinstance(row["ts"], int) and not isinstance(row["ts"], bool)
        assert (row["kind"], row["subject"], row["verdict"]) == ("ci-red", "ci-red:coco-atlas#108", "not-useful")


def test_summary_counts_and_latest_wins(tmp_path):
    with served(tmp_path) as server:
        assert summary(server) == {"kinds": {}, "latest": []}
        for kind, subject, verdict in (
            ("decision-waiting", "decision-waiting:a", "useful"), ("decision-waiting", "decision-waiting:b", "useful"),
            ("alert", "alert:x", "not-useful"), ("decision-waiting", "decision-waiting:a", "not-useful"),  # a changes its mind
            ("alert", "alert:x", "not-useful"),  # a repeat is still one subject
            ("other", "alert:x", "useful"),  # the same subject under another kind is another item
        ):
            assert vote(server, kind, subject, verdict)[0] == 201
        got = summary(server)
        assert got["kinds"] == {
            "decision-waiting": {"useful": 1, "not-useful": 1}, "alert": {"useful": 0, "not-useful": 1}, "other": {"useful": 1, "not-useful": 0},
        }
        assert [(r["kind"], r["subject"], r["verdict"]) for r in got["latest"]] == [
            ("decision-waiting", "decision-waiting:b", "useful"), ("decision-waiting", "decision-waiting:a", "not-useful"),
            ("alert", "alert:x", "not-useful"), ("other", "alert:x", "useful"),
        ]
        for i in range(25):
            assert vote(server, "quiet", f"quiet:n{i}")[0] == 201
        got = summary(server)
        assert [r["subject"] for r in got["latest"]] == [f"quiet:n{i}" for i in range(5, 25)]
        assert got["kinds"]["quiet"] == {"useful": 25, "not-useful": 0}


def test_summary_skips_foreign_lines(tmp_path):
    with served(tmp_path) as server:
        good = {"ts": 5, "kind": "alert", "subject": "alert:x", "verdict": "useful"}
        rows = [
            "not json", json.dumps([1]), json.dumps({**good, "ts": True}), json.dumps({**good, "verdict": "meh"}),
            json.dumps({**good, "kind": "Bad"}), json.dumps({"kind": "alert"}), json.dumps({**good, "text": "leak"}),
        ]
        (tmp_path / ".atlas" / "feedback.jsonl").write_text("\n".join(rows) + '\n{"ts": 1, "kind"', encoding="utf-8")
        assert summary(server) == {"kinds": {"alert": {"useful": 1, "not-useful": 0}}, "latest": [good]}  # the leak is dropped
        assert vote(server, "alert", "alert:y")[0] == 201  # the torn last line does not swallow the new row
        assert [r["subject"] for r in summary(server)["latest"]] == ["alert:x", "alert:y"]


def test_rotation_keeps_the_history_readable(tmp_path, monkeypatch):
    monkeypatch.setattr("atlas.alerts.ROTATE_BYTES", 1)
    with served(tmp_path) as server:
        for name in ("a", "b", "c"):
            assert vote(server, "quiet", f"quiet:{name}")[0] == 201
        assert (tmp_path / ".atlas" / "feedback.jsonl.1").is_file()
        assert [r["subject"] for r in summary(server)["latest"]] == ["quiet:b", "quiet:c"]

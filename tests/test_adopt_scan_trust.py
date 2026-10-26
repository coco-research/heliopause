"""--apply trusts adopt-scan.json only when it is ours, 0600, current, for this home, and not a symlink."""

import json
import os
import time
from pathlib import Path

import pytest

from atlas import adopt

PLANTED = "/planted/by/someone/else"


def _home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "home"
    (root / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", lambda: root)
    return root


def _payload(home: Path, **over) -> dict:
    data = {
        "folders": {},
        "suggest_ignore": [{"path": PLANTED, "reason": "inactive"}],
        "schema": 1,
        "created": int(time.time()),
        "home": str(home.resolve()),
    }
    data.update(over)
    return data


def _write(path: Path, data: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(mode)


def _apply(home: Path, lab: Path, *extra: str) -> int:
    return adopt.run(["--apply", "--home", str(home), "--lab", str(lab), "--lead-name", "Ada", *extra])


def _ignore(lab: Path) -> str:
    path = lab / ".atlas" / "ignore"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_a_current_scan_is_used(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    _write(lab / ".atlas" / "adopt-scan.json", _payload(home))
    assert _apply(home, lab, "--yes") == 0
    assert PLANTED in _ignore(lab)


@pytest.mark.parametrize(
    "case",
    ["symlink", "mode-0644", "no-schema", "other-schema", "other-home", "stale", "no-created"],
)
def test_an_untrusted_scan_is_rejected_and_rescanned(tmp_path, monkeypatch, capsys, case):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    scan = lab / ".atlas" / "adopt-scan.json"
    data = _payload(home)
    mode = 0o600
    if case == "mode-0644":
        mode = 0o644
    elif case == "no-schema":
        del data["schema"]
    elif case == "other-schema":
        data["schema"] = 99
    elif case == "other-home":
        data["home"] = str(tmp_path / "someone")
    elif case == "stale":
        data["created"] = int(time.time()) - 8 * 24 * 3600
    elif case == "no-created":
        del data["created"]
    if case == "symlink":
        outside = tmp_path / "outside.json"
        _write(outside, data)
        scan.parent.mkdir(parents=True)
        scan.symlink_to(outside)
        # Rejected as a scan, and the re-scan refuses to write through or replace the link.
        assert _apply(home, lab, "--yes") == 1
        err = capsys.readouterr().err
        assert "ignoring the saved scan" in err and "is a symlink" in err
        assert scan.is_symlink() and not (lab / "atlas.json").exists()
        assert json.loads(outside.read_text(encoding="utf-8")) == data
        return
    _write(scan, data, mode)
    assert _apply(home, lab, "--yes") == 0
    err = capsys.readouterr().err
    assert "ignoring the saved scan" in err
    assert PLANTED not in _ignore(lab)
    assert not scan.is_symlink()
    assert scan.stat().st_mode & 0o777 == 0o600
    fresh = json.loads(scan.read_text(encoding="utf-8"))
    assert fresh["schema"] == 1 and fresh["home"] == str(home.resolve())


def test_a_stale_scan_asks_again_and_no_keeps_everything(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    scan = lab / ".atlas" / "adopt-scan.json"
    _write(scan, _payload(home, created=int(time.time()) - 30 * 24 * 3600))
    before = scan.read_bytes()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert _apply(home, lab) == 1
    out = capsys.readouterr().out
    assert "no usable scan" in out
    assert out.splitlines()[-1] == "Nothing changed yet."
    assert scan.read_bytes() == before
    assert not (lab / "atlas.json").exists()


@pytest.mark.parametrize("breaker", ["\n", "\r", "\x0b", "\x1c", "\u2028", "\x85", "\x1b[2J"])
def test_control_characters_cannot_add_ignore_rows(tmp_path, monkeypatch, breaker):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    sneaky = f"/real/folder{breaker}/etc\tinactive"
    _write(lab / ".atlas" / "adopt-scan.json", _payload(home, suggest_ignore=[
        {"path": sneaky, "reason": "inactive"},
        {"path": "/plain/folder", "reason": "missing"},
    ]))
    assert _apply(home, lab, "--yes") == 0
    rows = _ignore(lab).splitlines()
    assert rows == ["/plain/folder\tmissing"]


def test_scan_refuses_a_symlink_at_the_report_path(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n", encoding="utf-8")
    victim.chmod(0o644)
    (lab / ".atlas").mkdir(parents=True)
    (lab / ".atlas" / "adopt-scan.json").symlink_to(victim)
    assert adopt.run(["--scan", "--home", str(home), "--lab", str(lab)]) == 1
    assert "is a symlink" in capsys.readouterr().err
    assert victim.read_text(encoding="utf-8") == "keep\n"
    assert victim.stat().st_mode & 0o777 == 0o644
    assert (lab / ".atlas" / "adopt-scan.json").is_symlink()
    assert [p.name for p in (lab / ".atlas").iterdir()] == ["adopt-scan.json"]


def test_scan_report_has_schema_home_and_time(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    assert adopt.run(["--scan", "--home", str(home), "--lab", str(lab)]) == 0
    report = lab / ".atlas" / "adopt-scan.json"
    assert not report.is_symlink() and report.stat().st_mode & 0o777 == 0o600
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["schema"] == 1 and data["home"] == str(home.resolve())
    assert abs(data["created"] - time.time()) < 60


def test_write_report_is_0600_even_with_a_loose_umask(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    old = os.umask(0)
    try:
        dest = adopt.write_report(tmp_path / "lab", {"folders": {}})
    finally:
        os.umask(old)
    assert dest.stat().st_mode & 0o777 == 0o600


def test_a_hard_linked_scan_is_rejected_before_it_is_read(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    other = tmp_path / "transcript.jsonl"
    _write(other, _payload(home))
    (lab / ".atlas").mkdir(parents=True)
    os.link(other, lab / ".atlas" / "adopt-scan.json")
    reads = []
    real_read = os.read
    monkeypatch.setattr(adopt.os, "read", lambda fd, n: reads.append(fd) or real_read(fd, n))
    data, why = adopt.load_scan(lab / ".atlas" / "adopt-scan.json", home)
    assert data is None and "hard-linked" in why
    assert reads == []


def test_an_oversized_scan_is_rejected_before_it_is_read(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    scan = lab / ".atlas" / "adopt-scan.json"
    _write(scan, _payload(home))
    with open(scan, "ab") as fh:
        fh.truncate(adopt.SCAN_MAX_BYTES + 1)
    reads = []
    real_read = os.read
    monkeypatch.setattr(adopt.os, "read", lambda fd, n: reads.append(fd) or real_read(fd, n))
    data, why = adopt.load_scan(scan, home)
    assert data is None and "larger than" in why
    assert reads == []


@pytest.mark.parametrize("path", ["/x/$(touch pwned)", "/x/`id`", "/x/a;b", "/x/it's here"])
def test_plan_shows_ignore_rows_shell_quoted(tmp_path, monkeypatch, capsys, path):
    import shlex

    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    _write(lab / ".atlas" / "adopt-scan.json", _payload(home, suggest_ignore=[{"path": path, "reason": "inactive"}]))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert _apply(home, lab) == 1
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.endswith(" — inactive for more than 60 days"))
    shown = row[: -len(" — inactive for more than 60 days")]
    assert shown == shlex.quote(path) and shlex.split(shown) == [path]


def test_plain_ignore_rows_are_shown_unchanged(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    _write(lab / ".atlas" / "adopt-scan.json", _payload(home, suggest_ignore=[{"path": "/x/plain-dir", "reason": "missing"}]))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert _apply(home, lab) == 1
    assert "/x/plain-dir — path no longer exists" in capsys.readouterr().out


def test_scan_report_shows_ignore_rows_shell_quoted(tmp_path):
    report = {
        "summary": {"folders": 0, "chats": 0, "active_30d": 0},
        "recent": [], "rules": [], "hooks": [], "mcp_servers": {"claude": [], "cursor": []},
        "plugins": [], "conflicts": [],
        "suggest_ignore": [{"path": "/x/$(id)", "reason": "missing"}],
    }
    text = adopt.render(report, tmp_path / "r.json")
    assert "'/x/$(id)' — path no longer exists" in text

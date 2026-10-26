"""GitHub PR/CI stays off unless the lab atlas.json opts in with a JSON boolean true."""

from __future__ import annotations

import subprocess

import pytest

from atlas import adopt
from atlas.__main__ import make_server
from atlas.github import enabled


def _refuse_gh(real):
    def wrapped(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and argv and argv[0] == "gh":
            raise AssertionError(argv)
        if argv == "gh":
            raise AssertionError(argv)
        return real(argv, *args, **kwargs)

    return wrapped


class _Poller:
    def __init__(self, *args, **kwargs):
        self.events = ["init"]

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")


def _pin_root(monkeypatch) -> None:
    monkeypatch.setattr("atlas.__main__.LAB", False)
    monkeypatch.setattr("atlas.__main__.LAB_DIR", None)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        '{"v": 1}',
        '{"github": {"enabled": false}}',
        '{"github": {"enabled": "true"}}',
        "{",
    ],
)
def test_enabled_off(tmp_path, raw):
    if raw is None:
        assert enabled(tmp_path / "missing") is False
        return
    (tmp_path / "atlas.json").write_text(raw, encoding="utf-8")
    assert enabled(tmp_path) is False


def test_enabled_on(tmp_path):
    (tmp_path / "atlas.json").write_text('{"github": {"enabled": true}}\n', encoding="utf-8")
    assert enabled(tmp_path) is True


def test_off_never_calls_gh(tmp_path, monkeypatch):
    run, popen = subprocess.run, subprocess.Popen
    monkeypatch.setattr(subprocess, "run", _refuse_gh(run))
    monkeypatch.setattr(subprocess, "Popen", _refuse_gh(popen))
    _pin_root(monkeypatch)
    server = make_server(tmp_path, 0)
    try:
        assert server.poller is None
    finally:
        server.server_close()
    lab = tmp_path / "lab"
    proj = tmp_path / "widget"
    lab.mkdir()
    proj.mkdir()
    (lab / "atlas.json").write_text("{}\n", encoding="utf-8")
    assert adopt.run(["--mark", str(proj), "--lab", str(lab)]) == 0
    stub = lab / "projects" / adopt.project_slug(proj) / "START-HERE.md"
    assert "Open pull requests" not in stub.read_text(encoding="utf-8")


def test_on_constructs_the_poller(tmp_path, monkeypatch):
    (tmp_path / "atlas.json").write_text('{"github": {"enabled": true}}\n', encoding="utf-8")
    _pin_root(monkeypatch)
    monkeypatch.setattr("atlas.__main__.Poller", _Poller)
    server = make_server(tmp_path, 0)
    try:
        assert isinstance(server.poller, _Poller)
        assert server.poller.events == ["init", "start"]
    finally:
        server.server_close()


def test_demo_never_constructs_the_poller(tmp_path, monkeypatch):
    (tmp_path / "atlas.json").write_text('{"github": {"enabled": true}}\n', encoding="utf-8")
    _pin_root(monkeypatch)
    monkeypatch.setattr("atlas.__main__.Poller", _Poller)
    server = make_server(tmp_path, 0, demo=True)
    try:
        assert server.poller is None
    finally:
        server.server_close()


def test_lab_setting_wins_over_the_server_root(tmp_path, monkeypatch):
    lab = tmp_path / "lab"
    root = tmp_path / "root"
    lab.mkdir()
    root.mkdir()
    (lab / "atlas.json").write_text("{}\n", encoding="utf-8")
    (root / "atlas.json").write_text('{"github": {"enabled": true}}\n', encoding="utf-8")
    run, popen = subprocess.run, subprocess.Popen
    monkeypatch.setattr(subprocess, "run", _refuse_gh(run))
    monkeypatch.setattr(subprocess, "Popen", _refuse_gh(popen))
    monkeypatch.setattr("atlas.__main__.LAB", True)
    monkeypatch.setattr("atlas.__main__.LAB_DIR", lab)
    monkeypatch.setattr("atlas.__main__.Poller", _Poller)
    server = make_server(root, 0)
    try:
        assert server.poller is None
    finally:
        server.server_close()
    (lab / "atlas.json").write_text('{"github": {"enabled": true}}\n', encoding="utf-8")
    again = make_server(root, 0)
    try:
        assert isinstance(again.poller, _Poller)
        assert again.poller.events == ["init", "start"]
    finally:
        again.server_close()

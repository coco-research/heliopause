"""dispatch runs only coco-route or ocr-stamp from one trusted folder, and never a routes.json reached through a lab."""

import json
import os

import pytest

from atlas import dispatch, init

_TOOL = '#!/bin/sh\necho "$0 $*" >> "{marker}"\nexit 0\n'


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ATLAS_LAB", raising=False)
    monkeypatch.delenv("ATLAS_DISPATCH_BIN_DIR", raising=False)
    monkeypatch.setattr("atlas.collect.LAB", False)
    return home


def _tools(folder, marker, names=("coco-route", "ocr-stamp")):
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        tool = folder / name
        tool.write_text(_TOOL.format(marker=marker), encoding="utf-8")
        tool.chmod(0o755)
    return folder


def _routes(usage, run):
    usage.mkdir(parents=True, exist_ok=True)
    table = {"v": 1, "types": {"build": {"mode": "read", "lanes": [{"run": run, "gate": "glm"}]}}}
    (usage / "routes.json").write_text(json.dumps(table), encoding="utf-8")
    (usage / "lanes.json").write_text(json.dumps({"lanes": {"glm": {"state": "live"}}}), encoding="utf-8")


def _dispatch(home, tmp_path):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return dispatch.main(["build", "--root", str(home), "--cwd", str(work), "--", "do it"])


def _usage(home):
    return home / ".heliopause" / "inbox" / "lab" / "usage"


def test_allowlisted_tool_runs_from_the_trusted_folder(home, tmp_path):
    marker = tmp_path / "ran"
    _tools(home / ".local" / "bin", marker)
    _routes(_usage(home), ["coco-route", "glm"])
    assert _dispatch(home, tmp_path) == 0
    assert marker.read_text(encoding="utf-8").startswith(str(home / ".local" / "bin" / "coco-route") + " glm -- do it")


@pytest.mark.parametrize("run0", ["sh", "/bin/sh", "python3", "bus-ask", "./coco-route", "{bin}/coco-route"])
def test_lane_command_outside_the_allowlist_never_runs(home, tmp_path, run0, capsys):
    marker = tmp_path / "ran"
    folder = _tools(home / ".local" / "bin", marker, ("coco-route", "ocr-stamp", "sh", "python3", "bus-ask"))
    _routes(_usage(home), [run0.format(bin=folder), "-c", f"echo x >> {marker}"])
    assert _dispatch(home, tmp_path) == 3
    assert not marker.exists()
    assert "is not a lane tool" in capsys.readouterr().err


def test_path_is_never_searched(home, tmp_path, monkeypatch, capsys):
    marker = tmp_path / "ran"
    elsewhere = _tools(tmp_path / "on-path", marker)
    monkeypatch.setenv("PATH", f"{elsewhere}:/usr/bin:/bin")
    _routes(_usage(home), ["coco-route", "glm"])
    assert _dispatch(home, tmp_path) == 3
    assert not marker.exists()
    assert "coco-route not found in" in capsys.readouterr().err


@pytest.fixture
def lab(home, tmp_path):
    path = tmp_path / "lab"
    assert init.run(["--yes", "--dir", str(path)]) == 0
    return path


def test_tool_folder_inside_a_lab_is_refused(home, lab, tmp_path, monkeypatch, capsys):
    marker = tmp_path / "ran"
    monkeypatch.setenv("ATLAS_DISPATCH_BIN_DIR", str(_tools(lab / "bin", marker)))
    _routes(_usage(home), ["coco-route", "glm"])
    assert _dispatch(home, tmp_path) == 3
    assert not marker.exists()
    assert "resolves into the lab" in capsys.readouterr().err


def test_tool_linked_into_a_lab_is_refused(home, lab, tmp_path, capsys):
    marker = tmp_path / "ran"
    _tools(lab / "bin", marker)
    folder = home / ".local" / "bin"
    folder.mkdir(parents=True)
    (folder / "coco-route").symlink_to(lab / "bin" / "coco-route")
    _routes(_usage(home), ["coco-route", "glm"])
    assert _dispatch(home, tmp_path) == 3
    assert not marker.exists()
    assert "resolves into the lab" in capsys.readouterr().err


@pytest.mark.parametrize("what", ["folder", "tool"])
def test_tool_writable_by_others_is_refused(home, tmp_path, what, capsys):
    marker = tmp_path / "ran"
    folder = _tools(home / ".local" / "bin", marker)
    (folder if what == "folder" else folder / "coco-route").chmod(0o777)
    _routes(_usage(home), ["coco-route", "glm"])
    assert _dispatch(home, tmp_path) == 3
    assert not marker.exists()
    assert "writable by other users" in capsys.readouterr().err


@pytest.mark.parametrize("link", [".heliopause/inbox/lab", ".heliopause/inbox/lab/usage", ".heliopause/inbox"])
def test_routes_reached_through_a_link_into_the_lab_is_refused(home, lab, tmp_path, link, capsys):
    # a reviewer's repro: a link at ~/.heliopause/inbox/lab into the lab got the lane command run.
    marker = tmp_path / "ran"
    _tools(home / ".local" / "bin", marker)
    planted = lab / "queue" / "planted"
    depth = len(link.split("/"))
    tail = ".heliopause/inbox/lab/usage".split("/")[depth:]
    _routes(planted.joinpath(*tail), ["coco-route", "glm"])
    spot = home / link
    spot.parent.mkdir(parents=True, exist_ok=True)
    spot.symlink_to(planted, target_is_directory=True)
    assert dispatch._table(home) is None
    assert "links into the lab" in capsys.readouterr().err
    assert _dispatch(home, tmp_path) == 2
    assert not marker.exists()


def test_routes_file_linked_into_the_lab_is_refused(home, lab, tmp_path, capsys):
    usage = _usage(home)
    _routes(lab / "queue" / "u", ["coco-route", "glm"])
    usage.mkdir(parents=True)
    (usage / "routes.json").symlink_to(lab / "queue" / "u" / "routes.json")
    assert dispatch._table(home) is None
    assert "links into the lab" in capsys.readouterr().err


def test_folder_on_the_way_writable_by_others_is_refused(home, tmp_path, capsys):
    _routes(_usage(home), ["coco-route", "glm"])
    assert dispatch._table(home) is not None
    (home / ".heliopause" / "inbox").chmod(0o777)
    try:
        assert dispatch._table(home) is None
    finally:
        (home / ".heliopause" / "inbox").chmod(0o755)
    assert "writable by other users" in capsys.readouterr().err


def test_link_on_the_way_to_a_safe_folder_is_fine(home, tmp_path):
    real = tmp_path / "elsewhere" / "inbox"
    _routes(real / "lab" / "usage", ["coco-route", "glm"])
    (home / ".heliopause").mkdir()
    (home / ".heliopause" / "inbox").symlink_to(real, target_is_directory=True)
    assert dispatch._table(home) is not None

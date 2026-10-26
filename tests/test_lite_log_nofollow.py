"""Dev only (L11): the lite recall log never writes through a symlink at or below the lite home."""

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "m0-lite"
pytestmark = pytest.mark.skipif(not _VENDOR.is_dir(), reason="dev checkout only (vendor/ is not shipped)")


@pytest.fixture
def lanes(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(_VENDOR))
    home = tmp_path / "lite"
    monkeypatch.setenv("M0_LITE_HOME", str(home))
    module = importlib.import_module("m0lite_recall_lanes")
    return module, home


def _outside(tmp_path):
    spot = tmp_path / "lab" / "notes"
    spot.mkdir(parents=True)
    return spot


def test_log_is_written_normally(lanes):
    module, home = lanes
    module.log_call("s1", "none", False, 0, 3)
    log = home / "logs" / "push-recall.jsonl"
    assert json.loads(log.read_text(encoding="utf-8"))["session_id"] == "s1"
    assert oct(log.stat().st_mode & 0o777) == "0o600" and oct((home / "logs").stat().st_mode & 0o777) == "0o700"


def test_linked_logs_folder_is_not_written_through(lanes, tmp_path):
    module, home = lanes
    spot = _outside(tmp_path)
    home.mkdir()
    (home / "logs").symlink_to(spot, target_is_directory=True)
    module.log_call("s2", "none", False, 0, 3)
    assert list(spot.iterdir()) == []


def test_linked_log_file_is_not_written_through(lanes, tmp_path):
    module, home = lanes
    target = _outside(tmp_path) / "board.md"
    target.write_text("keep\n", encoding="utf-8")
    (home / "logs").mkdir(parents=True)
    (home / "logs" / "push-recall.jsonl").symlink_to(target)
    module.log_call("s3", "none", False, 0, 3)
    assert target.read_text(encoding="utf-8") == "keep\n"


def test_hard_linked_log_file_is_not_written_through(lanes, tmp_path):
    module, home = lanes
    target = _outside(tmp_path) / "board.md"
    target.write_text("keep\n", encoding="utf-8")
    (home / "logs").mkdir(parents=True)
    os.link(target, home / "logs" / "push-recall.jsonl")
    module.log_call("s4", "none", False, 0, 3)
    assert target.read_text(encoding="utf-8") == "keep\n"


def test_linked_lite_home_is_not_written_through(lanes, tmp_path):
    module, home = lanes
    spot = _outside(tmp_path)
    home.symlink_to(spot, target_is_directory=True)
    module.log_call("s5", "none", False, 0, 3)
    assert list(spot.iterdir()) == []

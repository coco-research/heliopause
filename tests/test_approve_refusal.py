"""approve never records a yes. K7 off refuses everyone; K7 on still refuses planner and builder."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from atlas.cli import main

ROOT = Path(__file__).resolve().parents[1]
_REFUSED = "Refused: only the user approves, in the Heliopause page. Your answer is a recommendation."
_LATER = "Refused: approve from the command line is not available in this version."


def _lab(tmp_path: Path, **extra: object) -> Path:
    lab = tmp_path / "lab"
    lab.mkdir()
    base = {"v": 1, "owner": "Owner", "tz": "UTC", "lead": "Ada", "nodes": []}
    (lab / "atlas.json").write_text(json.dumps({**base, **extra}), encoding="utf-8")
    (lab / "README.md").write_text("marker\n", encoding="utf-8")
    return lab


def _snapshot(root: Path) -> list:
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        folder = Path(dirpath)
        info = folder.lstat()
        rows.append(("dir", str(folder.relative_to(root)), info.st_mtime_ns, info.st_mode))
        for name in sorted(filenames):
            path = folder / name
            st = path.lstat()
            if stat.S_ISLNK(st.st_mode):
                blob = os.readlink(path)
            elif stat.S_ISREG(st.st_mode):
                blob = path.read_bytes()
            else:
                blob = None
            rows.append(("file", str(path.relative_to(root)), st.st_mtime_ns, st.st_mode, blob))
    return rows


@pytest.mark.parametrize("flag", ["--yes", "--no"])
@pytest.mark.parametrize("actor", ["planner", "builder", "reviewer", "user", None])
def test_approve_refused_when_k7_off(tmp_path: Path, capsys, monkeypatch, flag: str, actor: str | None) -> None:
    lab = _lab(tmp_path, roles={"hq": "grok", "lab": "claude-code", "factory": ["cursor"]})
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    before = _snapshot(lab)
    argv = ["approve", "ask-1", flag]
    if actor is not None:
        argv.extend(["--as", actor])
    if actor == "builder":
        argv.extend(["--reason", "not yet"])
    assert main(argv) == 3
    out, err = capsys.readouterr()
    assert out == "" and err == _REFUSED + "\n"
    assert _snapshot(lab) == before


@pytest.mark.parametrize("actor", ["planner", "builder"])
def test_approve_refused_for_planner_and_builder_when_k7_on(
    tmp_path: Path, capsys, monkeypatch, actor: str
) -> None:
    lab = _lab(
        tmp_path,
        roles={"hq": "grok", "lab": "claude-code", "factory": ["cursor"]},
        k7={"enabled": True, "repos": ["coco", "atlas"]},
    )
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    before = _snapshot(lab)
    assert main(["approve", "ask-2", "--yes", "--as", actor, "--reason", "later"]) == 3
    out, err = capsys.readouterr()
    assert out == "" and err == _REFUSED + "\n"
    assert _snapshot(lab) == before


def test_approve_reviewer_cli_unavailable_when_k7_on(tmp_path: Path, capsys, monkeypatch) -> None:
    lab = _lab(
        tmp_path,
        roles={"hq": "grok", "lab": "claude-code", "factory": ["cursor"]},
        k7={"enabled": True, "repos": ["coco"]},
    )
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    before = _snapshot(lab)
    assert main(["approve", "ask-3", "--no", "--as", "reviewer"]) == 3
    out, err = capsys.readouterr()
    assert out == "" and err == _LATER + "\n"
    assert _snapshot(lab) == before


def test_approve_dispatches_from_atlas_module(tmp_path: Path) -> None:
    lab = _lab(tmp_path, roles={"hq": "you", "lab": "claude-code", "factory": ["codex"]})
    env = os.environ.copy()
    env["ATLAS_LAB"] = str(lab)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    before = _snapshot(lab)
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "atlas", "approve", "ask-9", "--no", "--as", "planner"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 3
    assert proc.stdout == ""
    assert proc.stderr == _REFUSED + "\n"
    assert _snapshot(lab) == before

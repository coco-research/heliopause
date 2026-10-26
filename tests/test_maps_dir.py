"""The suite reads maps from ATLAS_MAPS_DIR, so a public build can ship them as {}."""

import os
import subprocess
import sys
from pathlib import Path

MAPS = (
    "legacy.json",
    "aliases.json",
    "chats.json",
    "repos.json",
    "nodes.json",
    "directory.json",
)
ROOT = Path(__file__).resolve().parents[1]


def test_empty_public_maps_import(tmp_path):
    for name in MAPS:
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env["ATLAS_MAPS_DIR"] = str(tmp_path)
    help_run = subprocess.run(
        [sys.executable, "-m", "atlas", "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert help_run.returncode == 0, help_run.stderr
    imported = subprocess.run(
        [
            sys.executable,
            "-c",
            "import atlas.brief, atlas.chats, atlas.collect, atlas.events, atlas.write, atlas.github",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert imported.returncode == 0, imported.stderr


def test_map_path_honours_env_and_falls_back(tmp_path, monkeypatch):
    import atlas.collect as collect
    from atlas.collect import map_path

    monkeypatch.setenv("ATLAS_MAPS_DIR", str(tmp_path))
    assert map_path("aliases.json") == Path(os.environ["ATLAS_MAPS_DIR"]) / "aliases.json"

    package = Path(collect.__file__).with_name("aliases.json")
    monkeypatch.delenv("ATLAS_MAPS_DIR", raising=False)
    assert map_path("aliases.json") == package
    monkeypatch.setenv("ATLAS_MAPS_DIR", "")
    assert map_path("aliases.json") == package

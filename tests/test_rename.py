"""User-facing name is Heliopause. `atlas` remains a hidden alias."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from atlas import plugin

ROOT = Path(__file__).resolve().parents[1]
_ATLAS_WORD = re.compile(r"\batlas\b", re.IGNORECASE)


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("ATLAS_LAB", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-m", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _help_names_heliopause(proc: subprocess.CompletedProcess[str]) -> None:
    text = proc.stdout + proc.stderr
    assert proc.returncode == 0, text
    assert "heliopause" in text.lower()
    assert _ATLAS_WORD.search(text) is None, text


def test_heliopause_help_does_not_say_atlas(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _help_names_heliopause(_run(home, "heliopause", "--help"))
    _help_names_heliopause(_run(home, "heliopause", "brief", "--help"))


def test_atlas_module_is_a_hidden_alias(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    proc = _run(home, "atlas", "--help")
    assert proc.returncode == 0, proc.stderr
    assert "heliopause" in (proc.stdout + proc.stderr).lower()


def test_plugin_identity_is_heliopause() -> None:
    assert plugin._MARKET_NAME == "heliopause-local"
    assert plugin._PLUGIN_ID == "heliopause@heliopause-local"
    manifest = json.loads((ROOT / "plugin" / "heliopause" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "heliopause"
    assert not (ROOT / "plugin" / "atlas").exists()


def _home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def test_drop_market_leaves_a_stray_folder(tmp_path: Path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    stray = home / "Heliopause" / "marketplace" / "notes.txt"
    stray.parent.mkdir(parents=True)
    stray.write_text("keep", encoding="utf-8")
    assert plugin._drop_market() == 1
    assert stray.read_text(encoding="utf-8") == "keep"


def test_drop_market_removes_what_build_made(tmp_path: Path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    root = plugin._build()
    assert root == home / "Heliopause" / "marketplace"
    assert plugin._drop_market() == 0
    assert not root.exists()

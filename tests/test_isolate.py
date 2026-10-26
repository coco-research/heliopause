"""Atlas launches must not import a cwd or PYTHONPATH atlas package."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from atlas import hooks, init


def _shadow(tmp_path: Path) -> tuple[Path, Path]:
    shadow = tmp_path / "shadow"
    marker = tmp_path / "marker"
    (shadow / "atlas").mkdir(parents=True)
    (shadow / "atlas" / "__init__.py").write_text(
        "open(%r, 'w', encoding='utf-8').write('shadow')\n" % str(marker),
        encoding="utf-8",
    )
    return shadow, marker


def _child_env(home: Path, shadow: Path, path: str | None = None) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(shadow)
    if path is not None:
        env["PATH"] = path
    for key in ("ATLAS_LAB", "ATLAS_URL", "ATLAS_TOKEN_FILE", "PYTHONDONTWRITEBYTECODE"):
        env.pop(key, None)
    return env


def test_wrapper_ignores_shadow_package(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    shadow, marker = _shadow(tmp_path)
    wrap = lab / "bin" / "atlas"
    text = wrap.read_text(encoding="utf-8")
    assert "PYTHONPATH" not in text and "-I -B" in text
    res = subprocess.run(
        [str(wrap), "--help"],
        cwd=shadow,
        env=_child_env(home, shadow),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    assert "usage:" in res.stdout.lower()
    assert not marker.exists()


def _hook_bodies(text: str) -> list[str]:
    prefix, suffix = '(cd "$REPO_DIR" && ', ") || true"
    bodies = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix) and stripped.endswith(suffix):
            bodies.append(stripped[len(prefix):-len(suffix)])
    return bodies


def test_hook_lines_ignore_shadow_package(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    hooks.run(["--lab", str(lab), "--snippet", "--yes"])
    script = (home / hooks.hook_rels(lab)[0]).read_text(encoding="utf-8")
    repo_line = next(line for line in script.splitlines() if line.startswith("REPO_DIR="))
    bodies = _hook_bodies(script)
    assert len(bodies) == 2 and all("-I -B" in body for body in bodies)
    shadow, marker = _shadow(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    python3 = shutil.which("python3")
    assert python3 is not None
    os.symlink(python3, bindir / "python3")
    timeout = bindir / "timeout"
    timeout.write_text("#!/bin/sh\nshift\nexec \"$@\"\n", encoding="utf-8")
    timeout.chmod(0o755)
    env = _child_env(home, shadow, f"{bindir}:/usr/bin:/bin")
    env["ATLAS_URL"] = "http://127.0.0.1:1"
    for body in bodies:
        if marker.exists():
            marker.unlink()
        # Run the rendered command from the planted folder; skip the script's cd so cwd stays the shadow.
        res = subprocess.run(
            ["/bin/bash", "-c", repo_line + "\n" + body],
            cwd=shadow,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert res.returncode == 0, res.stderr
        assert "not running" in res.stderr, res.stderr
        assert not marker.exists()

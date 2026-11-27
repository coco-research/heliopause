"""~/.atlas and ~/atlas-lab keep working; `heliopause migrate` copies them to the new names, never in place.

Every test runs on a fixture home under tmp_path (HOME is set for the child process): the real ~/.atlas,
~/atlas-lab and the live port are never touched.
"""

import json
import os
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from atlas import paths

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_inherited_paths_env(monkeypatch):
    # Other tests export ATLAS_LAB into this process; the in-process resolver checks below must not see it.
    for name in ("HELIOPAUSE_HOME", "ATLAS_HOME", "HELIOPAUSE_LAB", "ATLAS_LAB"):
        monkeypatch.delenv(name, raising=False)


def _env(home: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ATLAS_", "HELIOPAUSE_"))}
    env.update(HOME=str(home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1", **extra)
    return env


def _run(home: Path, *args: str, module: str = "heliopause", **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", "-m", module, *args], cwd=REPO, env=_env(home), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, **kw,
    )


def _snap(root: Path) -> dict:
    """Every entry below root: kind, mode and content. Equal snapshots mean nothing was written."""
    out = {}
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = ("link", os.readlink(path))
        elif path.is_dir():
            out[rel] = ("dir", stat.S_IMODE(st.st_mode))
        else:
            out[rel] = ("file", stat.S_IMODE(st.st_mode), path.read_bytes())
    return out


@pytest.fixture
def legacy(tmp_path):
    """A fixture home with an old-style ~/.atlas (owner-only) and ~/atlas-lab made by `atlas init`."""
    home = tmp_path / "home"
    home.mkdir()
    done = _run(home, "init", "--dir", str(home / "atlas-lab"), "--yes", module="atlas")
    assert done.returncode == 0, done.stderr
    cfg = home / ".atlas"
    (cfg / "hooks").mkdir(parents=True)
    cfg.chmod(0o700)
    (cfg / "hooks").chmod(0o700)
    (cfg / "token").write_text("not-a-real-token\n")
    (cfg / "token").chmod(0o600)
    (cfg / "hooks" / "atlas-hook-start-0123456789ab").write_text(f"#!/bin/sh\nLAB_DIR={home / 'atlas-lab'}\nexit 0\n")
    (cfg / "hooks" / "atlas-hook-start-0123456789ab").chmod(0o700)
    (cfg / "lab").write_text(f"{home / 'atlas-lab'}\n")
    return home


# ---- resolver --------------------------------------------------------------------------------------------------------


def test_fresh_home_uses_the_new_names(tmp_path, monkeypatch):
    for name in ("HELIOPAUSE_HOME", "ATLAS_HOME", "HELIOPAUSE_LAB", "ATLAS_LAB"):
        monkeypatch.delenv(name, raising=False)
    assert paths.config_dir(tmp_path) == tmp_path / ".heliopause"
    assert paths.default_lab(tmp_path) == tmp_path / "heliopause-lab"
    assert paths.lab_dir(home=tmp_path) == tmp_path / "heliopause-lab"
    assert paths.config_rel(tmp_path) == ".heliopause"
    assert paths.is_legacy_layout(tmp_path)["legacy"] is False


def test_old_folders_are_used_while_they_are_all_there_is(tmp_path, monkeypatch):
    for name in ("HELIOPAUSE_HOME", "ATLAS_HOME", "HELIOPAUSE_LAB", "ATLAS_LAB"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".atlas").mkdir()
    (tmp_path / "atlas-lab").mkdir()
    assert paths.config_dir(tmp_path) == tmp_path / ".atlas"
    assert paths.config_rel(tmp_path) == ".atlas"
    assert paths.default_lab(tmp_path) == tmp_path / "atlas-lab"
    state = paths.is_legacy_layout(tmp_path)
    assert state["legacy"] is True and state["legacy_present"] == [".atlas", "atlas-lab"]
    (tmp_path / ".heliopause").mkdir()
    (tmp_path / "heliopause-lab").mkdir()
    assert paths.config_dir(tmp_path) == tmp_path / ".heliopause"
    assert paths.default_lab(tmp_path) == tmp_path / "heliopause-lab"
    assert paths.is_legacy_layout(tmp_path)["legacy"] is False


def test_lab_order_is_flag_then_env_then_pointer_then_default(tmp_path, monkeypatch):
    for name in ("HELIOPAUSE_HOME", "ATLAS_HOME", "HELIOPAUSE_LAB", "ATLAS_LAB"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".heliopause").mkdir()
    (tmp_path / ".heliopause" / "lab").write_text("/from/pointer\nignored\n")
    assert paths.lab_dir(home=tmp_path) == Path("/from/pointer")
    monkeypatch.setenv("ATLAS_LAB", "/from/atlas-env")
    assert paths.lab_dir(home=tmp_path) == Path("/from/atlas-env")
    monkeypatch.setenv("HELIOPAUSE_LAB", "/from/heliopause-env")
    assert paths.lab_dir(home=tmp_path) == Path("/from/heliopause-env")
    assert paths.lab_dir("/from/flag", home=tmp_path) == Path("/from/flag")


def test_home_env_wins_for_the_config_folder(tmp_path, monkeypatch):
    (tmp_path / ".heliopause").mkdir()
    monkeypatch.setenv("ATLAS_HOME", str(tmp_path / "elsewhere"))
    assert paths.config_dir(tmp_path) == tmp_path / "elsewhere"
    monkeypatch.setenv("HELIOPAUSE_HOME", str(tmp_path / "first"))
    assert paths.config_dir(tmp_path) == tmp_path / "first"


# ---- the old layout keeps working, with zero writes to it ---------------------------------------------------------------


def _serve_state(home: Path, module: str) -> dict:
    """`<module> up` on the fixture home: it must find ~/atlas-lab by itself. Returns /api/state."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-B", "-m", module, "up", "--port", str(port)], cwd=REPO, env=_env(home),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as resp:
                    return json.load(resp)
            except OSError:
                assert proc.poll() is None, proc.stderr.read()
                time.sleep(0.2)
        raise AssertionError("the server never answered")
    finally:
        proc.terminate()
        proc.communicate(timeout=10)


@pytest.mark.parametrize("module", ["heliopause", "atlas"])
def test_both_commands_serve_the_old_layout_and_write_nothing_to_it(legacy, module):
    before_cfg = _snap(legacy / ".atlas")
    before_home = {k for k in _snap(legacy) if not k.startswith("atlas-lab")}
    state = _serve_state(legacy, module)
    assert isinstance(state, dict) and state
    assert _snap(legacy / ".atlas") == before_cfg
    assert {k for k in _snap(legacy) if not k.startswith("atlas-lab")} == before_home
    assert not (legacy / ".heliopause").exists() and not (legacy / "heliopause-lab").exists()


def test_heliopause_module_is_the_atlas_program(tmp_path):
    assert _run(tmp_path, "demo-path").stdout == _run(tmp_path, "demo-path", module="atlas").stdout != ""


def test_atlas_prints_nothing_extra_for_the_old_name(legacy):
    got = _run(legacy, "demo-path", module="atlas")
    assert got.returncode == 0 and got.stderr == "" and got.stdout.count("\n") == 1


# ---- heliopause migrate ----------------------------------------------------------------------------------------------


def test_dry_run_prints_the_plan_and_changes_nothing(legacy):
    before = _snap(legacy)
    for args in (("migrate",), ("migrate", "--dry-run")):
        got = _run(legacy, *args)
        assert got.returncode == 0, got.stderr
        assert "~/.atlas -> ~/.heliopause" in got.stdout and "~/atlas-lab -> ~/heliopause-lab" in got.stdout
        assert "~/.heliopause-backup-" in got.stdout and "heliopause migrate --apply --yes" in got.stdout
        assert "rm -rf ~/.heliopause ~/heliopause-lab" in got.stdout
        assert _snap(legacy) == before


def test_apply_needs_yes(legacy):
    before = _snap(legacy)
    got = _run(legacy, "migrate", "--apply")
    assert got.returncode == 2 and "--yes" in got.stdout
    assert _snap(legacy) == before


def test_apply_copies_with_a_backup_keeps_the_old_folders_and_moves_the_pointer(legacy):
    old_cfg, old_lab = _snap(legacy / ".atlas"), _snap(legacy / "atlas-lab")
    got = _run(legacy, "migrate", "--apply", "--yes")
    assert got.returncode == 0, got.stderr
    assert "Undo: rm -rf ~/.heliopause ~/heliopause-lab" in got.stdout
    # The old folders are exactly as they were.
    assert _snap(legacy / ".atlas") == old_cfg and _snap(legacy / "atlas-lab") == old_lab
    # The new ones hold the same files with the same modes; only the pointer and hook LAB_DIR differ.
    new_cfg = _snap(legacy / ".heliopause")
    assert set(new_cfg) == set(old_cfg)
    for rel, entry in old_cfg.items():
        if rel not in ("lab", "hooks/atlas-hook-start-0123456789ab"):
            assert new_cfg[rel] == entry, rel
    assert new_cfg["lab"][2] == f"{legacy / 'heliopause-lab'}\n".encode()
    assert f"LAB_DIR={legacy / 'heliopause-lab'}\n".encode() in new_cfg["hooks/atlas-hook-start-0123456789ab"][2]
    assert _snap(legacy / "heliopause-lab") == old_lab
    assert stat.S_IMODE((legacy / ".heliopause").stat().st_mode) == 0o700
    assert stat.S_IMODE((legacy / ".heliopause" / "token").stat().st_mode) == 0o600
    assert stat.S_IMODE((legacy / ".heliopause" / "hooks" / "atlas-hook-start-0123456789ab").stat().st_mode) == 0o700
    # The backup is a copy of the old state made first.
    (backup,) = [p for p in legacy.iterdir() if p.name.startswith(".heliopause-backup-")]
    assert _snap(backup / ".atlas") == old_cfg and _snap(backup / "atlas-lab") == old_lab
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    # From now on the new names are the ones used.
    assert paths.lab_dir(home=legacy) == legacy / "heliopause-lab"
    assert paths.is_legacy_layout(legacy)["legacy"] is False


def test_migrated_home_serves_from_the_new_lab(legacy):
    assert _run(legacy, "migrate", "--apply", "--yes").returncode == 0
    old_cfg = _snap(legacy / ".atlas")
    assert _serve_state(legacy, "heliopause")
    assert _snap(legacy / ".atlas") == old_cfg


def test_a_second_apply_never_overwrites(legacy):
    assert _run(legacy, "migrate", "--apply", "--yes").returncode == 0
    (legacy / ".heliopause" / "token").write_text("edited after the move\n")
    after = _snap(legacy)
    got = _run(legacy, "migrate", "--apply", "--yes")
    assert got.returncode == 1 and "already exists" in got.stderr
    assert _snap(legacy) == after


def test_nothing_to_migrate_on_a_fresh_home(tmp_path):
    got = _run(tmp_path, "migrate", "--apply", "--yes")
    assert got.returncode == 0 and "Nothing to migrate" in got.stdout
    assert list(tmp_path.iterdir()) == []


# ---- ADV-1: links and existing targets are refused, the old folders stay as they were -----------------------------------


@pytest.mark.parametrize("where", [".atlas", "atlas-lab", "atlas-lab/queue"])
def test_a_symlink_inside_the_old_folders_is_refused(legacy, tmp_path, where):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("keep out\n")
    (legacy / where / "planted").symlink_to(outside)
    before = _snap(legacy)
    for args in (("migrate", "--dry-run"), ("migrate", "--apply", "--yes")):
        got = _run(legacy, *args)
        assert got.returncode == 1 and "planted" in got.stderr and "Nothing changed" in got.stderr
    assert _snap(legacy) == before
    assert not (legacy / ".heliopause").exists() and not (legacy / "heliopause-lab").exists()
    assert not [p for p in legacy.iterdir() if "backup" in p.name]
    assert (outside / "secret.txt").read_text() == "keep out\n"


def test_a_symlinked_old_folder_is_refused(legacy, tmp_path):
    real = tmp_path / "dotfiles-atlas"
    (legacy / ".atlas").rename(real)
    (legacy / ".atlas").symlink_to(real)
    got = _run(legacy, "migrate", "--apply", "--yes")
    assert got.returncode == 1 and "symlink" in got.stderr
    assert not (legacy / ".heliopause").exists()


@pytest.mark.parametrize("target", [".heliopause", "heliopause-lab"])
def test_an_existing_new_folder_is_never_overwritten(legacy, target):
    (legacy / target).mkdir()
    (legacy / target / "mine.txt").write_text("mine\n")
    before = _snap(legacy)
    got = _run(legacy, "migrate", "--apply", "--yes")
    assert got.returncode == 1 and "already exists" in got.stderr and target in got.stderr
    assert _snap(legacy) == before
    assert not [p for p in legacy.iterdir() if "backup" in p.name]


def test_a_symlink_at_the_new_name_is_not_followed(legacy, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (legacy / ".heliopause").symlink_to(elsewhere)
    got = _run(legacy, "migrate", "--apply", "--yes")
    assert got.returncode == 1
    assert list(elsewhere.iterdir()) == []

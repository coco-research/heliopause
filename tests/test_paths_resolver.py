"""Read-only path resolution, `home=` plumbing, legacy hook replacement, and console-script packaging.

Every test runs on a fixture home under tmp_path (HOME is set for child processes): the real ~/.atlas,
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

from atlas import hooks, labfs, paths

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


def _hook_commands_by_event(settings_path: Path) -> dict[str, list[str]]:
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for event, groups in data.get("hooks", {}).items():
        out[event] = [h["command"] for g in groups for h in g.get("hooks", []) if isinstance(h, dict) and "command" in h]
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


def test_fresh_home_uses_the_new_names(tmp_path):
    assert paths.config_dir(tmp_path) == tmp_path / ".heliopause"
    assert paths.default_lab(tmp_path) == tmp_path / "heliopause-lab"
    assert paths.lab_dir(home=tmp_path) == tmp_path / "heliopause-lab"
    assert paths.config_rel(tmp_path) == ".heliopause"
    assert paths.is_legacy_layout(tmp_path)["legacy"] is False


def test_old_folders_are_used_while_they_are_all_there_is(tmp_path):
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


# ---- the old layout keeps working, with zero writes to it ------------------------------------------------------------


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


# ---- home= plumbing, legacy hook replacement, and console-script packaging -------------------------------------------


def test_labfs_home_accepts_explicit_base_home_for_both_configs(tmp_path):
    home = tmp_path / "fixture-home"
    home.mkdir(mode=0o700)
    home.chmod(0o700)
    lab = home / "heliopause-lab"
    lab.mkdir()
    root, rel = labfs.home(".heliopause/lab", lab=lab, base_home=home)
    assert root == home.resolve() and rel == ".heliopause/lab"
    root2, rel2 = labfs.home(".claude/settings.json", lab=lab, base_home=home)
    assert root2 == home.resolve() and rel2 == ".claude/settings.json"
    with pytest.raises(labfs.Refused):
        labfs.check_lab(home / ".heliopause" / "nested", home=home)


def test_hooks_replaces_legacy_atlas_entries_and_preserves_user_hooks(tmp_path):
    """Running `heliopause hooks --yes` on ~/heliopause-lab replaces old ~/.atlas/hooks entries without duplicates."""
    home = tmp_path / "home"
    home.mkdir()
    old_lab = home / "atlas-lab"
    new_lab = home / "heliopause-lab"
    assert _run(home, "init", "--dir", str(old_lab), "--yes", module="atlas").returncode == 0
    (home / ".atlas").mkdir(mode=0o700)
    (home / ".atlas").chmod(0o700)
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    base_settings = {
        "theme": "dark",
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "/bin/existing-start"}]}],
            "PreToolUse": [{"hooks": [{"type": "command", "command": "/bin/true"}]}],
        },
    }
    settings_path.write_text(json.dumps(base_settings, indent=2) + "\n", encoding="utf-8")
    assert _run(home, "hooks", "--lab", str(old_lab), "--yes", module="atlas").returncode == 0
    assert hooks.has_legacy_hooks(new_lab, home=home) is True

    # Now switch to the new folders without touching settings.json yet, then run `heliopause hooks --yes`.
    assert _run(home, "init", "--dir", str(new_lab), "--yes", module="heliopause").returncode == 0
    (home / ".heliopause").mkdir(mode=0o700)
    (home / ".heliopause").chmod(0o700)
    # Plant a stale old-lab hook copy in ~/.heliopause/hooks/ to confirm hooks.run removes it cleanly.
    stale_rels = hooks.stale_hook_rels(new_lab, home=home)
    assert len(stale_rels) == 2
    (home / ".heliopause" / "hooks").mkdir(mode=0o700)
    (home / ".heliopause" / "hooks").chmod(0o700)
    (home / stale_rels[0]).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (home / stale_rels[0]).chmod(0o700)

    assert hooks.run(["--lab", str(new_lab), "--yes"], home=home) == 0
    cmds = _hook_commands_by_event(settings_path)
    expected_start, expected_recall = hooks.hook_commands(new_lab, cfg=".heliopause", home=home)
    assert cmds["SessionStart"] == ["/bin/existing-start", expected_start]
    assert cmds["UserPromptSubmit"] == [expected_recall]
    assert cmds["PreToolUse"] == ["/bin/true"]
    assert "/.atlas/hooks/" not in settings_path.read_text(encoding="utf-8")
    assert not (home / stale_rels[0]).exists()
    assert hooks.has_legacy_hooks(new_lab, home=home) is False


def test_pyproject_exposes_only_heliopause_script():
    """pyproject.toml exposes only the `heliopause` console script (never global `atlas`)."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    in_scripts = False
    scripts: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_scripts = line == "[project.scripts]"
            continue
        if in_scripts and "=" in line and not line.startswith("#"):
            scripts.append(line.split("=", 1)[0].strip())
    assert scripts == ["heliopause"]

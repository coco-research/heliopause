"""L8: a lab may not be at, inside or around ~/.atlas or ~/.claude, where what runs outside the sandbox lives."""

import json
import os

import pytest

from atlas import adopt, bus, hooks, init, labfs, lifecycle, plugin


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.delenv("ATLAS_LAB", raising=False)
    return path


def _spots(home):
    (home / "elsewhere").mkdir()
    (home / "link-to-atlas").symlink_to(home / ".atlas", target_is_directory=True)
    return {
        "atlas": home / ".atlas",
        "atlas-child": home / ".atlas" / "lab2",
        "claude": home / ".claude",
        "claude-child": home / ".claude" / "lab",
        "home": home,
        "link": home / "link-to-atlas" / "x",
    }


@pytest.mark.parametrize("spot", ["atlas", "atlas-child", "claude", "claude-child", "home", "link"])
def test_init_refuses_a_lab_near_atlas_or_claude(home, spot, capsys):
    target = _spots(home)[spot]
    with pytest.raises(SystemExit) as exc:
        init.run(["--yes", "--dir", str(target)])
    assert exc.value.code == 2
    assert "a lab may not" in capsys.readouterr().err
    assert not (home / ".atlas").exists() and not (home / ".claude").exists()


def _lab_at(home, rel=".atlas"):
    lab = home / rel
    lab.mkdir(mode=0o700, parents=True)
    (lab / "atlas.json").write_text(json.dumps({"owner": "Owner", "nodes": []}), encoding="utf-8")
    return lab


@pytest.mark.parametrize("command", ["hooks", "adopt-apply", "adopt-scan", "adopt-mark", "up", "doctor", "uninstall",
                                     "bus-ask", "bus-check", "bus-install"])
def test_commands_refuse_a_lab_at_atlas(home, tmp_path, command, capsys):
    lab = _lab_at(home)
    before = sorted(str(p) for p in home.rglob("*"))
    calls = {
        "hooks": lambda: hooks.run(["--lab", str(lab), "--yes"]),
        "adopt-apply": lambda: adopt.run(["--apply", "--yes", "--lab", str(lab)]),
        "adopt-scan": lambda: adopt.run(["--scan", "--lab", str(lab)]),
        "adopt-mark": lambda: adopt.run(["--mark", str(tmp_path), "--lab", str(lab)]),
        "up": lambda: lifecycle.up(["--lab", str(lab), "--port", "1"]),
        "doctor": lambda: lifecycle.doctor(["--lab", str(lab), "--port", "1"]),
        "uninstall": lambda: lifecycle.uninstall(["--lab", str(lab), "--yes"]),
        "bus-ask": lambda: bus.run(["ask", "--lab", str(lab), "owner", "Pick"]),
        "bus-check": lambda: bus.run(["check", "--lab", str(lab)]),
        "bus-install": lambda: bus.run(["install", "--lab", str(lab)]),
    }
    with pytest.raises(SystemExit) as exc:
        calls[command]()
    assert exc.value.code == 2
    assert "a lab may not be inside ~/.atlas" in capsys.readouterr().err
    assert sorted(str(p) for p in home.rglob("*")) == before


def test_server_refuses_a_lab_at_claude(home, capsys):
    from atlas.__main__ import main

    lab = _lab_at(home, ".claude")
    with pytest.raises(SystemExit) as exc:
        main(["--lab", str(lab), "--port", "1"])
    assert exc.value.code == 2
    assert "a lab may not be inside ~/.claude" in capsys.readouterr().err


def test_plugin_build_writes_nothing_into_a_lab_around_the_marketplace(home, monkeypatch, capsys):
    lab = _lab_at(home, "Heliopause")  # the marketplace (~/Heliopause/marketplace) would land inside this lab
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    assert plugin._build() is None
    assert not (home / "Heliopause" / "marketplace").exists()
    assert "inside the lab" in capsys.readouterr().err


def test_plugin_build_passes_the_lab(home, monkeypatch):
    lab = home / "atlas-lab"
    _lab_at(home, "atlas-lab")
    seen = []
    real = labfs.home_write

    def spy(rel, data, mode=None, *, lab=None, new_only=False):
        seen.append(lab)
        return real(rel, data, mode, lab=lab, new_only=new_only)

    monkeypatch.setattr(labfs, "home_write", spy)
    assert plugin._build() is not None
    assert seen and all(item == lab for item in seen)


def test_home_refuses_a_target_inside_any_known_lab(home, monkeypatch):
    lab = _lab_at(home, ".atlas/hooks")
    monkeypatch.setenv("ATLAS_LAB", str(lab))
    with pytest.raises(labfs.Refused):
        labfs.home(".atlas/hooks/atlas-hook-start-0123456789ab")


def test_home_refuses_a_settings_link_into_a_known_lab(home, monkeypatch):
    lab = _lab_at(home, "atlas-lab")
    (lab / "settings.json").write_text("{}", encoding="utf-8")
    (home / ".claude").mkdir(mode=0o700)
    (home / ".claude" / "settings.json").symlink_to(lab / "settings.json")
    with pytest.raises(labfs.Refused):
        labfs.home(".claude/settings.json")


def test_check_lab_accepts_an_ordinary_folder(home):
    assert labfs.check_lab(home / "atlas-lab") == os.path.realpath(home / "atlas-lab")
    assert labfs.check_lab(home / ".atlas-old") == os.path.realpath(home / ".atlas-old")

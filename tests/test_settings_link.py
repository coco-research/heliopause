"""L10: a dotfiles symlink at ~/.claude/settings.json is followed only into folders only you can write."""

import pytest

from atlas import labfs


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir(mode=0o755)
    path.chmod(0o755)
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.delenv("ATLAS_LAB", raising=False)
    (path / ".claude").mkdir(mode=0o700)
    return path


def _link(home, target):
    target.write_text("{}\n", encoding="utf-8")
    target.chmod(0o600)
    (home / ".claude" / "settings.json").symlink_to(target)


def test_a_private_dotfiles_target_is_followed(home):
    dots = home / "dotfiles" / "claude"
    dots.mkdir(parents=True, mode=0o755)
    _link(home, dots / "settings.json")
    root, rel = labfs.home(".claude/settings.json")
    assert root / rel == dots / "settings.json"


@pytest.mark.parametrize("loose", ["target-folder", "middle-folder", "home"])
def test_a_writable_folder_above_the_target_in_home_is_refused(home, loose):
    dots = home / "dotfiles" / "claude"
    dots.mkdir(parents=True, mode=0o755)
    _link(home, dots / "settings.json")
    {"target-folder": dots, "middle-folder": home / "dotfiles", "home": home}[loose].chmod(0o775)
    with pytest.raises(labfs.Refused, match="writable by other users"):
        labfs.home(".claude/settings.json")


def test_a_world_writable_folder_outside_home_is_refused(home, tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    _link(home, shared / "settings.json")
    shared.chmod(0o777)
    with pytest.raises(labfs.Refused, match="writable by other users"):
        labfs.home(".claude/settings.json")
    shared.chmod(0o1777)  # the rule is no group or other write bit; a sticky bit does not make it private
    with pytest.raises(labfs.Refused, match="writable by other users"):
        labfs.home(".claude/settings.json")


def test_a_private_folder_outside_home_is_followed(home, tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    _link(home, private / "settings.json")
    root, rel = labfs.home(".claude/settings.json")
    assert root / rel == private / "settings.json"


def test_a_group_writable_target_is_refused(home):
    dots = home / "dotfiles"
    dots.mkdir(mode=0o755)
    _link(home, dots / "settings.json")
    (dots / "settings.json").chmod(0o664)
    with pytest.raises(labfs.Refused, match="writable by other users"):
        labfs.home(".claude/settings.json")

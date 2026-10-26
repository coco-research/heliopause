"""labfs: strict reads and writes under one root."""

import errno
import os
import stat
import zipfile

import pytest

from atlas import labfs


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "root"
    r.mkdir()
    return r


def test_write_creates_folders_and_replaces_keeping_mode(root):
    assert labfs.write(root, "a/b/c.txt", b"one") == "created"
    (root / "a/b/c.txt").chmod(0o640)
    assert labfs.write(root, "a/b/c.txt", b"two") == "replaced"
    assert (root / "a/b/c.txt").read_bytes() == b"two"
    assert (root / "a/b/c.txt").stat().st_mode & 0o777 == 0o640
    assert sorted(p.name for p in (root / "a/b").iterdir()) == ["c.txt"]


@pytest.mark.parametrize("rel", ["/etc/passwd", "../x", "a/../x", "./x", "", "a//b"])
def test_paths_outside_or_unclean_are_refused(root, rel):
    with pytest.raises(labfs.Refused):
        labfs.write(root, rel, b"x")


def test_symlinked_folder_at_any_depth_is_refused(root, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (root / "a").mkdir()
    (root / "a" / "b").symlink_to(out, target_is_directory=True)
    for call in (
        lambda: labfs.write(root, "a/b/c", b"x"),
        lambda: labfs.read(root, "a/b/c"),
        lambda: labfs.check(root, "a/b/c"),
        lambda: labfs.unlink(root, "a/b/c"),
        lambda: labfs.makedirs(root, "a/b/c"),
    ):
        with pytest.raises(labfs.Refused):
            call()
    assert list(out.iterdir()) == []


def test_final_symlink_and_hard_link_are_refused(root, tmp_path):
    other = tmp_path / "other"
    other.write_bytes(b"keep")
    (root / "s").symlink_to(other)
    os.link(other, root / "h")
    for name in ("s", "h"):
        for call in (lambda: labfs.write(root, name, b"x"), lambda: labfs.read(root, name), lambda: labfs.unlink(root, name)):
            with pytest.raises(labfs.Refused):
                call()
    assert other.read_bytes() == b"keep"


def test_new_only_and_read_cap(root):
    labfs.write(root, "f", b"12345")
    with pytest.raises(FileExistsError):
        labfs.write(root, "f", b"x", new_only=True)
    with pytest.raises(labfs.Refused):
        labfs.read(root, "f", cap=4)
    assert labfs.read(root, "missing") is None


def test_rmdir_never_follows_a_link(root, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (root / "l").symlink_to(out, target_is_directory=True)
    assert labfs.rmdir(root, "l") is False
    assert out.is_dir() and (root / "l").is_symlink()
    (root / "d").mkdir()
    assert labfs.rmdir(root, "d") is True


# B2 helpers: append, rename, chmod, open_dir, create, makedirs

def test_append_creates_then_appends_and_refuses_links(root, tmp_path):
    labfs.append(root, "q/log.jsonl", b"one\n", 0o600)
    labfs.append(root, "q/log.jsonl", b"two\n")
    assert (root / "q/log.jsonl").read_bytes() == b"one\ntwo\n"
    assert (root / "q/log.jsonl").stat().st_mode & 0o777 == 0o600
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep\n")
    (root / "link").symlink_to(victim)
    os.link(victim, root / "hard")
    for rel in ("link", "hard"):
        with pytest.raises(labfs.Refused):
            labfs.append(root, rel, b"x\n")
    assert victim.read_bytes() == b"keep\n"


def test_open_append_without_create_needs_the_file(root):
    with pytest.raises(FileNotFoundError):
        labfs.open_append(root, "absent", create=False)


def test_rename_moves_a_file_and_never_writes_through_the_destination(root, tmp_path):
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep\n")
    (root / "a").mkdir()
    (root / "a/job.md").write_bytes(b"job\n")
    (root / "b").mkdir()
    (root / "b/job.md").symlink_to(victim)
    with pytest.raises(FileExistsError):
        labfs.rename(root, "a/job.md", "b/job.md")
    labfs.rename(root, "a/job.md", "b/job.md", replace=True)
    assert (root / "b/job.md").read_bytes() == b"job\n" and not (root / "b/job.md").is_symlink()
    assert victim.read_bytes() == b"keep\n"


def test_rename_refuses_a_symlinked_destination_folder(root, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (root / "job.md").write_bytes(b"job\n")
    (root / "done").symlink_to(out, target_is_directory=True)
    with pytest.raises(labfs.Refused):
        labfs.rename(root, "job.md", "done/job.md")
    assert list(out.iterdir()) == []


def test_chmod_never_follows_a_link(root, tmp_path):
    victim = tmp_path / "victim"
    victim.write_bytes(b"x")
    victim.chmod(0o600)
    (root / "tool").symlink_to(victim)
    with pytest.raises(labfs.Refused):
        labfs.chmod(root, "tool", 0o777)
    assert victim.stat().st_mode & 0o777 == 0o600
    (root / "real").write_bytes(b"x")
    labfs.chmod(root, "real", 0o700)
    assert (root / "real").stat().st_mode & 0o777 == 0o700


def test_makedirs_reports_what_it_made_with_exact_owner_only_mode(root):
    old = os.umask(0o077)
    try:
        assert labfs.makedirs(root, "x/y", 0o700) == ["x", "x/y"]
    finally:
        os.umask(old)
    assert (root / "x/y").stat().st_mode & 0o777 == 0o700
    assert labfs.makedirs(root, "x/y") == []


def test_create_leaves_anything_already_there(root, tmp_path):
    (root / "dangling").symlink_to(tmp_path / "nowhere")
    assert labfs.create(root, "dangling", b"x") is False
    assert not (tmp_path / "nowhere").exists()
    assert labfs.create(root, "new", b"x", 0o600) is True
    assert labfs.create(root, "new", b"y") is False
    assert (root / "new").read_bytes() == b"x"


def test_open_dir_refuses_a_symlinked_folder(root, tmp_path):
    (root / ".atlas").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(labfs.Refused):
        labfs.open_dir(root, ".atlas", create=True, mode=0o700)


# L5: no hard links on this filesystem

# Errno values differ by platform (ENOTSUP is 95 on Linux, 45 on macOS, where EOPNOTSUPP is 102), so use the
# names; ENOTSUP and EOPNOTSUPP are the same value on Linux and are deduplicated.
_NO_LINK_CODES = {}
for _name in ("EPERM", "ENOTSUP", "EOPNOTSUPP", "EXDEV"):
    _NO_LINK_CODES.setdefault(getattr(errno, _name), _name)


@pytest.mark.parametrize("code", sorted(_NO_LINK_CODES), ids=lambda c: _NO_LINK_CODES[c])
def test_write_without_hard_links_creates_the_file_directly(root, monkeypatch, code):
    def no_link(*a, **k):
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(labfs.os, "link", no_link)
    assert labfs.write(root, "d/new.txt", b"hello", 0o640) == "created"
    assert (root / "d/new.txt").read_bytes() == b"hello"
    assert (root / "d/new.txt").stat().st_mode & 0o777 == 0o640
    assert sorted(p.name for p in (root / "d").iterdir()) == ["new.txt"]
    with pytest.raises(FileExistsError):
        labfs.write(root, "d/new.txt", b"again", new_only=True)


def test_write_errors_name_the_path(root, monkeypatch):
    def no_link(*a, **k):
        raise OSError(1, "Operation not permitted")

    real_open = os.open

    def no_excl(path, flags, *a, **k):
        if flags & os.O_EXCL and not str(path).startswith("."):
            raise OSError(1, "Operation not permitted")
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(labfs.os, "link", no_link)
    monkeypatch.setattr(labfs.os, "open", no_excl)
    with pytest.raises(OSError) as info:
        labfs.write(root, "d/new.txt", b"x")
    assert info.value.filename == str(root / "d" / "new.txt")
    assert str(root / "d" / "new.txt") in str(info.value)
    assert list((root / "d").iterdir()) == []


# L6: a failed temp cleanup never masks the first error

def test_write_keeps_the_first_error_and_reports_the_leftover(root, monkeypatch):
    def full(*a, **k):
        raise OSError(28, "No space left on device")

    real_unlink = os.unlink

    def stuck(path, *a, **k):
        if ".atlas-" in str(path):
            raise PermissionError(13, "Permission denied")
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(labfs.os, "link", full)
    monkeypatch.setattr(labfs.os, "unlink", stuck)
    with pytest.raises(OSError) as info:
        labfs.write(root, "f", b"x")
    assert info.value.errno == 28
    assert isinstance(info.value.__cause__, OSError) or info.value.__cause__ is None
    (left, why), = info.value.atlas_leftovers
    assert left.startswith(str(root)) and os.path.exists(left) and isinstance(why, PermissionError)


# home(): the explicit allow-list of home-folder files

@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.mark.parametrize("rel", [".bashrc", ".ssh/authorized_keys", ".claude/settings.local.json", ".atlas/hooks/evil", ".atlas/labx", ".gitconfig"])
def test_home_refuses_files_off_the_list(home, rel):
    with pytest.raises(labfs.Refused, match="not a home-folder file"):
        labfs.home(rel)


def test_home_allows_the_listed_files(home):
    for rel in (".atlas/lab", ".atlas/plugin-hosts", ".atlas/marketplace/atlas/x.json", ".claude/settings.json", ".atlas/hooks/atlas-hook-start-0123456789ab"):
        assert labfs.home(rel) == (home.resolve(), rel)


def test_home_refuses_links_others_and_the_lab(home, tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    (home / ".atlas").mkdir()
    (home / ".atlas" / "lab").symlink_to(lab / "x")
    with pytest.raises(labfs.Refused, match="symlink"):
        labfs.home(".atlas/lab")
    (home / ".atlas" / "lab").unlink()
    (home / ".atlas").chmod(0o777)
    with pytest.raises(labfs.Refused, match="writable by other users"):
        labfs.home(".atlas/lab")
    (home / ".atlas").chmod(0o700)
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").symlink_to(lab / "settings.json")
    (lab / "settings.json").write_text("{}", encoding="utf-8")
    with pytest.raises(labfs.Refused, match="links into the lab"):
        labfs.home(".claude/settings.json", lab=lab)


def test_lab_target_is_only_the_exact_grok_hook_log():
    assert labfs.lab_target("logs/grok-hooks.log") == "logs/grok-hooks.log"
    for rel in ("logs/grok-hooksXlog", "logs/other.log", "logs/grok-hooks.log/extra", "board.md"):
        with pytest.raises(labfs.Refused):
            labfs.lab_target(rel)


def test_home_writes_a_dotfiles_settings_link_at_its_target(home, tmp_path):
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "settings.json").write_text("{}", encoding="utf-8")
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").symlink_to(dotfiles / "settings.json")
    labfs.home_write(".claude/settings.json", b'{"a": 1}\n')
    assert (home / ".claude" / "settings.json").is_symlink()
    assert (dotfiles / "settings.json").read_bytes() == b'{"a": 1}\n'


def test_open_read_nofollow_refuses_a_symlink(tmp_path):
    real = tmp_path / "real"
    real.write_bytes(b"zip")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(labfs.Refused, match="symlink"):
        labfs.open_read_nofollow(link)
    fd = labfs.open_read_nofollow(real)
    try:
        assert os.path.samestat(os.fstat(fd), real.lstat())
        assert os.read(fd, 8) == b"zip"
    finally:
        os.close(fd)


def test_unlinked_fd_has_no_links_and_is_owner_only():
    fd = labfs.unlinked_fd()
    try:
        st = os.fstat(fd)
        assert st.st_nlink == 0
        assert stat.S_ISREG(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o600
        assert not os.get_inheritable(fd)
    finally:
        os.close(fd)


def test_write_fd_writes_every_byte():
    fd = labfs.unlinked_fd()
    try:
        assert labfs.write_fd(fd, b"") is True
        assert labfs.write_fd(fd, b"hello") is True
        assert labfs.write_fd(fd, b" world") is True
        os.lseek(fd, 0, os.SEEK_SET)
        assert os.read(fd, 32) == b"hello world"
    finally:
        os.close(fd)


def test_open_zip_read_scans_and_closes_the_fd(tmp_path):
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.txt", b"hi")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    with labfs.open_zip_read(fd) as zf:
        assert zf.read("a.txt") == b"hi"
        with pytest.raises(ValueError):
            zf.writestr("b.txt", b"no")
    with pytest.raises(OSError):
        os.lseek(fd, 0, os.SEEK_SET)

    plain = tmp_path / "plain"
    plain.write_bytes(b"not a zip")
    bad = os.open(plain, os.O_RDONLY | os.O_CLOEXEC)
    with pytest.raises(zipfile.BadZipFile):
        with labfs.open_zip_read(bad):
            pass
    with pytest.raises(OSError):
        os.lseek(bad, 0, os.SEEK_SET)

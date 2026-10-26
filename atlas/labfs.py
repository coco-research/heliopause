"""Strict file access under one root folder: no symlink anywhere below the root, no hard-linked files.

WHY: lab agents can write anywhere in the lab, so they can plant symlinks or hard links that a later write
by a user-run command would follow out of the lab (a shell startup file, ~/.claude/settings.json, a folder
outside HOME). Every write, append, copy, chmod, mkdir and rename under the lab goes through here, and so does
every write to a file in the home folder (see home() and HOME_TARGETS). tests/test_fs_guard.py fails on any
plain write call elsewhere in atlas/; tests/fs_guard_allow.txt lists the few documented exceptions.

How: each folder below the root is opened with O_DIRECTORY|O_NOFOLLOW relative to its parent's descriptor,
so a symlink at any level fails with ELOOP and nothing can be swapped between the check and the use. Files
are opened with O_NOFOLLOW; new files are written to a temp file and hard-linked into place (fails if
anything appeared), existing ones are replaced with a rename. A file with st_nlink > 1 is refused. The root
itself is resolved once; the caller decides which roots are allowed. There is no option to follow links.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import secrets
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIR = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW | _CLOEXEC


class Refused(OSError):
    """A path that labfs will not touch. str() names the path and the reason."""

    def __init__(self, path: str, why: str) -> None:
        super().__init__(errno.EPERM, f"refusing {path}: {why}")
        self.path = path
        self.why = why

    def __str__(self) -> str:
        return f"refusing {self.path}: {self.why}"


def parts_of(rel: str | Path) -> list[str]:
    """The components of a relative path. Absolute paths, '..', '.' and empty names are refused."""
    text = str(rel)
    if not text or os.path.isabs(text):
        raise Refused(text, "not a path inside the root")
    parts = text.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise Refused(text, "not a plain path inside the root")
    return parts


def real_root(root: Path) -> str:
    return os.path.realpath(os.fspath(Path(root).expanduser()))


def rel_to(root: Path, path: Path) -> str:
    """path relative to root, without following any link below the root. Refused when it is outside."""
    base = real_root(root)
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    # Resolve only the root part: the caller's spelling of the root may itself be a link.
    for candidate in (os.fspath(Path(root).expanduser().absolute()), base):
        try:
            return Path(os.path.normpath(raw)).relative_to(os.path.normpath(candidate)).as_posix()
        except ValueError:
            continue
    raise Refused(str(path), f"outside {base}")


def _open_dirs(base: str, parts: list[str], create: bool, shown: str, mode: int = 0o755, made: list | None = None) -> int:
    """A descriptor for base/parts, each folder opened without following a link. Created folders get exactly mode."""
    if create and not os.path.isdir(base):
        # The root is the caller's own choice (the lab or HOME), so it is made as given.
        os.makedirs(base, exist_ok=True)
    fd = os.open(base, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _CLOEXEC)
    try:
        for i, part in enumerate(parts):
            fresh = False
            try:
                nfd = os.open(part, _DIR, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode, dir_fd=fd)
                    fresh = True
                except FileExistsError:
                    pass
                try:
                    nfd = os.open(part, _DIR, dir_fd=fd)
                except OSError as err:
                    raise _why(err, shown, part, fd) from err
            except OSError as err:
                raise _why(err, shown, part, fd) from err
            os.close(fd)
            fd = nfd
            if fresh:
                if not mode & 0o077:
                    # An owner-only folder is exactly mode, whatever the umask removed.
                    os.fchmod(fd, mode)
                if made is not None:
                    made.append("/".join(parts[: i + 1]))
        return fd
    except BaseException:
        os.close(fd)
        raise


def _why(err: OSError, shown: str, part: str, fd: int) -> OSError:
    if err.errno not in (errno.ELOOP, errno.ENOTDIR):
        return err
    try:
        st = os.stat(part, dir_fd=fd, follow_symlinks=False)
    except OSError:
        st = None
    if err.errno == errno.ELOOP or (st is not None and stat.S_ISLNK(st.st_mode)):
        return Refused(shown, f"{part}/ is a symlink")
    return Refused(shown, f"{part} is not a folder")


def _check_file(st: os.stat_result, shown: str) -> None:
    if stat.S_ISLNK(st.st_mode):
        raise Refused(shown, "it is a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise Refused(shown, "it is not a regular file")
    if st.st_nlink > 1:
        raise Refused(shown, f"it has {st.st_nlink} hard links")


def _lstat(name: str, dfd: int) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=dfd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def lstat(root: Path, rel: str | Path) -> os.stat_result | None:
    """lstat of root/rel below a link-free folder chain; None when it (or a folder on the way) is absent."""
    parts = parts_of(rel)
    base = real_root(root)
    shown = os.path.join(base, *parts)
    try:
        dfd = _open_dirs(base, parts[:-1], False, shown)
    except FileNotFoundError:
        return None
    try:
        return _lstat(parts[-1], dfd)
    finally:
        os.close(dfd)


def check(root: Path, rel: str | Path, *, folder: bool = False) -> bool:
    """Raise Refused if writing rel would pass a symlink or hit a link/hard link. True when it exists."""
    parts = parts_of(rel)
    base = real_root(root)
    shown = os.path.join(base, *parts)
    dirs = parts if folder else parts[:-1]
    try:
        dfd = _open_dirs(base, dirs, False, shown)
    except FileNotFoundError:
        return False
    try:
        if folder:
            return True
        st = _lstat(parts[-1], dfd)
        if st is None:
            return False
        _check_file(st, shown)
        return True
    finally:
        os.close(dfd)


def read(root: Path, rel: str | Path, cap: int = 64 << 20) -> bytes | None:
    """The bytes of a regular, singly linked file, or None when it is absent. Refused on links or > cap."""
    parts = parts_of(rel)
    base = real_root(root)
    shown = os.path.join(base, *parts)
    try:
        dfd = _open_dirs(base, parts[:-1], False, shown)
    except FileNotFoundError:
        return None
    try:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | _CLOEXEC, dir_fd=dfd)
        except FileNotFoundError:
            return None
        except OSError as err:
            if err.errno == errno.ELOOP:
                raise Refused(shown, "it is a symlink") from err
            raise
    finally:
        os.close(dfd)
    try:
        st = os.fstat(fd)
        _check_file(st, shown)
        if st.st_size > cap:
            raise Refused(shown, f"it is larger than {cap} bytes")
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                raise Refused(shown, f"it is larger than {cap} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


# Hard links unsupported (exFAT, some SMB and FUSE mounts): create the final name exclusively instead.
_NO_LINK = {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.EMLINK, errno.ENOSYS}


def _named(err: OSError, shown: str) -> OSError:
    """err with the path it was about, so a message never says only "Operation not permitted"."""
    if isinstance(err, Refused) or err.filename == shown or err.errno is None:
        return err
    out = OSError(err.errno, err.strerror or str(err), shown)
    if hasattr(err, "atlas_leftovers"):
        out.atlas_leftovers = err.atlas_leftovers
    return out


def _write_all(fd: int, data: bytes, shown: str) -> None:
    view = memoryview(data)
    while view:
        wrote = os.write(fd, view)
        if wrote == 0:
            raise OSError(errno.EIO, "short write", shown)
        view = view[wrote:]


def _create_direct(dfd: int, name: str, data: bytes, mode: int, shown: str) -> None:
    """O_CREAT|O_EXCL|O_NOFOLLOW at the final name, then write, fsync and verify. As race-safe as a link for a create."""
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600, dir_fd=dfd)
    except FileExistsError as err:
        raise Refused(shown, "something appeared at this path while writing") from err
    try:
        try:
            _write_all(fd, data, shown)
            os.fchmod(fd, mode)
            os.fsync(fd)
            st = os.fstat(fd)
            now = _lstat(name, dfd)
            if (
                st.st_size != len(data)
                or st.st_nlink != 1
                or now is None
                or (now.st_ino, now.st_dev) != (st.st_ino, st.st_dev)
            ):
                raise Refused(shown, "it changed while writing")
        except BaseException:
            now = _lstat(name, dfd)
            if now is not None and (now.st_ino, now.st_dev) == (os.fstat(fd).st_ino, os.fstat(fd).st_dev):
                os.unlink(name, dir_fd=dfd)
            raise
    finally:
        os.close(fd)


def write(root: Path, rel: str | Path, data: bytes, mode: int | None = None, *, new_only: bool = False) -> str:
    """Write data at root/rel. Returns "created" or "replaced". Missing folders are made, never followed.

    mode: for a new file, default 0644; for an existing one, its own mode is kept unless mode is given.
    new_only: an existing file raises FileExistsError instead of being replaced.
    Errors name the path. If removing the temp file fails after another error, that error is kept and the
    leftover is attached as err.atlas_leftovers [(path, cleanup error)].
    """
    parts = parts_of(rel)
    base = real_root(root)
    shown = os.path.join(base, *parts)
    try:
        return _write(base, parts, shown, data, mode, new_only)
    except OSError as err:
        named = _named(err, shown)
        if named is err:
            raise
        raise named from err


def _write(base: str, parts: list[str], shown: str, data: bytes, mode: int | None, new_only: bool) -> str:
    name = parts[-1]
    dfd = _open_dirs(base, parts[:-1], True, shown)
    tmp = None
    try:
        st = _lstat(name, dfd)
        if st is not None:
            _check_file(st, shown)
            if new_only:
                raise FileExistsError(errno.EEXIST, "already exists", shown)
        final_mode = mode if mode is not None else (stat.S_IMODE(st.st_mode) if st is not None else 0o644)
        tmp = f".{name}.atlas-{secrets.token_hex(6)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600, dir_fd=dfd)
        try:
            _write_all(fd, data, shown)
            os.fchmod(fd, final_mode)
            os.fsync(fd)
        finally:
            os.close(fd)
        if st is None:
            try:
                os.link(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            except FileExistsError as err:
                raise Refused(shown, "something appeared at this path while writing") from err
            except OSError as err:
                if err.errno not in _NO_LINK:
                    raise
                _create_direct(dfd, name, data, final_mode, shown)
            return "created"
        # Something may have been swapped in since the lstat: check again right before the rename.
        again = _lstat(name, dfd)
        if again is None or (again.st_ino, again.st_dev) != (st.st_ino, st.st_dev):
            raise Refused(shown, "it changed while writing")
        _check_file(again, shown)
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        tmp = None
        return "replaced"
    except BaseException as err:
        # WHY: a failed cleanup must not replace the original error; the leftover is attached for the caller.
        if tmp is not None:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except FileNotFoundError:
                pass
            except OSError as cleanup:
                err.atlas_leftovers = [*getattr(err, "atlas_leftovers", []), (os.path.join(os.path.dirname(shown), tmp), cleanup)]
            tmp = None
        raise
    finally:
        try:
            if tmp is not None:
                try:
                    os.unlink(tmp, dir_fd=dfd)
                except FileNotFoundError:
                    pass
                except OSError as cleanup:
                    left = os.path.join(os.path.dirname(shown), tmp)
                    raise OSError(cleanup.errno, f"wrote {shown}, but could not remove the temporary file {left}", left) from cleanup
        finally:
            os.close(dfd)


def create(root: Path, rel: str | Path, data: bytes, mode: int = 0o644) -> bool:
    """Create root/rel only if nothing is there, a symlink included (never followed). False if anything was there."""
    if lstat(root, rel) is not None:
        return False
    try:
        write(root, rel, data, mode, new_only=True)
    except FileExistsError:
        return False
    except Refused as err:
        if "appeared" not in err.why:
            raise
        return False
    return True


def write_fd(fd: int, data: bytes) -> bool:
    """Write all of data to fd (a descriptor labfs opened). False when the write came up short."""
    offset = 0
    while offset < len(data):
        wrote = os.write(fd, data[offset:])
        if wrote == 0:
            return False
        offset += wrote
    return True


def _open_or_create(name: str, flags: int, mode: int, dfd: int) -> int:
    """openat(name) creating it when absent: O_CREAT|O_EXCL first, then a plain open if someone else made it.

    WHY: on macOS, openat(dirfd, name, O_CREAT) without O_EXCL fails with ENOENT now and then when several
    threads create the same new name at once (seen on the CI runner: 1 of 20 concurrent first handoffs got
    503). Exclusive create and a plain open of an existing file never do.
    """
    try:
        return os.open(name, flags | os.O_CREAT | os.O_EXCL, mode, dir_fd=dfd)
    except FileExistsError:
        return os.open(name, flags, dir_fd=dfd)


def open_append(root: Path, rel: str | Path, *, create: bool = True, mode: int = 0o600, read: bool = False, make_dirs: bool = False, fix_mode: bool = False) -> int:
    """An O_APPEND descriptor for root/rel: link-free folders, O_NOFOLLOW, a regular file with one link.

    A new file is created with mode. read=True opens it O_RDWR (for a torn-line check or a lock-and-reread).
    FileNotFoundError when it is absent and create is False. The caller closes the descriptor.
    """
    parts = parts_of(rel)
    base = real_root(root)
    shown = os.path.join(base, *parts)
    dfd = _open_dirs(base, parts[:-1], make_dirs, shown)
    try:
        flags = (os.O_RDWR if read else os.O_WRONLY) | os.O_APPEND | _NOFOLLOW | _CLOEXEC | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = _open_or_create(parts[-1], flags, mode, dfd) if create else os.open(parts[-1], flags, dir_fd=dfd)
        except OSError as err:
            if err.errno == errno.ELOOP:
                raise Refused(shown, "it is a symlink") from err
            if isinstance(err, FileNotFoundError):
                raise
            raise _named(err, shown) from err
    finally:
        os.close(dfd)
    try:
        _check_file(os.fstat(fd), shown)
        if fix_mode:
            os.fchmod(fd, mode)  # an existing file gets exactly mode too, on the checked descriptor
    except BaseException:
        os.close(fd)
        raise
    return fd


def append(root: Path, rel: str | Path, data: bytes, mode: int = 0o600, *, make_dirs: bool = True) -> None:
    """Append data to root/rel (created with mode when absent). Refused on a symlink or a hard-linked file."""
    shown = os.path.join(real_root(root), *parts_of(rel))
    fd = open_append(root, rel, create=True, mode=mode, make_dirs=make_dirs)
    try:
        _write_all(fd, data, shown)
    finally:
        os.close(fd)


def open_dir(root: Path, rel: str | Path | None = None, *, create: bool = False, mode: int = 0o755) -> int:
    """A descriptor for the folder root/rel (root itself when rel is None), opened without following links."""
    base = real_root(root)
    parts = parts_of(rel) if rel is not None else []
    return _open_dirs(base, parts, create, os.path.join(base, *parts), mode)


def rename(root: Path, src: str | Path, dst: str | Path, *, replace: bool = False) -> None:
    """Move root/src to root/dst, both below link-free folder chains. src must be a regular, singly linked file.

    The destination entry is replaced, never written through. replace=False refuses an existing destination.
    """
    sparts, dparts = parts_of(src), parts_of(dst)
    base = real_root(root)
    sshown, dshown = os.path.join(base, *sparts), os.path.join(base, *dparts)
    sfd = _open_dirs(base, sparts[:-1], False, sshown)
    try:
        dfd = _open_dirs(base, dparts[:-1], False, dshown)
        try:
            st = _lstat(sparts[-1], sfd)
            if st is None:
                raise FileNotFoundError(errno.ENOENT, "not found", sshown)
            _check_file(st, sshown)
            there = _lstat(dparts[-1], dfd)
            if there is not None:
                if not replace:
                    raise FileExistsError(errno.EEXIST, "already exists", dshown)
                if stat.S_ISDIR(there.st_mode):
                    raise Refused(dshown, "it is a folder")
            os.rename(sparts[-1], dparts[-1], src_dir_fd=sfd, dst_dir_fd=dfd)
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)


def chmod(root: Path, rel: str | Path, mode: int, *, folder: bool = False) -> None:
    """Set the mode of a regular, singly linked file (or a real folder) on its no-follow descriptor."""
    parts = parts_of(rel)
    base = real_root(root)
    shown = os.path.join(base, *parts)
    if folder:
        fd = _open_dirs(base, parts, False, shown)
    else:
        fd = open_read(root, rel)
        try:
            _check_file(os.fstat(fd), shown)
        except BaseException:
            os.close(fd)
            raise
    try:
        os.fchmod(fd, mode)
    finally:
        os.close(fd)


# Exact files under a lab (relative to the lab root, full match, no wildcard).
# lab_target() refuses anything else. Other lab writes do not consult this list: they pass their own
# relative path to append or write, which still refuses a symlink or a second hard link.
# A parent folder is not an entry. The caller creates it with makedirs.
LAB_TARGETS = (
    (r"logs/grok-hooks\.log", "plugin: Grok session-start appends one line when GROK_HOOK_EVENT is set"),
)


def lab_target(rel: str) -> str:
    """rel when it fully matches one LAB_TARGETS pattern. Else Refused."""
    text = str(rel)
    if any(re.fullmatch(pattern, text) for pattern, _who in LAB_TARGETS):
        return text
    raise Refused(text, "not a lab file on the list")


# The only files in the home folder that Heliopause writes or removes (relative to HOME, full match).
# Everything else under HOME is refused by home(). Keep this list short and say who writes each one.
HOME_TARGETS = (
    (r"\.atlas/lab", "adopt: the pointer to the last applied lab (plugin reads it)"),
    (r"\.atlas/plugin-hosts", "plugin: which hosts the plugin was installed for"),
    (r"Heliopause/marketplace/[^/].*", "plugin: the local marketplace copy, rebuilt from this package"),
    (r"\.atlas/marketplace/[^/].*", "plugin: where 0.6 kept the marketplace copy; only removed now"),
    (r"\.atlas/hooks/atlas-(?:hook-start|recall-hook)-[0-9a-f]{12}", "hooks: owner-only hook scripts that settings.json runs"),
    (r"\.claude/settings\.json", "hooks and uninstall: the Claude Code hooks registration"),
    (r"\.cursor/hooks\.json", "connect: Cursor's hook file"),
    (r"\.cursor/hooks\.json\.heliopause-bak-\d{8}T\d{6}\d{6}Z(?:-\d+)?", "connect: backup of Cursor's hooks.json, named <file>.heliopause-bak-<YYYYMMDDTHHMMSSFFFFFFZ>[-n]"),
    (r"\.codex/hooks\.json", "connect: Codex's hook file"),
    (r"\.codex/hooks\.json\.heliopause-bak-\d{8}T\d{6}\d{6}Z(?:-\d+)?", "connect: backup of Codex's hooks.json, named <file>.heliopause-bak-<YYYYMMDDTHHMMSSFFFFFFZ>[-n]"),
    (r"\.grok/hooks/heliopause\.json", "connect: Grok's hook file"),
    (r"\.grok/hooks/heliopause\.json\.heliopause-bak-\d{8}T\d{6}\d{6}Z(?:-\d+)?", "connect: backup of Grok's heliopause.json, named <file>.heliopause-bak-<YYYYMMDDTHHMMSSFFFFFFZ>[-n]"),
)
# Optional add-ons that are not in every build add their exact files here (allow_home), at import.
_ADDED_TARGETS: list = []


def allow_home(pattern: str, who: str) -> None:
    """Add one HOME_TARGETS-style entry (a full-match pattern below ~/.atlas/hooks/) for an optional add-on."""
    if not pattern.startswith(r"\.atlas/hooks/"):
        raise ValueError("add-ons may only add files below ~/.atlas/hooks/")
    if (pattern, who) not in _ADDED_TARGETS:
        _ADDED_TARGETS.append((pattern, who))


# A dotfiles symlink at these is the user's own setup: written at its target, checked the same way.
HOME_FOLLOW = (".claude/settings.json",)


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def home(rel: str, *, lab: Path | None = None) -> tuple[Path, str]:
    """(root, rel) to pass to write/read/unlink/append for an allowed file in the home folder. Else Refused.

    rel must fully match one HOME_TARGETS pattern. Each folder on the way below HOME must be a real folder you
    own that nobody else can write; an existing file must be a regular file you own with one link. Only a
    HOME_FOLLOW file may be a symlink, and its target must pass the same file checks. Nothing may resolve
    inside lab (agents can write there).
    """
    text = str(rel)
    parts = parts_of(text)
    base = real_root(Path.home())
    shown = os.path.join(base, *parts)
    if not any(re.fullmatch(pattern, text) for pattern, _who in (*HOME_TARGETS, *_ADDED_TARGETS)):
        raise Refused(shown, "not a home-folder file Heliopause may write")
    uid = os.getuid()
    lab_real = real_root(lab) if lab is not None else None
    cur = base
    for part in parts[:-1]:
        cur = os.path.join(cur, part)
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            break
        if stat.S_ISLNK(st.st_mode):
            raise Refused(shown, f"{part}/ is a symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise Refused(shown, f"{part} is not a folder")
        if st.st_uid != uid:
            raise Refused(shown, f"{cur} is not owned by you")
        if st.st_mode & 0o022:
            raise Refused(shown, f"{cur} is writable by other users")
    if lab_real is not None and _inside(shown, lab_real):
        raise Refused(shown, "it is inside the lab")
    # Any lab, not only the one passed: agents write labs, so nothing that runs outside them may live in one.
    around = lab_around(shown)
    if around is not None:
        raise Refused(shown, f"it is inside the lab {around}")
    try:
        st = os.lstat(shown)
    except (FileNotFoundError, NotADirectoryError):
        return Path(base), text
    if stat.S_ISLNK(st.st_mode):
        if text not in HOME_FOLLOW:
            raise Refused(shown, "it is a symlink")
        real = os.path.realpath(shown)
        if lab_real is not None and _inside(real, lab_real) or lab_around(real) is not None:
            raise Refused(shown, f"it links into the lab ({real})")
        try:
            target = os.lstat(real)
        except FileNotFoundError:
            raise Refused(shown, f"it is a dangling symlink ({real})") from None
        _check_file(target, real)
        if target.st_uid != uid:
            raise Refused(shown, f"its target {real} is not owned by you")
        if target.st_mode & 0o022:
            raise Refused(shown, f"its target {real} is writable by other users")
        # Whoever can write a folder above the target can swap the file. Check each folder up to HOME, or just
        # the target's own folder when it lives outside HOME.
        parent = os.path.dirname(real)
        folders = [parent]
        if _inside(parent, base):
            while folders[-1] != base:
                folders.append(os.path.dirname(folders[-1]))
        for folder in folders:
            st = os.lstat(folder)
            if st.st_uid != uid:
                raise Refused(shown, f"its target folder {folder} is not owned by you")
            if st.st_mode & 0o022:
                raise Refused(shown, f"its target folder {folder} is writable by other users")
        return Path(parent), os.path.basename(real)
    _check_file(st, shown)
    if st.st_uid != uid:
        raise Refused(shown, "it is not owned by you")
    return Path(base), text


def known_labs() -> list[str]:
    """Real paths of the labs this user has: $ATLAS_LAB, the ~/.atlas/lab pointer, ~/atlas-lab (existing folders)."""
    found: list[str] = []
    candidates = [os.environ.get("ATLAS_LAB", "").strip()]
    try:
        pointer = read(Path.home(), ".atlas/lab", cap=4096)
    except OSError:
        pointer = None
    if pointer:
        candidates.append(pointer.decode("utf-8", "replace").strip().splitlines()[0].strip() if pointer.strip() else "")
    candidates.append(str(Path.home() / "atlas-lab"))
    for raw in candidates:
        if not raw:
            continue
        path = os.path.expanduser(raw)
        if not os.path.isabs(path) or not os.path.isdir(path):
            continue
        real = os.path.realpath(path)
        if real not in found:
            found.append(real)
    return found


def lab_around(path: Path | str) -> str | None:
    """The lab that path (resolved) is, or is inside: a known lab, or the nearest folder holding atlas.json."""
    real = os.path.realpath(os.path.expanduser(str(path)))
    for lab in known_labs():
        if _inside(real, lab):
            return lab
    cur = real
    while True:
        try:
            st = os.lstat(os.path.join(cur, "atlas.json"))
        except OSError:
            st = None
        if st is not None and stat.S_ISREG(st.st_mode):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def lab_near(path: Path | str) -> str | None:
    """A lab that path (resolved) is, is inside, or contains (known labs only for 'contains')."""
    real = os.path.realpath(os.path.expanduser(str(path)))
    inside = lab_around(real)
    if inside is not None:
        return inside
    for lab in known_labs():
        if _inside(lab, real):
            return lab
    return None


# Folders a lab must stay clear of: what runs outside the agents' sandbox lives here (hooks, plugin, settings).
PROTECTED = (".atlas", ".claude")


def check_lab(lab: Path | str) -> str:
    """The lab's real path, or Refused when it is, is inside, or contains ~/.atlas or ~/.claude (paths resolved).

    Agents can write anywhere in a lab, so a lab there could change what Claude Code or the plugin runs.
    """
    real = os.path.realpath(os.path.expanduser(str(lab)))
    home_real = os.path.realpath(str(Path.home()))
    for name in PROTECTED:
        guarded = os.path.realpath(os.path.join(home_real, name))
        for spot in {guarded, os.path.join(home_real, name)}:
            if _inside(real, spot):
                raise Refused(real, f"a lab may not be inside ~/{name}")
            if _inside(spot, real):
                raise Refused(real, f"a lab may not contain ~/{name}")
    return real


def refuse_lab(lab: Path | str, prog: str) -> None:
    """check_lab for a command: print why and exit 2 when the lab is at, inside or around ~/.atlas or ~/.claude."""
    try:
        check_lab(lab)
    except Refused as err:
        print(f"{prog}: {err}. Pick a lab folder outside ~/.atlas and ~/.claude.", file=sys.stderr)
        raise SystemExit(2) from None


def home_write(rel: str, data: bytes, mode: int | None = None, *, lab: Path | None = None, new_only: bool = False) -> str:
    root, name = home(rel, lab=lab)
    return write(root, name, data, mode, new_only=new_only)


def unlink(root: Path, rel: str | Path) -> bool:
    """Remove a regular, singly linked file. False when it was absent."""
    parts = parts_of(rel)
    base = real_root(root)
    shown = os.path.join(base, *parts)
    try:
        dfd = _open_dirs(base, parts[:-1], False, shown)
    except FileNotFoundError:
        return False
    try:
        st = _lstat(parts[-1], dfd)
        if st is None:
            return False
        _check_file(st, shown)
        os.unlink(parts[-1], dir_fd=dfd)
        return True
    finally:
        os.close(dfd)


def rmdir(root: Path, rel: str | Path) -> bool:
    """Remove an empty real folder. False when absent, not empty, or not a plain folder (a link is never followed)."""
    parts = parts_of(rel)
    base = real_root(root)
    shown = os.path.join(base, *parts)
    try:
        dfd = _open_dirs(base, parts[:-1], False, shown)
    except (FileNotFoundError, Refused):
        return False
    try:
        st = _lstat(parts[-1], dfd)
        if st is None or not stat.S_ISDIR(st.st_mode):
            return False
        try:
            os.rmdir(parts[-1], dir_fd=dfd)
        except OSError:
            return False
        return True
    finally:
        os.close(dfd)


def listdir(root: Path, rel: str | Path | None = None) -> list[str] | None:
    """Sorted entry names of the real folder root/rel, or None when it is absent. A symlinked folder is refused."""
    try:
        fd = open_dir(root, rel)
    except FileNotFoundError:
        return None
    try:
        return sorted(os.listdir(fd))
    finally:
        os.close(fd)


def makedirs(root: Path, rel: str | Path, mode: int = 0o755) -> list[str]:
    """Make root/rel and any missing folder on the way (each new one exactly mode); a symlink at any level is refused.

    Returns the relative paths of the folders it created, outermost first.
    """
    parts = parts_of(rel)
    base = real_root(root)
    made: list[str] = []
    os.close(_open_dirs(base, parts, True, os.path.join(base, *parts), mode, made))
    return made


def open_read(root: Path, rel: str | Path) -> int:
    """A read descriptor for root/rel opened with O_NOFOLLOW below a link-free folder chain.

    FileNotFoundError when absent; Refused when a folder on the way or the file itself is a symlink.
    The caller checks the file (fstat) and closes the descriptor.
    """
    parts = parts_of(rel)
    base = real_root(root)
    shown = os.path.join(base, *parts)
    dfd = _open_dirs(base, parts[:-1], False, shown)
    try:
        return os.open(parts[-1], os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | _CLOEXEC, dir_fd=dfd)
    except OSError as err:
        if err.errno == errno.ELOOP:
            raise Refused(shown, "it is a symlink") from err
        raise
    finally:
        os.close(dfd)


def open_path(path: Path | str, lab: Path | str | None = None) -> int:
    """A read descriptor for an absolute path: never through a symlink at the file, which must be a regular file
    with one link. Below lab (when given and the path is inside it) no folder on the way may be a link either.
    FileNotFoundError when absent, Refused otherwise. The caller closes the descriptor.
    """
    text = os.path.abspath(os.fspath(path))
    fd = -1
    if lab is not None:
        for spelling in (os.path.abspath(os.fspath(lab)), real_root(Path(lab))):
            if _inside(text, spelling) and text != spelling:
                fd = open_read(Path(spelling), os.path.relpath(text, spelling))
                break
    if fd < 0:
        try:
            fd = os.open(text, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | _CLOEXEC)
        except OSError as err:
            if err.errno == errno.ELOOP:
                raise Refused(text, "it is a symlink") from err
            raise
    try:
        _check_file(os.fstat(fd), text)
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_path(path: Path | str, lab: Path | str | None = None, cap: int = 64 << 20) -> bytes | None:
    """open_path, then the whole file (at most cap bytes). None when absent; Refused on links or > cap."""
    try:
        fd = open_path(path, lab)
    except FileNotFoundError:
        return None
    try:
        if os.fstat(fd).st_size > cap:
            raise Refused(os.fspath(path), f"it is larger than {cap} bytes")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > cap:
                raise Refused(os.fspath(path), f"it is larger than {cap} bytes")
            chunks.append(chunk)
    finally:
        os.close(fd)


def open_read_nofollow(path: Path | str) -> int:
    # WHY: the release checker opens the zip it just lstat'd. O_NOFOLLOW makes a
    # symlink fail this open instead of being followed; the caller matches fstat to that lstat.
    text = os.fspath(path)
    try:
        return os.open(text, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as err:
        if err.errno == errno.ELOOP:
            raise Refused(text, "it is a symlink") from err
        raise


def unlinked_fd() -> int:
    # WHY: the release checker seals a zip into a private copy. An unlinked 0600 file
    # has no name a planted symlink can replace. The caller closes the descriptor.
    tmp = tempfile.TemporaryFile()
    try:
        fd = os.dup(tmp.fileno())
    finally:
        tmp.close()
    os.set_inheritable(fd, False)
    os.fchmod(fd, 0o600)
    return fd


@contextlib.contextmanager
def open_zip_read(fd: int):
    # WHY: ZipFile can also write and extract. Callers that only scan pass a duplicate
    # of a sealed descriptor; this opens it read-only and closes that fd either way.
    try:
        handle = os.fdopen(fd, "rb")
    except Exception:
        os.close(fd)
        raise
    try:
        with zipfile.ZipFile(handle, "r") as zf:
            yield zf
    finally:
        if not handle.closed:
            handle.close()

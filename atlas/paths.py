"""Where Heliopause keeps its settings and its lab, in one place.

New installs use ~/.heliopause and ~/heliopause-lab. An existing ~/.atlas or ~/atlas-lab keeps working as it is:
nothing here writes unless you run `heliopause migrate --apply --yes`.
"""

from __future__ import annotations

import errno
import os
import secrets
import shlex
import stat
import sys
from datetime import datetime
from pathlib import Path

CONFIG = ".heliopause"
LEGACY_CONFIG = ".atlas"
LAB = "heliopause-lab"
LEGACY_LAB = "atlas-lab"
_HOME_ENV = ("HELIOPAUSE_HOME", "ATLAS_HOME")
_LAB_ENV = ("HELIOPAUSE_LAB", "ATLAS_LAB")


def _home(home: Path | str | None) -> Path:
    return Path(home).expanduser() if home is not None else Path.home()


def _env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def lab_env() -> str:
    """$HELIOPAUSE_LAB, else $ATLAS_LAB, else ''."""
    return _env(_LAB_ENV)


def config_dir(home: Path | str | None = None) -> Path:
    """$HELIOPAUSE_HOME / $ATLAS_HOME, else ~/.heliopause if it exists, else ~/.atlas if it exists, else ~/.heliopause."""
    base = _home(home)
    chosen = _env(_HOME_ENV)
    if chosen:
        return Path(chosen).expanduser()
    if (base / CONFIG).is_dir():
        return base / CONFIG
    if (base / LEGACY_CONFIG).is_dir():
        return base / LEGACY_CONFIG
    return base / CONFIG


def config_rel(home: Path | str | None = None) -> str:
    """The config folder's name below HOME (labfs only writes there): '.heliopause' or '.atlas'."""
    base = _home(home)
    found = config_dir(base)
    if found.parent == base and found.name in (CONFIG, LEGACY_CONFIG):
        return found.name
    return LEGACY_CONFIG if (base / LEGACY_CONFIG).is_dir() and not (base / CONFIG).is_dir() else CONFIG


def pointer_lab(home: Path | str | None = None) -> str:
    """First line of <config>/lab (the last applied lab), or ''. Read only; the folder is not checked."""
    for folder in (config_dir(home), _home(home) / LEGACY_CONFIG):  # the old pointer still counts until migrate
        try:
            lines = (folder / "lab").read_text(encoding="utf-8").strip().splitlines()
        except (OSError, UnicodeError):
            continue
        if lines:
            return lines[0].strip()
    return ""


def default_lab(home: Path | str | None = None) -> Path:
    """~/heliopause-lab if it exists, else ~/atlas-lab if it exists, else ~/heliopause-lab."""
    base = _home(home)
    if (base / LAB).is_dir():
        return base / LAB
    if (base / LEGACY_LAB).is_dir():
        return base / LEGACY_LAB
    return base / LAB


def lab_dir(flag: Path | str | None = None, home: Path | str | None = None) -> Path:
    """--lab, else $HELIOPAUSE_LAB / $ATLAS_LAB, else the <config>/lab pointer, else the default lab folder."""
    chosen = str(flag).strip() if flag is not None else ""
    chosen = chosen or lab_env() or pointer_lab(home)
    return Path(chosen).expanduser() if chosen else default_lab(home)


def is_legacy_layout(home: Path | str | None = None) -> dict:
    """Read-only: is this an old ~/.atlas / ~/atlas-lab setup that `heliopause migrate` could move?"""
    base = _home(home)
    old = [name for name in (LEGACY_CONFIG, LEGACY_LAB) if (base / name).is_dir()]
    new = [name for name in (CONFIG, LAB) if (base / name).is_dir()]
    return {
        "legacy": bool(old) and not new,
        "legacy_present": old,
        "new_present": new,
        "config_dir": str(config_dir(base)),
        "lab_dir": str(lab_dir(home=base)),
    }


# ---- heliopause migrate -------------------------------------------------------------------------------------------


def _tilde(path: Path | str, base: Path) -> str:
    text = str(path)
    root = str(base).rstrip("/")
    return "~" + text[len(root):] if text == root or text.startswith(root + "/") else text


def _scan(base: Path, rel: str) -> tuple[list[tuple[str, int]], list[tuple[str, int, int]]]:
    """(folders [(rel, mode)], files [(rel, mode, size)]) below base/rel. Any link, hard link or odd file is Refused."""
    from atlas import labfs

    folders: list[tuple[str, int]] = []
    files: list[tuple[str, int, int]] = []

    def walk(here: str) -> None:
        top = labfs.lstat(base, here)
        if top is None or stat.S_ISLNK(top.st_mode) or not stat.S_ISDIR(top.st_mode):
            raise labfs.Refused(os.path.join(str(base), here), "it is not a plain folder (a symlink is never followed)")
        folders.append((here, stat.S_IMODE(top.st_mode)))
        for name in labfs.listdir(base, here) or []:
            child = f"{here}/{name}"
            st = labfs.lstat(base, child)
            if st is not None and stat.S_ISDIR(st.st_mode):
                walk(child)
            elif st is not None and stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                files.append((child, stat.S_IMODE(st.st_mode), st.st_size))
            else:
                raise labfs.Refused(os.path.join(str(base), child), "it is a symlink, hard link or special file")

    walk(rel)
    return folders, files


def _copy(base: Path, src: str, dst: str, tree: tuple) -> None:
    """Copy a scanned tree to base/dst through labfs, keeping each file's and folder's mode. Never replaces a file."""
    from atlas import labfs

    folders, files = tree
    for rel, mode in folders:
        target = dst + rel[len(src):]
        labfs.makedirs(base, target, mode | 0o700)
    for rel, mode, size in files:
        data = labfs.read(base, rel, cap=size) or b""
        labfs.write(base, dst + rel[len(src):], data, mode, new_only=True)
    for rel, mode in reversed(folders):
        labfs.chmod(base, dst + rel[len(src):], mode, folder=True)


def _verify_copy(base: Path, src: str, dst: str, tree: tuple) -> None:
    """Confirm every scanned folder and regular file exists in base/dst with the expected size."""
    from atlas import labfs

    folders, files = tree
    for rel, _mode in folders:
        target = dst + rel[len(src):]
        st = labfs.lstat(base, target)
        if st is None or not stat.S_ISDIR(st.st_mode):
            raise OSError(errno.EIO, "copied folder missing", os.path.join(str(base), target))
    for rel, _mode, size in files:
        target = dst + rel[len(src):]
        st = labfs.lstat(base, target)
        if st is None or not stat.S_ISREG(st.st_mode) or st.st_size != size:
            raise OSError(errno.EIO, "copied file incomplete", os.path.join(str(base), target))


def _rewrite_copied_paths(base: Path, cfg_rel: str) -> None:
    """Update copied <config>/lab pointer and copied hook scripts that still point at ~/atlas-lab."""
    from atlas import labfs

    old_lab = base / LEGACY_LAB
    new_lab = base / LAB
    pointer = labfs.read(base, f"{cfg_rel}/lab", cap=4096)
    if pointer is not None:
        raw = pointer.decode("utf-8", "replace").strip()
        if raw and Path(raw).expanduser().resolve() == old_lab.resolve():
            labfs.write(base, f"{cfg_rel}/lab", (str(new_lab) + "\n").encode("utf-8"))
    old_targets = {f"LAB_DIR={shlex.quote(str(old_lab))}", f"LAB_DIR={shlex.quote(str(old_lab.resolve()))}"}
    new_assign = f"LAB_DIR={shlex.quote(str(new_lab))}"
    for name in labfs.listdir(base, f"{cfg_rel}/hooks") or []:
        rel = f"{cfg_rel}/hooks/{name}"
        raw_bytes = labfs.read(base, rel, cap=1 << 20)
        if raw_bytes is None:
            continue
        text = raw_bytes.decode("utf-8", "replace")
        updated = text
        for old_assign in old_targets:
            updated = updated.replace(old_assign, new_assign)
        if updated != text:
            labfs.write(base, rel, updated.encode("utf-8"))


def _planned_lab(base: Path, pairs: list[tuple[str, str]]) -> tuple[Path, Path]:
    """(target_lab after migrate, source_lab currently on disk) for hook re-pointing."""
    moving_lab = any(new == LAB for _, new in pairs)
    raw_ptr = pointer_lab(base)
    if raw_ptr:
        ptr_path = Path(raw_ptr).expanduser()
        if moving_lab and ptr_path.resolve() == (base / LEGACY_LAB).resolve():
            return base / LAB, base / LEGACY_LAB
        return ptr_path, ptr_path
    if moving_lab:
        return base / LAB, base / LEGACY_LAB
    cur = default_lab(base)
    return cur, cur


def migrate_legacy_layout(home: Path | str | None = None, apply: bool = False, yes: bool = False) -> int:
    """Plan (default) or do the move of ~/.atlas -> ~/.heliopause and ~/atlas-lab -> ~/heliopause-lab.

    Exit 0: planned, done, or nothing to move. 1: refused (a link, or the new folder already exists). 2: --apply without --yes.
    The old folders are never changed. A backup copy of them is made first.
    """
    from atlas import hooks, labfs

    base = _home(home)
    pairs = [(a, b) for a, b in ((LEGACY_CONFIG, CONFIG), (LEGACY_LAB, LAB)) if (base / a).is_dir() or (base / a).is_symlink()]
    if not pairs:
        print("heliopause migrate: no ~/.atlas or ~/atlas-lab found. Nothing to migrate.")
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f".heliopause-backup-{stamp}"
    trees = {}
    try:
        for old, new in pairs:
            if os.path.lexists(base / new):
                raise labfs.Refused(str(base / new), "it already exists, and migrate never overwrites it")
            trees[old] = _scan(base, old)
        if os.path.lexists(base / backup):
            raise labfs.Refused(str(base / backup), "the backup folder already exists")
    except labfs.Refused as err:
        print(f"heliopause migrate: {err}. Nothing changed.", file=sys.stderr)
        return 1

    planned_lab, source_lab = _planned_lab(base, pairs)
    repoint_hooks = (source_lab / "atlas.json").is_file() and hooks.has_legacy_hooks(planned_lab, home=base)

    print(f"heliopause migrate: {'plan' if not apply else 'applying'} (your old folders are never changed)")
    print("  notice  Stop Heliopause before migrating (if running) so nothing is written to the old folder during copy.")
    for old, new in pairs:
        folders, files = trees[old]
        print(f"  copy    {_tilde(base / old, base)} -> {_tilde(base / new, base)} ({len(files)} files, {len(folders)} folders, modes kept)")
    print(f"  backup  {_tilde(base / backup, base)} (a copy of the old folders, made first)")
    print("  pointer <config>/lab is updated to the new lab folder when it named the old one")
    if repoint_hooks:
        print(
            f"  hooks   re-point ~/.claude/settings.json hooks to ~/{CONFIG}/hooks/ "
            f"for {_tilde(planned_lab, base)} (with backup)"
        )
    roll = " ".join(_tilde(base / new, base) for _, new in pairs)
    print(f"  undo    rm -rf {roll}   (the old folders and the backup stay)")
    if not apply:
        print("Nothing changed. To apply: heliopause migrate --apply --yes")
        return 0
    if not yes:
        print("heliopause migrate: --apply needs --yes. Nothing changed.")
        return 2

    tag = f"migrating-{os.getpid()}-{secrets.token_hex(4)}"
    staged: list[tuple[str, str, str]] = []
    published: list[tuple[str, str]] = []
    try:
        labfs.makedirs(base, backup, 0o700)
        for old, new in pairs:
            _copy(base, old, f"{backup}/{old}", trees[old])
        for old, new in pairs:
            stage = f".{new.lstrip('.')}.{tag}"
            if os.path.lexists(base / stage):
                raise labfs.Refused(str(base / stage), "staging folder already exists")
            staged.append((old, new, stage))
            labfs.makedirs(base, stage, 0o700)
            _copy(base, old, stage, trees[old])
            _verify_copy(base, old, stage, trees[old])
        stage_map = {new: stage for _, new, stage in staged}
        if CONFIG in stage_map and LAB in stage_map:
            _rewrite_copied_paths(base, stage_map[CONFIG])
        for _old, new, stage in staged:
            labfs.rename_dir(base, stage, new)
            published.append((stage, new))
    except BaseException as err:
        rollback_errors: list[tuple[str, OSError]] = []
        leftovers: list[str] = []
        for stage, new in reversed(published):
            try:
                labfs.rename_dir(base, new, stage)
            except OSError as rb_err:
                rollback_errors.append((new, rb_err))
                if new not in leftovers:
                    leftovers.append(new)
        for _old, _new, stage in reversed(staged):
            try:
                labfs.remove_tree(base, stage)
            except OSError as rb_err:
                rollback_errors.append((stage, rb_err))
                if stage not in leftovers:
                    leftovers.append(stage)
        if not isinstance(err, OSError):
            raise
        if not rollback_errors:
            print(
                f"heliopause migrate: {err}. Your old folders are untouched and no partial new folder was left behind. "
                f"Backup: {_tilde(base / backup, base)}",
                file=sys.stderr,
            )
            return 1
        for _old, new, stage in staged:
            for rel in (new, stage):
                if os.path.lexists(base / rel) and rel not in leftovers:
                    leftovers.append(rel)
        shown = " ".join(_tilde(base / rel, base) for rel in leftovers)
        reasons = "; ".join(f"{_tilde(base / rel, base)}: {rb}" for rel, rb in rollback_errors)
        print(
            f"heliopause migrate: {err}. Rollback did not finish ({reasons}). "
            f"Paths that may still exist: {shown}. Remove them with: rm -rf {shown}. "
            f"Your old folders and the backup ({_tilde(base / backup, base)}) are untouched.",
            file=sys.stderr,
        )
        return 1

    if repoint_hooks:
        try:
            rc = hooks.run(["--lab", str(planned_lab), "--yes"], home=base)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
        if rc != 0:
            print(
                "heliopause migrate: folders were copied, but hooks could not be updated automatically. "
                f"Fix the error above and run: heliopause hooks --lab {shlex.quote(str(planned_lab))} --yes",
                file=sys.stderr,
            )
            print(f"Backup: {_tilde(base / backup, base)}. Undo: rm -rf {roll}", file=sys.stderr)
            return 1

    print(f"Migrated. Backup: {_tilde(base / backup, base)}. Old folders kept as they were.")
    if repoint_hooks:
        print(f"Hooks updated in ~/.claude/settings.json for {_tilde(planned_lab, base)} (backup in {_tilde(planned_lab / 'backups', base)}).")
    print(f"Undo: rm -rf {roll}")
    return 0

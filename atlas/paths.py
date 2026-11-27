"""Where Heliopause keeps its settings and its lab, in one place.

New installs use ~/.heliopause and ~/heliopause-lab. An existing ~/.atlas or ~/atlas-lab keeps working as it is:
nothing here writes to disk.
"""

from __future__ import annotations

import os
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
    """Read-only: is this an old ~/.atlas / ~/atlas-lab setup?"""
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

"""Initialize a new solo builder lab folder."""

from __future__ import annotations

import argparse, json, os, shlex, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas import labfs

_TEMPLATE = Path(__file__).resolve().parent / "templates" / "solo"
_DIRS = ("inbox", "done", "briefs", "queue/inbox", "queue/running", "queue/done", "queue/failed", "nodes", "decisions")


def _local_tz() -> str:
    # ponytail: /etc/localtime symlink covers macOS and systemd Linux; falls back to UTC.
    try:
        target = os.readlink("/etc/localtime")
        if "zoneinfo/" in target:
            cand = target.split("zoneinfo/", 1)[1].lstrip("/")
            ZoneInfo(cand)
            return cand
    except Exception:
        pass
    return "UTC"


def _valid_owner(owner: str) -> bool:
    # ponytail: owner validation caps at 40 printable chars without dot or newline.
    return 1 <= len(owner) <= 40 and owner.isprintable() and not any(c in owner for c in ("·", "\n", "\r"))


def atlas_document(owner: str) -> dict:
    return {
        "v": 1,
        "owner": owner,
        "tz": _local_tz(),
        "nodes": [
            {"id": "lead", "label": "Lead", "role": "lab"},
            {"id": "worker", "label": "Worker", "role": "factory"},
        ],
    }


def wrapper_text(dest: Path) -> str:
    """The text of DIR/bin/atlas. -I drops cwd and PYTHONPATH; the repo is inserted so this checkout is the one that runs."""
    repo = Path(__file__).resolve().parent.parent
    return (
        "#!/bin/sh\nATLAS_LAB=%s exec python3 -I -B -c "
        "'import sys; sys.path.insert(0, sys.argv[1]); sys.argv = [\"atlas\"] + sys.argv[2:]; "
        "from atlas.__main__ import main; main()' %s \"$@\"\n"
        % (shlex.quote(str(dest.resolve())), shlex.quote(str(repo)))
    )


def _wrapper(dest: Path) -> bool:
    return labfs.create(dest, "bin/atlas", wrapper_text(dest).encode("utf-8"), 0o755)


def ensure(dest: Path, owner: str = "Owner") -> list[Path]:
    """Create any missing solo-lab files. Existing files, and symlinks, are left as they are and never followed.

    Each file is written whole or not at all (labfs: temp file, then a hard link or an exclusive create).
    A symlinked subfolder is refused (labfs.Refused, an OSError); there is no option to follow one.
    """
    written: list[Path] = []
    os.close(labfs.open_dir(dest, create=True))  # the root is the caller's choice; made if missing
    for item in sorted(_TEMPLATE.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(_TEMPLATE)
        mode = 0o755 if rel.parts[:1] == ("bin",) else 0o644
        data = item.read_bytes()
        if rel.as_posix() == "board.md" and labfs.lstat(dest, "board.md") is None:
            # The first board line goes into the same write, so board.md never exists without it.
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            text = data.decode("utf-8")
            text += ("\n" if text and not text.endswith("\n") else "") + f"{now} · Lead · done · Lab created · atlas init\n"
            data = text.encode("utf-8")
        if labfs.create(dest, rel.as_posix(), data, mode):
            written.append(dest / rel)
    for name in _DIRS:
        labfs.makedirs(dest, name)
    # atlas.json carries the owner name: owner-only.
    if labfs.create(dest, "atlas.json", (json.dumps(atlas_document(owner), indent=2) + "\n").encode("utf-8"), 0o600):
        written.append(dest / "atlas.json")
    if labfs.create(dest, "nodes/lead.md", b"Lead\nRole: lab\nOrchestrator chat: reads the board first, plans, delegates, and reports to the owner.\n", 0o644):
        written.append(dest / "nodes" / "lead.md")
    if labfs.create(dest, "nodes/worker.md", b"Worker\nRole: factory\nFactory worker: executes tasks dispatched from the queue and reports results.\n", 0o644):
        written.append(dest / "nodes" / "worker.md")
    if _wrapper(dest):
        written.append(dest / "bin" / "atlas")
    return written


def _fresh(dest: Path) -> bool:
    """True when dest is absent, or an empty real folder (not a symlink)."""
    if dest.is_symlink():
        return False
    if not dest.exists():
        return True
    return dest.is_dir() and not any(dest.iterdir())


def _confirm(yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print("atlas init: non-interactive terminal: re-run with --yes to create the lab", file=sys.stderr)
        return False
    try:
        answer = input("Create this lab? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer in ("y", "yes"):
        return True
    print("Nothing created.")
    return False


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas init", description="Initialize a new lab folder")
    parser.add_argument("--dir", default="~/atlas-lab")
    parser.add_argument("--owner", default="Owner")
    parser.add_argument("--memory", action="store_true", help="install memory lite into the new lab (when this build includes it)")
    parser.add_argument("--yes", action="store_true", help="create the lab without the confirmation prompt")
    args = parser.parse_args(argv)

    if not _valid_owner(args.owner):
        print(f"atlas init: invalid --owner {args.owner!r}", file=sys.stderr)
        raise SystemExit(2)

    dest = Path(args.dir).expanduser()
    labfs.refuse_lab(dest, "atlas init")
    if not _fresh(dest):
        print(f"atlas init: {dest} exists and is not empty, or is a symlink", file=sys.stderr)
        raise SystemExit(1)
    # Ask before writing: init only ever creates this one new folder, and says how to undo it.
    shown = shlex.quote(str(dest))
    folders = ", ".join(["bin/", *dict.fromkeys(name.split("/", 1)[0] + "/" for name in _DIRS)])
    print(f"plan: create the lab folder {shown} (atlas.json, board.md, BUS.md, feed.md and {folders})")
    print("plan: nothing outside that folder is read or written")
    print(f"undo: python3 -m atlas uninstall --lab {shown}")
    if not _confirm(args.yes):
        return 1

    # WHY: the folder may have changed while the prompt waited. Check again, then make it owner-only (0700)
    # before anything goes in, so nobody else can plant a file or a link in it.
    if not _fresh(dest):
        print(f"atlas init: {dest} changed while waiting: it is no longer empty, or is a symlink. Nothing created.", file=sys.stderr)
        return 1
    try:
        # The parent is the user's own path; the lab folder is created (or an empty one kept) and set to 0700.
        labfs.makedirs(dest.parent, dest.name, 0o700)
        if not _fresh(dest):
            raise FileExistsError(f"{dest} is no longer empty")
        labfs.chmod(dest.parent, dest.name, 0o700, folder=True)
    except OSError as err:
        print(f"atlas init: {dest}: {err}. Nothing created.", file=sys.stderr)
        return 1

    try:
        ensure(dest, args.owner)
    except OSError as err:
        print(f"atlas init: {err}. The lab is incomplete; undo: python3 -m atlas uninstall --lab {shlex.quote(str(dest))}", file=sys.stderr)
        return 1
    if args.memory:
        from atlas.memory import NOT_INCLUDED, available, install

        if available():
            install(dest)
        else:
            # Not an error: the lab is still created, just without memory lite.
            print(f"atlas init: --memory skipped: {NOT_INCLUDED}", file=sys.stderr)

    print(f"Created lab in {dest}")
    print(f"python3 -m atlas --lab {dest}, then open http://127.0.0.1:4300")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())

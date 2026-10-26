from __future__ import annotations

import argparse, copy, errno, http.client, json, os, shutil, socket, stat, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas import labfs

_NEED = 200 * 1024 * 1024

def _lab_dir(raw: str, prog: str = "atlas") -> Path:
    path = Path(raw).expanduser()
    path = path if path.is_absolute() else path.resolve()
    labfs.refuse_lab(path, prog)
    return path

def _commands(lab: Path) -> set:
    """This lab's hook commands in settings.json (the scripts live in ~/.atlas/hooks, not in the lab)."""
    from atlas.hooks import hook_commands

    return set(hook_commands(lab))

def _all_commands(lab: Path) -> set:
    """Current and pre-move (DIR/bin/...) commands: uninstall removes both."""
    from atlas.hooks import old_commands

    return _commands(lab) | old_commands(lab)

def _kept(lab: Path) -> Path:
    return Path(str(lab) + ".decisions-kept")

def _has_decisions(lab: Path) -> bool:
    folder = lab / "decisions"
    return folder.is_dir() and any(folder.iterdir())

def _stamp(lab: Path) -> str:
    try:
        data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
        now = datetime.now(ZoneInfo(data["tz"]))
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        now = datetime.now().astimezone()
    return now.strftime("%Y-%m-%d %H:%M")

def _board(lab: Path, line: str) -> None:
    # labfs: a board.md that is a symlink or hard link (planted by an agent) is refused, never appended through.
    labfs.append(lab, "board.md", (line + "\n").encode("utf-8"), 0o644, make_dirs=False)

def board_line(lab: Path, text: str) -> None:
    """Append "<stamp> · text" to DIR/board.md (one line, at most 4000 characters)."""
    one = " ".join(str(text).splitlines())[:4000]
    _board(lab, f"{_stamp(lab)} · {one}")

def echo_job(lab: Path) -> bool:
    done_dir = lab / "queue" / "done"
    try:
        if labfs.check(lab, "queue/done", folder=True) and any(done_dir.glob("echo-*.md")):
            return False
    except labfs.Refused:
        pass  # the writes below refuse it with the path named
    stamp, name = _stamp(lab), "echo-hello.md"
    front = f"---\nid: echo-hello\nfrom: Owner\nto: Worker\ntype: job\ncreated: {stamp}\n---\n"
    inbox, running, done = f"queue/inbox/{name}", f"queue/running/{name}", f"queue/done/{name}"
    for rel in ("queue/inbox", "queue/running", "queue/done"):
        labfs.makedirs(lab, rel)
    # Every step through labfs: no link in the queue folders or at these names is followed.
    labfs.write(lab, inbox, front.encode("utf-8"), new_only=True)
    labfs.rename(lab, inbox, running)
    _board(lab, f"{stamp} · Worker · working · Echo job started")
    labfs.rename(lab, running, done)
    labfs.write(lab, done, (front + "echo: hello\n").encode("utf-8"))
    _board(lab, f"{stamp} · Worker · done · Echo job delivered")
    return True

def _parser(prog: str, port: bool = False, removing: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--lab", default="~/atlas-lab")
    if port:
        parser.add_argument("--port", type=int, default=4300)
    if removing:
        parser.add_argument("--yes", action="store_true")
        parser.add_argument("--delete-decisions", action="store_true")
    return parser

def up(argv: list[str] | None = None) -> int:
    args = _parser("atlas up", port=True).parse_args(argv)
    lab = _lab_dir(args.lab, "atlas up")
    if not (lab / "atlas.json").is_file():
        print(f"atlas up: run atlas init first ({lab / 'atlas.json'} not found)", file=sys.stderr)
        raise SystemExit(2)
    try:
        echo_job(lab)
    except OSError as err:
        # A planted link stops only the demo job; nothing is written through it, and the board still starts.
        print(f"atlas up: echo job skipped: {err}", file=sys.stderr)
    # WHY: collect reads ATLAS_LAB once, at import, so it must be set before atlas.__main__ loads.
    # --root is the lab too, so the write token lives in the lab and nothing lands in ~.
    os.environ["ATLAS_LAB"] = str(lab)
    try:
        from atlas.__main__ import main
    except Exception as err:
        if type(err).__name__ != "LabConfigError":
            raise
        print(err, file=sys.stderr)
        raise SystemExit(2) from None
    main(["--lab", str(lab), "--root", str(lab), "--port", str(args.port)])
    return 0

def _ok(name: str, good: bool, fix: str) -> bool:
    print(f"{name}: ok" if good else f"{name}: FIX: {fix}")
    return good

def _port_ok(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        err = sock.connect_ex(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        sock.close()
    if err == errno.ECONNREFUSED:
        return True
    if err != 0:
        return False
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        conn.request("GET", "/api/state")
        resp = conn.getresponse()
        good = resp.status == 200
        resp.read()
        conn.close()
        return good
    except (OSError, http.client.HTTPException):
        return False

def _registered() -> set:
    path = Path.home() / ".claude" / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    found, hooks = set(), data.get("hooks") if isinstance(data, dict) else None
    if isinstance(hooks, dict):
        for groups in hooks.values():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    continue
                found.update(h.get("command") for h in group["hooks"] if isinstance(h, dict))
    return found

def _hooks_ok(lab: Path) -> bool:
    return _commands(lab) <= _registered()

def _hooks_old(lab: Path) -> bool:
    from atlas.hooks import old_commands

    return bool(old_commands(lab) & _registered())

def doctor(argv: list[str] | None = None) -> int:
    args = _parser("atlas doctor", port=True).parse_args(argv)
    lab = _lab_dir(args.lab)
    bad = not _ok("python", sys.version_info >= (3, 9), "install Python 3.9 or newer")
    try:
        # Importing collect can itself raise LabConfigError when ATLAS_LAB names a broken lab.
        from atlas.collect import load_lab
        load_lab(lab)
        good, fix = True, ""
    except Exception as err:
        if type(err).__name__ != "LabConfigError":
            raise
        good, fix = False, str(err)
    bad += not _ok("lab", good, fix)
    board, feed = lab / "board.md", lab / "bin" / "feed-append"
    bad += not _ok("board", board.is_file() and os.access(board, os.W_OK), f"create a writable {board}")
    bad += not _ok("feed-append", feed.is_file() and os.access(feed, os.X_OK), f"make {feed} executable")
    bad += not _ok("port", _port_ok(args.port), f"free 127.0.0.1:{args.port} or serve GET /api/state there")
    if _hooks_ok(lab):
        print("hooks: ok")
    elif _hooks_old(lab):
        print("hooks: FIX: the hook scripts are still in the lab, where agents can edit them: run atlas hooks to move them")
    else:
        print("hooks: not connected: run atlas hooks")
    from atlas.memory import available as _memory_available, installed as _memory_installed

    lite = _memory_installed(lab)

    if lite and _memory_available():
        from atlas.memory import home_tools_ok as _tools_ok, tools_dir as _tools_dir

        if _tools_ok():
            print("memory: ok (lite installed)")
        else:
            print(f"memory: FIX: the recall hook's tools are not in {_tools_dir()} (owner-only): run atlas memory")
    elif lite:
        print("memory: ok (lite installed)")
    elif _memory_available():
        print("memory: not installed (optional: atlas memory)")
    else:
        print("memory: not included in this build")
    target = lab if lab.exists() else lab.parent
    try:
        room = shutil.disk_usage(target).free >= _NEED
    except OSError:
        room = False
    bad += not _ok("disk", room, f"free at least 200 MB under {lab}")
    return 1 if bad else 0

def _strip(data: dict, commands: set) -> dict:
    new = copy.deepcopy(data)
    hooks = new.get("hooks")
    if not isinstance(hooks, dict):
        return new
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            kept = [h for h in group["hooks"] if not (isinstance(h, dict) and h.get("command") in commands)]
            if not kept:
                continue
            if len(kept) != len(group["hooks"]):
                group = dict(group)
                group["hooks"] = kept
            kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        new.pop("hooks", None)
    return new

def _unhook(lab: Path) -> bool:
    """Remove our hook entries (current and pre-move). False when settings.json exists but cannot be read safely."""
    settings_path = Path.home() / ".claude" / "settings.json"
    try:
        root, rel = labfs.home(".claude/settings.json", lab=lab)
        raw = labfs.read(root, rel)
    except OSError as err:
        print(f"atlas uninstall: left {settings_path} unchanged ({err})", file=sys.stderr)
        return False
    if raw is None:
        return True
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as err:
        print(f"atlas uninstall: left {settings_path} unchanged ({err})", file=sys.stderr)
        return False
    if not isinstance(data, dict):
        print(f"atlas uninstall: left {settings_path} unchanged (not an object)", file=sys.stderr)
        return False
    new = _strip(data, _all_commands(lab))
    if new == data:
        return True
    text = (json.dumps(new, indent=2) + "\n").encode("utf-8")
    # ponytail: names sort in time order. Backups live in the lab, which agents can write: each is read through
    # labfs (no link, no hard link) and used only as a signal. The bytes written are always ours, at 0600.
    backups = []
    try:
        if labfs.check(lab, "backups", folder=True):
            backups = sorted(path.name for path in (lab / "backups").glob("settings.json.*"))
    except labfs.Refused:
        backups = []
    if backups:
        try:
            saved = json.loads((labfs.read(lab, f"backups/{backups[-1]}") or b"").decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            saved = None
        if saved == new:
            labfs.write(root, rel, text, 0o600)
            return True
    created = False
    try:
        created = labfs.lstat(lab, "backups/created-settings") is not None
    except labfs.Refused:
        pass
    if new == {} and not backups and rel == ".claude/settings.json":
        labfs.unlink(root, rel)
        if created:
            labfs.rmdir(root, ".claude")
        return True
    labfs.write(root, rel, text)
    return True

def _drop_hook_scripts(lab: Path) -> None:
    """Remove this lab's scripts from ~/.atlas/hooks (and the folder when it is left empty)."""
    from atlas.hooks import HOOKS_DIR, hook_rels

    for rel in hook_rels(lab):
        try:
            root, name = labfs.home(rel, lab=lab)
            if labfs.unlink(root, name):
                print(f"removed {Path.home() / rel}")
        except OSError as err:
            print(f"atlas uninstall: left {Path.home() / rel} ({err})", file=sys.stderr)
    # Only empty folders go: ~/.atlas also holds the adopt pointer and the plugin copy when those are in use.
    try:
        root, _name = labfs.home(hook_rels(lab)[0], lab=lab)
    except OSError:
        return
    if labfs.rmdir(root, HOOKS_DIR):
        labfs.rmdir(root, ".atlas")

def _confirm(yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print("re-run with --yes to uninstall")
        return False
    try:
        answer = input("Uninstall Heliopause? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")

def uninstall(argv: list[str] | None = None) -> int:
    args = _parser("atlas uninstall", removing=True).parse_args(argv)
    lab = _lab_dir(args.lab); kept = _kept(lab)
    # WHY: uninstall deletes a folder. Only one atlas init made, never home or anything above it.
    home = Path.home().resolve()
    real = lab.resolve()
    if real == home or real in home.parents or not (lab / "atlas.json").is_file() or not (lab / "BUS.md").is_file():
        print(f"atlas uninstall: {lab} is not a Heliopause lab folder (needs atlas.json and BUS.md, and must not be your home or above it)", file=sys.stderr)
        raise SystemExit(2)
    print(f"plan: remove Heliopause hooks for {lab} from {Path.home() / '.claude' / 'settings.json'}")
    from atlas.hooks import hook_rels

    for rel in hook_rels(lab):
        if os.path.lexists(Path.home() / rel):
            print(f"plan: delete {Path.home() / rel}")
    if _has_decisions(lab) and not args.delete_decisions:
        print(f"plan: move {lab / 'decisions'} to {kept}")
    print(f"plan: delete {lab}")
    if not _confirm(args.yes):
        return 0
    if _has_decisions(lab) and not args.delete_decisions and kept.exists():
        print(f"atlas uninstall: {kept} already exists", file=sys.stderr)
        raise SystemExit(1)
    if not _unhook(lab):
        print("atlas uninstall: fix or remove the Heliopause hooks in ~/.claude/settings.json first; nothing was deleted", file=sys.stderr)
        raise SystemExit(1)
    _drop_hook_scripts(lab)
    moved = _has_decisions(lab) and not args.delete_decisions
    if moved:
        source = lab / "decisions"
        if not stat.S_ISDIR(os.lstat(source).st_mode):
            print(f"atlas uninstall: {source} is not a real folder (a link?); nothing was moved or deleted. Move it yourself, or re-run with --delete-decisions", file=sys.stderr)
            raise SystemExit(1)
        # A rename moves the folder entry itself and never writes through a link (tests/fs_guard_allow.txt).
        try:
            os.rename(source, kept)
        except OSError as err:
            if err.errno != errno.EXDEV:
                raise
            # Another filesystem: copy with links kept as links (never followed), then remove the original.
            shutil.move(str(source), str(kept))
    if real.is_dir():
        shutil.rmtree(real)
        if lab.is_symlink():
            lab.unlink()
    elif lab.exists():
        print(f"atlas uninstall: {lab} is not a directory", file=sys.stderr)
        raise SystemExit(1)
    if moved:
        print(f"kept decisions at {kept}")
    return 0

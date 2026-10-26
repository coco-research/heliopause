"""SessionStart context for Claude Code, Grok and Cursor, and the local install.

The hook never uses the process working directory. Cursor starts hooks with cwd
set to the plugin root, so the session folder comes from stdin:

- Claude Code: ``cwd``
- Cursor: ``workspace_roots`` (hook docs) or ``workspace``
- Grok: ``workspaceRoot``

A payload that does not carry that host's field is silent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from atlas import __version__, labfs

_CONTEXT_MAX = 10000  # SessionStart additionalContext cap
# ponytail: one read, not a stream. A bigger hook payload is ignored.
_STDIN_CAP = 1_000_000
_MARKET_NAME = "heliopause-local"
_PLUGIN_ID = "heliopause@heliopause-local"
_PLUGIN_NAME = "heliopause"
_HOSTS = ("claude", "grok", "cursor")
_RULES = (
    "Read the board first.",
    "Never read other chats' transcripts.",
    "Never archive, message, or retitle other chats.",
    "Post a board line at each state change.",
    "Ask the owner before anything outside the lab.",
    "Do not edit files outside the lab unless the owner names them.",
    "Leave sorting and summaries to Heliopause code. The owner confirms each step.",
)


def _source() -> Path:
    return Path(__file__).resolve().parent.parent / "plugin" / "heliopause"


def _home_market() -> Path:
    return Path.home() / "Heliopause" / "marketplace"


def _clear_market(root: Path) -> None:
    if not root.exists():
        return
    try:
        data = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict) or data.get("name") != _MARKET_NAME:
        raise OSError(f"{root} was not made by plugin install and was left as is")
    _drop_tree(os.path.relpath(root, Path.home()))  # the folder just checked, through labfs


def _inside(folder: str, root: str) -> bool:
    # ponytail: normpath, not realpath. A symlink whose text points outside can still match.
    if not folder or not root or not os.path.isabs(folder) or not os.path.isabs(root):
        return False
    folder = os.path.normpath(folder)
    root = os.path.normpath(root)
    return folder == root or folder.startswith(root + os.sep)


def _paths(value: object) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, dict):
        found = []
        for key in ("path", "root", "folder", "cwd"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                found.append(item.strip())
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_paths(item))
        return found
    return []


def _folders(payload: dict) -> list[str]:
    # Cursor's cwd is the plugin root. The workspace field is workspace_roots, or workspace.
    # Missing that field stays silent: do not read cwd, and do not read the process cwd.
    if "cursor_version" in payload or "workspace_roots" in payload:
        if "workspace_roots" in payload:
            return _paths(payload.get("workspace_roots"))
        if "workspace" in payload:
            return _paths(payload.get("workspace"))
        return []
    # Grok's field is workspaceRoot. Its cwd is not a fallback.
    if "hookEventName" in payload or "workspaceRoot" in payload:
        if "workspaceRoot" not in payload:
            return []
        return _paths(payload.get("workspaceRoot"))
    if "workspace" in payload:
        return _paths(payload.get("workspace"))
    if "cwd" in payload:
        return _paths(payload.get("cwd"))
    return []


def _find_lab() -> Path | None:
    """The lab the plugin serves, or None. A lab at, inside or around ~/.atlas or ~/.claude is ignored."""
    lab = _locate_lab()
    if lab is None:
        return None
    try:
        labfs.check_lab(lab)
    except labfs.Refused as err:
        print(f"atlas plugin: ignoring the lab: {err}", file=sys.stderr)
        return None
    return lab


def _locate_lab() -> Path | None:
    raw = os.environ.get("ATLAS_LAB", "").strip()
    if raw:
        path = Path(raw)
        if path.is_absolute() and (path / "atlas.json").is_file():
            return path
        return None
    pointer = Path.home() / ".atlas" / "lab"
    try:
        text = pointer.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        text = ""
    if text:
        path = Path(text.splitlines()[0].strip())
        if path.is_absolute() and (path / "atlas.json").is_file():
            return path
        return None
    default = Path.home() / "atlas-lab"
    if (default / "atlas.json").is_file():
        return default
    return None


def _ok_name(name: str) -> bool:
    text = name.strip()
    return 1 <= len(text) <= 80 and text.isprintable()


def _lead(data: dict) -> str:
    lead = data.get("lead")
    if isinstance(lead, str) and _ok_name(lead):
        return lead.strip()
    nodes = data.get("nodes")
    if isinstance(nodes, list):
        for item in nodes:
            if not isinstance(item, dict) or item.get("id") != "lead":
                continue
            label = item.get("label")
            if isinstance(label, str) and _ok_name(label):
                return label.strip()
    return "Lead"


def _marked(data: dict) -> list[str]:
    raw = data.get("marked")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


def _allowed(folder: str, lab: Path, data: dict) -> bool:
    if _inside(folder, str(lab)):
        return True
    return any(_inside(folder, item) for item in _marked(data))


def _first_run(lab: Path) -> str:
    # ponytail: first session only, so the repo check's gh calls (GitHub on only) run once.
    from atlas.first_finding import NONE, top

    found = top(lab, Path.home())
    sentence = f"Here's one thing I found in your setup: {found['line']}" if found else NONE
    return (
        f"Open with exactly one sentence: '{sentence}' "
        "Then ask: 'Which project should I watch first?' Don't list folders."
    )


def _context(lab: Path, lead: str, first: bool = False, data: dict | None = None) -> str:
    lines = [
        f"You are {lead}, the Heliopause Lead for this lab.",
        f"Board: {lab / 'board.md'}",
        f"Feed: {lab / 'feed.md'}",
        f"Start here: {lab / 'START-HERE.md'}",
        "Rules:",
        *(f"- {rule}" for rule in _RULES),
    ]
    if first:
        lines.extend(["", _first_run(lab)])
    text = "\n".join(lines)
    if isinstance(data, dict) and isinstance(data.get("roles"), dict):
        try:
            from atlas.role_brief import hook_extra

            extra = hook_extra(lab, data)
        except Exception:
            extra = ""
        if extra:
            text = f"{text}\n\n{extra}"
    if len(text) >= _CONTEXT_MAX:
        text = text[: _CONTEXT_MAX - 1]
    return text


def _emit() -> None:
    payload = json.loads(sys.stdin.read(_STDIN_CAP))
    if not isinstance(payload, dict):
        return
    lab = _find_lab()
    if lab is None:
        return
    data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return
    folders = _folders(payload)
    if not any(_allowed(folder, lab, data) for folder in folders):
        return
    in_lab = any(_inside(folder, str(lab)) for folder in folders)
    text = _context(lab, _lead(data), first=in_lab and not _marked(data), data=data)
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": text,
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")


def _grok_log(entry: str) -> None:
    """Append one grok-hooks.log line. Silent when no lab is known."""
    # https://docs.x.ai/build/features/hooks — non-empty GROK_HOOK_EVENT is a Grok Build session.
    raw = os.environ.get("GROK_HOOK_EVENT") or ""
    if not raw:
        return
    try:
        lab = _find_lab()
        if lab is None:
            return
        token = re.sub(r"[^A-Za-z0-9_]", "", raw)[:40]
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # labfs.LAB_TARGETS: the exact file. makedirs creates its parent; append refuses a link.
        rel = labfs.lab_target("logs/grok-hooks.log")
        parent, _sep, _name = rel.rpartition("/")
        if parent:
            labfs.makedirs(lab, parent, 0o700)
        labfs.append(lab, rel, f"{stamp} grok {entry} {token}\n".encode("utf-8"), 0o600, make_dirs=False)
    except Exception:
        # WHY: a Grok session must stay silent. Same fail-soft rule as session_start.
        return


def session_start() -> int:
    # https://docs.x.ai/build/features/hooks — non-empty GROK_HOOK_EVENT is a Grok Build session.
    if os.environ.get("GROK_HOOK_EVENT"):
        _grok_log("session-start")
        return 0
    try:
        _emit()
    except (Exception, SystemExit):
        # Fail soft: a hook error must never block the session (exit 2 from argparse would). Ctrl-C still stops it.
        return 0
    return 0


def hook(argv: list[str] | None = None) -> int:
    # https://docs.x.ai/build/features/hooks — non-empty GROK_HOOK_EVENT is a Grok Build session.
    if os.environ.get("GROK_HOOK_EVENT"):
        return session_start()
    parser = argparse.ArgumentParser(prog="atlas hook")
    sub = parser.add_subparsers(dest="name", required=True)
    sub.add_parser("session-start")
    args = parser.parse_args(argv)
    if args.name == "session-start":
        return session_start()
    return 0


_MARKET = "Heliopause/marketplace"  # relative to HOME; labfs.HOME_TARGETS allows writes below it
_OLD_MARKET = ".atlas/marketplace"  # where 0.6 kept it; only ever removed


def _stamp(data: bytes) -> tuple[bytes, dict]:
    meta = json.loads(data.decode("utf-8"))
    meta["version"] = __version__
    return (json.dumps(meta, indent=2) + "\n").encode("utf-8"), meta


def _write_runtime(lab: Path | None = None) -> None:
    import atlas

    py = os.path.abspath(sys.executable)
    root = str(Path(atlas.__file__).resolve().parent.parent)
    found = lab if lab is not None else _find_lab()
    lab_text = str(found) if found is not None else ""
    if "\n" in py or "\n" in root or "\n" in lab_text:
        print("atlas plugin: runtime path contains a newline", file=sys.stderr)
        return
    body = py + "\n" + root + "\n"
    if lab_text:
        body += lab_text + "\n"
    # 0600 from the start; the hook refuses a file anyone else could write. lab=: nothing lands inside the lab.
    labfs.home_write(f"{_MARKET}/{_PLUGIN_NAME}/hooks/runtime", body.encode("utf-8"), 0o600, lab=found)


def _drop_tree(rel: str = _MARKET, plugin: str = _PLUGIN_NAME) -> None:
    """Remove ~/<rel>. Refused when it, or a folder above it, is a link or not yours."""
    root = Path.home() / rel
    labfs.home(f"{rel}/{plugin}/.claude-plugin/plugin.json")  # checks the folders from HOME down
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(st.st_mode):
        raise labfs.Refused(str(root), "it is not a real folder")
    # rmtree removes links inside without following them (tests/fs_guard_allow.txt).
    shutil.rmtree(root)


def _build() -> Path | None:
    src = _source()
    if not (src / ".claude-plugin" / "plugin.json").is_file():
        print(f"atlas plugin: missing {src}", file=sys.stderr)
        return None
    root = _home_market()
    # The lab is passed to every write: nothing the plugin host runs may land inside it (labfs.home).
    lab = _find_lab()
    try:
        labfs.home(f"{_MARKET}/.claude-plugin/marketplace.json", lab=lab)
        _clear_market(root)
        meta: dict = {}
        # Each file of the package's plugin folder, written through labfs.home_write (no link followed).
        for item in sorted(src.rglob("*")):
            if item.is_symlink() or not item.is_file():
                continue
            rel = item.relative_to(src).as_posix()
            data = item.read_bytes()
            if rel == ".claude-plugin/plugin.json":
                data, meta = _stamp(data)
            labfs.home_write(f"{_MARKET}/{_PLUGIN_NAME}/{rel}", data, stat.S_IMODE(item.stat().st_mode) & 0o755, lab=lab)
        _write_runtime(lab)
        market = {
            "name": _MARKET_NAME,
            "autoUpdate": False,
            "owner": {"name": "Heliopause"},
            "plugins": [
                {
                    "name": _PLUGIN_NAME,
                    "source": "./" + _PLUGIN_NAME,
                    "description": meta.get("description", ""),
                    "version": __version__,
                }
            ],
        }
        labfs.home_write(f"{_MARKET}/.claude-plugin/marketplace.json", (json.dumps(market, indent=2) + "\n").encode("utf-8"), lab=lab)
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        print(f"atlas plugin: {err}", file=sys.stderr)
        return None
    return root


# A host CLI that prompts or hangs must not block forever with nothing on screen.
HOST_TIMEOUT = 120


def _resolve(name: str) -> str | None:
    """The absolute path of the host CLI that would run: the first `name` on PATH."""
    found = shutil.which(name)
    return os.path.abspath(found) if found else None


def _show_host(name: str) -> str | None:
    path = _resolve(name)
    if path is None:
        print(f"{name}: not found on PATH; nothing to run")
    else:
        print(f"{name}: will run {path} (the first {name} on PATH), with a {HOST_TIMEOUT} s timeout")
    return path


def _run(argv: list[str]) -> tuple[int, str]:
    exe = _resolve(argv[0])
    if exe is None:
        print(f"atlas plugin: {argv[0]} not found on PATH", file=sys.stderr)
        return 127, ""
    try:
        proc = subprocess.run(
            [exe] + argv[1:],
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=HOST_TIMEOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        print(f"atlas plugin: {exe} did not finish within {HOST_TIMEOUT} s and was stopped", file=sys.stderr)
        return 1, ""
    except OSError as err:
        print(f"atlas plugin: {exe}: {err}", file=sys.stderr)
        return 1, ""
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode, proc.stdout + proc.stderr


def _is_blocked(text: str) -> bool:
    # ponytail: the host's own word, as in Grok's "blocked by allowlist".
    # A refusal that does not say "blocked" is a normal failure. No second install method.
    return re.search(r"\bblocked\b", text, re.IGNORECASE) is not None


def _rules_only(host: str) -> int:
    lab = _find_lab()
    if lab is None:
        print(f"atlas plugin: {host} blocked plugin install, and no lab folder was found", file=sys.stderr)
        return 1
    path = lab / ".atlas" / "rules" / f"{host}.md"
    try:
        data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
        lead = _lead(data) if isinstance(data, dict) else "Lead"
        body = (
            f"# Heliopause rules for {host}\n\n"
            "Plugin install was blocked. These rules stand in for the plugin.\n\n"
            f"{_context(lab, lead)}\n"
        )
        # labfs: a link at .atlas, .atlas/rules or the rules file (planted in the lab) is refused, never written through.
        labfs.write(lab, f".atlas/rules/{host}.md", body.encode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        print(f"atlas plugin: {host} blocked, and the rules file was not written ({err})", file=sys.stderr)
        return 1
    print(f"atlas plugin: {host} blocked. Rules only: {path}")
    return 0


def _host_cmd(host: str, argv: list[str]) -> tuple[int, bool]:
    code, text = _run(argv)
    return code, code != 0 and _is_blocked(text)


def _hosts_path() -> Path:
    return Path.home() / ".atlas" / "plugin-hosts"


def _installed() -> list[str]:
    path = _hosts_path()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    found = {line.strip() for line in text.splitlines() if line.strip()}
    return [name for name in ("claude", "grok") if name in found]


def _save_hosts(names: list[str]) -> int:
    ordered = [name for name in ("claude", "grok") if name in set(names)]
    try:
        root, rel = labfs.home(".atlas/plugin-hosts")
        if not ordered:
            labfs.unlink(root, rel)
            return 0
        labfs.write(root, rel, "".join(f"{name}\n" for name in ordered).encode("utf-8"))
    except OSError as err:
        print(f"atlas plugin: {err}", file=sys.stderr)
        return 1
    return 0


def _remember(name: str) -> int:
    if name not in ("claude", "grok"):
        return 0
    return _save_hosts(_installed() + [name])


def _forget(name: str) -> int:
    return _save_hosts([item for item in _installed() if item != name])


def _plugin_dir() -> Path:
    return _home_market() / _PLUGIN_NAME


def _cursor_lines() -> list[str]:
    # ponytail: cursor-agent plugin marketplace add takes a git URL, and there is no
    # plugin install. Do not invent a git remote to satisfy it. A session uses --plugin-dir.
    return [
        "cursor: persistent install is not supported. plugin marketplace add takes a git URL, and there is no plugin install.",
        "cursor-agent --plugin-dir ~/Heliopause/marketplace/heliopause",
    ]


def _print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line)


def hosts_present(home: Path) -> list[str]:
    found = []
    if (home / ".claude").is_dir():
        found.append("claude")
    if (home / ".grok").is_dir():
        found.append("grok")
    if (home / ".cursor").is_dir():
        found.append("cursor")
    return found


def _confirm(yes: bool, prompt: str) -> bool:
    """One [y/N]. No terminal, n, Enter, EOF and Ctrl-C are all a refusal; nothing has been written yet."""
    if yes:
        return True
    if not sys.stdin.isatty():
        print("atlas plugin: non-interactive terminal: re-run with --yes", file=sys.stderr)
        print("Nothing changed.")
        return False
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""
    if answer in {"y", "yes"}:
        return True
    print("Nothing changed.")
    return False


_built: dict[str, Path | None] = {}


def _build_once() -> Path | None:
    """Build ~/Heliopause/marketplace on the first yes of this run, never before a prompt is answered."""
    if "root" not in _built:
        _built["root"] = _build()
    return _built["root"]


def _stop(host: str, argv: list[str]) -> int | None:
    """None when the command worked. 0 when the host blocked and rules were written."""
    code, blocked = _host_cmd(host, argv)
    if blocked:
        return _rules_only(host)
    if code != 0:
        return code
    return None


def _install_claude(yes: bool) -> int:
    _print_lines(
        [
            "claude plugin marketplace add ~/Heliopause/marketplace",
            f"claude plugin install {_PLUGIN_ID}",
        ]
    )
    if _show_host("claude") is None:
        return 1
    if not _confirm(yes, "Run these commands? [y/N] "):
        return 1
    if _build_once() is None:
        return 1
    market = str(_home_market())
    stopped = _stop("claude", ["claude", "plugin", "marketplace", "add", market])
    if stopped is not None:
        return stopped
    stopped = _stop("claude", ["claude", "plugin", "install", _PLUGIN_ID])
    if stopped is not None:
        return stopped
    return _remember("claude")


def _install_grok(yes: bool) -> int:
    # ponytail: grok plugin install has no auto-update flag (help checked 2026-10-19).
    # A local path is not a git source, so session-start auto-update does not track it.
    # plugin_auto_update in config.toml is global; do not set it from here.
    _print_lines(
        [
            "grok: no auto-update flag; installing the local path, which is not auto-updated",
            "grok plugin install ~/Heliopause/marketplace/heliopause",
        ]
    )
    if _show_host("grok") is None:
        return 1
    if not _confirm(yes, "Run these commands? [y/N] "):
        return 1
    if _build_once() is None:
        return 1
    stopped = _stop("grok", ["grok", "plugin", "install", str(_plugin_dir())])
    if stopped is not None:
        return stopped
    return _remember("grok")


def _install_cursor(yes: bool) -> int:
    _print_lines(_cursor_lines())
    if not _confirm(yes, "Write the plugin copy to ~/Heliopause/marketplace/heliopause? [y/N] "):
        return 1
    return 0 if _build_once() is not None else 1


def _install_one(name: str, yes: bool) -> int:
    if name == "claude":
        return _install_claude(yes)
    if name == "grok":
        return _install_grok(yes)
    return _install_cursor(yes)


def install(yes: bool = False, hosts: list[str] | None = None) -> int:
    names = list(hosts) if hosts is not None else ["claude"]
    # WHY: the marketplace copy is rebuilt only after a yes; a refusal leaves ~/.atlas as it was.
    _built.clear()
    codes = [_install_one(name, yes) for name in names]
    return 0 if all(code == 0 for code in codes) else 1


def _drop_market() -> int:
    try:
        _clear_market(_home_market())
        _drop_tree(_OLD_MARKET, "atlas")  # a 0.6 install kept its copy at ~/.atlas/marketplace
    except OSError as err:
        print(f"atlas plugin: {err}", file=sys.stderr)
        return 1
    return 0


def _uninstall_claude() -> int:
    codes = [
        _run(["claude", "plugin", "uninstall", _PLUGIN_ID])[0],
        _run(["claude", "plugin", "marketplace", "remove", _MARKET_NAME])[0],
    ]
    if codes != [0, 0]:
        return 1
    return _forget("claude")


def _uninstall_grok() -> int:
    code = _run(["grok", "plugin", "uninstall", _PLUGIN_NAME])[0]
    if code != 0:
        return code
    return _forget("grok")


def _uninstall_one(name: str) -> int:
    if name == "claude":
        return _uninstall_claude()
    if name == "grok":
        return _uninstall_grok()
    print(
        "cursor: nothing was installed; sessions started with --plugin-dir ~/Heliopause/marketplace/heliopause stop loading Heliopause once it is removed."
    )
    return 0


def uninstall(hosts: list[str] | None = None) -> int:
    names = list(hosts) if hosts is not None else ["claude"]
    codes = [_uninstall_one(name) for name in names]
    # A host whose uninstall failed may still load the plugin from the marketplace copy; keep it then.
    # Cursor has no install record and loads --plugin-dir; keep the copy while ~/.cursor exists unless this run names cursor.
    if (
        all(code == 0 for code in codes)
        and not _installed()
        and ("cursor" in names or not (Path.home() / ".cursor").is_dir())
    ):
        codes.append(_drop_market())
    return 0 if all(code == 0 for code in codes) else 1


def _status_one(name: str) -> int:
    if name == "claude":
        return _run(["claude", "plugin", "list"])[0]
    if name == "grok":
        return _run(["grok", "plugin", "list"])[0]
    _print_lines(_cursor_lines())
    return 0


def status(hosts: list[str] | None = None) -> int:
    names = list(hosts) if hosts is not None else ["claude"]
    codes = [_status_one(name) for name in names]
    return 0 if all(code == 0 for code in codes) else 1


def _update_one(name: str) -> int:
    if name == "cursor":
        _print_lines(_cursor_lines())
        return 0
    if name == "claude":
        argv = ["claude", "plugin", "update", _PLUGIN_ID]
        print(f"claude plugin update {_PLUGIN_ID}")
    else:
        argv = ["grok", "plugin", "update", _PLUGIN_NAME]
        print(f"grok plugin update {_PLUGIN_NAME}")
    stopped = _stop(name, argv)
    if stopped is not None:
        return stopped
    return 0


def update(hosts: list[str] | None = None) -> int:
    """Refresh the local copy, then each installed host's plugin update.

    ``hosts`` None means the hosts recorded by a successful install. Cursor has
    no persistent install, so it is not in that list.
    """
    names = _installed() if hosts is None else list(hosts)
    if not names:
        print("atlas plugin: no installed host to update")
        return 0
    if _build() is None:
        return 1
    codes = [_update_one(name) for name in names]
    return 0 if all(code == 0 for code in codes) else 1


def after_switch() -> int:
    """`atlas plugin update` calls this to re-copy this version's plugin files. It downloads nothing."""
    return update()


def _names(host: str | None) -> list[str]:
    if host == "all":
        return list(_HOSTS)
    return [host or "claude"]


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas plugin")
    parser.add_argument("cmd", choices=("install", "uninstall", "status", "update"))
    parser.add_argument("--host", choices=(*_HOSTS, "all"))
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "update":
        if args.host in (None, "all"):
            return after_switch()
        return update([args.host])
    names = _names(args.host)
    if args.cmd == "install":
        return install(yes=args.yes, hosts=names)
    if args.cmd == "uninstall":
        return uninstall(hosts=names)
    return status(hosts=names)

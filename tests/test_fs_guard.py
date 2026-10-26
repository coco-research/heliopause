"""B2/L9 guard: no plain write, delete or link call in atlas/ outside labfs. It fails closed.

Lab agents can plant symlinks and hard links anywhere in the lab, so every write, append, copy, chmod, mkdir,
rename and delete under the lab, and every write to a home-folder file, goes through atlas/labfs.py. This test
parses each module (AST, not grep, so comments and strings never count) and flags every call that could write:
open() without a literal 'r'/'rb' (the default and computed modes count as writes), os.open unless its flags are
read-only names, write_text/bytes, shutil.*, the os write/delete/rename/chmod/link/exec family, the Path
methods of that kind, subprocess with shell=True or a redirected stream, sqlite3, logging file handlers,
json.dump to a file, and archive extraction. Import aliases and getattr(os, "...") are resolved.
The few exceptions are in tests/fs_guard_allow.txt, one line each: file:function:call and the reason.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ALLOW = Path(__file__).with_name("fs_guard_allow.txt")

# Fail closed: any call that can write, delete, rename, chmod or link a file is flagged unless it is inside
# atlas/labfs.py or on the allow-list. Import aliases (import shutil as sh, from os import replace) and
# getattr(os, "...") resolve to the module function. Reads must say so: open() needs a literal 'r' or 'rb'.
_OS = {
    "chmod", "lchmod", "fchmod", "chown", "lchown", "fchown", "chflags", "lchflags", "rename", "renames", "replace",
    "remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs", "link", "symlink", "truncate", "ftruncate",
    "mkfifo", "mknod", "utime", "write", "pwrite", "writev", "sendfile", "copy_file_range", "system", "popen",
    "fdopen", "posix_spawn", "posix_spawnp", "execv", "execve", "execvp", "execvpe", "execl", "execlp", "spawnv",
}
_OS_PREFIX = ("exec", "spawn")
_PATH_METHODS = {"write_text", "write_bytes", "mkdir", "chmod", "lchmod", "touch", "symlink_to", "hardlink_to",
                 "link_to", "unlink", "rmdir"}
_RISKY_MODULES = {"os", "shutil", "tempfile", "io", "codecs", "sqlite3", "zipfile", "tarfile", "subprocess", "logging",
                  "posix", "dbm", "shelve", "pathlib"}
_TEMPFILE = {"mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile", "TemporaryDirectory"}
_LABFS_WRITES = {"write", "create", "append", "open_append", "unlink", "rmdir", "makedirs", "rename", "chmod"}
_READ_FLAGS = {"O_RDONLY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK", "O_DIRECTORY", "O_NOCTTY", "O_NOATIME", "O_BINARY"}
_READ_MODES = {"r", "rb"}
_SUBPROCESS = {"run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"}
_QUIET_STREAMS = {"subprocess.DEVNULL", "subprocess.PIPE", "subprocess.STDOUT", "sys.stdout", "sys.stderr", "None"}


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return f"{_dotted(node.func)}()"
    return "?"


def _arg(node: ast.Call, position: int, name: str) -> ast.AST | None:
    if len(node.args) > position:
        return node.args[position]
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _read_mode(node: ast.Call, position: int) -> bool:
    """True only for an explicit literal read-only mode; the default and computed modes are not reads here."""
    mode = _arg(node, position, "mode")
    return isinstance(mode, ast.Constant) and mode.value in _READ_MODES


class _Finder(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.stack: list[str] = []
        self.found: list[tuple[str, str, str, int]] = []
        self.alias: dict[str, str] = {}  # local name -> dotted module or module.function
        self.consts: dict[str, ast.AST] = {}  # module-level NAME = expr, for os.open flags

    def visit_Module(self, node: ast.Module) -> None:
        for item in node.body:
            if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
                self.consts[item.targets[0].id] = item.value
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for name in node.names:
            if name.asname:
                self.alias[name.asname] = name.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for name in node.names:
            if name.name == "*" and module.split(".")[0] in _RISKY_MODULES:
                self._flag(f"from {module} import *", node)
            self.alias[name.asname or name.name] = f"{module}.{name.name}" if module else name.name
        self.generic_visit(node)

    def _scope(self, node) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def _flag(self, label: str, node: ast.AST) -> None:
        self.found.append((self.rel, ".".join(self.stack) or "<module>", label, node.lineno))

    def _name(self, func: ast.AST) -> str:
        """The dotted name of a call target, with import aliases and getattr(module, "name") resolved."""
        if isinstance(func, ast.Call) and _dotted(func.func) == "getattr" and len(func.args) >= 2:
            base = self._name(func.args[0])
            attr = func.args[1]
            if isinstance(attr, ast.Constant) and isinstance(attr.value, str):
                return f"{base}.{attr.value}"
            # A computed name on a module that can write is flagged; on anything else it is ordinary dispatch.
            return f"{base}.?" if base.split(".")[0] in _RISKY_MODULES else "<dynamic>"
        name = _dotted(func)
        head, _sep, rest = name.partition(".")
        if head in self.alias:
            name = self.alias[head] + (f".{rest}" if rest else "")
        return name

    def _flags_read_only(self, node: ast.AST | None, depth: int = 0) -> bool:
        if node is None or depth > 8:
            return False
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._flags_read_only(node.left, depth + 1) and self._flags_read_only(node.right, depth + 1)
        if isinstance(node, ast.Attribute) and self._name(node.value) == "os":
            return node.attr in _READ_FLAGS
        if isinstance(node, ast.Call) and self._name(node.func) == "getattr" and len(node.args) >= 2:
            attr = node.args[1]
            return self._name(node.args[0]) == "os" and isinstance(attr, ast.Constant) and attr.value in _READ_FLAGS
        if isinstance(node, ast.Constant) and node.value == 0:
            return True
        if isinstance(node, ast.Name) and node.id in self.consts:
            return self._flags_read_only(self.consts[node.id], depth + 1)
        return False

    def _label(self, node: ast.Call) -> str | None:
        func = node.func
        name = self._name(func)
        module, _sep, attr = name.rpartition(".")
        if name.endswith(".?"):
            return name
        if module == "os" and (attr in _OS or attr.startswith(_OS_PREFIX)):
            if attr == "fdopen" and _read_mode(node, 1):
                return None
            return name
        if name == "os.open":
            return None if self._flags_read_only(_arg(node, 1, "flags")) else "os.open(flags)"
        if module == "shutil":
            return name
        if module == "tempfile" and attr in _TEMPFILE:
            return name
        if name in ("open", "io.open", "codecs.open", "builtins.open"):
            return None if _read_mode(node, 1) else f"{name}(mode)"
        if name in ("sqlite3.connect", "logging.FileHandler", "logging.handlers.RotatingFileHandler", "dbm.open", "shelve.open"):
            return name
        if attr in ("extractall", "extract") or name in ("zipfile.ZipFile", "tarfile.open"):
            return name if module in ("zipfile", "tarfile") or attr in ("extractall", "extract") else None
        if name in ("json.dump", "pickle.dump", "marshal.dump"):
            target = _arg(node, 1, "fp")
            return None if target is not None and _dotted(target) in ("sys.stdout", "sys.stderr") else name
        if module == "subprocess" and attr in _SUBPROCESS:
            for kw in node.keywords:
                if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                    return f"{name}(shell)"
                if kw.arg in ("stdout", "stderr", "stdin") and _dotted(kw.value) not in _QUIET_STREAMS:
                    if not (isinstance(kw.value, ast.Constant) and kw.value.value is None):
                        return f"{name}({kw.arg}=handle)"
            return None
        if name.startswith("labfs.") and attr in _LABFS_WRITES and node.args and _dotted(node.args[0]) == "Path.home()":
            return f"{name}(Path.home())"  # home-folder files go through labfs.home(), which checks the allow-list
        if isinstance(func, ast.Attribute) and _dotted(func.value) != "labfs":
            if func.attr == "open":
                return None if _read_mode(node, 0) else ".open(mode)"
            if func.attr in _PATH_METHODS:
                return f".{func.attr}"
            # Path.rename/replace(target): str.replace takes two arguments, datetime.replace takes other keywords.
            if func.attr in ("rename", "replace"):
                one = len(node.args) == 1 and not node.keywords
                keyword = not node.args and any(kw.arg == "target" for kw in node.keywords)
                if (one or keyword) and not isinstance(func.value, ast.Constant):
                    return f".{func.attr}"
        return None

    def visit_Call(self, node: ast.Call) -> None:
        label = self._label(node)
        if label is not None:
            self._flag(label, node)
        self.generic_visit(node)


def _scan(source: str, rel: str = "x.py") -> list[str]:
    finder = _Finder(rel)
    finder.visit(ast.parse(source))
    return [site[2] for site in finder.found]


def _sites() -> list[tuple[str, str, str, int]]:
    found = []
    for path in sorted((REPO / "atlas").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel == "atlas/labfs.py":
            continue
        finder = _Finder(rel)
        finder.visit(ast.parse(path.read_text(encoding="utf-8"), filename=rel))
        found.extend(finder.found)
    return found


def _allowed() -> dict[tuple[str, str, str], str]:
    out = {}
    for raw in ALLOW.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _sep, reason = line.partition(" ")
        rel, func, call = key.split(":")
        out[(rel, func, call)] = reason.strip()
    return out


def test_no_plain_write_outside_labfs():
    allowed = _allowed()
    bad = [f"{rel}:{line} in {func}: {call}" for rel, func, call, line in _sites() if (rel, func, call) not in allowed]
    assert not bad, "write outside labfs (route it through atlas/labfs.py, or document it in tests/fs_guard_allow.txt):\n  " + "\n  ".join(bad)


def test_every_allow_entry_is_used_and_explained():
    used = {(rel, func, call) for rel, func, call, _line in _sites()}
    for key, reason in _allowed().items():
        assert key in used, f"stale entry in fs_guard_allow.txt: {':'.join(key)}"
        assert len(reason) >= 20, f"say why {':'.join(key)} is safe"
    assert len(_allowed()) <= 15, "the allow-list is meant to stay small"


@pytest.mark.parametrize(
    "source, label",
    [
        # writes, including the default and computed open modes
        ("open(p, 'w')", "open(mode)"),
        ("open(p, mode='a')", "open(mode)"),
        ("open(p)", "open(mode)"),
        ("open(p, mode)", "open(mode)"),
        ("open(p, 'r' if x else 'w')", "open(mode)"),
        ("open(p, 'r+')", "open(mode)"),
        ("io.open(p, m)", "io.open(mode)"),
        ("codecs.open(p, 'w', 'utf-8')", "codecs.open(mode)"),
        ("p.open()", ".open(mode)"),
        ("p.open('a', encoding='utf-8')", ".open(mode)"),
        ("p.open(mode=m)", ".open(mode)"),
        ("os.fdopen(fd, 'w')", "os.fdopen"),
        ("os.fdopen(fd)", "os.fdopen"),
        ("p.write_text('x')", ".write_text"),
        ("p.write_bytes(b'x')", ".write_bytes"),
        ("os.write(fd, b'x')", "os.write"),
        ("os.ftruncate(fd, 0)", "os.ftruncate"),
        ("os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)", "os.open(flags)"),
        ("os.open(p, flags)", "os.open(flags)"),
        ("os.open(p, FLAGS)\nFLAGS = os.O_RDWR", "os.open(flags)"),
        ("os.open(p, os.O_RDONLY | extra)", "os.open(flags)"),
        # deletes
        ("os.unlink(p)", "os.unlink"),
        ("os.remove(p)", "os.remove"),
        ("os.rmdir(p)", "os.rmdir"),
        ("os.removedirs(p)", "os.removedirs"),
        ("p.unlink()", ".unlink"),
        ("p.unlink(missing_ok=True)", ".unlink"),
        ("p.rmdir()", ".rmdir"),
        ("shutil.rmtree(p)", "shutil.rmtree"),
        # folders, modes, links, renames
        ("p.mkdir(parents=True)", ".mkdir"),
        ("p.mkdir(0o700, True)", ".mkdir"),
        ("p.touch(0o600, False)", ".touch"),
        ("p.chmod(0o755)", ".chmod"),
        ("p.symlink_to(q)", ".symlink_to"),
        ("p.hardlink_to(q)", ".hardlink_to"),
        ("p.replace(q)", ".replace"),
        ("p.rename(q)", ".rename"),
        ("p.rename(target=q)", ".rename"),
        ("os.chmod(p, 0o600)", "os.chmod"),
        ("os.replace(a, b)", "os.replace"),
        ("os.rename(a, b)", "os.rename"),
        ("os.makedirs(p)", "os.makedirs"),
        ("os.symlink(a, b)", "os.symlink"),
        ("os.link(a, b)", "os.link"),
        ("shutil.copy2(a, b)", "shutil.copy2"),
        ("shutil.copytree(a, b)", "shutil.copytree"),
        ("shutil.unpack_archive(a, b)", "shutil.unpack_archive"),
        ("tempfile.mkstemp(dir=d)", "tempfile.mkstemp"),
        # aliases and dynamic names
        ("from os import replace\nreplace(a, b)", "os.replace"),
        ("from os import unlink as rm\nrm(p)", "os.unlink"),
        ("import shutil as sh\nsh.move(a, b)", "shutil.move"),
        ("from shutil import copyfile\ncopyfile(a, b)", "shutil.copyfile"),
        ("getattr(os, 'replace')(a, b)", "os.replace"),
        ("getattr(os, name)(a, b)", "os.?"),
        ("import os as o\no.remove(p)", "os.remove"),
        # processes, databases, logs, archives
        ("subprocess.run(cmd, shell=True)", "subprocess.run(shell)"),
        ("subprocess.run(cmd, stdout=handle)", "subprocess.run(stdout=handle)"),
        ("subprocess.Popen(cmd, stderr=log)", "subprocess.Popen(stderr=handle)"),
        ("os.system(cmd)", "os.system"),
        ("os.popen(cmd)", "os.popen"),
        ("sqlite3.connect(p)", "sqlite3.connect"),
        ("logging.FileHandler(p)", "logging.FileHandler"),
        ("json.dump(data, handle)", "json.dump"),
        ("zipfile.ZipFile(p).extractall(d)", "zipfile.ZipFile"),
        ("tar.extractall(d)", "tar.extractall"),
        # home-folder files only through labfs.home()
        ("labfs.write(Path.home(), '.x', b'')", "labfs.write(Path.home())"),
        ("labfs.unlink(Path.home(), '.x')", "labfs.unlink(Path.home())"),
    ],
)
def test_the_guard_sees_each_kind_of_write(source, label):
    found = _scan(source)
    assert label in found, found


@pytest.mark.parametrize(
    "source",
    [
        "open(p, 'r')", "open(p, 'rb')", "open(p, mode='rb', encoding=None)", "p.open('r', encoding='utf-8')",
        "os.fdopen(fd, 'rb')", "p.read_text()", "p.read_bytes()", "'a-b'.replace('-', '/')", "now.replace(tzinfo=tz)",
        "os.open(p, os.O_RDONLY)", "os.open(p, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))",
        "_RO = os.O_RDONLY | os.O_CLOEXEC\nos.open(p, _RO)", "labfs.write(lab, 'x', b'')", "labfs.chmod(lab, 'x', 0o600)",
        "labfs.unlink(lab, 'x')", "batch.make_dir(root, 'bin')", "labfs.read(Path.home(), '.x')",
        "subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)", "subprocess.run(cmd, stdin=None)",
        "json.dump(data, sys.stdout)", "getattr(lifecycle, name)(argv)",
    ],
)
def test_the_guard_ignores_reads_and_other_replaces(source):
    assert _scan(source) == []


def test_rmtree_in_the_allow_list_does_not_follow_links():
    # The allow-listed rmtree calls rely on this: links inside are removed, never followed.
    if not shutil.rmtree.avoids_symlink_attacks:
        pytest.skip("this platform's rmtree is not symlink-attack safe")
    assert shutil.rmtree.avoids_symlink_attacks

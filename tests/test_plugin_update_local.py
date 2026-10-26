"""0.7: plugin update stays on this machine, and no update notice ships."""

from __future__ import annotations

import errno
import socket
import subprocess
from pathlib import Path

from atlas import plugin
from test_release_zip import _exclude

ROOT = Path(__file__).resolve().parents[1]
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_NOTICES = (
    "update available",
    "new version",
    "newer version",
    "upgrade available",
)
# "a new release" is left out: SECURITY.md's key-rotation sentence ("the new key ships in a new release") is not a notice.


def _host(address: object) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return "" if address is None else str(address)


def _block_network(monkeypatch) -> list[str]:
    """Record and refuse any connect or lookup that is not loopback. Returns the refused hosts."""
    refused: list[str] = []
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    create_connection = socket.create_connection
    getaddrinfo = socket.getaddrinfo

    def _refuse(host: str) -> bool:
        if not host or host in _LOOPBACK:
            return False
        refused.append(host)
        return True

    def guarded_connect(self, address):
        host = _host(address)
        if _refuse(host):
            raise OSError(errno.EACCES, f"refused {host}")
        return connect(self, address)

    def guarded_connect_ex(self, address):
        host = _host(address)
        if _refuse(host):
            return errno.EACCES
        return connect_ex(self, address)

    def guarded_create_connection(address, *args, **kwargs):
        host = _host(address)
        if _refuse(host):
            raise OSError(errno.EACCES, f"refused {host}")
        return create_connection(address, *args, **kwargs)

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        name = "" if host is None else str(host)
        if _refuse(name):
            raise OSError(errno.EACCES, f"refused {name}")
        return getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    return refused


def _chmod_tree(root: Path) -> None:
    for folder in (root, *root.rglob("*")):
        if folder.is_dir():
            folder.chmod(0o700)


def _argv_calls(log: Path) -> list[list[str]]:
    if not log.is_file():
        return []
    return [block.split("\n") for block in log.read_text(encoding="utf-8").split("\n\n") if block]


def test_plugin_update_makes_no_outbound_connection(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ATLAS_LAB", raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.log"
    for name in ("claude", "grok"):
        script = bin_dir / name
        script.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$@\" >> \"$ATLAS_ARGV_LOG\"\n"
            "printf '\\n' >> \"$ATLAS_ARGV_LOG\"\n"
            "exit 0\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("ATLAS_ARGV_LOG", str(log))
    (home / ".atlas").mkdir()
    (home / ".atlas" / "plugin-hosts").write_text("claude\ngrok\n", encoding="utf-8")
    meta = home / "Heliopause" / "marketplace" / ".claude-plugin"
    meta.mkdir(parents=True)
    (meta / "marketplace.json").write_text('{"name": "heliopause-local"}\n', encoding="utf-8")
    _chmod_tree(home)

    refused = _block_network(monkeypatch)
    codes = [plugin.run(["update"]), plugin.run(["update", "--host", "all"])]
    captured = capsys.readouterr()
    assert codes == [0, 0], captured.out + captured.err
    assert refused == []
    calls = _argv_calls(log)
    assert calls, "update never reached a host CLI"
    for args in calls:
        assert args[:2] == ["plugin", "update"] and len(args) >= 3, args


def _shipped() -> list[Path]:
    """Every file the release zip carries: git ls-files minus .public-exclude, the way release-zip.sh does it."""
    drops, stubs = _exclude((ROOT / ".public-exclude").read_text(encoding="utf-8"))
    gone = [*drops, *stubs]  # a stub ships as "{}", so it holds no text
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8").split("\0")
    return [
        ROOT / rel
        for rel in listed
        if rel and not any(rel == cut or rel.startswith(cut + "/") for cut in gone)
    ]


def test_no_update_notice_ships() -> None:
    hits = []
    for path in _shipped():
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # fonts and images
        for number, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if any(phrase in low for phrase in _NOTICES):
                rel = path.relative_to(ROOT)
                hits.append(f"{rel}:{number}")
                print(f"{rel}:{number}")
    assert hits == []


def test_after_switch_docstring_is_the_approved_text() -> None:
    # Legal's tag-time condition 1, word for word.
    assert plugin.after_switch.__doc__ == (
        "`atlas plugin update` calls this to re-copy this version's plugin files. It downloads nothing."
    )

"""Heliopause hook connect and disconnect. The base is always a lab folder, never the home folder."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from atlas import labfs
from atlas.connect import CURSOR_COMMAND, connect, disconnect, is_connected, plan
from atlas.connectors import _CURSOR

_BAK = re.compile(r"\.heliopause-bak-(\d{8}T\d{6})(\d{6})Z$")


def _dump(obj: dict) -> str:
    return json.dumps(obj, indent=2) + "\n"


def _frozen(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def _backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(path.name + ".heliopause-bak-*"))


def _cursor_cmd() -> str:
    """The command written into hooks.json. A tilde, so the file carries no home path."""
    return CURSOR_COMMAND


def _cursor_body() -> dict:
    cmd = _cursor_cmd()
    return {
        "version": 1,
        "hooks": {event: [{"command": cmd}] for event in _CURSOR},
    }


def test_unknown_tool_raises(tmp_path):
    for fn in (plan, is_connected):
        with pytest.raises(ValueError, match="unsupported tool"):
            fn("claude", tmp_path)
    with pytest.raises(ValueError, match="unsupported tool"):
        connect("claude", tmp_path, True)
    with pytest.raises(ValueError, match="unsupported tool"):
        disconnect("", tmp_path, False)


def test_connect_missing_file(tmp_path):
    # Arrange
    rel = Path(".cursor") / "hooks.json"
    assert not (tmp_path / rel).exists()

    # Act
    preview = plan("cursor", tmp_path)
    assert not (tmp_path / rel).exists()
    assert preview["before"] is None
    assert preview["path"] == str(tmp_path / rel)
    written = connect("cursor", tmp_path, True)
    assert written == preview

    # Assert
    path = tmp_path / rel
    text = path.read_text(encoding="utf-8")
    assert written["path"] == str(path)
    assert written["before"] is None
    assert written["after"] == text
    assert written["diff"].splitlines()[0] == "--- /dev/null"
    assert str(path) in written["diff"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert _backups(path) == []
    assert is_connected("cursor", tmp_path)
    assert json.loads(text) == _cursor_body()
    assert '"type"' not in text


def test_plan_and_yes_false_write_nothing(tmp_path):
    # Arrange
    path = tmp_path / ".cursor" / "hooks.json"
    original = _dump({
        "version": 1,
        "hooks": {"stop": [{"command": "/bin/other"}]},
    })
    _write(path, original, 0o640)
    before_stat = path.stat()

    # Act
    preview = connect("cursor", tmp_path, False)

    # Assert
    assert preview["before"] == original
    assert preview["after"] != original
    assert _cursor_cmd() in preview["after"]
    assert path.read_text(encoding="utf-8") == original
    assert path.stat().st_mtime_ns == before_stat.st_mtime_ns
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert _backups(path) == []
    assert not is_connected("cursor", tmp_path)
    gone = tmp_path / "empty-lab"
    gone.mkdir()
    plan("cursor", gone)
    disconnect("cursor", gone, False)
    assert list(gone.iterdir()) == []


def test_other_hooks_survive_and_disconnect_restores(tmp_path):
    # Arrange: canonical file so a round trip can be byte-identical.
    foreign_stop = {"command": "/opt/other.sh", "timeout": 10}
    foreign_start = {"command": "/bin/existing"}
    original = {
        "version": 1,
        "theme": "dark",
        "hooks": {
            "beforeShellExecution": [foreign_stop],
            "sessionStart": [foreign_start],
        },
    }
    path = tmp_path / ".cursor" / "hooks.json"
    raw = _dump(original)
    _write(path, raw, 0o640)
    stop_bytes = _frozen(foreign_stop)

    # Act
    connect("cursor", tmp_path, True)
    updated = json.loads(path.read_text(encoding="utf-8"))

    # Assert: foreign content is byte-identical, and our events were added once.
    assert _frozen(updated["hooks"]["beforeShellExecution"][0]) == stop_bytes
    assert _frozen(updated["hooks"]["sessionStart"][0]) == _frozen(foreign_start)
    assert updated["hooks"]["sessionStart"][1] == {"command": _cursor_cmd()}
    assert updated["theme"] == "dark"
    assert updated["version"] == 1
    # beforeShellExecution is one of ours now, so the array gains our entry after theirs.
    needle = '{\n        "command": "/opt/other.sh",\n        "timeout": 10\n      }'
    assert needle in raw
    assert needle in path.read_text(encoding="utf-8")
    for event in _CURSOR:
        cmds = [item.get("command") for item in updated["hooks"][event]]
        assert cmds.count(_cursor_cmd()) == 1

    # Act again: disconnect puts the other hooks back exactly.
    disconnect("cursor", tmp_path, True)
    assert path.read_bytes() == raw.encode("utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert not is_connected("cursor", tmp_path)


def test_plan_backup_path_is_the_file_written(tmp_path):
    path = tmp_path / ".cursor" / "hooks.json"
    original = _dump({"version": 1, "hooks": {"stop": [{"command": "/bin/keep"}]}})
    _write(path, original, 0o640)
    preview = plan("cursor", tmp_path)
    backup = Path(preview["backup"])
    assert not backup.exists()
    assert backup.parent == path.parent
    assert backup.name.startswith("hooks.json.heliopause-bak-")
    connect("cursor", tmp_path, True, preview)
    assert backup.read_text(encoding="utf-8") == original
    assert _backups(path) == [backup]


def test_created_hooks_file_round_trip_leaves_no_backup(tmp_path):
    path = tmp_path / ".cursor" / "hooks.json"
    for _ in range(2):
        connect("cursor", tmp_path, True)
        assert path.is_file()
        assert _backups(path) == []
        disconnect("cursor", tmp_path, True)
        assert not path.exists()
        assert list(tmp_path.rglob("*heliopause-bak*")) == []


def test_connect_twice_is_idempotent(tmp_path):
    connect("cursor", tmp_path, True)
    path = tmp_path / ".cursor" / "hooks.json"
    text = path.read_text(encoding="utf-8")
    again = connect("cursor", tmp_path, True)
    assert path.read_text(encoding="utf-8") == text
    assert again["before"] == text
    assert again["after"] == text
    assert again["diff"] == ""
    assert _backups(path) == []
    assert text.count(_cursor_cmd()) == len(_CURSOR)


def test_backup_is_utc_and_preserves_bytes(tmp_path):
    # Arrange
    path = tmp_path / ".cursor" / "hooks.json"
    original = _dump({"version": 1, "hooks": {"stop": [{"command": "/bin/keep"}]}})
    _write(path, original, 0o640)

    # Act
    connect("cursor", tmp_path, True)

    # Assert
    found = _backups(path)
    assert len(found) == 1
    assert found[0].read_text(encoding="utf-8") == original
    assert stat.S_IMODE(found[0].stat().st_mode) == 0o640
    match = _BAK.search(found[0].name)
    assert match
    stamp = datetime.strptime(match.group(1) + match.group(2), "%Y%m%dT%H%M%S%f")
    stamp = stamp.replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - stamp).total_seconds()) < 60
    assert stat.S_IMODE(path.stat().st_mode) == 0o640

    # Disconnect restores the user's bytes and does not write another backup.
    connected = path.read_text(encoding="utf-8")
    disconnect("cursor", tmp_path, False)
    assert path.read_text(encoding="utf-8") == connected
    assert _backups(path) == found
    disconnect("cursor", tmp_path, True)
    assert path.read_bytes() == original.encode("utf-8")
    assert _backups(path) == found


@pytest.mark.journey
def test_invalid_json_is_refused(tmp_path):
    """J5: a connect that hits invalid hooks.json is refused and writes nothing."""
    path = tmp_path / ".cursor" / "hooks.json"
    bad = '{"hooks": [unclosed'
    _write(path, bad, 0o600)
    for call in (
        lambda: plan("cursor", tmp_path),
        lambda: connect("cursor", tmp_path, True),
        lambda: disconnect("cursor", tmp_path, True),
        lambda: is_connected("cursor", tmp_path),
    ):
        with pytest.raises(ValueError, match=re.escape(str(path))):
            call()
        assert path.read_text(encoding="utf-8") == bad
        assert _backups(path) == []

    for text in ("[1, 2]\n", "null\n", '"nope"\n', '{"hooks": []}\n'):
        _write(path, text)
        with pytest.raises(ValueError, match=re.escape(str(path))):
            connect("cursor", tmp_path, True)
        assert path.read_text(encoding="utf-8") == text
        assert _backups(path) == []

    shaped = '{"version": 1, "hooks": {"sessionStart": "nope"}}\n'
    _write(path, shaped)
    with pytest.raises(ValueError, match=re.escape(str(path))):
        connect("cursor", tmp_path, True)
    assert path.read_text(encoding="utf-8") == shaped


def test_diff_does_not_glue_lines_without_trailing_newline(tmp_path):
    raw = '{"version": 1, "hooks": {"stop": [{"command": "/bin/other"}]}}'
    path = tmp_path / ".cursor" / "hooks.json"
    _write(path, raw)

    planned = connect("cursor", tmp_path, False)

    assert planned["before"] == raw
    assert not raw.endswith("\n")
    assert planned["after"].endswith("\n")
    assert path.read_bytes() == raw.encode("utf-8")
    minus = [
        line for line in planned["diff"].splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    assert minus
    assert all("+{" not in line for line in minus)


def test_disconnect_without_our_entries_does_not_reformat(tmp_path):
    raw = '{"version": 1, "hooks": {"stop": [{"command": "/bin/other"}]}}'
    path = tmp_path / ".cursor" / "hooks.json"
    _write(path, raw)
    planned = disconnect("cursor", tmp_path, True)
    assert planned["before"] == raw
    assert planned["after"] == raw
    assert planned["diff"] == ""
    assert path.read_text(encoding="utf-8") == raw
    assert _backups(path) == []


def test_disconnect_missing_file_is_noop(tmp_path):
    planned = disconnect("cursor", tmp_path, True)
    assert planned["before"] is None
    assert planned["after"] == ""
    assert planned["diff"] == ""
    assert not (tmp_path / ".cursor").exists()
    assert is_connected("cursor", tmp_path) is False


def test_mode_kept_on_existing_file(tmp_path):
    path = tmp_path / ".cursor" / "hooks.json"
    _write(
        path,
        _dump({"version": 1, "hooks": {"sessionStart": [{"command": "/bin/x"}]}}),
        0o604,
    )
    connect("cursor", tmp_path, True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o604
    disconnect("cursor", tmp_path, True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o604


def test_home_base_is_refused_and_writes_nothing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    for rel in (
        ".cursor/hooks.json",
        ".cursor/hooks.json.heliopause-bak-20261023T153045123456Z",
        ".cursor/hooks.json.heliopause-bak-20261023T153045123456Z-1",
        ".codex/hooks.json",
        ".codex/hooks.json.heliopause-bak-20261023T153045123456Z",
        ".grok/hooks/heliopause.json",
        ".grok/hooks/heliopause.json.heliopause-bak-20261023T153045123456Z-3",
    ):
        with pytest.raises(labfs.Refused, match="not a home-folder file"):
            labfs.home(rel)
    marker = home / "keep.txt"
    marker.write_text("stay", encoding="utf-8")
    for call in (
        lambda: connect("cursor", home, True),
        lambda: disconnect("cursor", home, True),
    ):
        with pytest.raises(ValueError, match=re.escape(str(home))):
            call()
    assert marker.read_text(encoding="utf-8") == "stay"
    assert [path.name for path in home.iterdir()] == ["keep.txt"]


def test_connect_refuses_a_symlink_above_the_hook_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    lab = tmp_path / "lab"
    lab.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "hooks.json"
    victim.write_text('{"version": 1}\n', encoding="utf-8")
    (lab / ".cursor").symlink_to(outside, target_is_directory=True)

    with pytest.raises(labfs.Refused, match="symlink"):
        connect("cursor", lab, True)

    assert victim.read_text(encoding="utf-8") == '{"version": 1}\n'
    assert [path.name for path in outside.iterdir()] == ["hooks.json"]
    assert list(home.iterdir()) == []


def test_disconnect_refuses_a_symlink_above_the_hook_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    lab = tmp_path / "lab"
    lab.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "hooks.json"
    original = _dump({
        "version": 1,
        "hooks": {"sessionStart": [{"command": _cursor_cmd()}]},
    })
    victim.write_text(original, encoding="utf-8")
    (lab / ".cursor").symlink_to(outside, target_is_directory=True)

    with pytest.raises(labfs.Refused, match="symlink"):
        disconnect("cursor", lab, True)

    assert victim.read_text(encoding="utf-8") == original
    assert [path.name for path in outside.iterdir()] == ["hooks.json"]
    assert list(home.iterdir()) == []

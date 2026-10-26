"""Grok Build sessions only append grok-hooks.log. Everywhere else the hooks are unchanged."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from atlas import memory, hooks, init

ROOT = Path(__file__).resolve().parents[1]
DOC = "https://docs.x.ai/build/features/hooks"
GUARDED = (
    ROOT / "plugin" / "heliopause" / "hooks" / "session-start.sh",
    ROOT / "atlas" / "plugin.py",
    ROOT / "atlas" / "hooks.py",
)
ENTRIES = ("session-start.sh", "session-start", "atlas-hook-start", "atlas-recall-hook")
_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) grok (\S+) ([A-Za-z0-9_]{0,40})$")


def _env(home: Path, lab: Path | None = None, **extra: str) -> dict[str, str]:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        # Closed port so a non-Grok atlas-hook-start brief fails fast and the same way twice.
        "ATLAS_URL": "http://127.0.0.1:1",
    }
    if lab is not None:
        env["ATLAS_LAB"] = str(lab)
    env.update(extra)
    return env


def _generated(home: Path, lab: Path, name: str) -> Path:
    """The hook script atlas hooks wrote under this test's HOME (~/.atlas/hooks), not <lab>/bin."""
    index = 0 if name == "atlas-hook-start" else 1
    return home / hooks.hook_rels(lab)[index]


def _plant_capture(home: Path, lab: Path) -> None:
    """Point the recall hook's capture tool at a shim. Dev builds run ~/.atlas/hooks/m0; a public build does not."""
    marker = lab / "captured"
    body = "#!/bin/sh\nprintf x >> " + shlex.quote(str(marker)) + "\nprintf 'CAPTURED\\n'\ncat\n"
    try:
        from atlas.memory_lite import _TOOLS
    except ImportError:
        capture = lab / "bin" / "m0-capture-hook"
        capture.write_text(body, encoding="utf-8")
        capture.chmod(0o755)
        return
    (lab / "bin" / "m0-decide").write_text("#\n", encoding="utf-8")
    tools = home / ".atlas" / "hooks" / "m0"
    tools.mkdir(mode=0o700)
    tools.chmod(0o700)
    for name in _TOOLS:
        path = tools / name
        if name == "m0-capture-hook":
            path.write_text(body, encoding="utf-8")
            path.chmod(0o700)
        else:
            path.write_text("", encoding="utf-8")
            path.chmod(0o600)


def _install(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ATLAS_LAB", raising=False)
    monkeypatch.delenv("GROK_HOOK_EVENT", raising=False)
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    assert hooks.run(["--lab", str(lab), "--snippet", "--yes"]) == 0
    _plant_capture(home, lab)
    for name in ("atlas-hook-start", "atlas-recall-hook"):
        text = _generated(home, lab, name).read_text(encoding="utf-8")
        assert DOC in text
        # A public build ships the recall hook as a bare exit 0 (no memory lite), with no set -u line.
        if name == "atlas-recall-hook" and "set -u" not in text:
            continue
        assert text.index("GROK_HOOK_EVENT") < text.index("set -u")
    return home, lab


def _trap_shim(home: Path) -> None:
    bindir = home / ".atlas" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "atlas"
    shim.write_text("#!/bin/sh\necho SHIM >&2\nexit 1\n", encoding="utf-8")
    shim.chmod(0o755)


def _files(root: Path) -> dict[str, bytes]:
    out = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == "logs/grok-hooks.log":
            continue
        out[rel] = path.read_bytes()
    return out


def _run(entry: str, env: dict[str, str], lab: Path, stdin: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    if entry == "session-start.sh":
        cmd = ["sh", str(ROOT / "plugin" / "heliopause" / "hooks" / "session-start.sh")]
    elif entry == "session-start":
        cmd = [sys.executable, "-B", "-m", "atlas", "hook", "session-start"]
        cwd = ROOT
    elif entry == "atlas-hook-start":
        cmd = [str(_generated(Path(env["HOME"]), lab, entry))]
    else:
        cmd = [str(_generated(Path(env["HOME"]), lab, "atlas-recall-hook"))]
    return subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or lab),
        timeout=20,
    )


def _assert_quiet(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr == ""


def _assert_new_line(lab: Path, name: str, before: str) -> None:
    text = (lab / "logs" / "grok-hooks.log").read_text(encoding="utf-8")
    assert text.startswith(before)
    added = text[len(before):]
    lines = added.splitlines()
    assert added.endswith("\n") and len(lines) == 1, added
    match = _LINE.fullmatch(lines[0])
    assert match, lines[0]
    assert match.group(2) == name
    assert match.group(3) == "SessionStart"
    seen = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - seen).total_seconds()) < 120


@pytest.fixture
def lab_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    return _install(tmp_path, monkeypatch)


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("lookup", ("atlas_lab", "pointer"))
def test_grok_session_only_logs(lab_env: tuple[Path, Path], entry: str, lookup: str) -> None:
    home, lab = lab_env
    _trap_shim(home)
    log = lab / "logs" / "grok-hooks.log"
    log.parent.mkdir(parents=True)
    log.write_text("seed\n", encoding="utf-8")
    before = _files(lab)
    env = _env(home, lab if lookup == "atlas_lab" else None, GROK_HOOK_EVENT="SessionStart")
    if lookup == "pointer":
        (home / ".atlas").mkdir(parents=True, exist_ok=True)
        (home / ".atlas" / "lab").write_text(str(lab) + "\n", encoding="utf-8")
    proc = _run(entry, env, lab, stdin=json.dumps({"cwd": str(lab)}) + "\n")
    _assert_quiet(proc)
    _assert_new_line(lab, entry, "seed\n")
    assert _files(lab) == before
    assert not (lab / "captured").exists()


@pytest.mark.parametrize("entry", ENTRIES)
def test_without_grok_output_matches_a_clean_run(lab_env: tuple[Path, Path], entry: str, tmp_path: Path) -> None:
    home, lab = lab_env
    payload = json.dumps({"cwd": str(lab)}) + "\n"
    extra: dict[str, str] = {}
    recorded = stdin_copy = None
    if entry == "session-start.sh":
        recorded = tmp_path / "shim-log"
        stdin_copy = tmp_path / "shim-stdin"
        bindir = home / ".atlas" / "bin"
        bindir.mkdir(parents=True)
        shim = bindir / "atlas"
        shim.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SHIM_LOG\"\ncat >> \"$SHIM_STDIN\"\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)
        extra = {"SHIM_LOG": str(recorded), "SHIM_STDIN": str(stdin_copy)}
    env = _env(home, lab, **extra)
    work = tmp_path / "workdir"
    work.mkdir()
    cwd = work if entry == "atlas-hook-start" else None
    first = _run(entry, env, lab, payload, cwd)
    second = _run(entry, env, lab, payload, cwd)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert second.stdout == first.stdout
    assert second.stderr == first.stderr
    blank = dict(env)
    blank["GROK_HOOK_EVENT"] = ""
    third = _run(entry, blank, lab, payload, cwd)
    assert third.returncode == 0, third.stderr
    assert third.stdout == first.stdout
    assert third.stderr == first.stderr
    if entry == "session-start.sh":
        assert recorded is not None and stdin_copy is not None
        assert recorded.read_text(encoding="utf-8").splitlines() == ["hook session-start"] * 3
        assert stdin_copy.read_text(encoding="utf-8") == payload * 3
        assert first.stdout == "" and first.stderr == ""
    elif entry == "session-start":
        assert "additionalContext" in first.stdout
    elif entry == "atlas-hook-start":
        assert "appended to" in first.stdout
        assert "Session started" in (lab / "board.md").read_text(encoding="utf-8")
    elif entry == "atlas-recall-hook" and memory._lite is None:
        # A public build leaves memory lite out: the recall hook is a bare exit 0.
        assert first.stdout == "" and not (lab / "captured").exists()
    else:
        assert first.stdout == "CAPTURED\n" + payload
        assert (lab / "captured").is_file()


@pytest.mark.parametrize("entry", ENTRIES)
def test_grok_session_does_not_touch_secrets(lab_env: tuple[Path, Path], entry: str) -> None:
    home, lab = lab_env
    _trap_shim(home)
    auth = home / ".grok" / "auth.json"
    auth.parent.mkdir()
    auth.write_text('{"token":"nope"}\n', encoding="utf-8")
    os.chmod(auth, 0)
    try:
        with pytest.raises(OSError):
            auth.read_bytes()
        env = _env(home, lab, GROK_HOOK_EVENT="SessionStart", XAI_API_KEY="sentinel")
        proc = _run(entry, env, lab, stdin=json.dumps({"cwd": str(lab)}) + "\n")
        _assert_quiet(proc)
        log = (lab / "logs" / "grok-hooks.log").read_text(encoding="utf-8")
        assert "sentinel" not in log
        assert "nope" not in log
    finally:
        os.chmod(auth, 0o644)


@pytest.mark.parametrize("entry", ENTRIES)
def test_grok_without_a_known_lab_logs_nothing(lab_env: tuple[Path, Path], entry: str) -> None:
    home, lab = lab_env
    _trap_shim(home)
    before = _files(lab)
    proc = _run(entry, _env(home, None, GROK_HOOK_EVENT="SessionStart"), lab, stdin="hello\n")
    _assert_quiet(proc)
    assert _files(lab) == before
    assert not (lab / "logs" / "grok-hooks.log").exists()
    assert not (lab / "captured").exists()
    assert list(home.rglob("grok-hooks.log")) == []


def test_sources_name_the_doc_and_not_grok_secrets() -> None:
    shell = GUARDED[0].read_text(encoding="utf-8")
    assert shell.index("GROK_HOOK_EVENT") < shell.index('shim="$HOME/.atlas/bin/atlas"')
    for path in GUARDED:
        text = path.read_text(encoding="utf-8")
        assert DOC in text
        assert "auth.json" not in text
        assert "XAI_API_KEY" not in text

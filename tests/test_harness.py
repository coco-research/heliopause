"""Drivers talk to fake claude and cursor-agent binaries, never the real CLIs."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import harness
from harness.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "tests" / "fixtures" / "harness" / "bin"
ATLAS = ROOT / "atlas"
PROMPT = "do-not-put-this-on-argv\nsecond line"
_KEPT_ENV = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "SHELL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
}


class _BytesIn:
    def __init__(self, text):
        self.buffer = io.BytesIO(text.encode("utf-8"))


@pytest.fixture(autouse=True)
def _parent_path_has_fakes(monkeypatch):
    # argv[0] is resolved from the parent PATH. Fakes must win over real CLIs.
    monkeypatch.setenv("PATH", str(BIN) + os.pathsep + os.environ.get("PATH", ""))
    for name in ("claude", "cursor-agent"):
        assert shutil.which(name) == str(BIN / name)


def _env(mode: str) -> dict:
    env = os.environ.copy()
    env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
    env["HARNESS_FAKE"] = mode
    return env


def _argv_seen(cwd: Path) -> list:
    return json.loads((cwd / "argv.json").read_text(encoding="utf-8"))


def _env_seen(cwd: Path) -> dict:
    seen = json.loads((cwd / "env.json").read_text(encoding="utf-8"))
    # CPython on macOS inserts this after startup. The driver never copies it.
    seen.pop("__CF_USER_TEXT_ENCODING", None)
    return seen


def _wrap(directory: Path, name: str, mode: str) -> None:
    """Point a CLI name at fake-agent with the mode set inside the child."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        "#!/bin/sh\nexport HARNESS_FAKE=%s\nexport FAKE_ARGV0=\"$0\"\nexec \"%s\" \"$@\"\n"
        % (mode, BIN / "fake-agent"),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _recorder(directory: Path, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "sys.stdin.buffer.read()\n"
        "work = Path.cwd()\n"
        "(work / 'argv.json').write_text(json.dumps(sys.argv), encoding='utf-8')\n"
        "(work / 'env.json').write_text(json.dumps(dict(os.environ), sort_keys=True), encoding='utf-8')\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _dead(pid: int) -> bool:
    for _ in range(25):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_version():
    assert harness.__version__ == "0.1.0"


@pytest.mark.parametrize(
    "name,binary,tail",
    [
        (
            "claude",
            "claude",
            [
                "-p",
                "--verbose",
                "--output-format",
                "stream-json",
                "--include-partial-messages",
                "--safe-mode",
                "--setting-sources",
                "user",
                "--strict-mcp-config",
                "--permission-mode",
                "dontAsk",
                "--permission-prompts",
                "none",
                "--model=sonnet",
            ],
        ),
        (
            "cursor",
            "cursor-agent",
            [
                "-p",
                "--output-format",
                "stream-json",
                "--stream-partial-output",
                "--mode",
                "ask",
                "--sandbox",
                "enabled",
            ],
        ),
    ],
)
def test_prompt_is_stdin_not_argv(name, binary, tail, tmp_path):
    model = "sonnet" if name == "claude" else None
    result = harness.run(name, PROMPT, str(tmp_path), 10, model=model, env=_env("ok"))
    assert result.exit_code == 0 and result.timed_out is False
    assert result.argv[0] == str(BIN / binary)
    assert os.path.isabs(result.argv[0])
    assert result.argv[1:] == tail
    assert PROMPT not in result.argv
    assert all(PROMPT not in arg for arg in result.argv)
    # The shell wrapper records $0, the absolute path passed as argv[0].
    seen = _argv_seen(tmp_path)
    assert seen[0] == str(BIN / binary)
    assert seen[1:] == tail
    assert all(PROMPT not in arg for arg in seen)
    assert (tmp_path / "stdin").read_bytes() == PROMPT.encode("utf-8")


def test_streaming_deltas_arrive_in_order_before_exit(tmp_path):
    deltas = []
    stamps = []

    def on_delta(text):
        deltas.append(text)
        stamps.append(time.time())

    result = harness.run("claude", PROMPT, str(tmp_path), 10, on_delta=on_delta, env=_env("stream"))
    exit_at = float((tmp_path / "exit_at").read_text(encoding="utf-8"))
    assert deltas == ["alpha\n", "beta\n"]
    assert result.text == "alpha\nbeta\n"
    assert stamps and max(stamps) < exit_at
    assert PROMPT not in result.argv


def test_nonzero_exit_is_passed_through(tmp_path):
    result = harness.run("cursor", PROMPT, str(tmp_path), 10, env=_env("fail"))
    assert result.exit_code == 7 and result.timed_out is False
    assert result.text == "nope\n"
    assert result.argv[0] == str(BIN / "cursor-agent")
    assert PROMPT.encode("utf-8") == (tmp_path / "stdin").read_bytes()


def test_leftover_child_holding_stdout_does_not_hang(tmp_path):
    started = time.monotonic()
    result = harness.run("claude", PROMPT, str(tmp_path), 60, env=_env("linger"))
    assert time.monotonic() - started < 10  # the grandchild sleeps 120 s with our stdout open
    assert result.exit_code == 0 and result.timed_out is False and result.text == "done\n"


def test_timeout_kills_process_group(tmp_path, monkeypatch):
    wrap = tmp_path / "wrap"
    _wrap(wrap, "claude", "hang")
    monkeypatch.setattr(sys, "stdin", _BytesIn(PROMPT))
    # The CLI does not forward HARNESS_FAKE. The wrapper sets the mode after exec.
    monkeypatch.setenv("PATH", str(wrap) + os.pathsep + os.environ.get("PATH", ""))
    started = time.monotonic()
    # 3 s, not 1: on a busy runner the fake's Python start-up can outlast 1 s, so SIGTERM lands
    # before it ignores SIGTERM and the run ends at once, which looks like a missing grace period.
    code = main(["run", "--name", "claude", "--cwd", str(tmp_path), "--timeout", "3"])
    # Grace is 5s after the timeout. An immediate SIGKILL would finish in about 3 s.
    assert time.monotonic() - started >= 3 + 5
    child = int((tmp_path / "child.pid").read_text(encoding="utf-8"))
    grandchild = int((tmp_path / "grandchild.pid").read_text(encoding="utf-8"))
    assert code == 124
    assert child != grandchild
    assert _dead(child) and _dead(grandchild)
    assert PROMPT.encode("utf-8") == (tmp_path / "stdin").read_bytes()
    assert PROMPT not in _argv_seen(tmp_path)


def test_module_prints_stream_and_child_status(tmp_path):
    wrap = tmp_path / "wrap"
    _wrap(wrap, "cursor-agent", "stream")
    _wrap(wrap, "claude", "fail")
    env = os.environ.copy()
    env.pop("HARNESS_FAKE", None)
    env["PATH"] = str(wrap) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness",
            "run",
            "--name",
            "cursor",
            "--cwd",
            str(tmp_path),
            "--timeout",
            "10",
            "--model",
            "gpt-5",
        ],
        input=PROMPT,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=20,
    )
    assert proc.returncode == 0
    assert proc.stdout == "alpha\nbeta\n"
    assert _argv_seen(tmp_path)[-1] == "--model=gpt-5"
    assert PROMPT not in proc.stdout

    failed = subprocess.run(
        [sys.executable, "-m", "harness", "run", "--name", "claude", "--cwd", str(tmp_path), "--timeout", "10"],
        input=PROMPT,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=20,
    )
    assert failed.returncode == 7
    assert failed.stdout == "nope\n"


def test_unknown_name_fails_closed(tmp_path):
    with pytest.raises(ValueError):
        harness.run("nope", PROMPT, str(tmp_path), 5)


def test_atlas_does_not_import_harness():
    hits = []
    for path in ATLAS.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("import harness") or stripped.startswith("from harness"):
                hits.append("%s:%s" % (path, lineno))
    assert hits == []


def test_lone_surrogate_prompt_finishes_before_timeout(tmp_path):
    prompt = "\ud800"
    result = harness.run("claude", prompt, str(tmp_path), 10, env=_env("ok"))
    assert result.exit_code == 0 and result.timed_out is False
    assert (tmp_path / "stdin").read_bytes() == prompt.encode("utf-8", "replace")


def test_second_thread_start_failure_stops_the_child(tmp_path, monkeypatch):
    real_start = harness.threading.Thread.start

    def start(self):
        # Fail only the harness's stdin writer, so no other thread in the process can trip this.
        if getattr(self, "_target", None) is not None and self._target.__name__ == "write_stdin":
            raise RuntimeError("start failed")
        real_start(self)

    monkeypatch.setattr(harness.threading.Thread, "start", start)
    spawned = []
    real_popen = harness.subprocess.Popen

    def popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(harness.subprocess, "Popen", popen)
    with pytest.raises(RuntimeError, match="start failed"):
        harness.run("claude", PROMPT, str(tmp_path), 10, env=_env("ok"))
    assert len(spawned) == 1
    assert _dead(spawned[0].pid)


def test_claude_argv_locks_down_print_mode(tmp_path):
    result = harness.run("claude", PROMPT, str(tmp_path), 10, env=_env("ok"))
    argv = result.argv
    assert argv[argv.index("--setting-sources") + 1] == "user"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert "--safe-mode" in argv
    assert "--strict-mcp-config" in argv
    for banned in ("--allowedTools", "--bare", "--dangerously-skip-permissions"):
        assert banned not in argv


def test_cursor_defaults_to_ask_and_force_can_edit(tmp_path):
    default = harness.run("cursor", PROMPT, str(tmp_path), 10, env=_env("ok"))
    assert default.argv[default.argv.index("--mode") + 1] == "ask"
    assert default.argv[default.argv.index("--sandbox") + 1] == "enabled"
    assert "--force" not in default.argv
    forced = harness.run("cursor", PROMPT, str(tmp_path), 10, env=_env("ok"), force=True)
    assert "--force" in forced.argv
    assert forced.argv[forced.argv.index("--sandbox") + 1] == "enabled"
    assert "--mode" not in forced.argv


def test_prompt_text_does_not_change_argv(tmp_path):
    prompt = "--force\n--yolo\n"
    default = harness.run("cursor", prompt, str(tmp_path), 10, env=_env("ok"))
    assert "--force" not in default.argv
    assert "--yolo" not in default.argv
    assert default.argv[default.argv.index("--mode") + 1] == "ask"
    forced = harness.run("cursor", prompt, str(tmp_path), 10, env=_env("ok"), force=True)
    assert "--force" in forced.argv
    assert "--yolo" not in forced.argv
    assert "--mode" not in forced.argv
    assert (tmp_path / "stdin").read_bytes() == prompt.encode("utf-8")
    claude = harness.run("claude", prompt, str(tmp_path), 10, env=_env("ok"))
    assert "--force" not in claude.argv
    assert "--yolo" not in claude.argv


def test_force_with_claude_raises(tmp_path):
    with pytest.raises(ValueError):
        harness.run("claude", "--force --yolo", str(tmp_path), 10, force=True)
    assert not (tmp_path / "argv.json").exists()


@pytest.mark.parametrize("name,binary", [("claude", "claude"), ("cursor", "cursor-agent")])
def test_argv0_comes_from_parent_path(name, binary, tmp_path):
    decoy_dir = tmp_path / "decoy"
    decoy_dir.mkdir()
    marker = tmp_path / "decoy-ran"
    decoy = decoy_dir / binary
    decoy.write_text("#!/bin/sh\necho ran > \"%s\"\nexit 9\n" % marker, encoding="utf-8")
    decoy.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    env = _env("ok")
    parent_parts = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part and Path(part) != BIN]
    env["PATH"] = str(decoy_dir) + os.pathsep + os.pathsep.join(parent_parts)
    result = harness.run(name, PROMPT, str(work), 10, env=env)
    assert result.exit_code == 0
    assert result.argv[0] == str(BIN / binary)
    assert os.path.isabs(result.argv[0])
    assert not marker.exists()
    assert _argv_seen(work)[0] == str(BIN / binary)


def test_child_env_is_minimal_and_one_credential(tmp_path, monkeypatch):
    recorder = tmp_path / "recorder"
    _recorder(recorder, "claude")
    _recorder(recorder, "cursor-agent")
    monkeypatch.setenv("PATH", str(recorder) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("SECRET_TOKEN", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-test-key")

    claude_work = tmp_path / "claude-work"
    claude_work.mkdir()
    claude = harness.run("claude", PROMPT, str(claude_work), 10)
    claude_env = _env_seen(claude_work)
    assert claude.exit_code == 0
    assert os.path.isabs(claude.argv[0])
    assert claude.argv[0] == str(recorder / "claude")
    assert "SECRET_TOKEN" not in claude_env
    assert claude_env.get("ANTHROPIC_API_KEY") == "anthropic-test-key"
    assert "CURSOR_API_KEY" not in claude_env
    assert set(claude_env) <= _KEPT_ENV | {"ANTHROPIC_API_KEY"}

    cursor_work = tmp_path / "cursor-work"
    cursor_work.mkdir()
    cursor = harness.run("cursor", PROMPT, str(cursor_work), 10)
    cursor_env = _env_seen(cursor_work)
    assert cursor.exit_code == 0
    assert "SECRET_TOKEN" not in cursor_env
    assert cursor_env.get("CURSOR_API_KEY") == "cursor-test-key"
    assert "ANTHROPIC_API_KEY" not in cursor_env
    assert set(cursor_env) <= _KEPT_ENV | {"CURSOR_API_KEY"}

    over = tmp_path / "over"
    over.mkdir()
    harness.run("claude", PROMPT, str(over), 10, env={"REVIEW_MARK": "1"})
    over_env = _env_seen(over)
    assert over_env.get("REVIEW_MARK") == "1"
    assert "SECRET_TOKEN" not in over_env
    assert "CURSOR_API_KEY" not in over_env

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    bare = tmp_path / "bare"
    bare.mkdir()
    harness.run("claude", PROMPT, str(bare), 10)
    bare_env = _env_seen(bare)
    assert "ANTHROPIC_API_KEY" not in bare_env
    assert "CURSOR_API_KEY" not in bare_env
    assert "SECRET_TOKEN" not in bare_env


def test_cli_force_is_only_valid_for_cursor(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _BytesIn(PROMPT))
    code = main(["run", "--name", "cursor", "--cwd", str(tmp_path), "--timeout", "10", "--force"])
    assert code == 0
    seen = _argv_seen(tmp_path)
    assert seen[seen.index("--sandbox") + 1] == "enabled"
    assert "--force" in seen
    assert "--mode" not in seen
    blocked = main(["run", "--name", "claude", "--cwd", str(tmp_path), "--timeout", "10", "--force"])
    assert blocked == 2
    captured = capsys.readouterr()
    assert "--force is only valid with --name cursor" in captured.err


def test_missing_binary_is_harness_missing(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    result = harness.run("claude", PROMPT, str(tmp_path), 5)
    assert result.outcome == "harness_missing"
    assert result.exit_code == 127 and result.text == "" and result.timed_out is False
    assert result.seconds == 0.0 and result.argv == ["claude"]


def test_missing_binary_never_falls_back_to_caller_path(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    fixture_bin = Path(__file__).parent / "fixtures" / "harness" / "bin"
    result = harness.run("claude", PROMPT, str(tmp_path), 5, env={"PATH": str(fixture_bin)})
    assert result.outcome == "harness_missing" and result.exit_code == 127
    assert result.argv == ["claude"]
    assert not (tmp_path / "argv.json").exists() and not (tmp_path / "stdin").exists()


def test_fixture_agents_are_regular_files():
    # The release zip refuses symlinks, so the stand-in agents must be real files.
    bin_dir = Path(__file__).parent / "fixtures" / "harness" / "bin"
    for name in ("claude", "cursor-agent", "fake-agent"):
        assert (bin_dir / name).is_file() and not (bin_dir / name).is_symlink()


def test_relative_path_entry_is_not_executed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = Path("relbin")
    rel.mkdir()
    marker = tmp_path / "ran"
    binary = rel / "claude"
    binary.write_text("#!/bin/sh\nprintf x > \"%s\"\n" % marker, encoding="utf-8")
    binary.chmod(0o755)
    # An empty entry plus a relative directory. which() can see the fake; it must not run.
    monkeypatch.setenv("PATH", "" + os.pathsep + "relbin")
    result = harness.run("claude", PROMPT, str(tmp_path), 5)
    assert result.outcome == "harness_missing" and result.exit_code == 127
    assert result.argv == ["claude"]
    assert not marker.exists()


def test_proxy_and_ca_env_pass_through(tmp_path, monkeypatch):
    recorder = tmp_path / "recorder"
    _recorder(recorder, "claude")
    monkeypatch.setenv("PATH", str(recorder) + os.pathsep + os.environ.get("PATH", ""))
    expected = {
        "HTTP_PROXY": "http://proxy.example",
        "HTTPS_PROXY": "http://proxy.example",
        "NO_PROXY": "localhost",
        "ALL_PROXY": "http://proxy.example",
        "http_proxy": "http://proxy.example",
        "https_proxy": "http://proxy.example",
        "no_proxy": "localhost",
        "all_proxy": "http://proxy.example",
        "SSL_CERT_FILE": "/tmp/ca.pem",
        "SSL_CERT_DIR": "/tmp/certs",
        "REQUESTS_CA_BUNDLE": "/tmp/ca.pem",
        "NODE_EXTRA_CA_CERTS": "/tmp/ca.pem",
    }
    for key, value in expected.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HARNESS_SENTINEL", "nope")
    work = tmp_path / "work"
    work.mkdir()
    result = harness.run("claude", PROMPT, str(work), 10)
    seen = _env_seen(work)
    assert result.exit_code == 0
    for key, value in expected.items():
        assert seen.get(key) == value
    assert "HARNESS_SENTINEL" not in seen


def test_writer_start_failure_joins_reader_and_closes_pipes(tmp_path, monkeypatch):
    real_start = harness.threading.Thread.start
    readers = []

    def start(self):
        target = getattr(self, "_target", None)
        name = getattr(target, "__name__", "")
        if name == "write_stdin":
            raise KeyboardInterrupt("stop")
        real_start(self)
        if name == "read_stdout":
            readers.append(self)

    monkeypatch.setattr(harness.threading.Thread, "start", start)
    spawned = []
    real_popen = harness.subprocess.Popen

    def popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(harness.subprocess, "Popen", popen)
    with pytest.raises(KeyboardInterrupt):
        harness.run("claude", PROMPT, str(tmp_path), 10, env=_env("ok"))
    assert len(spawned) == 1
    assert len(readers) == 1
    assert not readers[0].is_alive()
    assert spawned[0].stdin.closed
    assert spawned[0].stdout.closed
    assert _dead(spawned[0].pid)


@pytest.mark.parametrize("newline", [True, False])
def test_output_over_cap_is_truncated(tmp_path, monkeypatch, newline):
    monkeypatch.setattr(harness, "_MAX_OUTPUT_BYTES", 32)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "sys.stdin.buffer.read()\n"
        "Path('pid').write_text(str(os.getpid()))\n"
        f"line = b'y' * 64{' + bytes([10])' if newline else ''}\n"
        "for _ in range(40):\n"
        "    sys.stdout.buffer.write(line)\n"
        "    sys.stdout.flush()\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    work = tmp_path / "work"
    work.mkdir()
    result = harness.run("claude", PROMPT, str(work), 30)
    pid_path = work / "pid"
    try:
        assert result.truncated is True
        assert result.timed_out is False
        # A bounded read stops at the cap + 1 byte, with or without newlines in the stream.
        assert result.text and set(result.text) == {"y"} and len(result.text) <= 33
        assert pid_path.exists()
        assert _dead(int(pid_path.read_text(encoding="utf-8")))
    finally:
        if pid_path.exists():
            try:
                os.kill(int(pid_path.read_text(encoding="utf-8")), 9)
            except OSError:
                pass


def test_prompt_over_cap_is_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(harness, "_MAX_PROMPT_BYTES", 8)
    ok_dir = tmp_path / "ok"
    ok_dir.mkdir()
    ok = harness.run("claude", "12345678", str(ok_dir), 10, env=_env("ok"))
    assert ok.exit_code == 0 and ok.timed_out is False
    with pytest.raises(ValueError):
        harness.run("claude", "123456789", str(ok_dir), 10)

    class _Counted:
        def __init__(self, data):
            self.data = data
            self.n = None

        def read(self, n=-1):
            self.n = n
            if n is None or n < 0:
                return self.data
            return self.data[:n]

    class _Stdin:
        def __init__(self, data):
            self.buffer = _Counted(data)

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    stdin = _Stdin(b"123456789")
    monkeypatch.setattr(sys, "stdin", stdin)
    code = main(["run", "--name", "claude", "--cwd", str(blocked), "--timeout", "10"])
    captured = capsys.readouterr()
    assert code == 2
    assert "prompt exceeds" in captured.err
    assert stdin.buffer.n == harness._MAX_PROMPT_BYTES + 1
    assert not (blocked / "argv.json").exists()


def test_setsid_grandchild_does_not_block_return(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "sys.stdin.buffer.read()\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    Path('grandchild.pid').write_text(str(os.getpid()))\n"
        "    time.sleep(120)\n"
        "    os._exit(0)\n"
        "sys.stdout.write('done\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    work = tmp_path / "work"
    work.mkdir()
    started = time.monotonic()
    try:
        result = harness.run("claude", PROMPT, str(work), 30)
        elapsed = time.monotonic() - started
        pid_path = work / "grandchild.pid"
        assert pid_path.exists()
        pid = int(pid_path.read_text(encoding="utf-8"))
        os.kill(pid, 0)  # still alive: run did not wait out the 120s sleep
        assert elapsed < 15
        assert result.timed_out is False
        assert result.text == "done\n"
        assert result.truncated is False
    finally:
        pid_path = work / "grandchild.pid"
        if pid_path.exists():
            try:
                os.kill(int(pid_path.read_text(encoding="utf-8")), 9)
            except OSError:
                pass


def test_setsid_grandchild_holding_stdin_does_not_block_return(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    os.close(1)\n"
        "    Path('grandchild.pid').write_text(str(os.getpid()))\n"
        "    time.sleep(120)\n"
        "    os._exit(0)\n"
        "deadline = time.time() + 5\n"
        "while not Path('grandchild.pid').exists() and time.time() < deadline:\n"
        "    time.sleep(0.05)\n"
        "sys.stdout.write('done\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    work = tmp_path / "work"
    work.mkdir()
    prompt = "p" * (512 * 1024)
    started = time.monotonic()
    try:
        result = harness.run("claude", prompt, str(work), 30)
        elapsed = time.monotonic() - started
        assert elapsed < 15
        assert result.timed_out is False
        assert result.text == "done\n"
    finally:
        pid_path = work / "grandchild.pid"
        if pid_path.exists():
            try:
                os.kill(int(pid_path.read_text(encoding="utf-8")), 9)
            except OSError:
                pass


def test_model_flag_is_rejected_and_valid_model_is_one_token(tmp_path):
    with pytest.raises(ValueError):
        harness.run("claude", PROMPT, str(tmp_path), 5, model="--dangerously-skip-permissions")
    with pytest.raises(ValueError):
        harness.run("claude", PROMPT, str(tmp_path), 5, model="sonnet;id")
    assert not (tmp_path / "argv.json").exists()
    result = harness.run("claude", PROMPT, str(tmp_path), 10, model="gpt-5", env=_env("ok"))
    assert result.argv[-1] == "--model=gpt-5"
    assert "--model" not in result.argv
    assert "--dangerously-skip-permissions" not in result.argv


@pytest.mark.parametrize("value", ["inf", "nan", "21601"])
def test_timeout_must_be_finite_and_at_most_six_hours(value, tmp_path, monkeypatch, capsys):
    number = float(value)
    with pytest.raises(ValueError):
        harness.run("claude", PROMPT, str(tmp_path), number)
    monkeypatch.setattr(sys, "stdin", _BytesIn(PROMPT))
    code = main(["run", "--name", "claude", "--cwd", str(tmp_path), "--timeout", value])
    captured = capsys.readouterr()
    assert code == 2
    assert "21600" in captured.err
    allowed = harness.run("claude", PROMPT, str(tmp_path), harness._MAX_TIMEOUT_S, env=_env("ok"))
    assert allowed.exit_code == 0 and allowed.timed_out is False

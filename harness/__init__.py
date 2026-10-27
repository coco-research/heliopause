"""Headless drivers for coding-agent CLIs. Version 0.1: Claude Code and Cursor."""

from __future__ import annotations

import fcntl
import math
import os
import re
import select
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional

__version__ = "0.1.0"

# ponytail: five seconds is the whole grace period; a grandchild that calls setsid
# leaves this process group and survives SIGKILL. Upgrade path: a pid tree or a cgroup.
_KILL_GRACE_S = 5
_DRAIN_S = 2  # after the CLI exits, how long its leftover children may hold stdout
_POLL_S = 0.1  # how often the pipe threads check whether _reap told them to stop
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_PROMPT_BYTES = 1024 * 1024
_MAX_TIMEOUT_S = 21600  # six hours
# ponytail: a fixed allowlist. Add a key when a CLI refuses to start without it.
_ENV_KEYS = (
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
)
_CREDENTIAL = {"claude": "ANTHROPIC_API_KEY", "cursor": "CURSOR_API_KEY"}
# Hyphens stay legal inside a name (gpt-5). A leading hyphen is still a flag.
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")
# ponytail: string matches. Update when a CLI changes its wording.
_SIGN_IN = {
    "claude": ("invalid api key", "please run /login", "not logged in"),
    "cursor": ("not logged in", "authentication required", "please log in", "cursor-agent login"),
}


@dataclass
class Result:
    exit_code: int
    text: str
    timed_out: bool
    seconds: float
    argv: List[str]
    truncated: bool = False
    outcome: str = "ok"


def _argv(name: str, model: Optional[str], force: bool) -> List[str]:
    if name == "claude":
        if force:
            raise ValueError("force is only valid for cursor")
        # WHY: -p reads the prompt from stdin. stream-json in print mode errors without --verbose.
        # WHY: print mode loads this directory's hooks and .mcp.json with no trust check.
        argv = [
            "claude",
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
        ]
    elif name == "cursor":
        # WHY: cursor-agent --help is silent on stdin, but with no positional prompt and a
        # non-tty stdin it reads the prompt from the pipe. A pipe is not a tty.
        # WHY: print mode can write and run a shell. ask is read-only; force may edit, still sandboxed.
        argv = [
            "cursor-agent",
            "-p",
            "--output-format",
            "stream-json",
            "--stream-partial-output",
        ]
        if force:
            argv.extend(["--sandbox", "enabled", "--force"])
        else:
            argv.extend(["--mode", "ask", "--sandbox", "enabled"])
    else:
        raise ValueError("unknown agent %r; expected claude or cursor" % (name,))
    if model:
        if model.startswith("-") or _MODEL_RE.fullmatch(model) is None:
            raise ValueError("invalid model %r" % (model,))
        argv.append("--model=" + model)
    return argv


def _resolve(binary: str) -> str:
    # WHY: resolve before the caller's env is applied, so their PATH cannot choose the binary.
    found = shutil.which(binary, path=os.environ.get("PATH"))
    # WHY: "", ".", or "bin" on PATH makes which() return a relative path. Popen would
    # then resolve it against the job directory, where a planted binary can live.
    if not found or not os.path.isabs(found):
        raise FileNotFoundError("%s not found on PATH" % (binary,))
    return found


def _child_env(name: str, env: Optional[Mapping[str, str]]) -> dict:
    child = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
    credential = _CREDENTIAL[name]
    if credential in os.environ:
        child[credential] = os.environ[credential]
    if env:
        child.update(env)
    return child


def _signal_group(pid: int, sig: int) -> None:
    if not pid:
        return
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return


def _stop(proc: subprocess.Popen) -> None:
    _signal_group(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=_KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        _signal_group(proc.pid, signal.SIGKILL)
        proc.wait()


def _close_stream(stream) -> None:
    if stream is None:
        return
    try:
        if stream.closed:
            return
    except AttributeError:
        return
    # WHY: no pipe thread can be blocked inside the buffered object any more, so close()
    # cannot sit on the buffered-IO lock that a stuck reader or writer used to hold.
    try:
        stream.close()
    except (OSError, ValueError):
        return


def _outcome(name: str, exit_code: int, text: str, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if exit_code == 0:
        return "ok"
    tail = text.encode("utf-8", "replace")[-4096:].decode("utf-8", "replace").lower()
    for marker in _SIGN_IN[name]:
        if marker in tail:
            return "not_signed_in"
    return "error"


def _acquire_lock(lock_dir: str) -> Optional[int]:
    # WHY: O_NOFOLLOW refuses a symlink planted at harness.lock. Do not follow it.
    fd = os.open(
        os.path.join(lock_dir, "harness.lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def _release_lock(held: List[Optional[int]]) -> None:
    fd = held[0]
    if fd is None:
        return
    held[0] = None
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _reap(
    proc: subprocess.Popen,
    reader: threading.Thread,
    writer: threading.Thread,
    reader_started: bool,
    writer_started: bool,
    stop: threading.Event,
) -> None:
    if proc.poll() is None:
        _stop(proc)
    if reader_started:
        reader.join(_DRAIN_S)
        if reader.is_alive():
            _signal_group(proc.pid, signal.SIGKILL)
            stop.set()
            reader.join(_DRAIN_S)
    if writer_started:
        writer.join(_DRAIN_S)
        if writer.is_alive():
            stop.set()
            writer.join(_DRAIN_S)
    _close_stream(proc.stdin)
    _close_stream(proc.stdout)


def run(
    name: str,
    prompt: str,
    cwd: str,
    timeout_s: float,
    model: Optional[str] = None,
    on_delta: Optional[Callable[[str], None]] = None,
    env: Optional[Mapping[str, str]] = None,
    *,
    force: bool = False,
    lock_dir: str | None = None,
) -> Result:
    """Run one agent CLI. The prompt is written to stdin and is never placed in argv."""
    if not math.isfinite(timeout_s) or not timeout_s > 0 or timeout_s > _MAX_TIMEOUT_S:
        raise ValueError("timeout_s must be finite, > 0, and at most %s seconds" % (_MAX_TIMEOUT_S,))
    if len(prompt.encode("utf-8", "replace")) > _MAX_PROMPT_BYTES:
        raise ValueError("prompt exceeds %s bytes" % (_MAX_PROMPT_BYTES,))
    argv = _argv(name, model, force)
    if lock_dir is not None and not os.path.isdir(lock_dir):
        raise ValueError("lock_dir does not exist")
    binary = argv[0]
    try:
        argv[0] = _resolve(binary)
    except FileNotFoundError:
        return Result(
            exit_code=127,
            text="",
            timed_out=False,
            seconds=0.0,
            argv=[binary],
            outcome="harness_missing",
        )
    held: List[Optional[int]] = [None]
    if lock_dir is not None:
        held[0] = _acquire_lock(lock_dir)
        if held[0] is None:
            return Result(
                exit_code=75,
                text="",
                timed_out=False,
                seconds=0.0,
                argv=list(argv),
                outcome="busy",
            )
    try:
        started = time.monotonic()
        # WHY: never inherit os.environ. A caller PATH may overlay the child env, not argv[0].
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=_child_env(name, env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except BaseException:
        _release_lock(held)
        raise
    parts: List[str] = []
    errors: List[Exception] = []
    timed_out = False
    truncated = False

    stop = threading.Event()

    def read_stdout() -> None:
        nonlocal truncated
        assert proc.stdout is not None
        # WHY: closing the fd does not wake a thread blocked in read() on Linux, so read the raw
        # fd in short poll() steps and stop when _reap sets `stop`. Never use proc.stdout's
        # buffered object: its lock is what a blocked readline used to hold. poll, not select:
        # select() can't take an fd above 1023.
        fd = proc.stdout.fileno()
        ready = select.poll()
        ready.register(fd, select.POLLIN)
        got = 0
        pending = b""

        def emit(piece: bytes) -> None:
            text = piece.decode("utf-8", "replace")
            parts.append(text)
            if on_delta is not None:
                on_delta(text)

        try:
            while True:
                if stop.is_set():
                    break
                if not ready.poll(_POLL_S * 1000):
                    continue
                # WHY: a bounded read, so output with no newline can't grow past the cap in memory.
                chunk = os.read(fd, min(65536, _MAX_OUTPUT_BYTES - got + 1))
                if not chunk:
                    break
                got += len(chunk)
                pending += chunk
                while b"\n" in pending:
                    piece, pending = pending.split(b"\n", 1)
                    emit(piece + b"\n")
                if got > _MAX_OUTPUT_BYTES:
                    truncated = True
                    _signal_group(proc.pid, signal.SIGKILL)
                    break
            if pending:
                emit(pending)
        except Exception as exc:
            errors.append(exc)
            _signal_group(proc.pid, signal.SIGKILL)

    def write_stdin() -> None:
        assert proc.stdin is not None
        try:
            # WHY: a lone surrogate raises UnicodeEncodeError, which is not an OSError.
            # The thread would die with stdin still open and the child would wait out the timeout.
            data = memoryview(prompt.encode("utf-8", "replace"))
            # WHY: closing the fd does not wake a thread blocked in write() on Linux, so poll a
            # non-blocking fd in short poll() steps and stop when _reap sets `stop`.
            fd = proc.stdin.fileno()
            os.set_blocking(fd, False)
            ready = select.poll()
            ready.register(fd, select.POLLOUT)
            while data and not stop.is_set():
                if not ready.poll(_POLL_S * 1000):
                    continue
                try:
                    n = os.write(fd, data)
                except BlockingIOError:
                    continue
                data = data[n:]
        except OSError:
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    reader = threading.Thread(target=read_stdout)
    writer = threading.Thread(target=write_stdin)
    reader_started = False
    writer_started = False
    # WHY: start used to sit outside the cleanup. A failed writer.start left the reader
    # unjoined, and KeyboardInterrupt (BaseException) skipped the Exception handler.
    try:
        reader.start()
        reader_started = True
        writer.start()
        writer_started = True
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _stop(proc)
        _reap(proc, reader, writer, reader_started, writer_started, stop)
        if errors:
            raise errors[0]
        code = proc.returncode
        if code is None:
            code = proc.wait()
        text = "".join(parts)
        return Result(
            exit_code=code,
            text=text,
            timed_out=timed_out,
            seconds=time.monotonic() - started,
            argv=list(argv),
            truncated=truncated,
            outcome=_outcome(name, code, text, timed_out),
        )
    except BaseException:
        _reap(proc, reader, writer, reader_started, writer_started, stop)
        raise
    finally:
        _release_lock(held)

# harness

A small library that runs a coding-agent CLI headless and returns what it prints.

Version 0.1 drives Claude Code (`claude`) and Cursor (`cursor-agent`).

## API

```python
from harness import run

result = run(name, prompt, cwd, timeout_s, model=None, on_delta=None, env=None, force=False, lock_dir=None)
```

`name` is `"claude"` or `"cursor"`. `result` has `exit_code`, `text`, `timed_out`, `seconds`, `argv`, `truncated`, and `outcome`. `outcome` is `ok`, `timeout`, `busy`, `not_signed_in`, `harness_missing`, or `error`. `truncated` is true when stdout grows past 8 MiB. The driver then stops reading and kills the process group.

`on_delta(text)`, when given, is called with each stdout line as it arrives. `text` is those lines joined. In 0.1 the lines are the CLI's raw `stream-json` events; parsing them is the caller's job.

`model`, when given, must match `^[A-Za-z0-9._:/-]+$` and must not start with `-`. It is one argv token, `--model=<value>`.

`force` is keyword-only. It defaults to false and is only valid when `name` is `"cursor"`. `force=True` with `name="claude"` raises `ValueError`. The prompt is not parsed for flags, so text in the prompt cannot set `force`.

## Prompt on stdin

The prompt is written to the child's standard input. It is never put on the command line. Command-line arguments show up in process listings, so `argv` on the result does not contain the prompt. A prompt longer than 1 MiB is refused: `run` raises `ValueError`, and the command prints an error and exits 2.

## Claude Code

Claude Code in print mode loads the working directory's project hooks and `.mcp.json` servers with no trust check. The argv therefore includes `--safe-mode`, `--setting-sources user`, `--strict-mcp-config`, `--permission-mode dontAsk`, and `--permission-prompts none`. That keeps user settings, skips project MCP servers, and does not block on a permission prompt. The driver does not pass `--allowedTools`, `--bare`, or `--dangerously-skip-permissions`.

## Cursor

Cursor print mode can write files and run a shell with no sandbox. The argv always includes `--sandbox enabled`. With `force=False` it also includes `--mode ask`, so Cursor does not edit. With `force=True` it includes `--force` and no `--mode`, so Cursor can edit inside the sandbox.

## Environment

The child does not inherit the parent environment. Values are never logged or returned. When set, the child receives `PATH`, `HOME`, `USER`, `LOGNAME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TERM`, `TMPDIR`, `SHELL`, `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `ALL_PROXY`, the lowercase forms of those four proxy names, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, and `NODE_EXTRA_CA_CERTS`, plus one credential: `ANTHROPIC_API_KEY` for Claude, or `CURSOR_API_KEY` for Cursor. `env`, when given, is applied on top of that set.

`argv[0]` is an absolute path. The binary is resolved once from the parent's `PATH`, before `env` is applied. A `PATH` in `env` does not change `argv[0]`. If the name is not on the parent's `PATH`, or the match is not absolute (`""`, `.`, `bin`), `run` returns `outcome="harness_missing"` with exit 127 and does not start a process. It never falls back to a `PATH` in `env`. A bad argument still raises `ValueError`.

## Lock

`lock_dir`, when given, is an existing directory. `run` holds `<lock_dir>/harness.lock` for the whole call (`fcntl.flock`, non-blocking). A lock already held returns `outcome="busy"` (exit 75) and starts nothing. A symlink at `harness.lock` is refused. No `lock_dir` means no lock.

## Timeout

The child is started in its own process group. `timeout_s` must be finite, greater than 0, and at most 21600 seconds (six hours). When `timeout_s` elapses, that group is sent SIGTERM. After five seconds, if it is still running, it is sent SIGKILL. `timed_out` is then true. If the CLI exits but leaves a child holding its output open, that leftover group is killed after two seconds. A grandchild that called `setsid` has left the group and survives the kill. `run` closes its copy of the stdout pipe and waits at most two more seconds for the reader. That is the ceiling: `run` returns, and a `setsid` grandchild can outlive the call.

## Command

```shell
python3 -m harness run --name claude --cwd DIR --timeout 1800 [--model M] [--lock-dir DIR] < prompt.txt
python3 -m harness run --name cursor --cwd DIR --timeout 1800 [--model M] [--force] [--lock-dir DIR] < prompt.txt
```

`--force` is only valid with `--name cursor`. The command prints each stdout line as it arrives. The first stderr line is `outcome: <outcome>`. It exits 0 for ok, 75 for busy, 127 for harness_missing, 124 for timeout, 77 for not_signed_in, and the CLI's own non-zero status for error.

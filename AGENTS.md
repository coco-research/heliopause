# Agent rules

If a human asked you to install Heliopause, follow [INSTALL.md](INSTALL.md) exactly. This page is for agents changing the code.

## Rules

- Python 3.9+ (macOS's built-in python3), stdlib only. No `match`, and no `X | Y` types evaluated at runtime; every module starts with `from __future__ import annotations`.
- No new runtime dependencies. pytest is the only dev dependency.
- The server binds `127.0.0.1` only.
- Read-only on every source, except the append-only writes the API already has: board lines, handoffs, alerts, and feedback. Each one is token-authenticated and appends a single line.
- Never write outside the repository at runtime.
- Never read key files, `~/.secrets`, or any `.env`.
- Small PRs, one change each.
- A test in the same PR.
- Green CI. Never commit to main directly.
- Tests use fixture files under `tests/fixtures`, never live paths in your home folder.
- Ponytail: the simplest thing that works. Mark known ceilings with a `ponytail:` comment.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest tests -q
```

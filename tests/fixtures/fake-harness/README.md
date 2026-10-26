Shared stand-in for claude and cursor-agent. Pass FAKE_MODE via run(env=...); the child env is an allowlist.
FAKE_MODE=ok: echo stdin back as one stream-json line, exit 0.
FAKE_MODE=slow: sleep FAKE_SLEEP seconds, then ok.
FAKE_MODE=signed_out: claude prints "Error: Invalid API key · Please run /login"; cursor-agent prints "Not logged in. Authentication required. Please log in. cursor-agent login"; exit 1.
FAKE_MODE=fail: print boom, exit 2.

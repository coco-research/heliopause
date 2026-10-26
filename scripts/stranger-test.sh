#!/usr/bin/env bash
set -euo pipefail

# Stranger test: prove a fresh clone installs, runs, passes doctor, and uninstalls cleanly.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

START_TIME=$(date +%s)
SERVER_PID=""
CURRENT_STEP=""

H=$(mktemp -d)

cleanup() {
  local exit_code=$?
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$H"
  local elapsed=$(($(date +%s) - START_TIME))
  if [[ $exit_code -ne 0 && -n "$CURRENT_STEP" ]]; then
    echo "FAIL: $CURRENT_STEP"
  fi
  echo "Total: ${elapsed}s"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export HOME="$H"
export PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
# WHY: no PYTHONDONTWRITEBYTECODE here. The steps run the README's exact commands (python3 -B), so a
# missing -B or a cache Atlas writes shows up as a HOME difference, the way a stranger would get it.
unset PYTHONDONTWRITEBYTECODE
unset ATLAS_LAB
PY="${PY:-/usr/bin/python3}"

# a. Clone repo
CURRENT_STEP="a. clone"
git clone --quiet "$REPO" "$H/atlas"
echo "PASS: a. clone"

# b. Snapshot $H excluding $H/atlas
CURRENT_STEP="b. snapshot"
snapshot() {
  "$PY" -B -c '
import hashlib, os, sys
h = os.path.abspath(sys.argv[1])
atlas = os.path.join(h, "atlas")
lines = []
for root, dirs, files in os.walk(h):
    # The clone is not part of HOME under test; do not walk it.
    dirs[:] = [d for d in dirs if os.path.join(root, d) != atlas]
    for d in sorted(dirs):
        p = os.path.join(root, d)
        try:
            lines.append(f"{p} dir {oct(os.lstat(p).st_mode & 0o777)}")
        except OSError:
            pass
    for f in sorted(files):
        p = os.path.join(root, f)
        try:
            st = os.lstat(p)
            with open(p, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            lines.append(f"{p} {st.st_size} {oct(st.st_mode & 0o777)} {digest}")
        except OSError:
            pass
for line in sorted(lines):
    print(line)
' "$H"
}
snap1=$(snapshot)
echo "PASS: b. snapshot"

# c. init -> hooks --yes -> up -> poll state -> verify nodes & echo -> doctor -> stop server
CURRENT_STEP="c. lifecycle"
cd "$H/atlas"
"$PY" -B -m atlas init --yes >/dev/null
"$PY" -B -m atlas hooks --yes >/dev/null
grep -q "atlas-hook-start" "$H/.claude/settings.json" || { echo "hooks were not installed"; exit 1; }

PORT=$("$PY" -B -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
"$PY" -B -m atlas up --port "$PORT" >/dev/null 2>&1 &
SERVER_PID=$!

"$PY" -B -c '
import json, sys, time, urllib.request

port = int(sys.argv[1])
url = f"http://127.0.0.1:{port}/api/state"
deadline = time.time() + 10
state = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=1) as res:
            if res.status == 200:
                state = json.load(res)
                break
    except Exception:
        time.sleep(0.1)

if state is None:
    sys.exit("timed out waiting for /api/state")

nodes = sorted(state.get("nodes", {}).keys())
if nodes != ["lead", "owner", "worker"]:
    sys.exit(f"unexpected nodes: {nodes}")
' "$PORT"

"$PY" -B -c '
from pathlib import Path
import sys

lab = Path(sys.argv[1]) / "atlas-lab"
echoes = list((lab / "queue" / "done").glob("echo-*.md"))
if not echoes:
    sys.exit("no echo job found in queue/done")
text = echoes[0].read_text(encoding="utf-8")
if "echo: hello" not in text:
    sys.exit(f"no echo reply in {echoes[0]}")
' "$H"

"$PY" -B -m atlas doctor --port "$PORT" >/dev/null

kill "$SERVER_PID"
wait "$SERVER_PID" 2>/dev/null || true
SERVER_PID=""
echo "PASS: c. lifecycle"

# d. uninstall --yes -> snapshot again -> diff must be empty
CURRENT_STEP="d. uninstall"
"$PY" -B -m atlas uninstall --yes >/dev/null
snap2=$(snapshot)
if [[ "$snap1" != "$snap2" ]]; then
  diff -u <(echo "$snap1") <(echo "$snap2")
  exit 1
fi
echo "PASS: d. uninstall"

CURRENT_STEP=""

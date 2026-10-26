#!/bin/sh
# Fail-soft. No network. A hook's output is fed back into the tool, so this prints nothing and exits 0.
{
  runtime="$(dirname "$0")/runtime"
  # Only a file this user owns that nobody else can write, in a folder nobody else can write.
  hooks_dir="$(dirname "$0")"
  if [ -f "$runtime" ] && [ ! -L "$runtime" ] && [ -O "$runtime" ] \
    && [ -z "$(find "$runtime" "$hooks_dir" -prune \( -perm -020 -o -perm -002 \) 2>/dev/null)" ]; then
    py=
    root=
    lab=
    {
      IFS= read -r py || true
      IFS= read -r root || true
      IFS= read -r lab || true
    } < "$runtime"
    case "$py$root$lab" in *"
"*) py= ;; esac
    case "$py" in /*) ;; *) py= ;; esac
    case "$root" in /*) ;; *) root= ;; esac
    case "$lab" in /*) ;; *) lab= ;; esac
    # No lab line: nothing to report. An optional third line must be an existing absolute directory.
    if [ -n "$py" ] && [ -n "$root" ] && [ -n "$lab" ] && [ -d "$lab" ] \
      && [ -f "$py" ] && [ -x "$py" ] && [ -f "$root/atlas/__main__.py" ]; then
      # https://docs.x.ai/build/features/hooks — a non-empty GROK_HOOK_EVENT is a Grok session.
      if [ -n "$GROK_HOOK_EVENT" ]; then
        event=$(printf '%.40s' "$(printf '%s' "$GROK_HOOK_EVENT" | LC_ALL=C tr -cd 'A-Za-z0-9_')")
        mkdir -p "$lab/logs"
        printf '%s grok claude-report %s\n' "$(LC_ALL=C date -u +%Y-%m-%dT%H:%M:%SZ)" "$event" >> "$lab/logs/grok-hooks.log"
        exit 0
      fi
      # -I: ignore the session folder, PYTHONPATH and user site, so a repo with its own
      # atlas/ or atlas.py can never run here. The Heliopause folder is put on the path by hand.
      "$py" -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); from atlas.connectors import run; raise SystemExit(run(["report", "--tool", sys.argv[2], "--lab", sys.argv[3]]))' "$root" claude-code "$lab" >/dev/null 2>&1 || true
    fi
  fi
} >/dev/null 2>&1
exit 0

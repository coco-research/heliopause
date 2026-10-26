#!/bin/sh
# Sourced by connectors/<tool>/report.sh, which sets TOOL. Prints nothing. Exits 0.
# Runtime is ../runtime next to the tool folders: python, Heliopause root, lab. All three required.
{
  runtime="$(dirname "$0")/../runtime"
  runtime_dir="$(dirname "$runtime")"
  if [ -f "$runtime" ] && [ ! -L "$runtime" ] && [ -O "$runtime" ] \
    && [ -z "$(find "$runtime" "$runtime_dir" -prune \( -perm -020 -o -perm -002 \) 2>/dev/null)" ]; then
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
    if [ -n "$py" ] && [ -n "$root" ] && [ -n "$lab" ] && [ -d "$lab" ] \
      && [ -n "$TOOL" ] && [ -f "$py" ] && [ -x "$py" ] && [ -f "$root/atlas/__main__.py" ]; then
      # https://docs.x.ai/build/features/hooks — a non-empty GROK_HOOK_EVENT is a Grok session.
      if [ -n "$GROK_HOOK_EVENT" ] && [ "$TOOL" != grok ]; then
        event=$(printf '%.40s' "$(printf '%s' "$GROK_HOOK_EVENT" | LC_ALL=C tr -cd 'A-Za-z0-9_')")
        mkdir -p "$lab/logs"
        printf '%s grok %s-report %s\n' "$(LC_ALL=C date -u +%Y-%m-%dT%H:%M:%SZ)" "$TOOL" "$event" >> "$lab/logs/grok-hooks.log"
        exit 0
      fi
      # -I: ignore the session folder, PYTHONPATH and user site. The Heliopause root is added by hand.
      "$py" -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); from atlas.connectors import run; raise SystemExit(run(["report", "--tool", sys.argv[2], "--lab", sys.argv[3]]))' "$root" "$TOOL" "$lab" >/dev/null 2>&1 || true
    fi
  fi
} >/dev/null 2>&1
exit 0

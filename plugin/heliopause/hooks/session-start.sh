#!/bin/sh
# Fail-soft. No network. The shim is optional; this script always exits 0.
# https://docs.x.ai/build/features/hooks — non-empty GROK_HOOK_EVENT is a Grok Build session.
if [ -n "${GROK_HOOK_EVENT:-}" ]; then
  _gg_trim() {
    _gg_v=$1
    while :; do
      case "$_gg_v" in
        [[:space:]]*) _gg_v=${_gg_v#?} ;;
        *) break ;;
      esac
    done
    while :; do
      case "$_gg_v" in
        *[[:space:]]) _gg_v=${_gg_v%?} ;;
        *) break ;;
      esac
    done
    printf '%s' "$_gg_v"
  }
  _gg_event=$(printf '%s' "$GROK_HOOK_EVENT" | LC_ALL=C tr -cd 'A-Za-z0-9_' | LC_ALL=C cut -c1-40) 2>/dev/null || true
  _gg_lab=
  _gg_raw=$(_gg_trim "${ATLAS_LAB-}") 2>/dev/null || true
  if [ -n "$_gg_raw" ]; then
    case "$_gg_raw" in
      /*) [ -f "$_gg_raw/atlas.json" ] && _gg_lab="$_gg_raw" ;;
    esac
  elif [ -n "${HOME-}" ] && [ -r "${HOME}/.atlas/lab" ]; then
    _gg_line=
    _gg_saw=
    while IFS= read -r _gg_line || [ -n "$_gg_line" ]; do
      _gg_line=$(_gg_trim "$_gg_line") || true
      if [ -n "$_gg_line" ]; then
        _gg_saw=1
        break
      fi
    done < "${HOME}/.atlas/lab"
    if [ -n "$_gg_saw" ]; then
      case "$_gg_line" in
        /*) [ -f "$_gg_line/atlas.json" ] && _gg_lab="$_gg_line" ;;
      esac
    elif [ -f "${HOME}/atlas-lab/atlas.json" ]; then
      _gg_lab="${HOME}/atlas-lab"
    fi
  elif [ -n "${HOME-}" ] && [ -f "${HOME}/atlas-lab/atlas.json" ]; then
    _gg_lab="${HOME}/atlas-lab"
  fi
  if [ -n "$_gg_lab" ]; then
    mkdir -p "$_gg_lab/logs" 2>/dev/null || true
    _gg_stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ) 2>/dev/null || _gg_stamp=
    printf '%s grok session-start.sh %s\n' "$_gg_stamp" "$_gg_event" >> "$_gg_lab/logs/grok-hooks.log" 2>/dev/null || true
  fi
  exit 0
fi
shim="$HOME/.atlas/bin/atlas"
if [ -e "$shim" ]; then
  "$shim" hook session-start || true
else
  runtime="$(dirname "$0")/runtime"
  # Only a file this user owns that nobody else can write, in a folder nobody else can write.
  hooks_dir="$(dirname "$0")"
  if [ -f "$runtime" ] && [ ! -L "$runtime" ] && [ -O "$runtime" ] \
    && [ -z "$(find "$runtime" "$hooks_dir" -prune \( -perm -020 -o -perm -002 \) 2>/dev/null)" ]; then
    py=
    root=
    {
      IFS= read -r py || true
      IFS= read -r root || true
    } < "$runtime"
    case "$py$root" in *"
"*) py= ;; esac
    case "$py" in /*) ;; *) py= ;; esac
    case "$root" in /*) ;; *) root= ;; esac
    if [ -n "$py" ] && [ -n "$root" ] && [ -f "$py" ] && [ -x "$py" ] && [ -f "$root/atlas/__main__.py" ]; then
      # -I: ignore the session folder, PYTHONPATH and user site, so a repo with its own
      # atlas/ or atlas.py can never run here. The Heliopause folder is put on the path by hand.
      "$py" -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); from atlas.plugin import hook; raise SystemExit(hook(["session-start"]))' "$root" || true
    fi
  fi
fi
exit 0

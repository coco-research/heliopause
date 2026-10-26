#!/bin/sh
# Sources the shared script only if this user owns it and neither it nor its folder is group/world-writable.
TOOL=grok
common="$(dirname "$0")/../report-common.sh"
if [ -f "$common" ] && [ ! -L "$common" ] && [ -O "$common" ] \
  && [ -z "$(find "$common" "$(dirname "$common")" -prune \( -perm -020 -o -perm -002 \) 2>/dev/null)" ]; then
  . "$common" >/dev/null 2>&1
fi
exit 0

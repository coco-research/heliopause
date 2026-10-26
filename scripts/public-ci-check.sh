#!/bin/sh
# The public repo runs CI on GitHub-hosted runners only.
# A self-hosted label would let fork PRs run on the maintainer's machine, so the export stops.
set -eu

if [ "$#" -ne 1 ] || [ ! -d "$1" ]; then
    echo "usage: public-ci-check.sh <tree>" >&2
    exit 2
fi

tree=$1

if [ ! -s "$tree/.github/workflows/ci.yml" ]; then
    echo "STOP: no public ci.yml in the export" >&2
    exit 3
fi

if grep -rqis -- self-hosted "$tree/.github"; then
    echo "STOP: self-hosted runner in the public workflows" >&2
    exit 3
fi

# GitHub matches labels case-insensitively, and a custom label alone can reach a
# self-hosted runner, so a denylist is not enough: every runs-on/os value must be a
# GitHub-hosted label. Expressions are allowed on runs-on only; their values are
# checked through the os: lines of the matrix. Only ${{ matrix.os }} is accepted.
# ponytail: an exact list of full hosted labels (no suffix combining, so a made-up label never passes).
# A real label missing here stops the export (fails closed); add it when a workflow needs it.
# ponytail: line-based, not a YAML parser; block lists and group: runners are refused rather than understood.
for workflow in "$tree/.github/workflows"/*.yml "$tree/.github/workflows"/*.yaml; do
    [ -f "$workflow" ] || continue
    if ! stop=$(awk '
        BEGIN { sq = sprintf("%c", 39) }
        {
            line = $0
            sub(/^[ \t]+/, "", line)
            sub(/^-[ \t]+/, "", line)
            if (line ~ /^runs-on:/) {
                kind = "runs-on"
                value = substr(line, 9)
            } else if (line ~ /^os:/) {
                kind = "os"
                value = substr(line, 4)
            } else {
                next
            }
            sub(/[ \t]+#.*$/, "", value)
            sub(/^[ \t]+/, "", value)
            sub(/[ \t]+$/, "", value)
            if (substr(value, 1, 1) == "[") value = substr(value, 2)
            if (substr(value, length(value), 1) == "]") value = substr(value, 1, length(value) - 1)
            if (value == "") {
                print "STOP: runs-on value not a GitHub-hosted label: (block) (" FILENAME ")"
                exit 3
            }
            n = split(value, parts, ",")
            for (i = 1; i <= n; i++) {
                v = parts[i]
                sub(/^[ \t]+/, "", v)
                sub(/[ \t]+$/, "", v)
                if (substr(v, 1, 1) == "\"" || substr(v, 1, 1) == sq) v = substr(v, 2)
                if (substr(v, length(v), 1) == "\"" || substr(v, length(v), 1) == sq) v = substr(v, 1, length(v) - 1)
                sub(/^[ \t]+/, "", v)
                sub(/[ \t]+$/, "", v)
                if (kind == "runs-on" && v ~ /^\$\{\{ *matrix\.os *\}\}$/) continue
                if (tolower(v) ~ /^(ubuntu-latest|ubuntu-24\.04|ubuntu-22\.04|ubuntu-24\.04-arm|ubuntu-22\.04-arm|macos-latest|macos-15|macos-14|macos-13|macos-15-large|macos-14-large|macos-13-large|windows-latest|windows-2025|windows-2022|windows-11-arm)$/) continue
                print "STOP: runs-on value not a GitHub-hosted label: " v " (" FILENAME ")"
                exit 3
            }
        }
    ' "$workflow"); then
        printf '%s' "$stop" >&2
        exit 3
    fi
done

echo "public CI: hosted runners only"
exit 0

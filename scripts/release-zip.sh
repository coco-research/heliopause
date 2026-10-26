#!/usr/bin/env bash
# Pack <tag> into <outdir>/heliopause-<tag>.zip and sign a manifest for it.
# Exclude rules come from this checkout's .public-exclude, not from the tag, so v0.3.0 works too.
# ATLAS_SIGNING_PUBKEY is the release ed25519 public key. The private key stays in
# the ssh-agent. This script never opens it. Without the public key, or with no
# matching agent identity, nothing is built or signed.
set -euo pipefail
# Zip stores local times; UTC makes the same tag give the same bytes on any machine.
export TZ=UTC

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: scripts/release-zip.sh <tag> [outdir]" >&2
  exit 2
fi

# Resolve once. Later PATH changes cannot swap the interpreter that writes the manifest.
py=$(command -v python3 || true)
if [[ -z $py || ! -x $py ]]; then
  echo "release-zip: python3 not found" >&2
  exit 1
fi

pub=${ATLAS_SIGNING_PUBKEY:-}
if [[ -z $pub || ! -f $pub || -L $pub ]]; then
  echo "release-zip: set ATLAS_SIGNING_PUBKEY to the release public key; the private key stays in the ssh-agent. Refusing to build or sign" >&2
  exit 1
fi
keytype=$(awk 'NR==1 { print $1; exit }' "$pub")
blob=$(awk 'NR==1 { print $2; exit }' "$pub")
if [[ $keytype != ssh-ed25519 || -z $blob ]]; then
  echo "release-zip: ATLAS_SIGNING_PUBKEY must be an ed25519 public key. Refusing to build or sign" >&2
  exit 1
fi
sock=${SSH_AUTH_SOCK:-}
if [[ -z $sock || ! -S $sock ]]; then
  echo "release-zip: no ssh-agent identity; add the release key to the agent. Refusing to build or sign" >&2
  exit 1
fi
if ! listed=$(/usr/bin/ssh-add -L 2>/dev/null); then
  echo "release-zip: no ssh-agent identity; add the release key to the agent. Refusing to build or sign" >&2
  exit 1
fi
if ! printf '%s\n' "$listed" | awk -v blob="$blob" '$2 == blob { found=1 } END { exit found ? 0 : 1 }'; then
  echo "release-zip: ssh-agent has no identity matching ATLAS_SIGNING_PUBKEY. Refusing to build or sign" >&2
  exit 1
fi
fp=$(/usr/bin/ssh-keygen -lf "$pub" | awk '{ print $2; exit }')
if [[ $fp != SHA256:* ]]; then
  echo "release-zip: could not fingerprint ATLAS_SIGNING_PUBKEY. Refusing to build or sign" >&2
  exit 1
fi

tag=$1
outdir=${2:-dist}
root=$(cd "$(dirname "$0")/.." && pwd)
case $outdir in
  /*) ;;
  *) outdir=$(pwd)/$outdir ;;
esac

git -C "$root" rev-parse --verify "${tag}^{commit}" >/dev/null

work=$(mktemp -d)
chmod 700 "$work"
trap 'rm -rf "$work"' EXIT
# A tag may hold "/" (release/1.0); the file and folder names may not.
slug=${tag//\//-}
# Tag v1.2.3 records version 1.2.3. The filename keeps the tag.
version=${tag#v}
prefix="heliopause-${slug}"
git -C "$root" archive --format=tar --prefix="${prefix}/" -- "$tag" | /usr/bin/tar -x -C "$work"
tree=$work/$prefix

# A newline splits zip -@. A symlink is extracted as a link; writing or deleting
# through it would touch the referent, and zip would store that referent.
if find "$tree" -print0 | "$py" -c 'import sys; sys.exit(0 if b"\n" in sys.stdin.buffer.read() else 1)'; then
  echo "release-zip: refusing a path that contains a newline" >&2
  exit 1
fi
if [[ -n $(find "$tree" -type l -print -quit) ]]; then
  echo "release-zip: refusing a symlink in $tag" >&2
  exit 1
fi

trim() {
  local s=$1
  s=${s#"${s%%[![:space:]]*}"}
  s=${s%"${s##*[![:space:]]}"}
  printf '%s' "$s"
}

# Any symlink component, not only the final name. Returns 0 when one is present.
link_in() {
  local path=$1
  local cur=$tree
  local rel part
  if [[ -L $cur ]]; then
    return 0
  fi
  [[ $path == "$tree" ]] && return 1
  rel=${path#"$tree"/}
  while [[ -n $rel ]]; do
    part=${rel%%/*}
    cur=$cur/$part
    if [[ -L $cur ]]; then
      return 0
    fi
    if [[ $rel == */* ]]; then
      rel=${rel#*/}
    else
      break
    fi
    if [[ ! -e $cur && ! -L $cur ]]; then
      return 1
    fi
  done
  return 1
}

# Open each component with O_NOFOLLOW so the stub cannot truncate a referent.
write_stub() {
  local rel=$1
  "$py" - "$tree" "$rel" <<'PY'
import os
import sys

root, rel = sys.argv[1], sys.argv[2]
flag = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
try:
    fd = os.open(root, os.O_RDONLY | flag)
    try:
        parts = rel.split("/")
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | flag, dir_fd=fd)
            os.close(fd)
            fd = nxt
        out = os.open(parts[-1], os.O_WRONLY | os.O_TRUNC | flag, dir_fd=fd)
    finally:
        os.close(fd)
    try:
        os.write(out, b"{}\n")
    finally:
        os.close(out)
except OSError as err:
    sys.stderr.write("release-zip: refusing a symlink in %s (%s)\n" % (rel, err.strerror))
    sys.exit(1)
PY
}

while IFS= read -r raw || [[ -n ${raw:-} ]]; do
  raw=${raw%$'\r'}
  line=$(trim "${raw%%#*}")
  [[ -z $line ]] && continue
  rel=$line
  stub=0
  if [[ $line == *' => {}' ]]; then
    stub=1
    rel=$(trim "${line%' => {}'}")
  fi
  rel=${rel%/}
  case $rel in
    "" | . | ./* | /* | .. | ../* | */.. | */../*)
      echo "bad exclude path: $rel" >&2
      exit 1
      ;;
  esac
  target=$tree/$rel
  if link_in "$target"; then
    echo "release-zip: refusing a symlink in $rel" >&2
    exit 1
  fi
  if [[ $stub -eq 1 ]]; then
    if [[ -f $target && ! -L $target ]]; then
      write_stub "$rel"
    fi
  elif [[ -e $target || -L $target ]]; then
    rm -rf -- "$target"
  else
    # Older tags lack some paths, so this only warns; a renamed internal file would show up here.
    echo "release-zip: rule matched nothing in $tag: $rel" >&2
  fi
done <"$root/.public-exclude"

# An excluded path can leave its folder empty (vendor/). No empty folder ships.
(cd "$work" && find "$prefix" -mindepth 1 -depth -type d -empty -delete)

# tar leaves directory mtimes as "now". Zip stores those, so pin every path
# to the commit time. ponytail: DOS zip times are 2-second; this matches them.
# -h: do not follow a link if one appears after the scan above.
when=$(git -C "$root" log -1 --date=format-local:%Y%m%d%H%M.%S --format=%cd "${tag}^{commit}")
find "$tree" -exec touch -h -t "$when" {} +

name="heliopause-${slug}.zip"
# Hash and sign inside the 0700 work dir. outdir is writable by others; a name
# planted there is not created until the three files are ready to move.
zipfile=$work/$name
manifest=$work/heliopause-${slug}.manifest
sig=$work/heliopause-${slug}.manifest.sig
(
  cd "$work"
  find "$prefix" | LC_ALL=C sort | /usr/bin/zip -X -q -@ "$zipfile"
)
# macOS has shasum; other machines have sha256sum. Same digest. Absolute paths.
if [[ -x /usr/bin/shasum ]]; then
  digest=$(/usr/bin/shasum -a 256 "$zipfile" | awk '{ print $1; exit }')
elif [[ -x /usr/bin/sha256sum ]]; then
  digest=$(/usr/bin/sha256sum "$zipfile" | awk '{ print $1; exit }')
else
  echo "release-zip: need /usr/bin/shasum or /usr/bin/sha256sum" >&2
  exit 1
fi
size=$(wc -c <"$zipfile" | tr -d ' ')
date=$(date -u +%Y-%m-%dT%H:%M:%SZ)
"$py" - "$manifest" "$version" "$name" "$size" "$digest" "$date" <<'PY'
import json
import sys

path, version, filename, size, sha, date = sys.argv[1:7]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "version": version,
            "filename": filename,
            "size": int(size),
            "sha256": sha,
            "date": date,
        },
        handle,
        indent=2,
    )
    handle.write("\n")
PY
# Sign the manifest. -f is the public key; the private key stays in the agent.
if ! /usr/bin/ssh-keygen -Y sign -n atlas-release -f "$ATLAS_SIGNING_PUBKEY" <"$manifest" >"$sig"; then
  echo "release-zip: signing failed; the ssh-agent has no identity for ATLAS_SIGNING_PUBKEY. Refusing to publish" >&2
  exit 1
fi
final_zip=$outdir/$name
final_manifest=$outdir/heliopause-${slug}.manifest
final_sig=${final_manifest}.sig
# INSTALL checks the zip against this "<hex>  <name>" line.
final_sum=${final_zip}.sha256
printf '%s  %s\n' "$digest" "$name" >"$work/$name.sha256"
mkdir -p "$outdir"
# rm drops a planted symlink instead of writing through it. mv then creates the name.
rm -f -- "$final_zip" "$final_manifest" "$final_sig" "$final_sum"
mv -f -- "$zipfile" "$final_zip"
mv -f -- "$manifest" "$final_manifest"
mv -f -- "$sig" "$final_sig"
mv -f -- "$work/$name.sha256" "$final_sum"
echo "$final_zip"
echo "$digest"
# No separate release-notes template. This line is what the notes should carry.
echo "release notes: signing key ${fp} (ED25519). See SECURITY.md."

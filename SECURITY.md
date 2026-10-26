# Security policy

## Supported versions

Only the latest release is supported.

## Reporting a vulnerability

Report it privately with GitHub's **Report a vulnerability** flow (security advisories) for this repository. Do not open a public issue.

## Scope

In scope: the server, its JSON API, and the page.

## Security model

- The server binds `127.0.0.1` only.
- Host is locked on every request. Any other host is rejected.
- Every response sends a strict Content-Security-Policy and `X-Content-Type-Options: nosniff`.
- The board write is `POST /api/post`, which appends one line to the board as the configured actor (`~/.atlas/actor`, a node id; the default is the built-in lead node, `vp`). An actor Heliopause does not know is refused with 403. The other writes are the handoff routes (`/api/handoff`, `/api/claim`, `/api/done`), the alert and feedback routes below, and these routes, which exist only in lab mode (404 elsewhere): `POST /api/roles` and `POST /api/roles/switch` rewrite the lab's `atlas.json`; `POST /api/approve` answers an ask that is waiting on you; `POST /api/jobs/mark` records a job as done or failed; and `POST /api/report`, which needs the token, records a job result that a tool sends. The last three write message files in the lab's role queue. Every other POST path, and every PUT, DELETE, PATCH and OPTIONS, is answered with 405.
- A write needs the token from `~/.atlas/token` (created on first start, mode 0600 in a 0700 directory) in exactly one `X-Atlas-Token` header, compared in constant time. A missing or wrong token gets 401.
- Any request that sends an `Origin` header is refused with 403, so a web page can't write, even from this machine.
- The body must be `application/json` with a Content-Length of at most 8 KB. The headline is 1-60 characters, details at most 500, and control characters are refused.
- Alert posts (`POST /api/alerts`, and `POST /api/alerts/<id>/done` to close one) use the same gate as the board write: an `Origin` header is refused with 403, exactly one `X-Atlas-Token` is required, the body must be `application/json` and at most 8 KB. Unknown keys are refused, `source` and `kind` are lowercase names of up to 40 characters, and `text` is 1-200 characters.
- Alert text has control and invisible formatting characters removed and whitespace collapsed, and token-shaped strings are redacted, before it is stored. The page shows it as text, never as HTML.
- Alert links are allowlisted: `https://github.com/coco-research/...` or a local path that starts with a single `/`. A link with `..`, a backslash or a control character is refused, token-shaped strings in it are redacted, and the page makes only the GitHub form clickable.
- The page never holds the file token. Its page routes, `POST /api/alerts/<id>/close`, `POST /api/feedback`, `POST /api/roles`, `POST /api/roles/switch`, `POST /api/approve` and `POST /api/jobs/mark`, are the exception to the token rules above. They need a per-start UI nonce in one `X-Atlas-UI` header (constant-time compare) and exactly one `Origin` of `http://127.0.0.1:<port>` or `http://localhost:<port>`, plus `Sec-Fetch-Site: same-origin` when the browser sends it, `application/json`, and a body of at most 256 bytes. Anything else gets 403. A web page can't read the nonce (no CORS headers) or forge that `Origin`.
- The nonce keeps out web pages, not local programs. `GET /api/alerts` returns it to any process that can reach the port, including other users on the machine, and a process running as the owner can already read the 0600 token file. With the nonce a local process can close an alert, record a Useful / Not useful vote, change lab roles and switches, approve or decline an ask that is waiting on you, or mark a job done or failed. Posting an alert, the board write, the handoff routes and `POST /api/report` still need the token.
- `alerts.jsonl`, `rings.json` and `feedback.jsonl` are mode 0600, in the same 0700 `.atlas` directory as the token. `feedback.jsonl` holds only a timestamp, a kind, an id-shaped subject (up to 120 letters, digits and `._:#/-`) and the verdict, never text.
- The macOS notification for a needs-owner alert starts `osascript` with an argument list (no shell) and escapes the text. It is off with `--no-ring` and with `--demo`.
- Alerts are display only: Heliopause forwards nothing to a chat, inbox or agent.
- Key files, `~/keys`, `~/.secrets`, and any `.env` are never read.
- Transcript content is never read. Activity uses modification time only.

## Verifying a release

Releases are signed with a dedicated ed25519 SSH key. The private key stays in the ssh-agent (on the release Mac, the macOS keychain). `scripts/release-zip.sh` signs with `ssh-keygen -Y sign -n atlas-release -f "$ATLAS_SIGNING_PUBKEY"` and reads only that public key file. Without `ATLAS_SIGNING_PUBKEY`, or with no matching identity in the agent, the script stops and writes nothing.

The signature covers a manifest, `heliopause-v0.7.1.manifest`, not the zip. The manifest is JSON: version, filename, size, sha256, and a UTC date. The signature file is `heliopause-v0.7.1.manifest.sig`.

Heliopause 0.7 has no automatic updater. To update, download the new release and run the same check above before you unzip it.

The public key fingerprint is `SHA256:BhX36VYf09D8ozPzKQ76hAQEQjJ75y6pR+O4UNkeQCw` (ED25519). It is published in the README under "Verifying releases" and in the release notes line printed by the release script.

Before you unzip, check the download is ours. You need OpenSSH 8.1 or later. Recent macOS versions include it; check with `ssh -V`. In the folder with the four files, run:

```
shasum -a 256 -c --status heliopause-v0.7.1.zip.sha256 &&
echo 'atlas-release namespaces="atlas-release" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIArkj2zdOYSu0g8yfYV7Vru1PD1bUM/CKUUZ+QX2bXEV' > heliopause-allowed-signers &&
ssh-keygen -Y verify -f heliopause-allowed-signers -I atlas-release -n atlas-release -s heliopause-v0.7.1.manifest.sig < heliopause-v0.7.1.manifest > /dev/null &&
grep -qF "\"sha256\": \"$(shasum -a 256 heliopause-v0.7.1.zip | cut -c1-64)\"" heliopause-v0.7.1.manifest &&
grep -qF '"filename": "heliopause-v0.7.1.zip"' heliopause-v0.7.1.manifest &&
echo "Verified: heliopause-v0.7.1.zip is the signed v0.7.1 release." ||
{ echo "NOT VERIFIED: do not unzip. See SECURITY.md." >&2; false; }
```

It should print `Verified: heliopause-v0.7.1.zip is the signed v0.7.1 release.` If it doesn't, don't unzip, and see SECURITY.md.

### Emergency revocation

If the signing key is ever lost or stolen, it can't be revoked in copies you already have: a file signed with the old key still passes a check against the old fingerprint. If we change the key, the new key ships in a new release. Download it, check the new fingerprint published in the README and the release notes, then run the check above before you unzip it.

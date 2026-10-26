Heliopause reads this folder: `python3 -m atlas --lab ~/atlas-lab`

# The bus

A folder on this machine. Agents and the owner talk by writing small files. No network, no
service, no daemon: if you can read a file you can use it. It is local to this machine and
never synced anywhere.

```
~/atlas-lab/
  BUS.md      this file
  board.md    one line per state change, append only (who is working, blocked, done)
  feed.md     one entry per finished piece of work, append only
  inbox/      open asks waiting for an answer      (one .md file each)
  done/       replies, and closed asks             (one .md file each)
  queue/      (inbox/ running/ done/ failed/)
  nodes/
  decisions/
  briefs/     work orders for the factory             (one .md file each)
  atlas.json
  bin/        feed-append, bus-ask, bus-check, lab-ops-check
```

**The owner uses `python3 -m atlas bus …`, never `bin/*`.** The scripts in `bin/` are for agents working inside
their sandbox, and any agent can edit them, so the `atlas` command never runs them. Its bus commands are part of
the installed package and read and write this folder directly, without following a symlink or hard link:

- `python3 -m atlas bus ask <to> "<text>"` writes an ask into `inbox/` (`--type`, default `decision`; `--priority`; `--from`).
- `python3 -m atlas bus close <id>` moves an answered ask to `done/<id>.ask.md`.
- `python3 -m atlas bus check` validates this folder (the same checks as `bin/bus-check`). Read-only.
- `python3 -m atlas bus look` is the quick morning look (the same as `bin/lab-ops-check`). Read-only.
- `python3 -m atlas bus install` copies missing scripts into an existing lab for agents, asks first, and leaves a script unchanged when it differs from the shipped copy.

The lab is `--lab`, or `$ATLAS_LAB`, or `~/atlas-lab`.

## Who is who

| Role | What it is |
| --- | --- |
| owner | The human. Decides anything on the always-ask list. |
| lead | The orchestrator chat. Reads the board first, plans, delegates, reports to the owner. |
| brain | One chat per project, in that project's folder. Plans and decides for that project. |
| department | A standing function (design, engineering, research). Brains talk to departments directly. |

Names are plain labels you choose ("Billing brain", "Design dept"). No ids, no hashes.

## The board

One line per change of state. Always written with `bin/feed-append --board`, which stamps the
time from `date` (never type a time by hand).

```
YYYY-MM-DD HH:MM · <name> · <state> · <headline> · <details>
```

| State | Meaning |
| --- | --- |
| `working` | Started or still going. |
| `blocked` | Cannot continue without something from another agent. Say what in the details. |
| `needs-owner` | Waiting on a decision only the owner can make. |
| `needs-lead` | Waiting on the lead (a cross-project call, a merge, a conflict). |
| `done` | Finished. The headline says what now works. |
| `resolved` | Clears a `blocked`, `needs-owner` or `needs-lead` for a name. Headline: `<name>: <why>`. |

The headline is plain English for the owner: at most 60 characters, no ids, paths or hashes
("Report page merged, ready to try"). Details can hold anything else.

Latest line per name wins, so a name's current state is its newest line, unless a `resolved`
line for it is newer than the ask.

## The feed

After each finished piece of work (not every turn), one entry:

```
bin/feed-append "<title>" "<what changed, where>" "<what is needed next, if anything>"
```

## Asks and replies (the queue)

When an agent needs a decision, a review or a hand-off, it writes a file into `inbox/`:

```
bin/bus-ask --to owner --type decision --priority normal "Pick the report layout" <<'EOT'
Two options, A (table) and B (cards). Recommendation: A, because ...
EOT
```

The file is `inbox/<id>.md`, with a header between two `---` lines, then the body:

| Key | Required | Values |
| --- | --- | --- |
| `id` | yes | `[A-Za-z0-9._-]`, max 200 chars, unique, equals the filename. |
| `from` | yes | the asker's name |
| `to` | yes | `owner`, `lead`, or a brain or department name |
| `type` | yes | `decision`, `review`, `fyi`, `idea`, `job` |
| `priority` | no | `low`, `normal`, `high` |
| `created` | yes | `YYYY-MM-DD HH:MM` |

The answer is a file with the same id in `done/` (`done/<id>.md`, header `type: reply`). Then
the asker runs `bin/bus-ask --close <id>`, which moves the ask to `done/<id>.ask.md`.

Rules for writing files: write to a dot-temp name, then `mv` (bus-ask does this), so nobody reads
half a file. Never reuse an id. One ask, one decision, one question.

A reply in `done/` uses the same required keys (`id`, `from`, `to`, `type`, `created`), with `type: reply`.

## Briefs

A brief is a work order for the factory worker. The file is `briefs/<id>.md`, with a header between two `---` lines, then the sections Goal, Files allowed, Done when and Result.

| Key | Required | Values |
| --- | --- | --- |
| `id` | yes | `[A-Za-z0-9._-]`, max 30 characters (the board headline carries it), unique, equals the filename. |
| `repo` | yes | the project folder the owner opens |
| `branch` | yes | exactly `factory/<id>` (`bus-check` compares it with the id) |
| `model` | yes | the model the owner picks for this brief |

The factory does only what the brief says, touches only its files, runs its test, and commits on the branch. It does not push or merge. The lead reviews the branch and opens the pull request. Keep a finished brief: Result is the record.

Post a board line `Brief <id> · needs-owner · Open brief <id>` so the lead's own lines never hide it. When the brief is back, clear it with a `resolved` line whose headline is `Brief <id>: back`.

## Rules

- Append only: never edit or delete board and feed lines. Correct a line by adding a new one.
- Local only: this folder is never synced, mirrored or shared with another machine.
- No secrets in any file here. Ever.
- Check the folder any time: agents run `bin/bus-check`; the owner runs `python3 -m atlas bus check`. The owner's quick morning look is `python3 -m atlas bus look`.

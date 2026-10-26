# Copy pinned by tests/test_messages_view.py (from Design's COPY.md v22). Update both together.

- **Message states:** ◇ Waiting on you · ○ Sent · ◐ Picked up · ● Done · ▲ Failed

## Check words
- Checks: "● Tests pass" (with a count when the report has one, "· 6"), "▲ Tests fail · 2 of 14", "● Reviewer: approve", "○ No review", "● CI passed", "◐ CI running", "▲ CI failed", "○ No CI workflow"

**J8 · A job you start yourself **
- Moving now row on a sent job: "Copy job" (it replaces the hint "You start it in Cursor" as the action; the hint stays in the narrow-window rows)
- Thread note: "Cursor starts jobs when you do. Copy the job, paste it into Cursor, and Cursor reports back with the job's id."
- Copied text: "Job j-1042 for Builder in api-server: {job text}" followed by "When done, run cd ~/Heliopause && python3 -B -m atlas report j-1042 done|failed --tests pass|fail --lab ~/atlas-lab" **(K: the user pastes this into a tool, so it's an instruction to that tool)**
- Buttons: "Copy job" · "Open in Cursor" (opens Cursor at the repo's folder, and never types into it) · "Finished it another way? Mark it yourself." · "Mark done" · "Mark failed"

**J9

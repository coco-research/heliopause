# Copy pinned by tests/test_today_views.py (from Design's COPY.md v22, "Today and Repos"). Update both together.

## Today and Repos
Only what is wrong speaks: done, merged and passed stay ink; failed and check failed are red; a setup gap is amber.
**Today**
- Eyebrow: "Today · {weekday} {time}" · Title: "{n} jobs done and {n} failed today." The PR clause "{n} PRs merged, {n} open." is added only when GitHub is on and readable (github true, github_unreadable false).
- Sub-line, from the counts block: "{n} message(s) today · {n} tool(s) connected · {n} repo(s)". Singular only at 1 ("1 repo", "2 repos"). Filter: "All repos" · one button per repo · link "Yesterday"
- One block per repo; a repo with a failure comes first, then newest first. Block summary: "{n} failed · {n} done · {n} PR open"
- Job row: time · "● Done" / "▲ Failed" · job summary · "{member} · {tool}" · checks · "Report" (done or failed; "Send again" returns in 0.7.1)
- PR row (GitHub on): "◐ Opened" / "● Merged" · "PR #{n} · {title}" · "{member} · {tool}" or "Merged by you" · CI check
- Checks: "● Tests pass" (with a count when the report has one, "· 6"), "▲ Tests fail · 2 of 14", "● Reviewer: approve", "○ No review", "● CI passed", "◐ CI running", "▲ CI failed", "○ No CI workflow"
- GitHub off, in place of PR and CI rows: "○ PRs and CI are off. To see them here, turn GitHub on in ~/atlas-lab/atlas.json. Heliopause then asks GitHub through your own gh. The README shows how."
- GitHub on but unreadable: once, under the filters, the amber line "◇ GitHub couldn't be read, so PRs and CI aren't shown. If gh isn't signed in, run gh auth login in a terminal on this computer. This fills in on the next refresh." No per-repo grey line. PRs and CI aren't shown.
- J21 empty: title "Nothing done yet today." · sub is the counts line · "When a job reports done or failed, it shows here, by repo." · "Builders report with cd ~/Heliopause && python3 -B -m atlas report <id> done|failed --lab ~/atlas-lab. Jobs start in Messages." · link "Open Messages"
**Repos**
- Title, from the cards' jobs today, in card order. F = repos with failed > 0, Q = the other repos. Singular only at 1.
  - F non-empty: "{n} job(s) failed today, in {F joined with ', ' and ' and '}." If Q is one: " {name} is quiet." If Q is two: " {a} and {b} are quiet." If Q is more: " {k} repos are quiet."
  - else if any done: "{n} job(s) done today. Nothing failed."
  - else if any job sent or picked up today: "Nothing done yet today."
  - else: "No jobs today."
  - Empty list stays "No repos yet."
- Sub-line: the same counts line as Today ("1 repo", "2 repos").
- Empty, when the repo list is empty: title "No repos yet." · "Heliopause shows the repos you ticked in setup."
- Card per repo, three groups:
  - "Jobs today": "○ {n} sent · ◐ {n} picked up · ● {n} done · ▲ {n} failed"
  - "PRs and CI" (GitHub on and readable): "PR #{n} {title}" with its CI check · "main · Last CI run {time}" · "Merged today: PR #42 · Ship auth, PR #43 · Ship logs" or "None". GitHub off: the grey line under that heading. GitHub unreadable: the group is omitted, never an empty heading.
  - "Repo checks": README · LICENSE · CI workflow · Branch protection, each "● Check passed", "▲ Check failed" with the reason, or "○ Not checked". Fix link: "Copy fix as a job" (CI workflow). No badges; never "secure" or "compliant" (G6).
  - Reasons: CI workflow "No workflow in .github/workflows." · Branch protection (K-b wording) "Anyone with write access can push to main without a pull request." · passed "main needs a pull request and 1 passing check." · not checked "Needs GitHub on." / "Needs GitHub signed in."
- GitHub off: the same grey line as Today, under the "PRs and CI" heading. GitHub on but unreadable, amber, once above the cards, shape ◇: "◇ GitHub couldn't be read, so PRs and CI aren't shown. If gh isn't signed in, run gh auth login in a terminal on this computer. This fills in on the next refresh."
- Rail privacy line: K1's GitHub-on line, "Heliopause sends only PR and CI lookups to GitHub.", whenever the GitHub switch is on (signed in or not). Off: the short K1 line.

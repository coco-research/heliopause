"""Today and Repos views: copy, quiet states, and textContent."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TODAY = ROOT / "atlas" / "static" / "views" / "today.js"
REPOS = ROOT / "atlas" / "static" / "views" / "repos.js"
TODAY_CSS = ROOT / "atlas" / "static" / "views" / "today.css"
REPOS_CSS = ROOT / "atlas" / "static" / "views" / "repos.css"
COPY = ROOT / "tests" / "fixtures" / "copy" / "today.md"
MAIN = ROOT / "atlas" / "__main__.py"

_NODE = shutil.which("node") is None

_HARNESS = r"""
const assert = require("assert");
global.window = global;
window.HP = {
  toolName: function (id) {
    var names = { "claude-code": "Claude Code", cursor: "Cursor", codex: "Codex", grok: "Grok", you: "You" };
    if (id == null || id === "") return "";
    return Object.prototype.hasOwnProperty.call(names, id) ? names[id] : String(id);
  },
  age: function () { return "20 min"; },
  setPrivacy: function (github) { privacy.push(!!github); }
};
let privacy = [];
let created = 0;
let innerHTMLSets = 0;
function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    className: "",
    children: [],
    attrs: {},
    listeners: {},
    isConnected: true,
    _text: null
  };
  el.appendChild = function (child) {
    child.parent = el;
    el.children.push(child);
    el._text = null;
    return child;
  };
  el.removeChild = function (child) {
    const i = el.children.indexOf(child);
    if (i >= 0) el.children.splice(i, 1);
    return child;
  };
  el.setAttribute = function (k, v) { el.attrs[k] = String(v); };
  el.addEventListener = function (type, fn) {
    (el.listeners[type] || (el.listeners[type] = [])).push(fn);
  };
  el.contains = function (node) {
    if (node === el) return true;
    return el.children.some(function (c) { return c.contains && c.contains(node); });
  };
  Object.defineProperty(el, "firstChild", { get: function () { return el.children[0] || null; } });
  Object.defineProperty(el, "textContent", {
    get: function () {
      if (el.children.length) return el.children.map(function (c) { return c.textContent; }).join("");
      return el._text == null ? "" : el._text;
    },
    set: function (v) { el._text = String(v); el.children = []; }
  });
  Object.defineProperty(el, "innerHTML", {
    set: function () { innerHTMLSets += 1; },
    get: function () { return ""; }
  });
  return el;
}
const head = makeEl("head");
global.document = {
  hidden: false,
  activeElement: null,
  head: head,
  createElement: function (tag) { created += 1; return makeEl(tag); },
  createTextNode: function (s) {
    const n = makeEl("#text");
    n.textContent = s;
    return n;
  },
  addEventListener: function () {},
  querySelector: function () { return null; }
};
let tick = null;
let intervalMs = 0;
global.setInterval = function (fn, ms) { tick = fn; intervalMs = ms; return 1; };
global.clearInterval = function () {};
const today = require(process.argv[1]);
const repos = require(process.argv[2]);

function walk(node, acc) {
  acc.push(node);
  const kids = node.children || [];
  for (let i = 0; i < kids.length; i++) walk(kids[i], acc);
  return acc;
}
function hasClass(node, name) {
  return (" " + (node.className || "") + " ").indexOf(" " + name + " ") >= 0;
}
function find(root, pred) {
  const all = walk(root, []);
  for (let i = 0; i < all.length; i++) if (pred(all[i])) return all[i];
  return null;
}
function count(hay, needle) {
  let n = 0;
  let i = 0;
  while (hay && (i = hay.indexOf(needle, i)) >= 0) { n += 1; i += needle.length; }
  return n;
}
function click(node) {
  if (!node || !node.listeners.click) throw new Error("no click on " + (node && node.textContent));
  node.listeners.click[0]();
}

const OFF = "PRs and CI are off. To see them here, turn GitHub on in ~/atlas-lab/atlas.json. Heliopause then asks GitHub through your own gh. The README shows how.";
const OUT = "◇ GitHub couldn't be read, so PRs and CI aren't shown. If gh isn't signed in, run gh auth login in a terminal on this computer. This fills in on the next refresh.";
let gen = "2026-10-21T15:40:00";
let scene = "day";

function job(state, summary, extra) {
  const row = {
    kind: "job", ts: "2026-10-21T15:40:00", state: state, summary: summary,
    member: "Builder", tool: "cursor", checks: null, id: summary, thread: "t-1", pr: null, by_you: false
  };
  if (extra) Object.keys(extra).forEach(function (k) { row[k] = extra[k]; });
  return row;
}
function pr(n, state, summary, extra) {
  const row = {
    kind: "pr", ts: "2026-10-21T13:00:00", state: state, summary: summary,
    member: "Builder", tool: "cursor", checks: { ci: "pass" }, id: "pr-" + n, thread: null, pr: n, by_you: false
  };
  if (extra) Object.keys(extra).forEach(function (k) { row[k] = extra[k]; });
  return row;
}
function todayBody() {
  if (scene === "empty") {
    return {
      generated: gen, github: false, tools_connected: 9,
      counts: { messages_today: 0, tools_connected: 4, repos: 2 },
      repos: [
        { repo: "web-app", failed: false, rows: [] },
        { repo: "api-server", failed: false, rows: [] }
      ]
    };
  }
  return {
    generated: gen,
    github: scene !== "off",
    github_unreadable: scene === "unread",
    counts: { messages_today: 5, tools_connected: 1, repos: 1 },
    repos: [
      {
        repo: "web-app", failed: true,
        rows: [
          job("done", "<b>boom</b>", { checks: { tests: "pass", passed: 6, review: "pass", ci: "pass" } }),
          job("failed", "Broke the build", {
            member: "Planner", tool: "claude-code", thread: "t-2",
            checks: { tests: "fail", failed: 2, of: 14, review: "none" }
          }),
          pr(18, "opened", "Add nav", { checks: { ci: "pending" } })
        ]
      },
      {
        repo: "api-server", failed: false,
        rows: [
          job("done", "INDEX-UNIQUE", { id: "a1" }),
          job("done", "Second done", { id: "a2", thread: "t-3" }),
          job("done", "Third done", { id: "a3", thread: "t-4", checks: { ci: "none" } }),
          pr(42, "merged", "Ship auth", { by_you: true, member: "Nope" }),
          pr(43, "merged", "Ship logs", { member: "Builder", tool: "grok" }),
          pr(44, "opened", "Open health")
        ]
      }
    ]
  };
}
function card(repo, jobs, prs, checks, last) {
  return {
    repo: repo,
    jobs_today: jobs,
    prs: prs || [],
    checks: checks || [],
    last_ci: last || null
  };
}
const jobsA = { sent: 2, picked_up: 1, done: 3, failed: 0 };
const jobsB = { sent: 0, picked_up: 0, done: 1, failed: 2 };
const openPrs = [
  { number: 18, title: "Add nav", state: "open", ci: "pending" },
  { number: 19, title: "Docs tweak", state: "open", ci: "none" },
  { number: 42, title: "Ship auth", state: "merged", ci: "pass" },
  { number: 43, title: "Ship logs", state: "merged", ci: "pass" }
];
function reposBody() {
  const counts = { messages_today: 8, tools_connected: 2, repos: 6 };
  const base = [
    card("web-app", jobsA, openPrs, [], "2026-10-21T15:00:00"),
    card("api-server", jobsB, [], [])
  ];
  if (scene === "repos-empty") {
    return {
      generated: gen, github: false, github_unreadable: false,
      counts: { messages_today: 0, tools_connected: 1, repos: 0 },
      repos: []
    };
  }
  if (scene === "repos-off") {
    base[0].checks = [{ name: "LICENSE", state: "none" }];
    return { generated: gen, github: false, github_unreadable: false, counts: counts, repos: base };
  }
  if (scene === "repos-out") {
    return {
      generated: gen, github: true, gh_signed_out: false, github_unreadable: true, counts: counts,
      repos: [card("web-app", jobsA, openPrs, [{ name: "LICENSE", state: "none" }])]
    };
  }
  if (scene === "repos-checks") {
    return {
      generated: gen, github: true, github_unreadable: false, counts: counts,
      repos: [
        card("web-app", jobsA, [], [
          { name: "CI workflow", state: "fail" },
          { name: "Branch protection", state: "fail" }
        ]),
        card("api-server", jobsB, [], [
          { name: "README", state: "pass" },
          { name: "LICENSE", state: "pass" },
          { name: "CI workflow", state: "pass" },
          { name: "Branch protection", state: "pass" }
        ]),
        card("docs", { sent: 0, picked_up: 0, done: 0, failed: 0 }, [], [
          { name: "README", state: "pass" },
          { name: "LICENSE", state: "pass" },
          { name: "CI workflow", state: "pass" },
          { name: "Branch protection", state: "none" }
        ])
      ]
    };
  }
  if (scene === "repos-none") {
    return {
      generated: gen, github: true, github_unreadable: false, counts: counts,
      repos: [card("docs", { sent: 0, picked_up: 0, done: 0, failed: 0 }, [], [])]
    };
  }
  if (scene === "repos-sent") {
    return {
      generated: gen, github: true, github_unreadable: false, counts: counts,
      repos: [card("docs", { sent: 2, picked_up: 1, done: 0, failed: 0 }, [], [])]
    };
  }
  if (scene === "repos-one") {
    return {
      generated: gen, github: true, github_unreadable: false, counts: counts,
      repos: [card("web-app", { sent: 0, picked_up: 0, done: 4, failed: 1 }, [], [])]
    };
  }
  if (scene === "repos-done") {
    return {
      generated: gen, github: true, github_unreadable: false, counts: counts,
      repos: [card("web-app", { sent: 0, picked_up: 0, done: 1, failed: 0 }, [], [])]
    };
  }
  if (scene === "repos-crowd") {
    return {
      generated: gen, github: true, github_unreadable: false, counts: counts,
      repos: [
        card("web-app", { sent: 0, picked_up: 0, done: 0, failed: 1 }, [], []),
        card("api-server", { sent: 0, picked_up: 0, done: 0, failed: 2 }, [], []),
        card("docs", { sent: 0, picked_up: 0, done: 0, failed: 1 }, [], []),
        card("mobile", { sent: 0, picked_up: 0, done: 0, failed: 0 }, [], []),
        card("cli", { sent: 0, picked_up: 0, done: 0, failed: 0 }, [], []),
        card("site", { sent: 0, picked_up: 0, done: 0, failed: 0 }, [], [])
      ]
    };
  }
  return { generated: gen, github: true, github_unreadable: false, counts: counts, repos: base };
}

let lastToday = Promise.resolve();
let todayHits = 0;
const todayRoot = makeEl("div");
const todayCtx = {
  api: function (path) {
    assert.strictEqual(path, "/api/today");
    return Promise.resolve(todayBody());
  },
  refresh: function () {
    todayHits += 1;
    lastToday = today.render(todayRoot, todayCtx);
    return lastToday;
  }
};
let lastRepos = Promise.resolve();
const reposRoot = makeEl("div");
const reposCtx = {
  api: function (path) {
    assert.strictEqual(path, "/api/repos");
    return Promise.resolve(reposBody());
  },
  refresh: function () {
    lastRepos = repos.render(reposRoot, reposCtx);
    return lastRepos;
  }
};

function h3s(root) {
  return walk(root, []).filter(function (n) { return n.tagName === "H3"; }).map(function (n) { return n.textContent; });
}
function states(root, text) {
  return walk(root, []).filter(function (n) {
    return hasClass(n, "st") && n.textContent === text;
  }).map(function (n) { return n.className; });
}

(async function () {
  const out = {};
  out.todayTitle = window.HP.views.today.title;
  out.reposTitle = window.HP.views.repos.title;
  out.sameRender = window.HP.views.today.render === today.render && window.HP.views.repos.render === repos.render;

  scene = "day";
  await today.render(todayRoot, todayCtx);
  out.day = todayRoot.textContent;
  out.doneClass = states(todayRoot, "● Done");
  out.failedClass = states(todayRoot, "▲ Failed");
  out.openedClass = states(todayRoot, "◐ Opened");
  out.mergedClass = states(todayRoot, "● Merged");
  out.rowFail = hasClass(find(todayRoot, function (n) {
    return hasClass(n, "td-row") && n.textContent.indexOf("Broke the build") >= 0;
  }), "fail");
  const literal = find(todayRoot, function (n) { return n.tagName === "B" && n.textContent.indexOf("<b>") >= 0; });
  out.literal = literal.textContent;
  out.literalChildren = literal.children.length;
  const report = find(todayRoot, function (n) { return n.tagName === "A" && n.textContent === "Report"; });
  out.report = report.attrs.href;
  out.reportClass = report.className;
  const failRow = find(todayRoot, function (n) {
    return hasClass(n, "td-row") && n.textContent.indexOf("Broke the build") >= 0;
  });
  const failReport = find(failRow, function (n) { return n.tagName === "A" && n.textContent === "Report"; });
  out.failReportTag = failReport.tagName;
  out.failReport = failReport.attrs.href;
  out.failReportClass = failReport.className;
  out.sendAgain = walk(todayRoot, []).filter(function (n) {
    return n.tagName === "A" && n.textContent === "Send again";
  }).length;
  out.intervalMs = intervalMs;

  const made = created;
  gen = "2026-10-21T16:40:00";
  await todayCtx.refresh();
  out.skippedSameBody = created === made;

  const hits = todayHits;
  document.hidden = true;
  tick();
  out.pollSkippedHidden = todayHits === hits;
  document.hidden = false;
  const sel = document.createElement("select");
  todayRoot.appendChild(sel);
  document.activeElement = sel;
  tick();
  out.pollSkippedFocus = todayHits === hits;
  document.activeElement = null;
  todayRoot.removeChild(sel);
  tick();
  await lastToday;
  out.pollRan = todayHits === hits + 1;

  click(find(todayRoot, function (n) { return n.tagName === "BUTTON" && n.textContent === "web-app"; }));
  await lastToday;
  out.filtered = todayRoot.textContent;
  click(find(todayRoot, function (n) { return n.tagName === "BUTTON" && n.textContent === "All repos"; }));
  await lastToday;

  scene = "off";
  await todayCtx.refresh();
  out.off = todayRoot.textContent;

  scene = "unread";
  await todayCtx.refresh();
  out.unread = todayRoot.textContent;
  out.unreadCount = count(out.unread, OUT);
  out.unreadOff = count(out.unread, OFF);
  const todayAmber = find(todayRoot, function (n) { return hasClass(n, "td-note") && hasClass(n, "wait"); });
  out.todayAmber = todayAmber.className;
  out.todayAmberInRepo = hasClass(todayAmber.parent, "td-repo");
  out.unreadAmberAfterFilters = out.unread.indexOf(OUT) > out.unread.indexOf("Yesterday");

  scene = "empty";
  await todayCtx.refresh();
  out.empty = todayRoot.textContent;
  const openMsg = find(todayRoot, function (n) { return n.tagName === "A" && n.textContent === "Open Messages"; });
  out.emptyHref = openMsg.attrs.href;

  scene = "repos-on";
  await repos.render(reposRoot, reposCtx);
  out.repos = reposRoot.textContent;
  out.reposDone = states(reposRoot, "● 3 done");
  out.reposFailed0 = states(reposRoot, "▲ 0 failed");
  out.reposFailed2 = states(reposRoot, "▲ 2 failed");
  const reposMade = created;
  gen = "2026-10-21T17:40:00";
  await reposCtx.refresh();
  out.reposSkipped = created === reposMade;

  scene = "repos-off";
  await reposCtx.refresh();
  out.reposOff = reposRoot.textContent;
  out.offH3 = h3s(reposRoot);

  scene = "repos-out";
  await reposCtx.refresh();
  out.reposOut = reposRoot.textContent;
  out.outH3 = h3s(reposRoot);
  const amber = find(reposRoot, function (n) { return hasClass(n, "td-note") && hasClass(n, "wait"); });
  out.amberClass = amber.className;

  scene = "repos-checks";
  await reposCtx.refresh();
  out.checks = reposRoot.textContent;
  const fix = find(reposRoot, function (n) { return n.tagName === "A" && n.textContent === "Copy fix as a job"; });
  const how = find(reposRoot, function (n) { return n.tagName === "A" && n.textContent === "Show how"; });
  out.fixHref = fix.attrs.href;
  out.howHref = how ? how.attrs.href : null;

  scene = "repos-none";
  await reposCtx.refresh();
  out.reposNone = reposRoot.textContent;

  scene = "repos-sent";
  await reposCtx.refresh();
  out.reposSent = reposRoot.textContent;

  scene = "repos-one";
  await reposCtx.refresh();
  out.reposOne = reposRoot.textContent;

  scene = "repos-done";
  await reposCtx.refresh();
  out.reposDoneOne = reposRoot.textContent;

  scene = "repos-crowd";
  await reposCtx.refresh();
  out.reposCrowd = reposRoot.textContent;

  scene = "repos-empty";
  await reposCtx.refresh();
  out.reposEmpty = reposRoot.textContent;
  out.innerHTML = innerHTMLSets;
  out.privacy = privacy;
  out.offCount = count(out.off, OFF);
  out.reposOffCount = count(out.reposOff, OFF);
  out.outCount = count(out.reposOut, OUT);
  process.stdout.write(JSON.stringify(out));
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_views_register_in_source() -> None:
    today = _read(TODAY)
    repos = _read(REPOS)
    assert "window.HP.views.today" in today
    assert 'title: "Today"' in today
    assert "/api/today" in today
    assert "window.HP.views.repos" in repos
    assert 'title: "Repos"' in repos
    assert "/api/repos" in repos
    assert "5000" in today and "5000" in repos
    assert "document.hidden" in today and "document.hidden" in repos
    assert 'done: ["●", "Done", "q"]' in today
    assert '"green"' not in today


def test_check_words_match_copy() -> None:
    copy = _read(COPY)
    both = _read(TODAY) + "\n" + _read(REPOS)
    for phrase in (
        "● Tests pass",
        "▲ Tests fail",
        "● Reviewer: approve",
        "○ No review",
        "● CI passed",
        "◐ CI running",
        "▲ CI failed",
        "○ No CI workflow",
        "● Check passed",
        "▲ Check failed",
        "○ Not checked",
        "Nothing done yet today.",
        "When a job reports done or failed, it shows here, by repo.",
        "cd ~/Heliopause && python3 -B -m atlas report <id> done|failed --lab ~/atlas-lab",
        "Jobs start in Messages.",
        "Open Messages",
        "Copy fix as a job",
        "No workflow in .github/workflows.",
        "Anyone with write access can push to main without a pull request.",
        "main needs a pull request and 1 passing check.",
        "Needs GitHub on.",
        "Needs GitHub signed in.",
        "◇ GitHub couldn't be read, so PRs and CI aren't shown. If gh isn't signed in, run gh auth login in a terminal on this computer. This fills in on the next refresh.",
        "No repos yet.",
        "Heliopause shows the repos you ticked in setup.",
        "PRs and CI are off. To see them here, turn GitHub on in ",
        ". Heliopause then asks GitHub through your own ",
        ". The README shows how.",
        "PRs and CI are off.",
        "Jobs today",
        "PRs and CI",
        "Repo checks",
        "Merged by you",
        "All repos",
        "Yesterday",
        "No jobs today.",
        "Nothing failed.",
        " failed today, in ",
        " is quiet.",
        " are quiet.",
        " repos are quiet.",
    ):
        assert phrase in copy, phrase
        assert phrase in both, phrase
    assert "signed out" not in copy.lower()
    assert "signed out" not in both.lower()


def test_message_text_is_text_content() -> None:
    for path in (TODAY, REPOS):
        src = _read(path)
        assert "textContent" in src
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            assert banned not in src, path.name
        assert "Atlas" not in src


def test_only_wrong_is_colored_and_layout_holds() -> None:
    today = _read(TODAY_CSS)
    repos = _read(REPOS_CSS)
    for css in (today, repos):
        assert ".st.q" in css and "var(--ink-2)" in css and "var(--red)" in css
        assert ".st.done" not in css and ".st.prog" not in css
    assert "1100px" in today
    narrow = today[today.index("@media (max-width: 1100px)"):today.index("@media (max-width: 700px)")]
    assert "white-space: nowrap" in narrow
    assert ".td-rh h2" in narrow
    assert "text-overflow: ellipsis" in narrow
    assert ".td-rh .sum" in narrow
    repos_narrow = repos[repos.index("@media (max-width: 1100px)"):repos.index("@media (max-width: 700px)")]
    assert "white-space: nowrap" in repos_narrow
    assert ".rp-h h2" in repos_narrow
    assert "text-overflow: ellipsis" in repos_narrow
    assert 'grid-template-areas: "t st what act" ". . chk chk"' in today
    assert "minmax(0, 1fr)" in today
    assert "repeat(2, minmax(0, 1fr))" in repos
    assert "@media (max-width: 700px)" in repos
    assert "var(--amber)" in repos
    assert ".td-note.wait .m" in repos


def test_allowlist_lists_the_four_next_to_messages() -> None:
    src = _read(MAIN)
    window = src[src.index('"/views/messages.css"'):src.index('"/views/messages.css"') + 700]
    for path in ('"/views/today.js"', '"/views/today.css"', '"/views/repos.js"', '"/views/repos.css"'):
        assert path in window


@pytest.mark.skipif(_NODE, reason="node missing")
def test_js_parses() -> None:
    for path in (TODAY, REPOS):
        proc = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(_NODE, reason="node missing")
def test_today_and_repos_render() -> None:
    proc = subprocess.run(
        ["node", "-e", _HARNESS, str(TODAY), str(REPOS)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    assert out["todayTitle"] == "Today"
    assert out["reposTitle"] == "Repos"
    assert out["sameRender"] is True
    assert out["doneClass"] == ["st q", "st q", "st q", "st q"]
    assert out["failedClass"] == ["st fail"]
    assert out["openedClass"] == ["st q", "st q"]
    assert out["mergedClass"] == ["st q", "st q"]
    assert "done" not in out["doneClass"][0].split()
    assert "green" not in out["doneClass"][0].split()
    assert out["rowFail"] is True
    assert out["literal"] == "<b>boom</b>"
    assert out["literalChildren"] == 0
    assert out["innerHTML"] == 0
    assert out["report"] == "#/messages?thread=t-1"
    assert out["failReportTag"] == "A"
    assert out["failReport"] == "#/messages?thread=t-2"
    assert out["failReportClass"] == out["reportClass"] == "link"
    assert "btn" not in out["failReportClass"].split()
    assert out["sendAgain"] == 0
    day = out["day"]
    assert "4 jobs done and 1 failed today. 2 PRs merged, 2 open." in day
    assert "5 messages today · 1 tool connected · 1 repo" in day
    assert "1 failed · 1 done · 1 PR open" in day
    assert "3 done · 2 PRs merged · 1 PR open" in day
    assert "● Tests pass · 6" in day
    assert "▲ Tests fail · 2 of 14" in day
    assert "● Reviewer: approve" in day
    assert "○ No review" in day
    assert "● CI passed" in day
    assert "◐ CI running" in day
    assert "○ No CI workflow" in day
    assert "Builder · Cursor" in day
    assert "Planner · Claude Code" in day
    assert "Merged by you" in day
    assert "Nope" not in day
    assert "PR #18 · Add nav" in day
    assert "All repos" in day and "Yesterday" in day
    assert out["skippedSameBody"] is True
    assert out["pollSkippedHidden"] is True
    assert out["pollSkippedFocus"] is True
    assert out["pollRan"] is True
    assert out["intervalMs"] == 5000
    assert "INDEX-UNIQUE" not in out["filtered"]
    assert "4 jobs done and 1 failed today. 2 PRs merged, 2 open." in out["filtered"]
    assert "<b>boom</b>" in out["filtered"]
    off = out["off"]
    assert out["offCount"] == 2
    assert "PR #" not in off
    assert "● CI passed" not in off
    assert "◐ CI running" not in off
    assert "● Tests pass · 6" in off
    assert "4 jobs done and 1 failed today." in off
    assert "2 PRs merged" not in off
    assert "○ No CI workflow" not in off
    unread = out["unread"]
    assert "4 jobs done and 1 failed today." in unread
    assert "PRs merged" not in unread
    assert "PR #" not in unread
    # A job's CI chip is the builder's own report (local), so it stays when GitHub can't be read.
    assert "● CI passed" in unread
    assert "○ No CI workflow" in unread
    assert "● Tests pass · 6" in unread
    assert out["unreadCount"] == 1
    assert out["unreadOff"] == 0
    assert out["todayAmber"] == "td-note wait"
    assert out["todayAmberInRepo"] is False
    assert out["unreadAmberAfterFilters"] is True
    empty = out["empty"]
    assert "Nothing done yet today." in empty
    assert "0 messages today · 4 tools connected · 2 repos" in empty
    assert "9 tools" not in empty
    assert "When a job reports done or failed, it shows here, by repo." in empty
    assert "Builders report with cd ~/Heliopause && python3 -B -m atlas report <id> done|failed --lab ~/atlas-lab. Jobs start in Messages." in empty
    assert out["emptyHref"] == "#/messages"
    assert "All repos" not in empty
    assert "PRs and CI are off." not in empty
    repos = out["repos"]
    assert "2 jobs failed today, in api-server. web-app is quiet." in repos
    assert "Repo checks" not in repos
    assert "passes all" not in repos
    assert "8 messages today · 2 tools connected · 6 repos" in repos
    assert "2 repos · 4 jobs done today · 2 failed" not in repos
    assert "○ 2 sent" in repos and "◐ 1 picked up" in repos and "● 3 done" in repos
    assert "▲ 0 failed" in repos and "▲ 2 failed" in repos
    assert out["reposDone"] == ["st q"]
    assert out["reposFailed0"] == ["st q"]
    assert out["reposFailed2"] == ["st fail"]
    assert "PR #18" in repos and "Add nav" in repos and "◐ CI running" in repos
    assert "○ No CI workflow" in repos
    assert "Merged today" in repos and "PR #42 · Ship auth, PR #43 · Ship logs" in repos
    assert "Last CI run 20 min" in repos
    assert "None" in repos
    assert out["reposSkipped"] is True
    assert out["reposOffCount"] == 2
    assert out["offH3"].count("PRs and CI") == 2
    assert "PR #" not in out["reposOff"]
    assert "Needs GitHub on." in out["reposOff"]
    assert "○ Not checked" in out["reposOff"]
    assert out["outCount"] == 1
    assert out["amberClass"] == "td-note wait"
    assert "PRs and CI" not in out["outH3"]
    assert "3 jobs done today. Nothing failed." in out["reposOut"]
    assert "PR #" not in out["reposOut"]
    assert "Add nav" not in out["reposOut"]
    assert "Needs GitHub signed in." in out["reposOut"]
    assert "○ Not checked" in out["reposOut"]
    checks = out["checks"]
    assert "2 jobs failed today, in api-server. web-app and docs are quiet." in checks
    assert "● Check passed" in checks
    assert "▲ Check failed" in checks
    assert "No workflow in .github/workflows." in checks
    assert "Anyone with write access can push to main without a pull request." in checks
    assert "main needs a pull request and 1 passing check." in checks
    assert out["fixHref"] == "#/messages"
    assert out["howHref"] is None
    assert "No jobs today." in out["reposNone"]
    assert "failed today" not in out["reposNone"]
    assert "Nothing failed" not in out["reposNone"]
    assert "Nothing done yet today." in out["reposSent"]
    assert "No jobs today." not in out["reposSent"]
    assert "1 job failed today, in web-app." in out["reposOne"]
    assert "quiet" not in out["reposOne"]
    assert "1 job done today. Nothing failed." in out["reposDoneOne"]
    assert "4 jobs failed today, in web-app, api-server and docs. 3 repos are quiet." in out["reposCrowd"]
    empty_repos = out["reposEmpty"]
    assert "No repos yet." in empty_repos
    assert "Heliopause shows the repos you ticked in setup." in empty_repos
    assert "0 messages today · 1 tool connected · 0 repos" in empty_repos
    assert "Repo checks" not in empty_repos
    assert "secure" not in checks and "compliant" not in checks
    assert out["privacy"][0] is True
    assert False in out["privacy"]


# Same DOM stub as _HARNESS. clearInterval records ids so a leave can prove the 5s poll (id 1) was cleared.
_LEAVE_JS = r"""
const view = require(process.argv[1]);
const which = process.argv[2];
const payloads = {
  today: {
    generated: "2026-10-21T15:40:00", github: false, tools_connected: 9,
    counts: { messages_today: 0, tools_connected: 4, repos: 2 },
    repos: [
      { repo: "web-app", failed: false, rows: [] },
      { repo: "api-server", failed: false, rows: [] }
    ]
  },
  repos: {
    generated: "2026-10-21T15:40:00", github: false, github_unreadable: false,
    counts: { messages_today: 0, tools_connected: 1, repos: 0 },
    repos: []
  }
};

let resolveHold;
const held = new Promise(function (resolve) { resolveHold = resolve; });
let refreshCount = 0;
const root = makeEl("div");
const ctx = {
  api: function () { return held; },
  refresh: function () { refreshCount += 1; }
};

(async function () {
  const pending = view.render(root, ctx);
  assert.strictEqual(typeof root._hpStop, "function");
  root._hpStop();
  while (root.firstChild) root.removeChild(root.firstChild);
  const marker = makeEl("div");
  marker.textContent = "next-view";
  root.appendChild(marker);
  resolveHold(payloads[which]);
  await pending;
  assert.strictEqual(root.children.length, 1);
  assert.strictEqual(root.children[0], marker);
  assert.deepStrictEqual(cleared, [1]);
  const refreshes = refreshCount;
  tick();
  assert.strictEqual(refreshCount, refreshes);
  assert.strictEqual(root.children.length, 1);
  assert.strictEqual(root.children[0], marker);
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
"""


def _leave_script() -> str:
    head, sep, _rest = _HARNESS.partition("global.clearInterval = function () {};\n")
    assert sep, "harness clearInterval line moved"
    recording = "const cleared = [];\nglobal.clearInterval = function (id) { cleared.push(id); };\n"
    return head + recording + _LEAVE_JS


def _run_leave(path: Path, which: str) -> None:
    proc = subprocess.run(
        ["node", "-e", _leave_script(), str(path), which],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


@pytest.mark.skipif(_NODE, reason="node missing")
def test_leaving_today_drops_its_late_reply_and_poll() -> None:
    _run_leave(TODAY, "today")


@pytest.mark.skipif(_NODE, reason="node missing")
def test_leaving_repos_drops_its_late_reply_and_poll() -> None:
    _run_leave(REPOS, "repos")

"""0.7 has no Roles screen. User-visible pointers are gone; GET /api/roles stays."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "atlas" / "static"
VIEWS = STATIC / "views"
APP_HTML = STATIC / "app.html"
APP_JS = STATIC / "app.js"
ROLE_BRIEF = ROOT / "atlas" / "role_brief.py"
TODAY = VIEWS / "today.js"
REPOS = VIEWS / "repos.js"
SETUP = VIEWS / "setup.js"
MAIN = ROOT / "atlas" / "__main__.py"

PATTERNS = (
    "Change roles",
    "in Roles",
    ">Roles<",
    'aria-label="Roles"',
    '"Roles"',
    'data-view="roles"',
    "#/roles",
)

HELP_CLAUDE = (
    "Claude Code reads this at the start of each session, so it knows what you're building. "
    "Like anything you type to it, it's sent to Claude Code's own service."
)
HELP_ALL = (
    "The AI tools you've given a role read this at the start of each session, so they know what you're building. "
    "Like anything you type to them, it's sent to each tool's own service."
)
REPOS_EMPTY = "Heliopause shows the repos you ticked in setup."

_NODE = shutil.which("node") is None


def _scoped_text() -> str:
    paths = [APP_HTML, APP_JS, ROLE_BRIEF, *sorted(VIEWS.glob("*.js"))]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _counts() -> dict[str, int]:
    text = _scoped_text()
    return {pat: text.count(pat) for pat in PATTERNS}


def _count_message(counts: dict[str, int]) -> str:
    return " ".join(f"{pat}={counts[pat]}" for pat in PATTERNS)


def test_no_user_visible_roles_pointers() -> None:
    counts = _counts()
    assert sum(counts.values()) == 0, _count_message(counts)


def test_api_roles_route_remains() -> None:
    src = MAIN.read_text(encoding="utf-8")
    assert 'path == "/api/roles"' in src


def test_goal_help_and_repos_empty_drop_roles() -> None:
    from atlas.role_brief import GOAL_HELP_ALL, GOAL_HELP_CLAUDE

    assert GOAL_HELP_CLAUDE == HELP_CLAUDE
    assert GOAL_HELP_ALL == HELP_ALL
    setup = SETUP.read_text(encoding="utf-8")
    brief = ROLE_BRIEF.read_text(encoding="utf-8")
    assert f'var HELP_CLAUDE = "{HELP_CLAUDE}";' in setup
    assert f'var HELP_ALL = "{HELP_ALL}";' in setup
    assert "Change it any time in Roles." not in setup
    assert "Change it any time in Roles." not in brief
    repos = REPOS.read_text(encoding="utf-8")
    assert f'var REPOS_EMPTY = "{REPOS_EMPTY}";' in repos
    assert "Change them in Roles." not in repos


def test_today_and_repos_payloads_carry_lab_dir(tmp_path: Path) -> None:
    from atlas import screens

    lab = tmp_path / "lab"
    lab.mkdir()
    clock = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)
    shown = screens._shown_path(lab)
    assert screens.today(lab, clock, github_on=False)["lab_dir"] == shown
    assert screens.repos(lab, clock, github_on=False)["lab_dir"] == shown


_HASH = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
const root = {
  attrs: {},
  setAttribute: function (k, v) { this.attrs[k] = String(v); },
  removeAttribute: function (k) { delete this.attrs[k]; }
};
const box = {
  Object: Object,
  String: String,
  Promise: Promise,
  Date: Date,
  Math: Math,
  Number: Number,
  isNaN: isNaN,
  localStorage: {
    getItem: function () { return null; },
    setItem: function () {}
  },
  location: { port: "9", protocol: "http:", hash: "" },
  document: {
    documentElement: root,
    body: { appendChild: function () {} },
    getElementById: function () { return null; },
    querySelectorAll: function () { return []; },
    createElement: function () {
      return { setAttribute: function () {}, addEventListener: function () {} };
    },
    addEventListener: function () {}
  },
  matchMedia: function () {
    return { matches: false, addEventListener: function () {}, addListener: function () {} };
  }
};
box.window = box;
box.addEventListener = function () {};
vm.createContext(box);
vm.runInContext(src, box, { filename: "app.js" });
if (typeof box.HP.viewFromHash !== "function") throw new Error("viewFromHash missing");
box.location.hash = "#/roles";
const roles = box.HP.viewFromHash();
box.location.hash = "#/repos";
const repos = box.HP.viewFromHash();
process.stdout.write(JSON.stringify({ roles: roles, repos: repos }));
"""


@pytest.mark.skipif(_NODE, reason="node not installed")
def test_roles_hash_opens_now() -> None:
    proc = subprocess.run(
        ["node", "-e", _HASH, str(APP_JS)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    assert out["roles"] == "now"
    assert out["repos"] == "repos"


_OFF = r"""
const fs = require("fs");
global.window = global;
window.HP = { setPrivacy: function () {} };
let created = 0;
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
  el.addEventListener = function () {};
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
global.setInterval = function () { return 1; };
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
function links(root, label) {
  return walk(root, []).filter(function (n) { return n.tagName === "A" && n.textContent === label; });
}
function sentence(dir) {
  return "PRs and CI are off. To see them here, turn GitHub on in " + dir
    + "/atlas.json. Heliopause then asks GitHub through your own gh. The README shows how.";
}
function job() {
  return {
    kind: "job", ts: "2026-10-21T15:40:00", state: "done", summary: "Shipped",
    member: "Builder", tool: "cursor", checks: null, id: "j1", thread: null, pr: null, by_you: false
  };
}
function todayBody(dir) {
  const body = {
    generated: "2026-10-21T15:40:00",
    github: false,
    counts: { messages_today: 1, tools_connected: 1, repos: 1 },
    repos: [{ repo: "web-app", failed: false, rows: [job()] }]
  };
  if (dir) body.lab_dir = dir;
  return body;
}
function reposBody(dir) {
  const body = {
    generated: "2026-10-21T15:40:00",
    github: false,
    github_unreadable: false,
    counts: { messages_today: 0, tools_connected: 1, repos: 1 },
    repos: [{ repo: "web-app", jobs_today: { sent: 0, picked_up: 0, done: 1, failed: 0 }, prs: [], checks: [] }]
  };
  if (dir) body.lab_dir = dir;
  return body;
}
function note(root) {
  const node = find(root, function (n) { return hasClass(n, "td-note") && !hasClass(n, "wait"); });
  if (!node) throw new Error("no off note :: " + root.textContent.slice(0, 240));
  return node.textContent;
}
async function paint(view, path, body) {
  const root = makeEl("div");
  await view.render(root, { api: function (got) {
    if (got !== path) throw new Error("unexpected " + got);
    return Promise.resolve(body);
  } });
  return root;
}
(async function () {
  const home = sentence("~/atlas-lab");
  const srv = sentence("/srv/lab");
  const todayHome = await paint(today, "/api/today", todayBody("~/atlas-lab"));
  const todaySrv = await paint(today, "/api/today", todayBody("/srv/lab"));
  const reposHome = await paint(repos, "/api/repos", reposBody("~/atlas-lab"));
  const reposSrv = await paint(repos, "/api/repos", reposBody("/srv/lab"));
  const checksBody = {
    generated: "2026-10-21T15:40:00",
    github: true,
    github_unreadable: false,
    counts: { messages_today: 0, tools_connected: 1, repos: 1 },
    repos: [{
      repo: "web-app",
      jobs_today: { sent: 0, picked_up: 0, done: 0, failed: 0 },
      prs: [],
      checks: [
        { name: "Branch protection", state: "fail" },
        { name: "CI workflow", state: "fail" }
      ]
    }]
  };
  const checks = await paint(repos, "/api/repos", checksBody);
  const fix = links(checks, "Copy fix as a job");
  process.stdout.write(JSON.stringify({
    todayHome: note(todayHome),
    todaySrv: note(todaySrv),
    reposHome: note(reposHome),
    reposSrv: note(reposSrv),
    home: home,
    srv: srv,
    showHow: links(checks, "Show how").length,
    copyFix: fix.length,
    copyHref: fix.length ? fix[0].attrs.href : ""
  }));
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
"""


@pytest.mark.skipif(_NODE, reason="node not installed")
def test_github_off_line_uses_lab_dir() -> None:
    proc = subprocess.run(
        ["node", "-e", _OFF, str(TODAY), str(REPOS)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    assert out["todayHome"] == "○ " + out["home"]
    assert out["reposHome"] == "○ " + out["home"]
    assert out["todaySrv"] == "○ " + out["srv"]
    assert out["reposSrv"] == "○ " + out["srv"]
    assert "/srv/lab/atlas.json" in out["srv"]
    assert "~/atlas-lab/atlas.json" in out["home"]
    assert out["showHow"] == 0
    assert out["copyFix"] == 1
    assert out["copyHref"] == "#/messages"


_FINDING = r"""
global.window = global;
const view = require(process.argv[1]);
function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    className: "",
    children: [],
    disabled: false,
    value: "",
    checked: false,
    type: "",
    id: "",
    attrs: {},
    listeners: {},
    isConnected: true,
    _text: null
  };
  el.appendChild = function (child) {
    child.parent = el;
    el.children.push(child);
    return child;
  };
  el.setAttribute = function (k, v) { el.attrs[k] = String(v); };
  el.getAttribute = function (k) { return el.attrs[k]; };
  el.addEventListener = function (type, fn) {
    (el.listeners[type] || (el.listeners[type] = [])).push(fn);
  };
  Object.defineProperty(el, "textContent", {
    get: function () {
      if (el.children.length) return el.children.map(function (c) { return c.textContent; }).join("");
      return el._text == null ? "" : el._text;
    },
    set: function (v) { el._text = String(v); el.children = []; }
  });
  return el;
}
global.document = {
  createElement: function (tag) { return makeEl(tag); },
  createTextNode: function (s) {
    const n = makeEl("#text");
    n.textContent = s;
    return n;
  },
  querySelector: function () { return null; },
  head: makeEl("head")
};
const ONE = {
  tools: [
    { id: "claude-code", name: "Claude Code", found: true, cli: "/bin/claude-code", app: null,
      config_dir: null, install_url: "https://example.test/claude-code" },
    { id: "cursor", name: "Cursor", found: false, cli: null, app: null, config_dir: null,
      install_url: "https://example.test/cursor" }
  ],
  supported: ["claude-code", "cursor", "codex", "grok"],
  suggested: { hq: "you", lab: "claude-code", factory: ["claude-code"] }
};
function walk(node, acc) {
  acc.push(node);
  const kids = node.children || [];
  for (let i = 0; i < kids.length; i++) walk(kids[i], acc);
  return acc;
}
function find(root, pred) {
  const all = walk(root, []);
  for (let i = 0; i < all.length; i++) if (pred(all[i])) return all[i];
  return null;
}
function button(root, label) {
  const node = find(root, function (el) { return el.tagName === "BUTTON" && el.textContent === label; });
  if (!node) throw new Error("no button " + label + " :: " + root.textContent.slice(0, 240));
  return node;
}
function click(node) {
  return Promise.resolve(node.listeners.click[0]());
}
function api(finding) {
  return function (path) {
    if (path === "/api/setup/tools") return Promise.resolve(ONE);
    if (path === "/api/roles") return Promise.resolve({});
    if (path === "/api/setup/check") return Promise.resolve({ ok: true, ms: 1, member: "Builder" });
    if (path === "/api/first-finding") return Promise.resolve(finding);
    return Promise.reject(new Error("unexpected " + path));
  };
}
async function reach(finding) {
  const root = makeEl("div");
  view.render(root, { api: api(finding) });
  await click(button(root, "Find my tools"));
  await click(button(root, "Next: pick roles"));
  // Step 3's button reads "Next" once the 0.7.1 label fix (C1) lands, and "Next: connect" before it.
  const next = find(root, function (el) { return el.tagName === "BUTTON" && /^Next(: connect)?$/.test(el.textContent); });
  if (!next) throw new Error("no step 3 Next button :: " + root.textContent.slice(0, 240));
  await click(next);
  return root;
}
function found(root) {
  return find(root, function (el) { return el.className === "found-link"; });
}
function flag(root) {
  const node = find(root, function (el) { return el.tagName === "P" && el.className === "flag"; });
  if (!node) throw new Error("no flag :: " + root.textContent.slice(0, 240));
  return node.textContent;
}
(async function () {
  const roleLine = "A seat is empty.";
  const hookLine = "Hooks still live in the lab.";
  const repoLine = "One repo has no standard.";
  const role = await reach({ finding: { kind: "role", line: roleLine, link: "Show the change" }, none_line: "none" });
  const hook = await reach({ finding: { kind: "hook", line: hookLine, link: "Open Hooks & checks" }, none_line: "none" });
  const repo = await reach({ finding: { kind: "repo", line: repoLine, link: "Open Repos" }, none_line: "none" });
  const repoLink = found(repo);
  if (!repoLink) throw new Error("repos link missing");
  process.stdout.write(JSON.stringify({
    roleFlag: flag(role),
    roleLink: !!found(role),
    hookFlag: flag(hook),
    hookLink: !!found(hook),
    repoFlag: flag(repo),
    repoText: repoLink.textContent,
    repoHref: repoLink.attrs.href || ""
  }));
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
"""


@pytest.mark.skipif(_NODE, reason="node not installed")
def test_finding_links_only_when_the_screen_exists() -> None:
    proc = subprocess.run(
        ["node", "-e", _FINDING, str(SETUP)],
        capture_output=True, text=True, timeout=20,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    assert out["roleFlag"] == "◇ A seat is empty."
    assert out["roleLink"] is False
    assert out["hookFlag"] == "◇ Hooks still live in the lab."
    assert out["hookLink"] is False
    assert out["repoFlag"] == "◇ One repo has no standard."
    assert out["repoText"] == "Open Repos"
    assert out["repoHref"] == "#/repos"

"""Commands and paths that sit in a sentence are span.mono. The line's words stay the same."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "atlas" / "static" / "views" / "setup.js"
TODAY = ROOT / "atlas" / "static" / "views" / "today.js"
REPOS = ROOT / "atlas" / "static" / "views" / "repos.js"

HELP = "Run cd ~/Heliopause && python3 -B -m atlas doctor in a terminal, then try again."
CMD = "cd ~/Heliopause && python3 -B -m atlas doctor"
DIRS = ("~/atlas-lab", "/srv/lab")

_NODE = shutil.which("node") is None


def _sentence(directory: str) -> str:
    return (
        "PRs and CI are off. To see them here, turn GitHub on in "
        + directory
        + "/atlas.json. Heliopause then asks GitHub through your own gh. The README shows how."
    )


def _node(script: str, *paths: Path) -> dict:
    proc = subprocess.run(
        ["node", "-e", script, *[str(path) for path in paths]],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout)


_SETUP = r"""
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
    _text: null,
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
      if (el.children.length) return el.children.map((c) => c.textContent).join("");
      return el._text == null ? "" : el._text;
    },
    set: function (v) { el._text = String(v); el.children = []; },
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
  head: makeEl("head"),
};
const ONE = {
  tools: [
    { id: "claude-code", name: "Claude Code", found: true, cli: "/bin/claude-code", app: null,
      config_dir: null, install_url: "https://example.test/claude-code" },
    { id: "cursor", name: "Cursor", found: false, cli: null, app: null, config_dir: null,
      install_url: "https://example.test/cursor" },
  ],
  supported: ["claude-code", "cursor", "codex", "grok"],
  suggested: { hq: "you", lab: "claude-code", factory: ["claude-code"] },
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
  if (!node) throw new Error("no button " + label + " :: " + root.textContent.slice(0, 280));
  return node;
}
function click(node) {
  return Promise.resolve(node.listeners.click[0]());
}
function para(root, className) {
  const node = find(root, function (el) { return el.tagName === "P" && el.className === className; });
  if (!node) throw new Error("no p." + className + " :: " + root.textContent.slice(0, 280));
  return node;
}
function api(check) {
  return function (path) {
    if (path === "/api/setup/tools") return Promise.resolve(ONE);
    if (path === "/api/roles") return Promise.resolve({});
    if (path === "/api/setup/check") return Promise.resolve(check);
    if (path === "/api/first-finding") return Promise.resolve({ finding: null, none_line: "" });
    return Promise.reject(new Error("unexpected " + path));
  };
}
async function reach(check) {
  const root = makeEl("div");
  view.render(root, { api: api(check) });
  await click(button(root, "Find my tools"));
  await click(button(root, "Next: pick roles"));
  // Step 3's button reads "Next" once the 0.7.1 label fix (C1) lands, and "Next: connect" before it.
  const next = find(root, function (el) { return el.tagName === "BUTTON" && /^Next(: connect)?$/.test(el.textContent); });
  if (!next) throw new Error("no step 3 Next button :: " + root.textContent.slice(0, 280));
  await click(next);
  return root;
}
function monoKids(node) {
  return (node.children || []).filter(function (n) {
    return n.tagName === "SPAN" && (" " + (n.className || "") + " ").indexOf(" mono ") >= 0;
  });
}
(async function () {
  const root = await reach({ ok: false, ms: 4, member: "Builder" });
  const help = para(root, "help");
  process.stdout.write(JSON.stringify({
    help: help.textContent,
    mono: monoKids(help).map(function (n) { return n.textContent; })
  }));
})().catch(function (err) {
  console.error(err && err.stack || err);
  process.exit(1);
});
"""


_VIEWS = r"""
global.window = global;
window.HP = { setPrivacy: function () {} };
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
  createElement: function (tag) { return makeEl(tag); },
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
function monoKids(node) {
  return (node.children || []).filter(function (n) { return n.tagName === "SPAN" && hasClass(n, "mono"); });
}
function monoDesc(node) {
  const out = [];
  const all = walk(node, []);
  for (let i = 0; i < all.length; i++) {
    const n = all[i];
    if (n !== node && n.tagName === "SPAN" && hasClass(n, "mono")) out.push(n);
  }
  return out;
}
function childOf(node) {
  return { tag: node.tagName, className: node.className || "", text: node.textContent };
}
function note(root) {
  const node = find(root, function (n) { return hasClass(n, "td-note") && !hasClass(n, "wait"); });
  if (!node) throw new Error("no off note :: " + root.textContent.slice(0, 240));
  return node;
}
function job() {
  return {
    kind: "job", ts: "2026-10-21T15:40:00", state: "done", summary: "Shipped",
    member: "Builder", tool: "cursor", checks: null, id: "j1", thread: null, pr: null, by_you: false
  };
}
function todayBody(dir) {
  return {
    generated: "2026-10-21T15:40:00",
    github: false,
    lab_dir: dir,
    counts: { messages_today: 1, tools_connected: 1, repos: 1 },
    repos: [{ repo: "web-app", failed: false, rows: [job()] }]
  };
}
function reposBody(dir) {
  return {
    generated: "2026-10-21T15:40:00",
    github: false,
    github_unreadable: false,
    lab_dir: dir,
    counts: { messages_today: 0, tools_connected: 1, repos: 1 },
    repos: [{ repo: "web-app", jobs_today: { sent: 0, picked_up: 0, done: 1, failed: 0 }, prs: [], checks: [] }]
  };
}
async function paint(view, path, body) {
  const root = makeEl("div");
  await view.render(root, { api: function (got) {
    if (got !== path) throw new Error("unexpected " + got);
    return Promise.resolve(body);
  } });
  return root;
}
function snap(viewName, dir, root) {
  const node = note(root);
  const kids = node.children || [];
  const sentence = kids.length > 1 ? kids[1] : null;
  return {
    view: viewName,
    dir: dir,
    text: node.textContent,
    mono: monoDesc(node).map(function (n) { return n.textContent; }),
    children: kids.map(childOf),
    noteMono: monoKids(node).map(function (n) { return n.textContent; }),
    sentenceMono: sentence ? monoKids(sentence).map(function (n) { return n.textContent; }) : []
  };
}
(async function () {
  const dirs = ["~/atlas-lab", "/srv/lab"];
  const cases = [];
  for (let i = 0; i < dirs.length; i++) {
    const dir = dirs[i];
    cases.push(snap("today", dir, await paint(today, "/api/today", todayBody(dir))));
    cases.push(snap("repos", dir, await paint(repos, "/api/repos", reposBody(dir))));
  }
  process.stdout.write(JSON.stringify({ cases: cases }));
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
"""


@pytest.mark.skipif(_NODE, reason="node not installed")
def test_setup_doctor_line_is_mono() -> None:
    out = _node(_SETUP, SETUP)
    assert out["help"] == HELP
    assert out["mono"] == [CMD]


@pytest.mark.skipif(_NODE, reason="node not installed")
def test_github_off_path_and_gh_are_mono() -> None:
    out = _node(_VIEWS, TODAY, REPOS)
    assert len(out["cases"]) == 4
    seen = {(case["view"], case["dir"]) for case in out["cases"]}
    assert seen == {("today", directory) for directory in DIRS} | {("repos", directory) for directory in DIRS}
    for case in out["cases"]:
        sentence = _sentence(case["dir"])
        assert case["text"] == "○ " + sentence
        assert case["mono"] == [case["dir"] + "/atlas.json", "gh"]
        kids = case["children"]
        assert len(kids) == 2
        assert [child["tag"] for child in kids] == ["SPAN", "SPAN"]
        assert kids[0]["className"] == "m" and kids[0]["text"] == "○"
        assert kids[1]["className"] == "" and kids[1]["text"] == " " + sentence
        assert case["noteMono"] == []
        assert case["sentenceMono"] == [case["dir"] + "/atlas.json", "gh"]

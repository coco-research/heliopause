"""Messages view: copy text matches COPY.md, states are the five marks, summaries stay text."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "atlas" / "static" / "views" / "messages.js"
CSS = ROOT / "atlas" / "static" / "views" / "messages.css"
COPY = ROOT / "tests" / "fixtures" / "copy" / "messages.md"  # the COPY.md lines this view pins
FIX = ROOT / "tests" / "fixtures" / "messages" / "log.json"

_HARNESS = r"""
const fs = require("fs");
global.window = global;
const view = require(process.argv[1]);
const fix = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

let innerHTMLSets = 0;
function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    className: "",
    children: [],
    hidden: false,
    disabled: false,
    readOnly: false,
    value: "",
    selected: false,
    focused: false,
    type: "",
    tabIndex: 0,
    attrs: {},
    listeners: {},
    isConnected: true,
    _text: null,
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
  el.focus = function () { el.focused = true; };
  el.select = function () { el.selected = true; };
  el.contains = function (node) {
    if (node === el) return true;
    return el.children.some((c) => c.contains && c.contains(node));
  };
  Object.defineProperty(el, "firstChild", { get: function () { return el.children[0] || null; } });
  Object.defineProperty(el, "textContent", {
    get: function () {
      if (el.children.length) return el.children.map((c) => c.textContent).join("");
      return el._text == null ? "" : el._text;
    },
    set: function (v) { el._text = String(v); el.children = []; },
  });
  Object.defineProperty(el, "innerHTML", {
    set: function () { innerHTMLSets += 1; },
    get: function () { return ""; },
  });
  return el;
}

const visFns = [];
global.document = {
  hidden: false,
  activeElement: null,
  createElement: function (tag) { return makeEl(tag); },
  createTextNode: function (s) {
    const n = makeEl("#text");
    n.textContent = s;
    return n;
  },
  addEventListener: function (type, fn) { visFns.push({ type: type, fn: fn }); },
};
const copied = [];
if (typeof navigator === "undefined") globalThis.navigator = {};  // Node < 21 has no navigator (GitHub runners ship Node 20)
navigator.clipboard = {
  writeText: function (value) { copied.push(value); return Promise.resolve(); },
};
let tick = null;
let intervalMs = 0;
global.setInterval = function (fn, ms) { tick = fn; intervalMs = ms; return 1; };
global.clearInterval = function () {};
const timeouts = [];
global.setTimeout = function (fn, ms) { timeouts.push(ms); return 1; };
global.clearTimeout = function () {};

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
function click(node) {
  if (!node || !node.listeners.click) throw new Error("no click target");
  node.listeners.click[0]();
}

let markMode = "ok";
const calls = [];
let refreshCount = 0;
let lastRefresh = Promise.resolve();
const root = makeEl("div");
const ctx = {
  api: function (path, opts) {
    calls.push({ path: path, opts: opts || null });
    if (path === "/api/jobs/mark") {
      return Promise.resolve(markMode === "err" ? { error: "nope" } : { ok: true });
    }
    if (path.indexOf("thread=") >= 0) return Promise.resolve({ messages: fix.thread });
    return Promise.resolve({ generated: fix.generated, messages: fix.messages });
  },
  refresh: function () {
    refreshCount += 1;
    lastRefresh = view.render(root, ctx);
    return lastRefresh;
  },
};

(async function () {
  const out = {};
  const reg = window.HP.views.messages;
  out.registered = reg.title === "Messages" && reg.render === view.render;
  out.sample = view.copyJobText({
    id: "j-1042",
    to: { name: "Builder", role: "factory" },
    repo: "api-server",
    summary: "{job text}",
  });
  out.note = view.startNote("Cursor");
  out.real = view.copyJobText(fix.messages[1]);
  out.roleName = view.copyJobText({
    id: "m-001122334455",
    to: { role: "factory" },
    repo: "api-server",
    summary: "x",
  });

  await view.render(root, ctx);
  out.listText = root.textContent;
  out.firstUrl = calls[0].path;

  const roleAt = calls.length;
  click(find(root, function (n) { return n.tagName === "BUTTON" && n.textContent === "To Lab"; }));
  await lastRefresh;
  out.roleUrls = calls.slice(roleAt).map(function (c) { return c.path; });

  const waitAt = calls.length;
  click(find(root, function (n) {
    return n.tagName === "BUTTON" && n.textContent.indexOf("waiting on you") >= 0;
  }));
  await lastRefresh;
  out.waitUrls = calls.slice(waitAt).map(function (c) { return c.path; });

  const threadAt = calls.length;
  click(find(root, function (n) {
    return hasClass(n, "msg") && n.textContent.indexOf("{job text}") >= 0;
  }));
  await lastRefresh;
  out.threadUrls = calls.slice(threadAt).map(function (c) { return c.path; });
  out.threadText = root.textContent;

  click(find(root, function (n) { return n.tagName === "BUTTON" && n.textContent === "Copy job"; }));
  await Promise.resolve();
  await Promise.resolve();
  out.copied = copied[0];
  out.copiedLabel = find(root, function (n) { return n.tagName === "BUTTON" && n.textContent === "Copied"; }).textContent;
  out.timer = timeouts[0];

  navigator.clipboard.writeText = function () { return Promise.reject(new Error("denied")); };
  click(find(root, function (n) { return n.tagName === "BUTTON" && n.textContent === "Copied"; }));
  await Promise.resolve();
  await Promise.resolve();
  const box = find(root, function (n) { return n.tagName === "TEXTAREA"; });
  out.fallback = box.selected === true && box.readOnly === true && box.hidden === false && box.value === out.real;

  const markAt = calls.length;
  click(find(root, function (n) { return n.tagName === "BUTTON" && n.textContent === "Mark done"; }));
  await Promise.resolve();
  await lastRefresh;
  const markCall = calls.slice(markAt).filter(function (c) { return c.path === "/api/jobs/mark"; })[0];
  out.mark = JSON.parse(markCall.opts.body);
  out.markMethod = markCall.opts.method;
  out.markHeaders = markCall.opts.headers;

  markMode = "err";
  click(find(root, function (n) { return n.tagName === "BUTTON" && n.textContent === "Mark failed"; }));
  await Promise.resolve();
  await Promise.resolve();
  out.markError = root.textContent.indexOf("nope") >= 0;

  const visAt = refreshCount;
  document.hidden = true;
  visFns[0].fn();
  out.visHidden = refreshCount === visAt;
  document.hidden = false;
  visFns[0].fn();
  await lastRefresh;
  out.visShown = refreshCount === visAt + 1;

  const pollAt = refreshCount;
  document.hidden = true;
  tick();
  out.pollSkippedHidden = refreshCount === pollAt;
  document.hidden = false;
  document.activeElement = find(root, function (n) { return n.tagName === "SELECT"; });
  tick();
  out.pollSkippedFocus = refreshCount === pollAt;
  document.activeElement = null;
  tick();
  out.pollRan = refreshCount === pollAt + 1;
  out.intervalMs = intervalMs;
  out.innerHTML = innerHTMLSets;
  out.openInCursor = root.textContent.indexOf("Open in Cursor") >= 0;
  process.stdout.write(JSON.stringify(out));
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
"""


def _js() -> str:
    return JS.read_text(encoding="utf-8")


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


def _j8() -> str:
    body = COPY.read_text(encoding="utf-8").split("**J8", 1)[1].split("**J9", 1)[0]
    return body


def _quotes(section: str) -> list[str]:
    return re.findall(r'"([^"]*)"', section)


def test_js_parses() -> None:
    assert shutil.which("node"), "node is required"
    proc = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_registers_messages_view() -> None:
    src = _js()
    assert "window.HP.views.messages" in src
    assert 'title: "Messages"' in src
    assert "function render(rootEl, ctx)" in src


def test_states_match_shared_copy() -> None:
    src = _js()
    line = next(ln for ln in COPY.read_text(encoding="utf-8").splitlines() if ln.startswith("- **Message states:**"))
    bits = [bit.strip() for bit in line.split("**Message states:**", 1)[1].split("·")]
    assert bits == ["◇ Waiting on you", "○ Sent", "◐ Picked up", "● Done", "▲ Failed"]
    for bit in bits:
        assert bit in src


@pytest.mark.journey
def test_copy_job_template_matches_j8() -> None:
    """J8: with self-start off, the Messages copy line is what the user starts in the tool."""
    src = _js()
    j8 = _j8()
    quotes = _quotes(j8)
    note = next(q for q in quotes if "starts jobs when you do" in q)
    job_line = next(q for q in quotes if q.startswith("Job "))
    report_line = next(q for q in quotes if q.startswith("When done, run cd ~/Heliopause && python3 -B -m atlas report"))
    for part in note.split("Cursor"):
        if part:
            assert part in src
    for frag in (
        '"Job "',
        '" for "',
        '" in "',
        '": "',
        "When done, run cd ~/Heliopause && python3 -B -m atlas report ",
        '" done|failed --tests pass|fail --lab ~/atlas-lab"',
    ):
        assert frag in src
    for phrase in ("Copy job", "Finished it another way? Mark it yourself.", "Mark done", "Mark failed"):
        assert phrase in quotes
        assert phrase in src
    assert "Open in Cursor" in quotes
    assert "Open in Cursor" not in src
    assert job_line in j8 and report_line in j8


def test_check_words_match_copy() -> None:
    src = _js()
    copy = COPY.read_text(encoding="utf-8")
    for phrase in (
        "● Tests pass",
        "▲ Tests fail",
        "● Reviewer: approve",
        "○ No review",
        "● CI passed",
        "◐ CI running",
        "▲ CI failed",
        "○ No CI workflow",
    ):
        assert phrase in copy
        assert phrase in src


def test_summaries_are_not_html() -> None:
    src = _js()
    assert "textContent" in src
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert banned not in src


def test_endpoints_and_poll() -> None:
    src = _js()
    assert "/api/messages" in src
    assert "/api/jobs/mark" in src
    for key in ("role", "tool", "repo", "state", "thread", "waiting"):
        assert key in src
    assert "navigator.clipboard.writeText" in src
    assert "Copied" in src
    assert "2000" in src
    assert "readOnly" in src
    assert ".select()" in src
    assert "ponytail:" in src
    assert "document.hidden" in src
    assert "5000" in src


def test_layout_breakpoints() -> None:
    css = _css()
    assert "340px" in css
    assert "1100px" in css
    assert "1fr" in css
    assert "400px" in css
    assert "560px" in css
    assert 'grid-template-areas: "t ft st" ". sum sum"' in css


def test_fixture_log_copy_and_mark() -> None:
    assert shutil.which("node"), "node is required"
    proc = subprocess.run(
        ["node", "-e", _HARNESS, str(JS), str(FIX)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    j8 = _j8()
    quotes = _quotes(j8)
    note = next(q for q in quotes if "starts jobs when you do" in q)
    job_line = next(q for q in quotes if q.startswith("Job "))
    report_line = next(q for q in quotes if q.startswith("When done, run cd ~/Heliopause && python3 -B -m atlas report"))
    assert out["sample"] == job_line + "\n" + report_line
    assert out["note"] == note
    assert out["real"] == (
        "Job j-1042 for Builder in api-server: {job text}\n"
        "When done, run cd ~/Heliopause && python3 -B -m atlas report m-bbbbbbbbbbbb done|failed --tests pass|fail --lab ~/atlas-lab"
    )
    assert out["roleName"] == (
        "Job m-001122334455 for Factory in api-server: x\n"
        "When done, run cd ~/Heliopause && python3 -B -m atlas report m-001122334455 done|failed --tests pass|fail --lab ~/atlas-lab"
    )
    assert out["registered"] is True
    assert out["innerHTML"] == 0
    assert "<b>approve the plan</b>" in out["listText"]
    assert out["listText"].index("<b>approve the plan</b>") < out["listText"].index("{job text}")
    assert "2 messages today" in out["listText"]
    assert "◇ Waiting on you" in out["listText"] or (
        "◇" in out["listText"] and "Waiting on you" in out["listText"]
    )
    assert out["firstUrl"] == "/api/messages"
    assert "/api/messages?role=lab" in out["roleUrls"]
    assert "/api/messages?role=lab&state=waiting" in out["waitUrls"]
    assert "/api/messages?role=lab" in out["waitUrls"]
    thread_urls = [url for url in out["threadUrls"] if "thread=" in url]
    assert thread_urls == ["/api/messages?thread=m-bbbbbbbbbbbb"]
    assert "● Tests pass" in out["threadText"]
    assert "○ No review" in out["threadText"]
    assert "○ No CI workflow" in out["threadText"]
    assert note in out["threadText"]
    assert "Finished it another way? Mark it yourself." in out["threadText"]
    assert "sent the job to" in out["threadText"]
    assert out["openInCursor"] is False
    assert out["copied"] == out["real"]
    assert out["copiedLabel"] == "Copied"
    assert out["timer"] == 2000
    assert out["fallback"] is True
    assert out["mark"] == {"id": "m-bbbbbbbbbbbb", "result": "done"}
    assert out["markMethod"] == "POST"
    assert out["markHeaders"] == {"Content-Type": "application/json"}
    assert "X-Atlas-UI" not in out["markHeaders"]
    assert out["markError"] is True
    assert out["visHidden"] is True
    assert out["visShown"] is True
    assert out["pollSkippedHidden"] is True
    assert out["pollSkippedFocus"] is True
    assert out["pollRan"] is True
    assert out["intervalMs"] == 5000


_NAMES = r"""
const fs = require("fs");
const msgPath = process.argv[1];
const appPath = process.argv[2];

global.window = global;
const view = require(msgPath);

let created = 0;
function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    className: "",
    children: [],
    hidden: false,
    disabled: false,
    value: "",
    type: "",
    tabIndex: 0,
    attrs: {},
    listeners: {},
    isConnected: true,
    parent: null,
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
    child.parent = null;
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
  return el;
}

global.document = {
  hidden: false,
  activeElement: null,
  createElement: function (tag) { created += 1; return makeEl(tag); },
  createTextNode: function (s) {
    const n = makeEl("#text");
    n.textContent = s;
    return n;
  },
  addEventListener: function () {},
  getElementById: function () { return null; },
  querySelectorAll: function () { return []; },
  querySelector: function () { return {}; },
  body: { appendChild: function () {} },
  head: { appendChild: function () {} }
};
global.location = { port: "", protocol: "http:", hash: "#/messages" };
global.addEventListener = function () {};

let tick = null;
global.setInterval = function (fn) { tick = fn; return 1; };
global.clearInterval = function () {};

function walk(node, acc) {
  acc.push(node);
  for (let i = 0; i < (node.children || []).length; i++) walk(node.children[i], acc);
  return acc;
}
function hasClass(node, name) {
  return (" " + (node.className || "") + " ").indexOf(" " + name + " ") >= 0;
}
function find(pred) {
  const all = walk(root, []);
  for (let i = 0; i < all.length; i++) if (pred(all[i])) return all[i];
  return null;
}

const msg = {
  id: "m-job",
  ts: "2026-10-22T17:19:00Z",
  kind: "job",
  state: "sent",
  summary: "ship the map",
  repo: "cursor",
  from: { role: "lab", tool: "claude-code", name: "Planner" },
  to: { role: "factory", name: "Builder" },
  to_tool: "cursor",
  thread: "m-job"
};
let listGenerated = "2026-10-22T18:00:00Z";
let threadGenerated = "2026-10-22T18:00:00Z";
let refreshCount = 0;
let lastRefresh = Promise.resolve();
const root = makeEl("div");
const ctx = {
  api: function (path) {
    if (String(path).indexOf("thread=") >= 0) {
      return Promise.resolve({ generated: threadGenerated, messages: [msg] });
    }
    return Promise.resolve({ generated: listGenerated, messages: [msg] });
  },
  refresh: function () {
    refreshCount += 1;
    lastRefresh = view.render(root, ctx);
    return lastRefresh;
  }
};

function fail(m) {
  console.error(m);
  process.exit(1);
}

(async function () {
  const note = view.startNote("claude-code");
  if (note.indexOf("claude-code starts jobs when you do") !== 0) fail("fallback note " + note);
  await view.render(root, ctx);
  let row = find(function (n) { return hasClass(n, "msg"); });
  if (!row || row.textContent.indexOf("claude-code") < 0 || row.textContent.indexOf("Claude Code") >= 0) {
    fail("fallback row " + (row && row.textContent));
  }

  eval(fs.readFileSync(appPath, "utf8"));
  msg.summary = "ship the names";
  await ctx.refresh();
  row = find(function (n) { return hasClass(n, "msg"); });
  if (!row || row.textContent.indexOf("Claude Code") < 0 || row.textContent.indexOf("claude-code") >= 0) {
    fail("mapped row " + (row && row.textContent));
  }
  const toolSel = find(function (n) { return n.tagName === "SELECT" && n.attrs["aria-label"] === "Tool"; });
  const toolOpt = toolSel && toolSel.children.filter(function (c) { return c.value === "claude-code"; })[0];
  if (!toolOpt || toolOpt.textContent !== "Claude Code") fail("tool label " + (toolOpt && toolOpt.textContent));
  const repoSel = find(function (n) { return n.tagName === "SELECT" && n.attrs["aria-label"] === "Repo"; });
  const repoOpt = repoSel && repoSel.children.filter(function (c) { return c.value === "cursor"; })[0];
  if (!repoOpt || repoOpt.textContent !== "cursor") fail("repo label " + (repoOpt && repoOpt.textContent));

  row.listeners.click[0]();
  await lastRefresh;
  if (root.textContent.indexOf("Cursor starts jobs when you do") < 0) fail("thread note " + root.textContent);
  const kept = find(function (n) { return n.tagName === "P" && n.textContent.indexOf("Cursor starts jobs") >= 0; });
  if (!kept || !root.contains(kept)) fail("note missing");

  const before = refreshCount;
  document.activeElement = row;
  created = 0;
  listGenerated = "2026-10-22T19:00:00Z";
  threadGenerated = "2026-10-22T19:00:00Z";
  tick();
  await lastRefresh;
  const renderCalls = created;
  if (renderCalls !== 0) fail("identical poll drew " + renderCalls);
  if (!root.contains(kept)) fail("thread rebuilt");
  if (document.activeElement !== row) fail("focus dropped");
  if (refreshCount !== before + 1) fail("poll did not refresh " + refreshCount);

  msg.summary = "brand new";
  created = 0;
  tick();
  await lastRefresh;
  if (created === 0) fail("changed payload did not draw");
  if (root.textContent.indexOf("brand new") < 0) fail("new summary missing");

  delete msg.to_tool;
  msg.summary = "no target tool";
  await ctx.refresh();
  if (root.textContent.indexOf("no target tool") < 0) fail("bare thread missing");
  if (root.textContent.indexOf("starts jobs when you do") >= 0) fail("note without to_tool " + root.textContent);
  if (root.textContent.indexOf("Copy job") < 0) fail("copy without to_tool");
  if (root.textContent.indexOf("Mark done") < 0) fail("done without to_tool");
  if (root.textContent.indexOf("Mark failed") < 0) fail("failed without to_tool");

  process.stdout.write(JSON.stringify({
    row: "Claude Code",
    renderCalls: renderCalls,
    fallback: true
  }));
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
"""


def test_messages_row_and_identical_poll() -> None:
    assert shutil.which("node"), "node is required"
    proc = subprocess.run(
        ["node", "-e", _NAMES, str(JS), str(ROOT / "atlas" / "static" / "app.js")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    assert out["fallback"] is True
    assert out["row"] == "Claude Code"
    assert out["renderCalls"] == 0


def test_split_rows_keep_the_repo_on_one_line():
    css = _css()
    block = css.split("@media (min-width: 1101px)", 1)[1]
    assert "48px 150px minmax(0, 1fr) 132px" in block
    assert "text-overflow: ellipsis" in block
    assert "white-space: nowrap" in block
    assert "max-width: 12em" in block
    assert "48px 150px minmax(0, 1fr) auto 132px" in css


_DAYS = r"""
const fs = require("fs");
const msgPath = process.argv[1];
const appPath = process.argv[2];

global.window = global;
global.addEventListener = function () {};

function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    className: "",
    children: [],
    hidden: false,
    disabled: false,
    value: "",
    type: "",
    attrs: {},
    listeners: {},
    isConnected: true,
    parent: null,
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
    child.parent = null;
    return child;
  };
  el.setAttribute = function (k, v) { el.attrs[k] = String(v); };
  el.getAttribute = function (k) {
    return Object.prototype.hasOwnProperty.call(el.attrs, k) ? el.attrs[k] : null;
  };
  el.addEventListener = function (type, fn) {
    (el.listeners[type] || (el.listeners[type] = [])).push(fn);
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

global.document = {
  hidden: false,
  activeElement: null,
  documentElement: {
    setAttribute: function () {},
    removeAttribute: function () {},
    getAttribute: function () { return null; }
  },
  createElement: function (tag) { return makeEl(tag); },
  createTextNode: function (s) {
    const n = makeEl("#text");
    n.textContent = s;
    return n;
  },
  addEventListener: function () {},
  getElementById: function () { return null; },
  querySelectorAll: function () { return []; },
  querySelector: function () { return null; },
  body: { appendChild: function () {} },
  head: { appendChild: function () {} }
};
global.location = { port: "", protocol: "http:", hash: "#/messages" };
global.setInterval = function () { return 1; };
global.clearInterval = function () {};
global.setTimeout = function () { return 1; };
global.clearTimeout = function () {};

eval(fs.readFileSync(appPath, "utf8"));
const view = require(msgPath);

function iso(y, m, d, h, mi) {
  return new Date(y, m - 1, d, h, mi, 0, 0).toISOString();
}
function row(id, ts, summary) {
  return {
    id: id,
    ts: ts,
    kind: "job",
    state: "sent",
    summary: summary,
    repo: "api-server",
    from: { role: "lab", tool: "claude-code", name: "Planner" },
    to: { role: "factory", tool: "cursor", name: "Builder" },
    thread: id
  };
}
const messages = [
  row("m-000000000003", iso(2026, 10, 23, 11, 0), "today row"),
  row("m-yest", iso(2026, 10, 22, 23, 0), "yesterday row"),
  row("m-old", iso(2026, 10, 20, 9, 0), "older row")
];
const root = makeEl("div");
let pending = Promise.resolve();
const ctx = {
  api: function () {
    return Promise.resolve({ generated: iso(2026, 10, 23, 12, 0), messages: messages });
  },
  refresh: function () {
    pending = view.render(root, ctx);
    return pending;
  }
};

function walk(node, acc) {
  acc.push(node);
  for (let i = 0; i < (node.children || []).length; i++) walk(node.children[i], acc);
  return acc;
}
function hasClass(node, name) {
  return (" " + (node.className || "") + " ").indexOf(" " + name + " ") >= 0;
}
function find(pred) {
  const all = walk(root, []);
  for (let i = 0; i < all.length; i++) if (pred(all[i])) return all[i];
  return null;
}
function tallyClass(word) {
  const btn = find(function (n) {
    return n.tagName === "BUTTON" && n.parent && hasClass(n.parent, "tally") && n.textContent.indexOf(word) >= 0;
  });
  return btn ? btn.className : "";
}
function toolCount() {
  return walk(root, []).filter(function (n) { return hasClass(n, "tool-cell"); }).length;
}

(async function () {
  await view.render(root, ctx);
  const log = find(function (n) { return hasClass(n, "hp-log"); });
  const order = (log ? log.children : []).map(function (n) {
    if (hasClass(n, "day")) return n.textContent;
    if (hasClass(n, "msg")) return "row";
    return n.className;
  });
  const h1 = find(function (n) { return n.tagName === "H1"; });
  const eyebrow = find(function (n) { return hasClass(n, "eyebrow"); });
  const before = toolCount();
  const today = find(function (n) { return hasClass(n, "msg") && n.textContent.indexOf("today row") >= 0; });
  today.listeners.click[0]();
  await pending;
  const header = find(function (n) { return hasClass(n, "tmeta"); });
  process.stdout.write(JSON.stringify({
    order: order,
    title: h1 ? h1.textContent : "",
    eyebrow: eyebrow ? eyebrow.textContent : "",
    zero: {
      waiting: tallyClass("waiting on you"),
      picked_up: tallyClass("picked up"),
      failed: tallyClass("failed"),
      done: tallyClass("done")
    },
    sent: tallyClass("sent"),
    toolBefore: before,
    toolAfter: toolCount(),
    header: header ? header.textContent : ""
  }));
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
"""


def test_yesterday_divider_zero_tally_and_split_rows() -> None:
    assert shutil.which("node"), "node is required"
    proc = subprocess.run(
        ["node", "-e", _DAYS, str(JS), str(ROOT / "atlas" / "static" / "app.js")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    assert out["order"] == ["row", "Yesterday", "row", "Tue 20 Oct", "row"]
    assert out["title"] == "1 message today"
    assert out["eyebrow"] == "Messages · Today"
    assert out["zero"] == {"waiting": "st", "picked_up": "st", "failed": "st", "done": "st"}
    assert out["sent"] == "st sent"
    assert out["toolBefore"] == 3
    assert out["toolAfter"] == 0
    assert "m-000000000003" not in out["header"]


# Same DOM stub as _HARNESS. clearInterval records ids so a leave can prove the 5s poll (id 1) was cleared.
_LEAVE_JS = r"""
const assert = require("assert");
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
  resolveHold({ generated: fix.generated, messages: fix.messages });
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


def test_leaving_messages_drops_its_late_reply_and_poll() -> None:
    assert shutil.which("node"), "node is required"
    proc = subprocess.run(
        ["node", "-e", _leave_script(), str(JS), str(FIX)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout

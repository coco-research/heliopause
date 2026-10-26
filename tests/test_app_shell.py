"""Shell at /, the old page at /map, and the Now view against fixture JSON."""

import json
import re
import shutil
import subprocess
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from atlas.__main__ import make_server

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests" / "fixtures" / "home"
STATIC = ROOT / "atlas" / "static"
INDEX = (STATIC / "index.html").read_bytes()
APP_HTML = (STATIC / "app.html").read_bytes()
APP_CSS = (STATIC / "app.css").read_bytes()
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
NOW_JS = (STATIC / "views" / "now.js").read_text(encoding="utf-8")
FIXTURE = ROOT / "tests" / "fixtures" / "now.json"
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "font-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_OFF = "Heliopause sends nothing off this machine."
_ON = "Heliopause sends only PR and CI lookups to GitHub."
_NEW = (
    ("/", "text/html; charset=utf-8", APP_HTML),
    ("/app.css", "text/css; charset=utf-8", APP_CSS),
    ("/app.js", "text/javascript; charset=utf-8", APP_JS.encode("utf-8")),
    ("/views/now.js", "text/javascript; charset=utf-8", NOW_JS.encode("utf-8")),
)
_BLOCKED = ("/nope", "/app.html", "/views/nope.js", "/views/../collect.py", "/app.js/../__main__.py")

_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const fixture = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const twoJobs = JSON.parse(JSON.stringify(fixture));
twoJobs.seats.factory.push({ name: "Reviewer", tool: "grok", state: "idle", since: null, repo: null, waiting: 1 });
const empty = {
  generated: "2026-10-22T14:24:00Z",
  sentence: "",
  counts: { messages_today: 0, tools_connected: 3, repos: 2 },
  needs_you: [],
  seats: {
    hq: [{ name: "You", tool: null, state: "idle", since: null, repo: null, waiting: 0 }],
    lab: [{ name: "Planner", tool: "claude", state: "idle", since: null, repo: null, waiting: 0 }],
    factory: [{ name: "Builder", tool: "cursor", state: "idle", since: null, repo: null, waiting: 0 }]
  },
  moving: [],
  github: false
};

function Node(tag) {
  this.tag = tag || "#text";
  this.children = [];
  this.parentNode = null;
  this.attrs = {};
  this._text = "";
  this.className = "";
  this.hidden = false;
  this.disabled = false;
  this.type = "";
  this.id = "";
  this.href = "";
  this.listeners = {};
  var self = this;
  this.classList = {
    toggle: function (name, force) {
      var parts = self.className.split(/\s+/).filter(Boolean);
      var has = parts.indexOf(name) >= 0;
      var on = force === undefined ? !has : !!force;
      if (on && !has) parts.push(name);
      if (!on) parts = parts.filter(function (p) { return p !== name; });
      self.className = parts.join(" ");
      return on;
    }
  };
}
Node.prototype.appendChild = function (child) {
  child.parentNode = this;
  this.children.push(child);
  return child;
};
Node.prototype.removeChild = function (child) {
  var i = this.children.indexOf(child);
  if (i >= 0) this.children.splice(i, 1);
  child.parentNode = null;
  return child;
};
Object.defineProperty(Node.prototype, "firstChild", {
  get: function () { return this.children[0] || null; }
});
Object.defineProperty(Node.prototype, "textContent", {
  get: function () {
    if (this.tag === "#text") return this._text;
    if (this.children.length) return this.children.map(function (c) { return c.textContent; }).join("");
    return this._text;
  },
  set: function (v) {
    this.children = [];
    this._text = String(v);
  }
});
Node.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
Node.prototype.getAttribute = function (k) {
  return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
};
Node.prototype.addEventListener = function (type, fn) {
  (this.listeners[type] = this.listeners[type] || []).push(fn);
};
Node.prototype.focus = function () { active = this; };
Node.prototype.click = function () {
  var list = (this.listeners.click || []).slice();
  for (var i = 0; i < list.length; i++) list[i]({});
};
Node.prototype.querySelectorAll = function (sel) { return queryAll(this, sel); };

function matches(node, sel) {
  sel = sel.trim();
  if (sel.charAt(0) === "[") {
    var name = sel.slice(1, -1).split("=")[0];
    return node.getAttribute(name) != null || node[name];
  }
  return node.tag === sel;
}
function queryAll(root, selector) {
  var parts = selector.split(",");
  var out = [];
  function walk(node) {
    for (var i = 0; i < node.children.length; i++) {
      var child = node.children[i];
      for (var p = 0; p < parts.length; p++) {
        if (matches(child, parts[p])) { out.push(child); break; }
      }
      walk(child);
    }
  }
  walk(root);
  return out;
}

var active = null;
var docListeners = {};
var extra = [];
var root = new Node("div");
function walkAll(fn) {
  function walk(node) {
    fn(node);
    for (var i = 0; i < node.children.length; i++) walk(node.children[i]);
  }
  walk(root);
  for (var i = 0; i < extra.length; i++) walk(extra[i]);
}
var document = {
  title: "",
  createElement: function (tag) { return new Node(tag); },
  createTextNode: function (text) {
    var node = new Node("#text");
    node._text = String(text);
    return node;
  },
  getElementById: function (id) {
    var found = null;
    walkAll(function (node) { if (!found && node.id === id) found = node; });
    return found;
  },
  addEventListener: function (type, fn) {
    (docListeners[type] = docListeners[type] || []).push(fn);
  },
  removeEventListener: function (type, fn) {
    var list = docListeners[type] || [];
    var i = list.indexOf(fn);
    if (i >= 0) list.splice(i, 1);
  }
};
Object.defineProperty(document, "activeElement", { get: function () { return active; } });

var badge = new Node("span");
badge.id = "hp-now-badge";
badge.hidden = true;
var liveNarrow = new Node("span");
liveNarrow.id = "hp-live-narrow";
liveNarrow.textContent = "Live";
extra.push(badge, liveNarrow);

var handles = {};
var realSet = setInterval;
var realClear = clearInterval;
var sandbox = {
  document: document,
  navigator: { clipboard: { writeText: function () { return Promise.resolve(); } } },
  setInterval: function (fn, ms) {
    var id = realSet(fn, ms);
    handles[id] = fn;
    return id;
  },
  clearInterval: function (id) {
    delete handles[id];
    return realClear(id);
  },
  Date: Date,
  Intl: Intl,
  Promise: Promise,
  console: console,
  Math: Math,
  isNaN: isNaN,
  JSON: JSON,
  Object: Object
};
var privacy = [];
sandbox.HP = {
  views: {},
  setPrivacy: function (github) { privacy.push(!!github); }
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "now.js" });

var mode = "fixture";
var posts = [];
var refresh = 0;
function api(path, opts) {
  if (opts && opts.method === "POST") posts.push(JSON.parse(JSON.stringify(opts.body || {})));
  if (path === "/api/now") {
    if (mode === "fail") return Promise.reject(new Error("down"));
    if (mode === "empty") return Promise.resolve(empty);
    if (mode === "two") return Promise.resolve(twoJobs);
    return Promise.resolve(fixture);
  }
  return Promise.resolve({ ok: true });
}
function ctx() {
  return { api: api, refresh: function () { refresh += 1; } };
}
function flush() {
  return new Promise(function (resolve) { setImmediate(resolve); });
}
function button(text) {
  var all = queryAll(root, "button");
  for (var i = 0; i < all.length; i++) if (all[i].textContent === text) return all[i];
  return null;
}
function key(ev) {
  var list = (docListeners.keydown || []).slice();
  ev.preventDefault = ev.preventDefault || function () {};
  for (var i = 0; i < list.length; i++) list[i](ev);
}
function stop() {
  if (root._hpStop) root._hpStop();
}
function fail(msg) {
  console.error(msg);
  stop();
  process.exit(1);
}

(async function () {
  try {
    sandbox.HP.views.now.render(root, ctx());
    await flush();
    var text = root.textContent;
    if (text.indexOf("2 asks are waiting on you, and 1 job failed.") < 0) fail("sentence");
    if (text.indexOf("Merge PR #42: rate limit on /login") < 0) fail("ask");
    if (text.indexOf("Planner asks HQ · Lab") < 0) fail("ask header");
    if (text.indexOf("8 messages today · 1 job moving · 2 repos") < 0) fail("sub");
    if (text.indexOf("1 waiting") < 0) fail("you waiting");
    if (text.indexOf("Working") < 0) fail("working");
    if (text.indexOf("1 job waiting") < 0) fail("factory waiting");
    if (text.indexOf("Update the README install steps") < 0) fail("moving");
    if (text.indexOf("1 ask") < 0 || text.indexOf("1 job") < 0) fail("wires");
    if (document.title !== "(1) Heliopause") fail("title " + document.title);
    if (!privacy.length || privacy[privacy.length - 1] !== true) fail("privacy on");
    if (badge.hidden || badge.textContent !== "1") fail("badge");
    if (liveNarrow.textContent !== "Live") fail("live");

    var before = posts.length;
    button("Decline").click();
    await flush();
    text = root.textContent;
    if (text.indexOf("Why decline?") < 0) fail("sheet");
    if (text.indexOf("Planner gets your reason as the answer.") < 0) fail("sheet note");
    if (text.indexOf("Wrong scope") < 0 || text.indexOf("Too big") < 0) fail("chips");
    if (text.indexOf("Not now") < 0 || text.indexOf("Other") < 0) fail("chips2");
    if (!button("Send decline").disabled) fail("send armed early");
    key({ key: "Escape" });
    if (root.textContent.indexOf("Why decline?") >= 0) fail("escape left the sheet");
    if (posts.length !== before) fail("escape posted");

    button("Decline").click();
    button("Cancel").click();
    if (posts.length !== before) fail("cancel posted");
    if (root.textContent.indexOf("Why decline?") >= 0) fail("cancel left the sheet");

    button("Decline").click();
    var send = button("Send decline");
    var cancel = button("Cancel");
    cancel.focus();
    key({ key: "Tab" });
    if (document.activeElement !== button("Wrong scope")) fail("focus trap");
    button("Too big").click();
    if (send.disabled) fail("send stayed disabled");
    var pressed = button("Too big").getAttribute("aria-pressed");
    if (pressed !== "true") fail("chip pressed");
    send.click();
    await flush();
    var decline = posts[posts.length - 1];
    if (!decline || decline.id !== "m-ask-1" || decline.answer !== "decline" || decline.reason !== "too_big") {
      fail("decline body " + JSON.stringify(decline));
    }
    if (refresh !== 1) fail("decline refresh " + refresh);

    stop();
    refresh = 0;
    posts = [];
    mode = "fixture";
    sandbox.HP.views.now.render(root, ctx());
    await flush();
    button("Approve").click();
    await flush();
    var approve = posts[0];
    if (!approve || approve.id !== "m-ask-1" || approve.answer !== "approve" || "reason" in approve) {
      fail("approve body " + JSON.stringify(approve));
    }
    if (refresh !== 1) fail("approve refresh");

    stop();
    mode = "empty";
    document.title = "stale";
    sandbox.HP.views.now.render(root, ctx());
    await flush();
    text = root.textContent;
    if (document.title !== "Heliopause") fail("empty title " + document.title);
    if (text.indexOf("Nothing yet. Your tools are connected and idle.") < 0) fail("j21 title");
    if (text.indexOf("0 messages today · 3 tools connected · 2 repos") < 0) fail("j21 sub");
    if (text.indexOf("Nothing is waiting on you. Asks from Lab land here, with Approve and Decline.") < 0) fail("j21 wait");
    if (text.indexOf("No job yet. Ask Planner for a plan in its chat.") < 0) fail("j21 lab");
    if (text.indexOf("Waiting for its first job.") < 0) fail("j21 factory");
    if (text.indexOf("No asks") < 0 || text.indexOf("No jobs") < 0) fail("j21 wires");
    if (text.indexOf("No messages yet today. When Planner sends Builder a job, it shows here.") < 0) fail("j21 moving");
    if (text.indexOf("Asks from Lab appear here when they arrive.") < 0) fail("you idle");
    if (text.indexOf("Nothing waiting") < 0) fail("you mark");
    if (privacy[privacy.length - 1] !== false) fail("privacy off");

    stop();
    mode = "fail";
    document.title = "Heliopause";
    sandbox.HP.views.now.render(root, ctx());
    await flush();
    text = root.textContent;
    if (text.indexOf("Heliopause stopped at") < 0) fail("banner");
    if (text.indexOf("This page stopped updating. Start it again:") < 0) fail("fallback");
    if (text.indexOf("cd ~/Heliopause && python3 -B -m atlas up") < 0 || text.indexOf("doctor") >= 0) fail("commands");
    if (text.indexOf("Reports sent while it was stopped may not arrive.") < 0) fail("reports");
    if (liveNarrow.textContent.indexOf("Offline since ") !== 0) fail("offline " + liveNarrow.textContent);
    var privCount = privacy.length;
    var ids = Object.keys(handles);
    mode = "fixture";
    handles[ids[ids.length - 1]]();
    await flush();
    if (root.textContent.indexOf("2 asks are waiting on you, and 1 job failed.") < 0) fail("recover");
    if (liveNarrow.textContent !== "Live") fail("live recovered");
    if (document.title !== "(1) Heliopause") fail("title recovered");
    if (privacy.length !== privCount + 1 || privacy[privacy.length - 1] !== true) fail("privacy recovered");
    stop();
    mode = "two";
    sandbox.HP.views.now.render(root, ctx());
    await flush();
    text = root.textContent;
    if (text.indexOf("2 jobs") < 0) fail("factory wire");
    if (text.indexOf("Columns") < 0) fail("columns switch");
    var links = queryAll(root, "a");
    var plan = null;
    var map = null;
    for (var pi = 0; pi < links.length; pi++) {
      if (links[pi].textContent === "Show the plan" && String(links[pi].href).indexOf("#/messages?thread=m-ask-1") >= 0) plan = links[pi];
      if (links[pi].textContent === "Map" && links[pi].href === "/map") map = links[pi];
    }
    if (!plan) fail("show the plan");
    if (!map) fail("map link");
    var columns = button("Columns");
    if (!columns || columns.getAttribute("aria-pressed") !== "true") fail("columns pressed");
    stop();
    process.stdout.write("ok\n");
    process.exit(0);
  } catch (err) {
    console.error(err && err.stack ? err.stack : err);
    stop();
    process.exit(1);
  }
})();
"""


@pytest.fixture(scope="module")
def httpd():
    server = make_server(HOME, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _request(httpd, path):
    port = httpd.server_address[1]
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    resp = conn.getresponse()
    headers = dict(resp.getheaders())
    assert headers["Content-Security-Policy"] == _CSP
    payload = (resp.status, headers, resp.read())
    conn.close()
    return payload


def test_root_is_the_shell_and_map_is_the_old_page(httpd):
    status, headers, body = _request(httpd, "/")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert body == APP_HTML
    text = body.decode("utf-8")
    assert "Heliopause" in text
    for route in ("#/now", "#/messages", "#/today", "#/repos"):
        assert route in text
    assert 'id="lede"' not in text
    assert "onclick=" not in text

    status, headers, body = _request(httpd, "/map")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert body == INDEX


@pytest.mark.parametrize("path,content_type,raw", _NEW)
def test_new_static_files_are_served(httpd, path, content_type, raw):
    status, headers, body = _request(httpd, path)
    assert status == 200
    assert headers["Content-Type"] == content_type
    assert body == raw


@pytest.mark.parametrize("path", _BLOCKED)
def test_paths_outside_the_allowlist_are_404(httpd, path):
    status, _headers, _body = _request(httpd, path)
    assert status == 404


def test_privacy_lines_are_pinned():
    assert _OFF in APP_JS
    assert _ON in APP_JS
    assert _OFF in APP_HTML.decode("utf-8")


def test_decline_reasons_are_the_four_chips():
    for reason in ("wrong_scope", "too_big", "not_now", "other"):
        assert f'reason: "{reason}"' in NOW_JS
    assert 'answer: "approve"' in NOW_JS
    assert 'answer: "decline"' in NOW_JS
    assert "// ponytail: polling every 5s, SSE later if it matters" in NOW_JS


def test_no_inline_onclick():
    for path in (STATIC / "app.html", STATIC / "app.css", STATIC / "app.js", STATIC / "views" / "now.js"):
        assert "onclick=" not in path.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_shell_scripts_parse():
    for path in (STATIC / "app.js", STATIC / "views" / "now.js"):
        proc = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_now_view_renders_fixture_json():
    proc = subprocess.run(
        ["node", "-e", _HARNESS, str(FIXTURE), str(STATIC / "views" / "now.js")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


_TOOL_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const box = {
  Object: Object,
  String: String,
  Promise: Promise,
  location: { port: "9", protocol: "http:", hash: "" },
  document: {
    getElementById: function () { return null; },
    querySelectorAll: function () { return []; },
    createElement: function () { return {}; },
    body: { appendChild: function () {} }
  }
};
box.window = box;
box.addEventListener = function () {};
vm.createContext(box);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), box, { filename: "app.js" });
const n = box.HP.toolName;
const out = {
  "claude-code": n("claude-code"),
  cursor: n("cursor"),
  codex: n("codex"),
  grok: n("grok"),
  you: n("you"),
  planner: n("planner"),
  claude: n("claude"),
  Cursor: n("Cursor"),
  empty: n(""),
  none: n(null),
  missing: n(undefined),
  toString: n("toString"),
  constructor: n("constructor")
};
process.stdout.write(JSON.stringify(out));
"""

_NOW_NAMES = r"""
const fs = require("fs");
const vm = require("vm");
const nowPath = process.argv[1];
const appPath = process.argv[2];

function Node(tag) {
  this.tag = tag || "#text";
  this.children = [];
  this.parentNode = null;
  this.attrs = {};
  this._text = "";
  this.className = "";
  this.hidden = false;
  this.disabled = false;
  this.type = "";
  this.listeners = {};
}
Node.prototype.appendChild = function (child) {
  child.parentNode = this;
  this.children.push(child);
  return child;
};
Node.prototype.removeChild = function (child) {
  var i = this.children.indexOf(child);
  if (i >= 0) this.children.splice(i, 1);
  child.parentNode = null;
  return child;
};
Object.defineProperty(Node.prototype, "firstChild", {
  get: function () { return this.children[0] || null; }
});
Object.defineProperty(Node.prototype, "textContent", {
  get: function () {
    if (this.tag === "#text") return this._text;
    if (this.children.length) return this.children.map(function (c) { return c.textContent; }).join("");
    return this._text;
  },
  set: function (v) {
    this.children = [];
    this._text = String(v);
  }
});
Node.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
Node.prototype.getAttribute = function (k) {
  return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
};
Node.prototype.addEventListener = function (type, fn) {
  (this.listeners[type] = this.listeners[type] || []).push(fn);
};
Node.prototype.focus = function () { active = this; };
Node.prototype.click = function () {
  var list = (this.listeners.click || []).slice();
  for (var i = 0; i < list.length; i++) list[i]({});
};
Node.prototype.querySelectorAll = function (sel) {
  var tag = String(sel).split(",")[0].trim();
  var out = [];
  function walk(node) {
    for (var i = 0; i < node.children.length; i++) {
      var child = node.children[i];
      if (child.tag === tag) out.push(child);
      walk(child);
    }
  }
  walk(this);
  return out;
};

var active = null;
var docListeners = {};
var created = 0;
var document = {
  title: "",
  createElement: function (tag) { created += 1; return new Node(tag); },
  createTextNode: function (text) {
    var node = new Node("#text");
    node._text = String(text);
    return node;
  },
  getElementById: function () { return null; },
  addEventListener: function (type, fn) {
    (docListeners[type] = docListeners[type] || []).push(fn);
  },
  removeEventListener: function (type, fn) {
    var list = docListeners[type] || [];
    var i = list.indexOf(fn);
    if (i >= 0) list.splice(i, 1);
  }
};
Object.defineProperty(document, "activeElement", { get: function () { return active; } });

var pullFn = null;
global.window = global;
global.document = document;
global.setInterval = function (fn) { pullFn = fn; return 1; };
global.clearInterval = function () { pullFn = null; };

var payload = {
  generated: "2026-10-22T14:24:00Z",
  sentence: "1 ask is waiting on you.",
  counts: { messages_today: 1, tools_connected: 2, repos: 1 },
  needs_you: [{
    id: "m-ask-1",
    from: { role: "lab", tool: "claude-code", name: "Planner" },
    to: { role: "hq" },
    kind: "ask",
    summary: "Merge the gate",
    repo: "api-server",
    state: "sent",
    ts: "2026-10-22T14:18:00Z",
    waiting_on_you: true
  }],
  seats: {
    hq: [{ name: "You", tool: null, state: "needs_you", since: "2026-10-22T14:18:00Z", repo: null, waiting: 1 }],
    lab: [{ name: "Planner", tool: "claude-code", state: "working", since: "2026-10-22T14:20:00Z", repo: "web-app", waiting: 0 }],
    factory: []
  },
  moving: [{
    id: "m-job-1",
    from: { role: "lab", tool: "claude-code" },
    to: { role: "factory", tool: "cursor" },
    kind: "job",
    summary: "Update the README",
    repo: "api-server",
    state: "picked_up",
    ts: "2026-10-22T14:16:00Z"
  }],
  github: false
};

require(nowPath);

var root = new Node("div");
function api() { return Promise.resolve(payload); }
function ctx() { return { api: api, refresh: function () {} }; }
function flush() { return new Promise(function (resolve) { setImmediate(resolve); }); }
function walk(node, acc) {
  acc.push(node);
  for (var i = 0; i < (node.children || []).length; i++) walk(node.children[i], acc);
  return acc;
}
function hasClass(node, name) {
  return (" " + (node.className || "") + " ").indexOf(" " + name + " ") >= 0;
}
function find(pred) {
  var all = walk(root, []);
  for (var i = 0; i < all.length; i++) if (pred(all[i])) return all[i];
  return null;
}
function button(text) {
  return find(function (n) { return n.tag === "button" && n.textContent === text; });
}
function reaches(node) {
  var cur = node;
  while (cur) {
    if (cur === root) return true;
    cur = cur.parentNode;
  }
  return false;
}
function fail(msg) {
  console.error(msg);
  process.exit(1);
}

(async function () {
  window.HP.views.now.render(root, ctx());
  await flush();
  var card = find(function (n) { return hasClass(n, "tool") && n.textContent.indexOf("Planner") >= 0; });
  var row = find(function (n) { return hasClass(n, "msg"); });
  if (!card || card.textContent.indexOf("claude-code") < 0) fail("fallback card " + (card && card.textContent));
  if (!row || row.textContent.indexOf("claude-code") < 0) fail("fallback row " + (row && row.textContent));
  if (!find(function (n) { return hasClass(n, "tool") && n.textContent.indexOf("In this page") >= 0; })) fail("you card");

  var appBox = {
    Object: Object,
    String: String,
    Promise: Promise,
    location: { port: "80", protocol: "http:", hash: "" },
    document: {
      getElementById: function () { return null; },
      querySelectorAll: function () { return []; },
      createElement: function () { return {}; },
      body: { appendChild: function () {} }
    }
  };
  appBox.window = appBox;
  appBox.addEventListener = function () {};
  vm.createContext(appBox);
  vm.runInContext(fs.readFileSync(appPath, "utf8"), appBox, { filename: "app.js" });
  window.HP.toolName = appBox.HP.toolName;

  if (root._hpStop) root._hpStop();
  root = new Node("div");
  created = 0;
  window.HP.views.now.render(root, ctx());
  await flush();
  if (created === 0) fail("mapped paint did not draw");
  card = find(function (n) { return hasClass(n, "tool") && n.textContent.indexOf("Planner") >= 0; });
  row = find(function (n) { return hasClass(n, "msg"); });
  if (!card || card.textContent.indexOf("Claude Code") < 0 || card.textContent.indexOf("claude-code") >= 0) fail("card " + (card && card.textContent));
  if (!row || row.textContent.indexOf("Claude Code") < 0 || row.textContent.indexOf("claude-code") >= 0) fail("row " + (row && row.textContent));
  var you = find(function (n) { return hasClass(n, "tool") && n.textContent.indexOf("You") >= 0; });
  if (!you || you.textContent.indexOf("In this page") < 0) fail("you after map " + (you && you.textContent));

  var head = root.firstChild;
  var decline = button("Decline");
  if (!decline) fail("no decline");
  decline.focus();
  var focused = document.activeElement;
  var renderCalls = 0;
  created = 0;
  payload.generated = "2026-10-22T15:00:00Z";
  pullFn();
  await flush();
  renderCalls = created;
  if (renderCalls !== 0) fail("identical poll drew " + renderCalls);
  if (root.firstChild !== head) fail("head replaced");
  if (document.activeElement !== focused) fail("focus dropped");
  if (!reaches(focused)) fail("focus detached");

  decline.click();
  var chip = button("Wrong scope");
  if (!chip) fail("no chip");
  chip.focus();
  created = 0;
  payload.generated = "2026-10-22T16:00:00Z";
  pullFn();
  await flush();
  if (created !== 0) fail("sheet poll drew " + created);
  if (root.textContent.indexOf("Why decline?") < 0) fail("sheet closed");
  if (document.activeElement !== chip) fail("chip focus");
  if (!reaches(chip)) fail("chip detached");

  button("Cancel").click();
  if (root.textContent.indexOf("Why decline?") >= 0) fail("cancel left the sheet");
  created = 0;
  payload.sentence = "Something changed.";
  pullFn();
  await flush();
  if (created === 0) fail("changed payload did not draw");
  if (root.textContent.indexOf("Something changed.") < 0) fail("new sentence missing");

  if (root._hpStop) root._hpStop();
  process.stdout.write(JSON.stringify({ card: "Claude Code", row: "Claude Code", renderCalls: renderCalls }));
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_tool_name_map():
    proc = subprocess.run(
        ["node", "-e", _TOOL_HARNESS, str(STATIC / "app.js")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "claude-code": "Claude Code",
        "cursor": "Cursor",
        "codex": "Codex",
        "grok": "Grok",
        "you": "You",
        "planner": "planner",
        "claude": "claude",
        "Cursor": "Cursor",
        "empty": "",
        "none": "",
        "missing": "",
        "toString": "toString",
        "constructor": "constructor",
    }


def test_views_use_the_browser_zone():
    messages = (STATIC / "views" / "messages.js").read_text(encoding="utf-8")
    for src in (NOW_JS, messages):
        assert "America/New_York" not in src
        assert "timeZone" not in src


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_now_card_row_and_identical_poll():
    proc = subprocess.run(
        ["node", "-e", _NOW_NAMES, str(STATIC / "views" / "now.js"), str(STATIC / "app.js")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    assert out["card"] == "Claude Code"
    assert out["row"] == "Claude Code"
    assert out["renderCalls"] == 0


_THEME = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");

function load(osDark) {
  const store = {};
  const root = {
    attrs: {},
    setAttribute: function (k, v) { this.attrs[k] = String(v); },
    removeAttribute: function (k) { delete this.attrs[k]; },
    getAttribute: function (k) {
      return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
    }
  };
  const buttons = ["light", "dark", "system"].map(function (mode) {
    return {
      attrs: { "data-theme-mode": mode, "aria-pressed": mode === "system" ? "true" : "false" },
      setAttribute: function (k, v) { this.attrs[k] = String(v); },
      getAttribute: function (k) {
        return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
      },
      addEventListener: function () {}
    };
  });
  const box = {
    Object: Object,
    String: String,
    Promise: Promise,
    Date: Date,
    Math: Math,
    Number: Number,
    isNaN: isNaN,
    localStorage: {
      getItem: function (k) {
        return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
      },
      setItem: function (k, v) { store[k] = String(v); }
    },
    location: { port: "9", protocol: "http:", hash: "#/messages?thread=m-ask-1" },
    document: {
      documentElement: root,
      body: { appendChild: function () {} },
      getElementById: function () { return null; },
      querySelectorAll: function (sel) {
        return sel === ".hp .theme button" ? buttons : [];
      },
      createElement: function () { return {}; },
      addEventListener: function () {}
    },
    matchMedia: function () {
      return { matches: osDark, addEventListener: function () {}, addListener: function () {} };
    }
  };
  box.window = box;
  box.addEventListener = function () {};
  vm.createContext(box);
  vm.runInContext(src, box, { filename: "app.js" });
  return { box: box, root: root, store: store, buttons: buttons };
}

function attr(root) { return root.getAttribute("data-theme"); }
function stored(store) { return Object.prototype.hasOwnProperty.call(store, "atlas.theme") ? store["atlas.theme"] : null; }
function pressed(buttons, mode) {
  for (let i = 0; i < buttons.length; i++) {
    if (buttons[i].getAttribute("data-theme-mode") === mode) return buttons[i].getAttribute("aria-pressed");
  }
  return null;
}
function iso(y, m, d, h, mi) {
  return new Date(y, m - 1, d, h, mi, 0, 0).toISOString();
}

const dark = load(true);
const light = load(false);
const fresh = {
  dark: { attr: attr(dark.root), stored: stored(dark.store) },
  light: { attr: attr(light.root), stored: stored(light.store) }
};
const age = dark.box.HP.age;
const noon = iso(2026, 10, 22, 15, 0);
dark.box.HP.chooseTheme("light");
const asLight = { attr: attr(dark.root), stored: stored(dark.store) };
dark.box.HP.chooseTheme("dark");
const asDark = {
  attr: attr(dark.root),
  stored: stored(dark.store),
  pressed: {
    light: pressed(dark.buttons, "light"),
    dark: pressed(dark.buttons, "dark"),
    system: pressed(dark.buttons, "system")
  }
};
dark.box.HP.chooseTheme("system");
const asSystem = { attr: attr(dark.root), stored: stored(dark.store) };
light.box.HP.chooseTheme("system");

process.stdout.write(JSON.stringify({
  fresh: fresh,
  chosen: {
    light: asLight,
    dark: asDark,
    system: asSystem,
    lightSystem: { attr: attr(light.root), stored: stored(light.store) }
  },
  view: dark.box.HP.viewFromHash(),
  age: {
    min: age(iso(2026, 10, 22, 14, 1), noon),
    h: age(iso(2026, 10, 22, 12, 0), noon),
    yesterday: age(iso(2026, 10, 22, 23, 0), iso(2026, 10, 23, 1, 0))
  }
}));
"""

_SURFACE_COLOR = re.compile(r"#(?:fff|ffffff|000|000000)\b|\b(?:white|black)\b", re.I)


def _theme_report() -> dict:
    proc = subprocess.run(
        ["node", "-e", _THEME, str(STATIC / "app.js")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_theme_follows_the_system_until_the_switch_stores_it():
    shell = APP_HTML.decode("utf-8")
    assert "atlas-theme.js" not in shell
    assert shell.index('src="/app.js"') < shell.index("signal.css")
    for mode in ("light", "dark", "system"):
        assert f'data-theme-mode="{mode}"' in shell
    out = _theme_report()
    assert out["fresh"]["dark"] == {"attr": "dark", "stored": None}
    assert out["fresh"]["light"] == {"attr": None, "stored": None}
    assert out["chosen"]["light"] == {"attr": "light", "stored": "light"}
    assert out["chosen"]["dark"]["attr"] == "dark"
    assert out["chosen"]["dark"]["stored"] == "dark"
    assert out["chosen"]["dark"]["pressed"] == {"light": "false", "dark": "true", "system": "false"}
    assert out["chosen"]["system"] == {"attr": "dark", "stored": "system"}
    assert out["chosen"]["lightSystem"] == {"attr": None, "stored": "system"}
    assert out["view"] == "messages"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_age_is_minutes_hours_then_yesterday_across_midnight():
    out = _theme_report()
    assert out["age"] == {"min": "59 min", "h": "3 h", "yesterday": "yesterday"}


def test_no_literal_white_or_black_surface_colours():
    for path in (STATIC / "app.css", STATIC / "views" / "messages.css"):
        css = path.read_text(encoding="utf-8").replace("white-space", "")
        assert _SURFACE_COLOR.search(css) is None, path.name


def test_eyebrows_are_ink_3_mono():
    app = (STATIC / "app.css").read_text(encoding="utf-8")
    msg = (STATIC / "views" / "messages.css").read_text(encoding="utf-8")
    for css, sel in ((app, r"\.hp \.eyebrow"), (msg, r"\.hp-msg \.eyebrow")):
        block = re.search(sel + r" \{([^}]*)\}", css)
        assert block, sel
        body = block.group(1)
        assert "var(--ink-3)" in body
        assert "var(--mono)" in body
        assert "uppercase" in body


def test_no_left_stripes_or_side_select_bars():
    # Morgan 10-23 22:54: no side select bars or left stripes anywhere. Selection is a filled pill; states are washes.
    for path in (STATIC / "app.css", STATIC / "views" / "messages.css"):
        css = path.read_text(encoding="utf-8")
        assert re.search(r"box-shadow:\s*inset\s+\d+px\s+0\s+0", css) is None, path.name
        assert re.search(r"border-left:\s*[2-9]px solid var\(--(amber|red|blue)", css) is None, path.name

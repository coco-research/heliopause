"""Useful / Not useful on Waiting-on-you cards. The click stays on this machine."""

import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from test_feedback import stored, vote
from test_write import served

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "atlas" / "static" / "views" / "now.js"
FIX = ROOT / "tests" / "fixtures" / "now.json"

_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const fixture = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));

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
var innerHTMLSets = 0;
Object.defineProperty(Node.prototype, "innerHTML", {
  set: function () { innerHTMLSets += 1; },
  get: function () { return ""; }
});
Node.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
Node.prototype.getAttribute = function (k) {
  return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
};
Node.prototype.addEventListener = function (type, fn) {
  (this.listeners[type] = this.listeners[type] || []).push(fn);
};
Node.prototype.click = function () {
  var list = (this.listeners.click || []).slice();
  for (var i = 0; i < list.length; i++) list[i]({});
};
function matches(node, sel) {
  sel = sel.trim();
  if (sel.charAt(0) === "[") {
    var name = sel.slice(1, -1).split("=")[0];
    return node.getAttribute(name) != null;
  }
  return node.tag === sel;
}
Node.prototype.querySelectorAll = function (sel) {
  var parts = String(sel).split(",");
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
  walk(this);
  return out;
};

function walk(node, acc) {
  acc.push(node);
  for (var i = 0; i < (node.children || []).length; i++) walk(node.children[i], acc);
  return acc;
}
function hasClass(node, name) {
  return (" " + (node.className || "") + " ").indexOf(" " + name + " ") >= 0;
}
function buttonsIn(node) {
  return walk(node, []).filter(function (n) { return n.tag === "button"; });
}

var document = {
  title: "",
  createElement: function (tag) { return new Node(tag); },
  createTextNode: function (text) {
    var node = new Node("#text");
    node._text = String(text);
    return node;
  },
  getElementById: function () { return null; },
  addEventListener: function () {},
  removeEventListener: function () {}
};
var root = new Node("div");
var pull = null;
var calls = [];
var refresh = 0;
var fail = false;
var data = JSON.parse(JSON.stringify(fixture));
function api(path, opts) {
  var method = opts && opts.method ? opts.method : "GET";
  var body = opts && opts.body !== undefined ? JSON.parse(JSON.stringify(opts.body)) : null;
  calls.push({ path: path, method: method, body: body });
  if (path === "/api/feedback") {
    return fail ? Promise.reject(new Error("down")) : Promise.resolve({ ok: true });
  }
  if (path === "/api/now") return Promise.resolve(JSON.parse(JSON.stringify(data)));
  return Promise.resolve({ ok: true });
}
var sandbox = {
  document: document,
  navigator: { clipboard: { writeText: function () { return Promise.resolve(); } } },
  setInterval: function (fn) { pull = fn; return 1; },
  clearInterval: function () { pull = null; },
  Date: Date,
  Intl: Intl,
  Promise: Promise,
  console: console,
  JSON: JSON,
  Object: Object,
  isNaN: isNaN
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "now.js" });

function flush() {
  return new Promise(function (resolve) { setImmediate(resolve); });
}
function ask() {
  var all = walk(root, []);
  for (var i = 0; i < all.length; i++) if (hasClass(all[i], "ask")) return all[i];
  return null;
}
function movingButtons() {
  var out = [];
  walk(root, []).forEach(function (node) {
    if (!hasClass(node, "msg")) return;
    buttonsIn(node).forEach(function (btn) { out.push(btn.textContent); });
  });
  return out;
}
function named(card, text) {
  var all = buttonsIn(card);
  for (var i = 0; i < all.length; i++) if (all[i].textContent === text) return all[i];
  return null;
}
function snap() {
  var card = ask();
  var approve = named(card, "Approve");
  var decline = named(card, "Decline");
  var h3 = null;
  var kids = walk(card, []);
  for (var i = 0; i < kids.length; i++) if (kids[i].tag === "h3") h3 = kids[i];
  return {
    summary: h3 ? h3.textContent : "",
    approve: approve ? approve.textContent : "",
    approveDisabled: !!(approve && approve.disabled),
    decline: decline ? decline.textContent : ""
  };
}

(async function () {
  var out = {};
  try {
    sandbox.HP.views.now.render(root, {
      api: api,
      refresh: function () { refresh += 1; }
    });
    await flush();
    var card = ask();
    if (!card) throw new Error("no ask card");
    out.askButtons = buttonsIn(card).map(function (b) { return b.textContent; });
    out.movingButtons = movingButtons();
    out.before = {
      useful: named(card, "Useful").getAttribute("aria-pressed"),
      not: named(card, "Not useful").getAttribute("aria-pressed")
    };
    var summary = snap().summary;
    calls = [];
    named(card, "Useful").click();
    await flush();
    out.onClick = calls.slice();
    out.pressed = named(ask(), "Useful").getAttribute("aria-pressed");
    out.other = named(ask(), "Not useful").getAttribute("aria-pressed");
    calls = [];
    named(ask(), "Useful").click();
    await flush();
    out.onRepeat = calls.slice();
    var old = named(ask(), "Useful");
    old._mark = 1;
    data.sentence = "Redrawn sentence for the poll.";
    calls = [];
    pull();
    await flush();
    var again = named(ask(), "Useful");
    out.redraw = {
      calls: calls.slice(),
      useful: again.getAttribute("aria-pressed"),
      not: named(ask(), "Not useful").getAttribute("aria-pressed"),
      rebuilt: again !== old && again._mark !== 1,
      sentence: root.textContent.indexOf("Redrawn sentence for the poll.") >= 0
    };
    fail = true;
    calls = [];
    named(ask(), "Not useful").click();
    await flush();
    var failed = ask();
    out.onFail = calls.slice();
    out.couldnt = failed.textContent.indexOf("Couldn't save") >= 0;
    out.couldntMoving = movingButtons().length === 0 && walk(root, []).filter(function (n) {
      return hasClass(n, "msg") && n.textContent.indexOf("Couldn't save") >= 0;
    }).length === 0;
    out.afterFail = {
      useful: named(failed, "Useful").getAttribute("aria-pressed"),
      not: named(failed, "Not useful").getAttribute("aria-pressed"),
      card: snap(),
      summary: summary
    };
    out.refresh = refresh;
    out.innerHTML = innerHTMLSets;
    if (root._hpStop) root._hpStop();
    process.stdout.write(JSON.stringify(out));
  } catch (err) {
    console.error(err && err.stack ? err.stack : err);
    if (root._hpStop) root._hpStop();
    process.exit(1);
  }
})();
"""


def _block_off_machine(monkeypatch):
    """Raise if a socket connects anywhere but 127.0.0.1. The page's own POST is allowed."""
    blocked = []
    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def host_of(address):
        if isinstance(address, tuple) and address:
            return address[0]
        return address

    def connect(self, address):
        host = host_of(address)
        if host != "127.0.0.1":
            blocked.append(host)
            raise OSError("blocked connection to %s" % host)
        return real_connect(self, address)

    def create_connection(address, *args, **kwargs):
        host = host_of(address)
        if host != "127.0.0.1":
            blocked.append(host)
            raise OSError("blocked connection to %s" % host)
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    return blocked


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_waiting_card_feedback_buttons():
    proc = subprocess.run(
        ["node", "-e", _HARNESS, str(FIX), str(JS)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    ask_id = json.loads(FIX.read_text(encoding="utf-8"))["needs_you"][0]["id"]
    body = {"kind": "ask", "subject": ask_id, "verdict": "useful"}
    assert out["askButtons"] == ["Approve", "Decline", "Useful", "Not useful"]
    assert out["movingButtons"] == []
    assert out["before"] == {"useful": "false", "not": "false"}
    assert out["onClick"] == [{"path": "/api/feedback", "method": "POST", "body": body}]
    assert list(out["onClick"][0]["body"]) == ["kind", "subject", "verdict"]
    assert out["pressed"] == "true" and out["other"] == "false"
    assert out["onRepeat"] == []
    assert out["redraw"]["calls"] == [{"path": "/api/now", "method": "GET", "body": None}]
    assert out["redraw"]["useful"] == "true" and out["redraw"]["not"] == "false"
    assert out["redraw"]["rebuilt"] is True and out["redraw"]["sentence"] is True
    assert out["onFail"] == [{"path": "/api/feedback", "method": "POST", "body": {**body, "verdict": "not-useful"}}]
    assert out["couldnt"] is True and out["couldntMoving"] is True
    assert out["afterFail"]["useful"] == "true" and out["afterFail"]["not"] == "false"
    assert out["afterFail"]["card"] == {
        "summary": out["afterFail"]["summary"],
        "approve": "Approve",
        "approveDisabled": False,
        "decline": "Decline",
    }
    assert out["afterFail"]["summary"]
    assert out["refresh"] == 0
    assert out["innerHTML"] == 0


def test_one_ask_click_writes_one_local_record(tmp_path, monkeypatch):
    blocked = _block_off_machine(monkeypatch)
    ask_id = json.loads(FIX.read_text(encoding="utf-8"))["needs_you"][0]["id"]
    with served(tmp_path) as server:
        assert vote(server, "ask", ask_id, "useful") == (201, {"ok": True})
        (row,) = stored(tmp_path)
        assert list(row) == ["ts", "kind", "subject", "verdict"]
        assert isinstance(row["ts"], int) and not isinstance(row["ts"], bool)
        assert (row["kind"], row["subject"], row["verdict"]) == ("ask", ask_id, "useful")
        assert blocked == []

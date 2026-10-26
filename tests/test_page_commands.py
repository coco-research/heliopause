"""User-visible commands match the INSTALL runbook: python3 -B -m atlas, never a bare heliopause."""

from __future__ import annotations

import ast
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from atlas.job_report import job_prompt
from atlas.roles import new_message, send

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "copy" / "install-commands.md"
NOW = ROOT / "atlas" / "static" / "views" / "now.js"
BARE = re.compile(r"\bheliopause (up|report|doctor|approve)\b")
_INSTALL = (
    "cd ~/Heliopause && python3 -B -m atlas up",
    "cd ~/Heliopause && python3 -B -m atlas doctor",
    "cd ~/Heliopause && python3 -B -m atlas report",
)

_BANNER = r"""
const fs = require("fs");
const vm = require("vm");

function Node(tag) {
  this.tag = tag || "#text";
  this.children = [];
  this.attrs = {};
  this._text = "";
  this.className = "";
  this.listeners = {};
}
Node.prototype.appendChild = function (child) {
  this.children.push(child);
  return child;
};
Node.prototype.removeChild = function (child) {
  var i = this.children.indexOf(child);
  if (i >= 0) this.children.splice(i, 1);
  return child;
};
Object.defineProperty(Node.prototype, "firstChild", {
  get: function () { return this.children[0] || null; }
});
Object.defineProperty(Node.prototype, "textContent", {
  get: function () {
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
Node.prototype.click = function () {
  var list = (this.listeners.click || []).slice();
  for (var i = 0; i < list.length; i++) list[i]({});
};

function walk(node, acc) {
  acc.push(node);
  for (var i = 0; i < node.children.length; i++) walk(node.children[i], acc);
  return acc;
}

var tick = null;
var written = [];
var selected = [];
var document = {
  title: "",
  createElement: function (tag) { return new Node(tag); },
  createTextNode: function (text) {
    var node = new Node("#text");
    node._text = String(text);
    return node;
  },
  createRange: function () {
    return {
      selectNodeContents: function (node) { selected.push(node); }
    };
  },
  getElementById: function () { return null; },
  addEventListener: function () {},
  removeEventListener: function () {}
};
function clipOk(value) {
  written.push(String(value));
  return Promise.resolve();
}
var sandbox = {
  document: document,
  navigator: { clipboard: { writeText: clipOk } },
  getSelection: function () {
    return { removeAllRanges: function () {}, addRange: function () {} };
  },
  setInterval: function (fn) { tick = fn; return 1; },
  clearInterval: function () { tick = null; },
  Date: Date,
  Intl: Intl,
  Promise: Promise,
  console: console,
  Math: Math,
  isNaN: isNaN,
  JSON: JSON,
  Object: Object
};
sandbox.HP = { views: {} };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), sandbox, { filename: "now.js" });

function flush() {
  return new Promise(function (resolve) { setImmediate(resolve); });
}
function payload(dir) {
  return {
    app_dir: dir,
    generated: "2026-10-22T14:24:00Z",
    sentence: "",
    counts: { messages_today: 0, tools_connected: 0, repos: 0 },
    needs_you: [],
    seats: { hq: [], lab: [], factory: [] },
    moving: [],
    github: false
  };
}
function copyButton(root) {
  var all = walk(root, []);
  for (var i = 0; i < all.length; i++) {
    if (all[i].tag === "button" && all[i].textContent === "Copy command") return all[i];
  }
  return null;
}
function chipOf(root) {
  var all = walk(root, []);
  for (var i = 0; i < all.length; i++) {
    if (all[i].tag === "code" && all[i].className === "cmd") return all[i];
  }
  return null;
}
function setClipboard(mode) {
  if (mode === "missing") {
    sandbox.navigator.clipboard = undefined;
    return;
  }
  if (mode === "reject") {
    sandbox.navigator.clipboard = {
      writeText: function () { return Promise.reject(new Error("denied")); }
    };
    return;
  }
  sandbox.navigator.clipboard = { writeText: clipOk };
}
async function bannerFor(dir, mode) {
  var step = 0;
  var root = new Node("div");
  written = [];
  selected = [];
  setClipboard(mode || "ok");
  sandbox.HP.views.now.render(root, {
    api: function () {
      step += 1;
      if (step === 1) return Promise.resolve(payload(dir));
      return Promise.reject(new Error("down"));
    }
  });
  await flush();
  tick();
  await flush();
  var chip = chipOf(root);
  var button = copyButton(root);
  if (!chip) throw new Error("no chip for " + dir);
  if (!button) throw new Error("no copy button for " + dir);
  var text = root.textContent;
  var parts = chip.children.map(function (node) { return { tag: node.tag, text: node.textContent }; });
  button.click();
  await flush();
  var out = {
    text: text,
    chip: chip.textContent,
    parts: parts,
    clipboard: written[0] || "",
    label: button.textContent,
    selected: selected.length === 1 && selected[0] === chip
  };
  if (root._hpStop) root._hpStop();
  return out;
}

(async function () {
  var home = await bannerFor("~/Heliopause", "ok");
  var opt = await bannerFor("/opt/hp", "ok");
  var missing = await bannerFor("~/Heliopause", "missing");
  var rejected = await bannerFor("/opt/hp", "reject");
  process.stdout.write(JSON.stringify({ home: home, opt: opt, missing: missing, rejected: rejected }));
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
"""


def _lines() -> list[str]:
    return FIX.read_text(encoding="utf-8").splitlines()


def _code_only(src: str) -> str:
    """Drop docstrings and full-line comments so a bare command may live only there."""
    tree = ast.parse(src)
    docs: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        value = getattr(first, "value", None)
        if isinstance(first, ast.Expr) and isinstance(value, ast.Constant) and isinstance(value.value, str):
            docs.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    kept = []
    for number, line in enumerate(src.splitlines(), 1):
        if number in docs or line.lstrip().startswith("#"):
            kept.append("")
        else:
            kept.append(line)
    return "\n".join(kept)


def test_install_fixture_is_the_three_runbook_lines() -> None:
    assert _lines() == list(_INSTALL)


def test_sources_pin_install_commands() -> None:
    lines = _lines()
    doctor, report = lines[1], lines[2]
    messages = (ROOT / "atlas" / "static" / "views" / "messages.js").read_text(encoding="utf-8")
    today = (ROOT / "atlas" / "static" / "views" / "today.js").read_text(encoding="utf-8")
    setup = (ROOT / "atlas" / "static" / "views" / "setup.js").read_text(encoding="utf-8")
    brief = (ROOT / "atlas" / "role_brief.py").read_text(encoding="utf-8")
    job = new_message("lab", "board", "factory", "job", "Fix the gate", repo="atlas")
    assert report in messages
    assert report in job_prompt(job)
    assert report in brief
    assert report in today
    assert doctor in setup


def test_no_bare_heliopause_command() -> None:
    paths = sorted((ROOT / "atlas" / "static" / "views").glob("*.js"))
    paths += [
        ROOT / "atlas" / "static" / "app.js",
        ROOT / "atlas" / "static" / "app.html",
        ROOT / "atlas" / "role_brief.py",
    ]
    hits = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in paths
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if BARE.search(line)
    ]
    assert hits == []
    code = _code_only((ROOT / "atlas" / "job_report.py").read_text(encoding="utf-8"))
    assert BARE.search(code) is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_offline_banner_copies_the_shown_up_line() -> None:
    up = _lines()[0]
    proc = subprocess.run(
        ["node", "-e", _BANNER, str(NOW)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    home = out["home"]
    assert home["chip"] == up
    assert home["clipboard"] == home["chip"]
    assert home["label"] == "Copied"
    assert home["parts"] == [
        {"tag": "span", "text": "cd ~/Heliopause &&"},
        {"tag": "#text", "text": " "},
        {"tag": "span", "text": "python3 -B -m atlas up"},
    ]
    assert "Heliopause stopped at" in home["text"]
    assert "This page stopped updating. Start it again:" in home["text"]
    assert "Reports sent while it was stopped may not arrive." in home["text"]
    assert "after it starts" not in home["text"]
    assert "doctor" not in home["text"]
    assert "Type:" not in home["text"]
    opt_up = "cd /opt/hp && python3 -B -m atlas up"
    opt = out["opt"]
    assert opt["chip"] == opt_up
    assert opt["clipboard"] == opt["chip"]
    assert "doctor" not in opt["text"]
    assert "Type:" not in opt["text"]
    for key in ("missing", "rejected"):
        row = out[key]
        assert row["selected"] is True
        assert row["label"] == "Copy command"
        assert row["clipboard"] == ""
        assert "doctor" not in row["text"]
        assert "Type:" not in row["text"]


def test_shown_path_under_home(monkeypatch, tmp_path: Path) -> None:
    from atlas.screens import _shown_path

    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert _shown_path(home / "x") == "~/x"


def test_shown_path_outside_home(monkeypatch, tmp_path: Path) -> None:
    from atlas.screens import _shown_path

    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    outside = tmp_path / "opt" / "hp"
    outside.mkdir(parents=True)
    assert _shown_path(outside) == str(outside.absolute())


def test_shown_path_space_is_quoted_absolute(monkeypatch, tmp_path: Path) -> None:
    from atlas.screens import _shown_path

    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    spaced = home / "my lab"
    quoted = _shown_path(spaced)
    assert quoted == shlex.quote(str(spaced.absolute()))
    assert "~" not in quoted


def test_report_command_marks_the_job_done(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    job = new_message("lab", "board", "factory", "job", "Fix the gate")
    send(lab, job)
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "atlas", "report", job["id"], "done", "--tests", "pass", "--lab", str(lab)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    saved = json.loads((lab / ".heliopause" / "inbox" / "factory" / f"{job['id']}.json").read_text(encoding="utf-8"))
    assert saved["state"] == "done"

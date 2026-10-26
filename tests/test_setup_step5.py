"""Setup step 5 (Ready) and GET /api/setup/check."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_alerts import get
from test_write import served

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "atlas" / "static" / "views" / "setup.js"
CSS = ROOT / "atlas" / "static" / "views" / "setup.css"
NONE = "I didn't find anything to flag in Hooks & checks or Repo standard."
HIDDEN = "HIDDEN-FINDING-LINE"

_HARNESS = r"""
global.window = global;
const view = require(process.argv[1]);
let innerHTMLSets = 0;
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
  Object.defineProperty(el, "innerHTML", {
    set: function () { innerHTMLSets += 1; },
    get: function () { return ""; },
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
const NONE = "I didn't find anything to flag in Hooks & checks or Repo standard.";
const HIDDEN = "HIDDEN-FINDING-LINE";
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
function h1(root) {
  const node = find(root, function (el) { return el.tagName === "H1"; });
  if (!node) throw new Error("no h1");
  return node.textContent;
}
function anchor(root, label) {
  const node = find(root, function (el) { return el.tagName === "A" && el.textContent === label; });
  if (!node) throw new Error("no link " + label);
  return node;
}
function api(check, finding) {
  const calls = { check: 0, finding: 0 };
  const fn = function (path) {
    if (path === "/api/setup/tools") return Promise.resolve(ONE);
    if (path === "/api/roles") return Promise.resolve({});
    if (path === "/api/setup/check") {
      calls.check += 1;
      return typeof check === "function" ? check() : Promise.resolve(check);
    }
    if (path === "/api/first-finding") {
      calls.finding += 1;
      return typeof finding === "function" ? finding() : Promise.resolve(finding);
    }
    return Promise.reject(new Error("unexpected " + path));
  };
  fn.calls = calls;
  return fn;
}
async function reach(check, finding) {
  const fn = api(check, finding);
  const root = makeEl("div");
  view.render(root, { api: fn });
  await click(button(root, "Find my tools"));
  await click(button(root, "Next: pick roles"));
  await click(button(root, "Next"));
  return { root: root, calls: fn.calls };
}
function assertNoLegacy(root) {
  if (root.textContent.indexOf("Next: connect your tools") >= 0) throw new Error("legacy text");
}
async function main() {
  const line = "Hooks still live in the lab.";
  const found = await reach(
    { ok: true, ms: 1, member: "Builder" },
    { finding: { kind: "hook", line: line, link: "Open Hooks & checks" }, none_line: NONE }
  );
  const root = found.root;
  if (para(root, "step").textContent !== "5 of 5") throw new Error("header");
  if (h1(root) !== "You're set up.") throw new Error("title " + h1(root));
  if (para(root, "lead").textContent !== "Here's one thing I found in your setup:") throw new Error("lead");
  if (para(root, "flag").textContent !== "◇ " + line) throw new Error("flag " + para(root, "flag").textContent);
  const mark = find(root, function (el) { return el.className === "mark"; });
  if (!mark || mark.textContent !== "◇") throw new Error("diamond");
  if (find(root, function (el) { return el.className === "found-link"; })) throw new Error("hooks link");
  if (para(root, "quiet").textContent !== "It works. Builder's queue answered in 1 second.") throw new Error("singular");
  assertNoLegacy(root);

  const roles = await reach(
    { ok: true, ms: 1000, member: "Builder" },
    { finding: { kind: "role", line: "A seat is empty.", link: "Show the change" }, none_line: NONE }
  );
  if (para(roles.root, "flag").textContent !== "◇ A seat is empty.") throw new Error("roles line");
  if (find(roles.root, function (el) { return el.className === "found-link"; })) throw new Error("roles link");
  if (para(roles.root, "quiet").textContent !== "It works. Builder's queue answered in 1 second.") throw new Error("1000ms");

  const repos = await reach(
    { ok: true, ms: 1001, member: "Builder" },
    { finding: { kind: "repo", line: "One repo has no standard.", link: "Open Repos" }, none_line: NONE }
  );
  if (para(repos.root, "lead").textContent !== "Here's one thing I found in your setup:") throw new Error("repos lead");
  if (para(repos.root, "flag").textContent !== "◇ One repo has no standard.") throw new Error("repos line");
  if (anchor(repos.root, "Open Repos").attrs.href !== "#/repos") throw new Error("repos href");
  if (para(repos.root, "quiet").textContent !== "It works. Builder's queue answered in 2 seconds.") throw new Error("plural");

  const none = await reach(
    { ok: true, ms: 2000, member: "Builder" },
    { finding: null, none_line: NONE }
  );
  if (h1(none.root) !== "You're set up.") throw new Error("none title");
  if (para(none.root, "none").textContent !== NONE) throw new Error("none line");
  if (none.root.textContent.indexOf("◇") >= 0) throw new Error("none has a shape");
  if (find(none.root, function (el) { return el.className === "flag" || el.className === "mark"; })) {
    throw new Error("none has a mark");
  }
  if (para(none.root, "quiet").textContent !== "It works. Builder's queue answered in 2 seconds.") throw new Error("none plural");

  const missed = await reach(
    { ok: true, ms: 1, member: "Builder" },
    function () { return Promise.reject(new Error("down")); }
  );
  if (para(missed.root, "none").textContent !== NONE) throw new Error("failed fetch none");
  if (missed.root.textContent.indexOf("◇") >= 0) throw new Error("failed fetch shape");
  if (para(missed.root, "quiet").textContent !== "It works. Builder's queue answered in 1 second.") {
    throw new Error("failed fetch still ok");
  }

  const badLine = "▲ Messages didn't come back from Builder's queue, so Heliopause can't carry jobs yet.";
  const help = "Run cd ~/Heliopause && python3 -B -m atlas doctor in a terminal, then try again.";
  const failed = await reach(
    { ok: false, ms: 4, member: "Builder" },
    { finding: { kind: "hook", line: HIDDEN, link: "Show the change" }, none_line: NONE }
  );
  if (para(failed.root, "step").textContent !== "5 of 5") throw new Error("fail header");
  if (h1(failed.root) !== "Almost set up.") throw new Error("fail title");
  if (para(failed.root, "bad").textContent !== badLine) throw new Error("bad " + para(failed.root, "bad").textContent);
  if (para(failed.root, "help").textContent !== help) throw new Error("help");
  if (failed.root.textContent.indexOf(HIDDEN) >= 0) throw new Error("finding shown");
  if (failed.root.textContent.indexOf("◇") >= 0) throw new Error("shape on failure");
  if (failed.root.textContent.indexOf("Here's one thing I found") >= 0) throw new Error("lead on failure");
  if (failed.root.textContent.indexOf("Show the change") >= 0) throw new Error("link on failure");
  const again = button(failed.root, "Try again");
  if (again.className.indexOf("btn-accent") < 0) throw new Error("try again not primary");
  if (anchor(failed.root, "Open Now").attrs.href !== "#/now") throw new Error("open now");
  if (failed.calls.check !== 1) throw new Error("checks " + failed.calls.check);
  await click(again);
  if (failed.calls.check !== 2) throw new Error("try again checks " + failed.calls.check);
  if (h1(failed.root) !== "Almost set up.") throw new Error("still failing");
  if (failed.root.textContent.indexOf(HIDDEN) >= 0) throw new Error("finding after retry");
  if (para(failed.root, "bad").textContent !== badLine) throw new Error("bad after retry");

  const down = await reach(
    function () { return Promise.reject(new Error("no route")); },
    { finding: { kind: "hook", line: HIDDEN, link: "Open Hooks & checks" }, none_line: NONE }
  );
  if (h1(down.root) !== "Almost set up.") throw new Error("reject title");
  if (para(down.root, "bad").textContent !== badLine) throw new Error("reject bad");
  if (down.root.textContent.indexOf(HIDDEN) >= 0) throw new Error("reject showed finding");

  if (innerHTMLSets !== 0) throw new Error("innerHTML " + innerHTMLSets);
  process.stdout.write("ok\n");
}
main().catch(function (err) {
  console.error(err && err.stack || err);
  process.exit(1);
});
"""


def _probe_names(root: Path) -> list[str]:
    names = []
    for path in root.rglob("*"):
        if path.is_file() and ("setup-check-" in path.name or ".atlas-" in path.name):
            names.append(path.name)
    return names


def _as_lab(monkeypatch, lab: Path) -> None:
    monkeypatch.setattr("atlas.__main__.LAB", True)
    monkeypatch.setattr("atlas.__main__.LAB_DIR", lab)


def test_setup_js_has_no_queue_prompt():
    text = JS.read_text(encoding="utf-8")
    assert text.count("can you see your queue") == 0
    assert "Send a test message." not in text
    css = CSS.read_text(encoding="utf-8")
    assert ".hp-setup .mark" in css and "var(--amber)" in css
    assert ".hp-setup .none" in css and "var(--ink-2)" in css
    assert ".hp-setup .quiet" in css and "var(--ink-3)" in css
    assert ".hp-setup .bad" in css and "var(--red)" in css


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_setup_step5_view():
    check = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    proc = subprocess.run(["node", "-e", _HARNESS, str(JS)], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


def test_setup_check_ok_on_tmp_lab_leaves_no_file(tmp_path, monkeypatch):
    _as_lab(monkeypatch, tmp_path)
    with served(tmp_path) as server:
        status, raw = get(server, "/api/setup/check")
    assert status == 200
    body = json.loads(raw)
    assert set(body) == {"ok", "ms", "member"}
    assert body["ok"] is True
    assert type(body["ms"]) is int and body["ms"] >= 0
    assert body["member"] == "Builder"
    factory = tmp_path / ".heliopause" / "inbox" / "factory"
    assert factory.is_dir()
    assert list(factory.iterdir()) == []
    assert _probe_names(tmp_path) == []


def test_setup_check_not_ok_when_inbox_cannot_be_written(tmp_path, monkeypatch):
    inbox = tmp_path / ".heliopause" / "inbox"
    inbox.mkdir(parents=True)
    blocker = inbox / "factory"
    blocker.write_bytes(b"not a directory\n")
    _as_lab(monkeypatch, tmp_path)
    with served(tmp_path) as server:
        status, raw = get(server, "/api/setup/check")
    assert status == 200
    body = json.loads(raw)
    assert set(body) == {"ok", "ms", "member"}
    assert body["ok"] is False
    assert type(body["ms"]) is int and body["ms"] >= 0
    assert body["member"] == "Builder"
    assert blocker.read_bytes() == b"not a directory\n"
    assert blocker.is_file()
    assert _probe_names(tmp_path) == []

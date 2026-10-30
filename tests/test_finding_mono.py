"""Setup's finding line: a backticked command is one mono span, inside one sentence span."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "atlas" / "static" / "views" / "setup.js"
COMMAND = "atlas plugin install --host cursor"
LINE = (
    "Cursor is connected but has no Heliopause hook, so its work won't show here. "
    f"Run `{COMMAND}` to add it."
)
PLAIN = "Hooks still live in the lab."
OPEN = "See the `file"
TAIL = f"Run `{COMMAND}` then a stray ` mark"

_HARNESS = r"""
global.window = global;
const view = require(process.argv[1]);
const lines = JSON.parse(process.argv[2]);
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
    set: function () { throw new Error("innerHTML"); },
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
  if (!node) throw new Error("no button " + label + " :: " + root.textContent.slice(0, 240));
  return node;
}
function click(node) {
  return Promise.resolve(node.listeners.click[0]());
}
function para(root, className) {
  const node = find(root, function (el) { return el.tagName === "P" && el.className === className; });
  if (!node) throw new Error("no p." + className + " :: " + root.textContent.slice(0, 240));
  return node;
}
function api(line) {
  return function (path) {
    if (path === "/api/setup/tools") return Promise.resolve(ONE);
    if (path === "/api/roles") return Promise.resolve({});
    if (path === "/api/setup/check") return Promise.resolve({ ok: true, ms: 1, member: "Builder" });
    if (path === "/api/first-finding") {
      return Promise.resolve({ finding: { kind: "hook", line: line, link: "Open Hooks & checks" }, none_line: "none" });
    }
    return Promise.reject(new Error("unexpected " + path));
  };
}
async function reach(line) {
  const root = makeEl("div");
  view.render(root, { api: api(line) });
  await click(button(root, "Find my tools"));
  await click(button(root, "Next: pick roles"));
  await click(button(root, "Next"));
  return root;
}
function shot(root) {
  const flag = para(root, "flag");
  const kids = flag.children;
  const elements = [];
  for (let i = 0; i < kids.length; i++) if (kids[i].tagName !== "#TEXT") elements.push(kids[i]);
  const monos = [];
  const all = walk(flag, []);
  for (let i = 0; i < all.length; i++) {
    const el = all[i];
    if (el.tagName === "SPAN" && el.className === "mono") {
      monos.push({ text: el.textContent, inSecond: !!elements[1] && el.parent === elements[1] });
    }
  }
  return {
    children: kids.length,
    elements: elements.length,
    firstClass: elements[0] ? elements[0].className : "",
    secondTag: elements[1] ? elements[1].tagName : "",
    secondClass: elements[1] ? elements[1].className : null,
    monos: monos,
    text: flag.textContent,
  };
}
(async function () {
  const out = {
    command: shot(await reach(lines.command)),
    plain: shot(await reach(lines.plain)),
    open: shot(await reach(lines.open)),
    tail: shot(await reach(lines.tail)),
  };
  process.stdout.write(JSON.stringify(out));
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_finding_backticks_render_as_one_mono_span():
    check = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    proc = subprocess.run(
        ["node", "-e", _HARNESS, str(JS), json.dumps({
            "command": LINE, "plain": PLAIN, "open": OPEN, "tail": TAIL,
        })],
        capture_output=True, text=True, timeout=20,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    command = out["command"]
    assert command["children"] == 2
    assert command["elements"] == 2
    assert command["firstClass"] == "mark"
    assert command["secondTag"] == "SPAN"
    assert command["secondClass"] == ""
    assert command["monos"] == [{"text": COMMAND, "inSecond": True}]
    assert "`" not in command["text"]
    assert command["text"] == "\u25c7 " + LINE.replace("`", "")

    plain = out["plain"]
    assert plain["children"] == 2
    assert plain["elements"] == 2
    assert plain["monos"] == []
    assert plain["text"] == "\u25c7 " + PLAIN
    assert "`" not in plain["text"]

    opened = out["open"]
    assert opened["monos"] == []
    assert opened["text"] == "\u25c7 " + OPEN
    assert "`" in opened["text"]

    tail = out["tail"]
    assert tail["monos"] == [{"text": COMMAND, "inSecond": True}]
    assert tail["text"] == "\u25c7 " + TAIL.replace("`", "", 2)
    assert tail["text"].count("`") == 1

"""Setup view: registration, project field, and the no-tool empty state."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "atlas" / "static" / "views" / "setup.js"

_HARNESS = r"""
global.window = global;
const view = require(process.argv[1]);
if (!global.HP || !global.HP.views || !global.HP.views.setup) throw new Error("not registered");
if (global.HP.views.setup.render !== view.render) throw new Error("render mismatch");

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

const PLACEHOLDER = "e.g. A small web shop: api-server is the backend, web-app is the storefront.";
function row(id, name, found) {
  return { id: id, name: name, found: found, cli: found ? "/bin/" + id : null, app: null,
    config_dir: null, install_url: "https://example.test/" + id };
}
const ONE = {
  tools: [row("claude-code", "Claude Code", true), row("cursor", "Cursor", false),
    row("opencode", "OpenCode", false)],
  supported: ["claude-code", "cursor", "codex", "grok"],
  suggested: { hq: "you", lab: "claude-code", factory: ["claude-code"] },
};
const J3 = {
  tools: [row("claude-code", "Claude Code", false), row("cursor", "Cursor", false),
    row("codex", "Codex", false), row("grok", "Grok Build", false), row("opencode", "OpenCode", true)],
  supported: ["claude-code", "cursor", "codex", "grok"],
  suggested: null,
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
function apiFor(payload, posts) {
  return function (path, opts) {
    if (path === "/api/setup/tools") return Promise.resolve(payload);
    if (path === "/api/roles") {
      posts.push(JSON.parse(JSON.stringify(opts.body)));
      return Promise.resolve({});
    }
    return Promise.reject(new Error("unexpected " + path));
  };
}
function field(root) {
  const area = find(root, function (el) { return el.tagName === "TEXTAREA"; });
  if (!area) throw new Error("no project field");
  if (area.attrs.placeholder !== PLACEHOLDER) throw new Error("placeholder attr");
  return area;
}
async function saveWith(payload, value) {
  const posts = [];
  const root = makeEl("div");
  view.render(root, { api: apiFor(payload, posts) });
  if (root.textContent.indexOf("1 of 5") < 0) throw new Error("step header");
  await click(button(root, "Find my tools"));
  await click(button(root, "Next: pick roles"));
  const area = field(root);
  area.value = value;
  await click(button(root, "Next"));
  if (posts.length !== 1) throw new Error("posts " + posts.length);
  return { posts: posts, root: root };
}
async function main() {
  const empty = await saveWith(ONE, "");
  const body = empty.posts[0];
  if (Object.prototype.hasOwnProperty.call(body, "goal")) throw new Error("empty field posted a goal");
  if (JSON.stringify(body).indexOf(PLACEHOLDER) >= 0) throw new Error("placeholder posted");
  if (!body.roles || body.roles.lab !== "claude-code" || body.roles.factory[0] !== "claude-code") {
    throw new Error("roles " + JSON.stringify(body));
  }
  if (empty.root.textContent.indexOf("5 of 5") < 0) throw new Error("after save step");
  if (empty.root.textContent.indexOf("Almost set up.") < 0) throw new Error("after save");
  if (empty.root.textContent.indexOf("Next: connect your tools") >= 0) throw new Error("connect line");
  const link = find(empty.root, function (el) { return el.tagName === "A" && el.attrs.href === "#/now"; });
  if (!link) throw new Error("no now link");

  const held = await saveWith(ONE, PLACEHOLDER);
  if (Object.prototype.hasOwnProperty.call(held.posts[0], "goal")) throw new Error("placeholder became a goal");
  if (JSON.stringify(held.posts[0]).indexOf("e.g.") >= 0) throw new Error("placeholder text posted");

  const blank = await saveWith(ONE, "  \n  ");
  if (Object.prototype.hasOwnProperty.call(blank.posts[0], "goal")) throw new Error("blank field posted a goal");

  const typed = await saveWith(ONE, "  A shop  ");
  if (typed.posts[0].goal !== "A shop") throw new Error("goal " + JSON.stringify(typed.posts[0]));

  const root = makeEl("div");
  view.render(root, { api: apiFor(J3, []) });
  await click(button(root, "Find my tools"));
  const text = root.textContent;
  if (text.indexOf("No supported tool on this machine yet.") < 0) throw new Error("j3 title");
  if (text.indexOf("Heliopause works with Claude Code, Cursor, Codex and Grok. Install one of them, then check again. Heliopause never installs a tool.") < 0) {
    throw new Error("j3 lead");
  }
  if (text.indexOf("Found. Not supported yet.") < 0) throw new Error("unsupported copy");
  if (text.indexOf("Claude Code: Not found on this machine") < 0) throw new Error("missing copy");
  button(root, "Back");
  button(root, "Check again");
  const title = find(root, function (el) { return el.tagName === "H1"; });
  if (!title || title._text !== "No supported tool on this machine yet.") throw new Error("title not text");
  if (innerHTMLSets !== 0) throw new Error("innerHTML " + innerHTMLSets);
  process.stdout.write("ok\n");
}
main().catch(function (err) {
  console.error(err && err.stack || err);
  process.exit(1);
});
"""


def test_setup_source_sets_text_with_text_content():
    text = JS.read_text(encoding="utf-8")
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert banned not in text
    assert "textContent" in text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.journey
def test_setup_view_project_field_and_empty_state():
    """J3: with no supported tool, setup says so and pushes no roles."""
    check = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    proc = subprocess.run(["node", "-e", _HARNESS, str(JS)], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"

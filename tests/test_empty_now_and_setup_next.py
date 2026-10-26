"""Empty Now when nothing has checked in, and setup step 3's Next button."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from atlas.screens import now

ROOT = Path(__file__).resolve().parents[1]
SETUP_JS = ROOT / "atlas" / "static" / "views" / "setup.js"

_NONE = "Nothing yet. No tool has checked in."
_IDLE = "Nothing yet. Your tools are connected and idle."

# Same DOM stub and click path as tests/test_setup_view.py, stopped on step 3.
_STEP3 = r"""
global.window = global;
function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(), className: "", children: [], disabled: false,
    value: "", checked: false, type: "", id: "", attrs: {}, listeners: {}, _text: null,
  };
  el.appendChild = function (child) { child.parent = el; el.children.push(child); return child; };
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
  createTextNode: function (s) { const n = makeEl("#text"); n.textContent = s; return n; },
  querySelector: function () { return null; },
  head: makeEl("head"),
};
const view = require(process.argv[1]);
function walk(node, acc) {
  acc.push(node);
  for (let i = 0; i < (node.children || []).length; i++) walk(node.children[i], acc);
  return acc;
}
function find(root, pred) {
  const all = walk(root, []);
  for (let i = 0; i < all.length; i++) if (pred(all[i])) return all[i];
  return null;
}
function button(root, label) {
  const node = find(root, function (el) { return el.tagName === "BUTTON" && el.textContent === label; });
  if (!node) throw new Error("no button " + label);
  return node;
}
const ONE = {
  tools: [{ id: "claude-code", name: "Claude Code", found: true, cli: "/bin/claude-code",
    app: null, config_dir: null, install_url: "https://example.test/claude-code" }],
  supported: ["claude-code", "cursor", "codex", "grok"],
  suggested: { hq: "you", lab: "claude-code", factory: ["claude-code"] },
};
async function main() {
  const root = makeEl("div");
  view.render(root, {
    api: function (path) {
      if (path === "/api/setup/tools") return Promise.resolve(ONE);
      return Promise.reject(new Error("unexpected " + path));
    },
  });
  await Promise.resolve(button(root, "Find my tools").listeners.click[0]());
  await Promise.resolve(button(root, "Next: pick roles").listeners.click[0]());
  if (root.textContent.indexOf("3 of 5") < 0) throw new Error("not step 3");
  const accent = find(root, function (el) {
    return el.tagName === "BUTTON" && el.className === "btn btn-accent";
  });
  if (!accent || accent.textContent !== "Next") {
    throw new Error("step 3 label " + (accent && accent.textContent));
  }
  process.stdout.write("ok\n");
}
main().catch(function (err) {
  console.error(err && err.stack || err);
  process.exit(1);
});
"""


def _card(lab: Path) -> None:
    """One valid cursor card, same shape as tests/test_screens.py."""
    folder = lab / ".heliopause" / "cards" / "cursor"
    folder.mkdir(parents=True)
    doc = {
        "repo": "api",
        "session": "s",
        "state": "idle",
        "tool": "cursor",
        "ts": "2026-10-22T15:59:00Z",
    }
    (folder / "s.json").write_text(json.dumps(doc, sort_keys=True) + "\n", encoding="utf-8")


def test_empty_now_sentence_with_and_without_a_tool(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    lab.mkdir()
    assert now(lab)["sentence"] == _NONE
    _card(lab)
    assert now(lab)["sentence"] == _IDLE


def test_setup_js_drops_next_connect() -> None:
    text = SETUP_JS.read_text(encoding="utf-8")
    assert "Next: connect" not in text
    start = text.index("function paintPick")
    step3 = text[start:text.index("function paintReady")]
    assert 'btn("Next", "btn btn-accent", save)' in step3


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_setup_step3_button_label_is_next() -> None:
    check = subprocess.run(["node", "--check", str(SETUP_JS)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    proc = subprocess.run(
        ["node", "-e", _STEP3, str(SETUP_JS)], capture_output=True, text=True, timeout=20
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"

"""Repos, messages and today load into one page: no shared top-level names."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPOS = ROOT / "atlas" / "static" / "views" / "repos.js"
MESSAGES = ROOT / "atlas" / "static" / "views" / "messages.js"
TODAY = ROOT / "atlas" / "static" / "views" / "today.js"

_NODE = shutil.which("node") is None

# node -e, same idea as test_today_views._HARNESS. One vm context stands in for the page.
_SCRIPT = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const context = {
  console: console,
  HP: {
    toolName: function (id) {
      var names = { "claude-code": "Claude Code", cursor: "Cursor", codex: "Codex", grok: "Grok", you: "You" };
      if (id == null || id === "") return "";
      return Object.prototype.hasOwnProperty.call(names, id) ? names[id] : String(id);
    },
    age: function () { return "20 min"; },
    setPrivacy: function () {}
  },
  document: {
    hidden: false,
    querySelector: function () { return null; },
    createElement: function () { return {}; },
    head: { appendChild: function () {} },
    addEventListener: function () {}
  },
  setInterval: function () { return 1; },
  clearInterval: function () {}
};
context.window = context;
vm.createContext(context);

const before = Object.getOwnPropertyNames(context);
const files = [process.argv[1], process.argv[2], process.argv[3]];
for (let i = 0; i < files.length; i++) {
  vm.runInContext(fs.readFileSync(files[i], "utf8"), context, { filename: files[i] });
}
const after = Object.getOwnPropertyNames(context);
const leaked = after.filter(function (name) { return before.indexOf(name) < 0; });
let failed = false;
try {
  assert.deepStrictEqual(after, before);
} catch (err) {
  console.error("leaked: " + (leaked.join(", ") || "(none)"));
  failed = true;
}

function pathsFor(view) {
  const paths = [];
  view.render({}, {
    api: function (path) {
      paths.push(path);
      return new Promise(function () {});
    }
  });
  return paths;
}

const asked = {
  repos: pathsFor(context.HP.views.repos),
  messages: pathsFor(context.HP.views.messages),
  today: pathsFor(context.HP.views.today)
};
try {
  assert.deepStrictEqual(asked.repos, ["/api/repos"]);
  assert.deepStrictEqual(asked.messages, ["/api/messages"]);
  assert.deepStrictEqual(asked.today, ["/api/today"]);
} catch (err) {
  console.error(err && err.stack ? err.stack : err);
  failed = true;
}
if (failed) process.exit(1);
"""


@pytest.mark.skipif(_NODE, reason="node missing")
def test_views_share_one_page_without_leaking() -> None:
    proc = subprocess.run(
        ["node", "-e", _SCRIPT, str(REPOS), str(MESSAGES), str(TODAY)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout

"""The Budget panel: source locks the spec, and one node run renders fake payloads."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "atlas" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "graph.css").read_text(encoding="utf-8")
JS = (STATIC / "graph.js").read_text(encoding="utf-8")


def _media(query):
    at = CSS.index(query)
    open_at = CSS.index("{", at)
    depth = 0
    for i in range(open_at, len(CSS)):
        if CSS[i] == "{":
            depth += 1
        elif CSS[i] == "}":
            depth -= 1
            if depth == 0:
                return CSS[at:i + 1]
    raise AssertionError(query)


def test_budget_section_html():
    graph = INDEX.index('<section class="graph"')
    budget = INDEX.index('<section id="budget" class="budget" aria-labelledby="budget-h">')
    road = INDEX.index('<section id="roadmap" class="roadmap" aria-labelledby="roadmap-h">')
    below = INDEX.index('<div class="below"')
    assert graph < below < budget < road  # Agents, then Spend, then Roadmap
    assert '<h2 id="budget-h">Spend</h2>' in INDEX
    assert '<div id="budget-list"></div>' in INDEX
    assert "style=" not in INDEX


def test_budget_heading_matches_roadmap():
    h2 = re.search(r"\.budget h2 \{([^}]*)\}", CSS).group(1)
    assert "font-size: 17px" in h2 and "font-weight: 700" in h2
    assert h2.count("1.5px solid var(--rule)") == 2


def test_budget_breakpoints():
    wide = _media("@media (width >= 2200px)")
    assert "problems budget" in wide
    assert "grid-area: budget" in wide and "680px" in wide and "width: 100%" in wide
    assert ".budget h2" in wide and "border-top: 0" in wide
    phone = _media("@media (max-width: 640px)")
    assert ".budget-row { grid-template-columns: 96px minmax(0, 1fr); }" in phone
    assert ".budget-meter { display: none; }" in phone
    assert ".budget { max-width: 100%; }" in phone
    assert "96px minmax(0, 1fr)" in phone
    assert "ellipsis" not in phone
    used_phone = re.search(r"\.budget-used \{([^}]*)\}", phone).group(1)
    assert "ellipsis" not in used_phone
    assert "white-space: normal" in used_phone
    asof_phone = re.search(r"\.budget-asof \{([^}]*)\}", phone).group(1)
    assert "white-space: normal" in asof_phone


def test_budget_row_tokens():
    row = re.search(r"\.budget-row \{([^}]*)\}", CSS).group(1)
    assert re.search(r"grid-template-columns: ([^;]+);", row).group(1) == "140px minmax(0, 1fr) 120px 128px"
    assert "min-height: 40px" in row and "var(--line)" in row
    used = re.search(r"\.budget-used \{([^}]*)\}", CSS).group(1)
    assert "tabular-nums" not in used and "var(--font)" in used
    assert "var(--mono)" not in used
    track = re.search(r"\.budget-track \{([^}]*)\}", CSS).group(1)
    assert "height: 4px" in track and "var(--line)" in track and "120px" in track
    fill = re.search(r"\.budget-fill \{([^}]*)\}", CSS).group(1)
    assert "var(--ink-3)" in fill
    red = re.search(r"\.budget-fill\.red \{([^}]*)\}", CSS).group(1)
    assert "var(--red)" in red and "box-shadow: 2px 0 0 var(--red)" in red
    over = re.search(r"\.budget-over \{([^}]*)\}", CSS).group(1)
    assert "var(--red)" in over and "font-weight: 550" in over
    tick = re.search(r"\.budget-tick \{([^}]*)\}", CSS).group(1)
    assert "width: 1px" in tick and "var(--ink)" in tick
    sub = re.search(r"\.budget-sub \{([^}]*)\}", CSS).group(1)
    assert "font-size: 12px" in sub and "var(--ink-3)" in sub
    asof = re.search(r"\.budget-asof \{([^}]*)\}", CSS).group(1)
    assert "var(--mono)" in asof and "var(--ink-3)" in asof and "text-align: right" in asof
    assert ".budget-row.is-tall { padding-block: 4px; }" in CSS
    assert "transition" not in CSS[CSS.index(".budget {"):CSS.index("/* Roadmap.")]


def test_budget_source():
    assert "innerHTML" not in JS
    assert '"/api/budget"' in JS
    tick = JS[JS.index("function tick("):JS.index("function problemRow(")]
    assert "loadBudget(ctl.signal)" in tick
    render = JS[JS.index("function render("):JS.index("function tick(")]
    assert "renderBudget(" in render
    assert re.search(r"Boss", JS) is None
    assert re.search(r"Boss", CSS) is None
    assert re.search(r"Boss", INDEX) is None


_DRIVER = r"""
function makeDocument() {
  const ids = Object.create(null);
  function walk(n, out) {
    out.push(n);
    const kids = n._kids || [];
    for (let i = 0; i < kids.length; i++) walk(kids[i], out);
    return out;
  }
  function matches(n, sel) {
    const cls = " " + (n.className || "") + " ";
    if (sel.charAt(0) === ".") {
      const parts = sel.slice(1).split(".");
      for (let i = 0; i < parts.length; i++) {
        if (cls.indexOf(" " + parts[i] + " ") < 0) return false;
      }
      return true;
    }
    return n.tag === sel;
  }
  function make(tag) {
    const kids = [];
    const listeners = {};
    const attrs = {};
    const n = { tag: tag, className: "", textContent: "", hidden: false, type: "", style: {}, parentNode: null, firstChild: null, _kids: kids };
    function sync() { n.firstChild = kids[0] || null; }
    n.setAttribute = function (k, v) { attrs[k] = String(v); };
    n.getAttribute = function (k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; };
    n.appendChild = function (c) { kids.push(c); if (c) c.parentNode = n; sync(); return c; };
    n.removeChild = function (c) {
      const i = kids.indexOf(c);
      if (i >= 0) kids.splice(i, 1);
      if (c) c.parentNode = null;
      sync();
      return c;
    };
    n.addEventListener = function (type, fn) { (listeners[type] = listeners[type] || []).push(fn); };
    n.querySelectorAll = function (sel) {
      const all = walk(n, []);
      const out = [];
      for (let i = 0; i < all.length; i++) if (all[i] !== n && matches(all[i], sel)) out.push(all[i]);
      return out;
    };
    n.querySelector = function (sel) { return n.querySelectorAll(sel)[0] || null; };
    Object.defineProperty(n, "id", {
      get: function () { return attrs.id || ""; },
      set: function (v) {
        if (attrs.id && ids[attrs.id] === n) delete ids[attrs.id];
        attrs.id = String(v);
        ids[attrs.id] = n;
      }
    });
    return n;
  }
  const document = {
    activeElement: null,
    getElementById: function (id) { return ids[id] || null; },
    createElement: function (tag) { return make(tag); },
    createTextNode: function (text) {
      const tn = make("#text");
      tn.textContent = String(text);
      return tn;
    }
  };
  document.body = make("body");
  return document;
}
function clsHas(n, name) { return (" " + (n.className || "") + " ").indexOf(" " + name + " ") >= 0; }
function child(n, name) {
  const kids = n._kids || [];
  for (let i = 0; i < kids.length; i++) if (clsHas(kids[i], name)) return kids[i];
  return null;
}
function dump() {
  const kids = document.getElementById("budget-list")._kids || [];
  const rows = [];
  for (let i = 0; i < kids.length; i++) {
    const n = kids[i];
    const pool = child(n, "budget-pool");
    const used = child(n, "budget-used");
    const sentence = child(used, "budget-sentence");
    const over = child(sentence, "budget-over");
    const text = child(sentence, "budget-text");
    const sub = child(used, "budget-sub");
    const meter = child(n, "budget-meter");
    const track = meter ? child(meter, "budget-track") : null;
    const fill = track ? child(track, "budget-fill") : null;
    const tick = track ? child(track, "budget-tick") : null;
    const noAllowance = meter ? child(meter, "budget-no-allowance") : null;
    const asof = child(n, "budget-asof");
    rows.push({
      tag: n.tag,
      className: n.className,
      tall: clsHas(n, "is-tall"),
      pool: pool ? pool.textContent : "",
      over: over ? over.textContent : null,
      sentence: text ? text.textContent : "",
      sub: sub ? sub.textContent : null,
      noAllowance: noAllowance ? noAllowance.textContent : null,
      track: track ? true : false,
      fill: fill ? { width: fill.style.width, red: clsHas(fill, "red") } : null,
      tick: tick ? tick.style.left : null,
      asof: asof ? asof.textContent : ""
    });
  }
  return rows;
}
var document = makeDocument();
var budgetPayload = null;
var budgetDrawn = "";
var host = document.createElement("div");
host.id = "budget-list";
document.body.appendChild(host);

var payload = { pools: [
  { id: "claude", name: "Claude", unit: "pts", used: 7.8, allowance: 10.5, over: false, as_of: 1791331200, old: false, extra: { week_pct: 63, resets_at: "2026-10-10T23:00:00Z" } },
  { id: "grok", name: "Grok", unit: "tokens", used: 62000000, allowance: 130000000, over: false, as_of: 1791331200, old: false, extra: { target: 65000000 } },
  { id: "glm", name: "GLM", unit: "credits", used: 1240, allowance: 3600, over: true, as_of: 1791331200, old: false, extra: { five_hour: { used: 363, of: 2000 } } },
  { id: "deepseek", name: "DeepSeek", unit: "usd", used: 0, allowance: 0, over: false, as_of: 1791331200, old: true, extra: { allowance_until: "2026-10-08T00:00:00Z" } },
  { id: "apikeyfan", name: "apikey.fan", unit: "usd", used: 0, allowance: 0, over: false, as_of: 1791331200, old: false, extra: {} },
  { id: "default-pool", name: "Default pool", unit: "weekly_pct", used: 33, allowance: null, over: false, as_of: 1791331200, old: false, extra: {} }
] };

renderBudget(payload);
var first = dump();
var firstKids = host._kids.slice();
renderBudget(payload);
var rebuiltSameStamp = host._kids[0] !== firstKids[0];

payload.pools[4].allowance = 1.45;
renderBudget(payload);
var afterEdit = dump();

var emptyPayload = { pools: [
  { id: "claude", name: "Claude", unit: "pts", used: null, allowance: 10.5, over: false, as_of: null, old: false, extra: { week_pct: null, resets_at: null } }
] };
renderBudget(emptyPayload);
var emptyDump = dump();

console.log(JSON.stringify({ first: first, rebuiltSameStamp: rebuiltSameStamp, afterEdit: afterEdit, empty: emptyDump }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_budget_panel_runs():
    helpers = JS[JS.index("function missing"):JS.index("function svgEl")]
    budget = JS[JS.index("function formatReset"):JS.index("function roadmapCmp")]
    assert "function el(" in helpers and "function clear(" in helpers
    assert "function renderBudget(" in budget and "function roadmapCmp" not in budget
    script = (
        "const vm=require('vm'),code=process.argv[1];"
        "const helpers=code.slice(code.indexOf('function missing'),code.indexOf('function svgEl'));"
        "const budget=code.slice(code.indexOf('function formatReset'),code.indexOf('function roadmapCmp'));"
        "const ctx=vm.createContext({console:console,Intl:Intl,Date:Date,Math:Math,JSON:JSON,Number:Number,String:String,isFinite:isFinite,isNaN:isNaN});"
        "vm.runInContext(helpers+'\\n'+budget+'\\n'+process.argv[2],ctx);"
    )
    proc = subprocess.run(["node", "-e", script, JS, _DRIVER], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    first = out["first"]
    assert len(first) == 6
    assert [r["pool"] for r in first] == ["Claude", "Grok", "GLM", "DeepSeek", "apikey.fan", "Default pool"]

    # Claude: "7.8 of 10.5 pts", secondary "week 63% · resets Sat 19:00", 2 lines tall, meter fill 74.3%
    claude = first[0]
    assert claude["sentence"] == "7.8 of 10.5 pts"
    assert claude["sub"] == "week 63% \u00b7 resets Sat 19:00"
    assert claude["tall"] is True
    assert claude["track"] is True and claude["fill"] == {"width": "74.3%", "red": False}
    assert claude["asof"] == "as of 20:00"

    # Grok: "62M of 130M ceiling", tick at 50%
    grok = first[1]
    assert grok["sentence"] == "62M of 130M ceiling"
    assert grok["sub"] is None
    assert grok["tall"] is False
    assert grok["track"] is True and grok["fill"] == {"width": "47.7%", "red": False}
    assert grok["tick"] == "50%"
    assert grok["asof"] == "as of 20:00"

    # GLM: "1,240 of 3,600 credits", over -> "▲ Over " and red fill, secondary line
    glm = first[2]
    assert glm["over"] == "\u25b2 Over "
    assert glm["sentence"] == "1,240 of 3,600 credits"
    assert glm["sub"] == "5 h window 363 of 2,000"
    assert glm["tall"] is True
    assert glm["track"] is True and glm["fill"] == {"width": "34.4%", "red": True}
    assert glm["asof"] == "as of 20:00"

    # DeepSeek: "$0.00 · $0 allowance until 10-08", old -> "as of 20:00 · old"
    deepseek = first[3]
    assert deepseek["sentence"] == "$0.00 \u00b7 $0 allowance until 10-08"
    assert deepseek["sub"] is None
    assert deepseek["track"] is True and deepseek["fill"] == {"width": "0%", "red": False}
    assert deepseek["asof"] == "as of 20:00 \u00b7 old"

    # apikey.fan with allowance 0: "$0.00 · $0 allowance"
    apikey = first[4]
    assert apikey["sentence"] == "$0.00 \u00b7 $0 allowance"
    assert apikey["sub"] is None
    assert apikey["track"] is True and apikey["fill"] == {"width": "0%", "red": False}
    assert apikey["asof"] == "as of 20:00"

    # Default pool: "33% of the week", no meter, "no allowance set"
    default_pool = first[5]
    assert default_pool["sentence"] == "33% of the week"
    assert default_pool["sub"] is None
    assert default_pool["noAllowance"] == "no allowance set"
    assert default_pool["track"] is False and default_pool["fill"] is None

    # Rows are NOT buttons (tag is div)
    assert all(r["tag"] == "div" for r in first)

    # Second render with same payload does not rebuild (same stamp)
    assert out["rebuiltSameStamp"] is False

    # apikey.fan updated to allowance 1.45: "$0.00 of $1.45"
    after_apikey = out["afterEdit"][4]
    assert after_apikey["sentence"] == "$0.00 of $1.45"

    # No reading case (used null): "No reading yet", no meter, As of empty
    empty = out["empty"][0]
    assert empty["sentence"] == "No reading yet"
    assert empty["sub"] is None
    assert empty["track"] is False and empty["fill"] is None
    assert empty["asof"] == ""

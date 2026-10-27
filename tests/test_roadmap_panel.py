"""The Roadmap panel: source locks the spec, and one node run renders a fake payload."""

import json
import re
import shutil
from pathlib import Path

import pytest

from node_run import node_run

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


def test_roadmap_section_between_the_map_and_the_parts():
    graph = INDEX.index('<section class="graph"')
    road = INDEX.index('<section id="roadmap" class="roadmap" aria-labelledby="roadmap-h">')
    below = INDEX.index('<div class="below"')
    assert graph < below < road  # Agents, then Roadmap
    assert '<h2 id="roadmap-h">Roadmap</h2>' in INDEX
    assert '<div id="roadmap-list"></div>' in INDEX
    assert "style=" not in INDEX


def test_roadmap_heading_matches_problems_and_every_part():
    h2 = re.search(r"\.roadmap h2 \{([^}]*)\}", CSS).group(1)
    assert "font-size: 17px" in h2 and "font-weight: 700" in h2
    assert h2.count("1.5px solid var(--rule)") == 2
    group = re.search(r"\.roadmap-group \{([^}]*)\}", CSS).group(1)
    assert "var(--mono)" in group and "0.14em" in group and "uppercase" in group and "var(--ink-3)" in group


def test_roadmap_breakpoints():
    wide = _media("@media (width >= 2200px)")
    assert "680px" in wide and "48px" in wide and "grid-area: roadmap" in wide
    assert '"below roadmap"' in wide
    phone = _media("@media (max-width: 640px)")
    assert ".roadmap-bar, .roadmap-demo { display: none; }" in phone
    assert ".roadmap-row { grid-template-columns: 16px minmax(0, 1fr) 64px; }" in phone
    assert "width: 100%" in phone
    assert "1290px" in CSS
    assert "2200px" in CSS and "640px" in CSS


def test_roadmap_row_tokens():
    row = re.search(r"\.roadmap-row \{([^}]*)\}", CSS).group(1)
    assert re.search(r"grid-template-columns: ([^;]+);", row).group(1) == "16px 180px 64px 120px minmax(0, 1fr)"
    assert "min-height: 40px" in row and "var(--line)" in row
    assert "tabular-nums" in re.search(r"\.roadmap-count \{([^}]*)\}", CSS).group(1)
    bar = re.search(r"\.roadmap-bar \{([^}]*)\}", CSS).group(1)
    assert "height: 4px" in bar and "var(--line)" in bar
    assert "var(--ink-3)" in re.search(r"\.roadmap-fill \{([^}]*)\}", CSS).group(1)
    assert ".roadmap-demo { color: var(--ink-2); }" in CSS
    assert ".roadmap-demo.is-past { color: var(--ink-3); }" in CSS
    assert ".roadmap-flag.red" not in CSS
    assert ".roadmap-flag.amber { color: var(--amber); }" in CSS
    assert ".roadmap-orb, .roadmap-name, .roadmap-count, .roadmap-bar, .roadmap-demo { grid-row: 1; }" in CSS
    assert ".roadmap-row.is-marked { padding-block: 4px; }" in CSS
    mark = re.search(r"\.roadmap-mark \{([^}]*)\}", CSS).group(1)
    assert "grid-column: 2 / -1" in mark and "grid-row: 2" in mark
    assert "display: flex" in mark and "align-items: baseline" in mark and "gap: 8px" in mark
    assert "font-size: 13px" in mark and "line-height: 18px" in mark
    hl = re.search(r"\.roadmap-hl \{([^}]*)\}", CSS).group(1)
    assert "overflow: hidden" in hl and "text-overflow: ellipsis" in hl and "max-width" not in hl
    sheet = re.search(r"\.roadmap-sheet \{([^}]*)\}", CSS).group(1)
    assert "width: 600px" in sheet
    body = re.search(r"\.roadmap-body \{([^}]*)\}", CSS).group(1)
    assert "font-size: 14px" in body and "pre-wrap" in body
    box = re.search(r"\.roadmap-box \{([^}]*)\}", CSS).group(1)
    assert "width: 14px" in box and "height: 14px" in box
    assert "transition" not in CSS[CSS.index(".roadmap {"):]


def test_roadmap_source():
    assert "innerHTML" not in JS
    assert "no demo" in JS
    assert 'el("button", "roadmap-row")' in JS
    assert 'btn.type = "button"' in JS
    assert '"/api/roadmap"' in JS
    tick = JS[JS.index("function tick("):JS.index("function problemRow(")]
    assert "loadRoadmap(ctl.signal)" in tick
    assert "setInterval(tick, 5000)" in JS
    cmp = JS[JS.index("function roadmapCmp"):JS.index("function roadmapGroups")]
    assert "state" not in cmp
    groups = JS[JS.index("function roadmapGroups"):JS.index("function roadmapMark")]
    assert groups.index('"P0"') < groups.index('"P1"') < groups.index('"P2"') < groups.index('"Other"')
    marks = JS[JS.index("function roadmapMark"):JS.index("function roadmapDemo")]
    assert '"blocked"' in marks and '"waits"' in marks and '"waiting"' in marks
    assert re.search(r"Boss", JS) is None
    sheet = JS[JS.index("function fillRoadmapText"):JS.index("function roadmapSheet")]
    assert "roadmap-box" in sheet and "innerHTML" not in sheet
    assert r"- \[([ xX])\]" in sheet
    at = JS.index('document.addEventListener("keydown"')
    key = JS[at:JS.index("});", at)]
    assert key.index("roadmapKey(ev)") < key.index("select(sel)")
    render = JS[JS.index("function renderRoadmap"):JS.index("function loadRoadmap")]
    assert "roadmapDrawn" in render and "focusRoadmapFile(" in render


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
    if (sel === "button") return n.tag === "button";
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
    n.click = function () { (listeners.click || []).forEach(function (fn) { fn({ type: "click", target: n }); }); };
    n.focus = function () { document.activeElement = n; };
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
    createElement: function (tag) { return make(tag); }
  };
  document.body = make("body");
  return document;
}
function drawOrb(parent, ctx, id, size) {
  const svg = document.createElement("svg");
  svg.className = "orb";
  svg.setAttribute("data-id", id);
  svg.setAttribute("data-size", String(size));
  svg.setAttribute("data-ctx", ctx);
  parent.appendChild(svg);
}
function clsHas(n, name) { return (" " + (n.className || "") + " ").indexOf(" " + name + " ") >= 0; }
function child(n, name) {
  const kids = n._kids || [];
  for (let i = 0; i < kids.length; i++) if (clsHas(kids[i], name)) return kids[i];
  return null;
}
function findBtn(file) {
  const buttons = document.getElementById("roadmap-list").querySelectorAll("button");
  for (let i = 0; i < buttons.length; i++) if (buttons[i].getAttribute("data-file") === file) return buttons[i];
  return null;
}
function dump() {
  const kids = document.getElementById("roadmap-list")._kids;
  const groups = [];
  let cur = null;
  for (let i = 0; i < kids.length; i++) {
    const n = kids[i];
    if (n.tag === "h3") { cur = { priority: n.textContent, rows: [] }; groups.push(cur); continue; }
    const orb = child(n, "roadmap-orb");
    const demo = child(n, "roadmap-demo");
    const mark = child(n, "roadmap-mark");
    const bar = child(n, "roadmap-bar");
    const svg = (orb._kids || []).filter(function (k) { return k.tag === "svg"; })[0] || null;
    cur.rows.push({
      tag: n.tag, type: n.type, file: n.getAttribute("data-file"),
      name: child(n, "roadmap-name").textContent,
      count: child(n, "roadmap-count").textContent,
      demo: demo.textContent, past: clsHas(demo, "is-past"),
      segs: (bar._kids || []).map(function (s) { return { w: s.style.width, f: s._kids[0].style.width }; }),
      flags: ((mark && mark._kids) || []).map(function (m) { return { text: m.textContent, cls: m.className, title: m.getAttribute("title") }; }),
      marked: clsHas(n, "is-marked"),
      markCount: (n._kids || []).filter(function (k) { return clsHas(k, "roadmap-mark"); }).length,
      aria: n.getAttribute("aria-label"),
      orb: svg ? svg.getAttribute("data-size") : null
    });
  }
  return groups;
}
function rowNamed(groups, name) {
  for (let g = 0; g < groups.length; g++) {
    for (let r = 0; r < groups[g].rows.length; r++) if (groups[g].rows[r].name === name) return groups[g].rows[r];
  }
  return null;
}
function names(groups) {
  return groups.map(function (g) { return { priority: g.priority, names: g.rows.map(function (r) { return r.name; }) }; });
}
function allText(n) {
  let s = n.textContent || "";
  const kids = n._kids || [];
  for (let i = 0; i < kids.length; i++) s += allText(kids[i]);
  return s;
}
var document = makeDocument();
var ROAD_ORB = "r";
var current = { graph: { nodes: {
  alpha: { state: "blocked", headline: "Alpha is stuck on the build" },
  bravo: { state: "waiting", word: "Waiting on VP", headline: "Waiting on a review" },
  delta: { state: "working", headline: "should not show", label: "Delta Prime" },
  zed: { state: "waits", word: "Waits for you", headline: "Needs a decision" },
  hq: { state: "waiting", word: "Waiting on HQ" }
} } };
var roadmapPayload = null;
var roadmapDrawn = "";
var roadmapOpen = false;
var roadmapFile = "";
var roadmapReturn = null;
var host = document.createElement("div");
host.id = "roadmap-list";
document.body.appendChild(host);
var payload = { rows: [
  { file: "b.md", name: "Bravo", node: "bravo", priority: "P1", week1: { done: 1, total: 1 }, week2: { done: 1, total: 3 }, done: 2, total: 4, next_demo: null, text: "# Bravo\n- [ ] open\n- [x] shut\n" },
  { file: "a.md", name: "Alpha", node: "alpha", priority: "P0", week1: { done: 0, total: 2 }, week2: { done: 0, total: 2 }, done: 0, total: 4, next_demo: { date: "2026-10-02", text: "ships", past: true }, text: "alpha" },
  { file: "c.md", name: "Charlie", node: null, priority: "P1", week1: { done: 0, total: 0 }, week2: { done: 1, total: 1 }, done: 1, total: 1, next_demo: { date: "2026-12-01", text: "later", past: false }, text: "charlie" },
  { file: "z.md", name: "Zed", node: "zed", priority: "Other", week1: { done: 0, total: 1 }, week2: { done: 0, total: 1 }, done: 0, total: 2, next_demo: null, text: "zed" },
  { file: "d.md", name: "Delta", node: "delta", priority: "P2", week1: { done: 1, total: 2 }, week2: { done: 0, total: 2 }, done: 1, total: 4, next_demo: null, text: "delta" },
  { file: "n.md", name: "Aaron", node: null, priority: "nope", week1: { done: 0, total: 0 }, week2: { done: 0, total: 0 }, done: 0, total: 0, next_demo: null, text: "aaron" }
] };
renderRoadmap(payload);
var first = dump();
var bravo = findBtn("b.md");
bravo.focus();
renderRoadmap(payload);
var kept = document.activeElement === bravo;
bravo.click();
var sheet = document.getElementById("roadmap-sheet");
var body = document.getElementById("roadmap-body");
var boxes = body.querySelectorAll(".roadmap-box").map(function (b) { return { on: clsHas(b, "on"), text: b.textContent }; });
var sheetText = allText(body);
var opened = { hidden: sheet.hidden, active: document.activeElement.textContent, boxes: boxes, text: sheetText };
roadmapKey({ key: "Escape" });
var afterEsc = { hidden: sheet.hidden, file: document.activeElement.getAttribute("data-file") };
bravo.click();
sheet.querySelector("button").click();
var afterClose = { hidden: sheet.hidden, file: document.activeElement.getAttribute("data-file") };
findBtn("a.md").focus();
payload.rows[1].done = 3;
renderRoadmap(payload);
var edited = dump();
var afterEdit = { file: document.activeElement.getAttribute("data-file"), count: rowNamed(edited, "Alpha").count, order: names(edited) };
current.graph.nodes.alpha.state = "idle";
current.graph.nodes.bravo.state = "blocked";
renderRoadmap(payload);
var flipped = dump();
current.graph.nodes.bravo.headline = "";
renderRoadmap(payload);
var bare = dump();
console.log(JSON.stringify({ first: first, kept: kept, opened: opened, afterEsc: afterEsc, afterClose: afterClose, afterEdit: afterEdit, flipped: names(flipped), alpha: rowNamed(flipped, "Alpha").flags, bravo: rowNamed(flipped, "Bravo").flags, bare: rowNamed(bare, "Bravo"), hq: roadmapMark(current.graph.nodes.hq) }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_roadmap_panel_runs():
    helpers = JS[JS.index("function missing"):JS.index("function svgEl")]
    road = JS[JS.index("function roadmapCmp"):JS.index("function labelOf")]
    assert "function el(" in helpers and "function clear(" in helpers
    assert "function renderRoadmap(" in road and "function labelOf" not in road
    script = (
        "const vm=require('vm'),code=process.argv[1];"
        "const helpers=code.slice(code.indexOf('function missing'),code.indexOf('function svgEl'));"
        "const road=code.slice(code.indexOf('function roadmapCmp'),code.indexOf('function labelOf'));"
        "vm.runInContext(helpers+'\\n'+road+'\\n'+process.argv[2],vm.createContext({console:console}));"
    )
    proc = node_run(script, JS, _DRIVER, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert [g["priority"] for g in out["first"]] == ["P0", "P1", "P2", "Other"]
    assert [r["name"] for r in out["first"][0]["rows"]] == ["Alpha"]
    assert [r["name"] for r in out["first"][1]["rows"]] == ["Bravo", "Charlie"]
    assert [r["name"] for r in out["first"][2]["rows"]] == ["Delta Prime"]
    assert [r["name"] for r in out["first"][3]["rows"]] == ["Aaron", "Zed"]
    alpha, bravo, charlie, delta, zed = (out["first"][i]["rows"][j] for i, j in ((0, 0), (1, 0), (1, 1), (2, 0), (3, 1)))
    assert alpha["demo"] == "10-02 \u00b7 ships" and alpha["past"] is True
    assert alpha["flags"][0]["text"] == "\u25c7 Blocked" and "amber" in alpha["flags"][0]["cls"]
    assert alpha["flags"][1]["title"] == "Alpha is stuck on the build"
    assert alpha["marked"] is True and alpha["markCount"] == 1
    assert alpha["aria"] == "Alpha, 0/4, 10-02 \u00b7 ships, Blocked: Alpha is stuck on the build"
    assert bravo["demo"] == "no demo" and bravo["past"] is False
    assert bravo["flags"][0]["text"] == "\u25c7 Needs VP" and "amber" in bravo["flags"][0]["cls"]
    assert bravo["flags"][1]["title"] == "Waiting on a review"
    assert bravo["marked"] is True and bravo["markCount"] == 1
    assert bravo["aria"] == "Bravo, 2/4, no demo, Needs VP: Waiting on a review"
    assert zed["flags"][0]["text"] == "\u25c7 Needs Owner" and "amber" in zed["flags"][0]["cls"]
    assert zed["flags"][1]["title"] == "Needs a decision"
    assert delta["flags"] == [] and delta["marked"] is False and delta["markCount"] == 0
    assert delta["name"] == "Delta Prime" and delta["aria"] == "Delta Prime, 1/4, no demo"
    assert charlie["name"] == "Charlie" and charlie["marked"] is False and charlie["markCount"] == 0
    assert charlie["orb"] is None and alpha["orb"] == "16"
    assert bravo["segs"] == [{"w": "25%", "f": "100%"}, {"w": "75%", "f": "33.3%"}]
    assert alpha["segs"] == [{"w": "50%", "f": "0%"}, {"w": "50%", "f": "0%"}]
    assert all(row["tag"] == "button" and row["type"] == "button" for g in out["first"] for row in g["rows"])
    assert out["kept"] is True
    assert out["opened"]["hidden"] is False and out["opened"]["active"] == "Close"
    assert out["opened"]["boxes"] == [{"on": False, "text": ""}, {"on": True, "text": "\u2713"}]
    assert "- [ ]" not in out["opened"]["text"] and "- [x]" not in out["opened"]["text"]
    assert out["afterEsc"] == {"hidden": True, "file": "b.md"}
    assert out["afterClose"] == {"hidden": True, "file": "b.md"}
    assert out["afterEdit"]["file"] == "a.md" and out["afterEdit"]["count"] == "3/4"
    assert out["afterEdit"]["order"] == [
        {"priority": "P0", "names": ["Alpha"]},
        {"priority": "P1", "names": ["Bravo", "Charlie"]},
        {"priority": "P2", "names": ["Delta Prime"]},
        {"priority": "Other", "names": ["Aaron", "Zed"]},
    ]
    assert out["flipped"] == out["afterEdit"]["order"]
    assert out["alpha"] == []
    assert out["bravo"][0]["text"] == "\u25c7 Blocked" and "amber" in out["bravo"][0]["cls"]
    assert out["bare"]["marked"] is True and out["bare"]["markCount"] == 1
    assert out["bare"]["flags"] == [{"text": "\u25c7 Blocked", "cls": "roadmap-flag amber", "title": None}]
    assert out["bare"]["aria"] == "Bravo, 2/4, no demo, Blocked"
    assert out["hq"] is None

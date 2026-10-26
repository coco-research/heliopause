import ast
import colorsys
import functools
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from atlas.__main__ import make_server

HOME = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "home"
STATIC = Path(__file__).resolve().parents[1] / "atlas" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "graph.css").read_text(encoding="utf-8")
JS = (STATIC / "graph.js").read_text(encoding="utf-8")
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "font-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_LINKS = (
    "/signal/fonts.css",
    "/signal/layout.css",
    "/signal/signal.css",
    "/graph.css",
)
_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgb\(|rgba\(|hsl\(")
_BANNED = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval", "new Function")


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


def test_map_is_html(httpd):
    status, headers, body = _request(httpd, "/map")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert body.decode("utf-8") == INDEX


def test_index_constraints():
    assert "color.css" not in INDEX
    assert "<style" not in INDEX
    assert "style=" not in INDEX
    assert re.search(r"\son[a-zA-Z]+\s*=", INDEX) is None
    hrefs = re.findall(r'<link\b[^>]*href="([^"]+)"', INDEX)
    assert hrefs == list(_LINKS)
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", INDEX, re.S)
    assert len(scripts) == 3
    srcs = []
    for attrs, body in scripts:
        found = re.search(r'\bsrc\s*=\s*"([^"]+)"', attrs)
        assert found
        assert body.strip() == ""
        srcs.append(found.group(1))
    assert srcs == ["/atlas-theme.js", "/status.js", "/graph.js"]
    assert 'src="/status.js" defer' in INDEX and 'src="/graph.js" defer' in INDEX
    assert INDEX.index('src="/status.js"') < INDEX.index('src="/graph.js"')
    assert INDEX.index('src="/atlas-theme.js"') < INDEX.index(_LINKS[0])


def test_shell_markup():
    assert '<div class="app">' in INDEX
    assert '<nav class="rail rail-sheet"' in INDEX
    assert 'class="rail-head"' in INDEX and 'class="brand"' in INDEX
    assert '<div class="nav">' in INDEX and 'class="nav-item"' in INDEX
    assert 'class="badge"' in INDEX and 'class="nav-off"' in INDEX
    assert '<div class="main">' in INDEX and '<main class="page">' in INDEX
    assert "Local only · 127.0.0.1" in INDEX
    for href, word in (
        ("#now", "Now"),
        ("#agents", "Agents"),
        ("#work", "Work"),
        ("#spend", "Spend"),
        ("#roadmap", "Roadmap"),
    ):
        assert f'href="{href}"' in INDEX and f">{word}" in INDEX
    assert "#activity" not in INDEX and ">Activity<" not in INDEX
    assert 'id="rail-open"' in INDEX and 'id="rail-close"' in INDEX and 'id="scrim"' in INDEX
    assert 'id="jump"' in INDEX and "Jump to an agent" in INDEX and "⌘K" in INDEX
    assert 'id="theme"' in INDEX and 'id="lede"' in INDEX
    assert ".badge[hidden]" in CSS  # Signal's .badge sets display, which beats the hidden attribute


def test_shell_views():
    now = INDEX.index('data-view="now"')
    below = INDEX.index('<div class="below" data-view="now agents">')
    work = INDEX.index('data-view="work"')
    spend = INDEX.index('data-view="spend"')
    road = INDEX.index('data-view="roadmap"')
    assert 'data-view="activity"' not in INDEX
    assert now < below < work < spend < road
    graph = INDEX.index('<section class="graph"')
    assert INDEX.index('id="lede"') < INDEX.index('id="prov"') < now < graph < below
    g = INDEX.index('<svg id="g"')
    legend = INDEX.index('id="motion-legend"')
    ticker = INDEX.index('id="ticker"')
    assert graph < g < legend < ticker < INDEX.index("</section>", ticker) < below
    assert work < INDEX.index('id="problems"') < INDEX.index('id="alerts"') < spend
    assert spend < INDEX.index('id="budget"') < road < INDEX.index('id="roadmap"')


def test_needs_you_column():
    assert 'id="needs"' in INDEX and 'id="needs-h">Needs you' in INDEX
    assert 'id="needs-empty"' in INDEX and "Nothing needs you." in INDEX
    assert '<details id="fyi" class="fyi" hidden>' in INDEX and "FYI · 0 quieter things" in INDEX
    assert 'id="broken"' in INDEX and ">See all</a>" in INDEX
    assert INDEX.index('id="needs"') < INDEX.index('<section class="graph"')
    assert INDEX.index('id="ticker"') < INDEX.index('id="broken"') < INDEX.index("</section>", INDEX.index('id="ticker"'))
    assert INDEX.index('data-view="work"') < INDEX.index('id="problems"') < INDEX.index('id="alerts"')
    wide = CSS[CSS.index("@media (width >= 2200px)"):]
    assert '"graph needs"' in wide and "grid-area: needs" in wide
    # Side by side only where the network keeps its 1100px floor (rail 232 + padding + 1100 + 24 + 320 < 1800).
    side = CSS[CSS.index("@media (width >= 1800px)"):CSS.index("@media (width >= 2200px)")]
    assert "minmax(0, 1fr) 320px" in side
    assert "repeat(auto-fill, minmax(300px, 1fr))" in CSS  # narrower: the cards sit in a row above the network
    assert ".needs-empty { margin: 8px 0 0; color: var(--ink-3); }" in CSS
    assert "color: var(--red)" in re.search(r"\.broken \{([^}]*)\}", CSS).group(1)
    assert "min-height: 44px" in re.search(r"\.needs-act \{([^}]*)\}", CSS).group(1)
    assert ".needs button:focus-visible" in CSS
    assert "ledeText" not in JS
    paint = JS[JS.index("function paintStatus"):JS.index("function paintWaitBadge")]
    assert paint.count("statusLine(") == 1
    assert "AtlasStatus.statusLine(current, problemItems, alertPayload)" in paint
    assert "line.head" in paint and "paintBroken(line.broken)" in paint
    assert "brokenCountFrom" not in JS
    badge = JS[JS.index("function paintWaitBadge"):JS.index("function ageStamp")]
    assert "line.needs" in badge and "badge.hidden = !n" in badge
    assert "needsYou(" not in badge
    for name, end in (
        ("function render(", "function tick("),
        ("function renderProblems", "function loadProblems"),
        ("function loadAlerts", "function parentId"),
    ):
        assert "paintStatus()" in JS[JS.index(name):JS.index(end)]
    card = JS[JS.index("function needsCard"):JS.index("function paintNeeds")]
    assert 'el("button", "alert-done", "Done")' in card and "closeAlert(" in card
    assert card.index("nodeById(") < card.index('el("button", "needs-act", "Show agent")')
    assert "select(id)" in card
    assert "alertLink(" in card and "feedbackGroup(" in card and "ageSpan(" in card
    assert "fetch(" not in card
    assert "FYI · " in JS and "quieter things" in JS and 'n + " broken: "' in JS
    broken = JS[JS.index("function paintBroken"):JS.index("function calmNow")]
    assert "errorWords(" in broken
    assert JS.count('fetch("/api/feedback"') == 1
    assert JS.count('"/close"') == 1


def test_header_is_the_status_sentence():
    head = INDEX[INDEX.index('<header class="page-head">'):INDEX.index("</header>", INDEX.index("page-head"))]
    assert '<h1 id="lede">' in head and head.index('id="lede"') < head.index('id="prov"')
    assert 'id="jump"' in head and 'id="clock"' in head and 'id="theme"' in head
    assert ">Atlas</h1>" not in INDEX
    assert INDEX.count('class="brand">Heliopause</span>') == 2  # rail and the phone top bar
    rule = re.search(r"\.page-head h1 \{([^}]*)\}", CSS).group(1)
    assert "font-size: 26px" in rule and "line-height: 32px" in rule and "font-weight: 700" in rule
    paint = JS[JS.index("function paintStatus"):JS.index("function paintWaitBadge")]
    assert 'getElementById("lede")' in paint and "lede.textContent = line.head" in paint


def test_needs_card_primary_is_one_blue_link():
    block = JS[JS.index("function cardOpen"):JS.index("function fyiSummary")]
    assert block.index("alertLink(") < block.index('el("a", "needs-act"')
    assert 'host === "github.com" ? "Open on GitHub" : "Open"' in block
    start = JS.index('row.kind === "alert"')
    alert = JS[start:JS.index("else if", start)]
    assert alert.index("cardOpen(") < alert.index('el("button", "alert-done", "Done")')
    act = re.search(r"\.needs-act \{([^}]*)\}", CSS).group(1)
    assert "background: var(--blue)" in act and "color: var(--blue-ink)" in act and "min-height: 44px" in act
    focus = re.search(r"\.needs a:focus-visible[^{]*\{([^}]*)\}", CSS).group(1)
    assert "outline: 2px solid var(--blue)" in focus
    done = re.search(r"\.alert-done \{([^}]*)\}", CSS).group(1)
    assert "background: transparent" in done and "background: var(--blue)" not in done


def test_fyi_summary_carries_groups():
    body = JS[JS.index("function fyiSummary"):JS.index("function needsColumns")]
    assert 'quieter === 1 ? " quieter thing" : " quieter things"' in body
    assert "groups.length > 3 ? 3 : groups.length" in body
    assert 'labels.join(", ")' in body and '"…"' in body
    assert "href" not in body
    paint = JS[JS.index("function paintNeeds"):JS.index("function brokenPhrase")]
    assert "fyiSummary(quieter, groups)" in paint
    assert 'a.setAttribute("href", "#work")' in paint  # the groups' links stay in the open fold


def test_needs_row_is_one_row_below_1800():
    cols = JS[JS.index("function needsColumns"):JS.index("function fitNeedsRow")]
    assert "getComputedStyle" in cols and "gridTemplateColumns" in cols and "innerWidth < 800" in cols
    row = JS[JS.index("function fitNeedsRow"):JS.index("function paintNeeds")]
    assert "cols > 0 ? cols : cards.length" in row and "more \\u2192" in row
    more = INDEX[INDEX.index('id="needs-more"'):INDEX.index('id="needs-more"') + 80]
    assert 'href="#work"' in more
    paint = JS[JS.index("function paintNeeds"):JS.index("function brokenPhrase")]
    assert paint.index("if (stamp !== needsDrawn)") < paint.index("alertAge(") < paint.index("fitNeedsRow(list)")
    timer = JS[JS.index("let needsRowTimer"):JS.index("let needsRowTimer") + 450]
    assert 'addEventListener("resize"' in timer and "setTimeout" in timer and "150" in timer
    assert "fitNeedsRow(host)" in timer


def test_needs_meta_says_oldest_first():
    assert 'id="needs-meta"' in INDEX
    paint = JS[JS.index("function paintNeeds"):JS.index("function brokenPhrase")]
    assert "oldest first" in paint
    rule = re.search(r"\.needs-meta \{([^}]*)\}", CSS).group(1)
    assert "var(--mono)" in rule


def test_clear_needs_hides_the_heading():
    assert 'id="needs-head" hidden>' in INDEX
    assert 'class="needs-empty" hidden>' in INDEX and "Nothing needs you." in INDEX
    paint = JS[JS.index("function paintNeeds"):JS.index("function brokenPhrase")]
    assert "head.hidden = rows.length === 0" in paint and "empty.hidden = true" in paint
    assert 'id="fyi"' in INDEX


def test_inspector_drawer():
    now = INDEX[INDEX.index('<section class="view" data-view="now">'):INDEX.index('data-view="work"')]
    assert 'id="panel"' not in now and 'id="panel"' not in INDEX
    below = INDEX[INDEX.index('<div class="below"'):INDEX.index("</table>", INDEX.index('id="tbl"'))]
    assert 'id="panel"' not in below and 'id="tbl"' in below
    drawer = INDEX[INDEX.index('<aside id="inspector"'):INDEX.index("<script", INDEX.index("inspector"))]
    assert 'role="dialog"' in drawer and 'aria-modal="false"' in drawer
    assert 'aria-labelledby="inspector-h"' in drawer and "hidden" in drawer
    rule = re.search(r"\.inspector \{([^}]*)\}", CSS).group(1)
    assert "width: 380px" in rule and "var(--float-border)" in rule and "var(--float-shadow)" in rule
    assert "position: fixed" in rule
    phone = CSS[CSS.index("@media (width < 800px)"):CSS.index("@media (width < 800px)") + 80]
    assert "width: 100%" in phone and "min-width" not in phone
    close = re.search(r"\.inspector-close \{([^}]*)\}", CSS).group(1)
    assert "width: 44px" in close and "height: 44px" in close
    reduce = CSS[CSS.rindex("@media (prefers-reduced-motion: reduce)"):]
    assert ".inspector { animation: none; }" in reduce
    assert "inspector-in" not in reduce
    panel = JS[JS.index("function panel"):JS.index("function markTable")]
    for label in ("It asked you", "Doing", "Last seen", "Open PRs", "Queue", "Spend today", "Recent"):
        assert label in panel
    assert "\"None\"" not in panel  # rows with nothing are left out, never shown as "None"
    assert "if (!pair[1]) return;" in panel
    assert "askLines(" in panel and "jobLines(" in panel and "messageLines(" in panel
    assert "labelOf(" in panel and "kindText(node)" in panel and "ageOf(" in panel
    assert "needsActions(" in panel and 'aria-label", "Close"' in panel
    assert 'title.id = "inspector-h"' in panel and "messageLines(id).slice(0, 4)" in panel
    assert "select(id)" in panel or "select(sel)" in panel
    opened = JS[JS.index("function select("):JS.index("function showOffline")]
    assert "panel();" in opened and "focusInspector();" in opened
    key = JS[JS.index('document.addEventListener("keydown"'):JS.index('document.addEventListener("visibilitychange"')]
    assert 'ev.key === "Escape" && sel' in key and "select(sel)" in key
    click = JS[JS.index('svg.addEventListener("click"'):JS.index('getElementById("theme")')]
    assert 'closest(".node")' in click and "select(sel)" in click
    show = JS[JS.index("function showView"):JS.index("function setRail")]
    assert "select(sel)" in show and "jumpKeepsOpen" in show
    assert 'addEventListener("hashchange", showView)' in JS
    assert 'tr.addEventListener("click", function () { select(id); })' in JS


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_fyi_summary_sentence():
    script = (
        "const src = process.argv[1];"
        "eval(src.slice(src.indexOf('function fyiSummary'), src.indexOf('function needsColumns')));"
        "const groups = ["
        "  {label: '2 red CI on ci', count: 2},"
        "  {label: '1 low disk on disk-guard', count: 1},"
        "  {label: '1 warning on budget', count: 1},"
        "  {label: '1 note on lab', count: 1}"
        "];"
        "console.log(JSON.stringify({"
        "  three: fyiSummary(3, groups.slice(0, 2)),"
        "  one: fyiSummary(1, [groups[1]]),"
        "  more: fyiSummary(5, groups),"
        "  none: fyiSummary(0, [])"
        "}));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out["three"] == "FYI · 3 quieter things (2 red CI on ci, 1 low disk on disk-guard)"
    assert out["one"] == "FYI · 1 quieter thing (1 low disk on disk-guard)"
    assert out["more"] == "FYI · 5 quieter things (2 red CI on ci, 1 low disk on disk-guard, 1 warning on budget…)"
    assert out["none"] == "FYI · 0 quieter things"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_needs_row_cap_runs():
    script = (
        "const src = process.argv[1];"
        "eval(src.slice(src.indexOf('function needsColumns'), src.indexOf('function paintNeeds')));"
        "function run(width, columns, n) {"
        "  const cards = [];"
        "  for (let i = 0; i < n; i++) cards.push({ hidden: false });"
        "  const more = { hidden: true, textContent: '' };"
        "  global.window = { innerWidth: width };"
        "  global.getComputedStyle = function () { return { gridTemplateColumns: columns }; };"
        "  global.document = { getElementById: function () { return more; } };"
        "  fitNeedsRow({ children: cards });"
        "  return { hidden: cards.map(function (c) { return c.hidden; }), more: more.hidden ? '' : more.textContent };"
        "}"
        "console.log(JSON.stringify({"
        "  row: run(1440, '300px 300px 300px', 5),"
        "  phone: run(390, '300px', 5),"
        "  stack: run(1800, 'none', 4),"
        "  exact: run(1200, '400px 400px', 2)"
        "}));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out["row"] == {"hidden": [False, False, False, True, True], "more": "2 more \u2192"}
    assert out["phone"]["hidden"] == [False, False, False, False, False] and out["phone"]["more"] == ""
    assert out["stack"]["hidden"] == [False, False, False, False] and out["stack"]["more"] == ""
    assert out["exact"]["hidden"] == [False, False] and out["exact"]["more"] == ""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_card_open_reuses_alert_link_safety():
    script = (
        "const src = process.argv[1];"
        "function el(tag, className, text) {"
        "  const attrs = {};"
        "  return { tagName: tag.toUpperCase(), className: className, textContent: text,"
        "    setAttribute: function (k, v) { attrs[k] = String(v); },"
        "    getAttribute: function (k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },"
        "    attrs: attrs };"
        "}"
        "eval(src.slice(src.indexOf('function cardOpen'), src.indexOf('function fyiSummary')));"
        "eval(src.slice(src.indexOf('function alertLink'), src.indexOf('function alertClosed')));"
        "function pack(raw) {"
        "  const node = cardOpen(raw);"
        "  if (!node) return null;"
        "  return { text: node.textContent, href: node.attrs.href || '', rel: node.attrs.rel || '', target: node.attrs.target || '' };"
        "}"
        "console.log(JSON.stringify({"
        "  gh: pack('https://github.com/coco-research/atlas/pull/1'),"
        "  path: pack('/jobs/1'),"
        "  other: pack('https://example.com/x'),"
        "  scheme: pack('javascript:alert(1)'),"
        "  prose: pack('Answer or resolve it.')"
        "}));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out["gh"] == {
        "text": "Open on GitHub",
        "href": "https://github.com/coco-research/atlas/pull/1",
        "rel": "noreferrer noopener",
        "target": "_blank",
    }
    assert out["path"] == {"text": "Open", "href": "/jobs/1", "rel": "", "target": ""}
    assert out["other"] is None and out["scheme"] is None and out["prose"] is None
    assert "Nothing waiting. It hasn't reported anything yet." in JS  # never an empty drawer
    assert "#inspector-h:focus:not(:focus-visible)" in CSS


def test_shell_router_defaults_to_now():
    body = JS[JS.index("function currentView"):JS.index("function showView")]
    assert 'return "now"' in body and "activity" not in body
    show = JS[JS.index("function showView"):JS.index("function setRail")]
    assert '.split(" ")' in show and "indexOf(name)" in show
    assert 'setAttribute("aria-current", "page")' in show
    assert 'if (name === "now") relayout();' in show
    assert 'addEventListener("hashchange", showView)' in JS
    go = JS[JS.index("function goAgent"):JS.index("function wireShell")]
    assert 'location.hash = "#now"' in go and "select(id)" in go
    road = JS[JS.index("function renderRoadmap"):JS.index("function loadRoadmap")]
    assert 'getElementById("nav-roadmap")' in road and "nav.hidden = n === 0" in road


def test_shell_user_words():
    assert "Who talks to whom" not in INDEX and "Who talks to whom" not in JS
    assert "Every part" not in INDEX
    assert '<h2 id="graph-title">Your agents</h2>' in INDEX
    assert 'aria-label="Your agents"' in INDEX
    assert "<caption>All agents</caption>" in INDEX
    assert '<h2 id="budget-h">Spend</h2>' in INDEX
    prov = JS[JS.index("function provText"):JS.index("function hasPos")]
    assert '" agents ' in prov and '" parts ' not in prov


@pytest.mark.parametrize("text", [INDEX, CSS, JS])
def test_no_colour_literals(text):
    # The one exception is the seeded orb tone, built once in orbDraw (test_orb_tones_come_from_one_function).
    assert _COLOR.search(text.replace('"hsl("', "", 1)) is None


def test_graph_js_safe():
    for bad in _BANNED:
        assert bad not in JS
    assert re.search(r"Boss", JS) is None
    assert re.search(r"Boss", INDEX) is None
    assert re.search(r"Boss", CSS) is None
    assert '"owner"' in JS


def _iife_start(text: str) -> str:
    body = text.lstrip()
    while True:
        if body.startswith("/*"):
            end = body.find("*/")
            assert end != -1
            body = body[end + 2:].lstrip()
            continue
        if body.startswith("//"):
            newline = body.find("\n")
            assert newline != -1
            body = body[newline + 1:].lstrip()
            continue
        if body.startswith('"use strict"') or body.startswith("'use strict'"):
            body = body.split(";", 1)[1].lstrip()
            continue
        return body


def test_graph_js_live():
    body = _iife_start(JS)
    assert body.startswith("(function") or body.startswith("(() =>")
    tail = JS.rstrip()
    assert tail.endswith("})();") or tail.endswith("})()")
    assert 'EventSource("/api/events")' in JS
    assert re.search(r"^  const MAX_IN_FLIGHT = 6;$", JS, re.M)
    assert re.search(r"^  const MAX_QUEUED = 20;$", JS, re.M)
    assert re.search(r"^  const CALM_S = 60;$", JS, re.M)


def test_graph_js_signoff():
    assert '":m:"' in JS and "mentions" in JS
    assert "touched(" not in JS
    assert "function classList" not in JS
    assert "function talkClass" in JS and "function unrelated" in JS
    assert '"now"' in JS
    assert "mark-stale" in JS
    assert "whenOf" not in JS


@pytest.mark.parametrize(
    "path,content_type,name",
    [
        ("/map", "text/html; charset=utf-8", "index.html"),
        ("/graph.css", "text/css; charset=utf-8", "graph.css"),
        ("/graph.js", "text/javascript; charset=utf-8", "graph.js"),
        ("/atlas-theme.js", "text/javascript; charset=utf-8", "atlas-theme.js"),
    ],
)
def test_page_assets(httpd, path, content_type, name):
    status, headers, body = _request(httpd, path)
    assert status == 200
    assert headers["Content-Type"] == content_type
    assert body == (STATIC / name).read_bytes()


@pytest.mark.parametrize("path", ["/graph.js/../collect.py", "/static/graph.js"])
def test_page_traversal_404(httpd, path):
    status, _headers, _body = _request(httpd, path)
    assert status == 404


def test_graph_js_alive():
    assert ".recent" in JS and ".active" in JS
    for name, value in (
        ("AMBIENT_SPEED", "90"),
        ("AMBIENT_MAX", "24"),
        ("AMBIENT_BUSIEST_S", "8"),
        ("AMBIENT_TRAIL", "12"),
        ("REPLAY_SPEEDUP", "10"),
        ("REPLAY_IDLE_S", "60"),
        ("REPLAY_RESTART_S", "10"),
    ):
        assert re.search(rf"^  const {name} = {re.escape(value)};$", JS, re.M)
    assert "replaying the last hour at 10×" in JS
    assert "Replay · the last hour at 10×" in JS
    assert 'addEventListener("visibilitychange"' in JS
    assert "chat-on" in JS and "working now" in JS


def test_graph_js_glance_fixes():
    assert "const DOT_R " not in JS
    assert re.search(r"^  const LIVE_DOT_R = 4;$", JS, re.M)
    assert re.search(r"^  const AMBIENT_DOT_R = 3;$", JS, re.M)
    assert "No live messages" in JS and "Nothing moving" not in JS
    # idle replay counts from the newest real event, not from page load
    assert "lastRealAt" in JS and "let visibleSince = 0;" in JS
    assert "isBrokenPair(" in JS
    body = JS[JS.index("function brokenPartner"):JS.index("function drawLanes")]
    assert "failed_items" in body and ".owner" in body and "fibres" not in body


def test_graph_css_alive():
    assert "@keyframes chat-on" in CSS
    reduced = CSS[CSS.index("@media (prefers-reduced-motion"):]
    assert "chat-on" in reduced and "animation: none" in reduced


def test_comet_trails_js():
    assert re.search(r"^  const TRAIL_LENGTHS = \[16, 32, 52\];$", JS, re.M)
    assert "stroke-dasharray" in JS and "stroke-dashoffset" in JS
    assert "setTrails(" in JS and "makeTrails(" in JS
    # red (failed) links never carry a comet
    assert 'toneName !== "red"' in JS


def test_comet_colours_css():
    root = re.search(r"^:root \{([^}]*)\}", CSS, re.M).group(1)
    for token in ("--atlas-flow", "--atlas-live", "--atlas-replay"):
        assert f"{token}:" in root
    assert "var(--green)" in root and "var(--blue)" not in root
    assert "var(--green) 80%" in root  # light theme
    dark = re.search(r"^:root\[data-theme=\"dark\"\] \{([^}]*)\}", CSS, re.M).group(1)
    assert "--atlas-flow: color-mix(in srgb, var(--green) 55%, transparent)" in dark
    for i, (width, opacity) in enumerate((("3", "0.6"), ("2", "0.35"), ("1.5", "0.15"))):
        rule = re.search(rf"^\.trail\.t{i} \{{([^}}]*)\}}", CSS, re.M).group(1)
        assert f"stroke-width: {width}" in rule and f"stroke-opacity: {opacity}" in rule
    # blue is only for the live "blue" tone
    assert re.search(r"^\.trail\.live\.blue \{ stroke: var\(--blue\); \}", CSS, re.M)


def test_chat_ring_breathes_green():
    rule = re.search(r"^svg \.node\.chat-on circle\.glow \{([^}]*)\}", CSS, re.M).group(1)
    assert "stroke: var(--green)" in rule and "var(--blue)" not in rule
    assert "drop-shadow(0 0 4px var(--atlas-flow))" in rule and "1.6s" in rule  # NG's orb-ringpulse timing
    orb = re.search(r"^\.orb-cell circle\.glow \{([^}]*)\}", CSS, re.M).group(1)
    assert "stroke: var(--green)" in orb
    frames = re.search(r"^@keyframes chat-on \{.*$", CSS, re.M)
    assert frames and "opacity: 0.35" in frames.group(0) and "opacity: 1;" in frames.group(0)
    assert "filter: none" in CSS[CSS.index("@media (prefers-reduced-motion"):]


def test_graph_css_ambient_halo():
    rule = re.search(r"^\.amb-dot \{([^}]*)\}", CSS, re.M).group(1)
    assert "fill: var(--atlas-ambient)" in rule
    assert "stroke: var(--paper)" in rule and "stroke-width: 1;" in rule
    live = re.search(r"^\.pulse \{([^}]*)\}", CSS, re.M).group(1)
    assert "var(--atlas-live)" in live and "var(--atlas-ambient)" not in live


def test_the_hq_fold_is_gone():
    for gone in ("hqDrawn", "hqQuiet", "syncHq", "admitHq", "quietAt", "quietText", "wrapQuiet", "QUIET_", "quiet \\u00b7"):
        assert gone not in JS  # every HQ role is an orb, so nothing is folded into a text line
    assert "a.meta" not in CSS
    lanes = JS[JS.index("function drawLanes"):JS.index("function drawFibres")]
    assert 'svgEl("a"' not in lanes and "tspan" not in lanes
    assert re.search(r"function laneNodes\(lane\) \{\s*return lane\.nodes \|\| \[\];\s*\}", JS)  # no lane filters its parts


def test_kind_text_in_panel_and_compact_tooltip():
    assert "hq-hr" not in JS and "COO" not in JS
    assert re.search(r"function kindText\(node\) \{\s*return node\.kind \|\| \"\";\s*\}", JS)
    panel = JS[JS.index("function panel"):JS.index("function markTable")]
    assert "kindText(node)" in panel and "node.kind" not in panel
    nodes = JS[JS.index("function drawNodes"):JS.index("function unrelated")]
    assert "compact ? kindText(node)" in nodes
    # compact nodes skip the kind line and badge text (built in the else branch) and carry both in the tooltip
    assert nodes.index("if (compact) {") < nodes.index("} else {") < nodes.index("node.badge.text")
    assert "const extra = compact && node.badge" in nodes and "+ extra" in nodes
    assert JS.count("node.kind") == 1  # only inside kindText


def test_focus_ignores_a_selection_with_no_box():
    assert re.search(r"function focused\(\) \{\s*return sel && nodeEls\[sel\] \? sel : null;\s*\}", JS)
    body = JS[JS.index("function unrelated"):JS.index("function retargetLive")]
    assert "const f = focused();" in body
    # focus and dim state read the guarded id, never the raw selection
    assert not re.search(r"\bsel\b", body.replace("return sel && nodeEls[sel] ? sel : null;", "").replace('toggle("sel"', ""))


def test_selection_class_is_styled():
    toggled = re.findall(r'classList\.toggle\("(\w+)", id === (?:f|sel)\)', JS)
    assert toggled == ["sel"]
    assert f"svg .node.{toggled[0]} circle.pick" in CSS


def test_keyboard_focus_dashes_the_ink_pick_ring():
    rule = re.search(r":focus-visible circle\.pick \{([^}]*)\}", CSS).group(1)
    assert "stroke-dasharray: 4 3" in rule
    for block in re.findall(r"[^{}]*circle\.pick[^{]*\{([^}]*)\}", CSS):
        assert "stroke: var(--blue)" not in block


def _brace_end(text, open_at):
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise AssertionError("unclosed brace")


def test_focused_view_html():
    assert '<label for="show">Show</label>' in INDEX
    assert '<select id="show">' in INDEX


def test_focused_view_css():
    wide_at = CSS.index("@media (min-width: 1100px)")
    wide = CSS[wide_at:_brace_end(CSS, CSS.index("{", wide_at)) + 1]
    assert "min-width: 1100px" in wide
    desk_at = CSS.index("@media (min-width: 1800px)")
    desk = CSS[desk_at:_brace_end(CSS, CSS.index("{", desk_at)) + 1]
    assert "min-width" not in CSS.replace(wide, "", 1).replace(desk, "", 1)
    narrow_at = CSS.index("@media (max-width: 1099.98px)")
    narrow_open = CSS.index("{", narrow_at)
    narrow = CSS[narrow_open + 1:_brace_end(CSS, narrow_open)]
    assert ".focus-view" in narrow and "display: block" in narrow
    assert "min-width" not in narrow
    assert re.search(r"\.focus-partner\s*\{[^}]*min-height:\s*56px", CSS)
    show_open = CSS.index("{", CSS.index("#show"))
    show = CSS[show_open + 1:_brace_end(CSS, show_open)]
    assert "font-size: 16px" in show and "44px" in show
    assert CSS.index(".focus-stage { transition: opacity") < CSS.index(".focus-stage { transition: none; }")


def test_focused_view_js():
    assert 'matchMedia("(max-width: 1099.98px)")' in JS
    body = JS[JS.index("function fillShow"):JS.index("function drawBox")]
    assert 'el("optgroup")' in body and "graph.lanes" in body
    assert "laneNodes(" not in body
    assert "innerHTML" not in JS
    touch = JS[JS.index("function touchesFocus"):JS.index("function rowCount")]
    assert '"partner-" + (from === id ? to : from)' in touch  # no row, no rail flight
    rail = JS[JS.index("function layoutRail"):JS.index("function focusStamp")]
    assert 'Number(buttons[i].getAttribute("data-count"))' in rail
    select = JS[JS.index("function select("):JS.index("function showOffline")]
    assert "narrow" in select and "focusId = id" in select
    render = JS[JS.index("function render("):JS.index("function tick(")]
    assert render.index("draw(graph)") < render.index("renderFocus(")
    drawn = JS[JS.index("function focusStamp"):JS.index("function clearRailFlights")]
    assert "stamp === focusDrawn" in drawn


def test_ambient_is_background_motion():
    root = re.search(r"^:root \{([^}]*)\}", CSS, re.M).group(1)
    dark = re.search(r'^:root\[data-theme="dark"\] \{([^}]*)\}', CSS, re.M).group(1)
    assert "--atlas-ambient: color-mix(in srgb, var(--green) 60%, transparent)" in root
    assert "--atlas-ambient: color-mix(in srgb, var(--green) 45%, transparent)" in dark
    trail = re.search(r"^\.trail\.amb \{([^}]*)\}", CSS, re.M).group(1)
    assert "var(--atlas-ambient)" in trail
    start = JS[JS.index("function startAmb"):JS.index("function ambFrame")]
    assert "makeTrails(" not in start and start.count('svgEl("path"') == 2
    assert "AMBIENT_DOT_R" in start and "LIVE_DOT_R" not in start
    assert "ping(" not in start and ".talk" not in start and "renderTicker" not in start
    amb = JS[JS.index("// ---- ambient flow"):JS.index("// ---- idle replay")]
    for banned in ("ping(", ".talk", "renderTicker", "tickerItem"):
        assert banned not in amb
    assert "document.hidden" in amb and "reduceMotion" in amb and "AMBIENT_JITTER" in amb


def test_ambient_pairs_come_only_from_fibres():
    body = JS[JS.index("function syncAmbient"):JS.index("function placeAmb")]
    assert "(graph.fibres || []).forEach" in body
    assert "graph.recent" not in body and ".recent" not in body
    assert "ambientInterval(p.total, busiest)" in body
    assert "num(f.total_messages) + num(f.total_mentions)" in body
    assert body.count("list.push") == 1
    assert "Math.pow" not in body
    assert "AMBIENT_FAST_S" not in JS and "AMBIENT_SLOW_S" not in JS


def test_motion_legend_and_edge_info():
    assert INDEX.index('id="edge-info"') < INDEX.index('id="g"')
    assert INDEX.index('id="motion-legend"') < INDEX.index('id="ticker"')
    legend = INDEX[INDEX.index('id="motion-legend"'):INDEX.index('id="ticker"')]
    assert "Live now" in legend
    assert "Last hour, replayed 10×" in legend
    assert "24 h activity, scaled" in legend
    assert 'class="trail t0 live"' in legend and 'class="pulse"' in legend
    assert 'class="pulse replay"' in legend and "amb-dot" in legend
    assert 'id="lg-live"' not in INDEX and 'id="lg-broken"' in INDEX
    assert re.search(r'<p id="edge-info"[^>]*aria-live="polite"', INDEX)
    refresh = JS[JS.index("function refreshLegend"):JS.index("function newestReal")]
    assert "motion-legend" in refresh and "reduceMotion" in refresh
    hit = re.search(r"^\.edge-hit \{([^}]*)\}", CSS, re.M).group(1)
    assert "stroke-width: 10" in hit and "stroke: var(--paper)" in hit
    assert "stroke-opacity: 0" in hit and "pointer-events: stroke" in hit
    assert "#motion-legend" in CSS[CSS.index("@media (prefers-reduced-motion"):]
    hits = JS[JS.index("function drawHits"):JS.index("function drawNodes")]
    assert 'svgEl("title"' in hits and "edgeCaption(" in hits
    assert 'getElementById("edge-info")' in hits and "stopPropagation" in hits
    assert "sameLane(graph, fibre.from, fibre.to)) return" in hits
    assert "if (key === edgeKey) fresh = caption" in hits and 'textContent = fresh || ""' in hits
    assert 'r="2.5"' not in INDEX  # legend swatches match REPLAY_DOT_R / AMBIENT_DOT_R (3)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_ambient_interval_runs():
    script = (
        "const src = process.argv[1];"
        "const n = (k) => +src.match(new RegExp('const ' + k + ' = (\\\\d+);'))[1];"
        "const AMBIENT_BUSIEST_S = n('AMBIENT_BUSIEST_S');"
        "function num(value) { return typeof value === 'number' && isFinite(value) ? value : 0; }"
        "eval(src.slice(src.indexOf('function ambientInterval'), src.indexOf('function ambInitial')));"
        "console.log(JSON.stringify(["
        "ambientInterval(0, 10), ambientInterval(-3, 10), ambientInterval(undefined, 10),"
        "ambientInterval(10, 10), ambientInterval(5, 10)]));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    zero, neg, missing, busy, half = out
    assert zero is None and neg is None and missing is None
    busiest_s = int(re.search(r"^  const AMBIENT_BUSIEST_S = (\d+);$", JS, re.M).group(1))
    assert busy == busiest_s * 1000
    assert half == busy * 2


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_edge_caption_runs():
    script = (
        "const src = process.argv[1];"
        "function pad(n) { return (n < 10 ? '0' : '') + n; }"
        "function hhmm(d) { return pad(d.getHours()) + ':' + pad(d.getMinutes()); }"
        "function num(value) { return typeof value === 'number' && isFinite(value) ? value : 0; }"
        "eval(src.slice(src.indexOf('function edgeCaption'), src.indexOf('function drawHits')));"
        "const last = Date.parse('2026-10-02T18:05:00Z');"
        "console.log(JSON.stringify(["
        "edgeCaption('Design', 'VP', 1, 0, last),"
        "edgeCaption('Design', 'VP', 4, 1, last),"
        "edgeCaption('Design', 'VP', 2, 3, last),"
        "edgeCaption('Design', 'VP', 15, 0, last),"
        "hhmm(new Date(last))]));"
    )
    one, mention, many, past_cap, hm = json.loads(
        subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout
    )
    assert one == f"Design \u2192 VP \u00b7 1 message, 0 mentions in 24 h \u00b7 last at {hm}"
    assert mention == f"Design \u2192 VP \u00b7 4 messages, 1 mention in 24 h \u00b7 last at {hm}"
    assert many == f"Design \u2192 VP \u00b7 2 messages, 3 mentions in 24 h \u00b7 last at {hm}"
    assert past_cap.startswith("Design \u2192 VP \u00b7 15 messages, 0 mentions in 24 h")
    # The hover reads the server's uncapped totals, never the strand counts capped at 12.
    call = JS[JS.index("const caption = edgeCaption("):]
    call = call[: call.index(");")]
    assert "fibre.total_messages, fibre.total_mentions" in call and "fibre.messages" not in call


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_focus_row_count_runs():
    script = (
        "const src = process.argv[1];"
        "function num(value) { return typeof value === 'number' && isFinite(value) ? value : 0; }"
        "eval(src.slice(src.indexOf('function rowCount'), src.indexOf('function partnersOf')));"
        "console.log(JSON.stringify([rowCount(12, 3), rowCount(0, 3), rowCount(1, 0), rowCount(1, 1), rowCount(15, 0)]));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out == ["12 messages, 3 mentions", "3 mentions", "1 message", "1 message, 1 mention", "15 messages"]
    body = JS[JS.index("function partnersOf"):JS.index("function piece")]
    assert "fibre.total_messages" in body and "fibre.total_mentions" in body and "fibre.messages)" not in body


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_focus_partners_and_resolve_run():
    script = (
        "const src = process.argv[1];"
        "function num(value) { return typeof value === 'number' && isFinite(value) ? value : 0; }"
        "function nodeById(nodes, id) {"
        "  if (!nodes || id === null || id === undefined) return null;"
        "  if (!Object.prototype.hasOwnProperty.call(nodes, id)) return null;"
        "  return nodes[id];"
        "}"
        "eval(src.slice(src.indexOf('function resolveFocus'), src.indexOf('function stageFocus')));"
        "eval(src.slice(src.indexOf('function partnersOf'), src.indexOf('function piece')));"
        "const graph = {"
        "  nodes: { owner: {}, vp: {}, design: {}, stray: {} },"
        "  lanes: [{ nodes: ['design', 'vp'] }, { nodes: ['stray'] }],"
        "  fibres: ["
        "    { from: 'owner', to: 'vp', total_messages: 2, total_mentions: 1 },"
        "    { from: 'vp', to: 'owner', total_messages: 3, total_mentions: 9 },"
        "    { from: 'design', to: 'owner', total_messages: 5, total_mentions: 0 },"
        "    { from: 'owner', to: 'stray', total_messages: 1, total_mentions: 2 }"
        "  ]"
        "};"
        "const rows = partnersOf(graph, 'owner');"
        "const noowner = { nodes: { z: {}, a: {} }, lanes: [{ nodes: ['z', 'a'] }], fibres: [] };"
        "console.log(JSON.stringify({"
        "  ids: rows.map(function (r) { return r.id; }),"
        "  vp: rows.filter(function (r) { return r.id === 'vp'; })[0],"
        "  focus: resolveFocus(graph, null),"
        "  keep: resolveFocus(graph, 'design'),"
        "  first: resolveFocus(noowner, null)"
        "}));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out["ids"] == ["design", "vp", "stray"]
    assert out["vp"]["messages"] == 5 and out["vp"]["mentions"] == 10
    assert out["focus"] == "owner"
    assert out["keep"] == "design"
    assert out["first"] == "z"


def test_focus_row_label_and_stamp_keep_active():
    rows = JS[JS.index("function drawPartners"):JS.index("function layoutRail")]
    assert 'bits.push("working now")' in rows
    sel = JS[JS.index("function select("):JS.index("function select(") + 400]
    assert "narrow && sel === id" in sel  # a deselect never moves the focus view
    wide = JS[JS.index("const onNarrow"):JS.index("const onNarrow") + 700]
    assert "ambInitial(amb[key], t)" in wide  # no burst of ambient dots after narrow -> wide
    start = JS.index("const onNarrow")
    branch = JS[start:JS.index("if (!focusId", start)]
    branch = branch[branch.index("if (!next)"):]
    assert branch.index("draw(current.graph)") < branch.index("ambInitial(amb[key], t)")  # redraw, then re-seed, so the ambient dots never burst
    stamp = JS[JS.index("function focusStamp"):JS.index("function renderFocus")]
    assert "node.active === true" in stamp


def test_problems_section_html():
    prov = INDEX.index('id="prov"')
    block = INDEX.index('<section id="problems" class="problems" aria-labelledby="problems-h">')
    graph = INDEX.index('<section class="graph"')
    assert prov < graph < block  # Now holds the graph; Work follows it
    assert '<h2 id="problems-h">Problems</h2>' in INDEX
    assert '<ul id="problem-list"></ul>' in INDEX


def test_problems_list_js():
    assert '"/api/problems"' in JS
    assert 'credentials: "omit"' in JS
    assert "stamp === problemsDrawn" in JS
    assert "PROBLEM_CAP = 8" in JS
    assert "PROBLEM_CAP - 1" in JS
    assert "Nothing needs attention." in JS
    assert "Show all " in JS
    assert "problemsExpanded" in JS
    assert 'el("button", "problem-more"' in JS
    assert 'setAttribute("aria-expanded"' in JS
    assert 'setAttribute("aria-controls", "problem-list")' in JS
    assert '"Failed \\u00b7 "' in JS
    stamp = JS[JS.index("function problemsStamp"):JS.index("function splitProblems")]
    assert "problemsExpanded" in stamp
    assert "staleExpanded" in stamp and "splitProblems(items" in stamp  # the fold split is in the stamp
    body = JS[JS.index("function renderProblems"):JS.index("function loadProblems")]
    assert "select(" in body
    assert 'el("button", "problem-more problem-fold"' in body
    assert 'fold.type = "button"' in body
    assert 'setAttribute("aria-expanded"' in body
    assert "Stale PRs (" in body
    assert 'Show all " + view.fresh.length + " problems"' in body
    assert 'hl.appendChild(el("span", "problem-id", row.jobId))' in JS  # the id rides on the headline, never in the 7ch age track
    assert "innerHTML" not in JS


def test_problems_css():
    high = re.search(r"\.problem\.sev-high \.problem-glyph \{([^}]*)\}", CSS).group(1)
    mid = re.search(r"\.problem\.sev-medium \.problem-glyph \{([^}]*)\}", CSS).group(1)
    low = re.search(r"\.problem\.sev-low \.problem-glyph \{([^}]*)\}", CSS).group(1)
    assert "var(--red)" in high and "var(--amber)" in mid and "var(--ink-3)" in low
    button = re.search(r"\.problem \{([^}]*)\}", CSS).group(1)
    assert "min-height: 44px" in button
    age = re.search(r"\.problem-age \{([^}]*)\}", CSS).group(1)
    assert "var(--mono)" in age
    assert "@keyframes" not in CSS[CSS.index(".problems"):CSS.index(".graph {")]
    assert "text-align: start" in re.search(r"td button \{([^}]*)\}", CSS).group(1)
    narrow_open = CSS.index("{", CSS.index("@media (max-width: 1099.98px)"))
    narrow = CSS[narrow_open + 1:_brace_end(CSS, narrow_open)]
    hl = re.search(r"\.problem-hl \{([^}]*)\}", narrow).group(1)
    assert "overflow-wrap: anywhere" in hl
    assert "text-overflow: ellipsis" not in hl


def test_lower_blocks_cap_at_1800():
    assert re.findall(r"^\.(?:problems|below) \{[^}]*max-width: ([^;]+);", CSS, re.M) == [
        "min(100%, 1800px)", "min(100%, 1800px)"] and "1440px" not in CSS


def test_problems_two_columns_from_1800():
    at = CSS.index("@media (min-width: 1800px)")
    block = CSS[at:_brace_end(CSS, CSS.index("{", at)) + 1]
    assert "#problem-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 32px; }" in block
    assert "#problem-list > li:has(.problem-more) { grid-column: 1 / -1; }" in block
    assert ".problem {" not in block and ".problem-more {" not in block
    assert "display: grid" not in re.search(r"#problem-list \{([^}]*)\}", CSS).group(1)
    assert CSS.count("repeat(2, minmax(0, 1fr))") == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_problem_row_runs():
    script = (
        "const src = process.argv[1];"
        "eval(src.slice(src.indexOf('function ageOf'), src.indexOf('function glyph')));"
        "function missing(value) { return value === null || value === undefined; }eval(src.slice(src.indexOf('function problemRow'), src.indexOf('function problemsStamp')));"
        "function labels(id) { return id === 'vp' ? 'VP' : id; }"
        "const now = Date.parse('2026-10-02T12:00:00Z');"
        "function row(severity, headline, owner, since, kind, id) {"
        "  return problemRow({ severity: severity, headline: headline, owner: owner, since: since, kind: kind, id: id }, labels, now);"
        "}"
        "console.log(JSON.stringify({"
        "  high: row('high', 'A job failed', 'vp', '2026-10-02T09:00:00Z'),"
        "  mid: row('medium', 'Waiting', 'design', '2026-10-02T11:30:00Z'),"
        "  low: row('low', 'Quiet', 'no-such', '2026-10-01T12:00:00Z'),"
        "  fresh: row('low', 'Just now', 'no-such', '2026-10-02T11:59:30Z'),"
        "  split: row('medium', 'Stale', 'design', '2026-09-30T09:00:00Z'),"
        "  failed: row('high', '1 factory job failed', 'vp', '2026-10-02T09:00:00Z', 'job-failed', 'job-failed:job-9')"
        "}));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out["high"]["glyph"] == "\u25b2"
    assert out["mid"]["glyph"] == "\u25c6"
    assert out["low"]["glyph"] == "\u25bd"
    assert out["high"]["owner"] == "VP"
    assert out["mid"]["owner"] == "design"
    assert out["low"]["owner"] == "no-such"
    assert out["high"]["age"] == "3h"
    assert out["mid"]["age"] == "30m"
    assert out["low"]["age"] == "1d"
    assert out["fresh"]["age"] == "now"
    assert out["split"]["age"] == "2d 3h"
    assert out["high"]["label"] == "high: A job failed, owner VP, 3h"
    assert out["low"]["label"] == "low: Quiet, owner no-such, 1d"
    assert out["failed"]["headline"] == "Failed \u00b7 1 factory job failed"
    assert out["failed"]["jobId"] == "job-9"
    assert out["failed"]["label"] == "high: Failed \u00b7 1 factory job failed, owner VP, 3h, job-9"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_split_problems_runs():
    script = (
        "const src = process.argv[1];"
        "function missing(value) { return value === null || value === undefined; }eval(src.slice(src.indexOf('function splitProblems'), src.indexOf('function problemView')));"
        "const now = Date.parse('2026-10-03T12:00:00Z');"
        "const day = 86400000;"
        "function at(days) { return new Date(now - days * day).toISOString(); }"
        "const items = ["
        "  { id: 'ci-red:coco-courier#157', kind: 'ci-red', since: at(8) },"
        "  { id: 'pr-stale:coco-courier#157', kind: 'pr-stale', since: at(8) },"
        "  { id: 'ci-red:coco-atlas#3', kind: 'ci-red', since: at(2) },"
        "  { id: 'job-failed:job-9', kind: 'job-failed', since: at(30) },"
        "  { id: 'ci-red:coco-atlas#9', kind: 'ci-red', since: 'not-a-date' }"
        "];"
        "const out = splitProblems(items, now);"
        "console.log(JSON.stringify({"
        "  stale: out.stale.map(function (p) { return p.id; }),"
        "  fresh: out.fresh.map(function (p) { return p.id; }),"
        "  prs: out.prs"
        "}));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out["stale"] == ["ci-red:coco-courier#157", "pr-stale:coco-courier#157"]
    assert out["prs"] == 1
    assert out["fresh"] == ["ci-red:coco-atlas#3", "job-failed:job-9", "ci-red:coco-atlas#9"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_stale_pr_rows():
    stale_fn = JS[JS.index("function staleRow"):JS.index("function problemView")]
    assert "▽" not in stale_fn and "\\u25bd" not in stale_fn
    body = JS[JS.index("function renderProblems"):JS.index("function loadProblems")]
    assert "prRows.length" in body
    rule = re.search(r"\.problem\.sev-stale \.problem-glyph \{([^}]*)\}", CSS).group(1)
    assert "var(--ink);" in rule
    script = (
        "const src = process.argv[1];"
        "eval(src.slice(src.indexOf('function ageOf'), src.indexOf('function glyph')));"
        "function missing(value) { return value === null || value === undefined; }eval(src.slice(src.indexOf('function splitProblems'), src.indexOf('function problemView')));"
        "const now = Date.parse('2026-10-03T12:00:00Z');"
        "const day = 86400000;"
        "function at(days) { return new Date(now - days * day).toISOString(); }"
        "const items = ["
        "  { id: 'ci-red:r#1', kind: 'ci-red', since: at(40), owner: 'vp' },"
        "  { id: 'pr-stale:r#1', kind: 'pr-stale', since: at(30), owner: 'other' },"
        "  { id: 'pr-stale:k#4', kind: 'pr-stale', since: at(9), owner: 'design' },"
        "  { id: 'ci-red:fresh#2', kind: 'ci-red', since: at(1), owner: 'vp' }"
        "];"
        "const out = splitProblems(items, now);"
        "function labels(id) { return id === 'vp' ? 'VP' : id; }"
        "const red = staleRow(out.prRows[0], labels, now);"
        "const quiet = staleRow(out.prRows[1], labels, now);"
        "const voice = staleRow({ subject: 'Coco-Voice#1', red: true, untouchedDays: null, owner: 'voice', since: at(12) }, labels, now);"
        "console.log(JSON.stringify({"
        "  prs: out.prs,"
        "  rows: out.prRows.map(function (row) {"
        "    return { subject: row.subject, red: row.red, untouchedDays: row.untouchedDays, owner: row.owner };"
        "  }),"
        "  oldest: out.prRows[0].since === items[0].since,"
        "  red: red, quiet: quiet, voice: voice.headline"
        "}));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out["prs"] == 2
    assert out["rows"] == [
        {"subject": "r#1", "red": True, "untouchedDays": 30, "owner": "vp"},
        {"subject": "k#4", "red": False, "untouchedDays": 9, "owner": "design"},
    ]
    assert out["oldest"] is True
    assert out["red"]["glyph"] == "\u25b2" and out["red"]["sev"] == "sev-high"
    assert out["red"]["headline"] == "r #1 \u00b7 CI red \u00b7 untouched 30d"
    assert out["red"]["label"] == "stale: r #1 \u00b7 CI red \u00b7 untouched 30d, owner VP, 40d"
    assert out["quiet"]["glyph"] == "\u25cb" and out["quiet"]["sev"] == "sev-stale"
    assert out["quiet"]["headline"] == "k #4 \u00b7 untouched 9d"
    assert out["quiet"]["label"] == "stale: k #4 \u00b7 untouched 9d, owner design, 9d"
    assert out["voice"] == "Coco-Voice #1 \u00b7 CI red"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_replay_stays_off_when_narrow():
    script = (
        "const vm=require('vm'),src=process.argv[1],STATE={decisions:[],bus:{queue:{failed:0}},graph:{messages_24h:1,"
        "mentions_24h:0,lanes:[{id:'a',nodes:['owner']},{id:'b',nodes:['vp']}],nodes:{owner:{label:'Owner',state:'idle'},"
        "vp:{label:'VP',state:'idle'}},fibres:[],recent:[{from:'vp',to:'owner',ts:'2020-01-01T00:00:00Z',kind:'post',id:'e1'}]}};"
        "function boot(narrow0){const ids={},bag=new WeakMap();const node=()=>{const kids=[],n={id:'',className:'',"
        "textContent:'',hidden:false,value:'',style:{},dataset:{},parentNode:null,namespaceURI:null,offsetHeight:10,"
        "offsetWidth:10,isConnected:true,get firstChild(){return kids[0]||null},setAttribute(k,v){n['_'+k]=String(v)},"
        "getAttribute(k){return n['_'+k]??null},removeAttribute(k){delete n['_'+k]},appendChild(c){kids.push(c);"
        "if(c)c.parentNode=n;return c},removeChild(c){const i=kids.indexOf(c);if(i>=0)kids.splice(i,1);"
        "if(c)c.parentNode=null;return c},insertBefore(c){return n.appendChild(c)},addEventListener(){},"
        "querySelector(){return null},querySelectorAll(){return []},getBoundingClientRect(){return{top:0,height:10}},"
        "getTotalLength(){return 100},getPointAtLength(){return{x:0,y:0}},getBBox(){return{width:10,height:10}},"
        "focus(){},remove(){if(n.parentNode)n.parentNode.removeChild(n)}};"
        "n.classList={add(){},remove(){},toggle(){},contains(){return false}};bag.set(n,kids);return n};"
        "const text=o=>!o?'':(o.textContent||'')+' '+(bag.get(o)||[]).map(text).join(' ');"
        "['g','theme','show','theme-sun','theme-moon','ticker','motion-legend','edge-info','prov','lede','lg-broken',"
        "'clock','panel','focus-stage','focus-box','focus-partners','focus-rail'].forEach(id=>{ids[id]=node();ids[id].id=id});"
        "const tbody=node();let hb,mq;const document={hidden:false,documentElement:{dataset:{}},activeElement:null,"
        "getElementById:id=>ids[id]||null,createElement:node,createElementNS:()=>Object.assign(node(),{namespaceURI:'http://www.w3.org/2000/svg'}),"
        "createTextNode:t=>{const tn=node();tn.textContent=String(t);return tn},"
        "querySelector:s=>s==='#tbl tbody'?tbody:null,querySelectorAll:()=>[],addEventListener(){}};"
        "const sandbox={document,performance,AbortController,console,setTimeout:()=>1,clearTimeout(){},clearInterval(){},"
        "requestAnimationFrame:()=>1,setInterval:(fn,ms)=>{if(ms===1000)hb=fn;return 1},"
        "EventSource:function(){this.addEventListener=()=>{}},fetch:()=>Promise.resolve({ok:true,json:()=>Promise.resolve(STATE)}),"
        "window:{addEventListener(){},matchMedia(q){const m={matches:q.includes('max-width')?narrow0:false,"
        "addEventListener(_t,fn){m._fn=fn},addListener(fn){m._fn=fn}};if(q.includes('max-width'))mq=m;return m}}};"
        "vm.createContext(sandbox);vm.runInContext(src,sandbox);return{text,ids,hb:()=>hb(),fire(){mq.matches=true;mq._fn()}}}"
        "(async()=>{const a=boot(true),b=boot(false);await new Promise(r=>setImmediate(r));a.hb();b.hb();"
        "const before=b.text(b.ids.ticker);b.fire();console.log(JSON.stringify({narrow:a.text(a.ids.ticker),before,"
        "after:b.text(b.ids.ticker)}))})().catch(e=>{console.error(e);process.exit(1)});"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert "Replay" not in out["narrow"] and "Replay" in out["before"] and "Replay" not in out["after"]

def test_phone_legend_lists_live_only():
    narrow_open = CSS.index("{", CSS.index("@media (max-width: 1099.98px)"))
    narrow = CSS[narrow_open + 1:_brace_end(CSS, narrow_open)]
    assert ".motion-legend .ml-replay, .motion-legend .ml-amb { display: none; }" in narrow
    assert "ml-live" not in narrow


def test_problem_click_reads_the_current_owner():
    # A patched row keeps its button; the click must use the owner written on it, not the one it was built with.
    body = JS[JS.index("function renderProblems"):JS.index("function loadProblems")]
    assert "btn.dataset.owner" in body and "select(ownerId)" in body and "select(problem.owner)" not in body
    patch = JS[JS.index("function patchProblems"):JS.index("function renderProblems")]
    assert "dataset.owner" in patch


def test_problem_age_never_uses_an_unsynced_clock():
    # Problems can load before /api/state sets nowMs; a 0 clock would label every row "now".
    body = JS[JS.index("function problemRow"):JS.index("function problemsStamp")]
    assert "now || Date.now()" in body


def test_page_ships_no_private_words():
    # Words that must never ship (the owner's real name, say) come from the environment, so neither they nor anything
    # derived from them is in the repo. Set ATLAS_PRIVATE_WORDS="word,word" locally; CI has no private words to check.
    words = [w.strip().lower() for w in os.environ.get("ATLAS_PRIVATE_WORDS", "").split(",") if w.strip()]
    if not words:
        pytest.skip("ATLAS_PRIVATE_WORDS is not set")
    # Every served text asset, not just the page's own three files.
    served = [p for p in STATIC.rglob("*") if p.suffix in (".js", ".css", ".html")]
    text = re.sub(r"[^a-z]", "", "".join(p.read_text(encoding="utf-8") for p in served).lower())
    normalised = [n for w in words if (n := re.sub(r"[^a-z]", "", w))]  # an all-symbol word would match everything
    if not normalised:
        pytest.skip("ATLAS_PRIVATE_WORDS has no letters to check")  # skipped, never a silent pass
    assert [n for n in normalised if n in text] == []


# ---- orbs: Harbor's seeded avatar at v1.1 (harbor-ng#305) ----

# Harbor orbModel.ts (v1.0, commit 1e557c4b) run as shipped, then frozen. Per id: fnv1a, the first draw of seed ^ 227, the animation
# delay at 40px, then highlight x y, glow x y opacity and gradient radius to 3 places. v1.1 burns the old hue-step draw, so its
# stream is v1.0's again and an unplanned orb's geometry is still these numbers. The v1.0 tones are retired with the palette.
_NG_ORBS = {
    "vp": (1430905659, 0.07928814645856619, "-0.89", [28.493, 35.63, 72.517, 60.917, 0.306, 81.866]),
    "brain-atlas": (1073189089, 0.1576895818579942, "-1.32", [81.689, 43.697, 22.914, 60.475, 0.374, 88.309]),
    "dept-design": (390821643, 0.7596478576306254, "-0.09", [75.029, 45.022, 20.518, 44.329, 0.407, 81.519]),
    "factory-pi": (541090075, 0.8242537698242813, "-0.36", [68.481, 22.342, 28.907, 68.637, 0.369, 89.265]),
    "github": (584874290, 0.3836398108396679, "-1.17", [64.341, 28.538, 33.818, 68.91, 0.396, 81.812]),
}
# NG's orbModel.test.ts v1.1 vector for an unplanned harbour-desk: hue, light, mid, deep (light theme), deep (dark), highlight
# position, gradient radius, glow opacity.
_NG_HARBOUR = [176, "hsl(176.0 60% 84.0%)", "hsl(176.0 60% 60.0%)", "hsl(176.0 60% 42.0%)", "hsl(176.0 60% 42.0%)", 5,
               83.44473939109594, 0.30023315503494813]
# The spec's slot table, in the spec's order: hue and mid-tone L. S is 60% throughout.
_SLOTS = [(176, 38), (248, 38), (320, 60), (176, 60), (248, 60), (320, 38), (212, 38), (212, 60), (284, 38), (284, 60)]
_STEPS = [-4, 0, 4]

# Harbor's server rule, re-typed from harbor-ng#305 (backend/app/employee_orb.py) so that a drift on either side fails here: one id
# has to be one orb in both products. Atlas's lanes share no node, so NG's `placed` and `blocked` rules (a member an earlier team
# placed) never fire, and the slot walk below is only the per-team part of assign_orb_slots.
_NG_SPREAD = (0, 7, 3, 5, 1, 6, 2, 4)
# NG's test ids that all prefer slot 0 (backend/tests/test_employee_orb.py COLLISION), zero-padded so string order is numeric order.
_NG_COLLISION = ["m0021", "m0052", "m0056", "m0058", "m0061", "m0067", "m0083", "m0085", "m0087", "m0089", "m0094", "m0102"]


def _ng_fnv1a(text):
    h = 0x811C9DC5
    for ch in text:
        h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    return h


def _ng_base_slot(ident):
    return ((_ng_fnv1a(ident) ^ 227) & 0xFFFFFFFF) % 10


def _ng_mulberry32(seed):
    a = seed & 0xFFFFFFFF

    def rnd():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((a ^ (a >> 15)) * (1 | a)) & 0xFFFFFFFF
        t = (t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF) ^ t) & 0xFFFFFFFF
        return (t ^ (t >> 14)) / 4294967296

    return rnd


def _ng_seeded(ident):
    """NG's lightness step and seeded highlight: the step draw, the burned hue-step draw, then the highlight draw."""
    rnd = _ng_mulberry32(_ng_fnv1a(ident) ^ 227)
    step = _STEPS[int(rnd() * 3)]
    rnd()
    return step, int(rnd() * 8)


def _ng_far_step(first_step):
    return -4 if first_step == 4 else 4


def _ng_distinct_step(taken, seeded):
    if seeded not in taken:
        return seeded
    for candidate in (0, -4, 4):
        if candidate not in taken:
            return candidate
    return seeded


def _ng_highlights_for_slot(member_ids):
    used, out, first_step = {}, {}, 0
    for k, ident in enumerate(sorted(member_ids)):
        highlight, seeded = _NG_SPREAD[k % 8], _ng_seeded(ident)[0]
        taken = used.setdefault(highlight, set())
        if k == 0:
            step = first_step = seeded
        elif k == 1:
            step = _ng_far_step(first_step)
        elif k >= 8:
            step = _ng_distinct_step(taken, seeded)
        else:
            step = seeded
        taken.add(step)
        out[ident] = (highlight, step)
    return out


def _ng_orbs(lanes):
    """{id: [slot, highlight, step]} for lanes of ids: each lane's slot walk, then the highlights per slot across every lane."""
    slots = {}
    for members in lanes:
        counts = [0] * 10
        for index, ident in enumerate(sorted(members)):
            slot = _ng_base_slot(ident)
            if index >= 10:
                slot = counts.index(min(counts))
            elif counts[slot] != 0:
                free = next((c for c in range(10) if counts[c] == 0), None)
                slot = counts.index(min(counts)) if free is None else free
            slots[ident] = slot
            counts[slot] += 1
    out = {}
    for slot in set(slots.values()):
        for ident, (highlight, step) in _ng_highlights_for_slot([i for i, s in slots.items() if s == slot]).items():
            out[ident] = [slot, highlight, step]
    return out


def _on_slot(slot, count, prefix, step=None):
    """The first `count` ids `prefix` + n whose base slot is `slot` (and seeded step `step`, if given) by NG's rule. Bounded: a miss fails."""
    found = [i for i in (f"{prefix}{n}" for n in range(5000)) if _ng_base_slot(i) == slot and step in (None, _ng_seeded(i)[0])][:count]
    assert len(found) == count
    return found


def _graph_node(tail: str):
    """Run `tail` in node with graph.js's constants and its functions (pad .. unrelated, then the phone's drawBox .. railD) in
    scope, on a fake DOM."""
    script = (
        "const src = process.argv[1];"
        "const part = (a, b) => src.slice(src.indexOf(a), src.indexOf(b));"
        "const dom = [], hosts = {};"
        "const mk = (tag) => { const n = { tag, attrs: {}, kids: [], style: {}, setAttribute(k, v) { this.attrs[k] = String(v); },"
        " appendChild(c) { this.kids.push(c); return c; }, addEventListener() {},"
        " getComputedTextLength() { return svg.measure ? svg.measure(this.textContent || '') : 0; } }; dom.push(n); return n; };"
        "const document = { createElementNS: (ns, tag) => mk(tag), createElement: mk, createTextNode: (t) => ({ textContent: t }),"
        " getElementById: (id) => hosts[id] || (hosts[id] = mk('div')), activeElement: null, documentElement: { dataset: { theme: 'light' } } };"
        "const svg = mk('svg'), win = { innerHeight: 5000 };"
        "const api = new Function('document', 'svg', 'window', part('const NS =', 'const svg =')"
        " + 'let pos = {}, H = 560, W = W_MIN, laneX = LX, narrow = false, current = null, drawCount = 0, nodeEls = {};'"
        " + 'function draw(graph) { drawCount += 1; layout(graph); }' + part('function pad', 'function unrelated')"
        " + part('function knownNodes', 'function railD') + 'function layoutRail() {}'"
        " + 'return { layout, drawLanes, drawNodes, drawOrb, drawOrbCell, drawBox, drawPartners, wirePath, arcPath, orbDraw, planOrbs, fnv1a,"
        " mulberry32, animationDelay, fitScale, relayout, clipName, pos: () => pos, H: () => H, W: () => W, laneX: () => laneX,"
        " draws: () => drawCount, show: (graph) => { current = { graph }; }, narrow: (on) => { narrow = on; }, setPlan: (g) => (orbPlan = planOrbs(g)), slots: ORB_SLOTS, steps: ORB_STEPS, sat: ORB_SAT, spread: ORB_SPREAD,"
        " c: { PITCH, PITCH_COMPACT, NODE_H, COMPACT_H, TOP, BW, RING_GAP, COMPACT_NAME_X, COMPACT_NAME_MAX, COMPACT_CHAR_W, ORB_R, LX, W_MIN } };')(document, svg, win);"
        + tail
    )
    done = subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(done.stdout)


def _real_graph(httpd):
    status, _headers, body = _request(httpd, "/api/state")
    assert status == 200
    return json.loads(body)["graph"]


def _srgb(h, s, l):
    return colorsys.hls_to_rgb(h / 360.0, l / 100.0, s / 100.0)


def _oklab(rgb):
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb)
    cone = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
            0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
            0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    l, m, s = (math.copysign(abs(c) ** (1 / 3), c) for c in cone)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _tone(text):
    """'hsl(176.0 60% 38.0%)' -> (176.0, 60.0, 38.0)"""
    found = re.fullmatch(r"hsl\(([\d.]+) ([\d.]+)% ([\d.]+)%\)", text)
    assert found, text
    return tuple(float(x) for x in found.groups())


def _signal(dark):
    """The Signal colour tokens as {name: '#rrggbb'}; the dark theme overrides what it sets and inherits the rest."""
    css = (STATIC / "signal" / "signal.css").read_text(encoding="utf-8")

    def block(pattern):
        found = re.search(pattern, css, re.S)
        assert found, f"signal.css has no block matching {pattern}"
        return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", found.group(1)))

    tokens = block(r":root \{(.*?)\n\}")
    if dark:
        tokens.update(block(r'\[data-theme="dark"\] \{(.*?)\n\}'))
    return tokens


def _hex_oklab(code):
    return _oklab(tuple(int(code[i:i + 2], 16) / 255 for i in (1, 3, 5)))


@functools.lru_cache(maxsize=None)
def _tones():
    """Every slot at every step in both themes, as the page computes them."""
    return _graph_node(r"""
        const rows = [];
        for (let slot = 0; slot < 10; slot++) api.steps.forEach((step) => {
          const l = api.orbDraw('vp', false, { slot, step }), d = api.orbDraw('vp', true, { slot, step });
          rows.push({ slot, step, hue: l.hue, light: l.light, mid: l.mid, deep: l.deep, lightDark: d.light, midDark: d.mid, deepDark: d.deep });
        });
        console.log(JSON.stringify(rows));
    """)


def test_the_plan_is_made_once_per_render_and_every_orb_reads_it():
    render = JS[JS.index("function render("):JS.index("function tick(")]
    assert render.index("orbPlan = planOrbs(graph)") < render.index("draw(graph)") < render.index("table(graph)")
    assert JS.count("planOrbs(") == 2  # its definition and the one call in render
    assert "orbDraw(id, darkNow(), orbPlan[id])" in JS[JS.index("function drawOrb"):JS.index("function drawRings")]
    # the colour model is orbDraw only, and the code says so
    assert "The colour model is orbDraw and only orbDraw" in JS[JS.index("// ---- orbs:"):JS.index("const ORB_SLOTS")] and "Atlas's reading" not in JS


def test_orb_tones_come_from_one_function():
    assert JS.count('"hsl("') == 1
    assert '"hsl("' in JS[JS.index("function orbDraw"):JS.index("function animationDelay")]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orb_hash_and_stream_match_ng_runs():
    out = _graph_node(r"""
        function ngFnv(s) { let h = 0x811c9dc5; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); } return h >>> 0; }
        function ngMul(seed) { let a = seed; return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a);
          t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
        const probe = ['vp', 'owner', 'brain-atlas', 'dept-design', 'factory-pi', 'github', '', 'café'];
        const hash = probe.every((id) => api.fnv1a(id) === ngFnv(id));
        const draws = probe.every((id) => { const a = api.mulberry32(ngFnv(id) ^ 227), b = ngMul(ngFnv(id) ^ 227);
          return [1, 2, 3, 4, 5, 6, 7].every(() => a() === b()); });
        const rows = {}, r = (v) => +v.toFixed(3);
        ['vp', 'brain-atlas', 'dept-design', 'factory-pi', 'github'].forEach((id) => {
          const o = api.orbDraw(id, false);  // unplanned: the orb is its own seed
          rows[id] = [api.fnv1a(id), api.mulberry32(api.fnv1a(id) ^ 227)(), api.animationDelay(id, 40, 'working'),
            [r(o.highlight.x), r(o.highlight.y), r(o.glow.x), r(o.glow.y), r(o.glow.opacity), r(o.gradientRadius)]];
        });
        const h = api.orbDraw('harbour-desk', false), d = api.orbDraw('harbour-desk', true);
        console.log(JSON.stringify({ hash, draws, rows, harbour: [h.hue, h.light, h.mid, h.deep, d.deep, h.index, h.gradientRadius, h.glow.opacity] }));
    """)
    assert out["hash"] and out["draws"]
    for ident, golden in _NG_ORBS.items():
        assert out["rows"][ident] == list(golden), ident
    assert out["harbour"] == _NG_HARBOUR


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orb_slot_table_is_the_spec():
    out = _graph_node("console.log(JSON.stringify({ slots: api.slots, steps: api.steps, sat: api.sat, spread: api.spread }));")
    assert out["slots"] == [list(slot) for slot in _SLOTS]
    assert out["steps"] == _STEPS and out["sat"] == 60
    assert out["spread"] == list(_NG_SPREAD)  # far left, far right, top centre, then the spots in between
    for gone in ("ORB_LANE", "ORB_FAMILY", "ORB_DEFAULT", "ORB_HUE_STEPS", "ORB_LIGHTNESS", "orbFamily", "Math.max(185"):
        assert gone not in JS  # v1.1 drops the family mapping, the hue step and the 185-248 clamp


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orb_draw_is_seeded_runs():
    out = _graph_node(r"""
        function ngFnv(s) { let h = 0x811c9dc5; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); } return h >>> 0; }
        const ids = Array.from({ length: 60 }, (_, i) => 'part-' + i);
        const j = (id, dark) => JSON.stringify(api.orbDraw(id, dark));
        const names = ['owner', 'vp', 'hq-cos', 'dept-design', 'brain-atlas', 'factory-queue', 'github', 'users'];
        console.log(JSON.stringify({
          seeded: ids.map((id) => [api.orbDraw(id, false).slot, ((ngFnv(id) ^ 227) >>> 0) % 10]),
          steps: Array.from(new Set(ids.map((id) => api.orbDraw(id, false).step))).sort((a, b) => a - b),
          stable: j('vp', false) === j('vp', false), themed: j('vp', false) !== j('vp', true),
          distinct: new Set(names.map((id) => j(id, false))).size, names: names.length
        }));
    """)
    # base slot = the unsigned integer fnv1a(id) ^ 227, before any draw, mod 10: NG's base_slot
    assert all(slot == expected for slot, expected in out["seeded"])
    assert set(out["steps"]) <= set(_STEPS) and len(out["steps"]) == 3  # the stream reaches all three lightness steps
    assert out["stable"] and out["themed"] and out["distinct"] == out["names"]  # same id, same orb; other ids, other orbs


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orb_tones_follow_the_slot_and_the_step():
    for row in _tones():
        hue, mid_l = _SLOTS[row["slot"]]
        step = row["step"]
        assert _tone(row["light"]) == (hue, 60, mid_l + step + 24)
        assert _tone(row["mid"]) == (hue, 60, mid_l + step)
        assert _tone(row["deep"]) == (hue, 60, mid_l + step - 18)
        # dark theme: the same light and mid, and a rim that never goes below L 30
        assert _tone(row["lightDark"]) == _tone(row["light"]) and _tone(row["midDark"]) == _tone(row["mid"])
        assert _tone(row["deepDark"]) == (hue, 60, max(30, mid_l + step - 18))
        assert _tone(row["deepDark"])[2] >= 30


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orb_slots_are_far_apart_in_oklab():
    mids = [_oklab(_srgb(*_tone(row["mid"]))) for row in _tones() if row["step"] == 0]
    assert len(mids) == 10
    assert min(math.dist(a, b) for a, b in itertools.combinations(mids, 2)) >= 0.085  # the spec says 0.089
    assert min(math.dist(a, b) for a, b in itertools.combinations(mids[:3], 2)) >= 0.27  # the first 3 in order
    assert min(math.dist(a, b) for a, b in itertools.combinations(mids[:5], 2)) >= 0.175  # the first 5 (the spec rounds to 0.18)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orb_hues_stay_out_of_the_status_bands():
    # By construction: Signal's red, amber and green (fill and text, both themes) are at least 10 degrees from every slot hue.
    status = []
    for dark in (False, True):
        tokens = _signal(dark)
        for name in ("red", "red-fill", "amber", "amber-fill", "green", "green-fill"):
            rgb = tuple(int(tokens[name][i:i + 2], 16) / 255 for i in (1, 3, 5))
            status.append(colorsys.rgb_to_hsv(*rgb)[0] * 360)
    gap = lambda a, b: min(abs(a - b) % 360, 360 - abs(a - b) % 360)
    assert {hue for hue, _ in _SLOTS} == {176, 212, 248, 284, 320}
    assert min(gap(hue, other) for hue, _ in _SLOTS for other in status) >= 10


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orb_wine_slot_reads_apart_from_the_red_ring():
    wine = _oklab(_srgb(*_tone(next(r for r in _tones() if r["slot"] == 5 and r["step"] == 0)["mid"])))
    for dark in (False, True):  # the failed ring is --red-fill in light, --red in dark
        assert math.dist(wine, _hex_oklab(_signal(dark)["red" if dark else "red-fill"])) >= 0.085


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orb_slots_never_twin_in_a_lane_runs(httpd):
    real = _real_graph(httpd)
    assert len(real["nodes"]) == 33
    out = _graph_node(r"""
        const real = __REAL__;
        const ids = (p, n) => Array.from({ length: n }, (_, i) => p + i);
        const synth = (lanes) => ({ lanes: lanes.map((nodes, i) => ({ id: 'l' + i, nodes })), nodes: Object.fromEntries([].concat(...lanes).map((id) => [id, {}])) });
        // ten ids that all hash to base slot 3: in one lane they must still land on ten different slots
        const same = __SAME__;
        const plans = {
          real: api.planOrbs(real), eight: api.planOrbs(synth([ids('b', 8)])), ten: api.planOrbs(synth([ids('t', 10)])),
          eleven: api.planOrbs(synth([ids('e', 11)])), thirteen: api.planOrbs(synth([ids('x', 13)])), twins: api.planOrbs(synth([same]))
        };
        const flip = JSON.parse(JSON.stringify(real)); flip.lanes.reverse(); flip.lanes.forEach((lane) => lane.nodes.reverse());
        const byId = (plan) => JSON.stringify(Object.entries(plan).sort());
        const drawn = (id) => api.drawOrb(mk('div'), 'g', id, 40).kids[0].kids[0].kids[1].attrs['stop-color'];
        const plan = api.planOrbs(synth([same])); api.setPlan(synth([same]));
        console.log(JSON.stringify({ plans: Object.fromEntries(Object.entries(plans).map(([k, v]) => [k, Object.fromEntries(Object.entries(v))])),
          lanes: real.lanes.map((lane) => lane.nodes), same, again: byId(api.planOrbs(real)) === byId(plans.real),
          shuffled: byId(api.planOrbs(flip)) === byId(plans.real),
          tones: same.map((id) => [plan[id].slot, drawn(id), api.orbDraw(id, false, plan[id]).mid]) }));
    """.replace("__REAL__", json.dumps(real)).replace("__SAME__", json.dumps(_on_slot(3, 10, "twin-"))))
    slots = lambda plan, nodes: [plan[n]["slot"] for n in nodes]
    for nodes in out["lanes"]:  # the real 33-node set: no two nodes in a lane share a slot (HQ has 11: one forced twin)
        got = slots(out["plans"]["real"], [n for n in nodes if n in out["plans"]["real"]])
        assert len(set(got)) == len(got) if len(got) <= 10 else len(set(got)) == 10
    hq = max(out["lanes"], key=len)
    assert len(hq) == 11 and out["plans"]["real"][sorted(hq)[-1]]["slot"] == 0  # past ten: the least used, lowest number
    assert len(set(slots(out["plans"]["eight"], [f"b{i}" for i in range(8)]))) == 8
    assert sorted(slots(out["plans"]["ten"], [f"t{i}" for i in range(10)])) == list(range(10))  # ten nodes take all ten slots
    eleven = slots(out["plans"]["eleven"], [f"e{i}" for i in range(11)])
    assert sorted(eleven) == [0] + list(range(10)) and out["plans"]["eleven"]["e9"]["slot"] == 0  # e9 is last in id order
    # ten ids that all hash to slot 3: the first in id order keeps it, the rest take the first free slot scanning 0 to 9
    assert slots(out["plans"]["twins"], sorted(out["same"])) == [3, 0, 1, 2, 4, 5, 6, 7, 8, 9]
    # past ten the least used slot, lowest number on a tie: the 11th, 12th and 13th in id order (x7, x8, x9) take 0, 1, 2
    assert slots(out["plans"]["thirteen"], ["x7", "x8", "x9"]) == [0, 1, 2]
    assert out["again"] and out["shuffled"]  # one plan per graph: it does not depend on lane or node order
    # a node the plan moved is drawn in its planned slot, not its seeded one
    assert all(drawn == planned for _slot, drawn, planned in out["tones"])
    assert len({tone for _slot, tone, _planned in out["tones"]}) == 10


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orbs_on_one_slot_differ_in_two_of_step_highlight_glow_runs():
    crowd = _on_slot(3, 26, "n")  # 26 lanes of one node each, all on slot 3: the lane rule cannot spread them, the roster rule must
    out = _graph_node(r"""
        const same = __SAME__;
        const graph = { lanes: same.map((id, i) => ({ id: 'l' + i, nodes: [id] })), nodes: Object.fromEntries(same.map((id) => [id, {}])) };
        const plan = api.planOrbs(graph);
        console.log(JSON.stringify(same.slice().sort().map((id) => { const o = api.orbDraw(id, false, plan[id]);
          return [o.slot, o.index, o.step, +o.glow.x.toFixed(3), +o.glow.y.toFixed(3)]; })));
    """.replace("__SAME__", json.dumps(crowd)))
    rows, extra = out[:24], out[24:]
    assert len(out) == 26 and {row[0] for row in out} == {3}
    assert [row[1] for row in out] == [_NG_SPREAD[k % 8] for k in range(26)]  # NG's highlight order, in id order
    assert len({(row[1], row[2]) for row in rows}) == 24  # a 9th or later also takes a step nobody else on that highlight has
    for a, b in itertools.combinations(rows, 2):  # the spec's rule: at least two of step, highlight position, glow
        assert (a[2] != b[2]) + (a[1] != b[1]) + (a[3:] != b[3:]) >= 2, (a, b)
    # 8 highlights x 3 steps is 24 looks: a 25th and 26th on one slot must repeat one, and still differ in their glow
    assert all((row[1], row[2]) in {(r[1], r[2]) for r in rows} and all(row[3:] != r[3:] for r in rows) for row in extra)


def test_the_ng_rule_retyped_here_matches_ngs_own_vectors():
    # constants from NG's backend/tests/test_employee_orb.py: the hash, the seeded draws, the scan and the spread
    assert [_ng_fnv1a(t) for t in ("harbour-desk", "keel-review", "x", "")] == [413554066, 44859051, 4245442695, 2166136261]
    assert [_ng_seeded(t) for t in ("harbour-desk", "keel-review", "x")] == [(0, 5), (0, 1), (4, 2)]
    assert _ng_orbs([["harbour-desk", "keel-review", "x"]]) == {"harbour-desk": [3, 0, 0], "keel-review": [2, 0, 0], "x": [0, 0, 4]}
    orbs = _ng_orbs([_NG_COLLISION])
    assert [orbs[i][0] for i in _NG_COLLISION] == list(range(10)) + [0, 1]  # the scan 0 to 9, then the least used
    assert [orbs[i][1] for i in _NG_COLLISION[:10]] == [0] * 10  # each of the first ten is the first on its slot


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orb_plan_matches_ngs_server_rule_runs(httpd):
    real = _real_graph(httpd)
    vp = next(lane["nodes"] for lane in real["lanes"] if lane["id"] == "vp")
    lanes = {  # the 33 fixture ids in their seven lanes, NG's twelve slot-0 ids in one lane, and two crowds of 26 lanes of one id on slot 3
        # the VP walks with the brains (design lead's twin fix), so its lane joins theirs
        "real": [[n for n in lane["nodes"] + (vp if lane["id"] == "products" else []) if n in real["nodes"]]
                 for lane in real["lanes"] if lane["id"] != "vp"],
        "collision": [_NG_COLLISION],
        "crowd": [[ident] for ident in _on_slot(3, 26, "n")],  # 26 on one slot reach NG's 9th-and-later step rule
        # all seeding +4: every branch of it, whatever the ids: kept, moved to 0, then -4, and repeated once all three are taken
        "fours": [[ident] for ident in _on_slot(3, 26, "f", step=4)],
    }
    out = _graph_node(r"""
        const lanes = __LANES__;
        const orbs = (names) => {
          const plan = api.planOrbs({ lanes: names.map((nodes, i) => ({ id: 'l' + i, nodes })), nodes: Object.fromEntries([].concat(...names).map((id) => [id, {}])) });
          return Object.fromEntries(Object.keys(plan).map((id) => { const o = api.orbDraw(id, false, plan[id]); return [id, [o.slot, o.index, o.step]]; }));
        };
        console.log(JSON.stringify(Object.fromEntries(Object.keys(lanes).map((name) => [name, orbs(lanes[name])]))));
    """.replace("__LANES__", json.dumps(lanes)))
    assert sum(len(lane) for lane in lanes["real"]) == 33
    for name, names in lanes.items():  # one id, one orb: the (slot, highlight, step) NG's server sends
        assert out[name] == _ng_orbs(names), name


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orb_assigned_values_leave_the_stream_alone_runs():
    out = _graph_node(r"""
        const draw = (given) => api.orbDraw('harbour-desk', false, given), plain = draw(), moved = draw({ slot: 0, step: 4, index: 0 });
        const rest = (o) => [o.glow.opacity, o.gradientRadius, +Math.hypot(o.glow.x - 50, o.glow.y - 50).toFixed(6)];
        console.log(JSON.stringify({ plain: rest(plain), moved: rest(moved), x: [plain.highlight.x, moved.highlight.x],
          tones: [moved.hue, moved.light, moved.mid] }));
    """)
    # NG: an assigned slot, step or highlight changes only its own part; the radius, glow radius and opacity stay on their draws
    assert out["plain"] == out["moved"] and out["x"][0] != out["x"][1]
    assert out["tones"] == [176, "hsl(176.0 60% 66.0%)", "hsl(176.0 60% 42.0%)"]  # slot 0 (L 38) plus step +4


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_edges_end_on_the_orb_circle_runs():
    out = _graph_node(r"""
        api.layout({ lanes: [{ id: 'owner', nodes: ['owner'] }, { id: 'hq', nodes: ['h1','h2','h3','h4','h5','h6','h7','h8','h9'] },
          { id: 'vp', nodes: ['vp'] }, { id: 'products', nodes: ['b1','b2','b3','b4','b5','b6','b7','b8'] }], nodes: {} });
        const pos = api.pos(), err = [], leaves = {};
        function check(a, b, d) {
          const n = d.match(/-?\d+(?:\.\d+)?/g).map(Number), A = pos[a], B = pos[b];
          err.push(Math.abs(Math.hypot(n[0] - A.cx, n[1] - A.cy) - (A.r + 2)), Math.abs(Math.hypot(n[6] - B.cx, n[7] - B.cy) - (B.r + 2)));
          return n;
        }
        [['owner', 'vp'], ['vp', 'owner'], ['h1', 'vp'], ['vp', 'h1'], ['owner', 'h9'], ['h9', 'owner']].forEach(([a, b]) => {
          for (let k = 0; k < 12; k++) check(a, b, api.wirePath(a, b, k, 12));
          const n = check(a, b, api.wirePath(a, b, 0, 1));
          leaves[a + '>' + b] = [n[0] > pos[a].cx, n[6] < pos[b].cx];
        });
        const arc = [0, 1, 2].map((k) => check('b1', 'b2', api.arcPath('b1', 'b2', k)));
        console.log(JSON.stringify({ err: Math.max(...err), leaves, radii: [pos.owner.r, pos.h1.r], compact: pos.h1.compact,
          arcClears: arc.every((n) => n[0] + 0.75 * (n[2] - n[0]) > pos.b1.x + api.c.BW) }));
    """)
    assert out["err"] < 0.01  # every strand lands on the circle one ring gap outside its orb, rounding aside
    assert out["radii"] == [20, 12] and out["compact"] is True
    # a strand leaves the side that faces its partner: right then left to the right-hand node, and the reverse
    assert out["leaves"]["owner>vp"] == [True, True] and out["leaves"]["vp>owner"] == [False, False]
    assert out["leaves"]["owner>h9"] == [True, True] and out["leaves"]["h9>owner"] == [False, False]
    assert out["leaves"]["h1>vp"] == [True, True] and out["leaves"]["vp>h1"] == [False, False]
    assert out["arcClears"]  # a same-lane arc's apex is past the slot edge, clear of the name under the orb


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_every_node_gets_its_slot_runs():
    out = _graph_node(r"""
        const ids = (p, n) => Array.from({ length: n }, (_, i) => p + i);
        const hq = ['hq-cos'].concat(ids('r', 9), ['hq']);
        const g = { lanes: [{ id: 'owner', nodes: ['owner'] }, { id: 'hq', nodes: hq }, { id: 'vp', nodes: ['vp'] },
          { id: 'departments', nodes: ids('d', 6) }, { id: 'products', nodes: ids('b', 8) }], nodes: {} };
        api.layout(g);
        const lanes = g.lanes.map((lane) => lane.nodes.filter((id) => api.pos()[id])
          .map((id) => ({ y: api.pos()[id].y, h: api.pos()[id].h, compact: api.pos()[id].compact, cy: api.pos()[id].cy, r: api.pos()[id].r })));
        console.log(JSON.stringify({ c: api.c, H: api.H(), lanes, sizes: g.lanes.map((lane) => lane.nodes.length) }));
    """)
    c = out["c"]
    assert out["H"] >= 560 and [len(lane) for lane in out["lanes"]] == out["sizes"]  # no lane hides a node
    for lane in out["lanes"]:
        for a, b in zip(lane, lane[1:]):
            assert a["y"] + a["h"] <= b["y"]  # slots never overlap
            assert b["y"] - a["y"] >= (c["PITCH_COMPACT"] if a["compact"] else c["PITCH"]) - 1
        for node in lane:
            assert node["h"] >= 44  # a hit area of at least 44px, as the boxes had
            assert node["cy"] - node["r"] >= c["TOP"] - 1
    assert out["lanes"][1][0]["compact"] is True and out["lanes"][1][0]["r"] == 12  # 11 in HQ: compact
    assert out["lanes"][4][0]["compact"] is False
    assert out["H"] == c["TOP"] + 12 + 8 * c["PITCH"]  # eight brains set the height, eleven compact roles take less


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_every_hq_role_is_an_orb_runs(httpd):
    real = _real_graph(httpd)
    hq = next(lane["nodes"] for lane in real["lanes"] if lane["id"] == "hq")
    assert len(hq) == 11  # the fixture's HQ: ten roles and the generic box
    assert len(real["lanes"]) <= len(re.search(r"const LX = \[([^\]]*)\]", JS).group(1).split(","))  # every lane has a column
    out = _graph_node(r"""
        const real = __REAL__;
        svg.parentNode = { clientWidth: 2550 };
        api.layout(real); api.drawNodes(real);
        const p = api.pos();
        console.log(JSON.stringify({ orbs: svg.kids.filter((n) => n.attrs['data-id']).map((n) => n.attrs['data-id']),
          hq: real.lanes.find((lane) => lane.id === 'hq').nodes.map((id) => [p[id].compact, p[id].r, p[id].h, p[id].y]),
          tags: Array.from(new Set(dom.map((n) => n.tag))), H: api.H(), c: api.c }));
    """.replace("__REAL__", json.dumps(real)))
    assert sorted(out["orbs"]) == sorted(real["nodes"])  # all 31 parts are orbs: no lane hides one
    assert [i for i in out["orbs"] if i in hq] == hq  # the HQ lane's own order
    assert all(compact and r == 12 and h == out["c"]["COMPACT_H"] for compact, r, h, _y in out["hq"])  # the signed compact mode
    ys = [y for *_rest, y in out["hq"]]
    assert all(b - a >= out["c"]["PITCH_COMPACT"] - 1 for a, b in zip(ys, ys[1:]))
    assert "a" not in out["tags"] and out["H"] == out["c"]["TOP"] + 12 + 8 * out["c"]["PITCH"]  # no quiet link; brains set H


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_compact_names_end_inside_the_lane_and_sit_on_a_plate_runs():
    out = _graph_node(r"""
        const labels = ['General Counsel', 'Chief of Staff', 'CEO', 'CPO', 'CTO', 'CFO', 'COO', 'CMO', 'CSO', 'CCO', 'HQ'], nodes = {};
        labels.forEach((label, i) => { nodes['h' + i] = { lane: 'hq', kind: 'HQ', label, state: i === 1 ? 'waits' : 'idle', word: 'Word' }; });
        nodes.vp = { lane: 'vp', kind: 'LAB', label: 'A rather long name for a full size node', state: 'idle', word: 'Idle' };
        const graph = { nodes, lanes: [{ id: 'hq', nodes: Object.keys(nodes).filter((id) => id !== 'vp') }, { id: 'vp', nodes: ['vp'] }] };
        svg.measure = (t) => t === 'General Counsel' ? 116.1 : 0;  // the measured idle name; everything else keeps the char fallback
        api.layout(graph); api.drawNodes(graph);
        const tag = (k) => k.tag + (k.attrs.class ? '.' + k.attrs.class : ''), pick = (g, sel) => g.kids.filter((k) => tag(k) === sel);
        const glyphLong = (n) => (n === 'General Counsel' ? 116.1 : n.length * 8) + 12;
        const glyphShown = api.clipName('General Counsel', glyphLong);
        console.log(JSON.stringify({ max: api.c.COMPACT_NAME_MAX, w: api.c.COMPACT_CHAR_W, hit: api.c.COMPACT_NAME_X, bw: api.c.BW,
          rows: svg.kids.map((g) => { const name = pick(g, 'text.name')[0], plate = pick(g, 'rect.plate')[0];
            return { id: g.attrs['data-id'], shown: name.textContent, glyph: name.kids.map((t) => t.textContent), aria: g.attrs['aria-label'],
              tip: pick(g, 'title')[0].textContent, plate: plate ? [+plate.attrs.x, +plate.attrs.width] : null, nameX: +name.attrs.x, order: g.kids.map(tag),
              orbEdge: api.pos()[g.attrs['data-id']].cx + api.pos()[g.attrs['data-id']].r + api.c.RING_GAP }; }),
          clip: [['General Counsel', 8], ['Chief of Staff', 7.5], ['CEO', 7.5], ['', 8], ['abcdefghijklmnop', 9], ['aaaaaaaaaaa bbbbbbbb', 9], ['abcdefgh', 30]]
            .map(([s, w]) => api.clipName(s, (n) => n.length * w))
            .concat(api.clipName('abcdefghijklWW', (n) => Array.from(n).reduce((sum, c) => sum + (c === 'W' ? 20 : 8), 0))),
          idle: api.clipName('General Counsel', (n) => n === 'General Counsel' ? 116.1 : n.length * 8),
          glyph: [glyphShown, glyphLong(glyphShown)] }));
    """)
    limit, per = out["max"], out["w"]
    assert limit == 117  # BW + 8 - COMPACT_NAME_X - 4: the room from the name to the lane edge, less the plate's 4
    # at 8 a char 'General Counsel' is 120, past the 117 cap, so 13 and the ellipsis (112) fit; the next three fit; at 9 a char
    # 12 and the ellipsis (117) fit, and a cut that lands after a space drops the space; at 30 a char 2 fit (3 and the ellipsis
    # is 120); a wide last letter ('W' is 20) does not stop the 12 before it fitting with the ellipsis. Measured 116.1 with no
    # tail is kept; the same name plus a glyph clips to 116.
    assert out["clip"] == ["General Couns…", "Chief of Staff", "CEO", "", "abcdefghijkl…", "aaaaaaaaaaa…", "ab…", "abcdefghijkl…"]
    assert out["idle"] == "General Counsel" and out["glyph"] == ["General Coun…", 116]
    full = {f"h{i}": label for i, label in enumerate(["General Counsel", "Chief of Staff", "CEO", "CPO", "CTO", "CFO", "COO", "CMO", "CSO", "CCO", "HQ"])}
    clipped = 0
    for row in out["rows"]:
        if row["id"] == "vp":  # a full size node keeps its whole name, and has no plate
            assert row["shown"] == "A rather long name for a full size node" and row["plate"] is None
            continue
        label, tail = full[row["id"]], row["glyph"][0] if row["glyph"] else ""
        assert label in row["tip"] and row["aria"].startswith(label + ",")  # the full name stays in the title and the aria-label
        assert row["order"][:2] == ["rect.hit", "rect.plate"] and row["order"].index("rect.plate") < row["order"].index("svg.orb")  # below the rings
        assert row["order"].index("svg.orb") < row["order"].index("text.name")
        x, width = row["plate"]
        plate_end = x + width
        assert x == row["orbEdge"]  # it starts at the orb's ring gap, so no strand stub shows between orb and name
        assert plate_end <= row["nameX"] - out["hit"] + out["bw"] + 8  # lane rect ends 8 past the slot
        if row["id"] == "h0":  # measured 116.1, no tail: not clipped; plate is ceil(116.1) + 4 past the name
            assert row["shown"] == label and tail == "" and plate_end == row["nameX"] + 121
            continue
        size = (len(row["shown"]) + len(tail)) * per  # no text metrics here, so each character counts COMPACT_CHAR_W
        assert size <= limit  # name and glyph end inside the lane
        if row["shown"] != label:
            clipped += 1
            assert row["shown"].endswith("…") and label.startswith(row["shown"][:-1])
            assert size + per > limit  # as much of it as fits
        assert size + 4 <= plate_end - row["nameX"] <= size + 5  # and ends just past the name
    assert clipped == 1  # the lane's 117 clips "Chief of Staff" plus its dot (16 x 7.5 = 120); every other real role fits


def test_compact_names_sit_on_a_lane_tone_plate():
    plate = re.search(r"^svg \.node rect\.plate \{([^}]*)\}", CSS, re.M).group(1)
    lane = re.search(r"^\.lane rect \{([^}]*)\}", CSS, re.M).group(1)
    assert "color-mix(in srgb, var(--ink) 4.5%" in lane and "color-mix(in srgb, var(--ink) 4.5%, var(--paper))" in plate  # the lane's fill on paper
    assert "pointer-events: none" in plate  # a click on the name still reaches the hit area
    assert re.search(r"^svg \.node \.plate ~ \.name \{ stroke: none; \}", CSS, re.M)  # the plate does the text halo's job


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orbs_show_honest_states_runs():
    out = _graph_node(r"""
        const nodes = {
          owner: { lane: 'owner', kind: 'OWNER', label: 'Owner', state: 'waits', word: 'Waits for you' },
          vp: { lane: 'vp', kind: 'LAB', label: 'VP', state: 'idle', word: 'Idle' },
          b1: { lane: 'products', kind: 'PRODUCT', label: 'One', state: 'failed', word: 'Failed', active: true, badge: { text: '1 failed', tone: 'red' } },
          b2: { lane: 'products', kind: 'PRODUCT', label: 'Two', state: 'blocked', word: 'Blocked' },
          b3: { lane: 'products', kind: 'PRODUCT', label: 'Three', state: 'quiet', word: 'Quiet 3d', badge: { text: 'quiet 3d', tone: 'ink' } },
          b4: { lane: 'products', kind: 'PRODUCT', label: 'Four', state: 'working', word: 'Working', active: true },
          x1: { lane: 'outside', kind: 'OUTSIDE', label: 'Elsewhere', state: 'idle', word: 'Idle' }
        };
        const hqIds = Array.from({ length: 9 }, (_, i) => 'h' + i);
        hqIds.forEach((id, i) => { nodes[id] = { lane: 'hq', kind: 'HQ', label: 'Role ' + i,
          state: !i || i === 8 ? 'waits' : 'idle', word: 'w', active: i === 8,
          badge: i ? null : { text: '1 ask', tone: 'ink' } }; });
        const graph = { nodes, lanes: [{ id: 'owner', nodes: ['owner'] }, { id: 'hq', nodes: hqIds }, { id: 'vp', nodes: ['vp'] },
          { id: 'products', nodes: ['b1', 'b2', 'b3', 'b4'] }, { id: 'outside', nodes: ['x1'] }] };
        api.layout(graph);
        api.drawNodes(graph);
        const pick = (g, sel) => g.kids.filter((k) => (k.tag + (k.attrs.class ? '.' + k.attrs.class : '')) === sel);
        const sum = {};
        svg.kids.forEach((g) => {
          const orb = pick(g, 'svg.orb')[0], kind = pick(g, 'text.kind')[0], name = pick(g, 'text.name')[0];
          sum[g.attrs['data-id']] = {
            cls: g.attrs.class, role: g.attrs.role, hit: +pick(g, 'rect.hit')[0].attrs.height,
            orb: [orb.attrs.width, orb.attrs['aria-hidden']], orbR: +orb.attrs.width / 2,
            gap: pick(g, 'circle.gap').map((c) => +c.attrs.r), ring: pick(g, 'circle.ring').map((c) => [+c.attrs.r, +c.attrs['stroke-width']]),
            pick: pick(g, 'circle.pick').map((c) => +c.attrs.r),
            ping: pick(g, 'circle.ping').map((c) => +c.attrs.r),
            glow: pick(g, 'circle.glow').map((c) => [+c.attrs.r, c.style.animationDelay]),
            kind: kind ? kind.kids.map((t) => [t.attrs.class || '', t.textContent]) : null,
            name: name.textContent, nameX: +name.attrs.x, glyph: name.kids.map((t) => t.textContent), tip: pick(g, 'title')[0].textContent
          };
        });
        console.log(JSON.stringify({ sum, c: api.c, hqX: api.pos().h0.x, hqCx: api.pos().h0.cx }));
    """)
    s, c = out["sum"], out["c"]
    assert [s[i]["cls"] for i in ("owner", "vp", "b1", "b2", "b3", "b4")] == [
        "node waits", "node idle", "node failed chat-on", "node blocked", "node quiet", "node working chat-on"]
    assert all(v["role"] == "button" and v["hit"] >= 44 for v in s.values())
    assert all(v["orb"][1] == "true" for v in s.values())  # decorative
    assert {v["orb"][0] for v in s.values()} == {"24", "40"}  # 24px for compact HQ, 40px everywhere else
    # waits: blue 3px ring plus the dot beside the kind line, so the state is never colour only
    assert s["owner"]["ring"] == [[23.5, 3]] and s["owner"]["gap"] == [21] and s["owner"]["glow"] == []
    assert s["owner"]["kind"] == [["", "OWNER"], ["glyph", " ●"]]
    assert s["b1"]["ring"] == [[23, 2]] and s["b2"]["ring"] == [[23, 2]] and s["b3"]["ring"] == [[22.75, 1.5]]
    assert s["b2"]["kind"][-1] == ["glyph", " ◇"] and s["b3"]["kind"][-1] == ["glyph", " ○"]
    assert s["b1"]["kind"][:2] == [["", "PRODUCT"], ["badge red", " · 1 failed"]] and s["b1"]["kind"][2][0] == "glyph"
    # idle has no ring at all; only a working node gets the pulsing ring, outside the state ring, on a seeded negative delay
    assert s["vp"]["ring"] == [] and s["vp"]["gap"] == [] and s["vp"]["glow"] == [] and s["b3"]["glow"] == []
    assert s["b1"]["glow"][0][0] == 27 > s["b1"]["ring"][0][0]
    assert s["b4"]["glow"][0][0] == 23 and s["b4"]["ring"] == []
    assert all(re.fullmatch(r"-\d\.\d\ds", v["glow"][0][1]) for v in (s["b1"], s["b4"]))
    assert s["b1"]["glow"][0][1] != s["b4"]["glow"][0][1]
    # the pick ring never touches text: none on a working node (its working ring takes the look), and never past 27 from the centre
    assert s["b1"]["pick"] == [] and s["b4"]["pick"] == []
    assert s["owner"]["pick"] == [27] and s["b2"]["pick"] == [27]  # waits would reach 28 uncapped
    assert s["vp"]["pick"] == [23] and s["b3"]["pick"] == [26.5] and s["h0"]["pick"] == [20] and s["h1"]["pick"] == [15]
    assert all(r <= 27 for v in s.values() for r in v["pick"])
    # the spoke ping never crosses the text: 29 on a large orb, 22 on a compact one
    assert all(v["ping"][0] <= 29 for v in s.values() if v["orbR"] == 20)
    assert all(v["ping"][0] <= 22 for v in s.values() if v["orbR"] == 12)
    assert s["h8"]["cls"] == "node waits chat-on" and s["h8"]["ping"] == [22]
    # compact HQ: a 24px orb with the name beside it, no kind line, and the tooltip carries the kind and the badge text
    h0 = s["h0"]
    assert h0["orbR"] == 12 and h0["kind"] is None and h0["nameX"] == out["hqX"] + c["COMPACT_NAME_X"] and h0["glyph"] == [" ●"]
    assert h0["nameX"] - out["hqCx"] - 22.5 >= 1.5  # compact name clears the ping edge; the pick edge is 21
    assert h0["tip"].startswith("HQ · Role 0") and h0["tip"].endswith("1 ask")
    assert s["x1"]["orbR"] == 20  # outside parts get the same 40px orb


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_orb_gradient_ids_are_unique_and_resolve_runs():
    out = _graph_node(r"""
        const ids = ['owner', 'vp', 'brain-connect-m0', 'hq-cos', 'weird id (1)', 'a.b', 'a/b'];
        ids.forEach((id) => ['g', 't', 'f', 'p'].forEach((ctx) => [24, 40].forEach((size) => api.drawOrb(mk('div'), ctx, id, size))));
        const withId = dom.filter((n) => n.attrs.id), fills = dom.filter((n) => /^url\(#/.test(n.attrs.fill || ''));
        const have = new Set(withId.map((n) => n.attrs.id));
        const twins = ['a.b', 'a/b'].map((id) => api.drawOrb(mk('div'), 'g', id, 40).kids[0].kids[0].attrs.id);
        console.log(JSON.stringify({ twins, n: withId.length, unique: have.size, fills: fills.length,
          dangling: fills.filter((n) => !have.has(n.attrs.fill.slice(5, -1))).length, bad: withId.filter((n) => /[^A-Za-z0-9_-]/.test(n.attrs.id)).length,
          hidden: dom.filter((n) => n.tag === 'svg' && n.attrs.class === 'orb').every((n) => n.attrs['aria-hidden'] === 'true') }));
    """)
    assert out["n"] == out["unique"] == 7 * 4 * 2 * 2  # two gradients per orb, every one with its own id
    # "a.b" and "a/b" clean to the same string; the counter keeps their gradients apart
    assert out["twins"][0] != out["twins"][1] and all(re.fullmatch(r"o-g-a_b-40-\d+b", t) for t in out["twins"])
    assert out["fills"] == out["n"] and out["dangling"] == 0 and out["bad"] == 0 and out["hidden"]


def test_orbs_replace_the_boxes():
    assert '"box"' not in JS and "rect.box" not in CSS and "BH" not in JS and "chatPhase" not in JS
    # the table keeps a plain 24px orb (its State column says the state); the focus box and the partner rows draw the rings too
    assert re.findall(r'drawOrb\(\w+, "(\w)"', JS) == ["g", "d", "t"]
    assert re.findall(r'drawOrb\(\w+, "t", [^,]+, (\d+)\)', JS) == ["24"]
    assert re.findall(r'drawOrbCell\(\w+, "(\w)"', JS) == ["f", "p"]
    assert "redrawOrbs();" in JS[JS.index("function toggleTheme"):JS.index("function ageOf")]  # the tones follow the theme
    reduced = CSS[CSS.index("@media (prefers-reduced-motion"):]
    assert re.search(r"svg \.node\.chat-on circle\.glow \{[^}]*animation: none", reduced)  # the working ring stands still
    assert re.search(r"^:is\(svg \.node, \.orb-cell\)\.waits circle\.ring \{ stroke: var\(--blue\); \}", CSS, re.M)
    assert "innerHTML" not in JS


def test_working_ring_takes_the_pick_look_and_blocked_is_amber():
    # A working node has no pick ring, so selection, keyboard focus and hover restyle its working ring instead.
    sel = re.search(r"^svg \.node\.chat-on:is\(\.sel, :focus-visible\) circle\.glow \{([^}]*)\}", CSS, re.M).group(1)
    hover = re.search(r"^svg \.node\.chat-on:hover circle\.glow \{([^}]*)\}", CSS, re.M).group(1)
    for rule in (sel, hover):
        assert "stroke: var(--ink)" in rule and "animation: none" in rule and "filter: none" in rule
    assert "opacity: 1;" in sel and "opacity: 0.6;" in hover
    assert CSS.index("chat-on:hover circle.glow") < CSS.index("chat-on:is(.sel")  # same specificity: selection wins over hover
    # --amber-fill is under 3:1 on light paper: the blocked ring uses --amber
    assert re.search(r"^:is\(svg \.node, \.orb-cell\)\.blocked circle\.ring \{ stroke: var\(--amber\); \}", CSS, re.M)
    assert not re.search(r"^\.focus-box\.(waits|failed|blocked|quiet) \{ border", CSS, re.M)  # S6: the ring carries state
    assert re.search(r"^\.fv-main \{[^}]*white-space: normal; text-overflow: clip;", CSS, re.M)  # M4
    assert "var(--amber-fill)" not in CSS


def test_dark_failed_ring_strokes_red():
    assert re.search(r"^:is\(svg \.node, \.orb-cell\)\.failed circle\.ring \{ stroke: var\(--red-fill\); \}", CSS, re.M)
    assert re.search(
        r'^\[data-theme="dark"\] :is\(svg \.node, \.orb-cell\)\.failed circle\.ring \{ stroke: var\(--red\); \}',
        CSS, re.M)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_phone_orbs_carry_the_state_rings_runs():
    out = _graph_node(r"""
        const mkNode = (state, extra) => Object.assign({ lane: 'products', kind: 'PRODUCT', label: state, state, word: state }, extra || {});
        const nodes = { vp: mkNode('idle'), w: mkNode('waits'), d: mkNode('done'), wa: mkNode('waits', { active: true }), q: mkNode('quiet'),
          f: mkNode('failed'), b: mkNode('blocked'), a: mkNode('working', { active: true }) };
        const ids = ['w', 'd', 'wa', 'q', 'f', 'b', 'a'];
        const graph = { nodes, lanes: [{ id: 'products', nodes: ['vp'].concat(ids) }],
          fibres: ids.map((id) => ({ from: 'vp', to: id, count: 1, total_messages: 1, total_mentions: 0 })) };
        api.drawPartners(graph, 'vp', false);
        api.drawBox(graph, 'w');
        const shape = (cell) => ({ size: +cell.attrs.width, cls: cell.attrs.class, hidden: cell.attrs['aria-hidden'],
          orb: cell.kids.filter((k) => k.tag === 'svg').map((k) => [+k.attrs.width, +k.attrs.x, +k.attrs.y]),
          rings: cell.kids.filter((k) => k.tag === 'circle').map((k) => [k.attrs.class, +k.attrs.r, +k.attrs['stroke-width']]) });
        const rows = {};
        hosts['focus-partners'].kids.forEach((btn) => { rows[btn.id.replace('partner-', '')] = shape(btn.kids[0]); });
        console.log(JSON.stringify({ rows, box: shape(hosts['focus-box'].kids[0]) }));
    """)
    rows, box = out["rows"], out["box"]
    # waits and done must differ in shape, not only in colour: a 3px ring in a 36px cell against no ring in a 26px one
    assert rows["w"]["rings"] == [["gap", 13, 2], ["ring", 15.5, 3]] and rows["w"]["size"] == 36
    assert rows["d"]["rings"] == [] and rows["d"]["size"] == 26
    assert rows["w"]["cls"] == "orb-cell waits" and rows["d"]["cls"] == "orb-cell done"
    # the same rings a compact graph node draws: 2px failed and blocked, 1.5px quiet, the working ring outside them, still
    assert rows["f"]["rings"] == [["gap", 13, 2], ["ring", 15, 2]] == rows["b"]["rings"]
    assert rows["q"]["rings"] == [["gap", 13, 2], ["ring", 14.75, 1.5]]
    assert rows["wa"]["rings"] == [["glow", 20, 2], ["gap", 13, 2], ["ring", 15.5, 3]] and rows["wa"]["size"] == 44
    assert rows["a"]["rings"] == [["glow", 15, 2]] and rows["a"]["size"] == 34
    assert len({json.dumps(rows[k]["rings"]) for k in ("w", "d", "wa", "q", "a")}) == 5  # waits, done, live, quiet, working all read apart
    # decorative, and the 24px orb stays 24px at the cell's centre; the focus box draws the same cell as a partner row
    assert all(r["hidden"] == "true" and r["orb"] == [[24, r["size"] / 2 - 12, r["size"] / 2 - 12]] for r in rows.values())
    assert box["rings"] == rows["w"]["rings"] and box["size"] == 36 and box["cls"] == "orb-cell waits"


# ---- true size, fit and resize ----

_SEVEN = r"""
    const names = ['owner', 'hq', 'vp', 'departments', 'products', 'factories', 'outside'], ids = ['owner', 'h1', 'vp', 'd1', 'b0', 'f1', 'o1'];
    const graph = { nodes: {}, lanes: names.map((id, i) => ({ id, label: id, nodes: id === 'products' ? Array.from({ length: 8 }, (_, k) => 'b' + k) : [ids[i]] })) };
    const at = (w, h) => { svg.parentNode = { clientWidth: w }; win.innerHeight = h; svg.kids.length = 0; api.layout(graph); api.drawLanes(graph);
      return { box: svg.attrs.viewBox.split(' ').map(Number), x: ids.map((id) => api.pos()[id].x), r: api.pos().b0.r, lanes: api.laneX(), H: api.H(),
        rects: svg.kids.map((lane) => [+lane.kids[0].attrs.x, +lane.kids[0].attrs.width, +lane.kids[1].attrs.x]) }; };
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_wide_screens_draw_at_true_size_runs():
    out = _graph_node(_SEVEN + r"""
        console.log(JSON.stringify({ c: api.c, design: at(1322, 5000), wide: at(2550, 5000), mid: at(1200, 5000), unknown: at(0, 5000), narrow: at(1100, 5000) }));
    """)
    c, lx = out["c"], out["c"]["LX"]
    assert c["W_MIN"] == 1322 and c["ORB_R"] == 20 and lx[0] == 16
    assert out["design"]["box"] == [0, 0, 1322, 728]
    assert out["design"]["x"] == lx and out["design"]["lanes"] == lx  # at the design width these are exactly today's positions
    for name in ("mid", "unknown", "narrow"):  # narrower than the design, or not laid out yet: today's drawing, scaled down by the page
        assert out[name]["box"] == [0, 0, 1322, 728] and out[name]["x"] == lx
    wide = out["wide"]
    assert wide["box"] == [0, 0, 2550, 728]  # 1 unit is 1px, and the height still follows the content
    assert wide["r"] == c["ORB_R"]  # an orb keeps its size
    want = [round(16 + (x - 16) * (2550 - 182) / 1140) for x in lx]
    assert wide["x"] == want and wide["lanes"] == want  # every lane x grows in proportion to its distance from the first
    assert wide["x"][0] == 16 and wide["x"][-1] + c["BW"] + 16 == 2550  # the margins stay: 16 left, BW + 16 right
    assert all(abs((b - a) / (e - d) - 2368 / 1140) < 0.01 for a, b, d, e in zip(wide["x"], wide["x"][1:], lx, lx[1:]))  # gaps scale evenly
    assert wide["rects"] == [[x - 8, c["BW"] + 16, x] for x in want]  # lane backgrounds and headers read the per-render positions


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_fit_scales_down_never_up_runs():
    out = _graph_node(_SEVEN + r"""
        console.log(JSON.stringify({ unit: [[728, 1330], [728, 728], [728, 576], [728, 100], [100, 1330], [728, 0], [728, NaN]].map(([h, room]) => api.fitScale(h, room)),
          fits: at(2550, 1354), tall: at(2550, 600), tiny: at(2550, 200), huge: at(2550, 20000), small: at(1200, 800), smallTall: at(1200, 500) }));
    """)
    unit = out["unit"]
    assert unit[:2] == [1, 1] and unit[4] == 1 and unit[5:] == [1, 1]  # fits, fits exactly, a short graph, no window known: never enlarged
    assert unit[2] == 0.83 and unit[3] == 0.83  # too tall: the scale that fits, never below 0.83 (Design M5: legible text)
    assert out["fits"]["box"] == [0, 0, 2550, 728] and out["huge"]["box"] == [0, 0, 2550, 728]  # a tall window changes nothing
    for name, floor in (("tall", 0.83), ("tiny", 0.83)):
        run = out[name]
        s = 2550 / run["box"][2]  # the rendered scale: the container's width over the viewBox's
        assert abs(s - floor) < 0.001 and 0.83 - 1e-6 <= s <= 1 and run["r"] == 20  # the orb is 20 units: 20 * s px on screen
        assert abs(run["lanes"][-1] + 150 + 16 - run["box"][2]) <= 1  # the lanes still span the whole width
    assert out["small"]["box"] == [0, 0, 1322, 728]  # below the design width it already fits: untouched
    small = out["smallTall"]  # 1200 wide is already scaled by 1200 / 1322; the floor holds on what reaches the screen
    assert abs(728 * 1200 / small["box"][2] - 728 * 0.83) < 0.5  # 476 would fit, but text would drop below 0.83: it scrolls


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_resize_redraws_only_a_wide_graph_whose_width_changed_runs():
    out = _graph_node(_SEVEN + r"""
        const log = [], size = (w, h) => { svg.parentNode = { clientWidth: w }; win.innerHeight = h; };
        const step = (w, h, narrow) => { size(w, h); api.narrow(!!narrow); api.relayout(); log.push([api.draws(), api.W()]); };
        size(2550, 1354); api.layout(graph);
        step(2000, 1354);       // no graph on the page yet
        api.show(graph);
        step(2550, 1354);       // the width it was drawn at
        step(2400, 1354);       // wider or narrower: a new layout
        step(2400, 1354);       // nothing moved
        step(2000, 1354, true); // narrow draws the focused view, never this graph
        step(1322, 1354);       // back to the design width
        step(1250, 1354);       // every container below the design is the same drawing
        step(1150, 1354);
        step(2400, 600);        // a shorter window changes the fit
        step(2400, 600);
        console.log(JSON.stringify(log));
    """)
    assert [n for n, _w in out] == [0, 0, 1, 1, 1, 2, 2, 2, 3, 3]
    assert [w for _n, w in out][:5] == [2550, 2550, 2400, 2400, 2400] and out[5][1] == out[6][1] == out[7][1] == 1322 and out[8][1] > 2400


def test_resize_waits_one_frame_and_a_poll_redraw_never_scrolls():
    tail = JS[JS.index("let wideFrame = 0;"):JS.index("let railFrame = 0;")]
    assert "if (wideFrame) return;" in tail and "requestAnimationFrame" in tail and "relayout();" in tail
    # the focus put back on the orb after a redraw must not scroll the page to it
    assert "nodeEls[activeId].focus({ preventScroll: true })" in JS and "nodeEls[activeId].focus()" not in JS
    assert len(re.findall(r"\.focus\(\)", JS)) == 2  # the two left run inside a click handler (the problems list), never on a poll
    rest = JS.replace("let laneX = LX;", "")
    rest = rest[:rest.index("function layout(")] + rest[rest.index("function wirePath"):]  # layout is the one place that spreads LX
    assert not re.search(r"\bLX\b", rest[rest.index("const TOP"):])  # everything else reads this render's laneX


def test_poll_redraw_keeps_focus_without_scrolling():
    # A focused node was re-focused on every 5 s redraw, which scrolled the page back to the graph.
    assert "nodeEls[activeId].focus({ preventScroll: true })" in JS and "nodeEls[activeId].focus()" not in JS


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_table_rebuild_keeps_the_focused_row():
    # id "a]b" is not a CSS identifier: the restore compares data-id and never builds a selector from it.
    script = (
        "const src = process.argv[1]; const mk = (tag) => { const n = { tagName: tag.toUpperCase(), attrs: {}, kids: [], parentNode: null, firstChild: null,"
        " setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },"
        " appendChild(c) { c.parentNode = this; this.kids.push(c); this.firstChild = this.kids[0]; return c; }, removeChild(c) { this.kids = this.kids.filter((k) => k !== c); c.parentNode = null; this.firstChild = this.kids[0] || null; },"
        " addEventListener() {}, contains(node) { for (let p = node; p; p = p.parentNode) if (p === this) return true; return false; },"
        " querySelectorAll(q) { if (q !== 'button[data-id]') throw new Error(q); const out = []; (function walk(node) { node.kids.forEach((k) => { if (k.tagName === 'BUTTON' && k.getAttribute('data-id') !== null) out.push(k); walk(k); }); })(this); return out; },"
        " focus(opt) { document.activeElement = this; this.focusOpt = opt; } }; return n; };"
        "const body = mk('tbody'); const document = { createElement: mk, activeElement: null, querySelector: () => body }; let sel = null, nowMs = 0;"
        "function drawOrb() {} function orbFamily() { return ''; } function glyph() { return ''; } function tone() { return ''; } function ageOf() { return ''; }"
        "eval(src.slice(src.indexOf('function nodeById'), src.indexOf('function svgEl'))); eval(src.slice(src.indexOf('function table('), src.indexOf('function select(')));"
        "const graph = { nodes: { 'a]b': { label: 'A' }, b: { label: 'B' } }, lanes: [{ nodes: ['a]b', 'b'] }] }; table(graph);"
        "const first = body.querySelectorAll('button[data-id]').filter((n) => n.getAttribute('data-id') === 'a]b')[0]; first.focus({ preventScroll: true }); table(graph);"
        "const again = document.activeElement; console.log(JSON.stringify({ id: again.getAttribute('data-id'), fresh: again !== first, opt: again.focusOpt }));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out == {"id": "a]b", "fresh": True, "opt": {"preventScroll": True}}


def _phone_css():
    at = CSS.index("@media (width < 800px)")
    return CSS[at:_brace_end(CSS, CSS.index("{", at)) + 1]


def test_phone_tab_bar():
    start = INDEX.index('<nav class="tabbar"')
    bar = INDEX[start:INDEX.index("</nav>", start)]
    assert 'aria-label="Heliopause"' in bar and 'id="tab-more"' in bar
    assert 'aria-controls="rail"' in bar and 'aria-expanded="false"' in bar
    assert 'href="#now"' in bar and 'aria-current="page"' in bar
    assert 'href="#agents"' in bar and 'href="#work"' in bar
    for word in ("Now", "Agents", "Work", "More"):
        assert word in bar
    assert ".tabbar { display: none; }" in CSS
    phone = _phone_css()
    assert "position: sticky" in phone and "bottom: 0" in phone
    assert "env(safe-area-inset-bottom)" in phone
    assert "var(--blue-text)" in phone and "var(--ink-3)" in phone
    assert ".tabbar a:focus-visible" in phone and "var(--focus)" in phone
    assert 'a[href="#now"]' in phone and 'a[href="#spend"]' not in phone
    assert "min-width" not in phone
    show = JS[JS.index("function showView"):JS.index("function setRail")]
    assert ".tabbar a, .tabbar button" in show
    assert show.count('setAttribute("aria-current", "page")') == 2
    assert 'getElementById("tab-more")' in JS
    wire = JS[JS.index("function wireShell"):]
    assert wire.count("setRail(true)") == 2


def test_phone_now_order():
    phone = _phone_css()
    assert "display: contents" in phone
    assert phone.index("#needs-list") < phone.index("#broken { order: 2")
    assert phone.index("#broken { order: 2") < phone.index("#focus-view")
    assert phone.index("#focus-view") < phone.index("#fyi { order: 4")
    assert "order: 1" in phone and "order: 3" in phone
    assert ".needs-card { width: 100%; }" in phone
    assert ".needs-actions :is(button, a) { min-height: 44px; }" in phone
    assert "#needs-list { display: flex; flex-direction: column; }" in phone
    assert "min-width" not in phone


def test_phone_agent_rows_are_cards():
    phone = _phone_css()
    assert "#tbl thead { display: none; }" in phone
    assert 'grid-template-areas: "name state" "hl hl"' in phone
    assert "grid-area: name" in phone and "grid-area: state" in phone and "grid-area: hl" in phone
    assert "#tbl td.age { display: none; }" in phone
    assert "white-space: normal" in phone
    assert "min-width" not in phone


def test_first_run_markup():
    assert 'id="first-run"' in INDEX and 'id="now-stage"' in INDEX
    assert 'id="first-eye"' in INDEX and ">Let Heliopause see your agents.<" in INDEX
    assert "Heliopause runs on this machine only, reads what your agents report, and never wakes or drives them." in INDEX
    for title in ("Create your lab", "Connect Claude Code", "Add a repo", "Send a test alert"):
        assert ">%s<" % title in INDEX
    assert 'class="first-file">atlas.json<' in INDEX and "atlas/repos.json" not in INDEX
    assert "Nothing needs you yet. That is the goal: when this page is quiet, your agents are fine." in INDEX
    assert "Your network so far" in INDEX
    assert INDEX.count('class="first-copy"') == 2 and 'type="button" class="first-copy"' in INDEX
    assert "atlas hooks install" not in INDEX and "X-Atlas-Token" not in INDEX
    assert "navigator.clipboard" in JS and "selectNodeContents" in JS
    paint = JS[JS.index("function paintFirst"):JS.index("function copyCommand")]
    assert "first.hidden = !empty" in paint and "stage.hidden = empty" in paint
    assert "paintFirst(data);" in JS[JS.index("function render("):JS.index("function tick(")]
    assert "paintFirst(current);" in JS[JS.index("function loadAlerts"):JS.index("function currentView")]
    assert "wireCopy();" in JS


def test_first_run_css():
    block = CSS[CSS.index("/* First run"):]
    assert "min-width" not in block
    assert _COLOR.search(block) is None
    copy = re.search(r"\.first-copy \{([^}]*)\}", block).group(1)
    assert "min-height: 40px" in copy and "var(--blue)" in copy
    assert ".first-copy:focus-visible" in block and "var(--focus)" in block
    phone = block[block.index("@media (width < 800px)"):]
    assert "flex-direction: column" in phone
    assert ".first-preview" in phone and "width: 100%" in phone and "flex-basis: auto" in phone


def _cli_options(path):
    """add_argument flags, and whether each one is required, from one CLI module."""
    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument":
            continue
        flags = tuple(arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str))
        required = False
        for key in node.keywords:
            if key.arg == "required" and isinstance(key.value, ast.Constant) and key.value.value is True:
                required = True
        found.append((flags, required))
    return found


def _compared_strings(path):
    """String literals compared with == or `in`, so a dispatched subcommand counts."""
    found = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for op, comp in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                found.add(comp.value)
            elif isinstance(op, ast.In) and isinstance(comp, ast.Tuple):
                for elt in comp.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        found.add(elt.value)
    return found


def _alert_severities(path):
    found = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for comp in node.comparators:
            if not isinstance(comp, ast.Tuple):
                continue
            vals = tuple(elt.value for elt in comp.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str))
            if "needs-owner" in vals:
                found.update(vals)
    return found


def test_first_run_commands_are_the_cli():
    repo = Path(__file__).resolve().parents[1]
    cmds = re.findall(r'<code class="first-cmd">(.*?)</code>', INDEX)
    assert cmds == [
        "python3 -m atlas hooks",
        'scripts/atlas-alert needs-owner test "Test alert"',
        'python3 -m atlas --lab "$(python3 -m atlas demo-path)"',
    ]
    main = repo / "atlas" / "__main__.py"
    hooks = repo / "atlas" / "hooks.py"
    assert "hooks" in _compared_strings(main) and "demo-path" in _compared_strings(main)
    ran = subprocess.run(
        [sys.executable, "-m", "atlas", "demo-path"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(ran.stdout.strip()) == repo / "demo" / "generic"
    hook_opts = _cli_options(hooks)
    assert all(not required for _flags, required in hook_opts)
    assert any("--lab" in flags and not required for flags, required in hook_opts)
    assert any("--yes" in flags for flags, _required in hook_opts)
    main_opts = _cli_options(main)
    assert any("--lab" in flags and not required for flags, required in main_opts)
    assert (repo / "demo" / "generic" / "atlas.json").is_file()
    alert = repo / "scripts" / "atlas-alert"
    text = alert.read_text(encoding="utf-8")
    assert "needs-owner" in _alert_severities(alert)
    assert "ATLAS_TOKEN_FILE" in text and "~/.atlas/token" in text
    assert "print(token" not in text and "X-Atlas-Token" not in INDEX
    # The sender reads the file itself. The command must not carry the token.
    assert "token" not in cmds[1]


_FIRST = (
    "const src = process.argv[1];"
    "function currentView() { return 'now'; }"
    "var alertPayload = null;"
    "eval(src.slice(src.indexOf('function emptyLab'), src.indexOf('function wireCopy')));"
)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_empty_lab_and_the_four_steps():
    script = (
        _FIRST
        + "function base() {"
        + "  return { nodes: { owner: {}, lead: { headline: 'Lab created', ts: 't' }, worker: {} } };"
        + "}"
        + "const quiet = { lead: 'lead', board_lines: 0, graph: base() };"
        + "const spoke = base(); spoke.nodes.worker = { headline: 'Echo job started' };"
        + "const other = base(); other.nodes.scout = {};"
        + "console.log(JSON.stringify({"
        + "  empty: emptyLab(quiet),"
        + "  noData: emptyLab(null),"
        + "  noGraph: emptyLab({}),"
        + "  workerSpoke: emptyLab({ lead: 'lead', board_lines: 0, graph: spoke }),"
        + "  other: emptyLab({ lead: 'lead', board_lines: 0, graph: other }),"
        + "  lines: emptyLab({ lead: 'lead', board_lines: 1, graph: base() }),"
        + "  fresh: firstSteps(quiet, null),"
        + "  hooked: firstSteps({ hook: true, repos: [] }, { open: [], recent: [] }),"
        + "  repo: firstSteps({ repos: ['atlas'] }, { open: [], recent: [] }),"
        + "  alert: firstSteps({}, { open: [], recent: [{ id: 'a' }] }),"
        + "  open: firstSteps({}, { open: [{ id: 'a' }], recent: [] })"
        + "}));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out["empty"] is True and out["noData"] is False and out["noGraph"] is False
    assert out["workerSpoke"] is False and out["other"] is False and out["lines"] is False
    assert out["fresh"] == {"lab": True, "hooks": False, "repo": False, "alert": False}
    assert out["hooked"]["hooks"] is True and out["hooked"]["lab"] is True
    assert out["repo"]["repo"] is True and out["repo"]["hooks"] is False
    assert out["alert"]["alert"] is True and out["open"]["alert"] is True


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_first_run_hides_when_the_lab_fills():
    script = (
        _FIRST
        + "function el(attrs) {"
        + "  const node = { attrs: attrs, kids: [], hidden: false, textContent: '', cls: {},"
        + "    getAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; } };"
        + "  node.classList = { add(c) { node.cls[c] = 1; }, remove(c) { delete node.cls[c]; } };"
        + "  node.querySelector = function (sel) {"
        + "    const cls = sel.slice(1); let found = null;"
        + "    (function walk(n) { n.kids.forEach(function (k) { if (found) return;"
        + "      if ((k.attrs.class || '').split(' ').indexOf(cls) !== -1) found = k; else walk(k); }); })(node);"
        + "    return found; };"
        + "  node.querySelectorAll = function (sel) { const out = [];"
        + "    (function walk(n) { n.kids.forEach(function (k) {"
        + "      if (sel === '[data-step]' && k.getAttribute('data-step')) out.push(k); walk(k); }); })(node);"
        + "    return out; };"
        + "  return node; }"
        + "function nest(parent, child) { parent.kids.push(child); return child; }"
        + "const eye = el({ id: 'first-eye' });"
        + "const first = el({ id: 'first-run' }); nest(first, eye);"
        + "['lab', 'hooks', 'repo', 'alert'].forEach(function (step) {"
        + "  const li = el({ 'data-step': step });"
        + "  nest(li, el({ class: 'first-check' })); nest(li, el({ class: 'first-num' })); nest(first, li);"
        + "});"
        + "const stage = el({ id: 'now-stage' }); const below = el({ class: 'below' });"
        + "const ids = { 'first-run': first, 'now-stage': stage, 'first-eye': eye };"
        + "const document = { getElementById(id) { return ids[id] || null; }, querySelector(sel) { return sel === '.below' ? below : null; } };"
        + "const quiet = { lead: 'lead', board_lines: 0, hook: false, repos: [], graph: { nodes: { owner: {}, lead: {}, worker: {} } } };"
        + "alertPayload = { open: [], recent: [] };"
        + "paintFirst(quiet);"
        + "const shown = { first: first.hidden, stage: stage.hidden, below: below.hidden, eye: eye.textContent, done: first.kids.filter(function (k) { return k.cls['is-done']; }).length };"
        + "paintFirst({ lead: 'lead', board_lines: 1, hook: true, repos: ['atlas'], graph: quiet.graph });"
        + "console.log(JSON.stringify({ shown: shown, filled: { first: first.hidden, stage: stage.hidden, below: below.hidden } }));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out["shown"] == {
        "first": False, "stage": True, "below": True,
        "eye": "GET STARTED · 1 OF 4 DONE", "done": 1,
    }
    assert out["filled"] == {"first": True, "stage": False, "below": False}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_copy_command_uses_clipboard_then_selection():
    script = (
        "const src = process.argv[1]; const picks = []; const timers = [];"
        + "function setTimeout(fn, ms) { timers.push({ fn: fn, ms: ms }); }"
        + "const document = { createRange() { return { selectNodeContents(node) { picks.push(node); } }; } };"
        + "const window = { getSelection() { return { removeAllRanges() {}, addRange() {} }; } };"
        + "let navigator = { clipboard: { writeText(text) { picks.push(text); return { then(ok) { ok(); } }; } } };"
        + "eval(src.slice(src.indexOf('function emptyLab'), src.indexOf('function wireCopy')));"
        + "const code = { textContent: 'python3 -m atlas hooks' };"
        + "const button = { textContent: 'Copy command' };"
        + "copyCommand(code.textContent, code, button);"
        + "const copied = button.textContent;"
        + "const delay = timers[0].ms;"
        + "timers[0].fn();"
        + "const restored = button.textContent;"
        + "button.textContent = 'Copy command';"
        + "navigator = {};"
        + "copyCommand(code.textContent, code, button);"
        + "const fell = picks[picks.length - 1] === code;"
        + "navigator = { clipboard: { writeText() { return { then(_ok, bad) { bad(); } }; } } };"
        + "copyCommand('scripts/atlas-alert needs-owner test \"Test alert\"', code, button);"
        + "console.log(JSON.stringify({ copied: copied, restored: restored, delay: delay, wrote: picks[0], fell: fell, rejected: picks[picks.length - 1] === code }));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, JS], capture_output=True, text=True, check=True).stdout)
    assert out == {
        "copied": "Copied", "restored": "Copy command", "delay": 1500,
        "wrote": "python3 -m atlas hooks", "fell": True, "rejected": True,
    }

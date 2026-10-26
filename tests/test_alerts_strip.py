"""The Alerts strip: source locks the spec."""

import re
from pathlib import Path

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


def test_alerts_section_after_problems():
    problems = INDEX.index('<section id="problems" class="problems" aria-labelledby="problems-h">')
    alerts = INDEX.index('<section id="alerts" class="alerts" aria-labelledby="alerts-h">')
    graph = INDEX.index('<section class="graph"')
    assert graph < problems < alerts  # Work follows the Now graph
    end = INDEX.index("</section>", INDEX.index("problem-list"))
    assert "<section" not in INDEX[end + len("</section>"):alerts]
    assert '<h2 id="alerts-h">Alerts</h2>' in INDEX
    assert '<ul id="alert-list"></ul>' in INDEX
    assert "style=" not in INDEX


def test_alerts_source():
    for bad in ("innerHTML", "insertAdjacentHTML", "outerHTML"):
        assert bad not in JS
    close = JS[JS.index("function closeAlert"):JS.index("function alertRow")]
    assert "X-Atlas-UI" in close and "/close" in close and "encodeURIComponent" in close
    assert 'credentials: "same-origin"' in close and 'body: "{}"' in close
    assert "https://github.com/coco-research/" in JS
    assert "No alerts." in JS and "Couldn't close" in JS
    assert 'setAttribute("role", "status")' in JS
    assert 'setAttribute("rel", "noreferrer noopener")' in JS
    tick = JS[JS.index("function tick("):JS.index("function problemRow(")]
    assert "loadAlerts(ctl.signal)" in tick
    load = JS[JS.index("function loadAlerts"):JS.index("function parentId")]
    assert '"/api/alerts"' in load and 'cache: "no-store"' in load and 'credentials: "omit"' in load


def test_alerts_styles():
    assert "var(--amber)" in re.search(r"\.alert-badge\.is-owner \{([^}]*)\}", CSS).group(1)
    assert "var(--red)" in re.search(r"\.alert-badge\.is-warn \{([^}]*)\}", CSS).group(1)
    done = re.search(r"\.alert-done \{([^}]*)\}", CSS).group(1)
    assert "min-height: 44px" in done and "padding: 12px 16px" in done
    h2 = re.search(r"\.alerts h2 \{([^}]*)\}", CSS).group(1)
    assert "font-size: 17px" in h2 and "font-weight: 700" in h2
    assert ".alerts { max-width: 100%; }" in _media("@media (max-width: 640px)")
    assert "transition" not in CSS[CSS.index(".alerts {"):]


def test_lede_counts_alerts_that_need_the_owner():
    assert "ledeText" not in JS
    paint = JS[JS.index("function paintStatus"):JS.index("function paintWaitBadge")]
    assert "AtlasStatus.statusLine(current, problemItems, alertPayload)" in paint
    assert paint.index("statusLine(") < paint.index(".head")


def test_strip_review_guards():
    close = JS[JS.index("function closeAlert"):JS.index("function alertRow")]
    assert "new AbortController()" in close and "signal: ctl.signal" in close and "clearTimeout(timer)" in close
    row = JS[JS.index("function alertRow"):JS.index("function alertStamp")]
    assert '"alert-badge is-closed", ""]' in row  # Closed carries no mark: the grey word already says closed
    render = JS[JS.index("function renderAlerts"):JS.index("function loadAlerts")]
    assert 'querySelectorAll(".alert-age")' in render
    load = JS[JS.index("function loadAlerts"):JS.index("function parentId")]
    assert "paintStatus()" in load and "ledeText" not in load
    assert "ms > 8.64e15" in JS[JS.index("function alertAge"):JS.index("function alertLink")]


def test_strip_review_round_two():
    close = JS[JS.index("function closeAlert"):JS.index("function alertRow")]
    assert 'status.textContent = "Closed"' in close
    row = JS[JS.index("function alertRow"):JS.index("function alertStamp")]
    assert 'text.setAttribute("title"' in row
    assert ".alert-src { white-space: normal" in _media("@media (max-width: 999.98px)").replace(".alert-text, .alert-link, ", "")
    assert ".alert.is-closed .alert-link" in CSS


def test_feedback_buttons_source():
    paint = JS[JS.index("function paintFeedback"):JS.index("function sendFeedback")]
    assert 'setAttribute("aria-pressed"' in paint and "feedbackSaid.get(group.dataset.subject)" in paint
    send = JS[JS.index("function sendFeedback"):JS.index("function feedbackGroup")]
    assert 'fetch("/api/feedback"' in send and 'method: "POST"' in send and 'credentials: "same-origin"' in send
    assert '"Content-Type": "application/json"' in send and '"X-Atlas-UI": alertNonce' in send
    assert "new AbortController()" in send and "signal: ctl.signal" in send and "4000" in send and "clearTimeout(timer)" in send
    assert "JSON.stringify({ kind: kind, subject: subject, verdict: verdict })" in send
    assert "Couldn't save" in send and '"fb-status is-error"' in send and "feedbackSaid.set(subject, verdict)" in send
    group = JS[JS.index("function feedbackGroup"):JS.index("function alertRank")]
    assert '["useful", "Useful"], ["not-useful", "Not useful"]' in group and 'el("button", "fb-btn", pair[1])' in group
    assert 'setAttribute("type", "button")' in group and 'setAttribute("aria-label"' in group and 'setAttribute("role", "status")' in group


def test_feedback_only_on_owner_rows():
    assert JS.count("feedbackGroup(") == 5  # definition, problems, alerts, and both Needs-you cards
    assert 'problem.owner === "owner"' in JS[JS.index("function waitsOnOwner"):JS.index("function eachFeedback")]
    assert "waitsOnOwner(item)" in JS[JS.index("function problemsStamp"):JS.index("function splitProblems")]
    rows = JS[JS.index("function renderProblems"):JS.index("function loadProblems")]
    assert "if (!built && waitsOnOwner(problem)) {" in rows and "feedbackGroup(problem.kind, problem.id, row.headline)" in rows
    alert = JS[JS.index("function alertRow"):JS.index("function alertStamp")]
    assert alert.index("if (open) {") < alert.index("feedbackGroup(") < alert.index('el("button", "alert-done"')
    assert 'row.severity === "needs-owner"' in alert and 'feedbackGroup("alert", "alert:" + String(row.id)' in alert


def test_feedback_styles():
    css = CSS[CSS.index("/* Feedback (loop 5)"):CSS.index("@media (max-width: 640px)")]
    button = re.search(r"\.fb-btn \{([^}]*)\}", css).group(1)
    assert "min-height: 44px" in button and "font-size: 13px" in button and "var(--line)" in button
    assert "var(--blue-text)" in re.search(r'\.fb-btn\[aria-pressed="true"\][^{]*\{([^}]*)\}', css).group(1)
    assert "var(--red)" in re.search(r"\.fb-status\.is-error \{([^}]*)\}", css).group(1)
    assert "transition" not in css


def test_strip_review_round_three():
    row = JS[JS.index("function alertRow"):JS.index("function alertStamp")]
    assert 'setAttribute("aria-label", "Done: " + String(row.text || ""))' in row
    assert 'if (open) li.appendChild(ageSpan("", row.ts))' in row
    close = JS[JS.index("function closeAlert"):JS.index("function alertRow")]
    assert 'status.className = "alert-status is-error"' in close
    assert 'status.className = "alert-status"' in close
    assert 'errorWords(status, "Couldn\'t close")' in close
    status = re.search(r"\.alert-status \{([^}]*)\}", CSS).group(1)
    assert "var(--ink-3)" in status
    assert "var(--red)" in re.search(r"\.alert-status\.is-error \{([^}]*)\}", CSS).group(1)
    render = JS[JS.index("function renderAlerts"):JS.index("function loadAlerts")]
    assert "ages[k].dataset.ts" in render and "open.concat(closed)" not in render  # each age carries its own ts


def test_alert_marks_follow_the_budget_panels():
    badge = JS[JS.index("function alertBadge"):JS.index("function alertAge")]
    assert '"Needs you", "alert-badge is-owner", "\\u25c7"' in badge
    assert '"Warn", "alert-badge is-warn", "\\u25b2"' in badge
    assert '"Info", "alert-badge is-info", "\\u25cb"' in badge
    row = JS[JS.index("function alertRow"):JS.index("function alertStamp")]
    assert 'mark.setAttribute("aria-hidden", "true")' in row
    assert 'btn.setAttribute("aria-label", "Done: "' in row


def test_alerts_desktop_grid():
    rule = re.search(r"\.alert \{([^}]*)\}", CSS).group(1)
    assert "display: grid" in rule
    assert "grid-template-columns: minmax(0, 104px) minmax(0, 200px) minmax(0, 1fr) 104px 264px" in rule
    assert re.search(r"\.alert-body \{[^}]*grid-column: 3;", CSS)  # the link sits under the text, in the text column
    age = re.search(r"\.alert-age \{([^}]*)\}", CSS).group(1)
    assert "text-align: right;" in age and "overflow: hidden" in age and "text-overflow: ellipsis" in age
    assert re.search(r"\.alert-actions \{[^}]*grid-column: 5;", CSS)


def test_alerts_phone_three_lines():
    media = _media("@media (max-width: 999.98px)")  # stacks before the text column gets squeezed
    assert "grid-template-columns: auto minmax(0, 1fr)" in re.search(r"\.alert \{([^}]*)\}", media).group(1)
    assert ".alert-badge { grid-column: 1; grid-row: 1; }" in media
    assert ".alert-src { grid-column: 2; grid-row: 1; }" in media
    assert ".alert-body { grid-column: 1 / -1; grid-row: 2; }" in media
    assert ".alert-age { grid-column: 1 / -1; grid-row: 3; text-align: left;" in media
    assert ".alert-actions { grid-column: 2; grid-row: 3; }" in media


def test_failure_lines_carry_a_mark():
    helper = JS[JS.index("function errorWords"):JS.index("function closeAlert")]
    assert '"alert-mark", "\\u25b2"' in helper and 'setAttribute("aria-hidden", "true")' in helper
    close = JS[JS.index("function closeAlert"):JS.index("function alertRow")]
    assert 'errorWords(status, "Couldn\'t close")' in close
    send = JS[JS.index("function sendFeedback"):JS.index("function feedbackGroup")]
    assert 'errorWords(note, "Couldn\'t save")' in send


def test_problems_drop_alert_rows():
    filt = JS[JS.index("function pageProblems"):JS.index("function problemsStamp")]
    assert 'item.kind === "alert"' in filt
    for name, end in (("function problemsStamp", "function splitProblems"),
                      ("function problemView", "function patchProblems"),
                      ("function renderProblems", "function loadProblems")):
        assert "pageProblems(" in JS[JS.index(name):JS.index(end)], name


def test_closed_rows_show_the_closed_age():
    row = JS[JS.index("function alertRow"):JS.index("function alertStamp")]
    assert 'ageSpan("closed ", row.closed_ts)' in row


def test_alerts_stale_on_a_failed_load():
    load = JS[JS.index("function loadAlerts"):JS.index("function parentId")]
    assert 'getElementById("alerts")' in load
    assert 'classList.add("stale")' in load and 'classList.remove("stale")' in load


def test_shoulds_review_fixes():
    render = JS[JS.index("function renderAlerts"):JS.index("function loadAlerts")]
    assert "ages[k].dataset.prefix" in render and "ages[k].dataset.ts" in render
    row = JS[JS.index("function alertRow"):JS.index("function alertStamp")]
    assert 'ageSpan("closed ", row.closed_ts)' in row and 'ageSpan("", row.ts)' in row
    load = JS[JS.index("function loadAlerts"):JS.index("function parentId")]
    assert '"Alerts unavailable. Retrying."' in load and "!host.firstChild" in load
    assert "alertsDrawn" not in load[load.index('"Alerts unavailable. Retrying."'):]  # host is empty only before the first draw, so resetting the stamp is a no-op
    assert "flex-wrap: wrap" in re.search(r"\.alert-actions \{([^}]*)\}", CSS).group(1)


def test_alert_link_focus_ring_survives_the_clip():
    rule = re.search(r"\.alert-link:focus-visible \{([^}]*)\}", CSS).group(1)
    assert "outline-offset: -2px" in rule and "var(--blue)" in rule


def test_stale_sections_say_so():
    # Signal: every state gets shape plus word. A stale section keeps its red edge and adds the words, right after its h2.
    helper = JS[JS.index("function staleNote"):JS.index("function closeAlert")]
    assert '"stale-note"' in helper and 'setAttribute("role", "status")' in helper
    assert 'errorWords(span, "Not updating. Retrying.")' in helper  # the triangle is the helper's, aria-hidden
    problems = JS[JS.index("function loadProblems"):JS.index("function formatReset")]
    assert "staleNote(section, true)" in problems  # added on the failure path
    assert "staleNote(section, false)" in problems  # removed again on a good load, not on a button redraw
    alerts = JS[JS.index("function loadAlerts"):JS.index("function parentId")]
    assert "staleNote(section, true)" in alerts and "staleNote(section, false)" in alerts
    note = re.search(r"\.stale-note \{([^}]*)\}", CSS).group(1)
    assert "font-size: 12px" in note and "var(--red)" in note


def test_first_load_failure_carries_the_mark():
    alerts = JS[JS.index("function loadAlerts"):JS.index("function parentId")]
    assert 'errorWords(empty, "Alerts unavailable. Retrying.")' in alerts


def test_closed_row_drops_its_mark_and_keeps_one_gap():
    row = JS[JS.index("function alertRow"):JS.index("function alertStamp")]
    assert '"alert-badge is-closed", ""]' in row  # the closed badge carries no glyph
    assert "if (badge[2]) {" in row  # so no empty mark span is built, and no mark margin lands on the row
    media = _media("@media (max-width: 999.98px)")
    age = re.search(r"\.alert-age \{([^}]*)\}", media).group(1)
    # The long "closed …" age shares column 1 with the badge; spanning both tracks keeps it from widening the badge column,
    # so the gap to the source is the single 8px column gap on every row.
    assert "grid-column: 1 / -1" in age


def test_followup_review_fixes():
    rows = JS[JS.index("function renderProblems"):JS.index("function loadProblems")]
    assert "staleNote(section, false)" not in rows and 'classList.remove("stale")' not in rows
    load = JS[JS.index("function loadProblems"):JS.index("function formatReset")]
    assert load.index("staleNote(section, false)") < load.index("renderProblems(items)")
    assert '"alert-empty is-error"' in JS
    assert "var(--red)" in re.search(r"\.alert-empty\.is-error \{([^}]*)\}", CSS).group(1)
    assert "max-width: 45%" in re.search(r"\.alert-age \{ grid-column: 1 / -1;([^}]*)\}", CSS).group(1)

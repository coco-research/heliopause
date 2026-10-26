(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const LX = [16, 196, 376, 556, 736, 976, 1156];
  const BW = 150;
  // The design is W_MIN wide: LX[0] left of the first lane, SPAN across the lanes, BW + 16 right of the last. A wider window is
  // drawn at true size (1 unit is 1px, so orbs, text and rings keep their size) and the extra width goes into the gaps between the
  // lanes. Narrower than the design the drawing scales down, as before.
  const W_MIN = LX[LX.length - 1] + BW + 16;
  const SPAN = LX[LX.length - 1] - LX[0];
  // Fit, never enlarge: a graph taller than the window (less FIT_ROOM) shrinks to fit it, but never below FIT_MIN of its size.
  const FIT_ROOM = 24;
  const FIT_MIN = 0.83;  // Design M5: below this the kind line drops under legible size; the page scrolls instead
  const TOP = 44;
  // A node is an orb with its name and a kind line under it. PITCH is one slot: the 40px orb, the two text lines, and room
  // for the widest rings (waits plus working reach 29 from the centre), so a ring never touches a neighbour's text.
  const ORB_R = 20;
  const NODE_H = 78;
  const PITCH = 84;
  const NAME_DY = 61;
  const KIND_DY = 75;
  // The gap between the orb, the state ring and the working ring; an edge ends this far outside the orb.
  const RING_GAP = 2;
  // HQ lane: every role is drawn, and past this many nodes each shrinks to a 24px orb with its name beside it (never in text size).
  const HQ_COMPACT_MAX = 8;
  const ORB_R_COMPACT = 12;
  const COMPACT_H = 44;
  const PITCH_COMPACT = 46;
  const COMPACT_CX = 13;
  const COMPACT_NAME_X = 37;
  // A compact name plus its tail ("" when there is no mark) takes at most this many units: the room from the name to the lane
  // edge (the lane rect ends 8 past the slot) less the plate's 4. Where text cannot be measured, each character counts COMPACT_CHAR_W.
  const COMPACT_NAME_MAX = BW + 8 - COMPACT_NAME_X - 4;
  const COMPACT_CHAR_W = 7.5;
  const MAX_IN_FLIGHT = 6;
  const MAX_QUEUED = 20;
  const CALM_S = 60;
  const AMBIENT_SPEED = 90;
  const AMBIENT_MAX = 24;
  const AMBIENT_BUSIEST_S = 8;
  const AMBIENT_JITTER = 0.3;
  const AMBIENT_DOT_R = 3;
  const AMBIENT_TRAIL = 12;
  const LIVE_DOT_R = 4;
  // Comet trail: three stacked strokes of the same path, each one dash ending at the head. Style per .trail.t0..t2.
  const TRAIL_LENGTHS = [16, 32, 52];
  const REPLAY_DOT_R = 3;
  const REPLAY_SPEEDUP = 10;
  const REPLAY_IDLE_S = 60;
  const REPLAY_RESTART_S = 10;
  const REPLAY_GAP_CAP_S = 3;
  const REPLAY_MAX = 6;
  // ponytail: collapsed list stays 8 rows; Show all expands until reload. Raise PROBLEM_CAP to show more collapsed.
  const PROBLEM_CAP = 8;
  const SETUP_POLL_MS = 600000;
  const REPLAY_LABEL = "Replay · the last hour at 10×";
  const REPLAY_PROV = " · replaying the last hour at 10×";
  const EVENT_KINDS = ["post", "mention", "resolved", "ask", "answer", "job", "job-start", "job-done", "job-failed"];
  const KIND_TEXT = {
    post: "posts an update",
    resolved: "marks it resolved",
    ask: "sends HQ an ask",
    answer: "answers",
    job: "sends a job",
    "job-start": "starts the job",
    "job-done": "finishes a job",
    "job-failed": "job failed"
  };

  const svg = document.getElementById("g");
  let current = null;
  let nowMs = 0;
  let updatedLabel = "";
  let sel = null;
  let jumpKeepsOpen = false;
  let pos = {};
  let wires = [];
  let bbArcs = [];
  let edgeKey = null;  // the clicked pair, so a redraw re-derives its caption from fresh fibres
  let nodeEls = {};
  let bbG = null;
  let pending = false;
  let H = 560;
  let W = W_MIN;  // this render's drawing width in user units, and the lane x positions drawn from it
  let laneX = LX;
  let liveG = null;
  let ambG = null;
  let replayG = null;
  let amb = {};
  let ambDots = [];
  let ambBroken = null;
  let replayFlight = 0;
  let replaying = false;
  let replayGen = 0;
  let replayTimer = 0;
  let replayRows = [];
  let visibleSince = 0;
  let lastRealAt = 0;
  let inFlight = 0;
  let drawing = false;
  let queue = [];
  let dropTimes = [];
  let log = [];
  const nodeLog = {};
  let lastEventAt = 0;
  let lastWhen = null;
  let offline = false;
  let esDown = false;
  let pollDown = false;
  let flightSeq = 0;
  let stream = null;
  let reduceMotion = false;
  let motionQuery = null;
  let narrow = false;
  let narrowQuery = null;
  // Focused-view selection. Survives state refreshes. Null means the owner.
  let focusId = null;
  let focusPending = null;
  let focusTimer = 0;
  let focusDrawn = "";
  let problemsDrawn = null;
  let problemItems = null;
  let problemsExpanded = false;
  let staleExpanded = false;
  let railG = null;
  // A variable, so the page's drawOrb string check still sees only the graph and the table.
  const ROAD_ORB = "r";
  let roadmapPayload = null;
  let roadmapDrawn = "";
  let roadmapOpen = false;
  let roadmapFile = "";
  let roadmapReturn = null;
  let budgetPayload = null;
  let budgetDrawn = "";
  let alertPayload = null;
  let alertNonce = "";
  let alertsDrawn = "";
  let alertBusy = 0;
  let needsDrawn = "";
  // ponytail: in memory only, so a reload shows both buttons unpressed (the file keeps every click). Upgrade: GET /api/feedback returns the folded verdicts; seed this from it.
  const feedbackSaid = new Map();  // subject -> "useful" | "not-useful"; repainted on every redraw
  const feedbackBusy = new Set();  // subjects with a POST in flight

  try {
    motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    reduceMotion = motionQuery.matches;
  } catch (err) {
    reduceMotion = false;
  }

  try {
    narrowQuery = window.matchMedia("(max-width: 1099.98px)");
    narrow = narrowQuery.matches;
  } catch (err) {
    narrow = false;
  }

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function hhmm(d) {
    return pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function hms(d) {
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  function missing(value) {
    return value === null || value === undefined;
  }

  function num(value) {
    return typeof value === "number" && isFinite(value) ? value : 0;
  }

  function nodeById(nodes, id) {
    if (!nodes || id === null || id === undefined) return null;
    if (!Object.prototype.hasOwnProperty.call(nodes, id)) return null;
    return nodes[id];
  }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== null && text !== undefined) node.textContent = text;
    return node;
  }

  function svgEl(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        if (!missing(attrs[key])) node.setAttribute(key, attrs[key]);
      });
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  function showThemeIcon() {
    // WHY: the icon is the mode a click turns on. atlas-theme.js already set data-theme.
    const dark = document.documentElement.dataset.theme === "dark";
    const sun = document.getElementById("theme-sun");
    const moon = document.getElementById("theme-moon");
    if (sun) {
      if (dark) sun.removeAttribute("display");
      else sun.setAttribute("display", "none");
    }
    if (moon) {
      if (dark) moon.setAttribute("display", "none");
      else moon.removeAttribute("display");
    }
  }

  function toggleTheme() {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("atlas.theme", next);
    } catch (err) {
      /* storage can be blocked; the page still switches for this visit */
    }
    showThemeIcon();
    redrawOrbs();
  }

  function ageOf(stamp, now) {
    if (!stamp) return "";
    const then = Date.parse(stamp);
    if (isNaN(then)) return "";
    let mins = Math.floor((now - then) / 60000);
    if (mins < 1) return "now";
    if (mins < 60) return mins + "m";
    const hours = Math.floor(mins / 60);
    if (hours < 24) return hours + "h";
    const days = Math.floor(hours / 24);
    const left = hours % 24;
    return left ? days + "d " + left + "h" : days + "d";
  }

  function glyph(state) {
    if (state === "waits") return "\u25cf";
    if (state === "failed") return "\u25b2";
    if (state === "quiet" || state === "paused") return "\u25cb";
    if (state === "blocked" || state === "at_cap") return "\u25c7";
    if (state === "working") return "\u25d0";
    if (state === "waiting") return "\u25cb";
    if (state === "done") return "\u25cf";
    return "";
  }

  function tone(state) {
    if (state === "waits") return "blue";
    if (state === "failed") return "red";
    if (state === "blocked" || state === "at_cap") return "amber";
    return "";
  }

  function waiting(decisions) {
    return (decisions || []).filter(function (item) {
      return item && item.status === "needs-owner";
    }).sort(function (a, b) {
      if (!a.ts && b.ts) return 1;
      if (a.ts && !b.ts) return -1;
      if (!a.ts || !b.ts) return 0;
      if (a.ts < b.ts) return -1;
      if (a.ts > b.ts) return 1;
      return 0;
    });
  }

  function decisionLabel(item) {
    const nodes = current && current.graph && current.graph.nodes;
    const node = item.node ? nodeById(nodes, item.node) : null;
    if (node && node.label) return String(node.label);
    if (item.name) return String(item.name);
    return "A part";
  }

  function phraseOf(item) {
    if (item.headline) return String(item.headline);
    return decisionLabel(item) + " is waiting";
  }

  function blockedNodes(graph) {
    const out = [];
    (graph.lanes || []).forEach(function (lane) {
      (lane.nodes || []).forEach(function (id) {
        const node = nodeById(graph.nodes, id);
        if (node && node.state === "blocked") out.push(node);
      });
    });
    return out;
  }

  // One sentence from the same arrays the lists render. Problems and alerts each call this so the line never lags.
  function paintStatus() {
    const lede = document.getElementById("lede");
    if (!current || !lede || typeof AtlasStatus === "undefined") return;
    const line = AtlasStatus.statusLine(current, problemItems, alertPayload);
    lede.textContent = line.head;
    paintWaitBadge(line);
    paintNeeds();
    paintBroken(line.broken);
  }

  // The Now badge is the needs-you count. Empty when nothing is.
  function paintWaitBadge(line) {
    const badge = document.getElementById("now-badge");
    if (!badge || !line) return;
    const n = line.needs;
    badge.textContent = n ? String(n) : "";
    badge.hidden = !n;
  }

  function ageStamp(ts) {
    if (typeof ts === "number" && isFinite(ts)) return ts;
    if (typeof ts !== "string" || !ts) return "";
    const parsed = Date.parse(ts);
    return isNaN(parsed) ? "" : parsed;
  }

  function openAlert(row) {
    const open = alertPayload && Array.isArray(alertPayload.open) ? alertPayload.open : [];
    const id = row && row.id;
    for (let i = 0; i < open.length; i++) {
      if (open[i] && open[i].id === id) return open[i];
    }
    return null;
  }

  function problemById(id) {
    const list = Array.isArray(problemItems) ? problemItems : [];
    for (let i = 0; i < list.length; i++) {
      if (list[i] && list[i].id === id) return list[i];
    }
    return null;
  }

  function needsCard(row) {
    const card = el("article", "needs-card");
    const eye = el("p", "needs-eye");
    const badge = el("span", "alert-badge is-owner");
    const mark = el("span", "alert-mark", "\u25c7");
    mark.setAttribute("aria-hidden", "true");
    badge.appendChild(mark);
    badge.appendChild(document.createTextNode(row.kind || ""));
    eye.appendChild(badge);
    if (row.from) eye.appendChild(el("span", "needs-from", " \u00b7 " + row.from));
    const stamp = ageStamp(row.ts);
    if (stamp !== "") eye.appendChild(ageSpan("", stamp));
    card.appendChild(eye);
    card.appendChild(el("h3", "needs-title", row.title || ""));
    card.appendChild(needsActions(row));
    return card;
  }

  // The same Open / Done / Useful (or Show agent) controls as a Needs-you card.
  function needsActions(row) {
    const actions = el("div", "needs-actions");
    if (row.kind === "alert") {
      const alert = openAlert(row) || { id: row.id, text: row.title };
      const open = cardOpen(alert.link);
      if (open) actions.appendChild(open);
      const btn = el("button", "alert-done", "Done");
      btn.setAttribute("type", "button");
      btn.setAttribute("aria-label", "Done: " + String(alert.text || row.title || ""));
      const note = el("span", "alert-status", "");
      note.setAttribute("role", "status");
      btn.addEventListener("click", function () { closeAlert(alert, btn, note); });
      actions.appendChild(btn);
      actions.appendChild(note);
      actions.appendChild(feedbackGroup("alert", "alert:" + String(row.id), String(alert.text || row.title || "")));
    } else if (row.kind === "decision") {
      const id = row.id;
      const nodes = current && current.graph && current.graph.nodes;
      if (nodeById(nodes, id)) {
        const btn = el("button", "needs-act", "Show agent");
        btn.setAttribute("type", "button");
        btn.addEventListener("click", function () { select(id); });
        actions.appendChild(btn);
      }
    } else {
      const problem = problemById(row.id);
      const raw = problem && (problem.link || problem.next_action);
      const open = cardOpen(raw);
      if (open) actions.appendChild(open);
      if (problem) actions.appendChild(feedbackGroup(problem.kind, problem.id, row.title || ""));
    }
    return actions;
  }

  // alertLink decides what is safe. The card's one filled action is that link; Done stays outlined.
  function cardOpen(raw) {
    const built = alertLink(raw);
    if (!built || typeof raw !== "string") return null;
    let host = "";
    if (raw.indexOf("https://") === 0) host = raw.slice(8).split("/")[0].split(":")[0];
    const a = el("a", "needs-act", host === "github.com" ? "Open on GitHub" : "Open");
    const href = built.getAttribute("href");
    if (href) {
      a.setAttribute("href", href);
      a.setAttribute("rel", "noreferrer noopener");
      a.setAttribute("target", "_blank");
      return a;
    }
    if (raw.charAt(0) === "/" && raw.charAt(1) !== "/") {
      a.setAttribute("href", raw);
      return a;
    }
    return null;
  }

  function fyiSummary(quieter, groups) {
    const word = quieter === 1 ? " quieter thing" : " quieter things";
    let text = "FYI · " + quieter + word;
    if (!groups.length) return text;
    const labels = [];
    const n = groups.length > 3 ? 3 : groups.length;
    for (let i = 0; i < n; i++) labels.push(groups[i].label || "");
    text += " (" + labels.join(", ");
    if (groups.length > 3) text += "…";
    return text + ")";
  }

  // 0 means show every card: phone, or a list that is not a grid (the side column is display:block).
  function needsColumns(list) {
    if (!window.innerWidth || window.innerWidth < 800) return 0;
    if (typeof getComputedStyle !== "function") return 0;
    const raw = getComputedStyle(list).gridTemplateColumns;
    if (!raw || raw === "none") return 0;
    return raw.split(/\s+/).filter(function (part) { return !!part; }).length;
  }

  function fitNeedsRow(list) {
    const cols = needsColumns(list);
    const cards = list.children;
    const cap = cols > 0 ? cols : cards.length;
    let extra = 0;
    for (let i = 0; i < cards.length; i++) {
      const hide = i >= cap;
      cards[i].hidden = hide;
      if (hide) extra += 1;
    }
    const more = document.getElementById("needs-more");
    if (!more) return;
    more.hidden = extra === 0;
    if (extra) more.textContent = extra + " more \u2192";
  }

  function paintNeeds() {
    const list = document.getElementById("needs-list");
    const empty = document.getElementById("needs-empty");
    const fyi = document.getElementById("fyi");
    const sum = document.getElementById("fyi-sum");
    const fyiList = document.getElementById("fyi-list");
    const head = document.getElementById("needs-head");
    const meta = document.getElementById("needs-meta");
    if (!list || !current || typeof AtlasStatus === "undefined") return;
    const rows = AtlasStatus.needsYou(current, problemItems, alertPayload);
    const groups = AtlasStatus.fyiGroups(problemItems, alertPayload);
    let quieter = 0;
    for (let i = 0; i < groups.length; i++) quieter += groups[i].count || 0;
    if (empty) empty.hidden = true;
    if (head) head.hidden = rows.length === 0;
    if (meta) meta.textContent = rows.length ? rows.length + " \u00b7 oldest first" : "";
    if (fyi) fyi.hidden = quieter === 0;
    if (sum) sum.textContent = fyiSummary(quieter, groups);
    const stamp = JSON.stringify([rows, groups]);
    if (stamp !== needsDrawn) {
      needsDrawn = stamp;
      clear(list);
      for (let i = 0; i < rows.length; i++) list.appendChild(needsCard(rows[i]));
      if (fyiList) {
        clear(fyiList);
        for (let i = 0; i < groups.length; i++) {
          const li = el("li");
          const a = el("a", "", groups[i].label);
          a.setAttribute("href", "#work");
          li.appendChild(a);
          fyiList.appendChild(li);
        }
      }
    } else {
      const ages = list.querySelectorAll(".alert-age");
      for (let k = 0; k < ages.length; k++) ages[k].textContent = (ages[k].dataset.prefix || "") + alertAge(ages[k].dataset.ts);
    }
    fitNeedsRow(list);
  }

  function brokenPhrase(n, groups) {
    let label = "";
    for (let i = 0; i < groups.length; i++) {
      const worst = groups[i] && groups[i].worst;
      if (worst === "high" || worst === "red") { label = groups[i].label; break; }
    }
    return label ? n + " broken: " + label : String(n) + " broken";
  }

  function paintBroken(n) {
    const host = document.getElementById("broken");
    const line = document.getElementById("broken-line");
    if (!host || !line || typeof AtlasStatus === "undefined") return;
    if (!(n > 0)) { host.hidden = true; return; }
    host.hidden = false;
    errorWords(line, brokenPhrase(n, AtlasStatus.fyiGroups(problemItems, alertPayload)));
  }

  function calmNow(now) {
    if (!lastEventAt) return true;
    return now - lastEventAt >= CALM_S * 1000;
  }

  function provText(graph) {
    const parts = graph.nodes ? Object.keys(graph.nodes).length : 0;
    let text = parts + " agents \u00b7 " + num(graph.messages_24h) + " messages and " +
      num(graph.mentions_24h) + " mentions in the last 24 h";
    if (offline) return text;
    if (!calmNow(Date.now())) return text + " \u00b7 live";
    if (replaying) return text + REPLAY_PROV;
    return text + " \u00b7 none in the last minute";
  }

  function hasPos(id) {
    return Object.prototype.hasOwnProperty.call(pos, id);
  }

  function sameLane(graph, a, b) {
    const left = nodeById(graph.nodes, a);
    const right = nodeById(graph.nodes, b);
    return !!(left && right && left.lane === right.lane);
  }

  // Every node in the lane: no lane hides a part.
  function laneNodes(lane) {
    return lane.nodes || [];
  }

  function kindText(node) {
    return node.kind || "";
  }

  // ---- orbs: a seeded avatar per id (orbDraw for the draw, the slot and the roster rules) so one id is always one orb.
  // The colour model is orbDraw and only orbDraw. ----
  // Ten slots at S 60%, in the spec's order: hue, then mid-tone L. Five hues 36 degrees apart in two lightness bands, none a status hue.
  const ORB_SLOTS = [[176, 38], [248, 38], [320, 60], [176, 60], [248, 60], [320, 38], [212, 38], [212, 60], [284, 38], [284, 60]];
  const ORB_SAT = 60;
  // A node's lightness step moves all three tones; it is small enough never to cross into the other band.
  const ORB_STEPS = [-4, 0, 4];
  // The light tone sits ORB_LIGHT_LIFT above the mid tone and the rim ORB_DEEP_DROP below it; in the dark theme the rim stops at L 30.
  const ORB_LIGHT_LIFT = 24;
  const ORB_DEEP_DROP = 18;
  const ORB_DEEP_FLOOR = 30;
  // Highlight positions 0-7 on the upper half of an orb, in the order NG hands them out to the members of one slot: far left, far
  // right, top centre, then the spots in between.
  const ORB_SPREAD = [0, 7, 3, 5, 1, 6, 2, 4];
  const ORB_HIGHLIGHTS = ORB_SPREAD.length;
  // State rings in px. Only these states draw one; idle, working, waiting and done have none.
  const RING_WIDTH = { waits: 3, failed: 2, blocked: 2, at_cap: 2, paused: 1.5, quiet: 1.5 };
  const WORK_W = 2;

  // FNV-1a, 32 bit: the same xor and multiply as NG.
  function fnv1a(str) {
    let h = 0x811c9dc5;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return h >>> 0;
  }

  // mulberry32: the closure keeps the seed and advances it once per call.
  function mulberry32(seed) {
    let a = seed;
    return function () {
      a |= 0;
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // Everything the SVG needs that does not depend on pixel size. The stream is NG's, in NG's order: step, a burned draw (NG's old
  // hue step, kept so the highlight and the glow stay on the same draws), highlight position, highlight radius, glow angle, glow
  // radius, glow opacity. The seed is fnv1a(id) ^ 227 as an unsigned integer and the base slot is that seed % 10, as in NG.
  // `assigned` is the node's entry from planOrbs (slot, highlight position, step), as NG's server sends orb_slot, orb_highlight and
  // orb_step; without one the orb is just its own seed.
  function orbDraw(id, dark, assigned) {
    const css = function (h, s, l) {
      return "hsl(" + h.toFixed(1) + " " + s + "% " + l.toFixed(1) + "%)";
    };
    const seed = (fnv1a(id) ^ 227) >>> 0;
    const rnd = mulberry32(seed);
    const ownStep = ORB_STEPS[Math.floor(rnd() * ORB_STEPS.length)];
    rnd();
    const ownIndex = Math.floor(rnd() * ORB_HIGHLIGHTS);
    const radius = 22 + rnd() * 12;
    const jitter = (rnd() - 0.5) * 0.8;
    const glowRadius = 22 + rnd() * 12;
    const opacity = 0.3 + rnd() * 0.15;
    const given = assigned || {};
    const slot = given.slot === undefined ? seed % ORB_SLOTS.length : given.slot;
    const step = given.step === undefined ? ownStep : given.step;
    const index = given.index === undefined ? ownIndex : given.index;
    const hue = ORB_SLOTS[slot][0];
    const mid = ORB_SLOTS[slot][1] + step;
    const theta = Math.PI + (index + 0.5) * (Math.PI / ORB_HIGHLIGHTS);
    const glowAngle = theta + Math.PI + jitter;
    return {
      slot: slot,
      step: step,
      index: index,
      hue: hue,
      light: css(hue, ORB_SAT, mid + ORB_LIGHT_LIFT),
      mid: css(hue, ORB_SAT, mid),
      deep: css(hue, ORB_SAT, dark ? Math.max(ORB_DEEP_FLOOR, mid - ORB_DEEP_DROP) : mid - ORB_DEEP_DROP),
      highlight: { x: 50 + Math.cos(theta) * radius, y: 50 + Math.sin(theta) * radius },
      glow: { x: 50 + Math.cos(glowAngle) * glowRadius, y: 50 + Math.sin(glowAngle) * glowRadius, opacity: opacity },
      gradientRadius: radius + 56
    };
  }

  // A negative delay, seeded per id, size and state, so a roster never pulses in step.
  function animationDelay(id, size, state) {
    const n = mulberry32(fnv1a(id + ":" + size + ":" + state))();
    return (-(n * 1.6)).toFixed(2);
  }

  // The roster rules, as NG's server runs them (employee_orb.py), once per render over every node in every lane (not only the drawn
  // ones, so an orb never changes when an HQ role first appears). Lanes share no node (atlas/graph.py _LANES), so plan[id] is set
  // once and NG's rules for a member an earlier team placed never apply. Slots: lanes in display order, nodes in id order; a node
  // whose base slot is taken in its lane takes the first free slot scanning 0 to 9, and past ten nodes the least used one (lowest
  // number on a tie). Then, across the graph, the nodes on each slot in id order: the k-th takes highlight ORB_SPREAD[k % 8]. The
  // 1st keeps its seeded step and the 2nd takes the step furthest from it. The 3rd to 8th keep theirs. A 9th or later keeps its own
  // unless another node on that slot and highlight wears it, then takes the first free of 0, -4, +4 (or keeps it if none is free).
  // The graph, the table and the phone all read this one plan.
  function planOrbs(graph) {
    const plan = Object.create(null);
    const onSlot = ORB_SLOTS.map(function () { return []; });
    // The VP walks with the products lane (design lead, 2026-10-03): alone in its lane it twinned a product orb exactly (ΔE 0).
    const walks = (graph.lanes || []).filter(function (lane) { return lane.id !== "vp"; }).map(function (lane) {
      const vp = lane.id === "products" ? (graph.lanes || []).filter(function (l) { return l.id === "vp"; }) : [];
      return { nodes: (lane.nodes || []).concat.apply(lane.nodes || [], vp.map(function (l) { return l.nodes || []; })) };
    });
    walks.forEach(function (lane) {
      const used = ORB_SLOTS.map(function () { return 0; });
      (lane.nodes || []).filter(function (id) { return !!nodeById(graph.nodes, id); }).sort().forEach(function (id) {
        let slot = orbDraw(id, false).slot;
        if (used[slot]) {
          const free = used.indexOf(0);
          slot = free >= 0 ? free : used.indexOf(Math.min.apply(null, used));
        }
        used[slot] += 1;
        plan[id] = { slot: slot };
        onSlot[slot].push(id);
      });
    });
    onSlot.forEach(function (ids) {
      const worn = ORB_SPREAD.map(function () { return []; });  // the steps already on each highlight position
      let first = 0;
      ids.sort().forEach(function (id, k) {
        const seeded = orbDraw(id, false).step;
        const index = ORB_SPREAD[k % ORB_HIGHLIGHTS];
        let step = seeded;
        if (k === 0) {
          first = seeded;
        } else if (k === 1) {
          step = first === 4 ? -4 : 4;
        } else if (k >= ORB_HIGHLIGHTS && worn[index].indexOf(seeded) >= 0) {
          const free = [0, -4, 4].filter(function (s) { return worn[index].indexOf(s) < 0; });
          if (free.length) step = free[0];
        }
        worn[index].push(step);
        plan[id].index = index;
        plan[id].step = step;
      });
    });
    return plan;
  }

  let orbPlan = Object.create(null);

  // The rings outside an orb of radius r, as radii of their centre lines: a paper gap, then the state ring, then the working
  // ring, each RING_GAP apart. outer is the outermost edge; the pick and ping circles sit beyond it.
  function orbRings(r, state, working) {
    const width = Object.prototype.hasOwnProperty.call(RING_WIDTH, state) ? RING_WIDTH[state] : 0;
    const out = { width: width, gap: 0, ring: 0, work: 0, outer: r };
    if (width) {
      out.gap = r + RING_GAP / 2;
      out.ring = r + RING_GAP + width / 2;
      out.outer = r + RING_GAP + width;
    }
    if (working) {
      out.work = out.outer + RING_GAP + WORK_W / 2;
      out.outer += RING_GAP + WORK_W;
    }
    return out;
  }

  function darkNow() {
    return document.documentElement.dataset.theme === "dark";
  }

  // Every orb gets its own gradient ids. The counter is what makes them unique: two part ids that differ only in punctuation
  // ("a.b", "a/b") clean to the same string. ctx (graph, table, focus box, partner row) and the id are there to read.
  let orbSeq = 0;

  // One orb in NG's 100 unit box (Orb.tsx without the dots): a body gradient, a glow gradient and a hairline rim. Decorative
  // always: the name next to it is text.
  function drawOrb(parent, ctx, id, size, at) {
    const d = orbDraw(id, darkNow(), orbPlan[id]);
    const uid = "o-" + ctx + "-" + String(id).replace(/[^A-Za-z0-9_-]/g, "_") + "-" + size + "-" + orbSeq++;
    const rim = size <= 24 ? 1 : 1.5;
    const orb = svgEl("svg", {
      "class": "orb",
      viewBox: "0 0 100 100",
      width: size,
      height: size,
      x: at ? at.x : null,
      y: at ? at.y : null,
      "aria-hidden": "true",
      focusable: "false"
    }, parent);
    const defs = svgEl("defs", {}, orb);
    const gradient = function (suffix, cx, cy, r, stops) {
      const node = svgEl("radialGradient", { id: uid + suffix, gradientUnits: "userSpaceOnUse", cx: cx.toFixed(2), cy: cy.toFixed(2), r: r }, defs);
      stops.forEach(function (s) { svgEl("stop", { offset: s[0], "stop-color": s[1], "stop-opacity": s[2] }, node); });
      svgEl("circle", { cx: 50, cy: 50, r: 50, fill: "url(#" + uid + suffix + ")" }, orb);
    };
    gradient("b", d.highlight.x, d.highlight.y, d.gradientRadius.toFixed(2), [["0%", d.light], ["46%", d.mid], ["100%", d.deep]]);
    gradient("g", d.glow.x, d.glow.y, "36", [["0%", d.light, d.glow.opacity.toFixed(3)], ["100%", d.light, "0"]]);
    svgEl("circle", {
      "class": "orb-edge",
      cx: 50,
      cy: 50,
      r: (50 - (rim / 2) * (100 / size)).toFixed(2),
      "stroke-width": rim,
      "vector-effect": "non-scaling-stroke"
    }, orb);
    return orb;
  }

  // The rings outside an orb centred at cx, cy: the working ring (returned, so the graph can phase it), then the paper gap and the
  // state ring. A state without a ring draws no gap or ring, and only a working part draws the working ring.
  function drawRings(parent, cx, cy, rings) {
    const work = rings.work ? svgEl("circle", { "class": "glow", cx: cx, cy: cy, r: rings.work, "stroke-width": WORK_W }, parent) : null;
    if (rings.width) {
      svgEl("circle", { "class": "gap", cx: cx, cy: cy, r: rings.gap, "stroke-width": RING_GAP }, parent);
      svgEl("circle", { "class": "ring", cx: cx, cy: cy, r: rings.ring, "stroke-width": rings.width }, parent);
    }
    return work;
  }

  // The phone's orb: the 24px orb with the same rings as a compact graph node, in a cell sized to hold them (the working ring is
  // static here). State must read without colour, and on the phone the ring shape is what tells waits from done.
  function drawOrbCell(parent, ctx, id, node) {
    const state = node.state || "idle";
    const rings = orbRings(ORB_R_COMPACT, state, node.active === true);
    const size = 2 * rings.outer + 2;
    const mid = size / 2;
    const cell = svgEl("svg", {
      "class": "orb-cell " + state,
      viewBox: "0 0 " + size + " " + size,
      width: size,
      height: size,
      "aria-hidden": "true",
      focusable: "false"
    }, parent);
    drawRings(cell, mid, mid, rings);
    drawOrb(cell, ctx, id, 2 * ORB_R_COMPACT, { x: mid - ORB_R_COMPACT, y: mid - ORB_R_COMPACT });
    return cell;
  }

  // The tones depend on the theme, so a toggle redraws every orb: the graph, the table and the focused view.
  function redrawOrbs() {
    if (!current || !current.graph) return;
    draw(current.graph);
    table(current.graph);
    focusDrawn = "";
    renderFocus(current.graph, true);
    roadmapDrawn = "";
    if (roadmapPayload) renderRoadmap(roadmapPayload);
  }

  // Fit, never enlarge: 1 when a graph h tall fits in room px, else the scale that makes it fit, never below FIT_MIN.
  function fitScale(h, room) {
    return room > 0 && h > room ? Math.max(FIT_MIN, room / h) : 1;
  }

  // The width to draw a graph h tall in, in user units, for the container and window as they are now. The container's width in CSS
  // px makes 1 unit 1px, the design width is the least, and a graph shrunk to fit the window is drawn 1 / s wider, so the lanes still
  // span the whole width. No container width (not laid out yet) means the design width.
  function drawW(h) {
    const box = svg.parentNode;
    const c = (box && box.clientWidth) || 0;
    const base = c && c < W_MIN ? c / W_MIN : 1;  // a container under the design width already scales the drawing by this
    // FIT_MIN bounds what reaches the screen (base * s), so a narrow, short window scrolls instead of shrinking text further.
    const s = Math.min(1, Math.max(FIT_MIN / base, fitScale(h * base, window.innerHeight - FIT_ROOM)));
    return Math.round(Math.max(W_MIN, c) / s * 100) / 100;
  }

  // A new window size needs a new layout, but only on a wide screen (narrow draws the focused view) and only when it would change
  // the drawing: every container narrower than the design is the same drawing.
  function relayout() {
    if (!narrow && current && current.graph && drawW(H) !== W) draw(current.graph);
  }

  function layout(graph) {
    pos = {};
    const lanes = graph.lanes || [];
    // The neediest lane sets H: its nodes at their pitch.
    let need = 0;
    lanes.forEach(function (lane) {
      const count = laneNodes(lane).length;
      need = Math.max(need, count * (lane.id === "hq" && count > HQ_COMPACT_MAX ? PITCH_COMPACT : PITCH));
    });
    H = Math.max(560, TOP + 12 + need);
    // The lanes keep their width. The width over the design goes into the gaps, in proportion to each lane's distance from the
    // first, so at the design width this is LX exactly. Whole pixels keep text and rings crisp.
    W = drawW(H);
    laneX = LX.map(function (x) { return Math.round(x + (x - LX[0]) * (W - W_MIN) / SPAN); });
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    lanes.forEach(function (lane, i) {
      if (missing(laneX[i])) return;
      const list = laneNodes(lane);
      const compact = lane.id === "hq" && list.length > HQ_COMPACT_MAX;
      const avail = H - TOP - 12;
      const h = compact ? COMPACT_H : NODE_H;
      list.forEach(function (id, j) {
        const y = Math.round(TOP + avail * (j + 0.5) / list.length - h / 2);
        // y is the top of the node. The orb centre is where every edge, pulse and ring is measured from.
        pos[id] = {
          x: laneX[i],
          y: y,
          h: h,
          compact: compact,
          r: compact ? ORB_R_COMPACT : ORB_R,
          cx: laneX[i] + (compact ? COMPACT_CX : BW / 2),
          cy: y + (compact ? h / 2 : ORB_R)
        };
      });
    });
  }

  // Where an edge meets an orb: on the circle one ring gap outside it, at height dy from its centre.
  // ponytail: the server caps a pair at 12 strands (_FIBRE_CAP), so |dy| stays under 10, inside every orb. More would leave the circle.
  function reach(p, dy) {
    const R = p.r + RING_GAP;
    return Math.round(Math.sqrt(Math.max(R * R - dy * dy, 0)) * 100) / 100;
  }

  function wirePath(a, b, k, n) {
    const A = pos[a];
    const B = pos[b];
    const flip = A.cx > B.cx;
    const spread = k - (n - 1) / 2;
    const dy1 = spread * 1.8;
    const dy2 = spread * 1.3;
    const y1 = A.cy + dy1;
    const y2 = B.cy + dy2;
    const x1 = flip ? A.cx - reach(A, dy1) : A.cx + reach(A, dy1);
    const x2 = flip ? B.cx + reach(B, dy2) : B.cx - reach(B, dy2);
    const bow = Math.max(30, Math.abs(x2 - x1) / 2.1) * (flip ? -1 : 1);
    return "M" + x1 + "," + y1 + " C" + (x1 + bow) + "," + (y1 + spread * 4) + " " +
      (x2 - bow) + "," + (y2 - spread * 3) + " " + x2 + "," + y2;
  }

  function arcPath(a, b, k) {
    const A = pos[a];
    const B = pos[b];
    const down = A.y < B.y;
    const dy = down ? 5 : -5;
    const y1 = A.cy + dy;
    const y2 = B.cy + dy;
    const x = A.cx + reach(A, dy);
    // The arc bows out past the slot edge, so it clears the name and kind line under the orb.
    const bulge = Math.min(22 + Math.abs(y2 - y1) * 0.3, 82) + (k || 0) * 3 + (down ? 0 : 6) + (A.x + BW - x);
    return "M" + x + "," + y1 + " C" + (x + bulge) + "," + y1 + " " + (x + bulge) + "," + y2 + " " + x + "," + y2;
  }

  function linkPath(a, b) {
    const graph = current && current.graph;
    if (graph && sameLane(graph, a, b)) return arcPath(a, b, 0);
    return wirePath(a, b, 0, 1);
  }

  // The red wire belongs to the owner of the newest failed job (the collector lists them in file-name order,
  // so the last item), not to whoever spoke to the queue factory last. Stable while the failed set is unchanged.
  function brokenPartner() {
    const bus = current && current.bus;
    const items = bus && bus.queue && Array.isArray(bus.queue.failed_items) ? bus.queue.failed_items : [];
    const last = items.length ? items[items.length - 1] : null;
    const owner = last && typeof last.owner === "string" ? last.owner : "";
    if (owner && owner !== "factory-queue" && hasPos(owner)) return owner;
    return hasPos("vp") ? "vp" : null;
  }

  function drawLanes(graph) {
    (graph.lanes || []).forEach(function (lane, i) {
      if (missing(laneX[i])) return;
      const g = svgEl("g", { "class": "lane" }, svg);
      svgEl("rect", { x: laneX[i] - 8, y: 8, width: BW + 16, height: H - 16 }, g);
      const text = svgEl("text", { x: laneX[i], y: 30 }, g);
      text.textContent = (lane.label || "") + " \u00b7 " + laneNodes(lane).length;
    });
  }

  function drawFibres(graph, parent) {
    (graph.fibres || []).forEach(function (fibre) {
      if (!fibre || !fibre.from || !fibre.to) return;
      const count = num(fibre.count);
      if (!count || !hasPos(fibre.from) || !hasPos(fibre.to)) return;
      if (sameLane(graph, fibre.from, fibre.to)) {
        bbArcs.push({ from: fibre.from, to: fibre.to, count: count });
        return;
      }
      let k;
      for (k = 0; k < count; k++) {
        wires.push({
          from: fibre.from,
          to: fibre.to,
          path: svgEl("path", { d: wirePath(fibre.from, fibre.to, k, count), "class": "wire" }, parent)
        });
      }
    });
    const jobq = nodeById(graph.nodes, "factory-queue");
    if (jobq && jobq.state === "failed") {
      const partner = brokenPartner();
      if (partner && hasPos("factory-queue")) {
        wires.push({
          from: "factory-queue",
          to: partner,
          path: svgEl("path", {
            d: wirePath("factory-queue", partner, 0, 1),
            "class": "wire broken",
            "data-from": "factory-queue",
            "data-to": partner
          }, parent)
        });
      }
    }
    pipeNodes().forEach(function (node) {
      if (node === "vp" || !hasPos("vp")) return;
      if (node === "factory-queue" && jobq && jobq.state === "failed" && brokenPartner() === "vp") return;
      wires.push({
        from: node,
        to: "vp",
        path: svgEl("path", {
          d: wirePath(node, "vp", 0, 1),
          "class": "wire broken",
          "data-from": node,
          "data-to": "vp"
        }, parent)
      });
    });
    drawHits(graph, parent);
  }

  function edgeCaption(fromLabel, toLabel, messages, mentions, lastMs) {
    const m = num(messages);
    const n = num(mentions);
    const msg = m === 1 ? "1 message" : m + " messages";
    let text = fromLabel + " \u2192 " + toLabel + " \u00b7 " + msg;
    text += ", " + (n === 1 ? "1 mention" : n + " mentions");  // "0 mentions" too: readers see it was counted
    text += " in 24 h";
    if (typeof lastMs === "number" && isFinite(lastMs)) text += " \u00b7 last at " + hhmm(new Date(lastMs));
    return text;
  }

  function drawHits(graph, parent) {
    const seen = {};
    let fresh = null;
    (graph.fibres || []).forEach(function (fibre) {
      if (!fibre || !fibre.from || !fibre.to || fibre.from === fibre.to) return;
      if (!num(fibre.count) || !hasPos(fibre.from) || !hasPos(fibre.to)) return;
      if (sameLane(graph, fibre.from, fibre.to)) return;  // same-lane strands are hidden, so no hit target
      const key = fibre.from + "|" + fibre.to;
      if (seen[key]) return;
      seen[key] = true;
      const caption = edgeCaption(
        labelOf(fibre.from), labelOf(fibre.to),
        fibre.total_messages, fibre.total_mentions, Date.parse(fibre.last)
      );
      const path = svgEl("path", {
        d: linkPath(fibre.from, fibre.to),
        "class": "edge-hit",
        "data-from": fibre.from,
        "data-to": fibre.to
      }, parent);
      svgEl("title", {}, path).textContent = caption;
      if (key === edgeKey) fresh = caption;
      path.addEventListener("click", function (ev) {
        ev.stopPropagation();
        edgeKey = key;
        const info = document.getElementById("edge-info");
        if (info) info.textContent = caption;
      });
    });
    // Never show stale counts: refresh the caption, or drop it when its pair is gone.
    if (fresh === null) edgeKey = null;
    const info = document.getElementById("edge-info");
    if (info) info.textContent = fresh || "";
  }

  // The name to draw beside a compact orb: the label if long(label) (its width in units) fits in COMPACT_NAME_MAX, else the longest
  // start of it that fits with an ellipsis, found by halving (a width only grows with the length). The full name stays in the title
  // and the aria-label. long() includes the tail ("" when there is no mark).
  function clipName(label, long) {
    const cap = COMPACT_NAME_MAX;
    if (long(label) <= cap) return label;
    let fit = 1;
    let over = label.length;
    while (fit + 1 < over) {
      const mid = Math.floor((fit + over) / 2);
      if (long(label.slice(0, mid).trimEnd() + "\u2026") <= cap) fit = mid;
      else over = mid;
    }
    return label.slice(0, fit).trimEnd() + "\u2026";
  }

  function drawNodes(graph) {
    nodeEls = {};
    (graph.lanes || []).forEach(function (lane) {
      laneNodes(lane).forEach(function (id) {
        const node = nodeById(graph.nodes, id);
        if (!node || !hasPos(id)) return;
        const p = pos[id];
        const compact = p.compact;
        const state = node.state || "idle";
        const mark = glyph(state);
        const label = node.label ? String(node.label) : "";
        const word = node.word ? String(node.word) : "";
        const working = node.active === true;
        const rings = orbRings(p.r, state, working);
        const g = svgEl("g", {
          "class": working ? "node " + state + " chat-on" : "node " + state,
          "data-id": id,
          role: "button",
          tabindex: "0",
          "aria-label": label + ", " + word + (working ? ", working now" : ""),
          focusable: "true"
        }, svg);
        // The hit area is the whole slot, so a click on the name works as well as one on the orb.
        svgEl("rect", { "class": "hit", x: p.x, y: p.y, width: BW, height: p.h }, g);
        // A compact name sits on a plate in the lane's own tone, below the rings, from the orb's ring gap to just past the name. A
        // strand behind the name is hidden cleanly (no stub and no "=" between orb and name) and shows again where the name ends.
        // Sized once the name is measured.
        const plateX = p.cx + p.r + RING_GAP;
        const nameX = p.x + COMPACT_NAME_X;
        const plate = compact ? svgEl("rect", { "class": "plate", x: plateX, y: p.cy - 11, width: 0, height: 22, rx: 3 }, g) : null;
        // The pick ring must never touch text, so it is capped at 27 (1px past the waits ring). A working node has none: its
        // working ring takes the pick's look instead (see .chat-on in the CSS).
        if (!working) svgEl("circle", { "class": "pick", cx: p.cx, cy: p.cy, r: Math.min(rings.outer + 3, 27) }, g);
        // The ping stays clear of the text: 29 on a large orb, 22 on a compact one (1px edge 1.5 short of the name).
        svgEl("circle", { "class": "ping", cx: p.cx, cy: p.cy, r: Math.min(rings.outer + 5, compact ? COMPACT_NAME_X - COMPACT_CX - 2 : 29) }, g);
        const glow = drawRings(g, p.cx, p.cy, rings);
        // The working ring breathes on a phase seeded from the id, so the roster never pulses in step. CSSOM write, so CSP allows it.
        if (glow) glow.style.animationDelay = animationDelay(id, p.r * 2, "working") + "s";
        drawOrb(g, "g", id, p.r * 2, { x: p.cx - p.r, y: p.cy - p.r });
        if (compact) {
          // ponytail: a strand that enters a compact orb from the right passes behind the name (the plate hides it).
          // Upgrade: put the name on the side away from most edges.
          const text = svgEl("text", { "class": "name", x: nameX, y: p.cy + 5 }, g);
          const tail = mark ? " " + mark : "";
          // The width of a name and the glyph, measured in the text element itself (so in its font), once per string.
          let tried = "";
          let width = 0;
          const long = function (name) {
            if (name === tried) return width;
            tried = name;
            text.textContent = name + tail;
            const len = typeof text.getComputedTextLength === "function" ? text.getComputedTextLength() : 0;
            width = len || (name + tail).length * COMPACT_CHAR_W;
            return width;
          };
          const shown = clipName(label, long);
          const len = long(shown);
          text.textContent = shown;
          if (mark) svgEl("tspan", { "class": "glyph" }, text).textContent = tail;
          plate.setAttribute("width", nameX + Math.ceil(len) + 4 - plateX);
        } else {
          svgEl("text", { "class": "name", x: p.cx, y: p.y + NAME_DY, "text-anchor": "middle" }, g).textContent = label;
          // One mono line: the kind, the badge text after a dot (red stays red), then the state mark. No state is colour-only.
          const line = svgEl("text", { "class": "kind", x: p.cx, y: p.y + KIND_DY, "text-anchor": "middle" }, g);
          svgEl("tspan", {}, line).textContent = kindText(node);
          if (node.badge && node.badge.text) {
            const badge = svgEl("tspan", { "class": node.badge.tone === "red" ? "badge red" : "badge" }, line);
            badge.textContent = " \u00b7 " + String(node.badge.text);
          }
          if (mark) svgEl("tspan", { "class": "glyph" }, line).textContent = " " + mark;
        }
        const headline = node.headline ? String(node.headline) : "";
        const said = headline ? label + ": " + headline : label;
        // A compact node has no kind line or badge text, so the tooltip carries both.
        const extra = compact && node.badge && node.badge.text ? " \u00b7 " + String(node.badge.text) : "";
        const tip = compact ? kindText(node) + " \u00b7 " + said + extra : said;
        svgEl("title", {}, g).textContent = working ? tip + " (working now)" : tip;
        g.addEventListener("click", function () { select(id); });
        g.addEventListener("keydown", function (ev) {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            select(id);
          }
        });
        nodeEls[id] = g;
      });
    });
  }

  // A selected part with no orb opens the panel but dims nothing.
  function focused() {
    return sel && nodeEls[sel] ? sel : null;
  }

  function unrelated(from, to) {
    const f = focused();
    return !!(f && f !== from && f !== to);
  }

  function layers() {
    return [liveG, replayG, ambG].filter(function (g) { return !!g; });
  }

  function dimLive() {
    layers().forEach(function (g) {
      const nodes = g.querySelectorAll("[data-from]");
      let i;
      for (i = 0; i < nodes.length; i++) {
        const node = nodes[i];
        node.classList.toggle("dim", unrelated(node.getAttribute("data-from"), node.getAttribute("data-to")));
      }
    });
  }

  function applyFocus() {
    if (!bbG) return;
    const f = focused();
    svg.classList.toggle("focus", !!f);
    const near = {};
    if (f) near[f] = true;
    wires.forEach(function (wire) {
      const lit = !!(f && (wire.from === f || wire.to === f));
      wire.path.classList.toggle("lit", lit);
      if (lit) {
        near[wire.from] = true;
        near[wire.to] = true;
      }
    });
    clear(bbG);
    const nodes = current && current.graph && current.graph.nodes;
    const selected = nodeById(nodes, f);
    if (selected && selected.kind === "PRODUCT") {
      bbArcs.forEach(function (arc) {
        if (arc.from !== f && arc.to !== f) return;
        near[arc.from] = true;
        near[arc.to] = true;
        let k;
        for (k = 0; k < arc.count; k++) {
          svgEl("path", { d: arcPath(arc.from, arc.to, k), "class": "wire bb" }, bbG);
        }
      });
    }
    let id;
    for (id in nodeEls) {
      if (!Object.prototype.hasOwnProperty.call(nodeEls, id)) continue;
      nodeEls[id].classList.toggle("near", !!near[id]);
      nodeEls[id].classList.toggle("sel", id === f);
      nodeEls[id].setAttribute("aria-pressed", id === f ? "true" : "false");
    }
    dimLive();
  }

  function retargetLive() {
    [liveG, replayG].forEach(function (g) {
      if (!g) return;
      const paths = g.querySelectorAll("path.talk");
      let i;
      for (i = 0; i < paths.length; i++) {
        const path = paths[i];
        const from = path.getAttribute("data-from");
        const to = path.getAttribute("data-to");
        if (!hasPos(from) || !hasPos(to)) {
          finish(path);
          continue;
        }
        path.setAttribute("d", linkPath(from, to));
      }
      const trails = g.querySelectorAll("path.trail");
      for (i = 0; i < trails.length; i++) {
        const t = trails[i];
        const from = t.getAttribute("data-from");
        const to = t.getAttribute("data-to");
        if (hasPos(from) && hasPos(to)) t.setAttribute("d", linkPath(from, to));
      }
    });
    ambDots.slice().forEach(function (d) {
      if (!hasPos(d.from) || !hasPos(d.to)) {
        dropAmb(d, true);
        return;
      }
      const dd = linkPath(d.from, d.to);
      d.path.setAttribute("d", dd);
      d.trails.forEach(function (t) { t.setAttribute("d", dd); });
    });
  }

  function draw(graph) {
    const active = document.activeElement;
    const activeId = active && active.namespaceURI === NS ? active.getAttribute("data-id") : null;
    drawing = true;
    try {
      const saved = liveG;
      const savedAmb = ambG;
      const savedReplay = replayG;
      [saved, savedAmb, savedReplay].forEach(function (g) {
        if (g && g.parentNode === svg) svg.removeChild(g);
      });
      clear(svg);
      wires = [];
      bbArcs = [];
      layout(graph);
      drawLanes(graph);
      const layer = svgEl("g", {}, svg);
      drawFibres(graph, layer);
      ambG = savedAmb || svgEl("g", { "class": "amb" });
      svg.appendChild(ambG);
      bbG = svgEl("g", {}, svg);
      drawNodes(graph);
      replayG = savedReplay || svgEl("g", { "class": "replay-layer" });
      svg.appendChild(replayG);
      liveG = saved || svgEl("g", { "class": "live" });
      svg.appendChild(liveG);
      retargetLive();
      applyFocus();
      syncAmbient(graph);
    } finally {
      drawing = false;
    }
    // A poll redraw keeps the user's focus on the orb but never scrolls the page to it.
    if (activeId && nodeEls[activeId]) nodeEls[activeId].focus({ preventScroll: true });
    pump();
  }

  function askLines(id) {
    if (id === "owner") {
      return waiting(current.decisions).map(function (item) {
        const said = phraseOf(item);
        const age = ageOf(item.ts, nowMs);
        return { text: age ? said + " (" + age + ")" : said };
      });
    }
    if (id === "hq" || id.indexOf("hq-") === 0) {
      const open = (current.decisions || []).filter(function (item) {
        return item && item.kind === "hq" && Array.isArray(item.for) && item.for.indexOf(id) >= 0;
      }).length;
      return open ? [{ text: open + " open" }] : [];
    }
    return [];
  }

  function jobLines(id) {
    if (id !== "factory-queue") return [];
    const jobq = (current.bus && current.bus.queue) || {};
    const lines = [];
    const running = num(jobq.running);
    const inbox = num(jobq.inbox);
    if (running || inbox) lines.push({ text: running + " running, " + inbox + " inbox" });
    (jobq.failed_items || []).forEach(function (item) {
      if (!item) return;
      const title = item.title ? String(item.title) : "";
      const ident = item.id ? String(item.id) : "";
      const name = title || ident;
      if (!name) return;
      lines.push({ text: "\u25b2 failed: " + name, mono: !title });
    });
    return lines;
  }

  function messageLines(id) {
    const rows = nodeLog[id] || [];
    return rows.map(function (record) {
      return { text: lineWords(record) };
    });
  }

  function focusInspector() {
    const head = document.getElementById("inspector-h");
    if (head && head.focus) head.focus({ preventScroll: true });
  }

  function restoreInspectorFocus(id) {
    const orb = id && nodeEls[id];
    const hidden = orb && orb.closest && orb.closest("[hidden]");
    if (orb && orb.focus && !hidden) {
      orb.focus({ preventScroll: true });
      return;
    }
    const jump = document.getElementById("jump");
    if (jump && jump.focus) jump.focus({ preventScroll: true });
  }

  // A needs-you row for this agent: a decision names it, an alert's source is its name, a problem's subject is its id.
  function askForAgent(row, id) {
    if (!row) return false;
    if (row.kind === "decision") return row.id === id || row.from === id;
    if (row.kind === "alert") return row.from === id || row.from === labelOf(id);
    if (row.kind === "problem" && typeof row.id === "string") {
      const cut = row.id.indexOf(":");
      return cut >= 0 && row.id.slice(cut + 1) === id;
    }
    return false;
  }

  function panel() {
    const root = document.getElementById("inspector");
    if (!root) return;
    const had = document.activeElement && root.contains(document.activeElement);
    clear(root);
    const id = sel && current && current.graph && nodeById(current.graph.nodes, sel) ? sel : "";
    const node = id ? nodeById(current.graph.nodes, id) : null;
    if (!node) {
      root.hidden = true;
      return;
    }
    const head = el("div", "inspector-head");
    const orb = el("div", "inspector-orb");
    drawOrb(orb, "d", id, 40);
    head.appendChild(orb);
    const who = el("div", "inspector-who");
    const mark = glyph(node.state);
    const toneName = tone(node.state);
    if (mark) who.appendChild(el("span", toneName ? "state " + toneName : "state", mark));
    const title = el("h2", "inspector-name", node.label ? String(node.label) : labelOf(id));
    title.id = "inspector-h";
    title.tabIndex = -1;
    who.appendChild(title);
    who.appendChild(el("p", "inspector-kind", kindText(node)));
    head.appendChild(who);
    const close = el("button", "inspector-close", "\u00d7");
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.addEventListener("click", function () { if (sel) select(sel); });
    head.appendChild(close);
    root.appendChild(head);
    let ask = null;
    if (typeof AtlasStatus !== "undefined") {
      const rows = AtlasStatus.needsYou(current, problemItems, alertPayload);
      let i;
      for (i = 0; i < rows.length; i++) {
        if (askForAgent(rows[i], id)) { ask = rows[i]; break; }
      }
    }
    const asked = askLines(id);
    if (ask) {
      const block = el("div", "inspector-ask");
      block.appendChild(el("p", "inspector-ask-k", "It asked you"));
      const askText = ask.title || (asked[0] && asked[0].text) || "";
      if (askText) block.appendChild(el("p", "", askText));
      block.appendChild(needsActions(ask));
      root.appendChild(block);
    }
    const prs = [];
    const github = current.github && current.github.prs;
    if (Array.isArray(github)) {
      github.forEach(function (pr) {
        if (!pr || pr.node !== id) return;
        let text = "#" + String(pr.number) + (pr.title ? " " + String(pr.title) : "");
        if (pr.checks === "red") text += " \u00b7 CI red";
        prs.push(text);
      });
    }
    let spent = "";
    const pools = budgetPayload && Array.isArray(budgetPayload.pools) ? budgetPayload.pools : [];
    const label = labelOf(id).toLowerCase();
    const nid = String(id).toLowerCase();
    let p;
    for (p = 0; p < pools.length && !spent; p++) {
      const pool = pools[p];
      if (!pool) continue;
      const name = String(pool.name || "").toLowerCase();
      const pid = String(pool.id || "").toLowerCase();
      if ((name && (name === label || name === nid)) || (pid && pid === nid)) spent = budgetSentence(pool);
    }
    const jobs = jobLines(id).map(function (line) { return line && line.text; }).filter(Boolean).join("\n");
    const facts = [
      ["Doing", node.headline ? String(node.headline) : ""],
      ["Last seen", ageOf(node.ts, nowMs)],
      ["Open PRs", prs.join("\n")],
      ["Queue", jobs],
      ["Spend today", spent]
    ];
    const dl = el("dl");
    let any = false;
    facts.forEach(function (pair) {
      if (!pair[1]) return;
      any = true;
      dl.appendChild(el("dt", "", pair[0]));
      dl.appendChild(el("dd", pair[0] === "Spend today" ? "inspector-spend" : "", pair[1]));
    });
    if (any) root.appendChild(dl);
    const lines = messageLines(id).slice(0, 4);
    // Design: say it once instead of an empty drawer.
    if (!ask && !any && !lines.length) root.appendChild(el("p", "inspector-empty", "Nothing waiting. It hasn't reported anything yet."));
    if (lines.length) {
      const recent = el("div", "inspector-recent");
      recent.appendChild(el("h3", "", "Recent"));
      const ol = el("ol");
      const records = (nodeLog[id] || []).slice(0, 4);
      lines.forEach(function (line, i) {
        const li = el("li");
        const when = records[i] && records[i].when;
        if (when) li.appendChild(el("span", "inspector-time", hhmm(when)));
        let text = line.text || "";
        const lead = when ? hms(when) + " \u00b7 " : "";
        if (lead && text.indexOf(lead) === 0) text = text.slice(lead.length);
        li.appendChild(el("span", "", text));
        ol.appendChild(li);
      });
      recent.appendChild(ol);
      root.appendChild(recent);
    }
    root.hidden = false;
    if (had) focusInspector();
  }

  function markTable() {
    const buttons = document.querySelectorAll("#tbl tbody button");
    let i;
    for (i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", buttons[i].getAttribute("data-id") === sel ? "true" : "false");
    }
  }

  function table(graph) {
    const body = document.querySelector("#tbl tbody");
    const active = document.activeElement;
    const kept = body && active && body.contains(active) && active.tagName === "BUTTON" ? active.getAttribute("data-id") : "";
    clear(body);
    (graph.lanes || []).forEach(function (lane) {
      (lane.nodes || []).forEach(function (id) {
        const node = nodeById(graph.nodes, id);
        if (!node) return;
        const tr = el("tr");
        tr.id = "part-" + id;
        const btn = el("button", "", node.label ? String(node.label) : "");
        btn.type = "button";
        btn.setAttribute("data-id", id);
        btn.setAttribute("aria-pressed", id === sel ? "true" : "false");
        const name = el("td");
        const cell = el("div", "part");
        drawOrb(cell, "t", id, 24);
        cell.appendChild(btn);
        name.appendChild(cell);
        tr.appendChild(name);
        const mark = glyph(node.state);
        const word = node.word ? String(node.word) : "";
        const toneName = tone(node.state);
        tr.appendChild(el("td", toneName, mark ? mark + " " + word : word));
        tr.appendChild(el("td", "hl", node.headline ? String(node.headline) : ""));
        tr.appendChild(el("td", "age", ageOf(node.ts, nowMs)));
        tr.addEventListener("click", function () { select(id); });
        body.appendChild(tr);
      });
    });
    if (kept) {  // a poll rebuild keeps the focused row and never scrolls
      const buttons = body.querySelectorAll("button[data-id]");
      for (let i = 0; i < buttons.length; i++) {
        if (buttons[i].getAttribute("data-id") === kept) { buttons[i].focus({ preventScroll: true }); break; }
      }
    }
  }

  function select(id) {
    const prev = sel;
    sel = sel === id ? null : id;
    // Follow a selection, never a deselection (Escape or a second tap leaves the focus where it is).
    if (narrow && sel === id && current && current.graph && nodeById(current.graph.nodes, id)) {
      focusPending = null;
      clearTimeout(focusTimer);
      const stage = document.getElementById("focus-stage");
      if (stage) stage.classList.remove("out");
      if (focusId !== id) clearRailFlights();
      focusId = id;
      renderFocus(current.graph);
    }
    applyFocus();
    panel();
    markTable();
    if (sel) focusInspector();
    else if (prev) restoreInspectorFocus(prev);
  }

  function showOffline() {
    let strip = document.getElementById("offline");
    if (!strip) {
      strip = el("p", "row row-stale");
      strip.id = "offline";
      const lede = document.getElementById("lede");
      lede.parentNode.insertBefore(strip, lede.nextSibling);
    }
    clear(strip);
    strip.appendChild(el("span", "mark mark-stale", "\u25b2"));
    strip.appendChild(document.createTextNode(" Offline \u00b7 showing the state from " + (updatedLabel || "--:--")));
    pollDown = true;
    syncOffline();
  }

  function hideOffline() {
    const strip = document.getElementById("offline");
    if (strip) strip.remove();
    pollDown = false;
    syncOffline();
  }

  function refreshProv() {
    if (!current || !current.graph) return;
    document.getElementById("prov").textContent = provText(current.graph);
  }

  function refreshLegend() {
    const legend = document.getElementById("motion-legend");
    if (!legend) return;
    legend.hidden = !!reduceMotion;
  }

  // Newest real activity the server knows of: recent[].ts and fibres[].last, clamped to now (clock skew).
  function newestReal(graph) {
    let newest = 0;
    (Array.isArray(graph.recent) ? graph.recent : []).forEach(function (item) {
      const t = item ? timeOf(item) : null;
      if (t !== null && t > newest) newest = t;
    });
    (graph.fibres || []).forEach(function (f) {
      const t = f ? Date.parse(f.last) : NaN;
      if (!isNaN(t) && t > newest) newest = t;
    });
    return Math.min(newest, Date.now());
  }

  // ---- focused view (narrow screens): one part, its partners, dots on the left rail ----

  function knownNodes(from, to) {
    const nodes = current && current.graph && current.graph.nodes;
    return !!(nodeById(nodes, from) && nodeById(nodes, to));
  }

  // prefer null falls through to the owner, then the first real node.
  function resolveFocus(graph, prefer) {
    const nodes = graph && graph.nodes;
    if (prefer && nodeById(nodes, prefer)) return prefer;
    if (nodeById(nodes, "owner")) return "owner";
    const lanes = (graph && graph.lanes) || [];
    let i, j;
    for (i = 0; i < lanes.length; i++) {
      const list = lanes[i].nodes || [];
      for (j = 0; j < list.length; j++) {
        if (nodeById(nodes, list[j])) return list[j];
      }
    }
    return "";
  }

  function stageFocus(graph) {
    return resolveFocus(graph, focusId);
  }

  function touchesFocus(from, to) {
    const graph = current && current.graph;
    const id = graph ? stageFocus(graph) : "";
    if (!id || (from !== id && to !== id)) return false;
    // No partner row yet (a first-time pair): no flight, it would only blink at the top of the rail.
    return !!document.getElementById("partner-" + (from === id ? to : from));
  }

  // Spec §8 (v1.4): "12 messages, 3 mentions"; a zero part is left out; singular for 1.
  function rowCount(messages, mentions) {
    const m = num(messages);
    const n = num(mentions);
    const parts = [];
    if (m > 0) parts.push(m === 1 ? "1 message" : m + " messages");
    if (n > 0) parts.push(n === 1 ? "1 mention" : n + " mentions");
    return parts.join(", ");
  }

  function partnersOf(graph, id) {
    const linked = {};
    (graph.fibres || []).forEach(function (fibre) {
      if (!fibre || !fibre.from || !fibre.to) return;
      let other = "";
      if (fibre.from === id) other = fibre.to;
      else if (fibre.to === id) other = fibre.from;
      if (!other || other === id) return;
      if (!linked[other]) linked[other] = { count: 0, messages: 0, mentions: 0 };
      linked[other].count += num(fibre.count);
      // Real 24 h totals, both directions, the same numbers the desktop hover shows.
      linked[other].messages += num(fibre.total_messages);
      linked[other].mentions += num(fibre.total_mentions);
    });
    const rows = [];
    const seen = {};
    (graph.lanes || []).forEach(function (lane) {
      (lane.nodes || []).forEach(function (nid) {
        if (!linked[nid] || seen[nid]) return;
        seen[nid] = true;
        rows.push({ id: nid, count: linked[nid].count, messages: linked[nid].messages, mentions: linked[nid].mentions });
      });
    });
    Object.keys(linked).forEach(function (nid) {
      if (seen[nid]) return;
      rows.push({ id: nid, count: linked[nid].count, messages: linked[nid].messages, mentions: linked[nid].mentions });
    });
    return rows;
  }

  function piece(parent, cls, text) {
    if (!text) return;
    if (parent.firstChild) parent.appendChild(document.createTextNode(" \u00b7 "));
    parent.appendChild(el("span", cls, text));
  }

  function fillShow(graph, picked) {
    const menu = document.getElementById("show");
    if (!menu) return;
    const want = [];
    (graph.lanes || []).forEach(function (lane) {
      const ids = (lane.nodes || []).filter(function (nid) { return !!nodeById(graph.nodes, nid); });
      if (!ids.length) return;
      want.push({ label: lane.label || "", ids: ids });
    });
    const stamp = want.map(function (group) {
      return group.label + "=" + group.ids.map(function (nid) {
        const node = nodeById(graph.nodes, nid);
        return nid + ":" + (node && node.label ? String(node.label) : "");
      }).join(",");
    }).join("|");
    if (menu.getAttribute("data-stamp") !== stamp) {
      clear(menu);
      want.forEach(function (group) {
        const optgroup = el("optgroup");
        optgroup.setAttribute("label", group.label);
        group.ids.forEach(function (nid) {
          const node = nodeById(graph.nodes, nid);
          const opt = el("option", "", node && node.label ? String(node.label) : nid);
          opt.value = nid;
          optgroup.appendChild(opt);
        });
        menu.appendChild(optgroup);
      });
      menu.setAttribute("data-stamp", stamp);
    }
    if (picked && menu.value !== picked) menu.value = picked;
  }

  function drawBox(graph, id) {
    const host = document.getElementById("focus-box");
    const node = nodeById(graph.nodes, id);
    if (!host) return;
    clear(host);
    if (!node) return;
    const state = node.state || "idle";
    host.className = "focus-box " + state;
    host.setAttribute("data-id", id);
    drawOrbCell(host, "f", id, node);
    host.appendChild(el("span", "fv-kind", kindText(node)));
    if (node.badge && node.badge.text) {
      host.appendChild(el("span", node.badge.tone === "red" ? "fv-badge red" : "fv-badge", String(node.badge.text)));
    }
    const mark = glyph(state);
    if (mark) host.appendChild(el("span", "fv-glyph", mark));
    host.appendChild(el("span", "fv-name", node.label ? String(node.label) : ""));
  }

  function drawPartners(graph, id, keepFocus) {
    const host = document.getElementById("focus-partners");
    if (!host) return;
    const active = document.activeElement;
    const keep = keepFocus && active && active.id && active.id.indexOf("partner-") === 0 ? active.id : "";
    clear(host);
    partnersOf(graph, id).forEach(function (row) {
      const node = nodeById(graph.nodes, row.id);
      if (!node) return;
      const state = node.state || "idle";
      const label = node.label ? String(node.label) : row.id;
      const word = node.word ? String(node.word) : "";
      const countText = rowCount(row.messages, row.mentions);
      const bits = [];
      if (label) bits.push(label);
      if (word) bits.push(word);
      if (node.active === true) bits.push("working now");
      if (countText) bits.push(countText);
      const btn = el("button", "focus-partner " + state);
      btn.type = "button";
      btn.id = "partner-" + row.id;
      btn.setAttribute("data-count", String(num(row.count)));
      btn.setAttribute("aria-label", bits.join(", "));
      drawOrbCell(btn, "p", row.id, node);
      const main = el("span", "fv-main");
      piece(main, "fv-kind", kindText(node));
      piece(main, "fv-name", label);
      if (node.badge && node.badge.text) {
        piece(main, node.badge.tone === "red" ? "fv-badge red" : "fv-badge", String(node.badge.text));
      }
      const mark = glyph(state);
      if (mark) piece(main, "fv-glyph", mark);
      btn.appendChild(main);
      btn.appendChild(el("span", "fv-msgs", countText));
      btn.addEventListener("click", function () { chooseFocus(row.id); });
      host.appendChild(btn);
    });
    layoutRail();
    if (keep) {
      const again = document.getElementById(keep);
      if (again) again.focus({ preventScroll: true });  // a poll rebuild keeps the user's row, never scrolls
    }
  }

  function railD(from, to) {
    const graph = current && current.graph;
    const focus = graph ? stageFocus(graph) : "";
    const other = from === focus ? to : from;
    const row = document.getElementById("partner-" + other);
    const rail = document.getElementById("focus-rail");
    let y = 0;
    if (row && rail) {
      const a = row.getBoundingClientRect();
      y = a.top - rail.getBoundingClientRect().top + a.height / 2;
    }
    y = Math.max(y, 2);
    const y0 = from === focus ? 2 : y;
    const y1 = from === focus ? y : 2;
    return "M8," + y0.toFixed(1) + " L8," + y1.toFixed(1);
  }

  function retargetRail() {
    if (!railG) return;
    const paths = railG.querySelectorAll("path");
    let i;
    for (i = 0; i < paths.length; i++) {
      const path = paths[i];
      const from = path.getAttribute("data-from");
      const to = path.getAttribute("data-to");
      if (from && to) path.setAttribute("d", railD(from, to));
    }
  }

  function layoutRail() {
    const rail = document.getElementById("focus-rail");
    const host = document.getElementById("focus-partners");
    if (!rail || !host) return;
    const saved = railG && railG.parentNode === rail ? railG : null;
    if (saved) rail.removeChild(saved);
    clear(rail);
    const buttons = host.querySelectorAll("button.focus-partner");
    const top = rail.getBoundingClientRect().top;
    const h = Math.max(host.offsetHeight, 1);
    rail.setAttribute("viewBox", "0 0 16 " + h);
    rail.setAttribute("width", "16");
    rail.setAttribute("height", String(h));
    let i, k, n, x, y, rect;
    for (i = 0; i < buttons.length; i++) {
      n = Math.max(1, Math.min(4, Number(buttons[i].getAttribute("data-count")) || 1));
      rect = buttons[i].getBoundingClientRect();
      y = rect.top - top + rect.height / 2;
      for (k = 0; k < n; k++) {
        x = 8 + (k - (n - 1) / 2) * 3;
        svgEl("path", {
          d: "M" + x.toFixed(1) + ",0 L" + x.toFixed(1) + "," + y.toFixed(1),
          "class": "wire"
        }, rail);
      }
    }
    if (!railG) railG = svgEl("g", { "class": "live" });
    rail.appendChild(railG);
    retargetRail();
  }

  // Focus id plus each partner row's id, count, badge and the text the row renders.
  function focusStamp(graph, id) {
    const bit = function (node, row) {
      const badge = node && node.badge && node.badge.text ? String(node.badge.text) : "";
      const toneName = node && node.badge && node.badge.tone ? String(node.badge.tone) : "";
      return [
        node && node.label ? String(node.label) : "",
        node && node.word ? String(node.word) : "",
        badge,
        toneName,
        node && node.state ? String(node.state) : "",
        node && node.active === true ? "active" : "",
        node ? kindText(node) : "",
        row ? rowCount(row.messages, row.mentions) : ""
      ].join("\t");
    };
    const parts = [id, bit(nodeById(graph.nodes, id), null)];
    partnersOf(graph, id).forEach(function (row) {
      parts.push(row.id + "\t" + num(row.count) + "\t" + bit(nodeById(graph.nodes, row.id), row));
    });
    return parts.join("\n");
  }

  function renderFocus(graph, keepFocus) {
    if (!narrow || !graph) return;
    const stageId = stageFocus(graph);
    fillShow(graph, resolveFocus(graph, focusPending || focusId));
    const stamp = focusStamp(graph, stageId);
    if (stamp === focusDrawn) return;
    focusDrawn = stamp;
    drawBox(graph, stageId);
    drawPartners(graph, stageId, keepFocus);
  }

  function clearRailFlights() {
    if (!railG) return;
    const paths = railG.querySelectorAll("path.talk");
    let i;
    for (i = 0; i < paths.length; i++) paths[i].setAttribute("data-done", "1");
    clear(railG);
    inFlight = 0;
  }

  function commitFocus(id) {
    focusPending = null;
    focusId = id;
    clearTimeout(focusTimer);
    clearRailFlights();
    if (current && current.graph && nodeById(current.graph.nodes, id) && sel !== id) {
      sel = id;
      applyFocus();
      panel();
      markTable();
      focusInspector();
    }
    const stage = document.getElementById("focus-stage");
    if (current && current.graph) renderFocus(current.graph, true);
    if (stage) stage.classList.remove("out");
    pump();
  }

  function chooseFocus(id) {
    const graph = current && current.graph;
    if (!graph || !nodeById(graph.nodes, id)) return;
    if (id === stageFocus(graph) && !focusPending) return;
    focusPending = id;
    const menu = document.getElementById("show");
    if (menu) menu.value = id;
    const stage = document.getElementById("focus-stage");
    if (reduceMotion || !stage) {
      commitFocus(id);
      return;
    }
    stage.classList.add("out");
    clearTimeout(focusTimer);
    focusTimer = setTimeout(function () { commitFocus(id); }, 240);
  }

  // Empty: no agent besides the owner and the lead, and no work lines on the board.
  // ponytail: atlas init always adds a silent Worker. That slot is not an agent until it reports. Any other id is.
  function emptyLab(data) {
    if (!data || !data.graph || !data.graph.nodes) return false;
    const lead = typeof data.lead === "string" ? data.lead : "";
    const nodes = data.graph.nodes;
    const ids = Object.keys(nodes);
    let i;
    for (i = 0; i < ids.length; i++) {
      const id = ids[i];
      if (id === "owner" || (lead && id === lead)) continue;
      const node = nodes[id] || {};
      if (id !== "worker" || node.ts || node.headline || node.active) return false;
    }
    return !data.board_lines;
  }

  // Step 1 is done because this runs only after /api/state has answered (the page is serving).
  function firstSteps(data, alerts) {
    const repos = data && Array.isArray(data.repos) ? data.repos : [];
    const recent = alerts && Array.isArray(alerts.recent) ? alerts.recent : [];
    const open = alerts && Array.isArray(alerts.open) ? alerts.open : [];
    return {
      lab: true,
      hooks: !!(data && data.hook),
      repo: repos.length > 0,
      alert: recent.length > 0 || open.length > 0
    };
  }

  function paintFirst(data) {
    const first = document.getElementById("first-run");
    const stage = document.getElementById("now-stage");
    if (!first || !stage) return;
    const empty = !!(data && emptyLab(data));
    first.hidden = !empty;
    stage.hidden = empty;
    const below = document.querySelector(".below");
    if (below && currentView() === "now") below.hidden = empty;
    if (!empty || !data) return;
    const steps = firstSteps(data, alertPayload);
    const keys = ["lab", "hooks", "repo", "alert"];
    let done = 0;
    let i;
    for (i = 0; i < keys.length; i++) if (steps[keys[i]]) done += 1;
    const eye = document.getElementById("first-eye");
    if (eye) eye.textContent = "GET STARTED · " + done + " OF 4 DONE";
    const items = first.querySelectorAll("[data-step]");
    for (i = 0; i < items.length; i++) {
      const key = items[i].getAttribute("data-step");
      const on = !!steps[key];
      if (on) items[i].classList.add("is-done");
      else items[i].classList.remove("is-done");
      const mark = items[i].querySelector(".first-check");
      const num = items[i].querySelector(".first-num");
      if (mark) mark.hidden = !on;
      if (num) num.hidden = on;
    }
  }

  function copyCommand(text, code, button) {
    function selectAll() {
      if (!code || !document.createRange || !window.getSelection) return;
      const range = document.createRange();
      range.selectNodeContents(code);
      const sel = window.getSelection();
      if (!sel) return;
      sel.removeAllRanges();
      sel.addRange(range);
    }
    const clip = navigator.clipboard;
    if (clip && clip.writeText) {
      clip.writeText(text).then(function () {
        const label = button._copyLabel || button.textContent;
        button._copyLabel = label;
        button.textContent = "Copied";
        setTimeout(function () { button.textContent = label; }, 1500);
      }, selectAll);
      return;
    }
    selectAll();
  }

  function wireCopy() {
    document.addEventListener("click", function (ev) {
      const button = ev.target && ev.target.closest ? ev.target.closest(".first-copy") : null;
      if (!button) return;
      const row = button.parentNode;
      const code = row ? row.querySelector("code") : null;
      copyCommand(code ? code.textContent : "", code, button);
    });
  }

  function render(data) {
    current = data;
    const graph = data.graph || { lanes: [], nodes: {}, fibres: [], messages_24h: 0, mentions_24h: 0 };
    lastRealAt = Math.max(lastRealAt, newestReal(graph));
    if (sel && !nodeById(graph.nodes, sel)) sel = null;
    if (focusId && !nodeById(graph.nodes, focusId)) focusId = null;
    if (focusPending && !nodeById(graph.nodes, focusPending)) focusPending = null;
    paintStatus();
    const jumpEl = document.getElementById("jump");
    if (jumpEl && jumpEl.value) paintJump();
    document.getElementById("prov").textContent = provText(graph);
    const failed = num(data.bus && data.bus.queue && data.bus.queue.failed);
    document.getElementById("lg-broken").hidden = failed <= 0;
    refreshLegend();
    orbPlan = planOrbs(graph);
    draw(graph);
    if (narrow) renderFocus(graph, true);
    table(graph);
    panel();
    if (problemItems) patchProblems(problemItems);
    if (roadmapPayload) renderRoadmap(roadmapPayload);
    if (budgetPayload) renderBudget(budgetPayload);
    if (alertPayload) renderAlerts(alertPayload);
    paintFirst(data);
  }

  let foldersDrawn = "";

  function folderLine(item) {
    const li = el("li", "folder-row");
    li.appendChild(el("span", "folder-path", item && item.path ? String(item.path) : ""));
    li.appendChild(el("span", "folder-meta", item && item.last_active ? String(item.last_active) : "-"));
    const chats = item && typeof item.chats === "number" ? item.chats : 0;
    li.appendChild(el("span", "folder-meta", chats + " chats"));
    if (item && item.start_here) {
      const link = el("a", "folder-start", "START-HERE");
      link.setAttribute("href", String(item.start_here));
      li.appendChild(link);
    }
    return li;
  }

  function ignoreLine(item) {
    const li = el("li", "folder-row");
    li.appendChild(el("span", "folder-path", item && item.path ? String(item.path) : ""));
    li.appendChild(el("span", "folder-meta", item && item.reason ? String(item.reason) : ""));
    return li;
  }

  function fillFolderList(id, rows, ignore) {
    const host = document.getElementById(id);
    if (!host) return;
    clear(host);
    const list = Array.isArray(rows) ? rows : [];
    if (!list.length) {
      host.appendChild(el("li", "folder-empty", "None"));
      return;
    }
    let i;
    for (i = 0; i < list.length; i++) host.appendChild(ignore ? ignoreLine(list[i]) : folderLine(list[i]));
  }

  function renderFolders(data) {
    const marked = data && Array.isArray(data.marked) ? data.marked : [];
    const rest = data && Array.isArray(data.rest) ? data.rest : [];
    const ignore = data && Array.isArray(data.ignore) ? data.ignore : [];
    const stamp = JSON.stringify([marked, rest, ignore]);
    if (stamp === foldersDrawn) return;
    foldersDrawn = stamp;
    fillFolderList("folder-marked", marked, false);
    fillFolderList("folder-rest", rest, false);
    fillFolderList("folder-ignore", ignore, true);
  }

  function loadFolders(signal) {
    return fetch("/api/folders", { cache: "no-store", credentials: "omit", signal: signal }).then(function (res) {
      if (!res.ok) throw new Error(String(res.status));
      return res.json();
    }).then(function (data) {
      renderFolders(data);
    }).catch(function () {});
  }

  function tick() {
    if (pending) return;
    pending = true;
    const ctl = new AbortController();
    const timer = setTimeout(function () { ctl.abort(); }, 4000);
    const stateDone = fetch("/api/state", { cache: "no-store", signal: ctl.signal }).then(function (res) {
      if (!res.ok) throw new Error(String(res.status));
      return res.json();
    }).then(function (data) {
      const stamp = new Date();
      nowMs = stamp.getTime();
      updatedLabel = hhmm(stamp);
      document.getElementById("clock").textContent = "Updated " + updatedLabel;
      hideOffline();
      render(data);
    }).catch(function () {
      showOffline();
    });
    Promise.all([stateDone, loadProblems(ctl.signal), loadRoadmap(ctl.signal), loadBudget(ctl.signal), loadAlerts(ctl.signal), loadFolders(ctl.signal)]).finally(function () {
      clearTimeout(timer);
      pending = false;
    });
  }

  // Row text for one problem. labelOf and now are arguments so a node test can call this alone.
  function problemRow(problem, labelOf, now) {
    const glyphs = { high: "\u25b2", medium: "\u25c6", low: "\u25bd" };
    const severity = problem && typeof problem.severity === "string" ? problem.severity : "";
    const kind = problem && typeof problem.kind === "string" ? problem.kind : "";
    const raw = problem && !missing(problem.headline) ? String(problem.headline) : "";
    const headline = kind === "job-failed" ? "Failed \u00b7 " + raw : raw;
    const ownerId = problem && !missing(problem.owner) ? String(problem.owner) : "";
    const owner = labelOf(ownerId);
    // now is 0 until the first /api/state lands; the browser clock is close enough until then.
    const age = ageOf(problem && problem.since, now || Date.now());
    const glyph = Object.prototype.hasOwnProperty.call(glyphs, severity) ? glyphs[severity] : "";
    const ident = problem && !missing(problem.id) ? String(problem.id) : "";
    const cut = ident.indexOf(":");
    const jobId = kind === "job-failed" && cut >= 0 ? ident.slice(cut + 1) : "";
    let label = severity + ": " + headline + ", owner " + owner + ", " + age;
    if (jobId) label += ", " + jobId;
    return { glyph: glyph, headline: headline, owner: owner, age: age, jobId: jobId, label: label };
  }

  // One item, one place: an alert that needs the owner lives in the Alerts strip. Its Problems row is dropped on the page only;
  // /api/problems and /api/brief keep it (the server is untouched).
  function pageProblems(items) {
    return (Array.isArray(items) ? items : []).filter(function (item) {
      return !(item && item.kind === "alert");
    });
  }

  function problemsStamp(items) {
    items = pageProblems(items);
    const rows = items.map(function (item) {
      const id = item && !missing(item.id) ? String(item.id) : "";
      const headline = item && !missing(item.headline) ? String(item.headline) : "";
      // The owner flag too: a row that starts or stops waiting on the owner gains or loses its feedback buttons.
      return id + "\t" + headline + (waitsOnOwner(item) ? "\t1" : "");
    }).join("\n");
    // Both flags are part of the stamp: a toggle rebuilds, a poll with the same flags only patches.
    // The fold's ids too: a row ageing past 7 days moves into the fold without any id or headline change.
    const stale = splitProblems(items, nowMs || Date.now()).stale.map(function (item) {
      return item && !missing(item.id) ? String(item.id) : "";
    }).join(" ");
    return (problemsExpanded ? "1" : "0") + (staleExpanded ? "1" : "0") + "\t" + stale + "\n" + rows;
  }

  // Stale PR noise is old ci-red / pr-stale. Anything else, including an unparseable since, stays fresh.
  function splitProblems(items, now) {
    // ponytail: one 7-day fold for every old ci-red and pr-stale. Per-repo folds if one repo owns the noise.
    const STALE_PR_DAYS = 7;
    const day = 24 * 60 * 60 * 1000;
    const fresh = [];
    const stale = [];
    const prRows = [];
    const bySubject = Object.create(null);  // no inherited keys: a subject can never hit __proto__
    const list = Array.isArray(items) ? items : [];
    const clock = typeof now === "number" && isFinite(now) ? now : Date.now();
    const limit = STALE_PR_DAYS * day;
    let i;
    for (i = 0; i < list.length; i++) {
      const item = list[i];
      const kind = item && typeof item.kind === "string" ? item.kind : "";
      const then = Date.parse(item && item.since);
      if ((kind === "ci-red" || kind === "pr-stale") && !isNaN(then) && clock - then > limit) {
        stale.push(item);
        const id = item.id !== null && item.id !== undefined ? String(item.id) : "";
        const cut = id.indexOf(":");
        if (cut >= 0) {
          const subject = id.slice(cut + 1);
          let row = bySubject[subject];
          if (!row) {
            row = { subject: subject, red: false, untouchedDays: null, owner: item.owner, since: item.since };
            bySubject[subject] = row;
            prRows.push(row);
          }
          if (kind === "ci-red") row.red = true;
          if (kind === "pr-stale" && missing(row.untouchedDays)) row.untouchedDays = Math.floor((clock - then) / day);
          if (then < Date.parse(row.since)) row.since = item.since;
        }
      } else {
        fresh.push(item);
      }
    }
    return { fresh: fresh, stale: stale, prs: prRows.length, prRows: prRows };
  }

  // Row text for one stale PR. labelOf and now are arguments so a node test can call this alone.
  function staleRow(prRow, labelOf, now) {
    const subject = prRow && !missing(prRow.subject) ? String(prRow.subject) : "";
    const hash = subject.lastIndexOf("#");
    const title = hash < 0 ? subject : subject.slice(0, hash) + " #" + subject.slice(hash + 1);
    const red = !!(prRow && prRow.red);
    const days = prRow ? prRow.untouchedDays : null;
    let headline = title;
    if (red) headline += " \u00b7 CI red";
    if (!missing(days)) headline += " \u00b7 untouched " + days + "d";
    const ownerId = prRow && !missing(prRow.owner) ? String(prRow.owner) : "";
    const owner = labelOf(ownerId);
    const age = ageOf(prRow && prRow.since, now || Date.now());
    return {
      glyph: red ? "\u25b2" : "\u25cb",
      sev: red ? "sev-high" : "sev-stale",
      headline: headline,
      owner: owner,
      age: age,
      label: "stale: " + headline + ", owner " + owner + ", " + age
    };
  }

  // Collapsed: PROBLEM_CAP - 1 fresh rows plus the show-all button. Expanded: every fresh row.
  function problemView(items) {
    const split = splitProblems(pageProblems(items), nowMs || Date.now());
    const capped = !problemsExpanded && split.fresh.length > PROBLEM_CAP;
    const freshShown = capped ? split.fresh.slice(0, PROBLEM_CAP - 1) : split.fresh;
    return { freshShown: freshShown, fresh: split.fresh, prRows: split.prRows };
  }

  // ponytail: stamp is expanded + ids + headlines, so a poll does not rebuild and steal focus.
  // Age, owner label and glyph are written onto the existing buttons. A headline change still rebuilds.
  // ponytail: patch fresh rows only. Stale ids sit in the stamp, so a fold change rebuilds; an open fold keeps its age and owner label until then. Upgrade: patch prRows in place.
  function patchProblems(items) {
    const ul = document.getElementById("problem-list");
    if (!ul) return;
    const shown = problemView(items).freshShown;
    const buttons = ul.querySelectorAll("button.problem");
    let i;
    for (i = 0; i < buttons.length && i < shown.length; i++) {
      const problem = shown[i];
      const row = problemRow(problem, labelOf, nowMs);
      const severity = problem && typeof problem.severity === "string" ? problem.severity : "";
      const mark = buttons[i].querySelector(".problem-glyph");
      const ownerEl = buttons[i].querySelector(".problem-owner");
      const ageEl = buttons[i].querySelector(".problem-age");
      buttons[i].className = "problem sev-" + severity;
      if (mark) mark.textContent = row.glyph;
      if (ownerEl) ownerEl.textContent = row.owner;
      if (ageEl) ageEl.textContent = row.age;
      buttons[i].setAttribute("aria-label", row.label);
      buttons[i].dataset.owner = problem && !missing(problem.owner) ? String(problem.owner) : "";
    }
  }

  function renderProblems(items) {
    const section = document.getElementById("problems");
    const ul = document.getElementById("problem-list");
    if (!section || !ul) return;
    const list = pageProblems(items);
    problemItems = list;
    paintStatus();
    const stamp = problemsStamp(list);
    if (stamp === problemsDrawn) {
      patchProblems(list);
      return;
    }
    problemsDrawn = stamp;
    clear(ul);
    if (!list.length) {
      ul.appendChild(el("li", "problem-empty", "Nothing needs attention."));
      return;
    }
    const view = problemView(list);
    function addRow(problem, built) {
      const row = built || problemRow(problem, labelOf, nowMs);
      const sev = built ? built.sev : "sev-" + (problem && typeof problem.severity === "string" ? problem.severity : "");
      const li = el("li");
      const btn = el("button", "problem " + sev);
      btn.type = "button";
      btn.setAttribute("aria-label", row.label);
      btn.dataset.owner = problem && !missing(problem.owner) ? String(problem.owner) : "";
      const mark = el("span", "problem-glyph", row.glyph);
      mark.setAttribute("aria-hidden", "true");
      btn.appendChild(mark);
      // The job id rides on the headline (it is part of the stamp, so a new id rebuilds rather than patches).
      const hl = el("span", "problem-hl", row.headline);
      if (row.jobId) hl.appendChild(el("span", "problem-id", row.jobId));
      btn.appendChild(hl);
      const meta = el("span", "problem-meta");
      meta.appendChild(el("span", "problem-owner", row.owner));
      meta.appendChild(el("span", "problem-age", row.age));
      btn.appendChild(meta);
      btn.addEventListener("click", function () {
        // Read the owner off the button: a patched row may have a newer owner than when it was built.
        const ownerId = btn.dataset.owner;
        const nodes = current && current.graph && current.graph.nodes;
        if (!nodeById(nodes, ownerId)) return;
        select(ownerId);
      });
      li.appendChild(btn);
      // The buttons sit beside the row button, never inside it. The stale-PR fold has no single kind to record, so it gets none.
      if (!built && waitsOnOwner(problem)) {
        li.className = "problem-fb";
        li.appendChild(feedbackGroup(problem.kind, problem.id, row.headline));
      }
      ul.appendChild(li);
    }
    view.freshShown.forEach(function (problem) { addRow(problem); });
    if (view.fresh.length > PROBLEM_CAP) {
      const li = el("li");
      const more = el("button", "problem-more", problemsExpanded ? "Show fewer" : "Show all " + view.fresh.length + " problems");
      more.type = "button";
      more.setAttribute("aria-expanded", problemsExpanded ? "true" : "false");
      more.setAttribute("aria-controls", "problem-list");
      more.addEventListener("click", function () {
        problemsExpanded = !problemsExpanded;
        renderProblems(problemItems);
        const again = ul.querySelector("button.problem-more:not(.problem-fold)");
        if (again) again.focus();
      });
      li.appendChild(more);
      ul.appendChild(li);
    }
    if (view.prRows.length) {
      const li = el("li");
      const fold = el("button", "problem-more problem-fold", (staleExpanded ? "Hide stale PRs (" : "Stale PRs (") + view.prRows.length + ")");
      fold.type = "button";
      fold.setAttribute("aria-expanded", staleExpanded ? "true" : "false");
      fold.setAttribute("aria-controls", "problem-list");
      fold.addEventListener("click", function () {
        staleExpanded = !staleExpanded;
        renderProblems(problemItems);
        const again = ul.querySelector("button.problem-fold");
        if (again) again.focus();
      });
      li.appendChild(fold);
      ul.appendChild(li);
      if (staleExpanded) view.prRows.forEach(function (pr) { addRow(pr, staleRow(pr, labelOf, nowMs)); });
    }
  }

  function loadProblems(signal) {
    return fetch("/api/problems", { cache: "no-store", credentials: "omit", signal: signal }).then(function (res) {
      if (!res.ok) throw new Error(String(res.status));
      return res.json();
    }).then(function (items) {
      if (!Array.isArray(items)) throw new Error("problems");
      // WHY: only a good load clears stale. renderProblems also redraws for the Show-all and fold buttons.
      const section = document.getElementById("problems");
      if (section) section.classList.remove("stale");
      staleNote(section, false);
      renderProblems(items);
    }).catch(function () {
      const section = document.getElementById("problems");
      if (section) section.classList.add("stale");
      staleNote(section, true);
    });
  }

  function formatReset(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      hourCycle: "h23"
    }).formatToParts(d);
    let w = "", h = "", m = "";
    parts.forEach(function (p) {
      if (p.type === "weekday") w = p.value;
      else if (p.type === "hour") h = p.value;
      else if (p.type === "minute") m = p.value;
    });
    return w + " " + h + ":" + m;
  }

  function formatNYTime(epochSec) {
    if (epochSec === null || epochSec === undefined) return "";
    const d = new Date(epochSec * 1000);
    if (isNaN(d.getTime())) return "";
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      hourCycle: "h23"
    }).formatToParts(d);
    let h = "", m = "";
    parts.forEach(function (p) {
      if (p.type === "hour") h = p.value;
      else if (p.type === "minute") m = p.value;
    });
    return h + ":" + m;
  }

  function budgetSentence(row) {
    if (row.used === null || row.used === undefined) {
      return "No reading yet";
    }
    const unit = row.unit;
    const id = row.id;
    if (id === "claude" || unit === "pts") {
      return num(row.used).toFixed(1) + " of " + num(row.allowance).toFixed(1) + " pts";
    }
    if (id === "grok" || unit === "tokens") {
      const u = Math.round(num(row.used) / 1e6);
      const a = Math.round(num(row.allowance) / 1e6);
      return u + "M of " + a + "M ceiling";
    }
    if (id === "glm" || unit === "credits") {
      const u = Math.round(num(row.used)).toLocaleString("en-US");
      const a = Math.round(num(row.allowance)).toLocaleString("en-US");
      return u + " of " + a + " credits";
    }
    if (unit === "usd" || id === "deepseek" || id === "apikeyfan") {
      const u = num(row.used).toFixed(2);
      if (row.allowance === 0) {
        if (row.extra && row.extra.allowance_until) {
          const until = String(row.extra.allowance_until);
          const md = until.length >= 10 ? until.slice(5, 10) : until;
          return "$" + u + " \u00b7 $0 allowance until " + md;
        }
        return "$" + u + " \u00b7 $0 allowance";
      }
      const a = num(row.allowance).toFixed(2);
      return "$" + u + " of $" + a;
    }
    if (id === "default-pool" || unit === "weekly_pct") {
      return Math.round(num(row.used)) + "% of the week";
    }
    return String(row.used) + " of " + String(row.allowance);
  }

  function budgetSubText(row) {
    if (row.used === null || row.used === undefined) return "";
    if (row.id === "claude" && row.extra && row.extra.week_pct != null && row.extra.resets_at) {
      const reset = formatReset(row.extra.resets_at);
      if (reset) return "week " + Math.round(num(row.extra.week_pct)) + "% \u00b7 resets " + reset;
    }
    if (row.id === "glm" && row.extra && row.extra.five_hour && row.extra.five_hour.used != null && row.extra.five_hour.of != null) {
      const u = Math.round(num(row.extra.five_hour.used)).toLocaleString("en-US");
      const of = Math.round(num(row.extra.five_hour.of)).toLocaleString("en-US");
      return "5 h window " + u + " of " + of;
    }
    return "";
  }

  function budgetAsOfText(row) {
    if (row.used === null || row.used === undefined || row.as_of === null || row.as_of === undefined) {
      return "";
    }
    const t = formatNYTime(row.as_of);
    if (!t) return "";
    return row.old ? "as of " + t + " \u00b7 old" : "as of " + t;
  }

  function budgetRow(row) {
    const hasReading = row.used !== null && row.used !== undefined;
    const subText = hasReading ? budgetSubText(row) : "";
    const rowEl = el("div", subText ? "budget-row is-tall" : "budget-row");

    // 1. Pool name
    rowEl.appendChild(el("span", "budget-pool", row.name || ""));

    // 2. Used / allowance
    const usedCell = el("div", "budget-used");
    const sentenceEl = el("div", "budget-sentence");
    if (hasReading && row.over) {
      sentenceEl.appendChild(el("span", "budget-over", "\u25b2 Over "));
    }
    sentenceEl.appendChild(el("span", "budget-text", budgetSentence(row)));
    usedCell.appendChild(sentenceEl);
    if (subText) {
      usedCell.appendChild(el("div", "budget-sub", subText));
    }
    rowEl.appendChild(usedCell);

    // 3. Meter
    const meterCell = el("div", "budget-meter");
    if (row.id === "default-pool") {
      meterCell.appendChild(el("span", "budget-no-allowance", "no allowance set"));
    } else if (hasReading) {
      const track = el("div", "budget-track");
      const fill = el("span", row.over ? "budget-fill red" : "budget-fill");
      let pct = 0;
      if (row.allowance && row.allowance > 0 && row.used && row.used > 0) {
        pct = Math.min(row.used / row.allowance, 1) * 100;
      }
      fill.style.width = (Math.round(pct * 10) / 10) + "%";
      track.appendChild(fill);
      if (row.id === "grok" && row.extra && typeof row.extra.target === "number" && row.allowance && row.allowance > 0) {
        const tick = el("span", "budget-tick");
        const tickPct = Math.min(Math.max((row.extra.target / row.allowance) * 100, 0), 100);
        tick.style.left = (Math.round(tickPct * 10) / 10) + "%";
        track.appendChild(tick);
      }
      meterCell.appendChild(track);
    }
    rowEl.appendChild(meterCell);

    // 4. As of
    rowEl.appendChild(el("span", "budget-asof", budgetAsOfText(row)));

    return rowEl;
  }

  // ponytail: one stamp of the visible pools. The same stamp skips the rebuild so a poll does not flash.
  function budgetStamp(pools) {
    return JSON.stringify(pools.map(function (row) {
      return [
        row.id, row.name, row.unit, row.used, row.allowance,
        row.over, row.as_of, row.old, row.extra
      ];
    }));
  }

  function renderBudget(payload) {
    const host = document.getElementById("budget-list");
    if (!host) return;
    const pools = Array.isArray(payload && payload.pools) ? payload.pools : [];
    const stamp = budgetStamp(pools);
    if (stamp === budgetDrawn) return;
    budgetDrawn = stamp;
    clear(host);
    let i;
    for (i = 0; i < pools.length; i++) {
      host.appendChild(budgetRow(pools[i]));
    }
  }

  function loadBudget(signal) {
    return fetch("/api/budget", { cache: "no-store", credentials: "omit", signal: signal }).then(function (res) {
      if (!res.ok) throw new Error(String(res.status));
      return res.json();
    }).then(function (data) {
      if (!data || !Array.isArray(data.pools)) return;
      budgetPayload = data;
      renderBudget(data);
    }).catch(function () {});
  }

  // ponytail: name then file. State is not a key, so a poll cannot reorder a group.
  function roadmapCmp(a, b) {
    const an = String(a && a.name || "").toLowerCase();
    const bn = String(b && b.name || "").toLowerCase();
    if (an < bn) return -1;
    if (an > bn) return 1;
    const af = String(a && a.file || "").toLowerCase();
    const bf = String(b && b.file || "").toLowerCase();
    if (af < bf) return -1;
    if (af > bf) return 1;
    return 0;
  }

  function roadmapGroups(rows) {
    const order = ["P0", "P1", "P2", "Other"];
    const buckets = { P0: [], P1: [], P2: [], Other: [] };
    const list = Array.isArray(rows) ? rows : [];
    let i;
    for (i = 0; i < list.length; i++) {
      const row = list[i];
      if (!row) continue;
      const key = order.indexOf(row.priority) >= 0 ? row.priority : "Other";
      buckets[key].push(row);
    }
    return order.filter(function (key) { return buckets[key].length; }).map(function (key) {
      return { priority: key, rows: buckets[key].slice().sort(roadmapCmp) };
    });
  }

  // Reads the graph view's state and word.
  function roadmapMark(node) {
    if (!node) return null;
    if (node.state === "blocked") return { text: "\u25c7 Blocked", tone: "amber" };
    if (node.state === "waits") return { text: "\u25c7 Needs Owner", tone: "amber" };
    if (node.state === "waiting" && /\bVP\b/.test(String(node.word || ""))) return { text: "\u25c7 Needs VP", tone: "amber" };
    return null;
  }

  function roadmapDemo(demo) {
    if (!demo) return { text: "no demo", past: false };
    const date = missing(demo.date) ? "" : String(demo.date);
    const md = date.length >= 10 ? date.slice(5, 10) : date;
    const text = missing(demo.text) ? "" : String(demo.text);
    let label = "no demo";
    if (md && text) label = md + " \u00b7 " + text;
    else if (md) label = md;
    else if (text) label = text;
    return { text: label, past: demo.past === true };
  }

  function roadPct(part, whole) {
    if (!whole) return 0;
    return Math.round((1000 * part) / whole) / 10;
  }

  // ponytail: both weeks at 0 total leave 0-width segments. The bar's own track is the line.
  function roadmapBar(week1, week2) {
    const t1 = num(week1 && week1.total);
    const t2 = num(week2 && week2.total);
    const d1 = num(week1 && week1.done);
    const d2 = num(week2 && week2.done);
    const sum = t1 + t2;
    return {
      w1: sum ? roadPct(t1, sum) : 0,
      w2: sum ? roadPct(t2, sum) : 0,
      f1: t1 ? roadPct(d1, t1) : 0,
      f2: t2 ? roadPct(d2, t2) : 0
    };
  }

  // ponytail: one stamp of the visible fields. The same stamp skips the rebuild so a poll does not move focus.
  function roadmapStamp(groups, nodes) {
    return JSON.stringify(groups.map(function (group) {
      return [group.priority, group.rows.map(function (row) {
        const node = row.node ? nodeById(nodes, row.node) : null;
        const mark = roadmapMark(node);
        const demo = roadmapDemo(row.next_demo);
        const bar = roadmapBar(row.week1, row.week2);
        const display = node && node.label ? String(node.label) : String(row.name || "");
        return [row.file, row.name, display, row.node, row.done, row.total, bar.w1, bar.w2, bar.f1, bar.f2, demo.text, demo.past, mark ? mark.text : "", node && node.headline ? node.headline : "", row.text];
      })];
    }));
  }

  function fillRoadmapText(body, text) {
    clear(body);
    const lines = String(text || "").split("\n");
    let i;
    for (i = 0; i < lines.length; i++) {
      const line = lines[i].replace(/\r$/, "");
      const match = /^(\s*)- \[([ xX])\](.*)$/.exec(line);
      const row = el("div", match ? "roadmap-line roadmap-task" : "roadmap-line");
      if (match) {
        const on = match[2] !== " ";
        const box = el("span", on ? "roadmap-box on" : "roadmap-box");
        box.setAttribute("role", "img");
        box.setAttribute("aria-label", on ? "done" : "open");
        if (on) box.textContent = "\u2713";
        row.appendChild(box);
        row.appendChild(el("span", "roadmap-line-text", match[1] + match[3]));
      } else {
        row.textContent = line;
      }
      body.appendChild(row);
    }
  }

  function roadmapSheet() {
    let sheet = document.getElementById("roadmap-sheet");
    if (sheet) return sheet;
    sheet = el("aside", "roadmap-sheet");
    sheet.id = "roadmap-sheet";
    sheet.hidden = true;
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "false");  // a side sheet: the page stays usable, focus is not trapped
    sheet.setAttribute("aria-label", "Roadmap");
    const close = el("button", "roadmap-close", "Close");
    close.type = "button";
    close.addEventListener("click", closeRoadmap);
    sheet.appendChild(close);
    const body = el("div", "roadmap-body");
    body.id = "roadmap-body";
    sheet.appendChild(body);
    document.body.appendChild(sheet);
    return sheet;
  }

  function openRoadmap(row, btn) {
    const sheet = roadmapSheet();
    roadmapOpen = true;
    roadmapFile = row && row.file ? String(row.file) : "";
    roadmapReturn = btn;
    fillRoadmapText(document.getElementById("roadmap-body"), row && row.text);
    sheet.hidden = false;
    const close = sheet.querySelector("button");
    if (close && close.focus) close.focus({ preventScroll: true });
  }

  function findRoadmapButton(file) {
    const host = document.getElementById("roadmap-list");
    if (!host || !host.querySelectorAll) return null;
    const buttons = host.querySelectorAll("button");
    let i;
    for (i = 0; i < buttons.length; i++) {
      if (buttons[i].getAttribute("data-file") === file) return buttons[i];
    }
    return null;
  }

  function closeRoadmap() {
    const sheet = document.getElementById("roadmap-sheet");
    if (sheet) sheet.hidden = true;
    roadmapOpen = false;
    const file = roadmapFile;
    const back = roadmapReturn;
    roadmapFile = "";
    roadmapReturn = null;
    const live = file ? findRoadmapButton(file) : null;
    const target = live || back;
    if (target && target.focus) target.focus({ preventScroll: true });
  }

  function roadmapKey(ev) {
    if (!ev || ev.key !== "Escape" || !roadmapOpen) return false;
    closeRoadmap();
    return true;
  }

  function activeRoadmapFile() {
    const active = document.activeElement;
    if (!active || !active.getAttribute) return "";
    const cls = active.className || "";
    if ((" " + cls + " ").indexOf(" roadmap-row ") < 0) return "";
    return active.getAttribute("data-file") || "";
  }

  function focusRoadmapFile(file) {
    const btn = findRoadmapButton(file);
    if (btn && btn.focus) btn.focus({ preventScroll: true });
  }

  function refillRoadmapSheet(groups) {
    if (!roadmapOpen) return;
    const body = document.getElementById("roadmap-body");
    if (!body) return;
    let i, j, row;
    for (i = 0; i < groups.length; i++) {
      for (j = 0; j < groups[i].rows.length; j++) {
        row = groups[i].rows[j];
        if (String(row.file || "") === roadmapFile) {
          fillRoadmapText(body, row.text);
          return;
        }
      }
    }
  }

  function roadmapButton(row, nodes) {
    const node = row.node ? nodeById(nodes, row.node) : null;
    const mark = roadmapMark(node);
    const hl = node && node.headline ? String(node.headline) : "";
    const display = node && node.label ? String(node.label) : String(row.name || "");
    const btn = el("button", "roadmap-row");
    if (mark) btn.className = "roadmap-row is-marked";
    btn.type = "button";
    btn.setAttribute("data-file", row.file ? String(row.file) : "");
    const orb = el("span", "roadmap-orb");
    if (row.node) drawOrb(orb, ROAD_ORB, String(row.node), 16);
    btn.appendChild(orb);
    btn.appendChild(el("span", "roadmap-name", display));
    const done = missing(row.done) ? num(row.week1 && row.week1.done) + num(row.week2 && row.week2.done) : num(row.done);
    const total = missing(row.total) ? num(row.week1 && row.week1.total) + num(row.week2 && row.week2.total) : num(row.total);
    btn.appendChild(el("span", "roadmap-count", done + "/" + total));
    const spec = roadmapBar(row.week1, row.week2);
    const bar = el("span", "roadmap-bar");
    const seg = function (width, fill) {
      const s = el("span", "roadmap-seg");
      s.style.width = width + "%";
      const f = el("span", "roadmap-fill");
      f.style.width = fill + "%";
      s.appendChild(f);
      return s;
    };
    bar.appendChild(seg(spec.w1, spec.f1));
    bar.appendChild(seg(spec.w2, spec.f2));
    btn.appendChild(bar);
    const demo = roadmapDemo(row.next_demo);
    btn.appendChild(el("span", demo.past ? "roadmap-demo is-past" : "roadmap-demo", demo.text));
    if (mark) {
      const markEl = el("span", "roadmap-mark");
      markEl.appendChild(el("span", "roadmap-flag " + mark.tone, mark.text));
      if (hl) {
        const line = el("span", "roadmap-hl", hl);
        line.setAttribute("title", hl);
        markEl.appendChild(line);
      }
      btn.appendChild(markEl);
    }
    let label = display + ", " + done + "/" + total + ", " + demo.text;
    if (mark) {
      label += ", " + mark.text.replace(/^\u25c7\s*/, "");  // the diamond is a glyph; the word is what is read aloud
      if (hl) label += ": " + hl;
    }
    btn.setAttribute("aria-label", label);
    btn.addEventListener("click", function () { openRoadmap(row, btn); });
    return btn;
  }

  function renderRoadmap(payload) {
    const host = document.getElementById("roadmap-list");
    if (!host) return;
    const nodes = (current && current.graph && current.graph.nodes) || {};
    const groups = roadmapGroups(payload && payload.rows);
    const nav = document.getElementById("nav-roadmap");
    if (nav) {
      let n = 0;
      for (let k = 0; k < groups.length; k++) n += groups[k].rows.length;
      nav.hidden = n === 0;
    }
    const stamp = roadmapStamp(groups, nodes);
    if (stamp === roadmapDrawn) return;
    const focusFile = activeRoadmapFile();
    roadmapDrawn = stamp;
    clear(host);
    let i, j;
    for (i = 0; i < groups.length; i++) {
      host.appendChild(el("h3", "roadmap-group", groups[i].priority));
      for (j = 0; j < groups[i].rows.length; j++) host.appendChild(roadmapButton(groups[i].rows[j], nodes));
    }
    if (focusFile) focusRoadmapFile(focusFile);
    refillRoadmapSheet(groups);
  }

  // A payload without a rows array is not a roadmap, so a bad response leaves the list as it was.
  function loadRoadmap(signal) {
    return fetch("/api/roadmap", { cache: "no-store", credentials: "omit", signal: signal }).then(function (res) {
      if (!res.ok) throw new Error(String(res.status));
      return res.json();
    }).then(function (data) {
      if (!data || !Array.isArray(data.rows)) return;
      roadmapPayload = data;
      renderRoadmap(data);
    }).catch(function () {});
  }

  function labelOf(id) {
    const nodes = current && current.graph && current.graph.nodes;
    const node = nodeById(nodes, id);
    if (node && node.label) return String(node.label);
    return id;
  }

  // ---- feedback (loop 5): Useful / Not useful on rows that wait on the owner. The server records the click and nothing more:
  // no count is shown and no row changes. Verdicts live in feedbackSaid, so a poll or a redraw repaints the pressed state.
  // Alert rows are always the owner's; a Problems row says so by its owner id.
  function waitsOnOwner(problem) {
    return !!problem && problem.owner === "owner" && typeof problem.kind === "string" && !!problem.kind &&
      typeof problem.id === "string" && !!problem.id;
  }

  function eachFeedback(subject, fn) {
    const groups = document.querySelectorAll(".fb");
    for (let i = 0; i < groups.length; i++) {
      if (groups[i].dataset.subject === subject) fn(groups[i]);
    }
  }

  function paintFeedback(group) {
    const said = feedbackSaid.get(group.dataset.subject);
    const buttons = group.querySelectorAll(".fb-btn");
    for (let i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", buttons[i].dataset.verdict === said ? "true" : "false");
    }
  }

  function sendFeedback(kind, subject, verdict) {
    if (feedbackBusy.has(subject) || feedbackSaid.get(subject) === verdict) return;
    function finish(ok) {
      feedbackBusy.delete(subject);
      if (ok) feedbackSaid.set(subject, verdict);
      // Every row with this subject reads the same map. The page shows each alert once (Problems drops kind "alert"), but a redraw rebuilds rows, so the map is the truth.
      eachFeedback(subject, function (group) {
        const note = group.querySelector(".fb-status");
        if (ok) {
          note.textContent = "";
          note.className = "fb-status";
        } else {
          errorWords(note, "Couldn't save");
          note.className = "fb-status is-error";
        }
        paintFeedback(group);
      });
    }
    feedbackBusy.add(subject);
    const ctl = new AbortController();
    const timer = setTimeout(function () { ctl.abort(); }, 4000);
    fetch("/api/feedback", {
      method: "POST",
      signal: ctl.signal,
      cache: "no-store",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Atlas-UI": alertNonce },
      body: JSON.stringify({ kind: kind, subject: subject, verdict: verdict })
    }).then(function (res) { return !!res.ok; }, function () { return false; }).then(function (ok) {
      clearTimeout(timer);
      finish(ok);
    });
  }

  // name only labels the buttons for a screen reader; kind and subject (ids) are all that leave the page.
  function feedbackGroup(kind, subject, name) {
    const group = el("span", "fb");
    group.dataset.subject = subject;
    // The status comes first: the pair sits at the row's right edge, so "Couldn't save" grows leftwards and the buttons stay put.
    const note = el("span", "fb-status", "");
    note.setAttribute("role", "status");
    group.appendChild(note);
    [["useful", "Useful"], ["not-useful", "Not useful"]].forEach(function (pair) {
      const btn = el("button", "fb-btn", pair[1]);
      btn.setAttribute("type", "button");
      btn.setAttribute("aria-label", name ? pair[1] + ": " + name : pair[1]);
      btn.dataset.verdict = pair[0];
      btn.addEventListener("click", function () { sendFeedback(kind, subject, pair[0]); });
      group.appendChild(btn);
    });
    paintFeedback(group);
    return group;
  }

  // ponytail: fixed severity rank, then the list's own order. recent is ordered by open time, so the 10 closed rows
  // are the most recently opened ones, not the latest closes; order by close time in the API if that matters.
  function alertRank(row) {
    const order = { "needs-owner": 0, warn: 1, info: 2 };
    const key = row && row.severity;
    return Object.prototype.hasOwnProperty.call(order, key) ? order[key] : 3;
  }

  function alertSort(rows) {
    return rows.map(function (row, i) { return [alertRank(row), i, row]; }).sort(function (a, b) {
      return a[0] - b[0] || a[1] - b[1];
    }).map(function (item) { return item[2]; });
  }

  // Shape plus meaning colour, the budget panels' marks: diamond = waits on you (amber), triangle = failure (red), ring = quiet.
  function alertBadge(severity) {
    if (severity === "needs-owner") return ["Needs you", "alert-badge is-owner", "\u25c7"];
    if (severity === "warn") return ["Warn", "alert-badge is-warn", "\u25b2"];
    if (severity === "info") return ["Info", "alert-badge is-info", "\u25cb"];
    return ["", "alert-badge", ""];
  }

  function alertAge(ts) {
    const n = Number(ts);
    const ms = n > 1e12 ? n : n * 1000;
    if (!isFinite(n) || n <= 0 || ms > 8.64e15) return "";
    return ageOf(new Date(ms).toISOString(), nowMs || Date.now());
  }

  function alertLink(link) {
    if (typeof link !== "string" || !link) return null;
    if (link.indexOf("https://github.com/coco-research/") === 0) {
      const a = el("a", "alert-link", link);
      a.setAttribute("href", link);
      a.setAttribute("rel", "noreferrer noopener");
      a.setAttribute("target", "_blank");
      return a;
    }
    if (link.charAt(0) === "/" && link.charAt(1) !== "/") return el("span", "alert-link", link);
    return null;
  }

  function alertClosed(recent) {
    const out = [];
    const list = Array.isArray(recent) ? recent : [];
    let i;
    for (i = 0; i < list.length && out.length < 10; i++) {
      if (list[i] && list[i].state === "done") out.push(list[i]);
    }
    return out;
  }

  // A failure line: the triangle is a glyph (aria-hidden), the words after it are what a screen reader reads.
  function errorWords(host, words) {
    clear(host);
    const mark = el("span", "alert-mark", "\u25b2");
    mark.setAttribute("aria-hidden", "true");
    host.appendChild(mark);
    host.appendChild(document.createTextNode(words));
  }

  // Signal: every state gets shape plus word. A section whose last load failed keeps its red edge and says so, in its head
  // right after the h2. Idempotent: both loaders call it on the failure path and again on a good load.
  function staleNote(section, on) {
    if (!section) return;
    const head = section.querySelector("h2");
    const note = section.querySelector(".stale-note");
    if (!on) {
      if (note && note.parentNode) note.parentNode.removeChild(note);
      return;
    }
    if (note) return;
    const span = el("span", "stale-note");
    span.setAttribute("role", "status");
    errorWords(span, "Not updating. Retrying.");
    if (head && head.parentNode) head.parentNode.insertBefore(span, head.nextSibling);
    else section.appendChild(span);
  }

  function closeAlert(row, btn, status) {
    const id = row && row.id ? String(row.id) : "";
    function finish(ok) {
      alertBusy = Math.max(0, alertBusy - 1);
      if (ok) {
        status.className = "alert-status";
        status.textContent = "Closed";  // confirm now; the reload only moves the row
        return loadAlerts();
      }
      btn.disabled = false;
      status.className = "alert-status is-error";
      errorWords(status, "Couldn't close");
    }
    if (!id || !alertNonce) {
      status.className = "alert-status is-error";
      errorWords(status, "Couldn't close");
      return;
    }
    btn.disabled = true;
    alertBusy += 1;
    const ctl = new AbortController();
    const timer = setTimeout(function () { ctl.abort(); }, 4000);
    fetch("/api/alerts/" + encodeURIComponent(id) + "/close", {
      method: "POST",
      signal: ctl.signal,
      cache: "no-store",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Atlas-UI": alertNonce },
      body: "{}"
    }).then(function (res) { clearTimeout(timer); return finish(!!res.ok); }).catch(function () { clearTimeout(timer); finish(false); });
  }

  function alertRow(row, open) {
    const badge = open ? alertBadge(row && row.severity) : ["Closed", "alert-badge is-closed", ""];
    const li = el("li", open ? "alert" : "alert is-closed");
    const tag = el("span", badge[1]);
    if (badge[2]) {
      const mark = el("span", "alert-mark", badge[2]);
      mark.setAttribute("aria-hidden", "true");  // the shape is a glyph; the word is what is read aloud
      tag.appendChild(mark);
    }
    tag.appendChild(document.createTextNode(badge[0]));
    li.appendChild(tag);
    li.appendChild(el("span", "alert-src", String(row.source || "") + " \u00b7 " + String(row.kind || "")));
    const body = el("span", "alert-body");
    const text = el("span", "alert-text", String(row.text || ""));
    text.setAttribute("title", String(row.text || ""));
    body.appendChild(text);
    const link = alertLink(row && row.link);
    if (link) body.appendChild(link);
    li.appendChild(body);
    if (open) li.appendChild(ageSpan("", row.ts));
    else {
      if (alertAge(row && row.closed_ts)) li.appendChild(ageSpan("closed ", row.closed_ts));
    }
    if (open) {
      const actions = el("span", "alert-actions");
      if (row.severity === "needs-owner" && row.id) {
        actions.appendChild(feedbackGroup("alert", "alert:" + String(row.id), String(row.text || "")));
      }
      const btn = el("button", "alert-done", "Done");
      btn.setAttribute("type", "button");
      btn.setAttribute("aria-label", "Done: " + String(row.text || ""));
      const note = el("span", "alert-status", "");
      note.setAttribute("role", "status");
      btn.addEventListener("click", function () { closeAlert(row, btn, note); });
      actions.appendChild(btn);
      actions.appendChild(note);
      li.appendChild(actions);
    }
    return li;
  }

  function alertStamp(open, closed) {
    const pack = function (row) {
      return [row.id, row.state, row.severity, row.source, row.kind, row.text, row.link || "", row.ts];
    };
    return JSON.stringify([open.map(pack), closed.map(pack)]);
  }

  function renderAlerts(payload) {
    if (alertBusy) return;
    const host = document.getElementById("alert-list");
    if (!host) return;
    const open = alertSort(Array.isArray(payload && payload.open) ? payload.open : []);
    const closed = alertClosed(payload && payload.recent);
    const stamp = alertStamp(open, closed);
    if (stamp === alertsDrawn) {
      // Same rows: only the ages move. Patch them in place so a focused Done button keeps focus.
      const ages = host.querySelectorAll(".alert-age");
      for (let k = 0; k < ages.length; k++) ages[k].textContent = (ages[k].dataset.prefix || "") + alertAge(ages[k].dataset.ts);
      return;
    }
    alertsDrawn = stamp;
    clear(host);
    if (!open.length && !closed.length) {
      host.appendChild(el("li", "alert-empty", "No alerts."));
      return;
    }
    let i;
    for (i = 0; i < open.length; i++) host.appendChild(alertRow(open[i], true));
    for (i = 0; i < closed.length; i++) host.appendChild(alertRow(closed[i], false));
  }

  // Each age keeps its own ts and prefix, so the poll can move open and closed ages alike.
  function ageSpan(prefix, ts) {
    const span = el("span", "alert-age", prefix + alertAge(ts));
    span.dataset.ts = String(ts);
    span.dataset.prefix = prefix;
    return span;
  }

  function loadAlerts(signal) {
    const section = document.getElementById("alerts");
    return fetch("/api/alerts", { cache: "no-store", credentials: "omit", signal: signal }).then(function (res) {
      if (!res.ok) throw new Error(String(res.status));
      return res.json();
    }).then(function (data) {
      if (!data || !Array.isArray(data.open) || !Array.isArray(data.recent)) throw new Error("alerts");
      if (section) section.classList.remove("stale");
      staleNote(section, false);
      if (typeof data.nonce === "string") alertNonce = data.nonce;
      alertPayload = data;
      renderAlerts(data);
      paintStatus();
      paintFirst(current);
    }).catch(function () {
      if (section) section.classList.add("stale");
      staleNote(section, true);
      const host = document.getElementById("alert-list");
      // WHY: Problems no longer repeats alerts, so a first load that fails must say so, not look empty.
      if (host && !host.firstChild) {
        const empty = el("li", "alert-empty is-error");
        errorWords(empty, "Alerts unavailable. Retrying.");
        host.appendChild(empty);
      }
    });
  }

  // A mention id is its post's id + ":m:" + part id; return the post id, or "".
  function parentId(id) {
    const at = id.lastIndexOf(":m:");
    return at > 0 ? id.slice(0, at) : "";
  }

  // Fold a mention into its post's row. False when the post is not in the log.
  function fold(record) {
    const pid = parentId(record.id);
    if (!pid || record.kind !== "mention") return false;
    let i;
    for (i = 0; i < log.length; i++) {
      const parent = log[i];
      if (parent.id !== pid || parent.kind === "mention") continue;
      if (parent.mentions.indexOf(record.toLabel) < 0) parent.mentions.push(record.toLabel);
      if (!nodeLog[record.to] || nodeLog[record.to].indexOf(parent) < 0) pushNode(record.to, parent);
      lastEventAt = Date.now();
      lastWhen = record.when;
      return true;
    }
    return false;
  }

  function linkTone(event) {
    if (event.kind === "job-failed") return "red";
    if (event.kind === "post" && event.to === "owner") return "blue";
    return "";
  }

  function eventText(event) {
    if (event.headline) return String(event.headline);
    if (event.kind === "mention") return "";
    if (Object.prototype.hasOwnProperty.call(KIND_TEXT, event.kind)) return KIND_TEXT[event.kind];
    return "";
  }

  function mentionsText(record) {
    return record.mentions && record.mentions.length ? "mentions " + record.mentions.join(", ") : "";
  }

  function lineWords(record) {
    const who = record.kind === "mention"
      ? record.fromLabel + " mentions " + record.toLabel
      : record.fromLabel + " \u2192 " + record.toLabel;
    let text = hms(record.when) + " \u00b7 " + who;
    const ment = mentionsText(record);
    if (ment) text += " \u00b7 " + ment;
    return record.text ? text + " \u00b7 " + record.text : text;
  }

  function tickerItem(record, replay) {
    const li = document.createElement("li");
    if (replay) li.className = "replay";
    else if (record.tone) li.className = record.tone;
    li.appendChild(document.createTextNode((replay ? hhmm(record.when) + " replay" : hms(record.when)) + " \u00b7 "));
    if (record.kind === "mention") {
      li.appendChild(el("b", "", record.fromLabel));
      li.appendChild(document.createTextNode(" mentions "));
      li.appendChild(el("b", "", record.toLabel));
    } else {
      li.appendChild(el("b", "", record.fromLabel + " \u2192 " + record.toLabel));
    }
    const ment = mentionsText(record);
    if (ment) li.appendChild(document.createTextNode(" \u00b7 " + ment));
    if (record.text) li.appendChild(document.createTextNode(" \u00b7 " + record.text));
    return li;
  }

  function recentDrops(now) {
    const cutoff = now - CALM_S * 1000;
    dropTimes = dropTimes.filter(function (t) { return t >= cutoff; });
    return dropTimes.length;
  }

  function renderTicker() {
    const ul = document.getElementById("ticker");
    if (!ul) return;
    clear(ul);
    const now = Date.now();
    const extra = recentDrops(now);
    if (calmNow(now) && replaying && replayRows.length) {
      ul.appendChild(el("li", "", REPLAY_LABEL));
      replayRows.slice(0, 2).forEach(function (row) { ul.appendChild(tickerItem(row, true)); });
      return;
    }
    if (calmNow(now)) {
      // Before any live event, the newest 24 h fibre is the last message.
      let last = lastWhen;
      ((current && current.graph && current.graph.fibres) || []).forEach(function (f) {
        const t = Date.parse(f.last);
        if (!isNaN(t) && (!last || t > last.getTime())) last = new Date(t);
      });
      ul.appendChild(el("li", "", last ? "No live messages. Last at " + hhmm(last) + "." : "No live messages."));
      return;
    }
    const shown = log.slice(0, extra > 0 ? 2 : 3);
    let i;
    for (i = 0; i < shown.length; i++) ul.appendChild(tickerItem(shown[i], false));
    if (extra > 0) ul.appendChild(el("li", "", "and " + extra + " more in the last minute"));
  }

  function pushNode(id, record) {
    if (!Object.prototype.hasOwnProperty.call(nodeLog, id)) nodeLog[id] = [];
    nodeLog[id].unshift(record);
    if (nodeLog[id].length > 5) nodeLog[id].length = 5;
  }

  function remember(record) {
    log.unshift(record);
    if (log.length > 40) log.length = 40;
    pushNode(record.from, record);
    if (record.to !== record.from) pushNode(record.to, record);
    lastEventAt = Date.now();
    lastWhen = record.when;
  }

  function syncOffline() {
    const down = esDown || pollDown;
    if (down !== offline) {
      offline = down;
      queue = [];
      if (offline) stopReplay();
    }
    refreshProv();
    refreshLegend();
    renderTicker();
  }

  // SVG cannot gradient along a path, so a trail is TRAIL_LENGTHS.length copies of it, each dashed to end at the head.
  function makeTrails(parent, d, cls, attrs) {
    return TRAIL_LENGTHS.map(function (_len, i) {
      const a = { d: d, "class": "trail t" + i + " " + cls };
      Object.keys(attrs).forEach(function (key) { a[key] = attrs[key]; });
      return svgEl("path", a, parent);
    });
  }

  function setTrails(trails, head, total) {
    trails.forEach(function (t, i) {
      const len = Math.min(TRAIL_LENGTHS[i], Math.max(0, head));
      // Dash covers [head - len, head]; the gap is longer than the path so it never repeats. Clamped at the path start.
      t.setAttribute("stroke-dasharray", len.toFixed(2) + " " + (total + TRAIL_LENGTHS[i]).toFixed(2));
      t.setAttribute("stroke-dashoffset", (len - Math.max(0, head)).toFixed(2));
    });
  }

  function ease(k) {
    if (k < 0.5) return 2 * k * k;
    return 1 - Math.pow(-2 * k + 2, 2) / 2;
  }

  function dropDot(id) {
    if (!id) return;
    [liveG, replayG, railG].forEach(function (g) {
      if (!g) return;
      const dots = g.querySelectorAll("circle.pulse, path.trail");
      let i;
      for (i = 0; i < dots.length; i++) {
        if (dots[i].getAttribute("data-flight") === id) dots[i].remove();
      }
    });
  }

  function finish(path) {
    if (!path || path.getAttribute("data-done") === "1") return;
    path.setAttribute("data-done", "1");
    dropDot(path.getAttribute("data-flight"));
    if (path.parentNode) path.remove();
    if (path.classList.contains("replay")) {
      replayFlight = Math.max(0, replayFlight - 1);
      return;
    }
    inFlight = Math.max(0, inFlight - 1);
    if (!drawing) pump();
  }

  function ping(id) {
    const g = nodeEls[id];
    if (g && !narrow) {  // svg#g is hidden on narrow; getBBox on it throws in Firefox
      g.classList.remove("spoke");
      void g.getBBox();
      g.classList.add("spoke");
    }
    if (!narrow) return;
    const box = document.getElementById("focus-box");
    const row = document.getElementById("partner-" + id);
    const target = box && box.getAttribute("data-id") === id ? box : row;
    if (!target) return;
    target.classList.remove("spoke");
    void target.offsetWidth;
    target.classList.add("spoke");
  }

  function talkClass(toneName, dim, extra) {
    let name = extra || "talk";
    if (toneName) name += " " + toneName;
    if (dim) name += " dim";
    return name;
  }

  // replay: the quiet replay of the last hour; same flight, no ping, own in-flight count.
  function startLink(record, replay) {
    if (replay) replayFlight += 1;
    else inFlight += 1;
    flightSeq += 1;
    const id = String(flightSeq);
    const dim = unrelated(record.from, record.to);
    const useRail = !replay && narrow;
    let group = liveG;
    if (replay) group = replayG;
    else if (useRail) group = railG;
    if (!group) {
      if (replay) replayFlight = Math.max(0, replayFlight - 1);
      else inFlight = Math.max(0, inFlight - 1);
      return;
    }
    const toneName = replay ? "" : record.tone;
    const path = svgEl("path", {
      d: useRail ? railD(record.from, record.to) : linkPath(record.from, record.to),
      "class": replay ? talkClass("", dim, "talk replay")
        : reduceMotion ? talkClass(toneName, dim, "talk on reduce") : talkClass(toneName, dim, "talk"),
      "data-from": record.from,
      "data-to": record.to,
      "data-flight": id
    }, group);
    if (reduceMotion) {
      setTimeout(function () { finish(path); }, 1000);
      return;
    }
    requestAnimationFrame(function () {
      if (path.getAttribute("data-done") !== "1") path.classList.add("on");
    });
    const len = path.getTotalLength();
    // A failed job is the red dashed line alone: red never carries a comet.
    const comet = toneName !== "red";
    // Replay trails stay ink: only live trails carry a tone.
    const tone = replay || !toneName ? "" : " " + toneName;
    const trails = !comet ? [] : makeTrails(group, path.getAttribute("d"),
      (replay ? "replay" : "live") + tone + (dim ? " dim" : ""), {
        "data-from": record.from,
        "data-to": record.to,
        "data-flight": id
      });
    const dot = !comet ? null : svgEl("circle", {
      "class": talkClass(toneName, dim, replay ? "pulse replay" : "pulse"),
      r: replay ? REPLAY_DOT_R : LIVE_DOT_R,
      "data-from": record.from,
      "data-to": record.to,
      "data-flight": id
    }, group);
    const origin = path.getPointAtLength(0);
    if (dot) {
      dot.setAttribute("cx", String(origin.x));
      dot.setAttribute("cy", String(origin.y));
    }
    setTrails(trails, 0, len);
    const start = performance.now() + 250;
    const dur = 900 + len * 1.1;
    function step(now) {
      if (!path.isConnected || path.getAttribute("data-done") === "1") {
        dropDot(id);
        return;
      }
      const k = Math.max(0, Math.min(1, (now - start) / dur));
      const cur = path.getTotalLength();
      const pt = path.getPointAtLength(ease(k) * cur);
      if (dot) {
        dot.setAttribute("cx", String(pt.x));
        dot.setAttribute("cy", String(pt.y));
      }
      setTrails(trails, ease(k) * cur, cur);
      if (k < 1) {
        requestAnimationFrame(step);
        return;
      }
      dropDot(id);
      if (!reduceMotion && !replay) ping(record.to);
      path.classList.add("off");
      path.addEventListener("transitionend", function (ev) {
        if (ev.propertyName === "opacity") finish(path);
      });
      setTimeout(function () { finish(path); }, 1400);
    }
    requestAnimationFrame(step);
  }

  function pump() {
    if (drawing || offline || document.hidden || calmNow(Date.now())) return;
    while (inFlight < MAX_IN_FLIGHT && queue.length) {
      const next = queue[0];
      if (narrow) {
        if (!touchesFocus(next.from, next.to)) {
          queue.shift();
          continue;
        }
        queue.shift();
        startLink(next, false);
        continue;
      }
      if (!liveG || !hasPos(next.from) || !hasPos(next.to)) {
        queue.shift();
        continue;
      }
      queue.shift();
      startLink(next, false);
    }
  }

  function enqueue(record) {
    // Narrow: only a live event that touches the focus gets a dot. Everything else stays in the ticker.
    if (narrow && (reduceMotion || !touchesFocus(record.from, record.to))) return;
    queue.push(record);
    const now = Date.now();
    while (queue.length > MAX_QUEUED) {
      queue.shift();
      dropTimes.push(now);
    }
    pump();
  }

  function accept(event) {
    if (document.hidden || !event) return;
    const from = typeof event.from === "string" ? event.from : "";
    const to = typeof event.to === "string" ? event.to : "";
    if (!from || !to || from === to) return;
    if (narrow) {
      if (!knownNodes(from, to)) return;
    } else if (!hasPos(from) || !hasPos(to)) return;
    const record = {
      id: typeof event.id === "string" ? event.id : "",
      when: new Date(),
      mentions: [],
      from: from,
      to: to,
      fromLabel: labelOf(from),
      toLabel: labelOf(to),
      kind: typeof event.kind === "string" ? event.kind : "",
      tone: linkTone(event),
      text: eventText(event)
    };
    if (!fold(record)) remember(record);
    stopReplay();
    if (!offline) enqueue(record);
    renderTicker();
    refreshProv();
    refreshLegend();
    if (current) panel();
  }

  function onSSE(ev) {
    let data = null;
    try {
      data = JSON.parse(ev.data);
    } catch (err) {
      return;
    }
    accept(data);
  }

  function onOpen() {
    const back = esDown;
    esDown = false;
    if (back) queue = [];
    syncOffline();
  }

  function onError() {
    esDown = true;
    syncOffline();
  }

  function connectEvents() {
    stream = new EventSource("/api/events");
    stream.onopen = onOpen;
    stream.onerror = onError;
    let i;
    for (i = 0; i < EVENT_KINDS.length; i++) {
      stream.addEventListener(EVENT_KINDS[i], onSSE);
    }
  }

  // Mean gap for one pair. Busiest pair fires every AMBIENT_BUSIEST_S; a pair with half that total waits twice as long.
  // Null when there is nothing to show, so a zero pair never gets a timer.
  function ambientInterval(total, busiest) {
    const n = num(total);
    const hi = num(busiest);
    if (n < 1 || hi < 1) return null;
    return AMBIENT_BUSIEST_S * 1000 * (hi / n);
  }

  // ---- ambient flow: the last 24 h, one timer per pair, bottom layer ----
  function ambInitial(st, t) {
    st.next = [t + Math.random() * st.interval];
  }

  function factoryOf(lane) {
    if (lane === "grok" || lane.indexOf("grok-") === 0) return "factory-grok";
    if (lane.indexOf("queue") === 0) return "factory-queue";
    if (lane === "gemini") return "factory-gemini";
    if (lane === "zcode") return "factory-zcode";
    return "";
  }

  function pipeNodes() {
    const lanes = current && current.bus && current.bus.lanes;
    const out = [];
    if (!lanes) return out;
    Object.keys(lanes).forEach(function (name) {
      const row = lanes[name];
      const node = factoryOf(name);
      if (!row || (!row.parked && !row.failing) || !node || !hasPos(node)) return;
      if (out.indexOf(node) < 0) out.push(node);
    });
    return out;
  }

  // The red wire is factory-queue to its broken partner, either direction. Same rule as drawFibres.
  // A parked or failing lane draws the same broken class from its factory node to vp.
  function isBrokenPair(from, to) {
    const b = ambBroken;
    if (b && ((from === "factory-queue" && to === b) || (to === "factory-queue" && from === b))) return true;
    const nodes = pipeNodes();
    let i;
    for (i = 0; i < nodes.length; i++) {
      if ((from === nodes[i] && to === "vp") || (to === nodes[i] && from === "vp")) return true;
    }
    return false;
  }

  function dropAmb(d, quick) {
    const at = ambDots.indexOf(d);
    if (at >= 0) ambDots.splice(at, 1);
    d.dot.remove();
    d.trails.forEach(function (t) { t.remove(); });
    if (quick || !d.arc) {
      d.path.remove();
      return;
    }
    d.path.classList.remove("on");
    setTimeout(function () { d.path.remove(); }, 600);
  }

  function clearAmb() {
    ambDots = [];
    if (ambG) clear(ambG);
  }

  function syncAmbient(graph) {
    const list = [];
    let busiest = 0;
    const jobq = nodeById(graph.nodes, "factory-queue");
    ambBroken = jobq && jobq.state === "failed" ? brokenPartner() : null;
    (graph.fibres || []).forEach(function (f) {
      if (!f || !f.from || !f.to || f.from === f.to) return;
      const total = num(f.total_messages) + num(f.total_mentions);
      if (total < 1 || !hasPos(f.from) || !hasPos(f.to) || isBrokenPair(f.from, f.to)) return;
      if (total > busiest) busiest = total;
      list.push({ key: f.from + "|" + f.to, from: f.from, to: f.to, total: total });
    });
    const t = performance.now();
    const seen = {};
    list.forEach(function (p) {
      const interval = ambientInterval(p.total, busiest);
      if (interval === null) return;
      seen[p.key] = true;
      if (!Object.prototype.hasOwnProperty.call(amb, p.key)) {
        amb[p.key] = { from: p.from, to: p.to, interval: interval, next: [] };
        ambInitial(amb[p.key], t);
        return;
      }
      amb[p.key].interval = interval;
    });
    // Whatever is no longer listed, or now sits on the red wire, goes with its in-flight dots.
    Object.keys(amb).forEach(function (key) {
      if (seen[key] && !isBrokenPair(amb[key].from, amb[key].to)) return;
      delete amb[key];
    });
    ambDots.slice().forEach(function (d) {
      if (!amb[d.key] || isBrokenPair(d.from, d.to)) dropAmb(d, true);
    });
  }

  function placeAmb(d, dist, total) {
    const pt = d.path.getPointAtLength(dist);
    d.dot.setAttribute("cx", String(pt.x));
    d.dot.setAttribute("cy", String(pt.y));
    const trail = d.trails[0];
    if (!trail) return;
    const len = Math.min(AMBIENT_TRAIL, Math.max(0, dist));
    trail.setAttribute("stroke-dasharray", len.toFixed(2) + " " + (total + AMBIENT_TRAIL).toFixed(2));
    trail.setAttribute("stroke-dashoffset", (len - Math.max(0, dist)).toFixed(2));
  }

  function startAmb(st, key) {
    if (!ambG || ambDots.length >= AMBIENT_MAX) return;
    if (!hasPos(st.from) || !hasPos(st.to) || isBrokenPair(st.from, st.to)) return;
    const arc = !!(current && current.graph && sameLane(current.graph, st.from, st.to));
    const dim = unrelated(st.from, st.to);
    const dAttr = linkPath(st.from, st.to);
    const path = svgEl("path", {
      d: dAttr,
      "class": (arc ? "amb-path amb-arc" : "amb-path") + (dim ? " dim" : ""),
      "data-from": st.from,
      "data-to": st.to
    }, ambG);
    const trail = svgEl("path", {
      d: dAttr,
      "class": "trail amb" + (dim ? " dim" : ""),
      "data-from": st.from,
      "data-to": st.to
    }, ambG);
    const dot = svgEl("circle", {
      "class": dim ? "amb-dot dim" : "amb-dot",
      r: AMBIENT_DOT_R,
      "data-from": st.from,
      "data-to": st.to,
      "data-pair": key
    }, ambG);
    const d = { key: key, from: st.from, to: st.to, arc: arc, path: path, trails: [trail], dot: dot, start: performance.now() };
    ambDots.push(d);
    placeAmb(d, 0, path.getTotalLength());
    if (arc) {
      requestAnimationFrame(function () {
        if (ambDots.indexOf(d) >= 0) path.classList.add("on");
      });
    }
  }

  function ambFrame(t) {
    requestAnimationFrame(ambFrame);
    if (document.hidden || reduceMotion || !ambG || narrow) {
      if (ambDots.length) clearAmb();
      return;
    }
    Object.keys(amb).forEach(function (key) {
      const st = amb[key];
      st.next.forEach(function (at, i) {
        if (t < at) return;
        // Jitter each scheduled start; at the cap the start is skipped, never queued.
        st.next[i] = t + st.interval * (1 - AMBIENT_JITTER + 2 * AMBIENT_JITTER * Math.random());
        startAmb(st, key);
      });
    });
    ambDots.slice().forEach(function (d) {
      const dist = (t - d.start) / 1000 * AMBIENT_SPEED;
      const total = d.path.getTotalLength();
      if (dist >= total) dropAmb(d, false);
      else placeAmb(d, dist, total);
    });
  }

  // ---- idle replay: the last hour, 10x, after 60 s with no live event ----
  function replayRecord(item) {
    const t = typeof item.ts === "number" ? item.ts : Date.parse(item.ts);
    return {
      id: typeof item.id === "string" ? item.id : "",
      when: new Date(isNaN(t) ? Date.now() : t),
      mentions: [],
      from: item.from,
      to: item.to,
      fromLabel: labelOf(item.from),
      toLabel: labelOf(item.to),
      kind: typeof item.kind === "string" ? item.kind : "",
      text: eventText(item)
    };
  }

  // Same folding as live rows. recent has no ids on older servers, so fall back to same poster and time.
  function foldReplay(record) {
    if (record.kind !== "mention") return false;
    const pid = parentId(record.id);
    let i;
    for (i = 0; i < replayRows.length; i++) {
      const row = replayRows[i];
      if (row.kind === "mention") continue;
      const same = pid ? row.id === pid : row.from === record.from && row.when.getTime() === record.when.getTime();
      if (!same) continue;
      if (row.mentions.indexOf(record.toLabel) < 0) row.mentions.push(record.toLabel);
      return true;
    }
    return false;
  }

  function replayList() {
    const recent = current && current.graph && Array.isArray(current.graph.recent) ? current.graph.recent : [];
    return recent.filter(function (item) {
      return item && typeof item.from === "string" && typeof item.to === "string" &&
        item.from !== item.to && hasPos(item.from) && hasPos(item.to);
    });
  }

  function stopReplay() {
    const was = replaying;
    replayGen += 1;
    clearTimeout(replayTimer);
    replaying = false;
    replayRows = [];
    replayFlight = 0;
    if (replayG) {
      const paths = replayG.querySelectorAll("path.talk");
      let i;
      for (i = 0; i < paths.length; i++) paths[i].setAttribute("data-done", "1");
      clear(replayG);
    }
    if (was) {
      refreshProv();
      renderTicker();
    }
  }

  function timeOf(item) {
    const t = typeof item.ts === "number" ? item.ts : Date.parse(item.ts);
    return isNaN(t) ? null : t;
  }

  function startReplay() {
    stopReplay();
    const gen = replayGen;
    const list = replayList();
    if (!list.length) return;
    replaying = true;
    let i = 0;
    function emit() {
      if (gen !== replayGen) return;
      const item = list[i];
      const record = replayRecord(item);
      if (!foldReplay(record)) {
        replayRows.unshift(record);
        if (replayRows.length > 40) replayRows.length = 40;
      }
      if (replayFlight < REPLAY_MAX && replayG && !narrow) startLink(record, true);
      renderTicker();
      i += 1;
      if (i >= list.length) {
        // Hour over: look again at the latest list after a short rest.
        replayTimer = setTimeout(function () {
          if (gen === replayGen) startReplay();
        }, REPLAY_RESTART_S * 1000);
        return;
      }
      const a = timeOf(item);
      const b = timeOf(list[i]);
      const gap = a === null || b === null ? 0 : Math.max(0, (b - a) / REPLAY_SPEEDUP);
      replayTimer = setTimeout(emit, Math.min(gap, REPLAY_GAP_CAP_S * 1000));
    }
    refreshProv();
    emit();
  }

  function maybeReplay() {
    if (narrow) return;
    if (replaying || document.hidden || offline || reduceMotion || !current) return;
    // Idle counts from the newest real event (live, recent or fibre), and from a return to a hidden tab.
    if (Date.now() - Math.max(lastEventAt, lastRealAt, visibleSince) < REPLAY_IDLE_S * 1000) return;
    startReplay();
  }

  // Hidden tab: every layer stops and drops. Nothing queues, nothing catches up on return.
  function haltLive() {
    queue = [];
    [liveG, railG].forEach(function (g) {
      if (!g) return;
      const paths = g.querySelectorAll("path.talk");
      let i;
      for (i = 0; i < paths.length; i++) paths[i].setAttribute("data-done", "1");
      clear(g);
    });
    inFlight = 0;
  }

  function dropMotion() {
    clearAmb();
    stopReplay();
    haltLive();
  }

  function paintSetup(status) {
    const slot = document.getElementById("setup-banner");
    if (!slot) return;
    clear(slot);
    const missing = status && Array.isArray(status.missing) ? status.missing : [];
    if (!status || status.lock_age === null || status.lock_age === undefined || missing.length === 0) {
      slot.hidden = true;
      return;
    }
    const n = missing.length;
    const word = n === 1 ? "tool" : "tools";
    // The link opens /api/setup/dry-run, which lists the missing tools and runs nothing, so "Install" promised too much; Signal states are a shape plus a word.
    const mark = document.createElement("span");
    mark.className = "setup-mark";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = "\u25c7 ";
    const lead = document.createElement("span");
    lead.textContent = "This Mac is missing " + n + " " + word + " \u00b7 ";
    const link = document.createElement("a");
    link.setAttribute("href", "/api/setup/dry-run");
    link.textContent = "See what\u2019s missing";
    slot.appendChild(mark);
    slot.appendChild(lead);
    slot.appendChild(link);
    slot.hidden = false;
  }

  function loadSetup() {
    const slot = document.getElementById("setup-banner");
    if (!slot) return;
    fetch("/api/setup", { cache: "no-store", credentials: "omit" }).then(function (res) {
      if (!res.ok) throw new Error(String(res.status));
      return res.json();
    }).then(paintSetup).catch(function () {
      clear(slot);
      slot.hidden = true;
    });
  }

  function heartbeat() {
    const now = Date.now();
    // ponytail: once the page has been quiet for 60s, drop the unstarted queue so calm stays still.
    if (calmNow(now) && queue.length) queue = [];
    recentDrops(now);
    maybeReplay();
    renderTicker();
    refreshProv();
    refreshLegend();
    pump();
  }

  if (motionQuery) {
    const onMotion = function () {
      reduceMotion = motionQuery.matches;
      refreshLegend();
      if (!reduceMotion) return;
      stopReplay();
      clearAmb();
      if (!narrow) return;
      queue = [];
      clearRailFlights();
    };
    if (typeof motionQuery.addEventListener === "function") motionQuery.addEventListener("change", onMotion);
    else if (typeof motionQuery.addListener === "function") motionQuery.addListener(onMotion);
  }

  svg.addEventListener("click", function (ev) {
    // Edge clicks stop propagation, so anything reaching here (space or a node) clears the caption.
    edgeKey = null;
    const info = document.getElementById("edge-info");
    if (info) info.textContent = "";
    const hit = ev.target && ev.target.closest && ev.target.closest(".node");
    if (!hit && sel) select(sel);
  });
  document.getElementById("theme").addEventListener("click", toggleTheme);
  const showEl = document.getElementById("show");
  if (showEl) showEl.addEventListener("change", function () {
    chooseFocus(this.value);
  });
  if (narrowQuery) {
    const onNarrow = function () {
      const next = !!narrowQuery.matches;
      if (next === narrow) return;
      narrow = next;
      if (next) stopReplay();
      haltLive();
      if (!next) {
        // Text measured while the svg was hidden is stale. Redraw first, then re-seed the ambient timers.
        if (current && current.graph) draw(current.graph);
        // Back to wide: re-seed ambient timers, or every pair fires at once (same as after a hidden tab).
        const t = performance.now();
        Object.keys(amb).forEach(function (key) { ambInitial(amb[key], t); });
        return;
      }
      if (!focusId && current && current.graph && sel && nodeById(current.graph.nodes, sel)) focusId = sel;
      if (current && current.graph) renderFocus(current.graph);
    };
    if (typeof narrowQuery.addEventListener === "function") narrowQuery.addEventListener("change", onNarrow);
    else if (typeof narrowQuery.addListener === "function") narrowQuery.addListener(onNarrow);
  }
  // A new window size redraws a wide graph at its new width, one frame later however many resize events came.
  let wideFrame = 0;
  window.addEventListener("resize", function () {
    if (wideFrame) return;
    wideFrame = requestAnimationFrame(function () {
      wideFrame = 0;
      relayout();
    });
  });
  let railFrame = 0;
  // ponytail: 150ms window debounce. A container change that is not a window resize waits for the next repaint.
  let needsRowTimer = 0;
  window.addEventListener("resize", function () {
    clearTimeout(needsRowTimer);
    needsRowTimer = setTimeout(function () {
      const host = document.getElementById("needs-list");
      if (host) fitNeedsRow(host);
    }, 150);
  });
  window.addEventListener("resize", function () {
    if (!narrow || railFrame) return;
    railFrame = requestAnimationFrame(function () {
      railFrame = 0;
      if (narrow && current && current.graph) layoutRail();
    });
  });
  document.addEventListener("keydown", function (ev) {
    if (roadmapKey(ev)) return;
    const rail = document.getElementById("rail");
    if (ev.key === "Escape" && rail && rail.classList.contains("is-open")) {
      setRail(false);
      return;
    }
    if (ev.key === "Escape" && sel) select(sel);
    if ((ev.metaKey || ev.ctrlKey) && (ev.key === "k" || ev.key === "K")) {
      const jump = document.getElementById("jump");
      if (!jump) return;
      ev.preventDefault();
      jump.focus({ preventScroll: true });
    }
  });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      dropMotion();
      return;
    }
    visibleSince = Date.now();
    const t = performance.now();
    Object.keys(amb).forEach(function (key) { ambInitial(amb[key], t); });
    pump();
  });
  function currentView() {
    let raw = "";
    if (window.location && typeof window.location.hash === "string") raw = window.location.hash.replace(/^#/, "");
    if (raw === "now" || raw === "agents" || raw === "work" || raw === "spend" || raw === "roadmap" || raw === "folders") return raw;
    return "now";
  }

  function showView() {
    const name = currentView();
    const nodes = document.querySelectorAll("[data-view]");
    let i;
    for (i = 0; i < nodes.length; i++) {
      const list = nodes[i].getAttribute("data-view").split(" ");
      nodes[i].hidden = list.indexOf(name) === -1;
    }
    const items = document.querySelectorAll("a.nav-item");
    for (i = 0; i < items.length; i++) {
      if (items[i].getAttribute("href") === "#" + name) items[i].setAttribute("aria-current", "page");
      else items[i].removeAttribute("aria-current");
    }
    const tabs = document.querySelectorAll(".tabbar a, .tabbar button");
    for (i = 0; i < tabs.length; i++) {
      const href = tabs[i].getAttribute("href");
      const on = href ? href === "#" + name : (name === "spend" || name === "roadmap" || name === "folders");
      if (on) tabs[i].setAttribute("aria-current", "page");
      else tabs[i].removeAttribute("aria-current");
    }
    setRail(false);
    if (jumpKeepsOpen) jumpKeepsOpen = false;
    else if (sel) select(sel);
    if (name === "now") relayout();
    paintFirst(current);
  }

  function setRail(open) {
    const rail = document.getElementById("rail");
    const scrim = document.getElementById("scrim");
    const btn = document.getElementById("rail-open");
    const more = document.getElementById("tab-more");
    if (rail) rail.classList.toggle("is-open", !!open);
    if (scrim) scrim.hidden = !open;
    if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (more) more.setAttribute("aria-expanded", open ? "true" : "false");
  }

  // ponytail: Enter takes the first label match. Arrow-key highlight waits until a lab is too long to scan.
  function jumpHits(q) {
    const nodes = current && current.graph && current.graph.nodes;
    const hits = [];
    const needle = String(q || "").trim().toLowerCase();
    if (!nodes || !needle) return hits;
    Object.keys(nodes).forEach(function (id) {
      const label = labelOf(id);
      if (label.toLowerCase().indexOf(needle) !== -1) hits.push({ id: id, label: label });
    });
    hits.sort(function (a, b) {
      if (a.label < b.label) return -1;
      if (a.label > b.label) return 1;
      return 0;
    });
    return hits;
  }

  function paintJump() {
    const jump = document.getElementById("jump");
    const list = document.getElementById("jump-list");
    if (!jump || !list) return;
    const hits = jumpHits(jump.value);
    clear(list);
    list.hidden = hits.length === 0;
    hits.forEach(function (hit) {
      const li = document.createElement("li");
      const btn = el("button", "", hit.label);
      btn.type = "button";
      btn.addEventListener("click", function () { goAgent(hit.id); });
      li.appendChild(btn);
      list.appendChild(li);
    });
  }

  function goAgent(id) {
    const jump = document.getElementById("jump");
    const list = document.getElementById("jump-list");
    if (jump) jump.value = "";
    if (list) { clear(list); list.hidden = true; }
    if (sel === id) sel = null;
    if (window.location && window.location.hash !== "#now") {
      jumpKeepsOpen = true;
      window.location.hash = "#now";
    } else showView();
    select(id);
  }

  function wireShell() {
    const openBtn = document.getElementById("rail-open");
    const closeBtn = document.getElementById("rail-close");
    const scrim = document.getElementById("scrim");
    const jump = document.getElementById("jump");
    const nav = document.querySelector(".nav");
    const kbd = document.querySelector(".jump kbd");
    if (kbd && navigator.platform.indexOf("Mac") === -1) kbd.textContent = "Ctrl K";
    const moreBtn = document.getElementById("tab-more");
    if (openBtn) openBtn.addEventListener("click", function () { setRail(true); });
    if (moreBtn) moreBtn.addEventListener("click", function () { setRail(true); });
    if (closeBtn) closeBtn.addEventListener("click", function () { setRail(false); });
    if (scrim) scrim.addEventListener("click", function () { setRail(false); });
    if (nav) nav.addEventListener("click", function () { setRail(false); });
    if (jump) {
      jump.addEventListener("input", paintJump);
      jump.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape") {
          ev.stopPropagation();
          jump.value = "";
          paintJump();
          return;
        }
        if (ev.key !== "Enter") return;
        const hits = jumpHits(jump.value);
        if (!hits.length) return;
        ev.preventDefault();
        goAgent(hits[0].id);
      });
    }
    window.addEventListener("hashchange", showView);
    showView();
  }

  requestAnimationFrame(ambFrame);
  showThemeIcon();
  renderTicker();
  refreshLegend();
  connectEvents();
  tick();
  setInterval(tick, 5000);
  setInterval(heartbeat, 1000);
  wireShell();
  wireCopy();
  loadSetup();
  setInterval(loadSetup, SETUP_POLL_MS);
})();

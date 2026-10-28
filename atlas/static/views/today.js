/**
 * Today view. Summaries are untrusted: every field is textContent.
 * ctx.api(path) returns JSON. The shell adds the page nonce.
 */
// One scope per view: every view loads into the same page, so no top-level name may leak (body left unindented on purpose).
(function () {
var GH_UNREADABLE = "◇ GitHub couldn't be read, so PRs and CI aren't shown. If gh isn't signed in, run gh auth login in a terminal on this computer. This fills in on the next refresh.";
var DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
var STATE = {
  sent: ["○", "Sent", "q"],
  picked_up: ["◐", "Picked up", "q"],
  done: ["●", "Done", "q"],
  failed: ["▲", "Failed", "fail"],
  opened: ["◐", "Opened", "q"],
  open: ["◐", "Opened", "q"],
  merged: ["●", "Merged", "q"]
};

var repoFilter = "";
var seq = 0;
var poll = 0;
var visBound = false;
var liveRoot = null;
var liveCtx = null;
var drawnText = "";

var CLOCK = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23"
});

function toolName(id) {
  if (id == null || id === "") return "";
  var fn = window.HP && window.HP.toolName;
  if (typeof fn === "function") return fn(id);
  return String(id);
}

function responseText(data) {
  return JSON.stringify(data, function (key, value) {
    if (key === "generated") return undefined;
    return value;
  });
}

function clock(ts) {
  try {
    var d = new Date(ts);
    if (isNaN(d.getTime())) return "";
    return CLOCK.format(d);
  } catch (e) {
    return "";
  }
}

function whenLabel(ts, generated) {
  var spoken = "";
  if (window.HP && typeof window.HP.age === "function") spoken = window.HP.age(ts, generated);
  if (spoken === "yesterday") return "Yesterday";
  return clock(ts);
}

function eyebrow(ts) {
  var d = new Date(ts || "");
  if (isNaN(d.getTime())) return "Today";
  return "Today · " + DAYS[d.getDay()] + " " + clock(ts);
}


function rowsOf(block) {
  var rows = block && Array.isArray(block.rows) ? block.rows : [];
  var out = [];
  for (var i = 0; i < rows.length; i++) {
    if (rows[i] && typeof rows[i] === "object") out.push(rows[i]);
  }
  return out;
}

function isPr(row) {
  return row.kind === "pr";
}

function isJob(row) {
  return row.kind !== "pr";
}

function visibleRows(block, showPrs) {
  var rows = rowsOf(block);
  if (showPrs) return rows;
  var out = [];
  for (var i = 0; i < rows.length; i++) {
    if (!isPr(rows[i])) out.push(rows[i]);
  }
  return out;
}

function countJobs(blocks, state) {
  var n = 0;
  for (var i = 0; i < blocks.length; i++) {
    var rows = rowsOf(blocks[i]);
    for (var r = 0; r < rows.length; r++) {
      if (isJob(rows[r]) && rows[r].state === state) n += 1;
    }
  }
  return n;
}

function countPrs(blocks, states) {
  var n = 0;
  for (var i = 0; i < blocks.length; i++) {
    var rows = rowsOf(blocks[i]);
    for (var r = 0; r < rows.length; r++) {
      if (isPr(rows[r]) && states.indexOf(rows[r].state) >= 0) n += 1;
    }
  }
  return n;
}

function anyRow(blocks) {
  for (var i = 0; i < blocks.length; i++) {
    if (rowsOf(blocks[i]).length) return true;
  }
  return false;
}

function prBit(n, tail) {
  return n + " PR" + (n === 1 ? "" : "s") + " " + tail;
}

function blockBits(block, showPrs) {
  var rows = rowsOf(block);
  var failed = 0;
  var done = 0;
  var open = 0;
  var merged = 0;
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i];
    if (isJob(row) && row.state === "failed") failed += 1;
    else if (isJob(row) && row.state === "done") done += 1;
    else if (showPrs && isPr(row) && (row.state === "opened" || row.state === "open")) open += 1;
    else if (showPrs && isPr(row) && row.state === "merged") merged += 1;
  }
  return { failed: failed, done: done, open: open, merged: merged };
}

function nWord(n, one, many) {
  return n + " " + (n === 1 ? one : many);
}

function countNum(data, key) {
  var counts = data && data.counts;
  var n = counts && counts[key];
  return typeof n === "number" && isFinite(n) ? n : 0;
}

function subLine(data) {
  return nWord(countNum(data, "messages_today"), "message", "messages") + " today · "
    + nWord(countNum(data, "tools_connected"), "tool", "tools") + " connected · "
    + nWord(countNum(data, "repos"), "repo", "repos");
}

function blockSum(block, showPrs) {
  var bits = blockBits(block, showPrs);
  var words = [];
  if (bits.failed) words.push(bits.failed + " failed");
  if (bits.done) words.push(bits.done + " done");
  if (showPrs && bits.merged) words.push(prBit(bits.merged, "merged"));
  if (showPrs && bits.open) words.push(prBit(bits.open, "open"));
  return words.join(" · ");
}

function titleText(blocks, showPrs) {
  var done = countJobs(blocks, "done");
  var line = done + (done === 1 ? " job" : " jobs") + " done and " + countJobs(blocks, "failed") + " failed today.";
  if (showPrs) {
    line += " " + prBit(countPrs(blocks, ["merged"]), "merged") + ", " + countPrs(blocks, ["opened", "open"]) + " open.";
  }
  return line;
}

function checkLines(row, showPrs) {
  var c = row && row.checks;
  if (!c || typeof c !== "object") return [];
  var out = [];
  if (c.tests === "pass") {
    var pass = "● Tests pass";
    if (typeof c.passed === "number") pass += " · " + c.passed;
    out.push(["", pass]);
  } else if (c.tests === "fail") {
    var fail = "▲ Tests fail";
    if (typeof c.failed === "number" && typeof c.of === "number") fail += " · " + c.failed + " of " + c.of;
    out.push(["bad", fail]);
  }
  if (c.review === "pass") out.push(["", "● Reviewer: approve"]);
  else if (c.review === "none") out.push(["none", "○ No review"]);
  if (showPrs) {
    if (c.ci === "pass") out.push(["", "● CI passed"]);
    else if (c.ci === "pending") out.push(["", "◐ CI running"]);
    else if (c.ci === "fail") out.push(["bad", "▲ CI failed"]);
    else if (c.ci === "none") out.push(["none", "○ No CI workflow"]);
  }
  return out;
}

function whatText(row) {
  if (isPr(row)) {
    var n = row.pr == null ? "" : String(row.pr);
    var title = row.summary == null ? "" : String(row.summary);
    if (n && title) return "PR #" + n + " · " + title;
    if (n) return "PR #" + n;
    return title;
  }
  return row.summary == null ? "" : String(row.summary);
}

function byLine(row) {
  if (row && row.by_you) return "Merged by you";
  var member = row && row.member ? String(row.member) : "";
  var tool = toolName(row && row.tool);
  if (member && tool) return member + " · " + tool;
  return member || tool;
}

function el(parent, tag, cls) {
  var node = document.createElement(tag);
  if (cls) node.className = cls;
  if (parent) parent.appendChild(node);
  return node;
}

function text(parent, tag, cls, value) {
  var node = el(parent, tag, cls);
  node.textContent = value == null ? "" : String(value);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function addState(parent, row) {
  var info = STATE[row.state] || ["", row.state ? String(row.state) : "", "q"];
  var node = el(parent, "span", "st " + info[2]);
  text(node, "span", "m", info[0]);
  node.appendChild(document.createTextNode(info[1] ? " " + info[1] : ""));
  return node;
}

function addChecks(parent, row, showPrs) {
  var lines = checkLines(row, showPrs);
  var wrap = el(parent, "span", "chks");
  for (var i = 0; i < lines.length; i++) {
    text(wrap, "span", "ck" + (lines[i][0] ? " " + lines[i][0] : ""), lines[i][1]);
  }
  return wrap;
}

function link(parent, cls, label, href) {
  var node = text(parent, "a", cls, label);
  node.href = href;
  node.setAttribute("href", href);
  return node;
}

function appendGhOff(note, labDir) {
  var dir = labDir ? String(labDir) : "~/atlas-lab";
  var tx = el(note, "span", "");
  tx.appendChild(document.createTextNode(" PRs and CI are off. To see them here, turn GitHub on in "));
  text(tx, "span", "mono", dir + "/atlas.json");
  tx.appendChild(document.createTextNode(". Heliopause then asks GitHub through your own "));
  text(tx, "span", "mono", "gh");
  tx.appendChild(document.createTextNode(". The README shows how."));
}

function paintNote(parent, labDir) {
  var note = el(parent, "div", "td-note");
  text(note, "span", "m", "○");
  appendGhOff(note, labDir);
}

function paintUnread(parent) {
  var note = el(parent, "div", "td-note wait");
  text(note, "span", "m", GH_UNREADABLE.charAt(0));
  note.appendChild(document.createTextNode(GH_UNREADABLE.slice(1)));
}

function paintRow(parent, row, showPrs, generated, github) {
  var failed = row.state === "failed";
  var line = el(parent, "div", "td-row" + (failed ? " fail" : ""));
  text(line, "span", "t", whenLabel(row.ts, generated));
  addState(line, row);
  var what = el(line, "span", "what");
  var summary = text(what, "b", "", whatText(row));
  if (summary.textContent) summary.setAttribute("title", summary.textContent);
  var who = byLine(row);
  if (who) text(what, "span", "by", who);
  // A job's CI chip is the builder's own report, read locally, so it stays when GitHub is unreadable.
  addChecks(line, row, isJob(row) ? github : showPrs);
  var act = el(line, "span", "act");
  var thread = row.thread ? String(row.thread) : "";
  if (thread && isJob(row) && (row.state === "done" || row.state === "failed")) {
    link(act, "link", "Report", "#/messages?thread=" + encodeURIComponent(thread));
  }
}

function paintEmpty(view, data) {
  var head = el(view, "header", "hp-head");
  var titles = el(head, "div");
  text(titles, "p", "eyebrow", eyebrow(data && data.generated));
  text(titles, "h1", "", "Nothing done yet today.");
  text(titles, "p", "sub", subLine(data));
  text(head, "span", "live", "Live");
  var box = el(view, "div", "td-empty");
  text(box, "b", "", "When a job reports done or failed, it shows here, by repo.");
  var line = el(box, "p", "");
  line.appendChild(document.createTextNode("Builders report with "));
  text(line, "span", "mono", "cd ~/Heliopause && python3 -B -m atlas report <id> done|failed --lab ~/atlas-lab");
  line.appendChild(document.createTextNode(". Jobs start in Messages."));
  var jump = el(box, "div", "");
  link(jump, "link", "Open Messages", "#/messages");
}

function paintBlocks(view, data) {
  var blocks = Array.isArray(data.repos) ? data.repos : [];
  var github = !!data.github;
  var unreadable = !!data.github_unreadable;
  var showPrs = github && !unreadable;
  var head = el(view, "header", "hp-head");
  var titles = el(head, "div");
  text(titles, "p", "eyebrow", eyebrow(data.generated));
  text(titles, "h1", "", titleText(blocks, showPrs));
  text(titles, "p", "sub", subLine(data));
  text(head, "span", "live", "Live");
  var bar = el(view, "div", "filters");
  var seg = el(bar, "div", "hp-seg");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "Repo");
  function chip(value, label) {
    var on = repoFilter === value;
    var btn = text(seg, "button", on ? "on" : "", label);
    btn.type = "button";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.addEventListener("click", function () {
      repoFilter = value;
      refreshNow();
    });
  }
  chip("", "All repos");
  for (var i = 0; i < blocks.length; i++) {
    var name = blocks[i] && blocks[i].repo != null ? String(blocks[i].repo) : "";
    if (name) chip(name, name);
  }
  link(bar, "link", "Yesterday", "#/messages");
  if (unreadable) paintUnread(view);
  for (var b = 0; b < blocks.length; b++) {
    var block = blocks[b];
    if (!block || typeof block !== "object") continue;
    var repo = block.repo != null ? String(block.repo) : "";
    if (repoFilter && repo !== repoFilter) continue;
    var section = el(view, "section", "td-repo");
    var rh = el(section, "div", "td-rh");
    text(rh, "h2", "", repo);
    var sum = blockSum(block, showPrs);
    if (sum) text(rh, "span", "sum", sum);
    var shown = visibleRows(block, showPrs);
    for (var r = 0; r < shown.length; r++) paintRow(section, shown[r], showPrs, data.generated, github);
    if (!github) paintNote(section, data.lab_dir);
  }
}

function paint(rootEl, data) {
  var view = el(null, "div", "hp-today");
  var blocks = data && Array.isArray(data.repos) ? data.repos : [];
  if (!data || data.error) {
    text(view, "p", "td-empty", data && data.error ? String(data.error) : "Today didn't load.");
  } else if (!anyRow(blocks)) {
    paintEmpty(view, data);
  } else {
    paintBlocks(view, data);
  }
  clear(rootEl);
  rootEl.appendChild(view);
}

function refreshNow() {
  if (liveCtx && typeof liveCtx.refresh === "function") liveCtx.refresh();
  else if (liveRoot) render(liveRoot, liveCtx || {});
}

function ask(ctx) {
  try {
    return Promise.resolve(ctx.api("/api/today")).then(
      function (data) { return { ok: true, data: data }; },
      function () { return { ok: false }; }
    );
  } catch (e) {
    return Promise.resolve({ ok: false });
  }
}

// ponytail: one 5s interval, skipped while document.hidden and when the body is unchanged. Drop it for a push when the queue is hot.
function armPoll() {
  if (poll) clearInterval(poll);
  poll = setInterval(function () {
    if (typeof document !== "undefined" && document.hidden) return;
    if (!liveRoot || !liveRoot.isConnected) {
      clearInterval(poll);
      poll = 0;
      return;
    }
    var active = document.activeElement;
    if (active && liveRoot.contains(active)) {
      var tag = active.tagName;
      if (tag === "SELECT" || tag === "TEXTAREA" || tag === "INPUT") return;
    }
    refreshNow();
  }, 5000);
}

function bindVis() {
  if (visBound || typeof document === "undefined") return;
  visBound = true;
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) return;
    if (liveRoot && liveRoot.isConnected) refreshNow();
  });
}

function render(rootEl, ctx) {
  liveRoot = rootEl;
  liveCtx = ctx || null;
  if (!rootEl) return Promise.resolve();
  // app.js paint() calls this when another view takes the shared root: stop the poll and drop any reply still in flight.
  rootEl._hpStop = function () { if (poll) clearInterval(poll); poll = 0; seq++; liveRoot = null; };
  if (!ctx || typeof ctx.api !== "function") {
    rootEl.textContent = "Today can't load.";
    return Promise.resolve();
  }
  bindVis();
  armPoll();
  var mine = ++seq;
  return ask(ctx).then(function (part) {
    if (mine !== seq || !rootEl.isConnected) return;
    var data = part.ok ? part.data : { error: "Today didn't load." };
    if (data && window.HP && typeof window.HP.setPrivacy === "function") window.HP.setPrivacy(!!data.github);
    var sig = "/api/today\n" + repoFilter + "\n" + responseText(data);
    if (sig === drawnText && rootEl.firstChild) return;
    drawnText = sig;
    paint(rootEl, data && typeof data === "object" ? data : { error: "Today didn't load." });
  });
}

if (typeof window !== "undefined") {
  if (typeof document !== "undefined" && document.querySelector && !document.querySelector('link[href="/views/today.css"]')) {
    var sheet = document.createElement("link");
    sheet.rel = "stylesheet";
    sheet.href = "/views/today.css";
    if (document.head) document.head.appendChild(sheet);
  }
  window.HP = window.HP || {};
  window.HP.views = window.HP.views || {};
  window.HP.views.today = { title: "Today", render: render };
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { render: render };
}
})();

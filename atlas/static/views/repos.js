/**
 * Repos view. Titles and reasons are untrusted when they come from the payload:
 * every field is textContent. ctx.api(path) returns JSON.
 */
// One scope per view: every view loads into the same page, so no top-level name may leak (body left unindented on purpose).
(function () {
function GH_OFF(labDir) {
  var dir = labDir ? String(labDir) : "~/atlas-lab";
  return "PRs and CI are off. To see them here, turn GitHub on in " + dir + "/atlas.json. Heliopause then asks GitHub through your own gh. The README shows how.";
}
var GH_UNREADABLE = "◇ GitHub couldn't be read, so PRs and CI aren't shown. If gh isn't signed in, run gh auth login in a terminal on this computer. This fills in on the next refresh.";
var REPOS_EMPTY = "Heliopause shows the repos you ticked in setup.";
var DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
var JOBS = [
  ["sent", "○", "sent"],
  ["picked_up", "◐", "picked up"],
  ["done", "●", "done"],
  ["failed", "▲", "failed"]
];

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

function ciWhen(ts, generated) {
  var spoken = "";
  if (window.HP && typeof window.HP.age === "function") spoken = window.HP.age(ts, generated);
  if (!spoken) spoken = clock(ts);
  if (!spoken) return "";
  return "Last CI run " + spoken;
}

function eyebrow(ts) {
  var d = new Date(ts || "");
  if (isNaN(d.getTime())) return "Repos";
  return "Repos · " + DAYS[d.getDay()] + " " + clock(ts);
}

function num(value) {
  return typeof value === "number" && isFinite(value) ? value : 0;
}

function cardsOf(data) {
  var list = data && Array.isArray(data.repos) ? data.repos : [];
  var out = [];
  for (var i = 0; i < list.length; i++) {
    if (list[i] && typeof list[i] === "object") out.push(list[i]);
  }
  return out;
}

function checksOf(card) {
  return card && Array.isArray(card.checks) ? card.checks : [];
}

function prsOf(card) {
  return card && Array.isArray(card.prs) ? card.prs : [];
}

function stateOf(item) {
  if (!item || typeof item !== "object") return "";
  var raw = item.state != null ? item.state : item.status;
  return raw == null ? "" : String(raw);
}

function nameOf(item) {
  if (!item || typeof item !== "object" || item.name == null) return "";
  return String(item.name);
}

function repoName(card) {
  return card && card.repo != null ? String(card.repo) : "";
}

function joinAnd(names) {
  if (names.length <= 1) return names[0] || "";
  if (names.length === 2) return names[0] + " and " + names[1];
  return names.slice(0, names.length - 1).join(", ") + " and " + names[names.length - 1];
}

function titleText(cards) {
  var failedNames = [];
  var quietNames = [];
  var failedJobs = 0;
  var doneJobs = 0;
  var startedJobs = 0;
  for (var i = 0; i < cards.length; i++) {
    var jobs = cards[i] && cards[i].jobs_today;
    var failed = num(jobs && jobs.failed);
    var done = num(jobs && jobs.done);
    failedJobs += failed;
    doneJobs += done;
    startedJobs += num(jobs && jobs.sent) + num(jobs && jobs.picked_up);
    if (failed > 0) failedNames.push(repoName(cards[i]));
    else quietNames.push(repoName(cards[i]));
  }
  if (failedNames.length) {
    var line = failedJobs + (failedJobs === 1 ? " job" : " jobs") + " failed today, in " + joinAnd(failedNames) + ".";
    if (quietNames.length === 1) line += " " + quietNames[0] + " is quiet.";
    else if (quietNames.length === 2) line += " " + joinAnd(quietNames) + " are quiet.";
    else if (quietNames.length > 2) line += " " + quietNames.length + " repos are quiet.";
    return line;
  }
  if (doneJobs > 0) return doneJobs + (doneJobs === 1 ? " job" : " jobs") + " done today. Nothing failed.";
  if (startedJobs > 0) return "Nothing done yet today.";
  return "No jobs today.";
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

function ciWords(ci) {
  var key = ci == null ? "" : String(ci);
  if (key === "pass" || key === "green") return ["", "● CI passed"];
  if (key === "fail" || key === "red") return ["bad", "▲ CI failed"];
  if (key === "pending" || key === "running") return ["", "◐ CI running"];
  if (key === "none") return ["none", "○ No CI workflow"];
  return null;
}

function reasonFor(item, github, signedOut) {
  if (item && item.reason != null && String(item.reason)) return String(item.reason);
  var name = nameOf(item);
  var state = stateOf(item);
  if (state === "none" || state === "unchecked" || state === "") {
    if (signedOut) return "Needs GitHub signed in.";
    if (!github) return "Needs GitHub on.";
    return "";
  }
  if (name === "CI workflow" && (state === "fail" || state === "failed")) return "No workflow in .github/workflows.";
  if (name === "Branch protection" && (state === "fail" || state === "failed")) {
    return "Anyone with write access can push to main without a pull request.";
  }
  if (name === "Branch protection" && (state === "pass" || state === "passed")) {
    return "main needs a pull request and 1 passing check.";
  }
  return "";
}

function checkWords(item) {
  var state = stateOf(item);
  if (state === "pass" || state === "passed") return ["", "● Check passed"];
  if (state === "fail" || state === "failed") return ["bad", "▲ Check failed"];
  return ["none", "○ Not checked"];
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

function link(parent, cls, label, href) {
  var node = text(parent, "a", cls, label);
  node.href = href;
  node.setAttribute("href", href);
  return node;
}

function paintNote(parent, mark, sentence, wait) {
  var note = el(parent, "div", "td-note" + (wait ? " wait" : ""));
  if (wait) {
    text(note, "span", "m", GH_UNREADABLE.charAt(0));
    note.appendChild(document.createTextNode(GH_UNREADABLE.slice(1)));
    return note;
  }
  text(note, "span", "m", mark);
  note.appendChild(document.createTextNode(" " + sentence));
  return note;
}

function paintJobs(parent, counts) {
  var wrap = el(parent, "div", "rp-jobs");
  for (var i = 0; i < JOBS.length; i++) {
    var n = num(counts && counts[JOBS[i][0]]);
    var cls = JOBS[i][0] === "failed" && n > 0 ? "st fail" : "st q";
    var node = el(wrap, "span", cls);
    text(node, "span", "m", JOBS[i][1]);
    node.appendChild(document.createTextNode(" "));
    var rest = el(node, "span");
    text(rest, "span", "c", String(n));
    rest.appendChild(document.createTextNode(" " + JOBS[i][2]));
  }
}

function paintPrs(parent, card, generated) {
  var prs = prsOf(card);
  var open = [];
  var merged = [];
  for (var i = 0; i < prs.length; i++) {
    var pr = prs[i];
    if (!pr || typeof pr !== "object" || pr.number == null) continue;
    if (pr.state === "merged") merged.push(pr);
    else open.push(pr);
  }
  for (var o = 0; o < open.length; o++) {
    var row = el(parent, "div", "rp-ln");
    text(row, "span", "", "PR #" + open[o].number);
    text(row, "span", "rp-pr", open[o].title == null ? "" : String(open[o].title));
    var ci = ciWords(open[o].ci);
    if (ci) text(row, "span", "ck" + (ci[0] ? " " + ci[0] : ""), ci[1]);
  }
  var ran = ciWhen(card.last_ci, generated);
  if (ran) {
    var main = el(parent, "div", "rp-ln");
    text(main, "span", "", "main");
    text(main, "span", "rp-pr quiet", ran);
  }
  var line = el(parent, "div", "rp-ln");
  text(line, "span", "", "Merged today");
  var label = "None";
  if (merged.length) {
    var bits = [];
    for (var m = 0; m < merged.length; m++) {
      var title = merged[m].title == null ? "" : String(merged[m].title);
      bits.push("PR #" + merged[m].number + " · " + title);
    }
    label = bits.join(", ");
  }
  text(line, "span", "rp-pr quiet", label);
}

function paintChecks(parent, card, github, signedOut) {
  var list = checksOf(card);
  if (!list.length) return;
  var group = el(parent, "div", "rp-grp");
  text(group, "h3", "", "Repo checks");
  for (var i = 0; i < list.length; i++) {
    var item = list[i];
    if (!item || typeof item !== "object") continue;
    var row = el(group, "div", "rp-ln");
    text(row, "span", "", nameOf(item));
    var mid = el(row, "span");
    var words = checkWords(item);
    text(mid, "span", "ck" + (words[0] ? " " + words[0] : ""), words[1]);
    var why = reasonFor(item, github, signedOut);
    if (why) text(mid, "span", "why", why);
    var name = nameOf(item);
    var state = stateOf(item);
    var failed = state === "fail" || state === "failed";
    if (name === "CI workflow" && failed) link(row, "link", "Copy fix as a job", "#/messages");
  }
}

function paintCard(parent, card, mode, generated, labDir) {
  var article = el(parent, "article", "rp");
  var head = el(article, "div", "rp-h");
  text(head, "h2", "", card.repo == null ? "" : String(card.repo));
  var jobs = el(article, "div", "rp-grp");
  text(jobs, "h3", "", "Jobs today");
  paintJobs(jobs, card.jobs_today || {});
  if (mode === "off") {
    var off = el(article, "div", "rp-grp");
    text(off, "h3", "", "PRs and CI");
    paintNote(off, "○", GH_OFF(labDir), false);
  } else if (mode === "on") {
    var prs = el(article, "div", "rp-grp");
    text(prs, "h3", "", "PRs and CI");
    paintPrs(prs, card, generated);
  }
  paintChecks(article, card, mode !== "off", mode === "out");
}

function paint(rootEl, data) {
  var view = el(null, "div", "hp-repos");
  if (!data || data.error) {
    text(view, "p", "sub", data && data.error ? String(data.error) : "Repos didn't load.");
  } else {
    var cards = cardsOf(data);
    var unreadable = !!data.github_unreadable;
    var mode = !data.github ? "off" : (unreadable ? "out" : "on");
    var head = el(view, "header", "hp-head");
    var titles = el(head, "div");
    text(titles, "p", "eyebrow", eyebrow(data.generated));
    text(titles, "h1", "", cards.length ? titleText(cards) : "No repos yet.");
    text(titles, "p", "sub", subLine(data));
    text(head, "span", "live", "Live");
    if (mode === "out") paintNote(view, "◇", GH_UNREADABLE, true);
    if (!cards.length) {
      text(el(view, "div", "td-empty"), "p", "", REPOS_EMPTY);
    } else {
      var grid = el(view, "div", "rp-grid");
      for (var i = 0; i < cards.length; i++) paintCard(grid, cards[i], mode, data.generated, data.lab_dir);
    }
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
    return Promise.resolve(ctx.api("/api/repos")).then(
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
    rootEl.textContent = "Repos can't load.";
    return Promise.resolve();
  }
  bindVis();
  armPoll();
  var mine = ++seq;
  return ask(ctx).then(function (part) {
    if (mine !== seq || !rootEl.isConnected) return;
    var data = part.ok ? part.data : { error: "Repos didn't load." };
    if (data && window.HP && typeof window.HP.setPrivacy === "function") window.HP.setPrivacy(!!data.github);
    var sig = "/api/repos\n" + responseText(data);
    if (sig === drawnText && rootEl.firstChild) return;
    drawnText = sig;
    paint(rootEl, data && typeof data === "object" ? data : { error: "Repos didn't load." });
  });
}

if (typeof window !== "undefined") {
  if (typeof document !== "undefined" && document.querySelector && !document.querySelector('link[href="/views/repos.css"]')) {
    var sheet = document.createElement("link");
    sheet.rel = "stylesheet";
    sheet.href = "/views/repos.css";
    if (document.head) document.head.appendChild(sheet);
  }
  window.HP = window.HP || {};
  window.HP.views = window.HP.views || {};
  window.HP.views.repos = { title: "Repos", render: render };
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { render: render };
}
})();

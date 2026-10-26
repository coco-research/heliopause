/**
 * Messages view. Summaries are untrusted: every message field is textContent.
 * ctx.api(path, opts) returns JSON. ctx.refresh() re-renders. The shell adds the page nonce.
 */
// One scope per view: every view loads into the same page, so no top-level name may leak (body left unindented on purpose).
(function () {
var STATES = {
  waiting: { cls: "wait", label: "◇ Waiting on you", tally: "waiting on you" },
  sent: { cls: "sent", label: "○ Sent", tally: "sent" },
  picked_up: { cls: "prog", label: "◐ Picked up", tally: "picked up" },
  failed: { cls: "fail", label: "▲ Failed", tally: "failed" },
  done: { cls: "done", label: "● Done", tally: "done" }
};
var TALLY_ORDER = ["waiting", "sent", "picked_up", "failed", "done"];
var ROLE = { hq: "HQ", lab: "Lab", factory: "Factory" };
var ROLES = [["", "All"], ["hq", "To HQ"], ["lab", "To Lab"], ["factory", "To Factory"]];

var filters = { role: "", tool: "", repo: "", state: "" };
var openThread = "";
var openId = "";
var hashApplied = "";
var catalog = { tools: [], repos: [] };
var seq = 0;
var poll = 0;
var busy = 0;
var copyTimer = 0;
var visBound = false;
var liveRoot = null;
var liveCtx = null;
var drawnText = "";

var CLOCK = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23"
});
var DAY = new Intl.DateTimeFormat("en-CA", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit"
});

function partyName(party) {
  if (!party) return "";
  if (party.name) return String(party.name);
  if (party.member) return String(party.member);
  var role = party.role ? String(party.role) : "";
  return ROLE[role] || role;
}

function shortIdOf(msg) {
  if (!msg) return "";
  if (msg.short_id) return String(msg.short_id);
  if (msg.short) return String(msg.short);
  return String(msg.id || "");
}

function copyJobText(msg) {
  var id = String(msg.id || "");
  var shortId = shortIdOf(msg);
  var member = partyName(msg.to);
  var repo = msg.repo == null ? "" : String(msg.repo);
  var summary = msg.summary == null ? "" : String(msg.summary);
  return "Job " + shortId + " for " + member + " in " + repo + ": " + summary
    + "\nWhen done, run cd ~/Heliopause && python3 -B -m atlas report " + id + " done|failed --tests pass|fail --lab ~/atlas-lab";
}

// Falls back to the id when the shell's map isn't loaded (node harness).
function toolName(id) {
  if (id == null || id === "") return "";
  var fn = window.HP && window.HP.toolName;
  if (typeof fn === "function") return fn(id);
  return String(id);
}

function toolList(msg) {
  var out = [];
  function add(id) {
    var name = toolName(id);
    if (name && out.indexOf(name) < 0) out.push(name);
  }
  if (!msg) return "";
  if (msg.from) add(msg.from.tool);
  if (msg.to) add(msg.to.tool);
  add(msg.tool);
  return out.join(" · ");
}

function responseText(data) {
  return JSON.stringify(data, function (key, value) {
    if (key === "generated") return undefined;
    return value;
  });
}

function startNote(tool) {
  var name = toolName(tool);
  return name + " starts jobs when you do. Copy the job, paste it into " + name + ", and " + name + " reports back with the job's id.";
}

function jobTool(msg) {
  if (!msg) return "";
  if (msg.to && msg.to.tool) return String(msg.to.tool);
  if (msg.to_tool) return String(msg.to_tool);
  if (msg.tool) return String(msg.tool);
  return "";
}

function stateInfo(msg) {
  if (msg && (msg.waiting_on_you || msg.state === "waiting")) return STATES.waiting;
  if (msg && STATES[msg.state]) return STATES[msg.state];
  return { cls: "", label: msg && msg.state ? String(msg.state) : "", tally: "" };
}

function bucketOf(msg) {
  if (msg.waiting_on_you || msg.state === "waiting") return "waiting";
  if (msg.state === "sent" || msg.state === "picked_up" || msg.state === "done" || msg.state === "failed") return msg.state;
  return "";
}

function checkLines(msg) {
  var c = msg && msg.checks;
  if (!c) return [];
  var out = [];
  if (c.tests === "pass") out.push(["", "● Tests pass"]);
  else if (c.tests === "fail") out.push(["bad", "▲ Tests fail"]);
  if (c.review === "pass") out.push(["", "● Reviewer: approve"]);
  else if (c.review === "none") out.push(["none", "○ No review"]);
  if (c.ci === "pass") out.push(["", "● CI passed"]);
  else if (c.ci === "pending") out.push(["", "◐ CI running"]);
  else if (c.ci === "fail") out.push(["bad", "▲ CI failed"]);
  else if (c.ci === "none") out.push(["none", "○ No CI workflow"]);
  return out;
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

function dayKey(ts) {
  try {
    var d = new Date(ts);
    if (isNaN(d.getTime())) return "";
    return DAY.format(d);
  } catch (e) {
    return "";
  }
}

function countToday(list, generated) {
  var today = dayKey(generated || new Date().toISOString());
  var n = 0;
  for (var i = 0; i < list.length; i++) {
    if (dayKey(list[i].ts) === today) n += 1;
  }
  return n;
}

function hashThread() {
  var raw = "";
  try {
    raw = (typeof location !== "undefined" && location && location.hash) || "";
  } catch (e) {
    return "";
  }
  var q = String(raw).split("?")[1] || "";
  var bits = q.split("&");
  for (var i = 0; i < bits.length; i++) {
    var kv = bits[i].split("=");
    var key = kv[0] || "";
    try { key = decodeURIComponent(key); } catch (err) {}
    if (key !== "thread") continue;
    var val = kv.slice(1).join("=");
    try { return decodeURIComponent(val); } catch (err2) { return val; }
  }
  return "";
}

function takeHashThread() {
  var id = hashThread();
  if (!id && hashApplied) {
    // Back from a deep link (#/messages?thread=…) closes the thread it opened.
    hashApplied = "";
    openThread = "";
    openId = "";
    return;
  }
  if (!id || id === hashApplied) return;
  hashApplied = id;
  openThread = id;
  openId = id;
}

var DAY_W = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
var DAY_M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function dayHeading(ts, generated) {
  var spoken = "";
  if (window.HP && typeof window.HP.age === "function") spoken = window.HP.age(ts, generated);
  if (spoken === "yesterday") return "Yesterday";
  var d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  return DAY_W[d.getDay()] + " " + d.getDate() + " " + DAY_M[d.getMonth()];
}

function threadOf(msg) {
  if (msg && typeof msg.thread === "string" && msg.thread) return msg.thread;
  return String((msg && msg.id) || "");
}

function refreshNow() {
  if (liveCtx && typeof liveCtx.refresh === "function") liveCtx.refresh();
  else if (liveRoot) render(liveRoot, liveCtx || {});
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

function listQuery(withState) {
  var parts = [];
  function add(key, value) {
    if (value) parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(value));
  }
  add("role", filters.role);
  add("tool", filters.tool);
  add("repo", filters.repo);
  if (withState) add("state", filters.state);
  return parts.length ? "/api/messages?" + parts.join("&") : "/api/messages";
}

function remember(list) {
  for (var i = 0; i < list.length; i++) {
    var msg = list[i];
    if (!msg) continue;
    var tools = [];
    if (msg.from && msg.from.tool) tools.push(msg.from.tool);
    if (msg.to && msg.to.tool) tools.push(msg.to.tool);
    if (msg.tool) tools.push(msg.tool);
    for (var t = 0; t < tools.length; t++) {
      var name = String(tools[t]);
      if (name && catalog.tools.indexOf(name) < 0) catalog.tools.push(name);
    }
    if (msg.repo) {
      var repo = String(msg.repo);
      if (catalog.repos.indexOf(repo) < 0) catalog.repos.push(repo);
    }
  }
  catalog.tools.sort();
  catalog.repos.sort();
}

function withCurrent(list, current) {
  var out = list.slice();
  if (current && out.indexOf(current) < 0) out.push(current);
  out.sort();
  return out;
}

function fillSelect(sel, current, values, labelOf) {
  text(sel, "option", "", "All").value = "";
  var opts = withCurrent(values, current);
  for (var i = 0; i < opts.length; i++) {
    var opt = text(sel, "option", "", labelOf ? labelOf(opts[i]) : opts[i]);
    opt.value = opts[i];
  }
  sel.value = current;
}

function shapeOf(label) {
  var cut = label.indexOf(" ");
  return cut < 0 ? label : label.slice(0, cut);
}

function wordOf(label) {
  var cut = label.indexOf(" ");
  return cut < 0 ? "" : label.slice(cut + 1);
}

function addState(parent, info) {
  var node = el(parent, "span", "st" + (info.cls ? " " + info.cls : ""));
  text(node, "span", "m", shapeOf(info.label));
  node.appendChild(document.createTextNode(wordOf(info.label) ? " " + wordOf(info.label) : ""));
  return node;
}

function stepHeading(parent, msg, job) {
  var span = el(parent, "span");
  var from = partyName(msg.from);
  var to = partyName(msg.to);
  function bold(value) {
    text(span, "b", "", value);
  }
  if (msg.kind === "job") {
    bold(from);
    span.appendChild(document.createTextNode(" sent the job to "));
    bold(to);
    return;
  }
  if (msg.kind === "report" && job && job.state === "failed") {
    bold(from);
    span.appendChild(document.createTextNode(" reported a failure"));
    return;
  }
  if (msg.kind === "report") {
    bold(from);
    span.appendChild(document.createTextNode(" reported to "));
    bold(to);
    return;
  }
  if (msg.kind === "ask") {
    bold(from);
    span.appendChild(document.createTextNode(" asked "));
    bold(to);
    return;
  }
  if (msg.kind === "answer") {
    bold(from);
    span.appendChild(document.createTextNode(" answered "));
    bold(to);
    return;
  }
  bold(from);
  span.appendChild(document.createTextNode(" sent to "));
  bold(to);
}

function addChecks(parent, msg) {
  var lines = checkLines(msg);
  if (!lines.length) return;
  var wrap = el(parent, "div", "chks");
  for (var i = 0; i < lines.length; i++) {
    text(wrap, "span", "ck" + (lines[i][0] ? " " + lines[i][0] : ""), lines[i][1]);
  }
}

function copyJob(btn, box, value) {
  function done() {
    btn.textContent = "Copied";
    if (copyTimer) clearTimeout(copyTimer);
    copyTimer = setTimeout(function () { btn.textContent = "Copy job"; }, 2000);
  }
  function fallback() {
    box.hidden = false;
    box.readOnly = true;
    box.value = value;
    box.focus();
    box.select();
  }
  try {
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
      fallback();
      return;
    }
    navigator.clipboard.writeText(value).then(done, fallback);
  } catch (e) {
    fallback();
  }
}

function markJob(job, result, errEl, buttons) {
  var ctx = liveCtx;
  if (!ctx || typeof ctx.api !== "function") return;
  busy += 1;
  for (var i = 0; i < buttons.length; i++) buttons[i].disabled = true;
  var opts = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: String(job.id || ""), result: result })
  };
  var pending;
  try {
    pending = Promise.resolve(ctx.api("/api/jobs/mark", opts));
  } catch (e) {
    busy -= 1;
    for (var j = 0; j < buttons.length; j++) buttons[j].disabled = false;
    errEl.textContent = "Couldn't mark the job.";
    return;
  }
  pending.then(function (data) {
    busy -= 1;
    if (!data || data.ok === false || data.error) {
      errEl.textContent = data && data.error ? String(data.error) : "Couldn't mark the job.";
      for (var n = 0; n < buttons.length; n++) buttons[n].disabled = false;
      return;
    }
    refreshNow();
  }, function () {
    busy -= 1;
    errEl.textContent = "Couldn't mark the job.";
    for (var n = 0; n < buttons.length; n++) buttons[n].disabled = false;
  });
}

function findJob(list) {
  var fallback = null;
  for (var i = 0; i < list.length; i++) {
    if (!list[i] || list[i].kind !== "job") continue;
    if (!fallback) fallback = list[i];
    if (String(list[i].id || "") === openId) return list[i];
  }
  return fallback;
}

function findId(list, id) {
  for (var i = 0; i < list.length; i++) {
    if (list[i] && String(list[i].id || "") === id) return list[i];
  }
  return null;
}

function paintRow(log, msg) {
  var info = stateInfo(msg);
  var cls = "msg";
  if (String(msg.id || "") === openId) cls += " sel";
  if (info.cls === "fail") cls += " fail";
  var row = el(log, "div", cls);
  row.setAttribute("role", "button");
  row.tabIndex = 0;
  text(row, "span", "t", clock(msg.ts));
  var ft = el(row, "div", "ft");
  var initial = partyName(msg.from).slice(0, 1);
  if (initial) {
    var orb = text(ft, "span", "orb", initial);
    orb.setAttribute("aria-hidden", "true");
  }
  text(ft, "b", "", partyName(msg.from));
  text(ft, "span", "ar", "→");
  text(ft, "b", "", partyName(msg.to));
  var sum = el(row, "div", "sum");
  var summary = text(sum, "span", "sum-text", msg.summary == null ? "" : String(msg.summary));
  if (summary.textContent) summary.setAttribute("title", summary.textContent);
  text(sum, "span", "chip kind", msg.kind == null ? "" : String(msg.kind));
  if (msg.repo) text(sum, "span", "repo", String(msg.repo));
  if (!openThread) text(row, "span", "tool-cell", toolList(msg));
  addState(row, info);
  function open() { openThread = threadOf(msg); openId = String(msg.id || ""); refreshNow(); }
  row.addEventListener("click", open);
  row.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" || ev.key === " ") {
      if (ev.preventDefault) ev.preventDefault();
      open();
    }
  });
}

function paintThread(panel, part) {
  if (!openThread) return;
  if (!part || part.err) {
    text(panel, "p", "empty", part && part.error ? part.error : "This thread didn't load.");
    return;
  }
  var list = part.list.slice().sort(function (a, b) {
    return String(a.ts || "").localeCompare(String(b.ts || ""));
  });
  if (!list.length) {
    text(panel, "p", "empty", "This thread is empty.");
    return;
  }
  var job = findJob(list);
  var opened = findId(list, openId) || list[list.length - 1];
  var headMsg = job || opened;
  var info = stateInfo(headMsg);
  var meta = el(panel, "div", "tmeta");
  addState(meta, info);
  text(meta, "span", "chip kind", headMsg.kind == null ? "" : String(headMsg.kind));
  var repo = headMsg.repo == null ? "" : String(headMsg.repo);
  if (repo) text(meta, "span", "repo", repo);
  text(panel, "h3", "", headMsg.summary == null ? "" : String(headMsg.summary));
  var steps = el(panel, "div", "steps");
  for (var i = 0; i < list.length; i++) {
    var msg = list[i];
    var st = stateInfo(msg);
    var failedStep = msg.state === "failed" || (msg.kind === "report" && job && job.state === "failed");
    var step = el(steps, "div", "step" + (failedStep ? " fail" : ""));
    text(step, "span", "mk" + (st.cls ? " " + st.cls : ""), shapeOf(st.label));
    var bd = el(step, "div", "bd");
    var hd = el(bd, "div", "hd");
    stepHeading(hd, msg, job);
    text(hd, "span", "t", clock(msg.ts));
    text(bd, "div", "quote", msg.summary == null ? "" : String(msg.summary));
    if (msg.kind === "job" && msg.state === "picked_up") {
      var picked = jobTool(msg);
      if (picked) text(bd, "div", "tx", "You started it in " + toolName(picked) + ".");
    }
    if (msg.kind === "job") addChecks(bd, msg);
  }
  if (job && job.state === "sent") {
    var tool = jobTool(job);
    if (tool) text(panel, "p", "note", startNote(tool));
    var value = copyJobText(job);
    var box = el(panel, "textarea", "code");
    box.readOnly = true;
    box.hidden = true;
    box.value = value;
    box.setAttribute("aria-label", "Job text");
    var acts = el(panel, "div", "acts");
    var copyBtn = text(acts, "button", "btn btn-accent", "Copy job");
    copyBtn.type = "button";
    copyBtn.setAttribute("aria-live", "polite");
    copyBtn.addEventListener("click", function () { copyJob(copyBtn, box, value); });
    var mark = el(panel, "div", "mark-row");
    text(mark, "p", "note", "Finished it another way? Mark it yourself.");
    var err = text(mark, "p", "err", "");
    var doneBtn = text(mark, "button", "btn btn-sm", "Mark done");
    var failBtn = text(mark, "button", "btn btn-sm", "Mark failed");
    doneBtn.type = "button";
    failBtn.type = "button";
    if (busy) { doneBtn.disabled = true; failBtn.disabled = true; }
    doneBtn.addEventListener("click", function () { markJob(job, "done", err, [doneBtn, failBtn]); });
    failBtn.addEventListener("click", function () { markJob(job, "failed", err, [doneBtn, failBtn]); });
  }
}

function paint(rootEl, listPart, tallyPart, threadPart) {
  var tally = tallyPart && !tallyPart.err ? tallyPart : listPart;
  var tallyList = tally && !tally.err ? tally.list : [];
  if (!listPart.err) remember(tallyList.length ? tallyList : listPart.list);
  var counts = { waiting: 0, sent: 0, picked_up: 0, failed: 0, done: 0 };
  for (var i = 0; i < tallyList.length; i++) {
    var b = bucketOf(tallyList[i]);
    if (counts[b] != null) counts[b] += 1;
  }
  var n = countToday(tallyList, (tally && tally.generated) || (listPart && listPart.generated) || "");
  var view = el(null, "div", "hp-msg" + (openThread ? " is-thread" : ""));
  var back = text(view, "button", "hp-back", "Back");
  back.type = "button";
  back.addEventListener("click", function () {
    openThread = "";
    openId = "";
    hashApplied = "";
    try {
      if (typeof location !== "undefined" && location && String(location.hash || "").indexOf("thread=") >= 0) {
        location.hash = "#/messages";
      }
    } catch (e) {}
    refreshNow();
  });
  var head = el(view, "header", "hp-head");
  var titles = el(head, "div");
  text(titles, "p", "eyebrow", "Messages · Today");
  text(titles, "h1", "", n + (n === 1 ? " message today" : " messages today"));
  text(head, "span", "live", "Live");
  var bar = el(view, "div", "filters");
  var seg = el(bar, "div", "hp-seg");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "Role");
  for (var r = 0; r < ROLES.length; r++) {
    (function (value, label) {
      var on = filters.role === value;
      var btn = text(seg, "button", on ? "on" : "", label);
      btn.type = "button";
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.addEventListener("click", function () { filters.role = value; refreshNow(); });
    })(ROLES[r][0], ROLES[r][1]);
  }
  var toolLabel = el(bar, "label", "sel-box");
  toolLabel.appendChild(document.createTextNode("Tool: "));
  var toolSel = el(toolLabel, "select");
  toolSel.setAttribute("aria-label", "Tool");
  fillSelect(toolSel, filters.tool, catalog.tools, toolName);
  toolSel.addEventListener("change", function () { filters.tool = toolSel.value; refreshNow(); });
  var repoLabel = el(bar, "label", "sel-box");
  repoLabel.appendChild(document.createTextNode("Repo: "));
  var repoSel = el(repoLabel, "select");
  repoSel.setAttribute("aria-label", "Repo");
  fillSelect(repoSel, filters.repo, catalog.repos);
  repoSel.addEventListener("change", function () { filters.repo = repoSel.value; refreshNow(); });
  var tallyBar = el(view, "div", "tally");
  for (var k = 0; k < TALLY_ORDER.length; k++) {
    (function (key) {
      var info = STATES[key];
      var on = filters.state === key;
      var btn = el(tallyBar, "button", counts[key] ? "st " + info.cls : "st");
      btn.type = "button";
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      text(btn, "span", "m", shapeOf(info.label));
      var rest = el(btn, "span");
      text(rest, "span", "c", String(counts[key]));
      rest.appendChild(document.createTextNode(" " + info.tally));
      btn.addEventListener("click", function () {
        filters.state = filters.state === key ? "" : key;
        refreshNow();
      });
    })(TALLY_ORDER[k]);
  }
  var split = el(view, "div", "split");
  var log = el(split, "div", "hp-log");
  if (listPart.err) {
    text(log, "p", "empty", listPart.error || "Messages didn't load.");
  } else if (!listPart.list.length) {
    text(log, "p", "empty", "No messages.");
  } else {
    var rows = listPart.list.slice().sort(function (a, b) {
      return String(b.ts || "").localeCompare(String(a.ts || ""));
    });
    var clockNow = (tally && tally.generated) || listPart.generated || new Date().toISOString();
    var today = dayKey(clockNow);
    var seenDay = "";
    for (var m = 0; m < rows.length; m++) {
      var key = dayKey(rows[m].ts);
      if (key && today && key !== today && key !== seenDay) {
        seenDay = key;
        text(log, "div", "day", dayHeading(rows[m].ts, clockNow));
      }
      paintRow(log, rows[m]);
    }
  }
  var panel = el(split, "div", "thread");
  if (openThread) paintThread(panel, threadPart);
  clear(rootEl);
  rootEl.appendChild(view);
}

function unpack(part) {
  if (!part) return null;
  if (!part.ok) return { err: true, list: [], error: "", generated: "" };
  var data = part.data;
  if (Array.isArray(data)) return { err: false, list: data.filter(onlyObj), error: "", generated: "" };
  if (data && Array.isArray(data.messages)) {
    return { err: false, list: data.messages.filter(onlyObj), error: "", generated: data.generated || "" };
  }
  if (data && data.error) return { err: true, list: [], error: String(data.error), generated: "" };
  return { err: true, list: [], error: "", generated: "" };
}

function onlyObj(item) {
  return !!item && typeof item === "object";
}

function ask(ctx, url, opts) {
  try {
    var pending = opts ? ctx.api(url, opts) : ctx.api(url);
    return Promise.resolve(pending).then(
      function (data) { return { ok: true, data: data }; },
      function () { return { ok: false }; }
    );
  } catch (e) {
    return Promise.resolve({ ok: false });
  }
}

// ponytail: one 5s interval for the open list, skipped while document.hidden. Drop it for a push when the queue is hot.
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
    if (busy) return;
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
    rootEl.textContent = "Messages can't load.";
    return Promise.resolve();
  }
  bindVis();
  armPoll();
  takeHashThread();
  var mine = ++seq;
  var listUrl = listQuery(true);
  var tallyUrl = filters.state ? listQuery(false) : "";
  var threadUrl = openThread ? "/api/messages?thread=" + encodeURIComponent(openThread) : "";
  return Promise.all([
    ask(ctx, listUrl),
    tallyUrl ? ask(ctx, tallyUrl) : Promise.resolve(null),
    threadUrl ? ask(ctx, threadUrl) : Promise.resolve(null)
  ]).then(function (parts) {
    if (mine !== seq || !rootEl.isConnected) return;
    // The request is part of the picture: same body for a new filter still redraws.
    var sig = listUrl + "\n" + tallyUrl + "\n" + threadUrl + "\n" + responseText(parts);
    if (sig === drawnText && rootEl.firstChild) return;
    drawnText = sig;
    var listPart = unpack(parts[0]);
    var tallyPart = parts[1] ? unpack(parts[1]) : null;
    var threadPart = parts[2] ? unpack(parts[2]) : null;
    paint(rootEl, listPart, tallyPart, threadPart);
  });
}

if (typeof window !== "undefined") {
  // The shell loads only the view's script; the view brings its own stylesheet.
  if (typeof document !== "undefined" && !document.querySelector('link[href="/views/messages.css"]')) {
    var sheet = document.createElement("link");
    sheet.rel = "stylesheet";
    sheet.href = "/views/messages.css";
    document.head.appendChild(sheet);
  }
  window.HP = window.HP || {};
  window.HP.views = window.HP.views || {};
  window.HP.views.messages = { title: "Messages", render: render };
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { copyJobText: copyJobText, startNote: startNote, render: render };
}
})();

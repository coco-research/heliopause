/* Now. Reads GET /api/now. Posts answers to POST /api/approve.
   Waiting-on-you cards also post Useful / Not useful to POST /api/feedback. */
(function () {
  var HP = window.HP || (window.HP = {});
  HP.views = HP.views || {};

  var REASONS = [
    { label: "Wrong scope", reason: "wrong_scope" },
    { label: "Too big", reason: "too_big" },
    { label: "Not now", reason: "not_now" },
    { label: "Other", reason: "other" }
  ];
  var ROLE = { hq: "HQ", lab: "Lab", factory: "Factory" };
  var VERB = { hq: "decides", lab: "thinks", factory: "builds" };
  // The card is rebuilt on each poll. The press lives here, keyed by message id, so the new buttons can show it.
  var feedbackSaid = new Map();
  var feedbackBusy = new Set();

  function el(tag, cls) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    return node;
  }

  function parts(iso, opts) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return [];
    return new Intl.DateTimeFormat("en-US", opts).formatToParts(d);
  }

  function part(list, type) {
    for (var i = 0; i < list.length; i++) if (list[i].type === type) return list[i].value;
    return "";
  }

  function clock(iso) {
    var list = parts(iso, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    var h = part(list, "hour");
    var m = part(list, "minute");
    if (!h || !m) return "";
    return h + ":" + m;
  }

  function weekday(iso) {
    return part(parts(iso, { weekday: "long" }), "weekday");
  }

  // Falls back to the id when the shell's map isn't loaded (node harness).
  function toolName(id) {
    if (id == null || id === "") return "";
    if (typeof HP.toolName === "function") return HP.toolName(id);
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

  function canon(data) {
    return JSON.stringify(data == null ? null : data, function (key, value) {
      if (key === "generated") return undefined;
      return value;
    });
  }

  function localHM() {
    var d = new Date();
    var h = d.getHours();
    var m = d.getMinutes();
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }

  function age(ts, generated) {
    var fn = HP && HP.age;
    return typeof fn === "function" ? fn(ts, generated) : "";
  }

  function byTs(list, newest) {
    var copy = (list || []).slice();
    copy.sort(function (a, b) {
      var as = (a && a.ts) || "";
      var bs = (b && b.ts) || "";
      if (as < bs) return newest ? 1 : -1;
      if (as > bs) return newest ? -1 : 1;
      return 0;
    });
    return copy;
  }

  function roleWord(role) {
    return ROLE[role] || "";
  }

  function person(seats, ref) {
    var role = ref && ref.role;
    var tool = ref && ref.tool;
    var list = (seats && role && seats[role]) || [];
    var i;
    if (tool) {
      for (i = 0; i < list.length; i++) {
        if (list[i].tool === tool && list[i].name) return list[i].name;
        if (tool === "you" && list[i].name === "You") return "You";
      }
    }
    if (list.length === 1 && list[0].name) return list[0].name;
    return roleWord(role);
  }

  function mark(shape, word, cls) {
    var wrap = el("span", "st " + cls);
    var glyph = el("span", "m");
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = shape;
    var lab = el("span");
    lab.textContent = word;
    wrap.appendChild(glyph);
    wrap.appendChild(lab);
    return wrap;
  }

  function memberMark(state, n, isYou) {
    if (state === "working") return mark("●", "Working", "work");
    if (state === "maybe_waiting") return mark("◇", "Maybe waiting", "wait");
    if (state === "needs_you") return mark("◇", n + " waiting", "wait");
    if (isYou) return mark("○", "Nothing waiting", "idle");
    return mark("○", "Idle", "idle");
  }

  function msgMark(m) {
    if (m.waiting_on_you || m.state === "waiting") return mark("◇", "Waiting on you", "wait");
    if (m.state === "failed") return mark("▲", "Failed", "fail");
    if (m.state === "done") return mark("●", "Done", "done");
    if (m.state === "picked_up") return mark("◐", "Picked up", "prog");
    return mark("○", "Sent", "sent");
  }

  function emptyDay(data) {
    var counts = data.counts || {};
    return (counts.messages_today || 0) === 0 && !(data.needs_you || []).length && !(data.moving || []).length;
  }

  function jobsMoving(list) {
    var n = 0;
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (m && m.kind === "job" && m.state !== "done" && m.state !== "failed") n += 1;
    }
    return n;
  }

  function subLine(data, moving, blank) {
    var counts = data.counts || {};
    var msgs = counts.messages_today || 0;
    var repos = counts.repos || 0;
    var head = nWord(msgs, "message", "messages") + " today · ";
    if (blank) return head + nWord(counts.tools_connected || 0, "tool", "tools") + " connected · " + nWord(repos, "repo", "repos");
    return head + nWord(jobsMoving(moving), "job", "jobs") + " moving · " + nWord(repos, "repo", "repos");
  }

  function factoryWaiting(data) {
    var list = (data.seats && data.seats.factory) || [];
    var n = 0;
    for (var i = 0; i < list.length; i++) n += Number(list[i] && list[i].waiting) || 0;
    return n;
  }

  function tally(data) {
    var seen = {};
    var all = (data.needs_you || []).concat(data.moving || []);
    var asks = 0;
    var answers = 0;
    var failed = 0;
    for (var i = 0; i < all.length; i++) {
      var m = all[i];
      if (!m) continue;
      if (m.id && seen[m.id]) continue;
      if (m.id) seen[m.id] = 1;
      var from = (m.from && m.from.role) || "";
      var to = (m.to && m.to.role) || "";
      if (m.kind === "ask" && from === "lab" && to === "hq") asks += 1;
      else if (m.kind === "answer" && from === "hq" && to === "lab") answers += 1;
      if (m.state === "failed" && from === "factory" && to === "lab") failed += 1;
    }
    return { asks: asks, answers: answers, jobs: factoryWaiting(data), failed: failed };
  }

  function nWord(n, one, many) {
    return n + " " + (n === 1 ? one : many);
  }

  function wireEl(item) {
    var w = el("div", "wire " + item.dir);
    var lbl = el("span", "lbl" + (item.cls ? " " + item.cls : ""));
    lbl.textContent = item.lbl;
    var ln = el("span", "ln");
    var a = el("i");
    var b = el("i");
    b.className = "d2";
    ln.appendChild(a);
    ln.appendChild(b);
    w.appendChild(lbl);
    w.appendChild(ln);
    return w;
  }

  function laneEl(items) {
    var lane = el("div", "lane");
    for (var i = 0; i < items.length; i++) lane.appendChild(wireEl(items[i]));
    return lane;
  }

  function lanes(counts, blank) {
    var left = [];
    var right = [];
    if (blank) {
      left.push({ dir: "l", lbl: "No asks", cls: "" });
      right.push({ dir: "r", lbl: "No jobs", cls: "" });
      return [left, right];
    }
    if (counts.asks) left.push({ dir: "l", lbl: nWord(counts.asks, "ask", "asks"), cls: "hot" });
    if (counts.answers) left.push({ dir: "r", lbl: nWord(counts.answers, "answer", "answers"), cls: "" });
    if (counts.jobs) right.push({ dir: "r", lbl: nWord(counts.jobs, "job", "jobs"), cls: "" });
    if (counts.failed) right.push({ dir: "l", lbl: counts.failed + " failed", cls: "bad" });
    return [left, right];
  }

  function setBadge(n) {
    var ids = ["hp-now-badge", "hp-now-badge-tab"];
    for (var i = 0; i < ids.length; i++) {
      var b = document.getElementById(ids[i]);
      if (!b) continue;
      b.hidden = !(n > 0);
      b.textContent = n > 0 ? String(n) : "";
    }
  }

  function setLive(downAt) {
    var text = downAt ? "Offline since " + downAt : "Live";
    var ids = ["hp-live", "hp-live-narrow"];
    for (var i = 0; i < ids.length; i++) {
      var node = document.getElementById(ids[i]);
      if (!node) continue;
      node.textContent = text;
      if (node.classList) node.classList.toggle("off", !!downAt);
    }
  }

  function buttons(scope) {
    if (!scope || !scope.querySelectorAll) return [];
    return scope.querySelectorAll("button");
  }

  HP.views.now = {
    title: "Now",
    render: function (root, ctx) {
      var stoppedAt = null;
      var last = null;
      var appDir = "";
      var drawn = "";
      var sheet = false;
      var dirty = false;
      var untrap = null;
      var timer = null;
      var dead = false;

      function stop() {
        dead = true;
        if (timer) clearInterval(timer);
        timer = null;
        if (untrap) untrap();
        untrap = null;
      }
      root._hpStop = stop;

      function findDecline(id) {
        var list = buttons(root);
        for (var i = 0; i < list.length; i++) {
          if (list[i].getAttribute("data-ask") === id) return list[i];
        }
        return null;
      }

      function viewKey(data, downAt) {
        return (downAt || "") + "\n" + canon(data);
      }

      function draw(data, downAt) {
        if (dead) return;
        drawn = viewKey(data, downAt);
        while (root.firstChild) root.removeChild(root.firstChild);
        var head = pageHead(data, downAt);
        root.appendChild(head);
        if (downAt) root.appendChild(banner(downAt));
        var body = el("div", downAt ? "stale" : "");
        if (data) fill(body, data);
        root.appendChild(body);
        setLive(downAt);
        if (!data) return;
        var n = (data.needs_you || []).length;
        document.title = n ? "(" + n + ") Heliopause" : "Heliopause";
        setBadge(n);
        if (HP.setPrivacy) HP.setPrivacy(!!data.github);
      }

      function pull() {
        if (dead) return;
        ctx.api("/api/now").then(function (data) {
          if (dead) return;
          var next = data || {};
          if (next.app_dir) appDir = String(next.app_dir);
          // A good fetch always ends a stop, even when nothing else changed.
          var wasDown = stoppedAt;
          stoppedAt = null;
          // Same body (ignore generated): keep the sheet, focus and hover.
          if (viewKey(next, null) === drawn) {
            if (wasDown) setLive(null);
            return;
          }
          last = next;
          if (sheet) {
            dirty = true;
            setLive(null);
            return;
          }
          draw(last, null);
        }, function () {
          if (dead) return;
          if (!stoppedAt) stoppedAt = localHM();
          if (viewKey(last, stoppedAt) === drawn) return;
          if (sheet) {
            dirty = true;
            setLive(stoppedAt);
            return;
          }
          draw(last, stoppedAt);
        });
      }

      function pageHead(data, downAt) {
        var ph = el("div", "ph");
        var left = el("div");
        var eye = el("div", "eyebrow");
        eye.textContent = "Now";
        if (data && data.generated) {
          var day = weekday(data.generated);
          var hm = clock(data.generated);
          eye.textContent = "Now · " + day + (hm ? " " + hm : "");
        }
        left.appendChild(eye);
        if (data) {
          var blank = emptyDay(data);
          var sentence = data.sentence || (blank ? "Nothing yet. Your tools are connected and idle." : "");
          if (sentence) {
            var h1 = el("h1");
            h1.textContent = sentence;
            left.appendChild(h1);
          }
          var sub = el("div", "sub");
          sub.textContent = subLine(data, data.moving || [], blank);
          left.appendChild(sub);
        }
        ph.appendChild(left);
        var live = el("span", "live" + (downAt ? " off" : ""));
        live.id = "hp-live";
        live.textContent = downAt ? "Offline since " + downAt : "Live";
        ph.appendChild(live);
        return ph;
      }

      function banner(time) {
        var dir = appDir || "~/Heliopause";
        var up = "cd " + dir + " && python3 -B -m atlas up";
        var down = el("div", "down");
        var lead = el("p");
        lead.textContent = "This page stopped updating. Start it again:";
        var chip = el("code", "cmd");
        var cd = el("span");
        cd.textContent = "cd " + dir + " &&";
        var run = el("span");
        run.textContent = "python3 -B -m atlas up";
        chip.appendChild(cd);
        chip.appendChild(document.createTextNode(" "));
        chip.appendChild(run);
        var copy = el("button", "btn btn-sm");
        copy.type = "button";
        copy.textContent = "Copy command";
        copy.addEventListener("click", function () {
          var clip = navigator.clipboard;
          function selectChip() {
            var r = document.createRange();
            r.selectNodeContents(chip);
            var s = window.getSelection();
            s.removeAllRanges();
            s.addRange(r);
          }
          if (!clip || !clip.writeText) return selectChip();
          clip.writeText(up).then(function () { copy.textContent = "Copied"; }, selectChip);
        });
        var row = el("div", "cmd-row");
        row.appendChild(chip);
        row.appendChild(copy);
        var note = el("p");
        note.textContent = "Reports sent while it was stopped may not arrive.";
        down.appendChild(mark("▲", "Heliopause stopped at " + time + ".", "fail"));
        down.appendChild(lead);
        down.appendChild(row);
        down.appendChild(note);
        return down;
      }

      function fill(body, data) {
        var blank = emptyDay(data);
        var asks = byTs(data.needs_you, false);
        var moving = byTs(data.moving, true);
        body.appendChild(waiting(asks, data));
        body.appendChild(company(data, blank));
        body.appendChild(movingSec(moving, data));
      }

      function waiting(asks, data) {
        var sec = el("section", "sec");
        var head = el("div", "sec-h");
        var h2 = el("h2");
        h2.appendChild(document.createTextNode("Waiting on you "));
        var c = el("span", "c");
        c.textContent = String(asks.length);
        h2.appendChild(c);
        head.appendChild(h2);
        if (asks.length) {
          var old = el("span", "repo");
          old.textContent = "Oldest first";
          head.appendChild(old);
        }
        sec.appendChild(head);
        if (!asks.length) {
          var p = el("p", "empty");
          p.textContent = "Nothing is waiting on you. Asks from Lab land here, with Approve and Decline.";
          sec.appendChild(p);
          return sec;
        }
        var grid = el("div", "asks");
        for (var i = 0; i < asks.length; i++) grid.appendChild(askCard(asks[i], data));
        sec.appendChild(grid);
        return sec;
      }

      function groupsFor(id) {
        var found = [];
        var all = root.querySelectorAll("[data-subject]");
        for (var i = 0; i < all.length; i++) {
          if (all[i].getAttribute("data-subject") === id) found.push(all[i]);
        }
        return found;
      }

      function paintSaid(id) {
        var said = feedbackSaid.get(id);
        var groups = groupsFor(id);
        for (var i = 0; i < groups.length; i++) {
          var btns = groups[i].querySelectorAll("button");
          for (var k = 0; k < btns.length; k++) {
            var on = btns[k].getAttribute("data-verdict") === said;
            btns[k].setAttribute("aria-pressed", on ? "true" : "false");
          }
        }
      }

      function feedbackNote(id, text) {
        var groups = groupsFor(id);
        for (var i = 0; i < groups.length; i++) {
          var nodes = groups[i].querySelectorAll("[role]");
          for (var k = 0; k < nodes.length; k++) {
            if (nodes[k].getAttribute("role") !== "status") continue;
            nodes[k].textContent = text;
            nodes[k].className = text ? "fb-status is-error" : "fb-status";
          }
        }
      }

      function sendFeedback(id, verdict) {
        // A press that is already showing sends nothing, and never approves or declines the card.
        if (typeof id !== "string" || !id || feedbackBusy.has(id) || feedbackSaid.get(id) === verdict) return;
        feedbackBusy.add(id);
        ctx.api("/api/feedback", {
          method: "POST",
          body: { kind: "ask", subject: id, verdict: verdict }
        }).then(function () {
          feedbackBusy.delete(id);
          feedbackSaid.set(id, verdict);
          feedbackNote(id, "");
          paintSaid(id);
        }, function () {
          feedbackBusy.delete(id);
          feedbackNote(id, "Couldn't save");
          paintSaid(id);
        });
      }

      function feedbackPair(id) {
        var group = el("span", "fb");
        var subject = typeof id === "string" ? id : "";
        group.setAttribute("data-subject", subject);
        var said = feedbackSaid.get(subject);
        var pairs = [["useful", "Useful"], ["not-useful", "Not useful"]];
        for (var i = 0; i < pairs.length; i++) {
          (function (verdict, label) {
            var btn = el("button", "fb-btn");
            btn.type = "button";
            btn.textContent = label;
            btn.setAttribute("data-verdict", verdict);
            btn.setAttribute("aria-pressed", said === verdict ? "true" : "false");
            btn.addEventListener("click", function () { sendFeedback(subject, verdict); });
            group.appendChild(btn);
          })(pairs[i][0], pairs[i][1]);
        }
        var note = el("span", "fb-status");
        note.setAttribute("role", "status");
        note.textContent = "";
        group.appendChild(note);
        return group;
      }

      function askCard(m, data) {
        var art = el("article", "ask");
        var top = el("div", "ask-top");
        var from = el("span", "ask-from");
        var b = el("b");
        b.textContent = person(data.seats, m.from);
        from.appendChild(b);
        from.appendChild(document.createTextNode(" asks HQ · " + roleWord(m.from && m.from.role)));
        var repo = el("span", "repo");
        var ageText = age(m.ts, data.generated);
        repo.textContent = (m.repo ? m.repo + " · " : "") + ageText;
        top.appendChild(from);
        top.appendChild(repo);
        var h3 = el("h3");
        h3.textContent = m.summary || "";
        var act = el("div", "ask-act");
        var approve = el("button", "btn btn-accent");
        approve.type = "button";
        approve.textContent = "Approve";
        approve.addEventListener("click", function () {
          if (approve.disabled) return;
          approve.disabled = true;
          ctx.api("/api/approve", { method: "POST", body: { id: m.id, answer: "approve" } }).then(function () {
            ctx.refresh();
          }, function () {
            approve.disabled = false;
            approve.textContent = "Couldn’t send. Approve again";
          });
        });
        var decline = el("button", "btn");
        decline.type = "button";
        decline.textContent = "Decline";
        decline.setAttribute("data-ask", m.id || "");
        decline.addEventListener("click", function () { openSheet(art, act, decline, m); });
        act.appendChild(approve);
        act.appendChild(decline);
        var plan = el("a", "link");
        plan.href = "#/messages?thread=" + encodeURIComponent(String(m.thread || m.id || ""));
        plan.textContent = "Show the plan";
        act.appendChild(plan);
        act.appendChild(feedbackPair(m.id));
        art.appendChild(top);
        art.appendChild(h3);
        art.appendChild(act);
        return art;
      }

      function openSheet(art, act, decline, m) {
        if (sheet || dead) return;
        sheet = true;
        act.hidden = true;
        var picked = "";
        var box = el("div", "why-box");
        box.setAttribute("role", "dialog");
        box.setAttribute("aria-modal", "true");
        box.setAttribute("aria-label", "Why decline?");
        var title = el("b");
        title.textContent = "Why decline?";
        var chips = el("div", "rchips");
        var note = el("span", "why-note");
        note.textContent = "Planner gets your reason as the answer.";
        var row = el("div", "why-act");
        var send = el("button", "btn btn-accent");
        send.type = "button";
        send.textContent = "Send decline";
        send.disabled = true;
        var cancel = el("button", "btn");
        cancel.type = "button";
        cancel.textContent = "Cancel";
        var first = null;
        for (var i = 0; i < REASONS.length; i++) {
          (function (reason) {
            var chip = el("button", "rchip");
            chip.type = "button";
            chip.textContent = reason.label;
            chip.setAttribute("aria-pressed", "false");
            chip.addEventListener("click", function () {
              picked = reason.reason;
              var all = buttons(chips);
              for (var k = 0; k < all.length; k++) {
                var on = all[k] === chip;
                all[k].setAttribute("aria-pressed", on ? "true" : "false");
                if (all[k].classList) all[k].classList.toggle("on", on);
              }
              send.disabled = false;
            });
            if (!first) first = chip;
            chips.appendChild(chip);
          })(REASONS[i]);
        }
        function doneTrap() {
          if (untrap) untrap();
          untrap = null;
        }
        function closeSheet() {
          var id = m.id;
          var redo = dirty;
          sheet = false;
          dirty = false;
          doneTrap();
          if (redo) {
            draw(last, stoppedAt);
            var again = findDecline(id);
            if (again && again.focus) again.focus();
            return;
          }
          if (box.parentNode) box.parentNode.removeChild(box);
          act.hidden = false;
          if (decline.focus) decline.focus();
        }
        function onKey(e) {
          if (!sheet) return;
          if (e.key === "Escape") {
            if (e.preventDefault) e.preventDefault();
            closeSheet();
            return;
          }
          if (e.key !== "Tab") return;
          var list = box.querySelectorAll("button, [href], input, select, textarea");
          var usable = [];
          for (var n = 0; n < list.length; n++) usable.push(list[n]);
          if (!usable.length) return;
          var start = usable[0];
          var end = usable[usable.length - 1];
          if (e.shiftKey && document.activeElement === start) {
            if (e.preventDefault) e.preventDefault();
            end.focus();
          } else if (!e.shiftKey && document.activeElement === end) {
            if (e.preventDefault) e.preventDefault();
            start.focus();
          }
        }
        document.addEventListener("keydown", onKey);
        untrap = function () { document.removeEventListener("keydown", onKey); };
        cancel.addEventListener("click", closeSheet);
        send.addEventListener("click", function () {
          if (send.disabled || !picked) return;
          var reason = picked;
          send.disabled = true;
          ctx.api("/api/approve", {
            method: "POST",
            body: { id: m.id, answer: "decline", reason: reason }
          }).then(function () {
            sheet = false;
            dirty = false;
            doneTrap();
            ctx.refresh();
          }, function () {
            // The sheet and the picked reason stay, so the user can send again.
            send.disabled = false;
            send.textContent = "Couldn’t send. Send decline again";
          });
        });
        row.appendChild(send);
        row.appendChild(cancel);
        box.appendChild(title);
        box.appendChild(chips);
        box.appendChild(note);
        box.appendChild(row);
        art.appendChild(box);
        if (first && first.focus) first.focus();
      }

      function company(data, blank) {
        var sec = el("section", "sec");
        var head = el("div", "sec-h");
        var h2 = el("h2");
        h2.textContent = "Your company";
        var rt = el("div", "rt");
        var seg = el("div", "co-view");
        seg.setAttribute("role", "group");
        seg.setAttribute("aria-label", "Company view");
        var columns = el("button", "on");
        columns.type = "button";
        columns.setAttribute("aria-pressed", "true");
        columns.textContent = "Columns";
        var map = el("a", "link");
        map.href = "/map";
        map.textContent = "Map";
        seg.appendChild(columns);
        seg.appendChild(map);
        rt.appendChild(seg);
        head.appendChild(h2);
        head.appendChild(rt);
        sec.appendChild(head);
        var seats = data.seats || {};
        var pair = lanes(tally(data), blank);
        var grid = el("div", "roles");
        grid.appendChild(column("hq", seats.hq, data, blank));
        grid.appendChild(laneEl(pair[0]));
        grid.appendChild(column("lab", seats.lab, data, blank));
        grid.appendChild(laneEl(pair[1]));
        grid.appendChild(column("factory", seats.factory, data, blank));
        sec.appendChild(grid);
        return sec;
      }

      function column(role, members, data, blank) {
        var col = el("div", "rcol");
        var h = el("div", "rcol-h");
        var b = el("b");
        b.textContent = roleWord(role);
        var verb = el("span");
        verb.textContent = VERB[role] || "";
        h.appendChild(b);
        h.appendChild(verb);
        col.appendChild(h);
        var list = members || [];
        for (var i = 0; i < list.length; i++) col.appendChild(memberCard(list[i], role, data, blank, i === 0));
        if (role === "hq") col.appendChild(teamsSlot());
        return col;
      }

      function memberCard(m, role, data, blank, first) {
        var card = el("div", "tool");
        var top = el("div", "tool-top");
        var id = el("div", "tool-id");
        var isYou = m.name === "You";
        var b = el("b");
        b.textContent = m.name || "";
        var tool = el("span");
        tool.textContent = isYou ? "In this page" : toolName(m.tool);
        id.appendChild(b);
        id.appendChild(tool);
        var waiting = m.waiting || 0;
        var state = m.state || "idle";
        var n = waiting;
        if (state === "needs_you" && !n) n = 1;
        top.appendChild(id);
        top.appendChild(memberMark(state, n, isYou && state !== "needs_you" && !waiting));
        card.appendChild(top);
        var taskText = "";
        if (isYou && (state === "needs_you" || waiting > 0)) taskText = "Final yes on every ask. Approve or decline in Waiting on you.";
        else if (isYou) taskText = "Asks from Lab appear here when they arrive.";
        else if (blank && first && state === "idle" && role === "lab") taskText = "No job yet. Ask Planner for a plan in its chat.";
        else if (blank && first && state === "idle" && role === "factory") taskText = "Waiting for its first job.";
        if (taskText) {
          var task = el("div", "task");
          task.textContent = taskText;
          card.appendChild(task);
        }
        var bits = [];
        if (!isYou && m.repo) bits.push(m.repo);
        if (!isYou && m.since) {
          var since = age(m.since, data.generated);
          if (since) bits.push(since);
        }
        if (!isYou && waiting > 0 && state !== "needs_you") bits.push(waiting === 1 ? "1 job waiting" : waiting + " jobs waiting");
        if (bits.length) {
          var meta = el("div", "meta");
          for (var i = 0; i < bits.length; i++) {
            var sp = el("span");
            sp.textContent = bits[i];
            meta.appendChild(sp);
          }
          card.appendChild(meta);
        }
        return card;
      }

      function teamsSlot() {
        var slot = el("div", "slot");
        var ring = el("span", "ring");
        ring.setAttribute("aria-hidden", "true");
        var span = el("span");
        var b = el("b");
        b.textContent = "Coco Teams";
        span.appendChild(b);
        span.appendChild(document.createTextNode(" can be your HQ from a browser or phone. Planned."));
        slot.appendChild(ring);
        slot.appendChild(span);
        return slot;
      }

      function movingSec(moving, data) {
        var sec = el("section", "sec");
        var head = el("div", "sec-h");
        var h2 = el("h2");
        h2.textContent = "Moving now";
        head.appendChild(h2);
        if (moving.length) {
          var all = el("a", "link");
          all.href = "#/messages";
          all.textContent = "All messages";
          head.appendChild(all);
        }
        sec.appendChild(head);
        if (!moving.length) {
          var p = el("p", "empty");
          p.appendChild(document.createTextNode("No messages yet today. When Planner sends Builder a job, it shows here. "));
          var a = el("a", "link");
          a.href = "#/now";
          a.textContent = "Send a test message";
          p.appendChild(a);
          sec.appendChild(p);
          return sec;
        }
        var list = el("div", "msgs");
        for (var i = 0; i < moving.length; i++) list.appendChild(msgRow(moving[i], data));
        sec.appendChild(list);
        return sec;
      }

      function msgRow(m, data) {
        var row = el("div", "msg" + (m.state === "failed" ? " fail" : ""));
        var t = el("span", "t");
        t.textContent = clock(m.ts);
        var flow = el("span", "ft");
        var from = el("b");
        from.textContent = person(data.seats, m.from);
        var ar = el("span", "ar");
        ar.textContent = "→";
        var to = el("b");
        to.textContent = person(data.seats, m.to);
        flow.appendChild(from);
        flow.appendChild(ar);
        flow.appendChild(to);
        var sum = el("span", "sum");
        var s = el("span");
        s.textContent = m.summary || "";
        var repo = el("span", "repo");
        repo.textContent = m.repo || "";
        sum.appendChild(s);
        sum.appendChild(repo);
        var via = toolList(m);
        if (via) {
          var chip = el("span", "repo");
          chip.textContent = via;
          sum.appendChild(chip);
        }
        row.appendChild(t);
        row.appendChild(flow);
        row.appendChild(sum);
        row.appendChild(msgMark(m));
        return row;
      }

      // ponytail: polling every 5s, SSE later if it matters
      timer = setInterval(pull, 5000);
      pull();
    }
  };
})();

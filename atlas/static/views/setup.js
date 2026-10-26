/* First run. Steps 1–3, then 5. Step 4 (Connect) is a later slice. */
(function () {
  var SUPPORTED = ["claude-code", "cursor", "codex", "grok"];
  var PLACEHOLDER = "e.g. A small web shop: api-server is the backend, web-app is the storefront.";
  var HELP_CLAUDE = "Claude Code reads this at the start of each session, so it knows what you're building. Like anything you type to it, it's sent to Claude Code's own service.";
  var HELP_ALL = "The AI tools you've given a role read this at the start of each session, so they know what you're building. Like anything you type to them, it's sent to each tool's own service.";
  var CARDS = [
    ["HQ", "Decides", "Sets priorities, approves and merges, and reads the daily summary.", "You, or a tool such as Grok"],
    ["Lab", "Thinks", "Talks with you, plans, splits work into jobs and reviews what comes back.", "For example, a Claude Code chat"],
    ["Factory", "Builds", "Takes one job, does it and reports back.", "For example, Cursor"]
  ];

  var step = 1;
  var tools = [];
  var suggested = null;
  var picked = {};
  var seats = { hq: "you", lab: null, factory: [] };
  var projectText = "";
  var projectField = null;
  var errorText = "";
  var rootEl = null;
  var ctxApi = null;
  var checkResult = null;
  var findingPayload = null;
  var req = 0;
  var NONE_LINE = "I didn't find anything to flag in Hooks & checks or Repo standard.";
  var FINDING_HREF = {
    "Open Repos": "#/repos"
  };

  function el(tag, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function text(tag, className, value) {
    var node = el(tag, className);
    node.textContent = value;
    return node;
  }

  function btn(label, className, fn) {
    var node = el("button", className || "btn");
    node.type = "button";
    node.textContent = label;
    node.addEventListener("click", function () { return fn(); });
    return node;
  }

  function isSupported(id) {
    return SUPPORTED.indexOf(id) >= 0;
  }

  function nice(id) {
    if (id === "you") return "You";
    var hp = window.HP;
    if (hp && typeof hp.toolName === "function") {
      var named = hp.toolName(id);
      if (named && named !== id) return named;
    }
    for (var i = 0; i < tools.length; i++) if (tools[i].id === id) return tools[i].name;
    return id;
  }

  function ticked() {
    var ids = [];
    for (var i = 0; i < tools.length; i++) {
      var row = tools[i];
      if (row.found && isSupported(row.id) && picked[row.id] !== false) ids.push(row.id);
    }
    return ids;
  }

  function supportedFound() {
    var rows = [];
    for (var i = 0; i < tools.length; i++) {
      if (tools[i].found && isSupported(tools[i].id)) rows.push(tools[i]);
    }
    return rows;
  }

  function suggestFrom(ids) {
    if (!ids.length) return null;
    if (ids.length === 1) return { hq: "you", lab: ids[0], factory: [ids[0]] };
    var lab = ids.indexOf("claude-code") >= 0 ? "claude-code" : ids.filter(function (id) { return id !== "cursor"; })[0];
    var factory = ids.indexOf("cursor") >= 0 && lab !== "cursor"
      ? ["cursor"]
      : ids.filter(function (id) { return id !== lab; }).slice(0, 1);
    return { hq: "you", lab: lab, factory: factory };
  }

  function covers(sug, ids) {
    if (!sug || !sug.lab || ids.indexOf(sug.lab) < 0) return false;
    if (sug.hq && sug.hq !== "you" && ids.indexOf(sug.hq) < 0) return false;
    var fac = sug.factory || [];
    if (!fac.length) return false;
    for (var i = 0; i < fac.length; i++) if (ids.indexOf(fac[i]) < 0) return false;
    return true;
  }

  function activeSuggestion() {
    var ids = ticked();
    if (covers(suggested, ids)) return suggested;
    return suggestFrom(ids);
  }

  function readProject() {
    if (projectField && typeof projectField.value === "string") projectText = projectField.value;
  }

  function header(root, n) {
    root.appendChild(text("p", "step", n + " of 5"));
  }

  function actions(root, children) {
    var bar = el("div", "actions");
    for (var i = 0; i < children.length; i++) bar.appendChild(children[i]);
    root.appendChild(bar);
  }

  function findTools() {
    errorText = "";
    return ctxApi("/api/setup/tools").then(function (data) {
      tools = (data && data.tools) || [];
      suggested = data ? data.suggested : null;
      picked = {};
      step = 2;
      paint();
    }).catch(function () {
      errorText = "Couldn't check this machine.";
      paint();
    });
  }

  function goPick() {
    var sug = activeSuggestion();
    if (!sug || !sug.lab) return;
    seats = { hq: sug.hq || "you", lab: sug.lab, factory: (sug.factory || []).slice() };
    step = 3;
    paint();
  }

  function setSeat(seat, value) {
    if (seat === "hq") seats.hq = value;
    else if (seat === "lab") seats.lab = value;
    else seats.factory = [value];
    if (seats.hq && seats.hq !== "you" && (seats.hq === seats.lab || (seats.factory || []).indexOf(seats.hq) >= 0)) {
      seats.hq = "you";
    }
    paint();
  }

  function goalBody() {
    var raw = projectField && typeof projectField.value === "string" ? projectField.value : projectText;
    var textValue = String(raw || "").trim();
    var body = { roles: { hq: seats.hq, lab: seats.lab, factory: (seats.factory || []).slice() } };
    if (textValue && textValue !== PLACEHOLDER) body.goal = textValue;
    return body;
  }

  function callApi(path) {
    return new Promise(function (resolve) {
      try {
        if (typeof ctxApi !== "function") {
          resolve(null);
          return;
        }
        Promise.resolve(ctxApi(path)).then(resolve, function () { resolve(null); });
      } catch (err) {
        resolve(null);
      }
    });
  }

  function normalizeCheck(data) {
    var member = "Builder";
    var ms = 0;
    if (data && typeof data === "object") {
      if (typeof data.member === "string" && data.member) member = data.member;
      if (typeof data.ms === "number" && isFinite(data.ms)) ms = data.ms;
    }
    return { ok: !!(data && data.ok === true), ms: ms, member: member };
  }

  function normalizeFinding(data) {
    var none = NONE_LINE;
    if (!data || typeof data !== "object") return { finding: null, none_line: none };
    if (typeof data.none_line === "string" && data.none_line) none = data.none_line;
    var found = data.finding;
    if (!found || typeof found !== "object" || typeof found.line !== "string" || !found.line) {
      return { finding: null, none_line: none };
    }
    if (typeof found.link !== "string" || !found.link) return { finding: null, none_line: none };
    return { finding: { kind: found.kind, line: found.line, link: found.link }, none_line: none };
  }

  function loadReady() {
    return Promise.all([
      callApi("/api/setup/check").then(normalizeCheck),
      callApi("/api/first-finding").then(normalizeFinding)
    ]);
  }

  function runReady() {
    var token = ++req;
    var rootNow = rootEl;
    return loadReady().then(function (pair) {
      if (token !== req || rootEl !== rootNow) return;
      checkResult = pair[0];
      findingPayload = pair[1];
      step = 5;
      paint();
    });
  }

  function secondsOf(ms) {
    var n = Math.ceil(ms / 1000);
    if (!isFinite(n) || n < 0) n = 0;
    return n;
  }

  function workLine(check) {
    var n = secondsOf(check.ms);
    return "It works. " + check.member + "'s queue answered in " + n + " " + (n === 1 ? "second." : "seconds.");
  }

  function nowLink(primary) {
    var go = el("a", primary ? "btn btn-accent" : "btn");
    go.textContent = "Open Now";
    go.setAttribute("href", "#/now");
    return go;
  }

  function save() {
    readProject();
    errorText = "";
    return ctxApi("/api/roles", { method: "POST", body: goalBody() }).then(function () {
      // ponytail: step 4 Connect lands with the connect-writer stack; until then 3 goes to 5.
      return runReady();
    }).catch(function () {
      errorText = "Couldn't save roles.";
      paint();
    });
  }

  function paintIntro(root) {
    header(root, 1);
    root.appendChild(text("p", "eyebrow", "Set up"));
    root.appendChild(text("h1", "", "Heliopause runs your AI tools like a small company."));
    root.appendChild(text("p", "lead", "You choose which tool plays each role. Heliopause carries the messages between them and shows every one on this page."));
    var cards = el("div", "cards");
    for (var i = 0; i < CARDS.length; i++) {
      var card = el("section", "card");
      card.appendChild(text("h2", "", CARDS[i][0]));
      card.appendChild(text("p", "verb", CARDS[i][1]));
      card.appendChild(text("p", "", CARDS[i][2]));
      card.appendChild(text("p", "note", CARDS[i][3]));
      cards.appendChild(card);
    }
    root.appendChild(cards);
    root.appendChild(text("p", "note", "Heliopause keeps messages on this machine. Each tool you connect sends its own work to its own service, under its terms."));
    actions(root, [btn("Find my tools", "btn btn-accent", findTools)]);
  }

  function toolRow(row) {
    var line = el("div", "row");
    if (row.found && isSupported(row.id)) {
      var box = el("input");
      box.type = "checkbox";
      box.checked = picked[row.id] !== false;
      box.setAttribute("aria-label", row.name);
      box.addEventListener("change", function () {
        picked[row.id] = box.checked;
        paint();
      });
      line.appendChild(box);
      line.appendChild(text("span", "name", row.name));
      if (row.cli) line.appendChild(text("span", "", "Command line found"));
      return line;
    }
    if (row.found) {
      line.appendChild(text("span", "name", row.name));
      line.appendChild(text("span", "unsup", "Found. Not supported yet."));
      if (row.cli) line.appendChild(text("span", "", "Command line found"));
      return line;
    }
    line.className = "row miss";
    line.appendChild(text("span", "", row.name + ": Not found on this machine"));
    var link = el("a");
    link.textContent = "Get " + row.name;
    link.setAttribute("href", row.install_url || "");
    link.setAttribute("rel", "noreferrer");
    line.appendChild(link);
    return line;
  }

  function paintFind(root) {
    header(root, 2);
    var none = supportedFound().length === 0;
    root.appendChild(text("p", "eyebrow", "Find tools"));
    if (none) {
      root.appendChild(text("h1", "", "No supported tool on this machine yet."));
      root.appendChild(text("p", "lead", "Heliopause works with Claude Code, Cursor, Codex and Grok. Install one of them, then check again. Heliopause never installs a tool."));
    } else {
      root.appendChild(text("h1", "", "These tools are on this machine."));
      root.appendChild(text("p", "lead", "Any tool can play any role. Untick any you don't want Heliopause to watch. It checks when sessions start and stop. It doesn't open, keep or send your chat text."));
      root.appendChild(text("p", "note", "Heliopause never installs a tool."));
    }
    var list = el("div", "rows");
    for (var i = 0; i < tools.length; i++) list.appendChild(toolRow(tools[i]));
    root.appendChild(list);
    var back = btn("Back", "btn", function () { step = 1; paint(); });
    if (none) {
      actions(root, [back, btn("Check again", "btn btn-accent", findTools)]);
      return;
    }
    var next = btn("Next: pick roles", "btn btn-accent", goPick);
    next.disabled = ticked().length === 0;
    actions(root, [back, next]);
  }

  function choiceLabel(seat, id) {
    if (seat === "hq") return id === "you" ? "You, in this page" : nice(id) + ", named Reviewer";
    if (seat === "lab") return nice(id) + ", named Planner";
    if (id === seats.lab) return nice(id) + ", named Builder, a second chat";
    return nice(id) + ", named Builder";
  }

  function choices(seat) {
    var ids = ticked();
    var list = [];
    if (seat === "hq") {
      list.push("you");
      for (var i = 0; i < ids.length; i++) {
        if (ids[i] !== seats.lab && (seats.factory || []).indexOf(ids[i]) < 0) list.push(ids[i]);
      }
      return list;
    }
    return ids.filter(function (id) { return id !== seats.hq; });
  }

  function seatCard(seat, title) {
    var card = el("div", "seat");
    var current = seat === "factory" ? (seats.factory || [])[0] : seats[seat];
    var options = choices(seat);
    if (options.indexOf(current) < 0 && current) options.unshift(current);
    card.appendChild(text("p", "role", title));
    card.appendChild(text("p", "who", current ? choiceLabel(seat, current) : ""));
    var sel = el("select");
    sel.setAttribute("aria-label", title);
    for (var i = 0; i < options.length; i++) {
      var opt = el("option");
      opt.value = options[i];
      opt.textContent = choiceLabel(seat, options[i]);
      if (options[i] === current) opt.setAttribute("selected", "selected");
      sel.appendChild(opt);
    }
    sel.value = current || "";
    sel.addEventListener("change", function () { setSeat(seat, sel.value); });
    card.appendChild(sel);
    return card;
  }

  function suggestionLine() {
    var one = supportedFound();
    if (one.length === 1) {
      var name = nice(one[0].id);
      return "Only " + name + " was found. One " + name + " chat plans and another builds. You approve here.";
    }
    var hq = seats.hq === "you" ? "You decide" : nice(seats.hq) + " decides";
    var fac = (seats.factory || [])[0];
    return "Suggested: " + hq + ", " + nice(seats.lab) + " thinks and " + nice(fac) + " builds. Nothing merges without your yes.";
  }

  function paintPick(root) {
    header(root, 3);
    root.appendChild(text("p", "eyebrow", "Pick roles"));
    root.appendChild(text("h1", "", "Who plays each role?"));
    root.appendChild(text("p", "lead", suggestionLine()));
    var list = el("div", "seats");
    list.appendChild(seatCard("hq", "HQ"));
    list.appendChild(seatCard("lab", "Lab"));
    list.appendChild(seatCard("factory", "Factory"));
    root.appendChild(list);
    if (ticked().indexOf("claude-code") >= 0) {
      root.appendChild(btn("Keep it simple: you as HQ, Claude Code for Lab and Factory", "btn link", function () {
        seats = { hq: "you", lab: "claude-code", factory: ["claude-code"] };
        paint();
      }));
    }
    var label = el("label", "field");
    label.setAttribute("for", "hp-setup-project");
    label.appendChild(document.createTextNode("What's this project about? (1-2 lines, optional)"));
    var area = el("textarea");
    area.id = "hp-setup-project";
    area.setAttribute("placeholder", PLACEHOLDER);
    area.value = projectText;
    area.addEventListener("input", function () { projectText = area.value; });
    projectField = area;
    label.appendChild(area);
    root.appendChild(label);
    var help = HELP_ALL;
    var seated = [seats.hq, seats.lab].concat(seats.factory || []);
    if (seated.indexOf("claude-code") >= 0) help = HELP_CLAUDE;
    root.appendChild(text("p", "help", help));
    // The shell sets HP.briefHref when a brief GET exists. This page does not invent one.
    var href = window.HP && window.HP.briefHref;
    if (typeof href === "string" && href) {
      var see = el("a");
      see.textContent = "See what it reads";
      see.setAttribute("href", href);
      root.appendChild(see);
    }
    actions(root, [
      btn("Back", "btn", function () { readProject(); step = 2; paint(); }),
      btn("Next", "btn btn-accent", save)
    ]);
  }

  function paintReady(root) {
    header(root, 5);
    var check = checkResult || { ok: false, ms: 0, member: "Builder" };
    if (!check.ok) {
      root.appendChild(text("h1", "", "Almost set up."));
      root.appendChild(text("p", "bad", "\u25B2 Messages didn't come back from " + check.member + "'s queue, so Heliopause can't carry jobs yet."));
      root.appendChild(text("p", "help", "Run cd ~/Heliopause && python3 -B -m atlas doctor in a terminal, then try again."));
      actions(root, [btn("Try again", "btn btn-accent", runReady), nowLink(false)]);
      return;
    }
    var pack = findingPayload || { finding: null, none_line: NONE_LINE };
    root.appendChild(text("h1", "", "You're set up."));
    if (pack.finding) {
      root.appendChild(text("p", "lead", "Here's one thing I found in your setup:"));
      var flag = el("p", "flag");
      flag.appendChild(text("span", "mark", "\u25C7"));
      flag.appendChild(document.createTextNode(" " + pack.finding.line));
      root.appendChild(flag);
      var href = FINDING_HREF[pack.finding.link];
      if (href) {
        var link = el("a", "found-link");
        link.textContent = pack.finding.link;
        link.setAttribute("href", href);
        root.appendChild(link);
      }
    } else {
      root.appendChild(text("p", "none", pack.none_line || NONE_LINE));
    }
    root.appendChild(text("p", "quiet", workLine(check)));
    actions(root, [nowLink(true)]);
  }

  function paint() {
    if (!rootEl) return;
    readProject();
    rootEl.textContent = "";
    rootEl.className = "hp-setup";
    if (step === 2) paintFind(rootEl);
    else if (step === 3) paintPick(rootEl);
    else if (step === 5) paintReady(rootEl);
    else paintIntro(rootEl);
    if (errorText) rootEl.appendChild(text("p", "err", errorText));
  }

  function render(root, ctx) {
    if (rootEl !== root) {
      req += 1;
      step = 1;
      tools = [];
      suggested = null;
      picked = {};
      seats = { hq: "you", lab: null, factory: [] };
      projectText = "";
      projectField = null;
      errorText = "";
      checkResult = null;
      findingPayload = null;
    }
    rootEl = root;
    ctxApi = ctx && ctx.api;
    paint();
  }

  if (typeof window !== "undefined") {
    if (typeof document !== "undefined" && document.querySelector && !document.querySelector('link[href="/views/setup.css"]')) {
      var sheet = document.createElement("link");
      sheet.rel = "stylesheet";
      sheet.href = "/views/setup.css";
      (document.head || document.documentElement).appendChild(sheet);
    }
    window.HP = window.HP || {};
    window.HP.views = window.HP.views || {};
    window.HP.views.setup = { title: "Set up", render: render };
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { render: render };
})();

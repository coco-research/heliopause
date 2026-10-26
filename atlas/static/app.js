/* Page shell. Views register on window.HP.views from /views/<name>.js. */
(function () {
  var VIEWS = ["now", "messages", "today", "repos", "setup"];
  var OFF_MACHINE = "Heliopause sends nothing off this machine.";
  var GITHUB_ON = "Heliopause sends only PR and CI lookups to GitHub.";
  var THEME_KEY = "atlas.theme";
  var WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  var HP = window.HP || (window.HP = {});
  HP.views = HP.views || {};

  function storedTheme() {
    try {
      var v = localStorage.getItem(THEME_KEY) || "";
      if (v === "light" || v === "dark" || v === "system") return v;
    } catch (e) {}
    return "";
  }

  function systemDark() {
    try {
      return !!window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch (e) {
      return false;
    }
  }

  // Nothing stored, or System: follow the OS. Never stamp data-theme="light" for that case.
  function applyTheme(mode) {
    var root = document.documentElement;
    if (!root || !root.setAttribute) return;
    var pick = mode === "light" || mode === "dark" ? mode : "";
    if (!pick) pick = systemDark() ? "dark" : "";
    if (pick) root.setAttribute("data-theme", pick);
    else if (root.removeAttribute) root.removeAttribute("data-theme");
  }

  function markTheme() {
    if (!document.querySelectorAll) return;
    var mode = storedTheme();
    if (mode !== "light" && mode !== "dark") mode = "system";
    var buttons = document.querySelectorAll(".hp .theme button");
    for (var i = 0; i < buttons.length; i++) {
      var on = buttons[i].getAttribute("data-theme-mode") === mode;
      buttons[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  function chooseTheme(mode) {
    try { localStorage.setItem(THEME_KEY, mode); } catch (e) {}
    applyTheme(mode);
    markTheme();
  }
  HP.chooseTheme = chooseTheme;

  function watchSystem() {
    var mq;
    try { mq = window.matchMedia("(prefers-color-scheme: dark)"); } catch (e) { return; }
    if (!mq) return;
    var onChange = function () {
      var mode = storedTheme();
      if (mode === "light" || mode === "dark") return;
      applyTheme("");
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  function ymd(d) {
    return d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate();
  }

  // One clock for Now and Messages. Same local day: "{n} min" under 60, else "{n} h".
  // The previous local calendar day is "yesterday" even when that is under 24 hours.
  HP.age = function (ts, nowIso) {
    var a = new Date(ts || "");
    var b = new Date(nowIso || "");
    if (isNaN(a.getTime()) || isNaN(b.getTime())) return "";
    if (ymd(a) === ymd(b)) {
      var mins = Math.floor((b.getTime() - a.getTime()) / 60000);
      if (mins < 0) mins = 0;
      if (mins < 60) return mins + " min";
      return Math.floor(mins / 60) + " h";
    }
    var prev = new Date(b.getFullYear(), b.getMonth(), b.getDate() - 1);
    if (ymd(a) === ymd(prev)) return "yesterday";
    return WEEKDAYS[a.getDay()];
  };

  applyTheme(storedTheme());

  var TOOL_NAMES = {
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "codex": "Codex",
    "grok": "Grok",
    "you": "You"
  };

  // One map for every screen. Unknown ids stay as they are.
  HP.toolName = function (id) {
    if (id == null || id === "") return "";
    var key = String(id);
    if (Object.prototype.hasOwnProperty.call(TOOL_NAMES, key)) return TOOL_NAMES[key];
    return key;
  };

  function listenAddr() {
    var p = location.port;
    if (!p) p = location.protocol === "https:" ? "443" : "80";
    return "127.0.0.1:" + p;
  }

  HP.setPrivacy = function (github) {
    var line = github ? GITHUB_ON : OFF_MACHINE;
    var addr = listenAddr();
    var priv = document.getElementById("hp-priv");
    var ad = document.getElementById("hp-addr");
    var mid = document.getElementById("hp-mid-priv");
    if (priv) priv.textContent = line;
    if (ad) ad.textContent = addr;
    if (mid) mid.textContent = line + " · " + addr;
  };

  var nonce = "";

  function api(path, opts) {
    opts = opts || {};
    var method = String(opts.method || "GET").toUpperCase();
    function send(token) {
      var headers = {};
      var init = {
        method: method,
        credentials: "same-origin",
        cache: "no-store",
        headers: headers
      };
      if (method !== "GET" && method !== "HEAD") {
        headers["Content-Type"] = "application/json";
        headers["X-Atlas-UI"] = token || "";
        init.body = JSON.stringify(opts.body === undefined ? {} : opts.body);
      }
      return fetch(path, init).then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      });
    }
    var writing = method !== "GET" && method !== "HEAD";
    if (!writing || nonce) return send(nonce);
    return fetch("/api/state", { credentials: "same-origin", cache: "no-store" }).then(function (res) {
      if (!res.ok) return {};
      return res.json();
    }).then(function (data) {
      if (data && typeof data.nonce === "string") nonce = data.nonce;
      return send(nonce);
    });
  }

  function viewFromHash() {
    var raw = location.hash || "";
    var path = raw.replace(/^#\/?/, "").split("?")[0];
    var name = path.split("/")[0] || "now";
    if (VIEWS.indexOf(name) < 0) name = "now";
    return name;
  }
  HP.viewFromHash = viewFromHash;

  var current = "now";
  var loading = {};

  function markNav(name) {
    var links = document.querySelectorAll(".hp .nav a[data-view]");
    for (var i = 0; i < links.length; i++) {
      if (links[i].getAttribute("data-view") === name) links[i].setAttribute("aria-current", "page");
      else links[i].removeAttribute("aria-current");
    }
  }

  function paint(name) {
    current = name;
    markNav(name);
    var root = document.getElementById("hp-view");
    if (!root) return;
    if (typeof root._hpStop === "function") {
      root._hpStop();
      root._hpStop = null;
    }
    while (root.firstChild) root.removeChild(root.firstChild);
    var view = HP.views[name];
    if (!view || typeof view.render !== "function") {
      var p = document.createElement("p");
      p.className = "empty";
      p.textContent = "Not built yet.";
      root.appendChild(p);
      return;
    }
    view.render(root, {
      api: api,
      refresh: function () { paint(current); }
    });
  }

  function ensure(name) {
    if (HP.views[name]) return Promise.resolve();
    if (loading[name]) return loading[name];
    loading[name] = new Promise(function (resolve) {
      var s = document.createElement("script");
      s.src = "/views/" + name + ".js";
      s.onload = function () { resolve(); };
      s.onerror = function () { resolve(); };
      document.body.appendChild(s);
    });
    return loading[name];
  }

  function route() {
    var name = viewFromHash();
    ensure(name).then(function () {
      if (viewFromHash() !== name) return;
      paint(name);
    });
  }

  function seatsChosen(data) {
    var roles = data && data.roles;
    if (!roles) return false;
    if (roles.hq) return true;
    if (roles.lab) return true;
    return !!(roles.factory && roles.factory.length);
  }

  function openingNow(hash) {
    return hash === "" || hash === "#" || hash === "#/" || hash === "#/now";
  }

  // First paint only. A later click on Now stays on Now. No lab: stay on Now.
  function openFirst() {
    var hash = location.hash || "";
    if (!openingNow(hash) || typeof fetch !== "function") { route(); return; }
    api("/api/roles").then(function (data) {
      var still = location.hash || "";
      if (openingNow(still) && !seatsChosen(data)) { location.hash = "#/setup"; return; }
      route();
    }).catch(function () { route(); });
  }

  function boot() {
    if (HP._shell) return;
    HP._shell = 1;
    markTheme();
    if (document.querySelectorAll) {
      var buttons = document.querySelectorAll(".hp .theme button");
      for (var i = 0; i < buttons.length; i++) {
        buttons[i].addEventListener("click", function () {
          chooseTheme(this.getAttribute("data-theme-mode"));
        });
      }
    }
    watchSystem();
    window.addEventListener("hashchange", route);
    HP.setPrivacy(false);
    if (document.getElementById && document.getElementById("hp-view")) openFirst();
  }

  if (document.body) boot();
  else if (document.addEventListener) document.addEventListener("DOMContentLoaded", boot);
})();

// Pure status line, needs-you list, and FYI groups. No DOM, no fetch.
"use strict";

(function () {
  const PHRASE = {
    "ci-red": "red CI", "pr-stale": "stale PR", "job-failed": "failed job", "job-stuck": "stuck job",
    "quiet": "quiet agent", "decision-waiting": "waiting decision", "hq-unanswered": "unanswered HQ ask",
    "no-lane": "job with no lane", "disk-low": "low disk", "budget-near-cap": "budget near cap",
    "budget-over": "budget over cap", warn: "warning", info: "note"
  };
  const PLURAL = {
    "pr-stale": "stale PRs", "job-failed": "failed jobs", "job-stuck": "stuck jobs", "quiet": "quiet agents",
    "decision-waiting": "waiting decisions", "hq-unanswered": "unanswered HQ asks", "no-lane": "jobs with no lane",
    warn: "warnings", info: "notes"
  };
  const RANK = { high: 0, medium: 1, warn: 1, low: 2, info: 3 };

  function list(value) {
    return Array.isArray(value) ? value : [];
  }

  function openAlerts(alerts) {
    if (Array.isArray(alerts)) return alerts;
    return alerts && Array.isArray(alerts.open) ? alerts.open : [];
  }

  // Same rule as graph.js waitsOnOwner.
  function waitsOnOwner(problem) {
    return !!problem && problem.owner === "owner" && typeof problem.kind === "string" && !!problem.kind &&
      typeof problem.id === "string" && !!problem.id;
  }

  function stampMs(ts) {
    if (typeof ts === "number" && isFinite(ts)) return ts < 1e12 ? ts * 1000 : ts;
    if (typeof ts !== "string" || !ts) return null;
    const parsed = Date.parse(ts);
    return isNaN(parsed) ? null : parsed;
  }

  function subjectOf(id) {
    const cut = typeof id === "string" ? id.indexOf(":") : -1;
    return cut >= 0 ? id.slice(cut + 1) : "";
  }

  function decisionId(item) {
    if (item.node) return String(item.node);
    if (item.name) return String(item.name);
    if (item.id) return String(item.id);
    return "";
  }

  function decisionTitle(item) {
    if (typeof item.headline === "string" && item.headline) return item.headline;
    if (typeof item.title === "string" && item.title) return item.title;
    return (decisionId(item) || "An agent") + " is waiting";
  }

  function own(table, key) {
    return Object.prototype.hasOwnProperty.call(table, key);
  }

  function phrase(kind, n) {
    if (n !== 1 && own(PLURAL, kind)) return PLURAL[kind];
    if (own(PHRASE, kind)) return PHRASE[kind];
    if (!kind) return n === 1 ? "item" : "items";
    return n === 1 ? kind : kind + "s";
  }

  function rankOf(severity) {
    return own(RANK, severity) ? RANK[severity] : 4;
  }

  function problemPlace(problem) {
    const subject = subjectOf(problem && problem.id);
    const hash = subject.indexOf("#");
    if (hash > 0) return subject.slice(0, hash);
    if (problem && typeof problem.owner === "string" && problem.owner) return problem.owner;
    return subject;
  }

  // Decisions: waiting() (status needs-owner). Alerts: ownerAlerts (open, needs-owner, text).
  // kind alert / decision-waiting is the same row as that alert or decision. Order uses each row's own stamp.
  function needsYou(state, problems, alerts) {
    const rows = [];
    const seen = Object.create(null);
    function add(row, key) {
      if (!key || seen[key]) return;
      seen[key] = true;
      rows.push(row);
    }
    list(state && state.decisions).forEach(function (item) {
      if (!item || item.status !== "needs-owner") return;
      const id = decisionId(item);
      const title = decisionTitle(item);
      add({ kind: "decision", id: id, from: id, ts: item.ts, title: title }, "decision:" + (id || title));
    });
    openAlerts(alerts).forEach(function (row) {
      if (!row || row.severity !== "needs-owner" || typeof row.text !== "string") return;
      const id = typeof row.id === "string" ? row.id : "";
      add({
        kind: "alert", id: id, from: typeof row.source === "string" ? row.source : "", ts: row.ts, title: row.text
      }, "alert:" + (id || row.text));
    });
    list(problems).forEach(function (problem) {
      if (!waitsOnOwner(problem)) return;
      let key = "problem:" + problem.id;
      if (problem.kind === "alert") key = "alert:" + subjectOf(problem.id);
      else if (problem.kind === "decision-waiting") key = "decision:" + subjectOf(problem.id);
      add({
        kind: "problem", id: problem.id, from: problem.owner, ts: problem.since,
        title: typeof problem.headline === "string" ? problem.headline : ""
      }, key);
    });
    rows.sort(function (a, b) {
      const left = stampMs(a.ts);
      const right = stampMs(b.ts);
      if (left === null) return right === null ? 0 : 1;
      if (right === null) return -1;
      return left - right;
    });
    return rows;
  }

  function fyiGroups(problems, alerts) {
    const groups = Object.create(null);
    const order = [];
    function push(kind, place, severity) {
      let group = groups[kind + "\0" + place];
      if (!group) {
        group = { kind: kind, place: place, count: 0, worst: severity || "", rank: rankOf(severity) };
        groups[kind + "\0" + place] = group;
        order.push(group);
      }
      group.count += 1;
      if (rankOf(severity) < group.rank) {
        group.rank = rankOf(severity);
        group.worst = severity || group.worst;
      }
    }
    list(problems).forEach(function (problem) {
      if (!problem || waitsOnOwner(problem)) return;
      push(typeof problem.kind === "string" ? problem.kind : "", problemPlace(problem),
        typeof problem.severity === "string" ? problem.severity : "");
    });
    openAlerts(alerts).forEach(function (row) {
      if (!row || row.severity === "needs-owner" || typeof row.text !== "string") return;
      push(typeof row.kind === "string" && row.kind ? row.kind : (row.severity || ""),
        typeof row.source === "string" ? row.source : "", typeof row.severity === "string" ? row.severity : "");
    });
    order.sort(function (a, b) { return a.rank - b.rank || b.count - a.count; });
    return order.map(function (group) {
      let label = group.count + " " + phrase(group.kind, group.count);
      if (group.place) label += " on " + group.place;
      return { label: label, count: group.count, worst: group.worst };
    });
  }

  function blockedCount(state) {
    const graph = state && state.graph;
    if (!graph || !Array.isArray(graph.lanes) || !graph.nodes) return 0;
    let n = 0;
    graph.lanes.forEach(function (lane) {
      list(lane && lane.nodes).forEach(function (id) {
        const node = own(graph.nodes, id) ? graph.nodes[id] : null;
        if (node && node.state === "blocked") n += 1;
      });
    });
    return n;
  }

  // ponytail: job-failed rows and bus.queue.failed name the same jobs, so take the larger, not the sum.
  // Upgrade: id-match if those sets ever diverge.
  function brokenCount(state, problems) {
    let red = 0;
    let jobs = 0;
    list(problems).forEach(function (problem) {
      if (!problem || waitsOnOwner(problem)) return;
      if (problem.kind === "job-failed") { if (!problem.pipe) jobs += 1; }  // a failing pipe counts once, as its pipe-failing row
      else if (problem.kind === "ci-red" || problem.severity === "high") red += 1;
    });
    const jobq = state && state.bus && state.bus.queue;
    const failed = jobq && typeof jobq.failed === "number" && isFinite(jobq.failed) && jobq.failed > 0 ? jobq.failed : 0;
    return red + (failed > jobs ? failed : jobs) + blockedCount(state);
  }

  function plural(n, one, many) {
    return n === 1 ? one : many;
  }

  function headLine(needs, broken) {
    if (needs === 0 && broken === 0) return "Nothing needs you, and nothing is broken.";
    const parts = [];
    if (needs === 0) parts.push("Nothing needs you");
    else parts.push(needs + " " + plural(needs, "thing needs", "things need") + " you");
    if (broken > 0) parts.push(broken + " " + plural(broken, "thing is", "things are") + " broken");
    return parts.join(". ") + ".";
  }

  function agentFacts(state) {
    const graph = state && state.graph;
    const parts = [];
    const nodes = graph && graph.nodes;
    if (nodes && typeof nodes === "object" && !Array.isArray(nodes)) {
      let working = 0;
      let idle = 0;
      Object.keys(nodes).forEach(function (id) {
        const node = nodes[id];
        if (!node || typeof node.state !== "string") return;
        if (node.state === "working") working += 1;
        else if (node.state === "idle") idle += 1;
      });
      parts.push(working + " " + plural(working, "agent", "agents") + " working");
      parts.push(idle + " idle");
    }
    const messages = graph && graph.messages_24h;
    if (typeof messages === "number" && isFinite(messages)) {
      parts.push(messages + " " + plural(messages, "message", "messages") + " in the last 24 h");
    }
    return parts.join(" \u00b7 ");
  }

  function statusLine(state, problems, alerts) {
    const needs = needsYou(state, problems, alerts).length;
    const broken = brokenCount(state, problems);
    return { head: headLine(needs, broken), sub: agentFacts(state), needs: needs, broken: broken };
  }

  const api = { statusLine: statusLine, needsYou: needsYou, fyiGroups: fyiGroups };
  if (typeof window !== "undefined") window.AtlasStatus = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();

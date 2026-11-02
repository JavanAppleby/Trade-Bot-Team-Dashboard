"use strict";
// Renders the server's panels. All bot and web text goes in through textContent, never as HTML.

const BOTS = {
  guardian: { icon: "🛡️", blurb: "Protects and sells your positions" },
  radar: { icon: "📡", blurb: "Screens the market for movers" },
  scout: { icon: "🔭", blurb: "News and market context" },
  pilot: { icon: "✈️", blurb: "Paper buyer (practice trades)" },
};
const BADGES = { "LIVE": ["Live", "live"], "STALE": ["Stale", "stale"], "ERROR": ["Error", "error"],
  "STOPPED": ["Stopped", "grey"], "STARTING": ["Starting", "stale"], "NO DATA": ["No data", "grey"] };

// Columns whose values show as coloured pills, and how each value is coloured.
const PILL_COLS = new Set(["Level", "Status", "Protection", "State", "Taken", "Enabled", "Gap open", "Stale",
  "Halt open", "Breakeven armed", "Shadow", "Class", "Session", "Type", "Fresh catalyst",
  "Event before next session", "Macro next session", "Kind", "Why", "Radar"]);
const BAD_WHEN_YES = new Set(["Gap open", "Stale", "Halt open", "Shadow"]);
const TIME_COL = /^(time|found|opened|created|first|first seen|first buy|last ok|last seen|updated|closed|written|expires|flatten at)/i;
const SIGNED_COLS = new Set(["P&L $", "R", "Change"]);
const RED = ["error", "critical", "at risk", "stopped", "rejected", "stale", "http error", "timeout", "unprotected"];
const AMBER = ["warning", "starting", "intent", "pending", "partial", "unknown", "backoff"];
const GREEN = ["radar find", "ok", "working", "protected", "open", "live", "running", "filled", "yes", "managed", "breakeven",
  "target"];
const BLUE = ["pre-market", "regular hours", "after hours", "overnight", "wire", "filing", "stop", "r step",
  "atr trail", "pct trail", "initial", "flatten"];

const state = { data: null, bot: (location.hash || "#scout").slice(1), filters: {}, sorts: {}, timer: null };

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}

function tone(col, value) {
  const v = value.toLowerCase();
  if (!v) return "";
  if (BAD_WHEN_YES.has(col)) return v === "yes" ? "red" : "muted";
  if (col === "Protection" && v === "closed") return "muted";
  if (v === "no" || v === "closed" || v === "disabled") return "muted";
  if (RED.some((w) => v.startsWith(w))) return "red";
  if (AMBER.some((w) => v.startsWith(w))) return "amber";
  if (GREEN.some((w) => v.startsWith(w))) return "green";
  if (BLUE.some((w) => v.startsWith(w))) return "blue";
  return "violet";
}

function badge(code) {
  const [text, cls] = BADGES[code] || [code, "grey"];
  const b = el("span", "badge " + cls);
  b.append(el("span", "bdot"), document.createTextNode(text));
  return b;
}

function cellNode(col, text) {
  if (/^https?:\/\/\S+$/.test(text)) {
    const a = el("a", "link", "Open ↗");
    a.href = text; a.target = "_blank"; a.rel = "noopener noreferrer";
    return a;
  }
  if (PILL_COLS.has(col) && text) {
    if (col === "Protection" && text.includes(": ")) {
      const [head, rest] = [text.slice(0, text.indexOf(": ")), text.slice(text.indexOf(": ") + 2)];
      const wrap = el("span");
      wrap.append(el("span", "pill " + tone(col, head), head), el("span", "why", rest));
      return wrap;
    }
    return el("span", "pill " + tone(col, text), text);
  }
  if (SIGNED_COLS.has(col) && /^[-+]?[\d,.]+%?$/.test(text)) {
    const n = parseFloat(text.replace(/,/g, ""));
    return el("span", n > 0 ? "pos" : n < 0 ? "neg" : "", (n > 0 ? "+" : "") + text);
  }
  return document.createTextNode(text);
}

function renderFacts(facts) {
  const grid = el("div", "stats");
  for (const [label, value, level] of facts) {
    const card = el("div", "stat " + (level || ""));
    card.append(el("div", "slabel", label), el("div", "svalue", value === "" ? "–" : value));
    grid.append(card);
  }
  return grid;
}

function renderTable(t, bot, filterId) {
  const box = el("section", "card");
  const head = el("div", "chead");
  const h = el("h3", null, t.title);
  if (t.rows.length) h.append(el("span", "count", t.rows.length));
  head.append(h);
  let input = null;
  if (t.rows.length > 5) {
    input = el("input"); input.type = "search"; input.placeholder = "Filter…";
    input.value = state.filters[filterId] || "";
    head.append(input);
  }
  box.append(head);
  if (t.note) box.append(el("p", "note", t.note));
  if (!t.rows.length) return box;
  const wrap = el("div", "scroll");
  const table = el("table");
  const plain = t.columns.every((c) => c === "");
  if (plain) table.className = "kv";
  const sort = plain ? null : (state.sorts[filterId] || t.sort);
  if (!plain) {
    const thead = el("thead");
    const tr = el("tr");
    for (const c of t.columns) {
      const th = el("th", "sortable", c);
      th.title = "Click to sort";
      if (sort && sort.col === c) th.append(el("span", "arrow", sort.desc ? " ▼" : " ▲"));
      th.addEventListener("click", () => {
        const now = state.sorts[filterId] || t.sort;
        // First click: numbers and times high/newest first; a second click on the same column reverses.
        state.sorts[filterId] = { col: c, desc: now && now.col === c ? !now.desc : c !== "Rank" };
        render();
      });
      tr.append(th);
    }
    thead.append(tr);
    table.append(thead);
  }
  const body = el("tbody");
  for (const i of sortedOrder(t, sort)) {
    const row = t.rows[i];
    const r = el("tr");
    row.forEach((c, j) => {
      const td = el("td", TIME_COL.test(t.columns[j]) ? "nowrap" : null);
      td.append(cellNode(t.columns[j], c));
      r.append(td);
    });
    const key = t.keys[i];
    if (key && bot) {
      r.className = "click"; r.tabIndex = 0; r.title = "Click for details";
      r.addEventListener("click", (ev) => { if (ev.target.tagName !== "A") openDetail(bot, key); });
      r.addEventListener("keydown", (ev) => { if (ev.key === "Enter") openDetail(bot, key); });
    }
    if (row.some((c) => c.startsWith("At risk"))) r.classList.add("risk");
    body.append(r);
  }
  table.append(body);
  wrap.append(table);
  box.append(wrap);
  if (input) {
    const apply = () => {
      const q = input.value.toLowerCase();
      state.filters[filterId] = input.value;
      for (const r of body.rows) r.hidden = q !== "" && !r.textContent.toLowerCase().includes(q);
    };
    input.addEventListener("input", apply);
    apply();
  }
  return box;
}

function asNumber(text) {
  const s = text.replace(/[,$%+\s]/g, "");
  return /^-?\d+(\.\d+)?$/.test(s) ? parseFloat(s) : null;
}

// The row order for a sort: numbers by value, times by when they happened (the server sends rows
// newest first), other text alphabetically; empty cells always last.
function sortedOrder(t, sort) {
  const idx = t.rows.map((_, i) => i);
  const j = sort ? t.columns.indexOf(sort.col) : -1;
  if (j < 0) return idx;
  const isTime = TIME_COL.test(sort.col);
  const nums = t.rows.map((r) => asNumber(r[j]));
  const numeric = !isTime && t.rows.every((r, i) => r[j] === "" || nums[i] !== null);
  const dir = sort.desc ? -1 : 1;
  return idx.sort((a, b) => {
    const ea = t.rows[a][j] === "", eb = t.rows[b][j] === "";
    if (ea || eb) return ea === eb ? a - b : ea ? 1 : -1;
    if (isTime) return sort.desc ? a - b : b - a;
    const c = numeric ? nums[a] - nums[b] : t.rows[a][j].localeCompare(t.rows[b][j], undefined, { numeric: true });
    return c ? c * dir : a - b;
  });
}

// A small, safe Markdown view for the bots' own reports: headings, bullets, tables and **bold**.
function inline(parent, text) {
  text.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) parent.append(el("strong", null, part.slice(2, -2)));
    else if (part) parent.append(document.createTextNode(part.replace(/`/g, "")));
  });
}

function markdown(text) {
  const out = el("div", "md");
  const lines = text.split(/\r?\n/);
  let list = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*\|/.test(line)) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      i--;
      const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const table = el("table");
      const body = el("tbody");
      rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r)).forEach((r, n) => {
        const tr = el("tr");
        cells(r).forEach((c) => { const td = el(n === 0 ? "th" : "td"); inline(td, c); tr.append(td); });
        body.append(tr);
      });
      table.append(body);
      const wrap = el("div", "scroll");
      wrap.append(table);
      out.append(wrap);
      list = null;
      continue;
    }
    const m = /^(#{1,4})\s+(.*)$/.exec(line);
    if (m) { const h = el("h" + Math.min(6, m[1].length + 2)); inline(h, m[2]); out.append(h); list = null; continue; }
    const b = /^\s*[-*]\s+(.*)$/.exec(line);
    if (b) {
      if (!list) { list = el("ul"); out.append(list); }
      const li = el("li"); inline(li, b[1]); list.append(li); continue;
    }
    list = null;
    if (line.trim()) { const p = el("p"); inline(p, line); out.append(p); }
  }
  return out;
}

function renderTexts(texts, open) {
  const frag = document.createDocumentFragment();
  for (const t of texts) {
    const d = el("details", "card text");
    if (open) d.open = true;
    d.append(el("summary", null, t.title));
    d.append(markdown(t.text));
    frag.append(d);
  }
  return frag;
}

function renderTiles() {
  const nav = document.getElementById("tiles");
  nav.replaceChildren();
  for (const p of state.data.panels) {
    const meta = BOTS[p.bot] || { icon: "•", blurb: "" };
    const b = el("button", "tile t-" + p.bot + (p.bot === state.bot ? " on" : "") + (p.alert ? " alert" : ""));
    b.type = "button";
    const top = el("div", "ttop");
    top.append(el("span", "ticon", meta.icon), el("span", "tname", p.title), badge(p.badge));
    b.append(top);
    b.append(el("div", "thead", p.headline || "–"));
    b.append(el("div", "tblurb", p.age ? "Updated " + p.age : (p.reason || meta.blurb)));
    b.addEventListener("click", () => { state.bot = p.bot; location.hash = p.bot; render(); });
    nav.append(b);
  }
}

function renderPanel() {
  const main = document.getElementById("panel");
  const open = new Set([...main.querySelectorAll("details[open] > summary")].map((s) => s.textContent));
  const scroll = window.scrollY;
  main.replaceChildren();
  const p = state.data.panels.find((x) => x.bot === state.bot) || state.data.panels[0];
  if (!p) return;
  main.className = "p-" + p.bot;
  const meta = BOTS[p.bot] || { icon: "", blurb: "" };
  const h = el("div", "hero");
  const left = el("div");
  const title = el("h2");
  title.append(el("span", "hicon", meta.icon), document.createTextNode(p.title));
  left.append(title, el("div", "hsub", meta.blurb));
  const right = el("div", "hright");
  right.append(badge(p.badge));
  if (p.updated) right.append(el("div", "upd", "Updated " + p.updated));
  h.append(left, right);
  main.append(h);
  if (p.reason) main.append(el("p", "reason " + (BADGES[p.badge] || ["", "grey"])[1], p.reason));
  if (p.facts.length) main.append(renderFacts(p.facts));
  p.tables.forEach((t, i) => main.append(renderTable(t, p.bot, p.bot + ":" + i + ":" + t.title.split(" · ")[0])));
  main.append(renderTexts(p.texts, false));
  for (const s of main.querySelectorAll("details > summary")) if (open.has(s.textContent)) s.parentElement.open = true;
  window.scrollTo(0, scroll);
}

function render() {
  if (!state.data) return;
  document.getElementById("now").textContent = state.data.now;
  renderTiles();
  renderPanel();
}

function setConn(ok, text) {
  const c = document.getElementById("conn");
  c.className = "conn " + (ok ? "ok" : "bad");
  document.getElementById("conntext").textContent = text;
}

async function refresh() {
  try {
    const r = await fetch("/api/overview", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    state.data = await r.json();
    setConn(true, "Live");
  } catch (e) {
    setConn(false, "The dashboard program isn't answering (" + e.message + ")");
  }
  try {
    render();
  } catch (e) {
    setConn(false, "Page error: " + e.message + " (tell Claude)");
  }
  clearTimeout(state.timer);
  state.timer = setTimeout(refresh, 1000 * ((state.data && state.data.refresh_s) || 5));
}

async function openDetail(bot, key) {
  const dlg = document.getElementById("detail");
  const body = document.getElementById("dbody");
  dlg.className = "p-" + bot;
  document.getElementById("dtitle").textContent = "Loading…";
  body.replaceChildren();
  if (!dlg.open) dlg.showModal();
  try {
    const r = await fetch("/api/detail?bot=" + encodeURIComponent(bot) + "&key=" + encodeURIComponent(key), { cache: "no-store" });
    const d = await r.json();
    document.getElementById("dtitle").textContent = d.title;
    if (d.facts.length) body.append(renderFacts(d.facts));
    d.tables.forEach((t, i) => body.append(renderTable(t, null, "d:" + i)));
    body.append(renderTexts(d.texts, true));
  } catch (e) {
    document.getElementById("dtitle").textContent = "Couldn't load the details: " + e.message;
  }
}

document.getElementById("dclose").addEventListener("click", () => document.getElementById("detail").close());
document.getElementById("detail").addEventListener("click", (ev) => {
  if (ev.target.id === "detail") ev.target.close();  // a click on the backdrop closes it
});
window.addEventListener("hashchange", () => { state.bot = (location.hash || "#scout").slice(1); render(); });
refresh();

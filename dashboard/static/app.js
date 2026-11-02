"use strict";
// Renders the server's panels. All bot and web text goes in through textContent, never as HTML.

const state = { data: null, bot: (location.hash || "#guardian").slice(1), filters: {}, timer: null };

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}

function badgeClass(b) {
  return { "LIVE": "live", "STALE": "stale", "ERROR": "error" }[b] || "grey";
}

function cellNode(text) {
  if (/^https?:\/\/\S+$/.test(text)) {
    const a = el("a", null, "open");
    a.href = text; a.target = "_blank"; a.rel = "noopener noreferrer";
    return a;
  }
  return document.createTextNode(text);
}

function renderFacts(facts) {
  const dl = el("dl", "facts");
  for (const [label, value, level] of facts) {
    dl.append(el("dt", null, label));
    dl.append(el("dd", level || null, value === "" ? "–" : value));
  }
  return dl;
}

function renderTable(t, bot, filterId) {
  const box = el("section", "tbl");
  const head = el("div", "thead");
  head.append(el("h3", null, t.title));
  let input = null;
  if (t.rows.length > 5) {
    input = el("input"); input.type = "search"; input.placeholder = "filter...";
    input.value = state.filters[filterId] || "";
    head.append(input);
  }
  box.append(head);
  if (t.note) box.append(el("p", "note", t.note));
  if (!t.rows.length) return box;
  const wrap = el("div", "scroll");
  const table = el("table");
  const tr = el("tr");
  for (const c of t.columns) tr.append(el("th", null, c));
  const thead = el("thead");
  thead.append(tr);
  table.append(thead);
  const body = el("tbody");
  t.rows.forEach((row, i) => {
    const r = el("tr");
    for (const c of row) { const td = el("td"); td.append(cellNode(c)); r.append(td); }
    const key = t.keys[i];
    if (key && bot) {
      r.className = "click"; r.tabIndex = 0; r.title = "Click for details";
      r.addEventListener("click", (ev) => { if (ev.target.tagName !== "A") openDetail(bot, key); });
      r.addEventListener("keydown", (ev) => { if (ev.key === "Enter") openDetail(bot, key); });
    }
    if (row.some((c) => c.startsWith("AT RISK"))) r.classList.add("risk");
    body.append(r);
  });
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

function renderTexts(texts) {
  const frag = document.createDocumentFragment();
  for (const t of texts) {
    const d = el("details", "text");
    d.append(el("summary", null, t.title));
    d.append(el("pre", null, t.text));
    frag.append(d);
  }
  return frag;
}

function renderTiles() {
  const nav = document.getElementById("tiles");
  nav.replaceChildren();
  for (const p of state.data.panels) {
    const b = el("button", "tile" + (p.bot === state.bot ? " on" : "") + (p.alert ? " alert" : ""));
    b.type = "button";
    b.append(el("span", "name", p.title));
    b.append(el("span", "badge " + badgeClass(p.badge), p.badge));
    b.append(el("span", "head", p.headline || " "));
    b.append(el("span", "age", p.age ? "updated " + p.age : (p.reason || "")));
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
  const h = el("div", "phead");
  h.append(el("h2", null, p.title));
  h.append(el("span", "badge " + badgeClass(p.badge), p.badge));
  if (p.updated) h.append(el("span", "upd", "last update " + p.updated + " (" + p.age + ")"));
  main.append(h);
  if (p.reason) main.append(el("p", "reason " + badgeClass(p.badge), p.reason));
  main.append(renderFacts(p.facts));
  p.tables.forEach((t, i) => main.append(renderTable(t, p.bot, p.bot + ":" + i + ":" + t.title.split(" (")[0])));
  main.append(renderTexts(p.texts));
  for (const s of main.querySelectorAll("details > summary")) if (open.has(s.textContent)) s.parentElement.open = true;
  window.scrollTo(0, scroll);
}

function render() {
  if (!state.data) return;
  document.getElementById("now").textContent = state.data.now;
  renderTiles();
  renderPanel();
}

async function refresh() {
  const conn = document.getElementById("conn");
  try {
    const r = await fetch("/api/overview", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    state.data = await r.json();
    conn.textContent = "connected"; conn.className = "";
  } catch (e) {
    conn.textContent = "dashboard program not answering: " + e.message; conn.className = "bad";
  }
  try {
    render();
  } catch (e) {
    conn.textContent = "page error: " + e.message + " (tell Claude)"; conn.className = "bad";
  }
  clearTimeout(state.timer);
  state.timer = setTimeout(refresh, 1000 * ((state.data && state.data.refresh_s) || 5));
}

async function openDetail(bot, key) {
  const dlg = document.getElementById("detail");
  const body = document.getElementById("dbody");
  document.getElementById("dtitle").textContent = "Loading...";
  body.replaceChildren();
  if (!dlg.open) dlg.showModal();
  try {
    const r = await fetch("/api/detail?bot=" + encodeURIComponent(bot) + "&key=" + encodeURIComponent(key), { cache: "no-store" });
    const d = await r.json();
    document.getElementById("dtitle").textContent = d.title;
    body.append(renderFacts(d.facts));
    d.tables.forEach((t, i) => body.append(renderTable(t, null, "d:" + i)));
    const texts = renderTexts(d.texts);
    for (const det of texts.querySelectorAll("details")) det.open = true;
    body.append(texts);
  } catch (e) {
    document.getElementById("dtitle").textContent = "Could not load details: " + e.message;
  }
}

document.getElementById("dclose").addEventListener("click", () => document.getElementById("detail").close());
window.addEventListener("hashchange", () => { state.bot = (location.hash || "#guardian").slice(1); render(); });
refresh();

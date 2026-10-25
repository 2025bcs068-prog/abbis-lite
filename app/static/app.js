"use strict";

const GROUPS = ["O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"];
const TABS = [
  { id: "inventory", label: "Inventory", roles: ["admin", "coordinator", "manager", "clinic", "hospital"] },
  { id: "forecast", label: "Forecast", roles: ["admin", "coordinator", "manager"] },
  { id: "transfers", label: "Transfers", roles: ["admin", "coordinator", "manager"] },
  { id: "trace", label: "Trace a unit", roles: ["admin", "coordinator", "manager", "clinic"] },
  { id: "request", label: "Request blood", roles: ["admin", "manager", "hospital"] },
];
const state = { token: sessionStorage.getItem("abbis_token"), user: null, banks: [], tab: null };
const VIEWS = {};

const $ = (sel, el = document) => el.querySelector(sel);
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : "op-" + Date.now() + "-" + Math.random().toString(16).slice(2));
const shortName = (n) => n.replace(" Regional Blood Bank", "").replace(" National Blood Bank", "");
const bankName = (id) => (state.banks.find((b) => b.id === id) || { name: String(id).slice(0, 8) }).name;

// Build DOM with textContent only: user-supplied strings can never become markup.
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
  for (const kid of kids) if (kid != null) el.append(typeof kid === "string" ? document.createTextNode(kid) : kid);
  return el;
}

let toastTimer;
function toast(msg, kind = "info") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 7000);
}

async function api(path, { method = "GET", body, form, params } = {}) {
  const url = path + (params ? "?" + new URLSearchParams(params) : "");
  const headers = {};
  if (state.token) headers.Authorization = "Bearer " + state.token;
  let data;
  if (form) data = new URLSearchParams(form);
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; data = JSON.stringify(body); }
  const r = await fetch(url, { method, headers, body: data });
  let payload = null;
  try { payload = await r.json(); } catch { /* empty body */ }
  if (!r.ok) {
    if (r.status === 401 && state.token) logout("Your session expired. Please sign in again.");
    const d = payload && payload.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `Request failed (${r.status})`);
  }
  return payload;
}

const labeled = (text, control) => h("label", {}, h("span", {}, text), control);
const card = (k, v, kind = "") => h("div", { class: "card " + kind }, h("div", { class: "k" }, k), h("div", { class: "v" }, v));
const facts = (pairs) => h("dl", {}, pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))]));

function visibleBanks() {
  const scoped = ["clinic", "manager"].includes(state.user.role);
  return scoped ? state.banks.filter((b) => b.id === state.user.bank_id) : state.banks;
}
function bankSelect(id, banks = visibleBanks()) {
  return h("select", { id }, banks.map((b) => h("option", { value: b.id }, b.name)));
}
const groupSelect = (id, dflt) => {
  const sel = h("select", { id }, GROUPS.map((g) => h("option", { value: g }, g)));
  if (dflt) sel.value = dflt;
  return sel;
};

// ---------- Inventory ----------
VIEWS.inventory = async (root) => {
  const banks = state.user.role === "hospital" || state.user.role === "admin" || state.user.role === "coordinator"
    ? state.banks : visibleBanks();
  const rows = await Promise.all(banks.map(async (b) => ({ bank: b, inv: await api("/inventory", { params: { bank_id: b.id } }) })));
  const max = Object.fromEntries(GROUPS.map((g) => [g, Math.max(0, ...rows.map((r) => r.inv[g] || 0))]));
  const table = h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Blood bank"), GROUPS.map((g) => h("th", { class: "num" }, g)), h("th", { class: "num" }, "Total"))),
    h("tbody", {}, rows.map(({ bank, inv }) => h("tr", {},
      h("th", { scope: "row" }, bank.name),
      GROUPS.map((g) => {
        const v = inv[g] || 0;
        const low = rows.length > 1 && max[g] > 0 && v * 3 < max[g];
        return h("td", { class: "num" + (low ? " low" : "") }, v);
      }),
      h("td", { class: "num strong" }, GROUPS.reduce((a, g) => a + (inv[g] || 0), 0))))));
  root.append(
    h("h2", {}, "Available stock by blood group"),
    h("p", { class: "muted" }, "Units that are screened, unexpired and not yet issued."),
    h("div", { class: "scroll" }, table));
  if (rows.length > 1) root.append(h("p", { class: "legend" }, h("span", { class: "swatch" }), " Amber: under one third of the best-stocked bank's level for that group."));
};

// ---------- Forecast ----------
function chart(points) {
  const W = 640, H = 250, L = 44, R = 14, T = 14, B = 30;
  const top = Math.max(1, ...points.map((p) => p.high_80)) * 1.1;
  const x = (i) => L + (i * (W - L - R)) / Math.max(points.length - 1, 1);
  const y = (v) => H - B - (v * (H - T - B)) / top;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", class: "chart",
    "aria-label": "Forecast demand per day with an 80 percent interval" });
  for (const f of [0, 0.5, 1]) {
    const v = top * f;
    svg.append(s("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }),
      s("text", { x: L - 6, y: y(v) + 4, class: "tick", "text-anchor": "end" }, String(Math.round(v))));
  }
  const up = points.map((p, i) => `${x(i)},${y(p.high_80)}`);
  const lo = points.map((p, i) => `${x(i)},${y(p.low_80)}`).reverse();
  svg.append(s("polygon", { points: [...up, ...lo].join(" "), class: "band" }),
    s("polyline", { points: points.map((p, i) => `${x(i)},${y(p.units)}`).join(" "), class: "line", fill: "none" }));
  points.forEach((p, i) => svg.append(
    s("circle", { cx: x(i), cy: y(p.units), r: 3.5, class: "dot" }),
    s("text", { x: x(i), y: H - 10, class: "tick", "text-anchor": "middle" }, p.date.slice(5))));
  return h("figure", {}, svg,
    h("figcaption", { class: "muted" }, "Line: forecast units per day. Shaded band: 80% interval taken from backtest errors."));
}

function renderForecast(f, bankId, group) {
  const box = h("div", {});
  const short = f.projected_shortfall;
  box.append(
    h("div", { class: "cards" },
      card("Stock on hand", f.stock_on_hand),
      card(`${f.horizon_days}-day forecast demand`, f.projected_demand),
      card("Projected shortfall", short, short > 0 ? "bad" : "ok")),
    h("p", { class: "callout " + (short > 0 ? "bad" : "ok") },
      f.suggestion + (f.low_confidence ? " (low confidence: the rule-based fallback is in use)" : "")),
    chart(f.forecast),
    h("h3", {}, "How the model was chosen"),
    h("p", { class: "muted" }, "Rolling-origin backtest on past demand (8 folds, 7-day horizon). WAPE: lower is better."),
    h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Method"), h("th", { class: "num" }, "WAPE"), h("th", { class: "num" }, "MAE"))),
      h("tbody", {}, Object.entries(f.backtest).sort((a, b) => a[1].wape - b[1].wape).map(([name, m]) =>
        h("tr", {}, h("th", { scope: "row", class: name === f.model ? "pick" : "" }, name + (name === f.model ? "  (used)" : "")),
          h("td", { class: "num" }, m.wape), h("td", { class: "num" }, m.mae)))))));
  if (short > 0 && ["admin", "coordinator", "manager"].includes(state.user.role)) {
    const btn = h("button", { type: "button" }, "Create transfer proposal");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const r = await api("/transfers/suggest", { method: "POST", params: { to_bank_id: bankId, blood_group: group } });
        if (r.id) { toast("Proposal created. A person must approve it before anything moves.", "ok"); openTab("transfers"); }
        else toast(r.message || "No proposal could be made.", "info");
      } catch (e) { toast(e.message, "error"); } finally { btn.disabled = false; }
    });
    box.append(h("p", {}, btn));
  }
  return box;
}

VIEWS.forecast = async (root) => {
  const bank = bankSelect("fBank"), group = groupSelect("fGroup", "A+");
  const out = h("div", { class: "result" });
  const run = h("button", { type: "button" }, "Run forecast");
  run.addEventListener("click", async () => {
    run.disabled = true;
    out.replaceChildren(h("p", { class: "muted" }, "Forecasting. This can take several seconds."));
    try {
      const f = await api("/forecast", { params: { bank_id: bank.value, blood_group: group.value } });
      out.replaceChildren(renderForecast(f, bank.value, group.value));
    } catch (e) { out.replaceChildren(h("p", { class: "error" }, e.message)); } finally { run.disabled = false; }
  });
  root.append(h("h2", {}, "Demand forecast"),
    h("p", { class: "muted" }, "Compares projected hospital demand with stock on hand and flags shortfalls."),
    h("div", { class: "controls" }, labeled("Blood bank", bank), labeled("Blood group", group), run), out);
};

// ---------- Transfers ----------
function transferCard(t) {
  const r = t.rationale || {}, d = r.destination || {}, src = r.source || {};
  const card_ = h("article", { class: "tcard" },
    h("div", { class: "row" },
      h("strong", {}, `${t.units} × ${t.blood_group}`),
      h("span", { class: "route" }, `${shortName(bankName(t.from_bank_id))} → ${shortName(bankName(t.to_bank_id))}`),
      h("span", { class: "badge " + t.status }, t.status)),
    h("p", {}, r.explanation || ""),
    facts([["Model", t.model], ["Destination stock / forecast demand", `${d.stock} / ${d.demand}`],
      ["Source stock / forecast demand", `${src.stock} / ${src.demand}`], ["Backtest WAPE", d.wape]]));
  if (t.status === "proposed") {
    const reason = h("input", { type: "text", placeholder: "Reason (required to reject)", "aria-label": "Reason", maxlength: "300" });
    const decide = async (decision) => {
      try {
        const out = await api(`/transfers/${t.id}/decide`, { method: "POST",
          body: { client_op_id: uuid(), decision, reason: reason.value.trim() || null } });
        toast(decision === "approve" ? `Approved. ${out.moved_units.length} units moved.` : "Rejected.", "ok");
        openTab("transfers");
      } catch (e) { toast(e.message, "error"); }
    };
    card_.append(h("div", { class: "actions" }, reason,
      h("button", { type: "button", onclick: () => decide("approve") }, "Approve"),
      h("button", { type: "button", class: "secondary", onclick: () => decide("reject") }, "Reject")));
  } else {
    card_.append(h("p", { class: "muted" }, `Decided by ${t.decided_by}` + (t.decision_reason ? `: ${t.decision_reason}` : "")));
  }
  return card_;
}

VIEWS.transfers = async (root) => {
  const list = await api("/transfers");
  root.append(h("h2", {}, "Transfer proposals"),
    h("p", { class: "muted" }, "The system only proposes. Nothing moves until a person approves, and every decision is recorded."));
  if (!list.length) root.append(h("p", { class: "empty" }, "No proposals yet. Run a forecast and create one."));
  for (const t of list) root.append(transferCard(t));
};

// ---------- Trace ----------
function renderTrace(t) {
  const u = t.unit;
  return h("div", {},
    facts([["Unit", u.unit_code], ["Blood group", u.blood_group], ["Status", u.status],
      ["Collected", u.collected_on], ["Expires", u.expires_on], ["Held by", bankName(u.bank_id)]]),
    h("h3", {}, "Screening"),
    t.screening.length ? h("div", { class: "chips" }, t.screening.map((x) => h("span", { class: "chip " + x.result }, `${x.test}: ${x.result}`)))
      : h("p", { class: "muted" }, "Not screened yet."),
    h("h3", {}, "Audit trail"),
    h("ol", { class: "timeline" }, t.events.map((e) => h("li", {}, `${e.at.replace("T", " ").slice(0, 16)}  ·  ${e.action}  ·  by ${e.actor}`))));
}

VIEWS.trace = async (root) => {
  const input = h("input", { id: "code", type: "text", placeholder: "e.g. U00000123", autocomplete: "off", maxlength: "24" });
  const out = h("div", {});
  const go = async () => {
    const code = input.value.trim();
    if (!code) return;
    out.replaceChildren(h("p", { class: "muted" }, "Looking up…"));
    try { out.replaceChildren(renderTrace(await api(`/units/${encodeURIComponent(code)}/trace`))); }
    catch (e) { out.replaceChildren(h("p", { class: "error" }, e.message)); }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  root.append(h("h2", {}, "Trace a unit"),
    h("p", { class: "muted" }, "Follow one unit from collection through screening to issue, with who did what and when."),
    h("div", { class: "controls" }, labeled("Unit code", input), h("button", { type: "button", onclick: go }, "Trace")), out);
};

// ---------- Request blood ----------
VIEWS.request = async (root) => {
  const bank = bankSelect("rBank", state.user.role === "manager" ? visibleBanks() : state.banks);
  const group = groupSelect("rGroup", "O+");
  const units = h("input", { id: "rUnits", type: "number", min: "1", max: "20", value: "2" });
  const hospital = h("input", { id: "rHosp", type: "text", value: "Referral Hospital A", maxlength: "120" });
  const out = h("div", {});
  const submit = h("button", { type: "button" }, "Request units");
  submit.addEventListener("click", async () => {
    submit.disabled = true;
    try {
      const r = await api("/requests", { method: "POST", body: { client_op_id: uuid(), hospital: hospital.value.trim(),
        bank_id: bank.value, blood_group: group.value, units: Number(units.value) } });
      out.replaceChildren(h("p", { class: "callout " + (r.status === "rejected" ? "bad" : "ok") },
        `Request ${r.status}: ${r.issued_units.length} of ${r.requested} units issued (oldest-expiry first).`),
        r.issued_units.length ? h("p", { class: "muted" }, "Unit codes: " + r.issued_units.join(", ")) : null);
    } catch (e) { out.replaceChildren(h("p", { class: "error" }, e.message)); } finally { submit.disabled = false; }
  });
  root.append(h("h2", {}, "Request blood"),
    h("p", { class: "muted" }, "Units are issued first-expiry-first-out to reduce wastage."),
    h("div", { class: "controls" }, labeled("Hospital", hospital), labeled("Blood bank", bank), labeled("Blood group", group),
      labeled("Units (1-20)", units), submit), out);
};

// ---------- shell ----------
async function openTab(id) {
  state.tab = id;
  for (const b of $("#nav").children) b.setAttribute("aria-current", b.dataset.tab === id ? "page" : "false");
  const view = $("#view");
  view.replaceChildren(h("p", { class: "muted" }, "Loading…"));
  try {
    const content = h("div", {});
    await VIEWS[id](content);
    if (state.tab === id) view.replaceChildren(content);
  } catch (e) { view.replaceChildren(h("p", { class: "error" }, e.message)); }
}

function showLogin() {
  $("#login").hidden = false; $("#app").hidden = true; $("#who").hidden = true;
}

function showApp() {
  $("#login").hidden = true; $("#app").hidden = false; $("#who").hidden = false;
  const u = state.user;
  $("#whoText").textContent = `${u.username} · ${u.role}` + (u.bank_id ? ` · ${shortName(bankName(u.bank_id))}` : "");
  const tabs = TABS.filter((t) => t.roles.includes(u.role));
  $("#nav").replaceChildren(...tabs.map((t) => h("button", { type: "button", class: "tab", "data-tab": t.id }, t.label)));
  openTab(tabs[0].id);
}

function logout(msg) {
  sessionStorage.removeItem("abbis_token");
  state.token = null; state.user = null;
  showLogin();
  if (msg) toast(msg, "error");
}

async function loadSession() {
  state.banks = await api("/banks");
  showApp();
}

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const btn = $("button[type=submit]", e.target);
  btn.disabled = true;
  try {
    const r = await api("/auth/login", { method: "POST", form: { username: f.get("username"), password: f.get("password") } });
    state.token = r.access_token;
    sessionStorage.setItem("abbis_token", r.access_token);
    state.user = await api("/auth/me");
    e.target.reset();
    await loadSession();
  } catch (err) { toast(err.message, "error"); } finally { btn.disabled = false; }
});
$("#logout").addEventListener("click", () => logout());
$("#nav").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b) openTab(b.dataset.tab);
});

(async function init() {
  if (state.token) {
    try { state.user = await api("/auth/me"); await loadSession(); return; } catch { /* fall through to login */ }
  }
  showLogin();
})();

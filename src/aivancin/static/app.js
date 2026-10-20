"use strict";
/* AIVANCIN client. The server does all matching and checks; this file renders state and sends the
   user's own choices back. Nothing is accepted without a tick, and nothing is stored. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const FIELD_LABEL = { current_job: "Current job", other_work: "Other work", studies: "Studies" };
const FIELD_ICON = { current_job: "briefcase-business", other_work: "wrench", studies: "graduation-cap", all: "layers" };
const LANE_TEXT = { current_job: "current-job", other_work: "other-work", studies: "studies", all: "" };

const state = {
  status: null, personas: [], sources: {}, sourceNo: {},
  title: null, titleActs: [], untickedTitle: new Set(), cip: null,
  suggest: null, ticked: new Map(), dismissed: new Set(), manual: new Map(),
  lane: "all", opts: { lambda: 0, mu: 0, nu: 0, scenario: "moderate", threshold: 50, pace: 1.444, release: null, horizon: "plan", scope: "skilled" },
  knownTools: new Set(), results: null, baseline: null, selected: null, target: null, lastIntake: null,
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function icon(name, cls = "") { return `<svg class="ico ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`; }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const err = new Error("request failed"); err.status = res.status; err.data = data; throw err; }
  return data;
}

/* ---------- numbers with provenance ---------- */
function fmtNum(v, kind) {
  if (v === null || v === undefined) return "";
  if (kind === "pct") return `${Number(v).toFixed(1)}%`;
  if (kind === "rm") return `${v < 0 ? "-" : ""}RM${Math.abs(Math.round(v)).toLocaleString("en-MY")}`;
  if (kind === "step") return `${v >= 0 ? "+" : "-"}RM${Math.abs(Math.round(v)).toLocaleString("en-MY")}`;
  if (kind === "pts") return `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)} points`;
  if (kind === "int") return Math.round(v).toLocaleString("en-MY");
  return String(v);
}
function srcTitle(src) {
  return src.map(id => {
    if (id === "your-input") return "your input";
    const r = state.sources[id];
    return r ? `${state.sourceNo[id]}. ${r.title} (${r.release || ""}, accessed ${r.access_date || ""})` : id;
  }).join("; ");
}
/* A source reference without a number of its own (labels, cited assumptions). */
function srcMarks(src) { return `<span class="src-ref" data-src="${esc(src.join(" "))}" hidden></span>`; }
function fig(f, kind) {
  if (!f || f.v === null || f.v === undefined) return "";
  return `<span class="num" data-src="${esc(f.src.join(" "))}" title="Source: ${esc(srcTitle(f.src))}">${esc(fmtNum(f.v, kind))}</span>`;
}
/* Every element marked data-scope ends with one line linking the sources of the numbers inside it. */
function attachSources(root) {
  const scopes = root.matches("[data-scope]") ? [root, ...$$("[data-scope]", root)] : $$("[data-scope]", root);
  scopes.forEach(scope => {
    $$(".srcs", scope).forEach(x => { if (x.closest("[data-scope]") === scope) x.remove(); });
    const ids = new Set();
    $$("[data-src]", scope).forEach(el => {
      if (el.closest("[data-scope]") === scope) el.dataset.src.split(" ").filter(Boolean).forEach(i => ids.add(i));
    });
    if (!ids.size) return;
    const order = [...ids].sort((a, b) => (state.sourceNo[a] || 999) - (state.sourceNo[b] || 999));
    const line = document.createElement("p");
    line.className = "srcs";
    line.innerHTML = "Sources: " + order.map(id => id === "your-input" ? `<a href="#src-your-input">your input</a>`
      : `<a href="#src-${esc(id)}" title="${esc(srcTitle([id]))}">${state.sourceNo[id] || "?"}</a>`).join(", ");
    (scope.querySelector(":scope > .srcs-slot") || scope).append(line);
  });
}

/* ---------- appearance ---------- */
function initTheme() {
  const saved = document.documentElement.dataset.theme || "auto";
  const input = $(`.theme input[value="${saved}"]`);
  if (input) input.checked = true;
  $$(".theme input").forEach(i => i.addEventListener("change", () => {
    if (i.value === "auto") { delete document.documentElement.dataset.theme; } else { document.documentElement.dataset.theme = i.value; }
    try { localStorage.setItem("aivancin-theme", i.value); } catch (e) { /* storage unavailable */ }
  }));
}

/* ---------- stage navigation ---------- */
function showStage(id) {
  const sec = document.getElementById(id);
  sec.hidden = false;
  const key = { review: "work", results: "routes" }[id] || id;
  const link = $(`.stage-links a[data-stage="${key}"]`);
  if (link) link.removeAttribute("aria-disabled");
  $$(".stage-links a").forEach(a => a.toggleAttribute("aria-current", a.dataset.stage === key));
  $$(".stage-links a[aria-current]").forEach(a => a.setAttribute("aria-current", "step"));
  sec.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  const h = $("h2[tabindex]", sec);
  if (h) h.focus({ preventScroll: true });
}

/* ---------- stage 1: intake ---------- */
function combo(input, list, fetcher, onPick) {
  let items = [], active = -1;
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
  const render = () => {
    list.innerHTML = items.length ? items.map((it, i) =>
      `<li role="option" id="${list.id}-${i}" aria-selected="${i === active}">${esc(it.label)}<small>${esc(it.meta)}</small></li>`).join("")
      : `<li role="option" aria-disabled="true">No match. Try fewer or simpler words.</li>`;
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (active >= 0) input.setAttribute("aria-activedescendant", `${list.id}-${active}`);
    else input.removeAttribute("aria-activedescendant");
  };
  const search = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    items = await fetcher(q);
    active = -1;
    render();
  }, 160);
  input.addEventListener("input", search);
  input.addEventListener("keydown", e => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { active = Math.min(items.length - 1, active + 1); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); render(); e.preventDefault(); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); onPick(items[active]); close(); }
    else if (e.key === "Escape") { close(); }
  });
  list.addEventListener("mousedown", e => {
    const li = e.target.closest("li[id]");
    if (!li) return;
    e.preventDefault();
    onPick(items[Number(li.id.split("-").pop())]);
    close();
  });
  input.addEventListener("blur", () => setTimeout(close, 120));
}

function renderChosen(input, value, onClear) {
  let box = input.parentElement.querySelector(".chosen");
  if (!value) { if (box) box.remove(); input.hidden = false; return; }
  if (!box) { box = document.createElement("div"); box.className = "chosen"; input.after(box); }
  box.innerHTML = `<span class="chip">${esc(value)}</span><button type="button" class="link-btn">Change</button>`;
  input.hidden = true;
  box.querySelector("button").addEventListener("click", () => { onClear(); input.hidden = false; input.value = ""; input.focus(); });
}

async function setTitle(code) {
  if (!code) { state.title = null; state.titleActs = []; renderChosen($("#f-title"), null); return; }
  const d = await api(`/api/occupations/${encodeURIComponent(code)}/activities`);
  state.title = { code: d.code, title: d.title };
  state.titleActs = d.activities;
  state.untickedTitle = new Set();
  renderChosen($("#f-title"), `${d.title} (${d.code})`, () => setTitle(null));
}
function setCip(cip) {
  state.cip = cip;
  renderChosen($("#f-field"), cip ? `${cip.title} (${cip.cip})` : null, () => setCip(null));
}

function intakeBody() {
  const f = $("#intake");
  const num = name => { const v = f.elements[name].value.trim(); return v === "" ? null : Number(v); };
  return {
    current_job: $("#f-current").value, other_work: $("#f-other").value, studies: $("#f-studies").value,
    title_code: state.title ? state.title.code : null, cip_code: state.cip ? state.cip.cip : null,
    degree_field: state.cip ? state.cip.title : "", degree_level: f.elements.degree_level.value,
    weekly_hours: num("weekly_hours"), budget_rm: num("budget_rm"),
  };
}

function showFieldErrors(errors) {
  $$(".err[data-err]").forEach(p => { p.textContent = ""; p.closest(".field")?.classList.remove("invalid"); });
  let first = null;
  for (const [k, msg] of Object.entries(errors || {})) {
    const p = $(`.err[data-err="${k}"]`);
    if (p) { p.textContent = msg; p.closest(".field")?.classList.add("invalid"); first = first || p; }
  }
  return first;
}

async function loadPersona(p) {
  $("#f-current").value = p.current_job; $("#f-other").value = p.other_work; $("#f-studies").value = p.studies;
  $("#f-hours").value = p.weekly_hours; $("#f-budget").value = p.budget_rm;
  $(`#intake input[name="degree_level"][value="${p.degree_level}"]`).checked = true;
  await setTitle(p.title_code);
  setCip(p.cip_code ? { cip: p.cip_code, title: p.cip_title || p.degree_field } : null);
  $("#ai-status").textContent = `Loaded ${p.name}'s example. ${p.name} is a synthetic person, not a real user.`;
}

async function readWork(e) {
  e.preventDefault();
  const body = intakeBody();
  const first = showFieldErrors(clientChecks(body));
  if (first) { first.closest(".field").querySelector("input, textarea")?.focus(); return; }
  if (!body.current_job.trim() && !body.other_work.trim() && !body.studies.trim() && !body.title_code) {
    $("#ai-status").textContent = "Write at least one sentence about your work, or choose your current job title.";
    $("#ai-status").classList.add("warn");
    return;
  }
  $("#ai-status").classList.remove("warn");
  $("#read-btn").disabled = true;
  $("#read-btn").textContent = "Reading";
  try {
    const sug = await api("/api/suggest", body);
    state.suggest = sug;
    state.ticked = new Map(); state.dismissed = new Set(); state.manual = new Map();
    state.lastIntake = body;
    renderReview();
    showStage("review");
  } catch (err) {
    $("#ai-status").textContent = "Something went wrong while reading your text. Please try again.";
    $("#ai-status").classList.add("warn");
  } finally {
    $("#read-btn").disabled = false;
    $("#read-btn").textContent = "Read my work";
  }
}

function clientChecks(b) {
  const e = {};
  if (!(b.weekly_hours > 0)) e.weekly_hours = "Enter the hours you can spend each week (more than 0).";
  if (b.budget_rm === null || !(b.budget_rm >= 0)) e.budget_rm = "Enter a budget of RM0 or more.";
  return e;
}

/* ---------- stage 2: review ---------- */
function suggestionMap() {
  const m = new Map();
  (state.suggest?.suggestions || []).forEach(s => m.set(s.dwa_id, s));
  return m;
}

function renderReview() {
  const box = $("#sentences");
  const sug = state.suggest;
  const smap = suggestionMap();
  let html = `<div class="cap">${icon("info")}<p>A small AI model on this server matched each of your sentences to work activities listed by O*NET. It is often wrong, so nothing counts until you tick it.</p></div>`;
  if (!sug.available) {
    html += `<div class="callout">${icon("triangle-alert")}<div><p><b>AI suggestions are unavailable.</b></p><p class="sub">You can still search the activity list and add what you do. Results will be labelled "without AI suggestions".</p></div></div>`;
    html += manualSearchCard();
  }
  if (state.titleActs.length) {
    const kept = state.titleActs.filter(a => !state.untickedTitle.has(a.dwa_id)).length;
    html += `<details class="sent title-acts"><summary>${icon("briefcase-business")}<span>From your job title, ${esc(state.title.title)}: <span class="num" id="title-kept">${kept}</span> of ${state.titleActs.length} O*NET activities</span>${icon("chevron-down")}</summary>
      <p class="foot">These form your current-job lane. Untick any you do not do.</p>
      <fieldset class="sugg">${state.titleActs.map(a => `<label class="opt"><input type="checkbox" data-title="${esc(a.dwa_id)}" ${state.untickedTitle.has(a.dwa_id) ? "" : "checked"}><span class="t">${esc(a.name)}</span></label>`).join("")}</fieldset></details>`;
  }
  const sents = sug.available ? sug.sentences : [];
  const fieldSeq = { current_job: 0, other_work: 0, studies: 0 };
  for (const s of sents) {
    fieldSeq[s.field] += 1;
    const n = fieldSeq[s.field];
    const src = `${icon(FIELD_ICON[s.field], "sm")}From "${FIELD_LABEL[s.field]}", sentence ${n}`;
    const rows = s.top.map(d => optionRow(smap.get(d), s)).join("");
    if (s.guard) {
      const kind = s.guard.kind === "negated" ? "reads as something you have not done" : "reads as a plan, not done work";
      html += `<div class="guard" data-sentence="${s.index}">
        <h4>${icon("circle-dashed")}Not counted: you said you have not done this yet</h4>
        <blockquote class="words"><mark class="words">${esc(s.text)}</mark></blockquote>
        <p class="why">"${esc(s.guard.cue)}" ${kind}. Its suggestions stay out unless you confirm on purpose that you have done them.</p>
        <details><summary>Show the suggestions anyway ${icon("chevron-down", "sm")}</summary><fieldset class="sugg">${rows}</fieldset></details>
      </div>`;
      continue;
    }
    const dismissed = state.dismissed.has(s.index);
    html += `<div class="sent" data-sentence="${s.index}">
      <p class="src">${src}</p>
      <blockquote class="words"><mark class="words">${esc(s.text)}</mark></blockquote>
      ${dismissed ? `<p class="dismissed">You said none of the 5 suggestions fit. <button type="button" class="link-btn" data-undismiss="${s.index}">Show them again</button></p>`
        : `<fieldset class="sugg"><legend class="vh">Activities suggested for this sentence</legend>${rows}</fieldset>
      <div class="sent-actions"><button type="button" class="btn btn-gray" data-dismiss="${s.index}">None of these fit</button><button type="button" class="btn btn-gray" data-find="${s.index}">${icon("search", "sm")}Find another activity</button></div>
      <div class="find" data-find-box="${s.index}" hidden></div>`}
    </div>`;
  }
  if (sug.available && !sents.length) html += `<p class="cap">${icon("info")}No sentences to read. Add text in step 1, or search for activities below.</p>`;
  if (sug.available) html += manualSearchCard();
  box.innerHTML = html;
  const manual = $('[data-find-box="manual"]', box);
  if (manual) openFind(manual, "other_work", false);
  renderRail();
}

function optionRow(entry, sentence) {
  if (!entry) return "";
  const id = entry.dwa_id;
  const others = entry.triggers.filter(t => t.sentence !== sentence.index).map(t => t.sentence);
  const also = others.length ? `<span class="also">Also suggested for ${others.length === 1 ? "another sentence" : `${others.length} other sentences`}</span>` : "";
  const ticked = state.ticked.has(id);
  if (entry.needs_override && sentence.guard) {
    return `<div class="opt" data-row="${esc(id)}"><span class="t">${esc(entry.name)}${also}
      <span class="also">${ticked ? "Counted as done work because you confirmed it." : ""}</span></span>
      <button type="button" class="btn btn-gray confirm-guard" data-override="${esc(id)}" aria-pressed="${ticked}">${ticked ? "Remove" : "I have done this"}</button></div>`;
  }
  return `<label class="opt"><input type="checkbox" data-dwa="${esc(id)}" ${ticked ? "checked" : ""}><span class="t">${esc(entry.name)}${also}</span></label>`;
}

function manualSearchCard() {
  return `<div class="sent"><p class="src">${icon("pencil-line", "sm")}Something we missed?</p>
    <div class="find" data-find-box="manual"></div></div>`;
}

function openFind(boxEl, defaultLane, focus = true) {
  boxEl.hidden = false;
  boxEl.innerHTML = `<label class="vh" for="find-${boxEl.dataset.findBox}">Search activities</label>
    <input type="search" id="find-${boxEl.dataset.findBox}" placeholder="Search activities, for example: website" autocomplete="off">
    <ul class="results" hidden></ul>`;
  const input = $("input", boxEl), list = $(".results", boxEl);
  const run = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { list.hidden = true; return; }
    const hits = await api(`/api/activities?q=${encodeURIComponent(q)}`);
    list.hidden = false;
    list.innerHTML = hits.length ? hits.map(h => `<li><span>${esc(h.name)}</span><span>
      <select aria-label="Where you did this">${["current_job", "other_work", "studies"].map(l => `<option value="${l}" ${l === defaultLane ? "selected" : ""}>${FIELD_LABEL[l]}</option>`).join("")}</select>
      <button type="button" class="btn btn-gray" data-add="${esc(h.dwa_id)}" data-name="${esc(h.name)}">Add</button></span></li>`).join("")
      : `<li>No activity matches "${esc(q)}". Try fewer or simpler words.</li>`;
  }, 180);
  input.addEventListener("input", run);
  input.addEventListener("keydown", e => {
    if (e.key === "Escape") {
      boxEl.hidden = boxEl.dataset.findBox !== "manual";
      const opener = $(`[data-find="${boxEl.dataset.findBox}"]`);
      if (opener) opener.focus();
    }
  });
  if (focus) input.focus();
}

function laneCounts() {
  const smap = suggestionMap();
  const c = { current_job: 0, other_work: 0, studies: 0 };
  const all = new Set();
  for (const id of state.ticked.keys()) {
    all.add(id);
    (smap.get(id)?.lanes || []).forEach(l => { c[l] += 1; });
  }
  for (const [id, m] of state.manual) { all.add(id); c[m.lane] += 1; }
  const titleKept = state.titleActs.filter(a => !state.untickedTitle.has(a.dwa_id));
  titleKept.forEach(a => all.add(a.dwa_id));
  c.current_job += titleKept.length;
  return { ...c, all: all.size, confirmed: state.ticked.size + state.manual.size, title: titleKept.length };
}

function renderRail() {
  const smap = suggestionMap();
  const items = [...state.ticked.keys()].map(id => ({ id, name: smap.get(id)?.name || id, note: "suggested, ticked by you", icon: "check" }))
    .concat([...state.manual].map(([id, m]) => ({ id, name: m.name, note: `added by you, ${FIELD_LABEL[m.lane].toLowerCase()}`, icon: "pencil-line" })));
  $("#mine").innerHTML = items.map(it => `<li>${icon(it.icon, "sm")}<span class="mine-text">${esc(it.name)}<span class="mine-note">${esc(it.note)}</span></span>
    <button type="button" class="rm" data-remove="${esc(it.id)}" aria-label="Remove ${esc(it.name)}">${icon("x", "sm")}</button></li>`).join("");
  $("#mine-empty").hidden = items.length > 0;
  const c = laneCounts();
  $("#count").textContent = c.confirmed;
  $("#count-m").textContent = c.confirmed;
  $(`#lane-ctl input[value="${state.lane}"]`).checked = true;
  let note;
  if (state.lane === "studies") {
    note = state.cip ? `Ranks the roles your field (${state.cip.title}) leads to in the NCES crosswalk by all ${c.all} of your activities.`
      : "Choose your degree or diploma field in step 1 to use the studies lane.";
  } else if (state.lane === "all") {
    note = `Uses all ${c.all} activities${c.title ? `, including ${c.title} from your job title` : ""}.`;
  } else {
    const other = c.all - c[state.lane];
    note = `Uses your ${c[state.lane]} ${LANE_TEXT[state.lane]} activities. ${other > 0 ? `Your other ${other} stay out of this lane.` : ""}`;
  }
  $("#lane-note").textContent = note;
}

function reviewEvents() {
  const root = $("#review");
  root.addEventListener("change", e => {
    const t = e.target;
    if (t.matches("input[data-dwa]")) {
      const id = t.dataset.dwa;
      if (t.checked) state.ticked.set(id, { override: false }); else state.ticked.delete(id);
      $$(`input[data-dwa="${CSS.escape(id)}"]`).forEach(i => { i.checked = t.checked; });
      renderRail();
    } else if (t.matches("input[data-title]")) {
      if (t.checked) state.untickedTitle.delete(t.dataset.title); else state.untickedTitle.add(t.dataset.title);
      $("#title-kept").textContent = state.titleActs.length - state.untickedTitle.size;
      renderRail();
    } else if (t.matches('#lane-ctl input')) {
      state.lane = t.value;
      renderRail();
    }
  });
  root.addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.dismiss !== undefined) {
      state.dismissed.add(Number(b.dataset.dismiss));
      renderReview();
      $(`[data-undismiss="${b.dataset.dismiss}"]`)?.focus();
    } else if (b.dataset.undismiss !== undefined) {
      state.dismissed.delete(Number(b.dataset.undismiss));
      renderReview();
      $(`[data-dismiss="${b.dataset.undismiss}"]`)?.focus();
    } else if (b.dataset.find !== undefined) {
      const s = state.suggest.sentences.find(x => x.index === Number(b.dataset.find));
      openFind($(`[data-find-box="${b.dataset.find}"]`), s ? s.field : "other_work");
    } else if (b.dataset.add) {
      const lane = b.parentElement.querySelector("select").value;
      state.manual.set(b.dataset.add, { name: b.dataset.name, lane });
      b.textContent = "Added"; b.disabled = true;
      renderRail();
    } else if (b.dataset.override) {
      const id = b.dataset.override;
      if (state.ticked.has(id)) state.ticked.delete(id); else state.ticked.set(id, { override: true });
      renderReview();
      $(`[data-override="${CSS.escape(id)}"]`)?.focus();
      $$(`[data-override="${CSS.escape(id)}"]`).forEach(x => x.closest("details")?.setAttribute("open", ""));
    } else if (b.dataset.remove) {
      state.ticked.delete(b.dataset.remove); state.manual.delete(b.dataset.remove);
      renderReview();
    } else if (b.dataset.action === "rank") {
      rank();
    }
  });
}

/* ---------- stage 3: results ---------- */
function resultsBody(extraOpts = {}) {
  const smap = suggestionMap();
  const confirmed = [...state.ticked].map(([id, t]) => ({ dwa_id: id, source: "suggested", ...(t.override ? { guard_override: true } : {}) }))
    .filter(c => smap.has(c.dwa_id))
    .concat([...state.manual].map(([id, m]) => ({ dwa_id: id, source: "added_by_you", lane: m.lane })));
  return {
    ...state.lastIntake, confirmed, unticked_title_activities: [...state.untickedTitle],
    options: { lane: state.lane, ...state.opts, known_tools: [...state.knownTools], ...extraOpts },
  };
}

async function rank() {
  $("#rank-err").textContent = "";
  try {
    const res = await api("/api/results", resultsBody());
    state.results = res;
    state.selected = res.roles[0]?.code || null;
    state.target = null;
    state.baseline = null;
    if (state.title && state.lane !== "current_job") {
      try {
        state.baseline = await api("/api/results", { ...resultsBody({ lane: "current_job", lambda: 0, mu: 0 }), confirmed: [], unticked_title_activities: [] });
      } catch (e) { state.baseline = null; }
    }
    renderResults();
    showStage("results");
  } catch (err) {
    const errors = err.data?.errors || {};
    const intakeKeys = ["weekly_hours", "budget_rm", "degree_level", "cip_code", "title_code"];
    if (intakeKeys.some(k => k in errors)) {
      const first = showFieldErrors(errors);
      $("#rank-err").textContent = "Some details in step 1 need attention.";
      showStage("work");
      first?.closest(".field")?.querySelector("input, textarea")?.focus();
      return;
    }
    $("#rank-err").textContent = errors.activities || errors.lane || Object.values(errors)[0] || "Something went wrong. Please try again.";
  }
}

const refresh = debounce(async () => {
  try {
    const res = await api("/api/results", resultsBody());
    const keep = state.selected;
    state.results = res;
    state.selected = res.roles.some(r => r.code === keep) ? keep : res.roles[0]?.code || null;
    renderResults(true);
  } catch (err) {
    const errors = err.data?.errors || {};
    $("#change").innerHTML = `<div class="callout">${icon("triangle-alert")}<p>${esc(Object.values(errors)[0] || "Could not update the results.")}</p></div>`;
  }
}, 180);

function releaseLabel(rid) {
  const r = state.sources[`aei-${rid}`];
  if (!r || !r.window_start) return rid;
  const d = new Date(`${r.window_start}T00:00:00`);
  return d.toLocaleDateString("en-GB", { month: "short", year: "numeric" });
}

const DEFAULT_OPTS = { lambda: 0, mu: 0, nu: 0, scenario: "moderate", threshold: 50, pace: 1.444, release: null, horizon: "plan", scope: "skilled" };
const SCEN_LABEL = { slower: "Slower (assumption)", moderate: "Moderate", faster: "Faster (assumption)" };
const OUTLOOK_NU = 0.3;

function renderControls() {
  const o = state.opts, res = state.results;
  const rels = state.status.releases;
  const current = res.release;
  const hz = [["plan", "Estimated planning horizon"], [12, "1 year"], [24, "2 years"], [36, "3 years"], [60, "5 years"]];
  $("#controls").innerHTML = `
    <fieldset class="ctl" data-scope><legend>Order roles by</legend>
      <div class="seg-ctl small order-ctl" style="--n:2"><label><input type="radio" name="order" value="closest" ${o.nu ? "" : "checked"}>Closest match</label><label><input type="radio" name="order" value="outlook" ${o.nu ? "checked" : ""}>Closest with job outlook</label></div>
      <p class="foot">${o.nu ? `Adds ${OUTLOOK_NU} times the US projected-growth band (Decline -1 to Much faster +1) to each role's score.` : "Activity overlap only. Each role still shows its job outlook."}</p>${srcMarks(["onet-online-outlook"])}
      <label class="check-line"><input type="checkbox" id="c-scope" ${o.scope === "all" ? "checked" : ""}> Also explore semi-skilled and unmapped roles</label>
      <p class="foot">${esc(res.scope_note)}</p>${srcMarks(["esco-onet-isco"])}</fieldset>
    <fieldset class="ctl"><legend>${icon("history", "sm")} AI-usage release</legend>
      <div class="seg-ctl small rel-ctl" style="--n:${rels.length}">${rels.map((r, i) => `<label title="Release ${esc(r)}"><input type="radio" name="rel" value="${esc(r)}" data-i="${i}" ${r === current ? "checked" : ""}>${esc(releaseLabel(r))}</label>`).join("")}</div>
      <p class="foot">Figures and the radar average every release up to the one you choose.</p></fieldset>
    <div class="ctl"><label for="c-lambda">AI evidence in ranking: <span class="val">${o.lambda ? o.lambda.toFixed(2) : "off"}</span></label>
      <input id="c-lambda" type="range" min="0" max="0.5" step="0.05" value="${o.lambda}">
      <p class="foot">Lowers roles where AI already does more of the tasks directly. Off by default.</p></div>
    <div class="ctl"><label for="c-mu">Decent-work priority (SDG 8.5): <span class="val">${o.mu ? o.mu.toFixed(1) : "off"}</span></label>
      <input id="c-mu" type="range" min="0" max="1" step="0.1" value="${o.mu}">
      <p class="foot">Raises roles with a larger DOSM pay step from your current group.</p></div>
    <fieldset class="ctl" data-scope><legend>Future scenario</legend>
      <div class="seg-ctl small scen-ctl" style="--n:3">${["slower", "moderate", "faster"].map(k => `<label><input type="radio" name="scen" value="${k}" ${o.scenario === k ? "checked" : ""}>${k === "moderate" ? "Moderate" : capitalise(k)}</label>`).join("")}</div>
      <div class="inline-nums"><label for="c-thr">Threshold <input id="c-thr" type="number" min="1" max="100" step="1" value="${o.threshold}">%</label>
        <label for="c-pace">Faster pace <input id="c-pace" type="number" min="1" max="3" step="0.01" value="${o.pace}"> per 8 months</label>${srcMarks(["aei-report-2511.15080", "your-input"])}</div>
      <p class="foot">Moderate keeps each activity's historical average. Slower and faster are assumptions. The plan adds a step to direct and check AI output on a gap activity when its projected share reaches the threshold.</p></fieldset>
    <div class="ctl"><label for="c-hz">Look ahead to</label>
      <select id="c-hz">${hz.map(([v, t]) => `<option value="${v}" ${String(o.horizon) === String(v) ? "selected" : ""}>${t}</option>`).join("")}</select>
      <p class="foot">The estimated planning horizon is the longest of one year (an assumption), the plan's study time, and the time a role's requirements imply: a degree top-up (24 months, an assumption) or related experience. It is a look-ahead for the scenarios, not a date by which you are ready for the role.</p></div>
    <div class="ctl-wide"><p class="replay-msg">${esc(res.replay.message || "First bundled release: nothing earlier to compare.")}${res.replay.comparability_note ? ` ${esc(res.replay.comparability_note)}` : ""}</p>
      <button type="button" class="btn btn-gray" id="c-reset">Reset weights and scenario</button></div>`;
  $("#c-lambda").addEventListener("input", e => { o.lambda = Number(e.target.value); e.target.previousElementSibling.querySelector(".val").textContent = o.lambda ? o.lambda.toFixed(2) : "off"; refresh(); });
  $("#c-mu").addEventListener("input", e => { o.mu = Number(e.target.value); e.target.previousElementSibling.querySelector(".val").textContent = o.mu ? o.mu.toFixed(1) : "off"; refresh(); });
  $$('input[name="order"]').forEach(i => i.addEventListener("change", () => { o.nu = i.value === "outlook" ? OUTLOOK_NU : 0; refresh(); }));
  $$('input[name="scen"]').forEach(i => i.addEventListener("change", () => { o.scenario = i.value; refresh(); }));
  $$('input[name="rel"]').forEach(i => i.addEventListener("change", () => { o.release = i.value; refresh(); }));
  $("#c-hz").addEventListener("change", e => { o.horizon = e.target.value === "plan" ? "plan" : Number(e.target.value); refresh(); });
  $("#c-scope").addEventListener("change", e => { o.scope = e.target.checked ? "all" : "skilled"; refresh(); });
  $("#c-thr").addEventListener("change", e => { const v = Number(e.target.value); if (v >= 1 && v <= 100) { o.threshold = v; refresh(); } });
  $("#c-pace").addEventListener("change", e => { const v = Number(e.target.value); if (v >= 1 && v <= 3) { o.pace = v; refresh(); } });
  $("#c-reset").addEventListener("click", () => { Object.assign(o, DEFAULT_OPTS); refresh(); });
}

function bandShort(jo) { return jo.band ? jo.band.split(" (")[0] : "No projection"; }

function radarBlock(r) {
  const f = state.results.forecast, sc = state.results.scenario;
  const hz = r.horizon;
  const clsKey = c => ({ "AI does most of it directly": "ai", "shared": "mid", "people mostly work with AI": "people" }[c] || "none");
  const hzText = `${hz.basis === "your choice" ? "the look-ahead you chose" : `an estimated planning horizon (${hz.basis})`}`;
  const rows = r.radar.map(x => `<tr>
      <th scope="row"><span class="act-name">${esc(x.name)}</span><span class="kind">${esc(x.kind)}${x.weak ? " · evidence weak" : ""}</span></th>
      <td data-label="Now">${x.now ? fig(x.now, "pct") : `<span class="foot">no data</span>`}</td>
      ${["slower", "moderate", "faster"].map(k => `<td data-label="${k === "moderate" ? "Moderate" : capitalise(k)}" class="${k === sc ? "sel" : ""}">${x.now ? fig(x.projected[k], "pct") : ""}</td>`).join("")}
      <td data-label="In the ${esc(sc)} scenario"><span class="cls cls-${clsKey(x.classes[sc])}">${esc(x.classes[sc])}</span></td></tr>`).join("");
  const err = f.error_mae ? `Moderate keeps the historical average; its typical error is ${fig(f.error_mae, "")} points and 80% of errors are within ${fig(f.error_p80, "")} points (backtest on the June 2026 release${f.calibrated === false ? "; the band is not calibrated" : ""}). ` : "";
  const dir = f.direction_labels ? "" : "Past release-to-release changes reversed more often than they continued, so the radar shows no rising or falling labels; change appears only through the scenarios. ";
  return `<section class="sec-block radar" data-scope aria-labelledby="h-radar"><h5 id="h-radar">${icon("history", "sm")} Future Skills Radar</h5>
    <p class="foot">How much of each activity AI does directly in observed Claude use (global), now and projected ${fig(hz.months, "")} months ahead, ${esc(hzText)}. These are estimates of how the work shifts between doing it and directing and checking AI, not forecasts of jobs.</p>
    <div class="radar-wrap"><table class="radar-t"><thead><tr><th scope="col">Activity</th><th scope="col">Now</th><th scope="col">Slower</th><th scope="col">Moderate</th><th scope="col">Faster</th><th scope="col">In the ${esc(sc)} scenario</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="foot">${err}Slower subtracts ${fig(f.slower_drop, "")} points, the largest fall between bundled releases. ${dir}Thresholds: ${fig(f.threshold, "")}% for "AI does most of it directly", below ${fig(f.lower_cut, "")}% for "people mostly work with AI".</p>${srcMarks(["aivancin-backtest"])}
  </section>`;
}

function outlookFact(r) {
  const jo = r.job_outlook;
  return `<div class="fact" data-scope><dt><span class="tile-ico">${icon("banknote", "sm")}</span>Job outlook (US projection)</dt>
    <dd>${jo.band ? `BLS projected growth ${esc(jo.period)}: <b>${esc(jo.band)}</b>${jo.openings ? `; ${fig(jo.openings, "int")} projected openings (${esc(jo.openings_period)})` : ""}.` : "No projection published for this role."}
      ${jo.bright.length ? ` Bright Outlook: ${esc(jo.bright.join(", "))}.` : ""}${srcMarks(jo.src)}</dd>
    <dd class="src-line">United States figures, kept separate from AI usage and DOSM. AIVANCIN found no Malaysian per-occupation projection to show.</dd></div>`;
}

function overlapBar(role, large) {
  const segs = role.done.map(d => ({ ...d, done: true })).concat(role.missing.map(d => ({ ...d, done: false })))
    .sort((a, b) => b.weight.v - a.weight.v || (a.dwa_id < b.dwa_id ? -1 : 1));
  const total = segs.reduce((s, x) => s + x.weight.v, 0) || 1;
  return `<div class="bar ${large ? "lg" : ""}" role="img" aria-label="${esc(role.summary)}">${segs.map(s =>
    `<span class="seg ${s.done ? "done" : ""}" style="flex:${(s.weight.v / total).toFixed(4)}" ${large ? `title="${esc(s.name)}${s.done ? " (you do this)" : ""}"` : ""}></span>`).join("")}</div>`;
}

function renderResults(keepFocus) {
  const res = state.results;
  const c = laneCounts();
  const laneWord = state.lane === "all" ? "" : `${LANE_TEXT[state.lane]} `;
  $("#results-lead").innerHTML = `<p>Graduate-level roles (O*NET Job Zone 3 or 4) that share the most recorded activities with your ${fig(res.profile_size, "int")} ${laneWord}activities, out of ${fig(res.candidate_pool, "int")} roles compared. Activity overlap is not how much of a job you can already do.</p>`;
  let change = "";
  if (res.without_ai_suggestions) change += `<div class="callout">${icon("info")}<p>Ranked without AI suggestions: every activity was added by you.</p></div>`;
  if (res.no_overlap_listed) change += `<div class="callout">${icon("info")}<p>Few roles your degree field leads to share your activities yet. Roles with no activity overlap yet are listed after the others.</p></div>`;
  if (state.baseline && state.baseline.roles.length) {
    const base = state.baseline.roles.map(r => r.code);
    const now = res.roles.map(r => r.code);
    const changed = now.filter(x => !base.includes(x)).length;
    change += `<div class="callout">${icon("layers")}<div><p><b>${changed === now.length ? `All ${now.length}` : `${changed} of the ${now.length}`} closest roles changed when you added the activities you confirmed.</b></p>
      <details><summary>Your job title alone gives</summary><ol>${state.baseline.roles.map(r => `<li>${esc(r.title)}</li>`).join("")}</ol></details></div></div>`;
  }
  if (Object.keys(res.rejected_releases || {}).length) change += `<div class="callout">${icon("triangle-alert")}<p>An AI-usage release failed its check and is not used: ${esc(Object.keys(res.rejected_releases).join(", "))}. The other releases stay active.</p></div>`;
  $("#change").innerHTML = change;
  renderControls();
  $("#role-list").innerHTML = res.roles.map(r => {
    const moved = r.rank_without_weights && r.rank_without_weights !== r.rank
      ? `<span class="rr-move">${r.rank < r.rank_without_weights ? "Up" : "Down"} ${Math.abs(r.rank_without_weights - r.rank)} with ${state.results.order === "outlook" ? "the outlook order and your weights" : "your weights"}</span>` : "";
    return `<li><button type="button" class="role-row" data-role="${esc(r.code)}" aria-pressed="${r.code === state.selected}">
      <span class="rank">${r.rank}</span><span><span class="rr-title">${esc(r.title)}</span><span class="rr-meta">O*NET ${esc(r.code)} · Job Zone ${fig(r.job_zone, "")}</span>
      <span class="rr-outlook"><span class="tag good">US outlook: ${esc(bandShort(r.job_outlook))}</span>${r.job_outlook.bright.length ? ` <span class="tag sdg">Bright Outlook</span>` : ""}${srcMarks(r.job_outlook.src)}</span>
      <span class="rr-bar">${overlapBar(r, false)}</span><span class="rr-foot"><span>You do <b>${fig(r.done_count, "int")}</b> of ${fig(r.total_count, "int")} activities</span>${moved}</span></span>${icon("chevron-right")}</button></li>`;
  }).join("") || `<li class="loading">No role shares an activity with this lane yet. Choose another lane or tick more activities.</li>`;
  if (res.roles.length) $("#role-list").insertAdjacentHTML("beforeend", `<li class="list-srcs srcs-slot"></li>`);
  attachSources($("#role-list"));
  attachSources($("#results-lead"));
  attachSources($("#controls"));
  renderDetail();
  renderPath();
  if (!keepFocus) $("#role-list .role-row")?.blur();
}

function sentenceFor(dwaId) {
  const s = suggestionMap().get(dwaId);
  if (state.ticked.has(dwaId) && s) {
    const t = s.triggers.find(x => !x.guarded) || s.triggers[0];
    const sent = state.suggest.sentences.find(x => x.index === t.sentence);
    if (sent) return `<p class="from"><mark class="words">${esc(sent.text)}</mark></p>`;
  }
  if (state.manual.has(dwaId)) return `<p class="from-sys">Added by you (${esc(FIELD_LABEL[state.manual.get(dwaId).lane].toLowerCase())})</p>`;
  return `<p class="from-sys">From your job title</p>`;
}

function groupDone(done) {
  const groups = new Map();
  for (const d of done) {
    const key = sentenceFor(d.dwa_id);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(d);
  }
  return [...groups].map(([from, acts]) => `<li>${from}${acts.map(d => `<p class="act">${icon("check", "sm")}${esc(d.name)} <span class="sdg-mini">SDG 4.4</span></p>`).join("")}</li>`).join("");
}

function labelTag(l) {
  const ico = { fits_studies: "graduation-cap", needs_degree: "graduation-cap", needs_experience: "briefcase-business", needs_licence: "id-card" }[l.id];
  return `<span class="tag ${l.id === "fits_studies" ? "good" : "limit"}">${icon(ico, "sm")}${esc(l.text)}${srcMarks(l.src)}</span>`;
}

function renderDetail() {
  const r = state.results.roles.find(x => x.code === state.selected);
  const box = $("#detail");
  if (!r) { box.innerHTML = ""; return; }
  const ai = r.ai;
  const lam = state.results.weights.lambda.v;
  const gaps = r.missing.slice(0, 8);
  const aiBlock = ai ? `
      <dd><b>${fig(ai.share, "pct")}</b> share of Claude conversations on this job's tasks where AI does the work directly (Claude usage, global, averaged over releases up to ${esc(state.results.release)}).
        <div class="ev-bar" aria-hidden="true"><i style="width:${Math.min(100, ai.share.v)}%"></i></div>
        <p class="src-line">Covers ${fig(ai.coverage, "pct")} of this role's task weight. ${ai.weak ? `<span class="tag weak">Evidence weak</span>` : ""}
        ${r.ai_occupation ? ` Occupation-level figure for ${esc(releaseLabel(r.ai_occupation.release))}: ${fig(r.ai_occupation.share, "pct")}.` : ""}</p>
        <p class="src-line">${lam ? `Used in the ranking with weight ${fig(state.results.weights.lambda, "")}.` : "Not used in the ranking: the weight is 0."} Higher does not mean worse prospects: this is usage data, not labour demand.</p>
        <details class="more"><summary class="link-btn">Task-level figures</summary><ul class="ev-tasks">${ai.tasks.slice(0, 6).map(t =>
          `<li>${esc(t.task)}<br><b>${fig(t.share, "pct")}</b>${t.interval ? ` <span class="foot">(95% interval ${fig({ v: t.interval[0], src: t.share.src })} to ${fig({ v: t.interval[1], src: t.share.src }, "pct")}, ${fig({ v: t.pooled_classified, src: t.share.src }, "int")} classified conversations)</span>` : ""}
           ${t.weak ? `<span class="tag weak">Evidence weak</span>` : ""}<span class="ev-dots">${t.releases.map(x => `<span>${esc(releaseLabel(x.release))}: ${fig({ v: x.share, src: [`aei-${x.release}`] }, "pct")}</span>`).join("")}</span></li>`).join("")}</ul></details></dd>`
    : `<dd>No AI evidence: no task of this role appears in the bundled releases.</dd>`;
  const delta = (state.results.replay.deltas || []).find(d => d.code === r.code);
  const cur = state.results.current;
  const pay = r.pay ? `<dd>${esc(r.masco.group)}: DOSM 2025 median ${fig(r.pay.target_median, "rm")} a month; step from your current group: <b>${fig(r.pay.step, "step")}</b>. Group medians, not a salary offer.</dd>`
    : `<dd>${esc(r.masco.major ? cur.note || "Choose your current job title to see the pay step." : "Skill level unmapped for this role, so no pay step is shown.")}</dd>`;
  const plan = r.plan;
  const steps = plan.steps.map(s => {
    const gapName = s.kind === "tool" ? `Tool: ${esc(s.gap.name)}` : esc(s.gap.name);
    if (!s.course) return `<li class="none"><p class="gap-name">${gapName}</p><p class="meta">${esc(capitalise(s.status))} · SDG 4.4</p></li>`;
    return `<li><p class="gap-name">${gapName}</p><p class="course"><a href="${esc(s.course.url)}" target="_blank" rel="noopener">${esc(s.course.title)} ${icon("external-link", "sm")}</a></p>
      <p class="meta">${esc(s.course.provider)} · ${fig(s.course.cost_rm, "rm")} · ${fig(s.course.hours, "")} hours${s.cum_weeks ? ` · ${fig(s.cum_weeks, "")} weeks so far at your pace` : ""} · link checked ${esc(s.course.checked_on)} · SDG 4.4${s.status !== "verified" ? ` · ${esc(s.status)}` : ""}</p></li>`;
  }).join("");
  const aiSteps = plan.ai_steps.map(s => `<li class="ai"><p class="gap-name">Learn to direct and check AI output on: ${esc(s.gap.name)}</p>
      <p class="meta">AI does this activity directly in ${fig({ v: s.reason.now, src: s.reason.src }, "pct")} of observed Claude conversations now; ${fig({ v: s.reason.projected, src: s.reason.src.concat(["your-input"]) }, "pct")} projected ${fig(r.horizon.months, "")} months ahead in the ${esc(state.results.scenario)} scenario, at or above the ${fig(state.results.threshold, "pct")} threshold.${s.reason.weak ? " Evidence weak." : ""}</p>
      ${s.course ? `<p class="course"><a href="${esc(s.course.url)}" target="_blank" rel="noopener">${esc(s.course.title)} ${icon("external-link", "sm")}</a> · ${fig(s.course.hours, "")} hours</p>
        <p class="meta">${s.match === "activity" ? "Teaches AI use on this activity" : "General guide to directing and checking AI output; no verified resource teaches AI use on this activity"}${s.status === "covered by an earlier step" ? " · already counted in an earlier step" : ""} · SDG 4.4</p>` : `<p class="meta">No verified resource · SDG 4.4</p>`}</li>`).join("");
  const tools = r.emerging.technologies;
  box.innerHTML = `<article class="detail" aria-labelledby="d-title" data-scope>
    <p class="d-kicker">Rank ${r.rank} of ${state.results.roles.length} · O*NET ${esc(r.code)} · Job Zone ${fig(r.job_zone, "")}</p>
    <div class="d-title"><h4 id="d-title">${esc(r.title)}</h4></div>
    <div class="labels" data-scope>${r.labels.map(labelTag).join("")}${r.sdg.map(s => `<span class="tag sdg">SDG ${esc(s.goal)}</span>`).join("")}</div>
    <div data-scope><div class="d-bar">${overlapBar(r, true)}</div>
    <p class="d-overlap">You do ${fig(r.done_count, "int")} of its ${fig(r.total_count, "int")} recorded activities (activity overlap ${fig(r.overlap, "pct")}).</p>
    <div class="legend"><span><i class="done"></i>You do this</span><span><i></i>Still to build</span><span>Wider segments are activities that appear in more of the role's tasks.</span></div></div>
    <div class="d-grid">
      <div><h5>You already do</h5><p class="foot">Grouped by where it came from.</p><ul class="did">${groupDone(r.done)}</ul></div>
      <div><h5>Still to build</h5><p class="foot">Most frequent in the role's tasks first.</p><ul class="gaps">${gaps.map(g => `<li><b></b><span>${esc(g.name)}</span><span class="w">SDG 4.4</span></li>`).join("")}</ul>
        ${r.missing.length > gaps.length ? `<details class="more"><summary class="link-btn">${r.missing.length - gaps.length} more in the full gap table</summary><ul class="gaps">${r.missing.slice(8).map(g => `<li><b></b><span>${esc(g.name)}</span><span class="w">SDG 4.4</span></li>`).join("")}</ul></details>` : ""}
        ${r.tool_gaps.length ? `<p class="foot" style="margin-top:12px">Plus ${r.tool_gaps.length} in-demand tools you have not marked as known (below).</p>` : ""}</div>
    </div>
    <dl class="facts">
      <div class="fact" data-scope><dt><span class="tile-ico ev">${icon("history", "sm")}</span>AI usage on this job's tasks</dt>${aiBlock}
        ${delta ? `<p class="src-line">Change since ${esc(releaseLabel(state.results.replay.previous))}: ${fig(delta.delta, "pts")} (a release-to-release delta, not a trend).</p>` : ""}</div>
      ${outlookFact(r)}
      <div class="fact" data-scope><dt><span class="tile-ico">${icon("wallet", "sm")}</span>Pay step (DOSM 2025)</dt>${pay}
        <dd class="src-line">${esc(r.masco.label)}${r.masco.mapping ? ` (mapping: ${esc(r.masco.mapping)})` : ""}${srcMarks(["esco-onet-isco"])}</dd></div>
      <div class="fact" data-scope><dt><span class="tile-ico">${icon("layers", "sm")}</span>SDG</dt>${r.sdg.map(s => `<dd><b>SDG ${esc(s.goal)}:</b> ${esc(s.reason)}${srcMarks(s.src)}</dd>`).join("")}
        <dd><b>SDG 4.4:</b> every activity you already do and every gap is a skill for employment; each learning step builds one.</dd></div>
      <div class="fact" data-scope><dt><span class="tile-ico">${icon("file-text", "sm")}</span>What's emerging</dt>
        <dd>${r.emerging.tasks.length ? `<ul class="emerging">${r.emerging.tasks.map(t => `<li>${esc(t.task)} <span class="foot">(${esc(t.category)}, ${esc(t.date)})${srcMarks(t.src)}</span></li>`).join("")}</ul>` : "No emerging-task record in O*NET 31.0."}</dd>
        <dd>${tools.length ? `In-demand technologies (US employer postings)${srcMarks(["onet31-software"])}. Tick the ones you already use:${techList(tools)}` : "No in-demand technology listed."}</dd></div>
    </dl>
    ${radarBlock(r)}
    <section class="sec-block" aria-labelledby="h-plan" data-scope><h5 id="h-plan">${icon("route", "sm")} Your plan for the largest gaps</h5>
      <p class="foot">Free, link-checked resources only. Gaps without one say so; nothing is filled in by generated text.</p>
      <ol class="plan">${steps}${aiSteps}</ol>
      <div class="totals"><span>${icon("clock", "sm")}${fig(plan.total_hours, "")} hours in total</span><span>${icon("wallet", "sm")}${fig(plan.total_cost_rm, "rm")} of your ${fig({ v: state.lastIntake.budget_rm, src: ["your-input"] }, "rm")} budget</span>
        <span>About ${fig(plan.study_months, "")} months of study at ${fig({ v: state.lastIntake.weekly_hours, src: ["your-input"] }, "int")} hours a week (estimate; each course counted once)</span>
        <span>${icon("history", "sm")}Scenarios look ${fig(r.horizon.months, "")} months ahead (${esc(r.horizon.basis)})</span></div>
      <ul class="notes">${r.scenario_notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul>
      <button type="button" class="btn btn-primary choose" data-target="${esc(r.code)}">${state.target === r.code ? "Chosen as your target" : "Choose this role as my target"}</button>
    </section>
  </article>`;
  attachSources(box);
}

function techList(tools) {
  const box = t => `<label><input type="checkbox" data-tool="${esc(t.name)}" ${state.knownTools.has(t.name.toLowerCase()) ? "checked" : ""}>${esc(t.name)}${t.hot ? ` <span class="hot">hot</span>` : ""}</label>`;
  const first = tools.slice(0, 10), rest = tools.slice(10);
  return `<div class="tech">${first.map(box).join("")}</div>${rest.length ? `<details class="more"><summary class="link-btn">Show ${rest.length} more</summary><div class="tech">${rest.map(box).join("")}</div></details>` : ""}
    <p class="foot">"Hot" marks O*NET Hot Technologies: tools frequently named in US employer job postings.</p>`;
}

function capitalise(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

function renderPath() {
  const box = $("#path");
  const r = state.results.roles.find(x => x.code === state.target);
  if (!r) { box.innerHTML = ""; return; }
  const b = state.results.baseline;
  const skills = r.plan.steps.filter(s => s.course && s.status === "verified").length;
  box.innerHTML = `<section class="path-card" aria-labelledby="h-path" data-scope><p class="eyebrow">Your chosen path</p><h3 id="h-path">${esc(r.title)}: how this path contributes</h3>
    <ul>
      <li><b>SDG 8.5, decent work:</b> ${esc(r.sdg[0].reason)}${srcMarks(r.sdg[0].src)}</li>
      <li><b>SDG 4.4, skills for employment:</b> your plan covers ${skills} gap ${skills === 1 ? "skill" : "skills"} with verified free resources.</li>
      ${b ? `<li><b>Why it matters in Malaysia:</b> in ${esc(b.date.slice(0, 7))}, ${fig(b.rate_25_34, "pct")} of employed tertiary-educated Malaysians aged 25-34 (${fig(b.persons_25_34_k, "int")} thousand people) worked in semi- or low-skilled jobs.</li>
      <li><b>SDG 5, gender equality:</b> women's skill-related underemployment is ${fig(b.rate_female, "pct")} against men's ${fig(b.rate_male, "pct")} (all ages).</li>` : ""}
    </ul></section>`;
  attachSources(box);
}

function resultsEvents() {
  $("#results").addEventListener("click", e => {
    const row = e.target.closest(".role-row");
    if (row) {
      state.selected = row.dataset.role;
      $$(".role-row").forEach(x => x.setAttribute("aria-pressed", String(x === row)));
      renderDetail();
      if (matchMedia("(max-width: 979px)").matches) $("#detail").scrollIntoView({ block: "start" });
      return;
    }
    const t = e.target.closest("[data-target]");
    if (t) {
      state.target = t.dataset.target;
      renderDetail(); renderPath();
      $("#path").scrollIntoView({ block: "nearest" });
    }
  });
  $("#results").addEventListener("change", e => {
    if (e.target.matches("input[data-tool]")) {
      const n = e.target.dataset.tool.toLowerCase();
      if (e.target.checked) state.knownTools.add(n); else state.knownTools.delete(n);
      refresh();
    }
  });
}

/* ---------- start ---------- */
function renderSources() {
  const recs = state.status.snapshots;
  recs.forEach((r, i) => { state.sources[r.source_id] = r; state.sourceNo[r.source_id] = i + 1; });
  $("#sources").innerHTML = recs.map((r, i) => `<li id="src-${esc(r.source_id)}"><b>${i + 1}. ${esc(r.title)}</b>. ${esc(r.publisher || "")}. ${esc(r.release || "")}${r.window_start ? `, window ${esc(r.window_start)} to ${esc(r.window_end)}` : ""}; accessed ${esc(r.access_date || "")}; ${esc(r.licence || "")}. ${/^https?:/.test(r.url || "") ? `<a href="${esc(r.url)}" rel="noopener" target="_blank">Source</a>` : esc(r.url || "")}</li>`).join("")
    + `<li id="src-your-input"><b>you. Your input</b>: hours, budget and the weights you set.</li>`;
}

async function start() {
  initTheme();
  $("#intake").addEventListener("submit", readWork);
  combo($("#f-title"), $("#f-title-list"), async q => (await api(`/api/occupations?q=${encodeURIComponent(q)}`)).map(o => ({ ...o, label: o.title, meta: o.code })), it => setTitle(it.code));
  combo($("#f-field"), $("#f-field-list"), async q => (await api(`/api/fields?q=${encodeURIComponent(q)}`)).map(o => ({ ...o, label: o.title, meta: `CIP ${o.cip}` })), it => setCip({ cip: it.cip, title: it.title }));
  reviewEvents();
  resultsEvents();
  try {
    const [status, personas] = await Promise.all([api("/api/status"), api("/api/personas")]);
    state.status = status; state.personas = personas;
    renderSources();
    if (status.ai_suggestions !== "available") {
      $("#ai-status").textContent = "AI suggestions are unavailable. You can still search for activities and see roles.";
      $("#ai-status").classList.add("warn");
    }
    $("#personas").insertAdjacentHTML("beforeend", personas.map(p => `<button type="button" class="btn btn-gray" data-persona="${esc(p.id)}">Try ${esc(p.name)}'s example</button>`).join(""));
    $("#personas").addEventListener("click", e => {
      const b = e.target.closest("[data-persona]");
      if (b) loadPersona(state.personas.find(p => p.id === b.dataset.persona));
    });
  } catch (e) {
    $("#ai-status").textContent = "The service is starting or unavailable. Reload in a moment.";
    $("#ai-status").classList.add("warn");
  }
}

start();

/* ---------- adopted revision: compare routes, your plan, what changed (SPEC section 10) ---------- */
const routeState = { data: null, target: null, completed: new Set(), opts: { weeks: 4, willing_long: false, scenario: "moderate", threshold: 50 }, change: null };
const STATUS_CLASS = { met: "good", missing: "limit", planned: "sdg", "you decide": "weak" };

function routesRequest(extra = {}) {
  const base = resultsBody();
  return { ...base, options: { ...routeState.opts, completed: [...routeState.completed], ...(routeState.target ? { target: routeState.target } : {}), ...extra } };
}

function readRouteOpts() {
  const f = $("#intake");
  routeState.opts.weeks = Number(f.elements.weeks.value);
  routeState.opts.willing_long = f.elements.willing_long.value === "yes";
}

async function compareRoutes() {
  $("#rank-err").textContent = "";
  readRouteOpts();
  try {
    routeState.data = await api("/api/routes", routesRequest());
    renderRoutes();
    showStage("routes");
  } catch (err) {
    const errors = err.data?.errors || {};
    if (["weekly_hours", "budget_rm", "degree_level"].some(k => k in errors)) {
      const first = showFieldErrors(errors);
      $("#rank-err").textContent = "Some details in step 1 need attention.";
      showStage("work");
      first?.closest(".field")?.querySelector("input, textarea")?.focus();
      return;
    }
    $("#rank-err").textContent = errors.activities || Object.values(errors)[0] || "Something went wrong. Please try again.";
  }
}

function reqRow(q) {
  return `<li><span class="tag ${STATUS_CLASS[q.status] || "weak"}">${esc(capitalise(q.status))}</span> <span>${esc(q.text)}</span>
    <span class="foot">${esc(q.scope)} · ${esc(q.why)}</span>${srcMarks(q.src)}</li>`;
}

function routeCard(r) {
  const b = r.block;
  const chosen = routeState.target === r.code;
  const served = r.prioritized.filter(g => g.served).length;
  return `<article class="route-card${chosen ? " chosen" : ""}" data-scope aria-labelledby="rt-${esc(r.code)}">
    <p class="d-kicker">${esc(r.family_label)}</p>
    <h3 id="rt-${esc(r.code)}">${esc(r.title)}</h3>
    <p class="rr-meta">O*NET ${esc(r.code)} · Job Zone ${fig(r.job_zone, "")} · ${esc(r.masco.label || "")}</p>
    <div class="rc-sec"><h4>Your work in this role</h4>
      <p>You do ${fig(r.done_count, "int")} of its ${fig(r.total_count, "int")} recorded activities (activity overlap ${fig(r.overlap, "pct")}).</p>
      ${r.related.length ? `<p class="foot">Also counted toward this route: ${r.related.map(x => `“${esc(x.name.replace(/\.$/, ""))}”, which O*NET groups with its “${esc(x.role_name.replace(/\.$/, ""))}”`).join("; ")}. These do not change the overlap or the gaps.</p>` : ""}</div>
    <div class="rc-sec"><h4>Requirements</h4><ul class="reqs">${r.requirements.map(reqRow).join("")}</ul></div>
    <div class="rc-sec"><h4>Opportunity</h4>
      <p><span class="tag good">US outlook: ${esc(bandShort(r.job_outlook))}</span>${r.job_outlook.bright.length ? ` <span class="tag sdg">Bright Outlook</span>` : ""}${srcMarks(r.job_outlook.src)}</p>
      <p class="foot">${esc(r.local_evidence)}</p>
      ${r.pay ? `<p class="foot">DOSM 2025 median pay step from your current group: ${fig(r.pay.step, "step")}. Group medians, not a salary offer.</p>` : ""}</div>
    <div class="rc-sec"><h4>First ${fig(routeState.data.weeks, "int")}-week block</h4>
      <p><b>${fig(b.effort_hours, "")}</b> of ${fig(b.capacity_hours, "")} hours · ${fig(b.cost_rm, "rm")} · serves ${served} of ${r.prioritized.length} priority gaps</p>
      <p class="foot">${esc(capitalise(b.status))}. Verified resources exist for ${fig(r.supported_share, "pct")} of the priority gaps.</p></div>
    <p class="foot"><b>SDG 8.5:</b> ${esc(r.sdg[0].reason)}${srcMarks(r.sdg[0].src)}</p>
    <button type="button" class="btn ${chosen ? "btn-primary" : "btn-gray"} choose-route" data-route="${esc(r.code)}">${chosen ? "Chosen: see your plan" : "Choose this route"}</button>
  </article>`;
}

function renderRoutes() {
  const d = routeState.data;
  $("#routes-lead").innerHTML = `<p>Supported destinations (${fig(d.supported_destinations, "int")} roles in three families) where your confirmed work already counts. Each card shows its dimensions separately; there is no combined score, and you choose.</p>`;
  const shared = d.shared;
  $("#routes-body").innerHTML = `
    ${d.fewer_note ? `<div class="callout">${icon("info")}<p>${esc(d.fewer_note)}</p></div>` : ""}
    ${d.routes.length ? `<div class="route-grid">${d.routes.map(routeCard).join("")}</div>` :
      `<div class="callout">${icon("info")}<p>None of the supported routes counts at least two of your confirmed activities yet. Explore all roles, or tick more activities.</p></div>`}
    ${shared ? `<section class="path-card" data-scope aria-labelledby="h-shared"><h3 id="h-shared">Shared foundations</h3>
      <p>${shared.common_steps.length ? `${shared.common_steps.length} step${shared.common_steps.length === 1 ? "" : "s"} (${fig(shared.common_hours, "")} hours) appear in every route's block and serve a priority gap of each, so they keep these options open.` : "No step is common to every route's block."}</p>
      <ul>${shared.extra_hours.map(e => `<li>${esc(e.title)}: ${fig(e.hours, "")} hours beyond the shared steps</li>`).join("")}</ul>
      <p class="foot">These describe the compared plans, not a probability of anything.</p></section>` : ""}
    <p class="explore-row"><button type="button" class="link-btn" data-action="rank">Explore all roles, including ones without complete-route support</button></p>`;
  attachSources($("#routes"));
  $$(".stage-links a").forEach(a => { if (a.dataset.stage === "routes") a.removeAttribute("aria-disabled"); });
}

async function choose(code) {
  routeState.target = code;
  await refreshPlan();
  showStage("plan");
}

async function refreshPlan() {
  try {
    routeState.data = await api("/api/routes", routesRequest());
    renderRoutes();
    renderPlan();
  } catch (err) {
    $("#plan-body").innerHTML = `<div class="callout">${icon("triangle-alert")}<p>${esc(Object.values(err.data?.errors || {})[0] || "Could not build the plan.")}</p></div>`;
  }
}

function stepItem(s, i) {
  const ai = s.form === "ai";
  const d = routeState.data;
  const why = s.reason.filter(r => r.projected[d.scenario].v >= d.threshold.v);
  const purpose = s.kind === "prerequisite" ? "Required first by a later step." : s.kind === "ai_practice" ? "AI practice on an activity in this plan." : s.purpose;
  const because = ai && why.length
    ? `<p class="foot">Because ${why.map(r => `AI does “${esc(r.name.replace(/\.$/, ""))}” directly in ${fig(r.now, "pct")} of observed Claude conversations now, ${fig(r.projected[d.scenario], "pct")} projected in the ${esc(d.scenario)} scenario`).join("; ")}; threshold ${fig(d.threshold, "pct")}.</p>`
    : "";
  return `<li class="pstep${s.completed ? " done" : ""}" data-scope>
    <div class="pstep-head"><span class="pnum">${i + 1}</span>
      <div><p class="gap-name">${esc(s.title)} <a href="${esc(s.url)}" target="_blank" rel="noopener" aria-label="Open ${esc(s.title)} at ${esc(s.provider)}">${icon("external-link", "sm")}</a></p>
      <p class="meta">${esc(purpose)}</p></div></div>
    ${s.objective ? `<p class="objective">Provider's objective: “${esc(s.objective)}”</p>` : ""}
    <p class="meta">${esc(s.provider)} · ${fig(s.provider_hours, "")} h course + ${fig(s.practice_hours, "")} h practice (estimate) · ${fig(s.cost_rm, "rm")} · link checked ${esc(s.checked_on)}${s.access ? ` · ${esc(s.access)}` : ""} · SDG 4.4</p>
    <div class="checkpoint${ai ? " ai" : ""}"><p><b>${ai ? "Checkpoint, with AI checking" : "Checkpoint"}:</b> ${esc(s.checkpoint)}</p>${because}</div>
    <label class="check-line"><input type="checkbox" data-done="${esc(s.id)}" ${s.completed ? "checked" : ""}> I have done this step</label>
  </li>`;
}

function renderPlan() {
  const d = routeState.data;
  const r = d.routes.find(x => x.code === routeState.target);
  if (!r) { $("#plan-body").innerHTML = ""; return; }
  const b = r.block;
  const next = b.steps.find(s => !s.completed);
  $("#plan-body").innerHTML = `
    <div class="plan-head" data-scope>
      <p class="d-kicker">${esc(r.family_label)}</p><h3 class="plan-title">${esc(r.title)}</h3>
      <p>${fig(b.effort_hours, "")} of your ${fig(b.capacity_hours, "")} hours over ${fig(d.weeks, "int")} weeks (${fig(b.provider_hours, "")} h of courses, ${fig(b.practice_hours, "")} h of practice, estimated) · ${fig(b.cost_rm, "rm")} of your ${fig(d.budget_rm, "rm")} budget.</p>
      <p class="foot">${esc(capitalise(b.status))}. This block is a start, not a complete transition; it does not make anyone ready for the role.</p>
      ${next ? `<p class="next">${icon("route", "sm")} <b>Start with:</b> ${esc(next.title)}</p>` : `<p class="next">${icon("check", "sm")} Every step in this block is marked done.</p>`}
    </div>
    <fieldset class="ctl plan-scen" data-scope><legend>Future scenario for the checkpoints</legend>
      <div class="seg-ctl small scen-ctl" style="--n:3">${["slower", "moderate", "faster"].map(k => `<label><input type="radio" name="pscen" value="${k}" ${d.scenario === k ? "checked" : ""}>${k === "moderate" ? "Moderate" : capitalise(k)}</label>`).join("")}</div>
      <p class="foot">Looking ${fig(b.horizon.months, "")} months ahead (${esc(b.horizon.basis)}). A step's checkpoint adds AI checking when AI does its activity directly in at least ${fig(d.threshold, "pct")} of observed Claude conversations by then. ${b.scenario_differs.length ? `Steps that change between scenarios: ${esc(b.scenario_differs.join("; "))}.` : "No step changes between scenarios."} Slower and faster are assumptions, not forecasts.</p></fieldset>
    <ol class="psteps">${b.steps.map(stepItem).join("")}</ol>
    ${b.uncovered.length ? `<section class="sec-block" data-scope><h5>Priority gaps without a step in this block</h5><ul class="gaps">${b.uncovered.map(g => `<li><b></b><span>${esc(g.name)}</span><span class="w">SDG 4.4</span></li>`).join("")}</ul>
      <p class="foot">Either no verified resource teaches them yet, or the block's time or budget ran out.</p></section>` : ""}
    <section class="sec-block" data-scope><h5>Beyond this block</h5><ul class="reqs">${r.requirements.map(reqRow).join("")}</ul></section>
    <p class="explore-row"><button type="button" class="btn btn-gray" data-action="changed">See what new evidence changes</button></p>`;
  attachSources($("#plan"));
  $$(".stage-links a").forEach(a => { if (a.dataset.stage === "plan" || a.dataset.stage === "changed") a.removeAttribute("aria-disabled"); });
}

async function showChanged() {
  const ids = state.status.releases;
  routeState.change = routeState.change || { from: ids[0], to: ids[ids.length - 1] };
  try {
    const res = await api("/api/update", routesRequest({ from_release: routeState.change.from, to_release: routeState.change.to }));
    renderChanged(res);
  } catch (err) {
    $("#changed-body").innerHTML = `<div class="callout">${icon("triangle-alert")}<p>${esc(Object.values(err.data?.errors || {})[0] || "Could not compare.")}</p></div>`;
  }
  showStage("changed");
}

function renderChanged(res) {
  const ids = state.status.releases;
  const sel = (name, v) => `<select id="${name}">${ids.map(r => `<option value="${esc(r)}" ${r === v ? "selected" : ""}>${esc(releaseLabel(r))} (release ${esc(r)})</option>`).join("")}</select>`;
  const formText = f => (f === "ai" ? "with AI checking" : "plain");
  // Each pooled release's value and the evidence behind it, so a thin sample is visible next to the change it caused.
  const perRelease = c => (c.after.reason || []).map(r => `<p class="foot">${esc(r.name.replace(/\.$/, ""))}, by release: ${(r.per_release || []).map(x =>
    `${esc(releaseLabel(x.release))} ${fig(x.share, "pct")} (${fig(x.statements, "int")} task statement${x.statements.v === 1 ? "" : "s"}${x.conversations
      ? `, ${fig(x.conversations, "int")} conversations` : ", no counts published"})`).join("; ")}.</p>`).join("");
  const reasonText = c => (c.after.reason || []).map(r => {
    const b0 = (c.before.reason || []).find(x => x.dwa_id === r.dwa_id);
    return `${esc(r.name.replace(/\.$/, ""))}: ${b0 ? `${fig(b0.now, "pct")} → ` : ""}${fig(r.now, "pct")} of observed Claude conversations where AI does it directly`;
  }).join("; ");
  $("#changed-body").innerHTML = `
    <p class="lead-left">Same work, constraints, target, completed steps and scenario (${esc(routeState.opts.scenario)}). Only the AI-usage evidence changes, from one bundled release to another.</p>
    <div class="ctl change-ctl"><label for="c-from">Earlier evidence</label>${sel("c-from", res.from_release)}<label for="c-to">Later evidence</label>${sel("c-to", res.to_release)}</div>
    <div class="callout" data-scope>${icon("history")}<div><p><b>${esc(res.summary)}</b></p>
      <p class="foot">Effort ${fig(res.hours_before, "")} h → ${fig(res.hours_after, "")} h; cost ${fig(res.cost_before, "rm")} → ${fig(res.cost_after, "rm")}. Your completed steps and chosen target are kept.</p>
      ${res.comparability_note ? `<p class="foot">${esc(res.comparability_note)}</p>` : ""}</div></div>
    <ol class="changes">${res.changes.map(c => `<li class="chg ${c.change.replace(" ", "-")}" data-scope>
      <p class="gap-name">${esc(c.title)}${c.completed ? ` <span class="tag good">done</span>` : ""}</p>
      <p class="meta"><b>${esc(capitalise(c.change))}</b>${c.before && c.after && c.change !== "retained" ? `: checkpoint ${formText(c.before.form)} → ${formText(c.after.form)}` : ""}</p>
      ${c.change === "changed form" ? `<p class="foot">${reasonText(c)}.</p>${perRelease(c)}` : ""}
    </li>`).join("")}</ol>`;
  attachSources($("#changed"));
  const go = () => { routeState.change = { from: $("#c-from").value, to: $("#c-to").value }; showChanged(); };
  $("#c-from").addEventListener("change", go);
  $("#c-to").addEventListener("change", go);
}

function routeEvents() {
  document.addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.action === "routes") compareRoutes();
    else if (b.classList.contains("choose-route")) choose(b.dataset.route);
    else if (b.dataset.action === "changed") showChanged();
    else if (b.dataset.action === "rank" && !b.closest("#review")) rank();
  });
  $("#plan").addEventListener("change", e => {
    if (e.target.matches("input[data-done]")) {
      const id = e.target.dataset.done;
      if (e.target.checked) routeState.completed.add(id); else routeState.completed.delete(id);
      refreshPlan();
    } else if (e.target.matches('input[name="pscen"]')) {
      routeState.opts.scenario = e.target.value;
      refreshPlan();
    }
  });
}
routeEvents();

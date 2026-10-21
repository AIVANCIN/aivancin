"use strict";
/* AIVANCIN client. The server does all matching and checks; this file renders state and sends the
   user's own choices back. Nothing is accepted without a tick. The server keeps nothing; answers and
   choices are saved in this browser only (see "saved progress"), and Clear my data removes them. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const FIELD_LABEL = { current_job: "Current job", other_work: "Other work", studies: "Studies" };
const FIELD_ICON = { current_job: "briefcase-business", other_work: "wrench", studies: "graduation-cap", all: "layers" };
const LANE_TEXT = { current_job: "current-job", other_work: "other-work", studies: "studies", all: "" };
const FIELD_PAGE = { current_job: [1, "#f-current"], other_work: [2, "#f-other"], studies: [3, "#f-studies"] };

const state = {
  status: null, personas: [], sources: {}, sourceNo: {},
  title: null, titleActs: [], untickedTitle: new Set(), cip: null,
  suggest: null, ticked: new Map(), dismissed: new Set(), manual: new Map(),
  lane: "all", opts: { lambda: 0, mu: 0, nu: 0, scenario: "moderate", threshold: 50, pace: 1.444, release: null, horizon: "plan", scope: "skilled" },
  knownTools: new Set(), results: null, baseline: null, selected: null, target: null, lastIntake: null,
  openSent: null,  // review: the sentences shown open; null opens the first one
};
/* Compare routes, the chosen plan and what changed (SPEC section 10). */
const routeState = { data: null, target: null, completed: new Set(), opts: { weeks: 4, willing_long: false, scenario: "moderate", threshold: 50 }, change: null,
  sort: "overlap", open: null, tab: "overview", openCp: new Set(), scenOpen: false, learn: null, saved: new Map(), seen: new Set() };
/* Saved progress: routes were compared before the page was reloaded, so Continue can rebuild them. */
let routesReached = false;
const STATUS_CLASS = { met: "good", missing: "limit", planned: "sdg", "you decide": "weak" };

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
    if (id === "explore-defaults") return "starting assumptions: 6 hours a week, RM0 budget";
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
/* Every element marked data-scope ends with a Sources button that opens the sources of the numbers inside it. */
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
    line.innerHTML = `<button type="button" class="src-btn" data-src-ids="${esc(order.join(" "))}" aria-haspopup="dialog" title="${esc(srcTitle(order))}">${icon("info", "sm")}Sources (${order.length})</button>`;
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

/* ---------- screens ----------
   One screen at a time. Each move is a history entry, so the browser's back button and the in-app
   back button agree. The four steps drive the progress bar; the intake questions fill the first quarter. */
const PAGES = 4;
const STEP = { work: [1, "Your work"], reading: [1, "Your work"], review: [1, "Your work"], routes: [2, "Compare routes"], route: [2, "Compare routes"],
  results: [2, "Explore all roles"], plan: [3, "Your plan"], changed: [4, "What changed"] };
let current = { screen: "welcome", page: 1 };

/* Once routes exist, five tabs reach the sections: Routes, Plan, Learn, Saved and Profile. Intake and
   review stay one full screen per step without tabs. Each tab returns to where it was left; choosing the
   tab you are already in goes back to its first screen. */
const TAB_OF = { routes: "routes", route: "routes", results: "routes", plan: "plan", changed: "plan", learn: "learn", saved: "saved", profile: "profile" };
const tabLast = {};

function show(screen, page = 1, focus = true) {
  current = { screen, page };
  $$(".screen").forEach(s => { s.hidden = s.id !== screen; });
  document.body.dataset.screen = screen;
  $("#bar").hidden = screen === "welcome";
  const tab = TAB_OF[screen];
  const tabs = Boolean(tab && routeState.data);
  $("#tabbar").hidden = !tabs;
  document.body.dataset.tabs = tabs ? "on" : "off";
  if (tabs) {
    tabLast[tab] = screen;
    $$("#tabbar .tab").forEach(b => { if (b.dataset.tab === tab) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  }
  if (STEP[screen]) {
    const [n, label] = STEP[screen];
    const fill = screen === "work" ? page / (PAGES + 1) / 4 : screen === "reading" ? 0.22 : n / 4;
    $("#progress-fill").style.width = `${(fill * 100).toFixed(1)}%`;
    $("#bar-count").textContent = `${n}/4`;
    $("#bar-label").textContent = screen === "work" ? `Step 1 of 4, ${label}, question ${page} of ${PAGES}` : `Step ${n} of 4, ${label}`;
  }
  // Every section draws from the current state when it appears, so a tab or the back button never shows stale figures.
  if (screen === "welcome") updateContinue();
  else if (screen === "learn") renderLearn();
  else if (screen === "saved") renderSaved();
  else if (screen === "profile") renderProfile();
  else if (screen === "plan" && routeState.data) renderPlan();
  else if (screen === "routes" && routeState.data) renderRoutes();
  else if (screen === "route" && routeState.data) renderRoute();
  let heading = $("[tabindex='-1']", document.getElementById(screen));
  if (screen === "work") {
    $$(".q-page").forEach(q => { q.hidden = Number(q.dataset.page) !== page; });
    heading = $(`.q-page[data-page="${page}"] [tabindex='-1']`);
    if (!$("#read-btn").disabled) $("#read-btn").innerHTML = page < PAGES ? `Continue${icon("arrow-right", "sm")}` : "Read my work";
    $("#read-now").hidden = page >= PAGES;
  }
  window.scrollTo({ top: 0, behavior: "instant" });
  if (focus && heading) heading.focus({ preventScroll: true });
  saveProgress();
}

/* After a reload or Clear my data, a history entry can point at a screen whose data is gone. */
function canShow(screen) {
  if (["routes", "route", "plan", "changed", "learn", "saved", "profile"].includes(screen)) return Boolean(routeState.data) && (screen !== "route" || Boolean(routeState.open));
  if (screen === "review") return Boolean(state.suggest);
  if (screen === "results") return Boolean(state.results);
  return screen !== "reading";
}

function navigate(screen, page = 1) {
  if (current.screen !== screen || current.page !== page) history.pushState({ screen, page }, "");
  show(screen, page);
}
const showStage = navigate;

function pageOf(el) { return Number(el?.closest(".q-page")?.dataset.page || 1); }

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
  examplePersona = p.id;
  $$("#personas [data-persona]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.persona === p.id)));
  setStatus(`Loaded ${p.name}'s example.`);
}

/* "Get started" is your own words with no examples on screen; "Try an example" fills the questions
   with a synthetic person and shows the example switcher. Whatever you typed yourself is kept aside
   while you look at an example and comes back when you return to your own. */
let mode = "own", ownDraft = null, examplePersona = null;
function snapshotIntake() {
  return { fields: [...$("#intake").elements].filter(el => el.matches("input, textarea, select")).map(el => [el, el.type === "radio" ? el.checked : el.value]),
    title: state.title, titleActs: state.titleActs, untickedTitle: new Set(state.untickedTitle), cip: state.cip };
}
function restoreIntake(d) {
  d.fields.forEach(([el, v]) => { if (el.type === "radio") el.checked = v; else el.value = v; });
  state.title = d.title; state.titleActs = d.titleActs; state.untickedTitle = d.untickedTitle;
  renderChosen($("#f-title"), d.title ? `${d.title.title} (${d.title.code})` : null, () => setTitle(null));
  setCip(d.cip);
}
async function setMode(next) {
  if (next === "example" && mode !== "example") {
    ownDraft = snapshotIntake();
    if (state.personas[0]) await loadPersona(state.personas[0]);
  } else if (next === "own" && mode === "example") {
    if (ownDraft) restoreIntake(ownDraft);
    else { $("#intake").reset(); setTitle(null); setCip(null); }
    examplePersona = null;
    setStatus("");
  }
  mode = next;
  showFieldErrors({});
  $(".examples").hidden = mode !== "example";
}

/* "Read my work now" skips the remaining questions; their starting values stay and can be changed later. */
let readNow = false;
async function readWork(e) {
  e.preventDefault();
  const body = intakeBody();
  const now = readNow;
  readNow = false;
  if (current.page < PAGES && !now) { setStatus(""); navigate("work", current.page + 1); return; }
  const first = showFieldErrors(clientChecks(body));
  if (first) { first.closest(".field").querySelector("input, textarea")?.focus(); return; }
  if (!body.current_job.trim() && !body.other_work.trim() && !body.studies.trim() && !body.title_code) {
    navigate("work", 1);
    setStatus("Write at least one sentence about your work, or choose your current job title.", true);
    return;
  }
  setStatus("");
  $("#read-btn").disabled = true;
  $("#read-btn").textContent = "Reading";
  // Only a slow read (cold start, busy host) shows the reading screen; a fast one goes straight to review.
  const slow = setTimeout(() => show("reading"), 400);
  try {
    const sug = await api("/api/suggest", body);
    state.suggest = sug;
    state.ticked = new Map(); state.dismissed = new Set(); state.manual = new Map(); state.openSent = null;
    state.lastIntake = body;
    // New answers make the compared routes stale until Compare routes runs again; the chosen route, ticked steps and saved routes stay.
    routeState.data = null; routesReached = false;
    renderReview();
    clearTimeout(slow);
    navigate("review");
  } catch (err) {
    clearTimeout(slow);
    if (current.screen === "reading") show("work", PAGES);
    setStatus("Something went wrong while reading your text. Please try again.", true);
  } finally {
    $("#read-btn").disabled = false;
    $("#read-btn").textContent = "Read my work";
  }
}

/* The status line sits above the form's button, so it shows on every question. A warning about the
   service itself stays until reload; other messages clear when the user moves on. */
let serviceWarning = "";
function setStatus(text, warn = false) {
  const el = $("#ai-status");
  el.textContent = text || serviceWarning;
  el.classList.toggle("warn", Boolean(warn || (!text && serviceWarning)));
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
  let html = "";
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
  const lastIndex = sents.filter(x => !x.guard).map(x => x.index).pop();
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
    if (state.openSent === null) state.openSent = new Set([s.index]);
    const last = s.index === lastIndex;
    const next = last ? "" : `<button type="button" class="btn btn-primary next-sent" data-next="${s.index}">Next sentence</button>`;
    html += `<details class="sent" data-sentence="${s.index}" ${state.openSent.has(s.index) ? "open" : ""}>
      <summary><span class="sent-head"><span class="src">${src}</span><span class="sent-status" data-status="${s.index}">${sentStatus(s)}</span></span>
        <blockquote class="words"><mark class="words">${esc(s.text)}</mark></blockquote>${icon("chevron-down", "sent-chev")}</summary>
      ${shortFormHint(s)}
      ${dismissed ? `<p class="dismissed">You said none of the 5 suggestions fit. <button type="button" class="link-btn" data-undismiss="${s.index}">Show them again</button></p>
      ${next ? `<div class="sent-actions">${next}</div>` : ""}`
        : `<fieldset class="sugg"><legend class="vh">Activities suggested for this sentence</legend>${rows}</fieldset>
      <div class="sent-actions"><button type="button" class="btn btn-gray" data-dismiss="${s.index}">None of these fit</button><button type="button" class="btn btn-gray" data-find="${s.index}">${icon("search", "sm")}Find another activity</button>${next}</div>
      <div class="find" data-find-box="${s.index}" hidden></div>`}
    </details>`;
  }
  if (sug.available && !sents.length) html += `<p class="cap">${icon("info")}No sentences to read. Add text in step 1, or search for activities below.</p>`;
  if (sug.available) html += manualSearchCard();
  box.innerHTML = html;
  const manual = $('[data-find-box="manual"]', box);
  if (manual) openFind(manual, "other_work", false);
  renderRail();
}

/* One sentence open at a time keeps the review short; each closed sentence says where it stands. */
function sentStatus(s) {
  if (state.dismissed.has(s.index)) return `<span class="st done">None fit</span>`;
  const n = s.top.filter(d => state.ticked.has(d)).length;
  return n ? `<span class="st done">${icon("check", "sm")}${n} ticked</span>` : `<span class="st">Not checked yet</span>`;
}

function updateSentStatus() {
  const byIndex = new Map((state.suggest?.sentences || []).map(s => [s.index, s]));
  $$("[data-status]").forEach(el => { const s = byIndex.get(Number(el.dataset.status)); if (s) el.innerHTML = sentStatus(s); });
}

function nextSentence(index) {
  const all = $$("#sentences details.sent[data-sentence]");
  const i = all.findIndex(d => Number(d.dataset.sentence) === index);
  const cur = all[i], nxt = all[i + 1];
  if (!nxt) return;
  cur.open = false;
  nxt.open = true;
  nxt.scrollIntoView({ block: "start" });
  $("summary", nxt).focus({ preventScroll: true });
}

/* The small model does not know most short forms (IOC, SIEM, KYC) and matches the rest of the sentence instead. */
function shortFormHint(s) {
  const forms = s.short_forms || [];
  if (!forms.length) return "";
  const quoted = forms.map(f => `“${esc(f)}”`);
  const list = quoted.length === 1 ? quoted[0] : `${quoted.slice(0, -1).join(", ")} and ${quoted[quoted.length - 1]}`;
  return `<p class="short-hint">${icon("info", "sm")}<span>${list} may not be understood. Write ${forms.length === 1 ? "it" : "them"} out in plain words for better suggestions.
    <button type="button" class="link-btn" data-edit-sentence="${s.index}">Edit this answer</button></span></p>`;
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
  const previous = new Set($$("#mine [data-remove]").map(b => b.dataset.remove));
  const smap = suggestionMap();
  const items = [...state.ticked.keys()].map(id => ({ id, name: smap.get(id)?.name || id, note: "suggested, ticked by you", icon: "check" }))
    .concat([...state.manual].map(([id, m]) => ({ id, name: m.name, note: `added by you, ${FIELD_LABEL[m.lane].toLowerCase()}`, icon: "pencil-line" })));
  $("#mine").innerHTML = items.map(it => `<li class="${previous.has(it.id) ? "" : "is-new"}">${icon(it.icon, "sm")}<span class="mine-text">${esc(it.name)}<span class="mine-note">${esc(it.note)}</span></span>
    <button type="button" class="rm" data-remove="${esc(it.id)}" aria-label="Remove ${esc(it.name)}">${icon("x", "sm")}</button></li>`).join("");
  const c = laneCounts();
  // Job-title activities count too, so the rail shows them as one row rather than a total that does not add up.
  if (c.title) {
    $("#mine").insertAdjacentHTML("beforeend", `<li class="title-row">${icon("briefcase-business", "sm")}<span class="mine-text">${c.title} from your job title<span class="mine-note">${esc(state.title.title)}, as O*NET lists it. <button type="button" class="link-btn" data-open-title>Review them</button></span></span></li>`);
  }
  $("#mine-empty").hidden = items.length > 0;
  $("#count").textContent = c.all;
  $("#count-m").textContent = c.all;
  $("#count-m-word").textContent = c.all === 1 ? "activity" : "activities";
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
  updateSentStatus();
  saveProgress();
}

function reviewEvents() {
  const root = $("#review");
  root.addEventListener("toggle", e => {
    const d = e.target;
    if (!d.matches?.("details.sent[data-sentence]") || !state.openSent) return;
    if (d.open) state.openSent.add(Number(d.dataset.sentence)); else state.openSent.delete(Number(d.dataset.sentence));
  }, true);
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
    if (b.dataset.next !== undefined) {
      nextSentence(Number(b.dataset.next));
    } else if (b.dataset.dismiss !== undefined) {
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
    } else if (b.dataset.editSentence !== undefined) {
      // Back to the question this sentence came from, with the sentence selected in the text box.
      const s = state.suggest.sentences.find(x => x.index === Number(b.dataset.editSentence));
      const [page, box] = FIELD_PAGE[s.field];
      navigate("work", page);
      const ta = $(box);
      ta.focus();
      ta.setSelectionRange(s.start, s.end);
    } else if (b.dataset.openTitle !== undefined) {
      const d = $(".title-acts");
      d.open = true;
      d.querySelector("summary").focus({ preventScroll: true });
      d.scrollIntoView({ block: "start" });
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
      showStage("work", pageOf(first));
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
function bandClass(jo) {
  const b = bandShort(jo);
  return /^(Much faster|Faster)/.test(b) ? "grow" : /^Decline/.test(b) ? "limit" : "";
}

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
      <span class="rr-outlook"><span class="tag ${bandClass(r.job_outlook) || "good"}">US outlook: ${esc(bandShort(r.job_outlook))}</span>${r.job_outlook.bright.length ? ` <span class="tag sdg">Bright Outlook</span>` : ""}${srcMarks(r.job_outlook.src)}</span>
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
    + `<li id="src-your-input"><b>you. Your input</b>: hours, budget and the weights you set.</li>`
    + `<li id="src-explore-defaults"><b>default. Starting assumptions</b>: before you describe your work, first blocks use 6 hours a week and a RM0 budget, the questions' starting values.</li>`;
}

/* Sources open in a sheet over the current screen, so checking a figure never loses your place.
   A section's button shows only that section's sources; "Show all" and the settings entry show every
   source with the licence notes. A single cited source is highlighted and focused. Closing returns focus. */
let sourcesOpener = null;
function openSources(id, opener, only = null) {
  const sheet = $("#sources-sheet");
  sourcesOpener = opener;
  const keep = only ? new Set(only.map(x => `src-${x}`)) : null;
  $$("#sources li").forEach(li => { li.hidden = Boolean(keep && !keep.has(li.id)); });
  sheet.classList.toggle("filtered", Boolean(keep));
  $("#h-sources").textContent = keep ? `Sources for this section (${keep.size})` : "Sources and licences";
  $$("#sources li.hit").forEach(li => li.classList.remove("hit"));
  if (!sheet.open) sheet.showModal();
  const li = id ? document.getElementById(id) : null;
  if (li) {
    li.classList.add("hit");
    li.tabIndex = -1;
    li.scrollIntoView({ block: "center" });
    li.focus({ preventScroll: true });
  } else {
    $(".sheet-body", sheet).scrollTop = 0;
  }
}

async function start() {
  initTheme();
  restoreProgress();
  history.replaceState({ screen: "welcome", page: 1 }, "");
  show("welcome", 1, false);
  // Only the app's own entries carry a state; an in-page anchor's entry has none and must not move screens.
  window.addEventListener("popstate", e => { if (e.state) { if (canShow(e.state.screen)) show(e.state.screen, e.state.page || 1); else show("welcome"); } });
  $("#sources-done").addEventListener("click", () => $("#sources-sheet").close());
  $("#sources-all").addEventListener("click", () => openSources(null, sourcesOpener));
  $("#sources-sheet").addEventListener("close", () => { sourcesOpener?.focus({ preventScroll: true }); sourcesOpener = null; });
  // A click on the backdrop (the dialog element itself, outside its content) closes either sheet.
  $$("dialog.sheet").forEach(d => d.addEventListener("click", e => { if (e.target === d) d.close(); }));
  $("#back").addEventListener("click", () => history.back());
  document.addEventListener("click", async e => {
    if (e.target.closest(".settings-open")) { $("#settings").showModal(); return; }
    const cite = e.target.closest('a[href^="#src-"]');
    if (cite) { e.preventDefault(); openSources(cite.getAttribute("href").slice(1), cite); return; }
    const section = e.target.closest(".src-btn");
    if (section) { openSources(null, section, section.dataset.srcIds.split(" ")); return; }
    const all = e.target.closest(".sources-open");
    if (all) {
      // From inside Display settings, focus returns to the visible gear button once sources close.
      const settings = all.closest("dialog");
      if (settings) settings.close();
      openSources(null, settings ? $$(".settings-open").find(b => b.offsetParent) : all);
      return;
    }
    if (e.target.closest(".skip")) {
      e.preventDefault();
      const heading = current.screen === "work" ? $(`.q-page[data-page="${current.page}"] [tabindex='-1']`)
        : $("[tabindex='-1']", document.getElementById(current.screen));
      heading?.focus();
      return;
    }
    const go = e.target.closest("[data-go]")?.dataset.go;
    if (go === "home") navigate("welcome");
    else if (go === "continue") continueProgress();
    else if (go === "edit") navigate("work", 1);
    else if (go === "start") openApp();
    else if (go === "describe") { await setMode("own"); navigate("work", 1); }
    else if (go === "example") { await setMode("example"); navigate("work", 1); }
    else if (go === "own") { await setMode("own"); $("#h-work").focus(); }
  });
  $("#intake").addEventListener("submit", readWork);
  $("#read-now").addEventListener("click", () => { readNow = true; $("#intake").requestSubmit(); });
  combo($("#f-title"), $("#f-title-list"), async q => (await api(`/api/occupations?q=${encodeURIComponent(q)}`)).map(o => ({ ...o, label: o.title, meta: o.code })), it => setTitle(it.code));
  combo($("#f-field"), $("#f-field-list"), async q => (await api(`/api/fields?q=${encodeURIComponent(q)}`)).map(o => ({ ...o, label: o.title, meta: `CIP ${o.cip}` })), it => setCip({ cip: it.cip, title: it.title }));
  reviewEvents();
  resultsEvents();
  try {
    const [status, personas] = await Promise.all([api("/api/status"), api("/api/personas")]);
    state.status = status; state.personas = personas;
    renderSources();
    if (status.ai_suggestions !== "available") {
      serviceWarning = "AI suggestions are unavailable. You can still search for activities and see roles.";
      setStatus("");
    }
    $("#personas").insertAdjacentHTML("beforeend", personas.map(p => `<button type="button" class="chip-btn" data-persona="${esc(p.id)}" aria-pressed="${p.id === examplePersona}"><b>${esc(p.name)}, ${esc(p.age)}</b><span>${esc(capitalise(p.degree_level))} in ${esc(p.cip_title || p.degree_field)}</span></button>`).join(""));
    $("#personas").addEventListener("click", e => {
      const b = e.target.closest("[data-persona]");
      if (b) loadPersona(state.personas.find(p => p.id === b.dataset.persona));
    });
  } catch (e) {
    serviceWarning = "The service is starting or unavailable. Reload in a moment.";
    setStatus("");
  }
}

/* ---------- adopted revision: compare routes, your plan, what changed (SPEC section 10) ---------- */

function routesRequest(extra = {}) {
  const base = resultsBody();
  return { ...base, options: { ...routeState.opts, completed: [...routeState.completed], ...(routeState.target ? { target: routeState.target } : {}), ...extra } };
}

function readRouteOpts() {
  const f = $("#intake");
  routeState.opts.weeks = Number(f.elements.weeks.value);
  routeState.opts.willing_long = f.elements.willing_long.value === "yes";
}

/* Before the person's work is confirmed, the tabs show every supported role (explore); afterwards, their routes. */
const isPersonal = () => routeState.data?.personal !== false;
function exploreRequest() {
  return { options: { ...routeState.opts, completed: [...routeState.completed], ...(routeState.target ? { target: routeState.target } : {}) } };
}
const loadRoutes = personal => (personal ? api("/api/routes", routesRequest()) : api("/api/explore", exploreRequest()));

async function openApp() {
  if (!routeState.data) {
    try { routeState.data = await loadRoutes(false); } catch (err) { navigate("work", 1); return; }
  }
  navigate(routeState.target && isPersonal() ? "plan" : "routes");
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
      showStage("work", pageOf(first));
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

/* Compare routes is a short list: each route is one card that opens its detail screen, so the person
   scans first and reads only the route they care about. The order changes only on request. */
const FAMILY_ICON = { it_support: "server", software: "code", finance_analytics: "chart-column" };
/* Each supported role: its icon, and the words its Jobs tab searches for (AIVANCIN's choice, closer to
   Malaysian job ads than the American O*NET title). */
const ROLE_META = {
  "15-1232.00": ["headset", "IT support"], "15-1231.00": ["network", "network support"], "15-1244.00": ["server-cog", "system administrator"],
  "15-1212.00": ["shield-check", "cyber security analyst"], "15-1252.00": ["square-code", "software developer"], "15-1253.00": ["bug", "software tester"],
  "15-1254.00": ["globe", "web developer"], "15-1251.00": ["square-terminal", "programmer"], "13-2011.00": ["calculator", "accountant"],
  "13-2031.00": ["piggy-bank", "budget analyst"], "13-2041.00": ["credit-card", "credit analyst"], "15-2051.01": ["chart-line", "business intelligence analyst"],
};
const SORTS = {
  overlap: ["Most overlap", "Ordered by activity overlap, as compared."],
  outlook: ["Job outlook", "Ordered by US projected growth, then activity overlap."],
  entry: ["Easier entry", "Ordered by the requirements you are missing, then Job Zone, then activity overlap."],
};
const EXPLORE_SORTS = {
  outlook: ["Job outlook", "Ordered by US projected growth."],
  entry: ["Easier entry", "Ordered by O*NET Job Zone, the preparation a role usually needs."],
};
const sortKey = () => (!isPersonal() && routeState.sort === "overlap" ? "outlook" : routeState.sort);
const missingReqs = r => r.requirements.filter(q => q.status === "missing").length;

function sortedRoutes(routes) {
  const list = [...routes];
  const byOverlap = (a, b) => b.overlap.v - a.overlap.v || (a.code < b.code ? -1 : 1);
  const key = sortKey();
  if (key === "outlook") list.sort((a, b) => b.outlook_score - a.outlook_score || byOverlap(a, b));
  else if (key === "entry") list.sort((a, b) => missingReqs(a) - missingReqs(b) || a.job_zone.v - b.job_zone.v || byOverlap(a, b));
  else list.sort(byOverlap);
  return list;
}

function familyTile(r, large) { return `<span class="fam-tile fam-${esc(r.family)}${large ? " lg" : ""}" aria-hidden="true">${icon(ROLE_META[r.code]?.[0] || FAMILY_ICON[r.family] || "route")}</span>`; }

/* Overlap once work is confirmed; before that, how many activities the role records. */
function workChip(r) {
  return isPersonal() ? `<span class="chip you">You do ${fig(r.done_count, "int")} of ${fig(r.total_count, "int")} activities</span>`
    : `<span class="chip">${fig(r.total_count, "int")} recorded activities</span>`;
}

/* A bookmark keeps a route in the Saved tab. It sits beside the card's button, never inside it. */
function saveBtn(r) {
  return `<button type="button" class="icon-btn save-btn" data-save="${esc(r.code)}" aria-pressed="${routeState.saved.has(r.code)}" aria-label="Save ${esc(r.title)}">${icon("bookmark")}</button>`;
}

function entryChip(r) {
  if (!isPersonal()) return "";
  const n = missingReqs(r);
  if (n) return `<span class="chip limit">${n} requirement${n === 1 ? "" : "s"} missing</span>`;
  const decide = r.requirements.filter(q => q.status === "you decide").length;
  return `<span class="chip">${decide ? `${decide} for you to decide` : "Requirements met"}</span>`;
}

function routeRow(r) {
  const chosen = routeState.target === r.code;
  return `<li class="route-item"><button type="button" class="route-row${chosen ? " chosen" : ""}" data-open-route="${esc(r.code)}">
    ${familyTile(r)}
    <span class="rr-body"><span class="rr-title">${esc(r.title)}</span>
      ${chosen ? `<span class="rr-chosen">${icon("check", "sm")}Your route</span>` : ""}
      <span class="rr-desc">${esc(r.description.first)}${srcMarks(r.description.src)}</span>
      <span class="chips">${workChip(r)}
        <span class="chip ${bandClass(r.job_outlook)}">US outlook: ${esc(bandShort(r.job_outlook))}${srcMarks(r.job_outlook.src)}</span>${entryChip(r)}</span></span>
    </button>${saveBtn(r)}</li>`;
}

function describeCard(where = "routes") {
  const review = Boolean(state.suggest);
  return `<section class="describe-card" aria-labelledby="h-describe-${where}"><span class="fam-tile" aria-hidden="true">${icon("pencil-line")}</span>
    <div><h2 id="h-describe-${where}">${review ? "Finish checking your activities" : "See which roles fit you"}</h2>
      <p>${review ? "Confirm what you do, then compare the routes that use your work." : "Describe the work you do in a few sentences. You confirm every match before it counts."}</p>
      <div class="describe-actions">${review ? `<button type="button" class="btn btn-primary" data-tab-go="review">Review your activities</button>`
        : `<button type="button" class="btn btn-primary" data-go="describe">Describe your work</button><button type="button" class="link-btn" data-go="example">Try an example</button>`}</div></div></section>`;
}

function renderRoutes() {
  const d = routeState.data;
  const personal = isPersonal();
  const sort = sortKey();
  const sorts = personal ? SORTS : EXPLORE_SORTS;
  $("#routes .eyebrow").textContent = personal ? "Compare routes" : "Explore routes";
  $("#h-routes").textContent = personal ? "Routes that use your work" : "Roles you can grow into";
  $("#routes-lead").innerHTML = personal
    ? `<p>Of ${fig(d.supported_destinations, "int")} supported roles, the ones where your confirmed work already counts. Open one to see the work, how AI is used in it, and your first learning block.</p>`
    : `<p>${fig(d.supported_destinations, "int")} supported roles in IT and network support, software, and finance and analytics, each with how AI is used in it and a free first learning block.</p>`;
  const shared = d.shared;
  const chosenRoute = d.routes.find(r => r.code === routeState.target);
  $("#routes-body").innerHTML = `
    ${personal ? "" : describeCard()}
    ${d.fewer_note ? `<div class="callout">${icon("info")}<p>${esc(d.fewer_note)}</p></div>` : ""}
    ${d.routes.length ? `
      ${d.routes.length > 1 ? `<fieldset class="sort-ctl"><legend class="vh">Order routes by</legend>
        <div class="seg-ctl sort-seg${personal ? "" : " two"}" style="--n:${Object.keys(sorts).length}">${Object.entries(sorts).map(([k, [label]]) => `<label><input type="radio" name="rsort" value="${k}" ${sort === k ? "checked" : ""}>${label}</label>`).join("")}</div>
        <p class="foot">${sorts[sort][1]} No combined score: you choose.</p></fieldset>` : ""}
      <ul class="route-list" aria-label="Routes" data-scope>${sortedRoutes(d.routes).map(routeRow).join("")}<li class="list-srcs srcs-slot"></li></ul>` :
      `<div class="callout">${icon("info")}<p>None of the supported routes counts at least two of your confirmed activities yet. Explore all roles, or tick more activities.</p></div>`}
    ${shared ? `<section class="soft-card" data-scope aria-labelledby="h-shared"><h2 id="h-shared">${icon("layers", "sm")}Shared foundations</h2>
      <p>${shared.common_steps.length ? `${shared.common_steps.length} step${shared.common_steps.length === 1 ? " appears" : "s appear"} (${fig(shared.common_hours, "")} hours) in every route's first block, so ${shared.common_steps.length === 1 ? "it keeps" : "they keep"} these options open.` : "No step is common to every route's first block."}</p>
      ${shared.common_steps.length ? `<ul>${shared.extra_hours.map(e => `<li>${esc(e.title)}: ${fig(e.hours, "")} hours beyond the shared steps</li>`).join("")}</ul>` : ""}
      <p class="foot">These describe the compared plans, not a probability of anything.</p></section>` : ""}
    ${personal ? `<p class="explore-row"><button type="button" class="link-btn" data-action="rank">Explore all roles, including ones without complete-route support</button></p>` : ""}
    ${chosenRoute ? `<div class="stage-cta"><span class="cta-label"><span class="cta-kicker">Your route</span>${esc(chosenRoute.title)}</span><button type="button" class="btn btn-primary choose-route" data-route="${esc(chosenRoute.code)}">See plan</button></div>` : ""}`;
  attachSources($("#routes"));
  if (current.screen === "route") renderRoute();
}

/* ---------- one route: overview, AI use, first block ---------- */
const AI_GROUPS = [
  ["AI does most of it directly", "sparkles", "AI often does the work itself", "When people use Claude for these, it mostly does the task for them."],
  ["shared", "layers", "Shared between people and AI", "Claude does part of the work; people direct and finish it."],
  ["people mostly work with AI", "user", "People mostly do the work", "People do these and use Claude to help, rather than handing them over."],
  ["no evidence in the bundled releases", "circle-dashed", "No usage evidence", "No bundled release covers these activities."],
];

function ring(done, total) {
  const share = total ? done / total : 0, c = 2 * Math.PI * 26;
  return `<svg class="ring" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="26" class="ring-track"/>
    <circle cx="32" cy="32" r="26" class="ring-fill" stroke-dasharray="${(share * c).toFixed(2)} ${c.toFixed(2)}" transform="rotate(-90 32 32)"/></svg>`;
}

function overviewPanel(r) {
  const served = new Set(r.block.gaps_served);
  // The header already shows the description's first sentence; the rest continues here.
  const more = r.description.text.slice(r.description.first.length).trim();
  const work = isPersonal() ? workSections(r, served) : `<section class="soft-card" data-scope aria-labelledby="h-rd-acts"><h2 id="h-rd-acts">Main activities</h2>
      <p class="foot">The ${r.prioritized.length} largest of the ${fig(r.total_count, "int")} activities O*NET records for this role.</p>
      <ul class="gaps">${r.prioritized.map(g => `<li><b class="${served.has(g.dwa_id) ? "in-block" : ""}"></b><span>${esc(g.name)}${served.has(g.dwa_id) ? `<span class="gap-note">In the first block</span>` : ""}</span></li>`).join("")}</ul>
      <button type="button" class="btn btn-gray" data-go="describe">${icon("pencil-line", "sm")}Describe your work</button>
      <p class="foot">See how many of these you already do.</p></section>`;
  return `
    ${more ? `<section class="soft-card" data-scope aria-labelledby="h-rd-do"><h2 id="h-rd-do">More about the work</h2>
      <p>${esc(more)}${srcMarks(r.description.src)}</p></section>` : ""}
    ${work}
    <section class="soft-card" data-scope aria-labelledby="h-rd-req"><h2 id="h-rd-req">Requirements</h2><ul class="reqs">${r.requirements.map(reqRow).join("")}</ul></section>
    <section class="soft-card" data-scope aria-labelledby="h-rd-opp"><h2 id="h-rd-opp">Opportunity</h2>
      <p><span class="tag ${bandClass(r.job_outlook) || "good"}">US outlook: ${esc(bandShort(r.job_outlook))}</span>${r.job_outlook.bright.length ? ` <span class="tag sdg">Bright Outlook</span>` : ""}${srcMarks(r.job_outlook.src)}</p>
      <p class="foot">${esc(r.local_evidence)}</p>
      ${r.pay ? `<p class="foot">DOSM 2025 median pay step from your current group: ${fig(r.pay.step, "step")}. Group medians, not a salary offer.</p>` : ""}
      <p class="foot"><b>SDG 8.5:</b> ${esc(r.sdg[0].reason)}${srcMarks(r.sdg[0].src)}</p></section>`;
}

function workSections(r, served) {
  return `
    <section class="soft-card" data-scope aria-labelledby="h-rd-work"><h2 id="h-rd-work">Your work in this role</h2>
      <div class="ring-row"><span class="ring-box">${ring(r.done_count.v, r.total_count.v)}<span class="ring-num" aria-hidden="true">${esc(r.done_count.v)}/${esc(r.total_count.v)}</span></span>
        <p>You already do ${fig(r.done_count, "int")} of its ${fig(r.total_count, "int")} recorded activities (activity overlap ${fig(r.overlap, "pct")}). Overlap is not how much of the job you can already do.</p></div>
      <ul class="did">${groupDone(r.done)}</ul>
      ${r.related.length ? `<p class="foot">Also counted toward this route: ${r.related.map(x => `“${esc(x.name.replace(/\.$/, ""))}”, which O*NET groups with its “${esc(x.role_name.replace(/\.$/, ""))}”`).join("; ")}. These do not change the overlap or the gaps.</p>` : ""}</section>
    <section class="soft-card" data-scope aria-labelledby="h-rd-gaps"><h2 id="h-rd-gaps">Still to build</h2>
      <p class="foot">The role's ${r.prioritized.length} largest activities you have not confirmed, most frequent first.</p>
      <ul class="gaps">${r.prioritized.map(g => {
        const note = served.has(g.dwa_id) ? "In your first block" : g.has_resource ? "" : "No verified resource yet";
        return `<li><b class="${served.has(g.dwa_id) ? "in-block" : ""}"></b><span>${esc(g.name)}${note ? `<span class="gap-note">${note}</span>` : ""}</span></li>`;
      }).join("")}</ul></section>`;
}

/* Before work is described, first blocks rest on the questions' starting values; say so where the figures are. */
const ASSUMPTION_NOTE = "Built on starting assumptions: 6 hours a week and no budget. Describe your work to fit it to you.";

function aiPanel(r) {
  const d = routeState.data;
  const ai = r.ai_use;
  const direct = ai.filter(a => a.class === AI_GROUPS[0][0]).length;
  const groups = AI_GROUPS.map(([key, ico, title, sub]) => {
    const items = ai.filter(a => a.class === key);
    if (!items.length) return "";
    return `<section class="ai-group ai-${ico}" data-scope aria-labelledby="h-ai-${ico}"><h2 id="h-ai-${ico}">${icon(ico, "sm")}${title}</h2><p class="foot">${sub}</p>
      <ul>${items.map(a => `<li><span class="ai-name">${esc(a.name)}${a.done ? ` <span class="tag you">You do this</span>` : ""}${a.weak ? ` <span class="tag weak">Evidence weak</span>` : ""}</span>${a.now ? `<span class="ai-val">${fig(a.now, "pct")}</span>` : ""}</li>`).join("")}</ul></section>`;
  }).join("");
  return `
    <p class="rd-lead">How much of each of this role's ${ai.length} largest activities AI does directly when people use Claude for it, across bundled releases up to ${esc(releaseLabel(d.release))}.</p>
    ${direct ? `<div class="callout ai-take">${icon("info")}<p>AI does most of the work directly on ${direct} of these ${ai.length} activities in observed use. In your first block, a checkpoint on such an activity asks you to do it yourself, then check an AI's output against yours.</p></div>` : ""}
    ${groups}
    <p class="foot rd-note">Global Claude usage, not employment, risk or a trend. Grouped at the plan's threshold: ${fig(d.threshold, "pct")} and above, AI does most of it directly; below ${fig(d.forecast.lower_cut, "")}%, people mostly work with AI.</p>`;
}

function blockPanel(r) {
  const b = r.block, d = routeState.data;
  const served = r.prioritized.filter(g => g.served).length;
  return `
    <section class="soft-card" data-scope aria-labelledby="h-rd-block"><h2 id="h-rd-block">First ${fig(d.weeks, "int")}-week block</h2>
      <div class="stat-row"><p><b>${fig(b.effort_hours, "")}</b><span>of ${fig(b.capacity_hours, "")} hours</span></p><p><b>${fig(b.cost_rm, "rm")}</b><span>of ${fig(d.budget_rm, "rm")} budget</span></p><p><b>${served}/${r.prioritized.length}</b><span>priority gaps served</span></p></div>
      <p class="foot">${esc(capitalise(b.status))}. Verified resources exist for ${fig(r.supported_share, "pct")} of the priority gaps. A first block is a start, not a complete transition.</p>
      ${isPersonal() ? "" : `<p class="foot">${ASSUMPTION_NOTE}</p>`}
      <ol class="mini-steps">${b.steps.map(s => `<li><span>${esc(s.title)}</span><span class="foot">${esc(s.provider)} · ${fig({ v: Math.round((s.provider_hours.v + s.practice_hours.v) * 10) / 10, src: s.provider_hours.src }, "")} h</span></li>`).join("")}</ol></section>`;
}

/* AIVANCIN does not list jobs; this tab opens a job-ad search on another site for the role. */
const JOB_SITES = [
  ["JobStreet Malaysia", t => `https://my.jobstreet.com/${t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}-jobs`],
  ["LinkedIn Jobs, Malaysia", t => `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(t)}&location=Malaysia`],
];

function jobsPanel(r) {
  const term = ROLE_META[r.code]?.[1] || r.title;
  return `<section class="soft-card" aria-labelledby="h-rd-jobs"><h2 id="h-rd-jobs">Search job ads</h2>
    <p>AIVANCIN does not list or check jobs. These open a search for “${esc(term)}” on another site.</p>
    <ul class="res-list">${JOB_SITES.map(([name, url]) => `<li><a class="res-row" href="${esc(url(term))}" target="_blank" rel="noopener"><span class="res-tile" aria-hidden="true">${icon("search")}</span>
      <span class="res-text"><b>${esc(name)}</b><span>Jobs matching “${esc(term)}”</span></span>${icon("external-link", "sm")}<span class="vh">(opens in a new tab)</span></a></li>`).join("")}</ul>
    <p class="foot">${esc(r.title)} is the American O*NET title. Ads in Malaysia often use other titles, so try related words too.</p></section>`;
}

function renderRoute() {
  const d = routeState.data;
  const r = d && d.routes.find(x => x.code === routeState.open);
  const box = $("#route-body");
  if (!r) { box.innerHTML = ""; return; }
  const chosen = routeState.target === r.code;
  const tab = routeState.tab;
  const panel = tab === "ai" ? aiPanel(r) : tab === "block" ? blockPanel(r) : tab === "jobs" ? jobsPanel(r) : overviewPanel(r);
  box.innerHTML = `
    <header class="rd-head" data-scope>${familyTile(r, true)}
      <div><p class="d-kicker">${esc(r.family_label)}</p><h1 id="h-route" class="rd-title" tabindex="-1">${esc(r.title)}</h1></div>${saveBtn(r)}
      <p class="rd-desc">${esc(r.description.first)}${srcMarks(r.description.src)}</p>
      <p class="rr-meta">O*NET ${esc(r.code)} · Job Zone ${fig(r.job_zone, "")} · ${esc(r.masco.label || "")}</p>
      <div class="chips">${workChip(r)}<span class="chip ${bandClass(r.job_outlook)}">US outlook: ${esc(bandShort(r.job_outlook))}${srcMarks(r.job_outlook.src)}</span>${entryChip(r)}</div>
    </header>
    <fieldset class="rd-tabs"><legend class="vh">Show</legend>
      <div class="seg-ctl tab-seg" style="--n:4">${[["overview", "Overview"], ["ai", "AI use"], ["block", "First block"], ["jobs", "Jobs"]].map(([k, t]) => `<label><input type="radio" name="rtab" value="${k}" ${tab === k ? "checked" : ""}>${t}</label>`).join("")}</div></fieldset>
    <div class="rd-panel" id="rd-panel">${panel}</div>
    <div class="stage-cta"><span class="cta-label"><span class="cta-kicker">First block</span>${fig(r.block.effort_hours, "")} h · ${fig(r.block.cost_rm, "rm")}</span>
      <button type="button" class="btn btn-primary choose-route" data-route="${esc(r.code)}">${chosen ? "See your plan" : "Choose this route"}</button></div>`;
  attachSources(box);
}

function openRoute(code) {
  routeState.open = code;
  routeState.tab = "overview";
  renderRoute();
  navigate("route");
}

async function choose(code) {
  routeState.target = code;
  await refreshPlan();
  showStage("plan");
}

async function refreshPlan() {
  const active = document.activeElement;
  const focusDone = active?.dataset.done;
  const focusScenario = active?.name === "pscen" ? active.value : null;
  try {
    routeState.data = await loadRoutes(isPersonal());
    const keepFocus = document.activeElement === active;
    renderRoutes();
    renderPlan();
    // A tab opened while the update was on its way shows the new figures too.
    ({ learn: renderLearn, saved: renderSaved, profile: renderProfile })[current.screen]?.();
    saveProgress();
    const restore = focusDone ? $$("#plan input[data-done]").find(input => input.dataset.done === focusDone)
      : focusScenario ? $$("#plan input[name='pscen']").find(input => input.value === focusScenario) : null;
    if (keepFocus) restore?.focus({ preventScroll: true });
  } catch (err) {
    $("#plan-body").innerHTML = `<div class="callout">${icon("triangle-alert")}<p>${esc(Object.values(err.data?.errors || {})[0] || "Could not build the plan.")}</p></div>`;
  }
}

/* A plan step reads like a timeline entry: what it builds, the resource that teaches it, and its hours.
   The checkpoint and the evidence behind its form open on request; their open state survives updates. */
function stepItem(s, i) {
  const ai = s.form === "ai";
  const d = routeState.data;
  const why = s.reason.filter(r => r.projected[d.scenario].v >= d.threshold.v);
  const heading = s.kind === "prerequisite" ? "Required first by a later step" : s.kind === "ai_practice" ? "AI practice on an activity in this plan"
    : s.serves.map(g => g.name.replace(/\.$/, "")).join("; ");
  const because = ai && why.length
    ? `<p class="foot">Because ${why.map(r => `AI does “${esc(r.name.replace(/\.$/, ""))}” directly in ${fig(r.now, "pct")} of observed Claude conversations now, ${fig(r.projected[d.scenario], "pct")} projected in the ${esc(d.scenario)} scenario`).join("; ")}; threshold ${fig(d.threshold, "pct")}.</p>`
    : "";
  const hours = { v: Math.round((s.provider_hours.v + s.practice_hours.v) * 10) / 10, src: s.provider_hours.src };
  const price = s.cost_rm.v ? fig(s.cost_rm, "rm") : `${s.access || "Free"}${srcMarks(s.cost_rm.src)}`;
  return `<li class="pstep${s.completed ? " done" : ""}" data-scope>
    <span class="pnum" aria-hidden="true">${s.completed ? icon("check", "sm") : i + 1}</span>
    <div class="pstep-top"><h3 class="gap-name">${esc(heading)}</h3><span class="p-hours">${fig(hours, "")} h</span></div>
    <a class="res-row" href="${esc(s.url)}" target="_blank" rel="noopener"><span class="res-tile" aria-hidden="true">${icon("book-open")}</span>
      <span class="res-text"><b>${esc(s.title)}</b><span>${esc(s.provider)} · ${price}</span></span>${icon("external-link", "sm")}<span class="vh">(opens in a new tab)</span></a>
    <details class="checkpoint${ai ? " ai" : ""}" data-cp="${esc(s.id)}" ${routeState.openCp.has(s.id) ? "open" : ""}>
      <summary><span><b>Checkpoint</b>${ai ? `<span class="tag ai-tag">with AI checking</span>` : ""}</span>${icon("chevron-down", "sm")}</summary>
      <p>${esc(s.checkpoint)}</p>${because}
      ${s.objective ? `<p class="foot">Provider's objective: “${esc(s.objective)}”</p>` : ""}
      <p class="foot">${fig(s.provider_hours, "")} h course + ${fig(s.practice_hours, "")} h practice (estimate) · ${fig(s.cost_rm, "rm")} · link checked ${esc(s.checked_on)} · SDG 4.4</p></details>
    <label class="check-line"><input type="checkbox" data-done="${esc(s.id)}" ${s.completed ? "checked" : ""}> I have done this step</label>
  </li>`;
}

function renderPlan() {
  const d = routeState.data;
  const r = d.routes.find(x => x.code === routeState.target);
  if (!r) { $("#plan-body").innerHTML = emptyState("list-checks", "No route chosen yet", "Open a route and choose it to see its first learning block here.", "routes", "Compare routes"); return; }
  const b = r.block;
  const next = b.steps.find(s => !s.completed);
  const done = b.steps.filter(s => s.completed).length;
  $("#plan-body").innerHTML = `
    <div class="plan-head" data-scope>
      <div class="ph-top">${familyTile(r)}<div><p class="d-kicker">${esc(r.family_label)}</p><h2 class="plan-title">${esc(r.title)}</h2></div></div>
      <div class="stat-row"><p><b>${fig(b.effort_hours, "")}</b><span>of ${fig(b.capacity_hours, "")} hours</span></p><p><b>${fig(d.weeks, "int")}</b><span>weeks</span></p><p><b>${fig(b.cost_rm, "rm")}</b><span>of ${fig(d.budget_rm, "rm")} budget</span></p></div>
      <p class="foot">${fig(b.provider_hours, "")} h of courses and ${fig(b.practice_hours, "")} h of practice (estimated). ${esc(capitalise(b.status))}: a start, not a complete transition; it does not make anyone ready for the role.</p>
      ${isPersonal() ? "" : `<p class="foot">${ASSUMPTION_NOTE}</p>`}
      ${next ? `<p class="next">${icon("route", "sm")}<span><b>Start with</b> ${esc(next.title)}</span></p>` : `<p class="next">${icon("check", "sm")}<span>Every step in this block is marked done.</span></p>`}
    </div>
    <details class="plan-scen" data-scope ${routeState.scenOpen ? "open" : ""}>
      <summary><span>Future scenario <span class="foot">${esc(capitalise(d.scenario))}${b.scenario_differs.length ? `, changes ${b.scenario_differs.length} checkpoint${b.scenario_differs.length === 1 ? "" : "s"}` : ""}</span></span>${icon("chevron-down", "sm")}</summary>
      <fieldset><legend class="vh">Future scenario for the checkpoints</legend>
      <div class="seg-ctl small scen-ctl" style="--n:3">${["slower", "moderate", "faster"].map(k => `<label><input type="radio" name="pscen" value="${k}" ${d.scenario === k ? "checked" : ""}>${k === "moderate" ? "Moderate" : capitalise(k)}</label>`).join("")}</div></fieldset>
      <p class="foot">Looking ${fig(b.horizon.months, "")} months ahead (${esc(b.horizon.basis)}). A step's checkpoint adds AI checking when AI does its activity directly in at least ${fig(d.threshold, "pct")} of observed Claude conversations by then. ${b.scenario_differs.length ? `Steps that change between scenarios: ${esc(b.scenario_differs.join("; "))}.` : "No step changes between scenarios."} Slower and faster are assumptions, not forecasts.</p></details>
    <h2 class="vh">Steps</h2>
    <ol class="psteps">${b.steps.map(stepItem).join("")}</ol>
    ${b.uncovered.length ? `<section class="soft-card" data-scope aria-labelledby="h-uncovered"><h2 id="h-uncovered">Priority gaps without a step in this block</h2><ul class="gaps">${b.uncovered.map(g => `<li><b></b><span>${esc(g.name)}</span><span class="w">SDG 4.4</span></li>`).join("")}</ul>
      <p class="foot">Either no verified resource teaches them yet, or the block's time or budget ran out.</p></section>` : ""}
    <section class="soft-card" data-scope aria-labelledby="h-beyond"><h2 id="h-beyond">Beyond this block</h2><ul class="reqs">${r.requirements.map(reqRow).join("")}</ul></section>
    <div class="stage-cta"><span class="cta-label"><b class="num">${done}</b> of ${b.steps.length} steps done</span>${isPersonal()
      ? `<button type="button" class="btn btn-primary" data-action="changed">What changed</button>`
      : `<button type="button" class="btn btn-primary" data-go="describe">Describe your work</button>`}</div>`;
  attachSources($("#plan"));
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
    if (b.dataset.openRoute) openRoute(b.dataset.openRoute);
    else if (b.dataset.save) toggleSaved(b.dataset.save);
    else if (b.dataset.unsave) { routeState.saved.delete(b.dataset.unsave); saveProgress(); renderSaved(); $("#h-saved").focus(); }
    else if (b.dataset.tabGo) navigate(b.dataset.tabGo);
    else if (b.dataset.action === "routes") compareRoutes();
    else if (b.classList.contains("choose-route")) choose(b.dataset.route);
    else if (b.dataset.action === "changed") showChanged();
    else if (b.dataset.action === "rank" && !b.closest("#review")) rank();
  });
  $("#routes").addEventListener("change", e => {
    if (!e.target.matches('input[name="rsort"]')) return;
    routeState.sort = e.target.value;
    renderRoutes();
    $(`#routes input[name="rsort"][value="${routeState.sort}"]`)?.focus();
  });
  // Switching tabs redraws the route and returns focus to the tab control.
  $("#route").addEventListener("change", e => {
    if (!e.target.matches('input[name="rtab"]')) return;
    routeState.tab = e.target.value;
    if (routeState.tab === "ai") { routeState.seen.add("ai"); saveProgress(); }
    renderRoute();
    $(`#route input[name="rtab"][value="${routeState.tab}"]`)?.focus();
  });
  $("#plan").addEventListener("toggle", e => {
    const d = e.target;
    if (d.dataset?.cp) { if (d.open) routeState.openCp.add(d.dataset.cp); else routeState.openCp.delete(d.dataset.cp); }
    else if (d.classList?.contains("plan-scen")) routeState.scenOpen = d.open;
  }, true);
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

/* ---------- saved progress ----------
   Answers, confirmed activities and route choices stay in this browser so a person can come back to
   their plan; the server keeps nothing. Clear my data in Profile removes them; Display settings are kept
   separately. Without storage (private window, blocked) the app works as before, for this visit only. */
const STORE = "aivancin-progress";

function formValues() {
  return [...$("#intake").elements].filter(el => el.name && el.matches("textarea, input:not([type=search])") && (el.type !== "radio" || el.checked))
    .map(el => [el.name, el.value]);
}

function saveProgress() {
  const form = formValues();
  const typed = form.some(([n, v]) => ["current_job", "other_work", "studies"].includes(n) && v.trim());
  if (!state.lastIntake && !typed && !state.title && !routeState.target && !routeState.saved.size && !routeState.data) return;
  const data = {
    v: 1, mode, persona: examplePersona, form, title: state.title, titleActs: state.titleActs, untickedTitle: [...state.untickedTitle], cip: state.cip,
    lastIntake: state.lastIntake, suggest: state.suggest, ticked: [...state.ticked], dismissed: [...state.dismissed], manual: [...state.manual],
    lane: state.lane, knownTools: [...state.knownTools],
    routes: { reached: (Boolean(routeState.data) && isPersonal()) || routesReached, target: routeState.target, completed: [...routeState.completed], opts: routeState.opts,
      sort: routeState.sort, saved: [...routeState.saved], seen: [...routeState.seen] },
  };
  try { localStorage.setItem(STORE, JSON.stringify(data)); } catch (e) { /* storage unavailable or full: progress lasts for this visit only */ }
}

function restoreProgress() {
  let d = null;
  try { d = JSON.parse(localStorage.getItem(STORE) || "null"); } catch (e) { d = null; }
  if (!d || d.v !== 1) return;
  try {
    const f = $("#intake");
    d.form.forEach(([n, v]) => { const el = f.elements[n]; if (el) el.value = v; });
    state.title = d.title; state.titleActs = d.titleActs || []; state.untickedTitle = new Set(d.untickedTitle);
    renderChosen($("#f-title"), d.title ? `${d.title.title} (${d.title.code})` : null, () => setTitle(null));
    setCip(d.cip);
    mode = d.mode === "example" ? "example" : "own";
    examplePersona = mode === "example" ? d.persona : null;
    $(".examples").hidden = mode !== "example";
    state.lastIntake = d.lastIntake; state.suggest = d.suggest;
    state.ticked = new Map(d.ticked); state.dismissed = new Set(d.dismissed); state.manual = new Map(d.manual);
    state.lane = d.lane || "all"; state.knownTools = new Set(d.knownTools);
    const r = d.routes || {};
    Object.assign(routeState, { target: r.target || null, completed: new Set(r.completed), sort: r.sort || "overlap", saved: new Map(r.saved), seen: new Set(r.seen) });
    if (r.opts) Object.assign(routeState.opts, r.opts);
    routesReached = Boolean(r.reached);
    if (state.suggest) renderReview();
  } catch (e) {
    // A record this version cannot read is dropped rather than half applied.
    try { localStorage.removeItem(STORE); } catch (x) { /* storage unavailable */ }
    location.replace(location.pathname);
  }
}

function clearProgress() {
  try { localStorage.removeItem(STORE); } catch (e) { /* storage unavailable */ }
  // A fresh load, not a reload, so the browser does not put the old answers back into the form.
  location.replace(location.pathname);
}

/* The welcome screen offers to pick up where the person left off; their answers stay editable. */
function updateContinue() {
  const has = Boolean(state.lastIntake || routeState.target || routeState.saved.size);
  $("#continue-btn").hidden = !has;
  $("#continue-label").textContent = routeState.target ? "Continue your plan" : "Continue where you left off";
  const own = Boolean(state.lastIntake) && mode === "own";
  const start = $("#start-btn");
  start.dataset.go = own ? "edit" : "start";
  start.firstChild.textContent = own ? "Edit my answers" : "Get started";
  start.classList.toggle("btn-light", !has);
  start.classList.toggle("btn-on-art", has);
}

async function continueProgress() {
  if (!routeState.data && routesReached) {
    try {
      routeState.data = await api("/api/routes", routesRequest());
    } catch (err) {
      // The chosen route may no longer be supported; compare again without it before giving up.
      routeState.target = null;
      try { routeState.data = await api("/api/routes", routesRequest()); } catch (e) { routeState.data = null; }
    }
    if (routeState.data && routeState.target && !routeState.data.routes.some(r => r.code === routeState.target)) routeState.target = null;
    if (routeState.data) renderRoutes();
  }
  // Explored without describing work yet: rebuild the explore view with the chosen route and saved roles.
  if (!routeState.data && !state.suggest) {
    try { routeState.data = await loadRoutes(false); } catch (e) { routeState.data = null; }
  }
  if (routeState.data) navigate(routeState.target ? "plan" : "routes");
  else navigate(state.suggest ? "review" : "work", 1);
}

/* ---------- section tabs ---------- */
function goTab(tab) {
  const root = TAB_OF[current.screen] === tab;
  const dest = root ? tab : tabLast[tab] || tab;
  if (dest === "changed") showChanged(); else navigate(dest);
}

function toggleSaved(code) {
  const r = routeState.data?.routes.find(x => x.code === code);
  if (routeState.saved.has(code)) routeState.saved.delete(code); else if (r) routeState.saved.set(code, r.title);
  saveProgress();
  if (current.screen === "saved") { renderSaved(); $("#h-saved").focus(); return; }
  if (current.screen === "route") renderRoute(); else renderRoutes();
  $(`#${current.screen} [data-save="${CSS.escape(code)}"]`)?.focus();
}

function emptyState(ico, title, text, tab, label) {
  return `<div class="empty">${icon(ico)}<h2>${esc(title)}</h2><p>${esc(text)}</p>${tab ? `<button type="button" class="btn btn-gray" data-tab-go="${tab}">${esc(label)}</button>` : ""}</div>`;
}

/* ---------- Learn: every verified course for one route's priority gaps ---------- */
function learnRoute() {
  const d = routeState.data;
  const code = [routeState.learn, routeState.target].find(c => c && d.routes.some(r => r.code === c)) || sortedRoutes(d.routes)[0]?.code;
  return d.routes.find(r => r.code === code);
}

function courseRow(o) {
  const price = o.cost_rm.v ? fig(o.cost_rm, "rm") : `${esc(o.access || "Free")}${srcMarks(o.cost_rm.src)}`;
  const tags = `${o.in_block ? `<span class="tag sdg">In your first block</span>` : ""}${o.ai_practice ? `<span class="tag good">AI practice</span>` : ""}`;
  return `<li><a class="res-row" href="${esc(o.url)}" target="_blank" rel="noopener"><span class="res-tile" aria-hidden="true">${icon(o.ai_practice ? "sparkles" : "book-open")}</span>
    <span class="res-text"><b>${esc(o.title)}</b><span>${esc(o.provider)} · ${price} · ${fig(o.provider_hours, "")} h · link checked ${esc(o.checked_on)}</span>${tags ? `<span class="res-tags">${tags}</span>` : ""}</span>
    ${icon("external-link", "sm")}<span class="vh">(opens in a new tab)</span></a></li>`;
}

function renderLearn() {
  const d = routeState.data, box = $("#learn-body");
  if (!d) { box.innerHTML = ""; return; }
  const r = d.routes.length ? learnRoute() : null;
  if (!r) { box.innerHTML = emptyState("book-open", "No route to learn for yet", "Courses appear here once a route counts your work.", "routes", "Compare routes"); return; }
  const total = r.resources.reduce((s, g) => s + g.options.length, 0);
  const left = r.resources.reduce((s, g) => s + g.options.filter(o => !o.in_block).length, 0);
  box.innerHTML = `
    <p class="lead-left">Every verified course AIVANCIN has for a route's largest gaps, including the ones your first block leaves out for time or budget.</p>
    ${d.routes.length > 1 ? `<div class="ctl learn-pick"><label for="learn-route">Courses for</label><select id="learn-route">${sortedRoutes(d.routes).map(x =>
      `<option value="${esc(x.code)}" ${x.code === r.code ? "selected" : ""}>${esc(x.title)}${x.code === routeState.target ? " (your route)" : ""}</option>`).join("")}</select></div>` : ""}
    <p class="foot learn-sum">${total} course${total === 1 ? "" : "s"} for ${r.resources.length} gap${r.resources.length === 1 ? "" : "s"}; ${left} not in your first block.</p>
    ${r.resources.map((g, i) => `<section class="soft-card" data-scope aria-labelledby="h-learn-${i}"><h2 id="h-learn-${i}">${esc(g.name)}</h2>
      <p class="foot">${g.served ? "Your first block covers this gap." : g.options.length ? "Not in your first block." : "No verified course teaches this yet."}</p>
      ${g.options.length ? `<ul class="res-list">${g.options.map(courseRow).join("")}</ul>` : ""}</section>`).join("")}`;
  attachSources(box);
  $("#learn-route")?.addEventListener("change", e => { routeState.learn = e.target.value; renderLearn(); $("#learn-route").focus(); });
}

/* ---------- Saved: bookmarked routes ---------- */
function renderSaved() {
  const d = routeState.data, box = $("#saved-body");
  if (!routeState.saved.size) {
    box.innerHTML = emptyState("bookmark", "No saved routes yet", "Use the bookmark on a route to keep it here.", "routes", "Compare routes");
    return;
  }
  const live = d ? sortedRoutes(d.routes).filter(r => routeState.saved.has(r.code)) : [];
  const gone = [...routeState.saved].filter(([code]) => !live.some(r => r.code === code));
  box.innerHTML = `<p class="lead-left">Routes you bookmarked, saved in this browser. Open one to compare its work, AI use and first block.</p>
    ${live.length ? `<ul class="route-list" aria-label="Saved routes" data-scope>${live.map(routeRow).join("")}<li class="list-srcs srcs-slot"></li></ul>` : ""}
    ${gone.length ? `<section class="soft-card" aria-labelledby="h-gone"><h2 id="h-gone">No longer among your routes</h2>
      <p class="foot">With the activities you confirmed now, these do not count enough of your work.</p>
      <ul class="plain-rows">${gone.map(([code, title]) => `<li><span>${esc(title)}</span><button type="button" class="link-btn" data-unsave="${esc(code)}">Remove</button></li>`).join("")}</ul></section>` : ""}`;
  attachSources(box);
}

/* ---------- Profile: answers, activities, progress and the saved data ---------- */
function activityNames() {
  const smap = suggestionMap();
  return [...state.ticked.keys()].map(id => smap.get(id)?.name || id).concat([...state.manual.values()].map(m => m.name));
}

function renderProfile() {
  const box = $("#profile-body");
  const c = laneCounts();
  const target = routeState.data?.routes.find(r => r.code === routeState.target);
  const steps = target ? target.block.steps : [];
  const items = [
    ["Describe your work", Boolean(state.lastIntake)],
    ["Confirm your activities", c.all > 0],
    ["Compare routes", Boolean(routeState.data) && isPersonal()],
    ["See how AI is used in a role", routeState.seen.has("ai")],
    ["Choose a route", Boolean(target)],
    ["Start learning", steps.some(s => s.completed)],
    ["Finish your first block", steps.length > 0 && steps.every(s => s.completed)],
  ];
  const done = items.filter(x => x[1]).length;
  const next = items.find(x => !x[1]);
  const li = state.lastIntake || {};
  const persona = mode === "example" ? state.personas.find(p => p.id === examplePersona) : null;
  const words = v => (v && v.trim() ? `<mark class="words">${esc(v)}</mark>` : `<span class="foot">Not answered</span>`);
  const names = activityNames();
  box.innerHTML = `
    ${persona ? `<div class="callout">${icon("info")}<p>You are looking at ${esc(persona.name)}'s example, a synthetic person. Choose Describe your work to use your own words.</p></div>` : ""}
    <section class="soft-card" aria-labelledby="h-prog"><h2 id="h-prog">Your progress</h2>
      <div class="ring-row"><span class="ring-box">${ring(done, items.length)}<span class="ring-num" aria-hidden="true">${done}/${items.length}</span></span>
        <p>${done} of ${items.length} steps done.${next ? ` Next: ${esc(next[0].toLowerCase())}.` : " Every step is done."}</p></div>
      <ul class="progress-list">${items.map(([t, ok]) => `<li class="${ok ? "ok" : ""}"><span class="pl-mark" aria-hidden="true">${ok ? icon("check", "sm") : ""}</span>${esc(t)}<span class="vh">${ok ? ", done" : ", not yet"}</span></li>`).join("")}</ul></section>
    ${state.lastIntake ? `<section class="soft-card" aria-labelledby="h-answers"><h2 id="h-answers">Your answers</h2>
      <dl class="answers">
        <dt>Current job${state.title ? `, ${esc(state.title.title)}` : ""}</dt><dd>${words(li.current_job)}</dd>
        <dt>Other work</dt><dd>${words(li.other_work)}</dd>
        <dt>Studies${state.cip ? `, ${esc(state.cip.title)}` : ""}</dt><dd>${words(li.studies)}</dd>
        <dt>Time and budget</dt><dd>${li.weekly_hours ?? "?"} hours a week, ${fmtNum(li.budget_rm ?? 0, "rm")} budget, first block of ${esc(routeState.opts.weeks)} weeks</dd>
      </dl>
      <button type="button" class="btn btn-gray" data-go="edit">${icon("pencil-line", "sm")}Edit answers</button>
      <p class="foot">Reading your answers again asks you to confirm your activities again.</p></section>
    <section class="soft-card" aria-labelledby="h-acts"><h2 id="h-acts">Your activities <span class="num">${c.all}</span></h2>
      ${names.length ? `<ul class="plain-rows">${names.map(n => `<li>${icon("check", "sm")}<span>${esc(n)}</span></li>`).join("")}</ul>` : ""}
      ${c.title ? `<p class="foot">Plus ${c.title} from your job title, ${esc(state.title.title)}.</p>` : ""}
      <button type="button" class="btn btn-gray" data-tab-go="review">Review activities</button></section>` : describeCard("profile")}
    <section class="soft-card" aria-labelledby="h-data"><h2 id="h-data">Your data</h2>
      <p>Your answers, activities, chosen route, ticked steps and saved routes are kept in this browser only, so you can come back later. The server keeps nothing.</p>
      <button type="button" class="btn btn-gray btn-danger-text" id="clear-open">${icon("trash-2", "sm")}Clear my data</button></section>`;
}

function sectionEvents() {
  $("#tabbar").addEventListener("click", e => { const t = e.target.closest(".tab"); if (t) goTab(t.dataset.tab); });
  document.addEventListener("click", e => {
    if (!e.target.closest("#clear-open")) return;
    $("#clear-sheet").returnValue = "";  // a backdrop click must never repeat an earlier answer
    $("#clear-sheet").showModal();
  });
  $("#clear-sheet").addEventListener("close", () => {
    if ($("#clear-sheet").returnValue === "clear") clearProgress(); else $("#clear-open")?.focus();
  });
}

routeEvents();
sectionEvents();
start();

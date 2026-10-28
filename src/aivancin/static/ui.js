"use strict";
/* Career presentation, illustrations and learning-path layout. */
const suppliedScenes = {
  "Choose your career direction": ["choose-career-direction.png", 1536, 1024],
  "Vance learning milestone": ["learning-milestone.png", 1448, 1086]
};
function artSlot(label, width, height, cls = "") {
  const scene = suppliedScenes[label];
  if (!scene) return "";
  return `<figure class="illustration-slot image-filled ${cls}" aria-hidden="true"><div class="white-canvas"><img src="/art/illustrations/vance/${scene[0]}" width="${scene[1]}" height="${scene[2]}" alt="" decoding="async"></div></figure>`;
}
const roleCategory = r => ["15-2051.00", "15-2051.01"].includes(r.code) ? "data" : r.family;
const guidePortrait = "/art/illustrations/vance/learning-guide.png";
const careerPortraits = {
  "15-1232.00": "/art/illustrations/vance/computer-user-support-specialists.png",
  "15-1231.00": "/art/illustrations/vance/computer-network-support-specialists.png",
  "15-1244.00": "/art/illustrations/vance/network-and-computer-systems-administrators.png",
  "15-1212.00": "/art/illustrations/vance/information-security-analysts.png",
  "15-1252.00": "/art/illustrations/vance/software-developers.png",
  "15-1251.00": "/art/illustrations/vance/computer-programmers.png",
  "15-1254.00": "/art/illustrations/vance/web-developers.png",
  "15-1253.00": "/art/illustrations/vance/software-quality-assurance-analysts-and-testers.png",
  "13-2011.00": "/art/illustrations/vance/accountants-and-auditors.png",
  "13-2031.00": "/art/illustrations/vance/budget-analysts.png",
  "13-2041.00": "/art/illustrations/vance/credit-analysts.png",
  "13-2051.00": "/art/illustrations/vance/financial-and-investment-analysts.png",
  "13-2061.00": "/art/illustrations/vance/financial-examiners.png",
  "13-2072.00": "/art/illustrations/vance/loan-officers.png",
  "13-2082.00": "/art/illustrations/vance/tax-preparers.png",
  "15-2051.01": "/art/illustrations/vance/business-intelligence-analysts.png",
  "15-2051.00": "/art/illustrations/vance/data-scientists.png"
};
function mascotPortrait(source, cls = "") {
  return `<figure class="illustration-slot image-filled ${cls}" aria-hidden="true"><div class="white-canvas"><img src="${esc(source)}" width="1122" height="1402" alt="" decoding="async"></div></figure>`;
}
const careerFields = [
  { id: "it_support", label: "IT & networks", title: "Keep people connected.", description: "Support the people, systems and networks that make everyday work possible." },
  { id: "software", label: "Software & web", title: "Make ideas work.", description: "Build, improve and test the software people use." },
  { id: "finance_analytics", label: "Finance & business", title: "Bring clarity to decisions.", description: "Work with money, records and rules to help organisations move forward." },
  { id: "data", label: "Data & insights", title: "Find the story in the numbers.", description: "Turn information into patterns, explanations and useful decisions." },
];
const careerCopy = {
  "15-1232.00": ["IT support specialist", "Help people solve problems with their computers and software."],
  "15-1231.00": ["Network support specialist", "Find and fix the network problems that interrupt people's work."],
  "15-1244.00": ["Systems administrator", "Keep an organisation's networks and computer systems running."],
  "15-1212.00": ["Cybersecurity analyst", "Protect systems and information, and investigate security risks."],
  "15-1251.00": ["Computer programmer", "Write, change and test the code that makes software work."],
  "15-1252.00": ["Software developer", "Design and build applications around what people need."],
  "15-1253.00": ["Software tester", "Find problems in software and check that fixes work."],
  "15-1254.00": ["Web developer", "Build and improve websites and web applications."],
  "13-2011.00": ["Accountant & auditor", "Prepare and examine financial records to check their accuracy."],
  "13-2031.00": ["Budget analyst", "Help organisations plan spending and keep track of their budgets."],
  "13-2041.00": ["Credit analyst", "Examine financial information to assess lending risk."],
  "13-2051.00": ["Financial analyst", "Evaluate financial information and investment opportunities."],
  "13-2061.00": ["Financial examiner", "Check that financial institutions follow the rules."],
  "13-2072.00": ["Loan officer", "Review loan applications and guide people through the process."],
  "13-2082.00": ["Tax preparer", "Prepare tax returns using people's financial records."],
  "15-2051.00": ["Data scientist", "Use data and models to investigate questions and find patterns."],
  "15-2051.01": ["Business intelligence analyst", "Turn business data into reports and insights people can use."],
};
let selectedCareerField = "all";
let careerOrderOpen = false;

function applyCareerField(field, announceChange = false) {
  const root = document.querySelector("#routes-body");
  const buttons = [...root.querySelectorAll("[data-career-filter]")];
  selectedCareerField = buttons.some(b => b.dataset.careerFilter === field) ? field : "all";
  buttons.forEach(b => b.setAttribute("aria-pressed", String(b.dataset.careerFilter === selectedCareerField)));
  const picker = root.querySelector("#career-field-picker");
  if (picker) picker.value = selectedCareerField;
  const sections = root.querySelectorAll(".field-section");
  if (sections.length) {
    sections.forEach(section => { section.hidden = selectedCareerField !== "all" && section.dataset.category !== selectedCareerField; });
  } else {
    root.querySelectorAll(".route-item[data-category]").forEach(item => { item.hidden = selectedCareerField !== "all" && item.dataset.category !== selectedCareerField; });
  }
  if (announceChange) {
    const button = buttons.find(b => b.dataset.careerFilter === selectedCareerField);
    const label = button.firstChild.textContent.trim();
    const count = button.querySelector("span").textContent;
    root.querySelector("#career-filter-status").textContent = `${label}: ${count} careers shown.`;
  }
}

function decorateRoutes() {
  document.querySelectorAll(".route-row[data-open-route]").forEach(row => {
    const r = routeState.data?.routes.find(x => x.code === row.dataset.openRoute);
    if (!r || row.querySelector(".role-preview")) return;
    row.closest(".route-item").dataset.category = roleCategory(r);
    const copy = careerCopy[r.code] || [r.title, r.description.first];
    const previewImage = careerPortraits[r.code] || guidePortrait;
    const imageSize = [1122, 1402];
    row.insertAdjacentHTML("afterbegin", `<span class="role-preview has-image" aria-hidden="true"><span class="white-canvas"><img src="${previewImage}" width="${imageSize[0]}" height="${imageSize[1]}" alt="" loading="lazy" decoding="async"></span></span>`);
    row.querySelector(".rr-title").textContent = copy[0];
    row.querySelector(".rr-title").insertAdjacentHTML("afterend", `<span class="role-sentence">${esc(copy[1])}</span>`);
    if (isPersonal()) row.querySelector(".rr-body").insertAdjacentHTML("afterbegin", `<span class="career-category">${esc(r.family_label)}</span>`);
  });
  const root = document.querySelector("#routes-body");
  const list = root.querySelector(":scope > .route-list");
  if (list && !document.querySelector(".career-filters")) {
    const items = [...list.querySelectorAll(":scope > .route-item[data-category]")];
    const groups = [{id:"all",label:"All careers"}, ...careerFields];
    const counts = Object.fromEntries(groups.map(({id}) => [id, items.filter(item => id === "all" || item.dataset.category === id).length]));
    list.insertAdjacentHTML("beforebegin", `<div class="career-filters" role="group" aria-label="Career fields">${groups.filter(({id}) => counts[id]).map(({id, label}) => `<button type="button" data-career-filter="${id}" aria-pressed="${id === "all"}">${label}<span>${counts[id]}</span></button>`).join("")}</div><label class="mobile-field-picker" for="career-field-picker">Career field<select id="career-field-picker">${groups.filter(({id}) => counts[id]).map(({id, label}) => `<option value="${id}">${label} (${counts[id]})</option>`).join("")}</select></label><p id="career-filter-status" class="vh" role="status"></p>`);
    if (!isPersonal()) {
      const directory = document.createElement("div");
      directory.className = "career-directory";
      careerFields.forEach((field, i) => {
        const roles = items.filter(item => item.dataset.category === field.id);
        if (!roles.length) return;
        const section = document.createElement("section");
        section.className = "field-section";
        section.dataset.category = field.id;
        section.setAttribute("aria-labelledby", `h-field-${field.id}`);
        section.innerHTML = `<header class="field-heading"><div><p class="field-index">0${i + 1} / ${roles.length} CAREERS</p><h2 id="h-field-${field.id}">${field.label}</h2><p class="field-promise">${field.title}</p></div><p class="field-description">${field.description}</p></header><ul class="field-roles" aria-label="${esc(field.label)} careers"></ul>`;
        roles.forEach(item => section.querySelector("ul").append(item));
        directory.append(section);
      });
      const source = list.querySelector(".list-srcs");
      if (source) {
        const footer = document.createElement("div");
        footer.className = "directory-sources";
        footer.append(...source.childNodes);
        directory.append(footer);
      }
      list.replaceWith(directory);
      const describe = root.querySelector(".describe-card");
      if (describe) {
        describe.classList.add("directory-personalise");
        directory.after(describe);
      }
      const sortLegend = root.querySelector(".sort-ctl legend");
      if (sortLegend) sortLegend.textContent = "Order careers within each field";
      root.querySelector(".sort-help p")?.prepend("Within each career field. ");
      const sortControl = root.querySelector(".sort-ctl");
      if (sortControl) {
        const order = document.createElement("details");
        order.className = "directory-order";
        order.open = careerOrderOpen;
        order.innerHTML = `<summary>Order within fields: <b>${esc(EXPLORE_SORTS[sortKey()][0])}</b></summary>`;
        sortControl.before(order);
        order.append(sortControl);
      }
      document.querySelector("#h-routes").textContent = "Explore your next direction.";
      document.querySelector("#routes-lead").innerHTML = '<p>Discover careers in IT, software, finance and data. Find out what the work involves and where you could begin.</p>';
    } else {
      list.classList.add("personal-gallery");
    }
  }
  applyCareerField(selectedCareerField);
}

function decorateCareer() {
  const r = routeState.data?.routes.find(x => x.code === routeState.open);
  const head = document.querySelector(".rd-head");
  if (!r || !head || head.querySelector(".career-scene")) return;
  head.dataset.category = roleCategory(r);
  document.querySelector("#route-body").dataset.category = roleCategory(r);
  head.classList.add("career-hero");
  head.querySelector(".d-kicker").textContent = careerFields.find(field => field.id === roleCategory(r))?.label || r.family_label;
  head.insertAdjacentHTML("beforeend", mascotPortrait(careerPortraits[r.code] || guidePortrait, "career-scene"));
  const panel = document.querySelector("#rd-panel");
  if (routeState.tab === "overview") {
    panel.classList.add("career-overview");
    const activities = panel.querySelector('[aria-labelledby="h-rd-acts"]');
    if (activities) {
      activities.classList.add("role-work-section");
      activities.querySelector("h2").textContent = "What the work looks like";
      activities.querySelector(".gaps").classList.add("role-tasks-grid");
    }
    const requirements = panel.querySelector('[aria-labelledby="h-rd-req"]');
    if (requirements) requirements.classList.add("role-requirements");
    const opportunity = panel.querySelector('[aria-labelledby="h-rd-opp"]');
    if (opportunity) opportunity.classList.add("role-opportunity");
  }
}

function decoratePath() {
  const r = routeState.data?.routes.find(x => x.code === routeState.target);
  const box = document.querySelector("#plan-body");
  if (!r || !box || box.querySelector(".journey-banner")) return;
  const steps = r.block.steps;
  const done = steps.filter(x => x.completed).length;
  const next = steps.findIndex(x => !x.completed);
  const pct = steps.length ? Math.round(done / steps.length * 100) : 0;
  box.dataset.category = roleCategory(r);
  const planCategory = box.querySelector(".plan-head .d-kicker");
  if (planCategory) planCategory.textContent = careerFields.find(field => field.id === roleCategory(r))?.label || r.family_label;
  box.insertAdjacentHTML("afterbegin", `<section class="journey-banner" aria-label="Learning progress"><div><p class="career-category">YOUR FIRST LEARNING BLOCK</p><h2>${done === steps.length ? "Look at what you have built." : done ? "A little further. A little stronger." : "Your next chapter starts here."}</h2><p>${done === steps.length ? "All steps marked done. Review your checkpoints and what you made." : "A clear route from what you know to what you can do next."}</p></div><div class="journey-progress"><strong>${done}<span> / ${steps.length}</span></strong><span>steps marked done</span><progress value="${done}" max="${Math.max(1, steps.length)}" aria-label="Steps marked done">${pct}%</progress></div></section>`);
  box.querySelectorAll(".pstep").forEach((el, i) => {
    const status = steps[i].completed ? "Complete" : i === next ? "Up next" : "Later in your path";
    el.dataset.pathState = steps[i].completed ? "complete" : i === next ? "current" : "upcoming";
    el.insertAdjacentHTML("afterbegin", `<p class="path-state">${status}<span>STEP ${i + 1} OF ${steps.length}</span></p>`);
  });
  const list = box.querySelector(".psteps");
  if (list) {
    list.insertAdjacentHTML("beforebegin", '<div class="path-heading"><h2>Your learning path</h2><p>Learn. Make something. Check your work.</p></div>');
    const scen = box.querySelector(".scen-card");
    if (scen) list.insertAdjacentElement("afterend", scen);
  }
  const start = box.querySelector(".start-card");
  const startList = start?.querySelector(".start-list");
  if (startList && startList.children.length > 1) {
    const more = document.createElement("details");
    more.className = "start-preparation";
    more.innerHTML = '<summary>Preparation, time and what you will make</summary><dl class="start-list"></dl>';
    const destination = more.querySelector("dl");
    [...startList.children].slice(1).forEach(el => destination.append(el));
    startList.after(more);
  }
  const currentStep = box.querySelector('.pstep[data-path-state="current"]');
  if (start && currentStep) {
    const task = document.createElement("details");
    task.className = "next-task-details";
    task.innerHTML = '<summary>Start this step: your first task</summary>';
    task.append(start);
    currentStep.querySelector(".res-row").after(task);
  }
  const lead = document.querySelector("#plan-lead");
  lead.innerHTML = `<details class="block-explanation"><summary>About this learning block</summary>${lead.innerHTML}</details>`;
  const side = box.querySelector(".plan-head");
  if (side) side.insertAdjacentHTML("beforeend", artSlot("Vance learning milestone", 640, 480, "plan-illustration"));
}

const originalRenderRoutes = renderRoutes;
renderRoutes = function() { originalRenderRoutes(); decorateRoutes(); };
const originalRenderRoute = renderRoute;
renderRoute = function() { originalRenderRoute(); decorateCareer(); };
const originalRenderPlan = renderPlan;
renderPlan = function() { originalRenderPlan(); decoratePath(); };
document.querySelector("#routes-body").addEventListener("click", e => {
  const filter = e.target.closest("[data-career-filter]");
  if (!filter) return;
  applyCareerField(filter.dataset.careerFilter, true);
});
document.querySelector("#routes-body").addEventListener("change", e => {
  if (e.target.id === "career-field-picker") applyCareerField(e.target.value, true);
  if (e.target.matches('input[name="rsort"]')) careerOrderOpen = true;
});
document.querySelector("#routes-body").addEventListener("toggle", e => {
  if (e.target.matches(".directory-order")) careerOrderOpen = e.target.open;
}, true);

const welcomeProcess = document.querySelector(".welcome-process");
const welcomePortrait = document.querySelector(".welcome-art");
welcomePortrait.classList.add("image-filled");
welcomePortrait.innerHTML = `<div class="white-canvas" aria-hidden="true"><img src="${guidePortrait}" width="1122" height="1402" alt="" decoding="async"></div>`;
welcomeProcess.insertAdjacentHTML("afterend", `<section class="welcome-benefits" aria-labelledby="h-welcome-benefits"><figure class="illustration-slot image-filled welcome-benefit-scene" aria-hidden="true"><div class="white-canvas"><img src="/art/illustrations/vance/experience-to-direction.png" width="1448" height="1086" alt="" loading="lazy" decoding="async"></div></figure><div><p class="career-category">A PATH BUILT FROM YOUR EXPERIENCE</p><h2 id="h-welcome-benefits">You do not have to start from zero.</h2><div class="welcome-benefit"><h3>See what carries over</h3><p>Connect the work you do in your job, projects and studies to the activities a role needs.</p></div><div class="welcome-benefit"><h3>Know what to build next</h3><p>Understand the gaps, entry requirements and evidence behind each direction.</p></div><div class="welcome-benefit"><h3>Turn a direction into a first step</h3><p>Find a learning block that fits your time and budget, with something to make and check.</p></div><button class="btn btn-primary" type="button" data-go="start">Find your direction ${icon("arrow-right", "sm")}</button></div></section><section class="welcome-fields" aria-labelledby="h-welcome-fields"><p class="career-category">FIND YOUR STARTING POINT</p><h2 id="h-welcome-fields">What kind of work draws you in?</h2><div>${careerFields.map(field => `<button type="button" data-welcome-field="${field.id}" data-category="${field.id}"><span>${field.label}</span><span>${field.title}</span>${icon("arrow-right", "sm")}</button>`).join("")}</div></section>`);
document.querySelector(".welcome-fields").addEventListener("click", async e => {
  const button = e.target.closest("[data-welcome-field]");
  if (!button) return;
  const previousField = selectedCareerField;
  selectedCareerField = button.dataset.welcomeField;
  try {
    routeState.data = await loadRoutes(false);
    routesReached = true;
    navigate("routes");
  } catch (error) {
    selectedCareerField = previousField;
    announce("The careers could not load. Try again.");
  }
});

/* Use the direction illustration in career-selection empty states. */
const originalEmptyState = emptyState;
emptyState = function(...args) {
  return originalEmptyState(...args).replace(/<img class="comprehension-art"[^>]+>/g, artSlot("Choose your career direction", 960, 640, "empty-illustration"));
};

// Restore presentation when saved progress rendered before this script loaded.
if (document.querySelector("#routes-body .route-row")) decorateRoutes();
if (document.querySelector(".rd-head")) decorateCareer();
if (document.querySelector("#plan-body .plan-head")) decoratePath();

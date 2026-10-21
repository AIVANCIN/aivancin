"""Routes, first learning blocks, shared foundations and the update comparison (SPEC section 10).

A route is a supported destination the person's confirmed work already overlaps. Its first block is a
sequence of verified, curated entries that fits the person's planning weeks, weekly hours and budget.
Nothing here judges proficiency or readiness; requirements the app cannot judge are left to the person.
"""
import datetime as dt

from . import aei, outlook, pathway, sdg
from .report import ONET_SRC, PAY_SRC, ReportError, fig, pct

WEEKS = (4, 8, 12)
PRIORITY_GAPS = 8
MAX_ROUTES = 3
MIN_DONE = 2  # initial: a route needs at least two confirmed activities that count for the role (see credited)
SIBLING_CREDIT = True  # a confirmed near-twin of one of the role's activities counts (table built by tools/build_routes.py)
PER_GAP = 2  # initial: at most two entries per prioritized gap in a block (an introduction, then practice)
LONG_SHARE = 0.5  # initial: an entry needing more than this share of the block's hours waits for the shorter ones
CAT = "aivancin-catalogue"
ROUTES_SRC = "aivancin-routes"
AI_FORM = ("Do it yourself first, then ask an AI assistant to do the same task and check its output against yours; "
           "write down every error you found and how you spotted it.")
AI_USE_ACTIVITIES = 10  # the role's largest-weight activities shown under "AI use"
LOCAL_EVIDENCE = "Malaysian opportunity evidence: not shown until reuse permission is confirmed (MyCOL, MyMahir)."
# Explore, before the person describes their work: every supported role with nothing confirmed, and a first block
# built on the intake form's starting values (6 hours a week, no budget). These are stated as assumptions.
EXPLORE_PROFILE = {"title_code": None, "degree_field": "", "cip_code": None, "degree_level": None, "weekly_hours": 6.0,
                   "budget_rm": 0.0, "without_ai_suggestions": False, "activities": []}
EXPLORE_SRC = "explore-defaults"


RADAR_MAIN = 3  # initial: before work is described, the Radar shows each supported role's three largest activities
EXPLORATION_CARDS = 3  # SPEC 11.7: at most three exploration cards when fewer than two routes qualify
EXPLORATION_LABEL = "Exploration: no curated first block yet"


def job_group(band):
    name = outlook.band_name(band)
    if name in outlook.GROW_BANDS:
        return "growing"
    if name in outlook.DECLINE_BANDS:
        return "declining"
    return "steady" if name else "no projection"


def effort(c):
    return c["hours"] + c.get("practice_hours", 0.0)


def decent_order(routes):
    """Decent work (SDG 8.5) order: the target group's latest DOSM median (higher first; none last), then activity
    overlap, then code. Group medians, not salary offers."""
    return [r["code"] for r in sorted(routes, key=lambda r: (r["median"] is None, -(r["median"] or {"v": 0})["v"],
                                                              -r["overlap"]["v"], r["code"]))]


def fits_family(c, family):
    """An entry limited to some destination families serves only those (its activities mean other work elsewhere)."""
    return not c.get("families") or family in c["families"]


class Planner:
    def __init__(self, snaps, matcher, reporter):
        self.snaps, self.m, self.rep = snaps, matcher, reporter
        self.dests = {d["code"]: d for d in (snaps.routes or {}).get("destinations", []) if d["admitted"]}

    # ---- options ---------------------------------------------------------------------------------
    def options(self, opts, profile):
        errors = {}
        weeks = opts.get("weeks", 4)
        if weeks not in WEEKS:
            errors["weeks"] = "Choose a planning period of 4, 8 or 12 weeks."
        willing = opts.get("willing_long", False)
        if not isinstance(willing, bool):
            errors["willing_long"] = "Say yes or no to a longer qualification."
        scenario = opts.get("scenario", "moderate")
        if scenario not in outlook.SCENARIOS:
            errors["scenario"] = "Choose the slower, moderate or faster scenario."
        threshold = opts.get("threshold", pathway.DEFAULT_THRESHOLD)
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 1 <= threshold <= 100:
            errors["threshold"] = "The threshold must be between 1 and 100."
        pace = opts.get("pace", pathway.DEFAULT_PACE)
        if isinstance(pace, bool) or not isinstance(pace, (int, float)) or not 1.0 <= pace <= 3.0:
            errors["pace"] = "The faster-scenario pace must be between 1.0 and 3.0."
        ev = self.snaps.evidence
        release = opts.get("release") or (ev.ids[-1] if ev.ids else None)
        if release not in ev.ids:
            errors["release"] = "Choose one of the bundled releases."
        horizon = opts.get("horizon", "plan")
        if horizon != "plan" and horizon not in outlook.HORIZON_CHOICES:
            errors["horizon"] = "Choose the estimated planning horizon or 12, 24, 36 or 60 months."
        completed = opts.get("completed") or []
        if not isinstance(completed, list) or not all(isinstance(x, str) for x in completed):
            errors["completed"] = "Completed steps must be a list of step identifiers."
        target = opts.get("target")
        if target is not None and target not in self.dests:
            errors["target"] = "Choose one of the supported destinations."
        if errors:
            raise ReportError(errors)
        return {"weeks": weeks, "willing_long": willing, "scenario": scenario, "threshold": float(threshold),
                "pace": float(pace), "release": release, "horizon": horizon, "completed": set(completed), "target": target,
                "capacity": weeks * profile["weekly_hours"], "budget": profile["budget_rm"]}

    def credited(self, code, prof):
        """Confirmed activities that count toward the two-activity rule: the role's own, and (with SIBLING_CREDIT)
        near-twins of the role's activities, because O*NET links near-identical activities to different roles
        ("Develop computer or online applications" only to Computer Programmers, "Design software applications" only
        to Software Developers). A near-twin shares the O*NET intermediate work activity and has a similar name (the
        destination's sibling table). Overlap, gaps and order stay exact."""
        role = self.m.w.get(code, {})
        direct = sorted(d for d in prof if d in role)
        related = []
        if SIBLING_CREDIT:
            twin_of = {}
            for d, wgt in sorted(role.items(), key=lambda kv: (-kv[1], kv[0])):
                for x in self.dests[code].get("siblings", {}).get(d, []):
                    twin_of.setdefault(x, d)
            related = [(d, twin_of[d]) for d in sorted(prof) if d not in role and d in twin_of]
        return direct, related

    # ---- requirements ----------------------------------------------------------------------------
    def requirement_status(self, req, profile, o):
        if req["judge"] == "degree":
            if profile["degree_level"] is None:
                return "you decide", "Describe your work and studies to check this."
            if profile["degree_level"] == "degree":
                return "met", "You hold a degree."
            if o["willing_long"]:
                return "planned", "You said you would take a longer qualification (a degree top-up)."
            return "missing", "You hold a diploma and did not choose a longer qualification."
        if req["judge"] == "none":
            return "met", "No related experience is usually required."
        return "you decide", "AIVANCIN cannot judge this from your description."

    # ---- blocks ----------------------------------------------------------------------------------
    def _closure(self, c, by_id, chosen, completed, seen=None):
        """Prerequisites of c not yet chosen or completed, in dependency order; None if one is not verified."""
        seen = seen if seen is not None else set()
        out = []
        for rid in c.get("requires", []):
            if rid in chosen or rid in completed or rid in seen:
                continue
            req = by_id.get(rid)
            if req is None:
                return None
            sub = self._closure(req, by_id, chosen, completed, seen)
            if sub is None:
                return None
            out += sub
            if rid not in seen:
                seen.add(rid)
                out.append(req)
        return out

    def block(self, code, missing, courses, o):
        courses = [c for c in courses if fits_family(c, self.dests[code]["family"])]
        prioritized = missing[:PRIORITY_GAPS]
        weight = {g["dwa_id"]: g["weight"] for g in prioritized}
        by_id = {c["id"]: c for c in courses}

        def serves(c):
            return [g for g in prioritized if g["dwa_id"] in c.get("dwas", [])]

        supported = {g["dwa_id"] for c in courses for g in serves(c)}
        candidates = sorted((c for c in courses if serves(c) and not c.get("ai_skill")),
                            key=lambda c: (-max(weight[g["dwa_id"]] for g in serves(c)), c["cost_rm"], effort(c), c["id"]))
        chosen, used, spent = {}, 0.0, 0.0
        done = [by_id[i] for i in sorted(o["completed"]) if i in by_id and serves(by_id[i])]
        per_gap = {}
        # Breadth before depth: entries needing more than half the block's hours are considered only after the
        # shorter ones, and an entry joins only if it serves a gap that has fewer than two entries so far.
        long_entry = lambda c: effort(c) > o["capacity"] * LONG_SHARE  # noqa: E731
        for c in [c for c in candidates if not long_entry(c)] + [c for c in candidates if long_entry(c)]:
            if c["id"] in chosen or c["id"] in o["completed"]:
                continue
            if all(per_gap.get(g["dwa_id"], 0) >= PER_GAP for g in serves(c)):
                continue
            chain = self._closure(c, by_id, chosen, o["completed"])
            if chain is None:
                continue
            chain = [x for x in chain if x["id"] not in chosen] + [c]
            e = sum(effort(x) for x in chain)
            k = sum(x["cost_rm"] for x in chain)
            if used + e <= o["capacity"] + 1e-9 and spent + k <= o["budget"] + 1e-9:
                for x in chain:
                    chosen[x["id"]] = x
                    for g in serves(x):
                        per_gap[g["dwa_id"]] = per_gap.get(g["dwa_id"], 0) + 1
                used += e
                spent += k
        ordered = done + list(chosen.values())
        served = {g["dwa_id"] for x in ordered for g in serves(x)}
        return {"prioritized": prioritized, "supported": supported, "served": served, "steps": ordered,
                "serves": serves, "used": used, "spent": spent}

    def _step(self, c, serves, completed, form, reason, kind="learn"):
        gaps = serves(c)
        if kind == "learn" and not gaps:
            kind = "prerequisite"
        checkpoint = c["checkpoint"] + (" " + AI_FORM if form == "ai" else "")
        return {"id": c["id"], "title": c["title"], "provider": c["provider"], "url": c["url"], "kind": kind,
                "serves": [{"dwa_id": g["dwa_id"], "name": g["name"]} for g in gaps],
                "purpose": ("Builds: " + "; ".join(g["name"].rstrip(".") for g in gaps) + ".") if gaps else
                           "Required first by a later step.",
                "objective": (c.get("objectives") or [""])[0], "coverage": c.get("coverage"),
                "provider_hours": fig(c["hours"], CAT), "practice_hours": fig(c.get("practice_hours", 0.0), CAT, estimate=True),
                "cost_rm": fig(c["cost_rm"], CAT), "checked_on": c["link_check"]["checked_on"],
                "requires": c.get("requires", []), "checkpoint": checkpoint, "form": form, "reason": reason,
                "completed": c["id"] in completed, "access": c.get("access", "")}

    def _forms(self, step_gaps, months, o):
        """Checkpoint form per scenario for a step's gaps, with the figures that decided it."""
        forms, reason = {}, []
        for g in step_gaps:
            ol = self.rep.skills.outlook(g["dwa_id"], o["release"])
            if ol is None:
                continue
            proj = {s: round(self.rep.skills.project(ol["now"], s, months, o["pace"]), 1) for s in outlook.SCENARIOS}
            reason.append({"dwa_id": g["dwa_id"], "name": g["name"], "now": fig(ol["now"], *ol["src"]),
                           "projected": {s: fig(v, *ol["src"], *(["aei-report-2511.15080", "your-input"] if s == "faster" else []))
                                         for s, v in proj.items()},
                           "releases": [r["release"] for r in ol["releases"]], "weak": ol["weak"],
                           # what each pooled release contributed, and how much evidence stood behind it
                           "per_release": [{"release": r["release"], "share": fig(r["share"], f"aei-{r['release']}", "onet31-task-dwas"),
                                            "statements": fig(r["tasks"], f"aei-{r['release']}", "onet31-task-dwas"),
                                            "conversations": None if r["conversations"] is None else
                                            fig(r["conversations"], f"aei-{r['release']}")} for r in ol["releases"]]})
        for s in outlook.SCENARIOS:
            forms[s] = "ai" if any(r["projected"][s]["v"] >= o["threshold"] for r in reason) else "plain"
        return forms, reason

    def ai_use(self, code, prof, o, months):
        """The role's largest-weight activities with how much of each AI does directly in observed Claude use,
        classed with the plan's threshold, plus each activity's skill demand (F2) and AI shift (F3) at the route's
        horizon. Usage evidence and published projections, never one combined score."""
        top = sorted(self.m.w.get(code, {}).items(), key=lambda kv: (-kv[1], kv[0]))[:AI_USE_ACTIVITIES]
        out = []
        for d, _ in top:
            ol = self.rep.skills.outlook(d, o["release"])
            row = self.rep.skill_row(d, self.snaps.onet.dwa_names[d], o["release"], months, o["threshold"], o["pace"],
                                     "you do this" if d in prof else "still to build")
            out.append({**row, "done": d in prof, "weak": bool(ol and ol["weak"]),
                        "class": outlook.classify(ol["now"] if ol else None, o["threshold"])})
        return out

    def future(self, code, masco):
        """The route's Future tab beyond its activities: the Malaysian group trend (F4, observed) and O*NET's
        emerging tasks and in-demand technologies."""
        onet = self.snaps.onet
        return {"trend": outlook.group_trend(self.snaps, masco.get("major")),
                "emerging": {"tasks": [{**t, "src": ["onet31-emerging-tasks"]} for t in onet.emerging.get(code, [])],
                             "technologies": [{**t, "src": ["onet31-software"]} for t in onet.software.get(code, [])]}}

    def resources(self, family, prioritized, courses, ai_courses, block_ids, served):
        """Every verified entry for each prioritized gap, including the ones the first block leaves out (time,
        budget or breadth), so the person can see the alternatives. Entries in the block come first."""
        pool = [c for c in courses + ai_courses if fits_family(c, family)]
        out = []
        for g in prioritized:
            opts = [c for c in pool if g["dwa_id"] in c.get("dwas", [])]
            opts.sort(key=lambda c: (c["id"] not in block_ids, c["cost_rm"], effort(c), c["id"]))
            out.append({"dwa_id": g["dwa_id"], "name": g["name"], "served": g["dwa_id"] in served,
                        "options": [{"id": c["id"], "title": c["title"], "provider": c["provider"], "url": c["url"],
                                     "access": c.get("access", ""), "coverage": c.get("coverage"),
                                     "ai_practice": bool(c.get("ai_skill")), "in_block": c["id"] in block_ids,
                                     "provider_hours": fig(c["hours"], CAT),
                                     "practice_hours": fig(c.get("practice_hours", 0.0), CAT, estimate=True),
                                     "cost_rm": fig(c["cost_rm"], CAT), "checked_on": c["link_check"]["checked_on"]}
                                    for c in opts]})
        return out

    # ---- one route -------------------------------------------------------------------------------
    def route(self, code, prof, profile, courses, ai_courses, o):
        onet = self.snaps.onet
        dest = self.dests[code]
        done, missing = self.m.done_and_missing(code, prof)
        total = len(done) + len(missing)
        b = self.block(code, missing, courses, o)
        labels = self.rep.labels(code, profile)
        months_study = b["used"] / profile["weekly_hours"] / pathway.WEEKS_PER_MONTH if profile["weekly_hours"] else 0.0
        if o["horizon"] == "plan":
            months, basis = outlook.planning_months(months_study, labels, onet.experience.get(code))
        else:
            months, basis = o["horizon"], "your choice"
        steps, differ, extra = [], [], {s: [] for s in outlook.SCENARIOS}
        chosen_ids = {x["id"] for x in b["steps"]}
        ai_pool = [c for c in ai_courses if c["id"] not in chosen_ids]
        used, spent = b["used"], b["spent"]
        for x in b["steps"]:
            forms, reason = self._forms(b["serves"](x), months, o)
            step = self._step(x, b["serves"], o["completed"], forms[o["scenario"]], reason)
            step["forms"] = forms
            if len(set(forms.values())) > 1:
                differ.append(x["title"])
            steps.append(step)
            for s in outlook.SCENARIOS:
                if forms[s] != "ai":
                    continue
                for g in b["serves"](x):
                    ai = next((c for c in ai_pool if g["dwa_id"] in c.get("dwas", [])), None)
                    if ai and ai["id"] not in {e["id"] for e in extra[s]}:
                        extra[s].append(ai)
        # Activity-specific AI-practice entries join the selected scenario's block when capacity and budget allow.
        added = []
        for ai in extra[o["scenario"]]:
            if used + effort(ai) <= o["capacity"] + 1e-9 and spent + ai["cost_rm"] <= o["budget"] + 1e-9:
                used += effort(ai)
                spent += ai["cost_rm"]
                added.append(self._step(ai, b["serves"], o["completed"], "ai", [], kind="ai_practice"))
        steps += added
        for s in steps:
            s["sdg"] = sdg.step_tags(s, CAT)
        open_steps = [s for s in steps if not s["completed"]]
        provider = sum(s["provider_hours"]["v"] for s in open_steps)
        practice = sum(s["practice_hours"]["v"] for s in open_steps)
        served = b["served"]
        uncovered = [g for g in b["prioritized"] if g["dwa_id"] not in served]
        all_supported_served = b["supported"] <= served
        reqs = []
        for r in dest["requirements"]:
            status, why = self.requirement_status(r, profile, o)
            reqs.append({**r, "status": status, "why": why})
        masco = self.rep.masco(code)
        step_pay, cur_major, _ = self.rep.pay_step_fn(profile.get("title_code"))
        pay = step_pay(code)
        median = self.rep.median(masco.get("major"))
        trend = outlook.group_trend(self.snaps, masco.get("major"))
        description = onet.descriptions.get(code, "")
        job_outlook = self.rep.job_outlook(code)
        learn = self.resources(dest["family"], b["prioritized"], courses, ai_courses, {s["id"] for s in steps}, served)
        return {
            "code": code, "title": onet.occupations[code], "family": dest["family"], "family_label": dest["family_label"],
            "description": {"text": description, "first": description.split(". ")[0].rstrip(".") + "." if description else "",
                            "src": ["onet31-occupations"]},
            "ai_use": self.ai_use(code, prof, o, months),
            "future": self.future(code, masco),
            "outlook_score": outlook.band_score(job_outlook.get("band")),
            "job_zone": fig(onet.job_zones.get(code), "onet31-job-zones"),
            "overlap": fig(pct(self.m.overlap(code, prof)), *ONET_SRC),
            "done_count": fig(len(done), *ONET_SRC), "total_count": fig(total, *ONET_SRC),
            "related": [{"dwa_id": d, "name": onet.dwa_names[d], "role_dwa_id": r, "role_name": onet.dwa_names[r]}
                        for d, r in self.credited(code, prof)[1]],
            "done": [{**d, "weight": fig(d["weight"], *ONET_SRC), "sdg": ["4.4"]} for d in done],
            "requirements": reqs, "masco": masco, "job_outlook": job_outlook, "local_evidence": LOCAL_EVIDENCE,
            "pay": None if pay is None else {"step": fig(pay, "dosm-wages-occupation", "esco-onet-isco")},
            "median": None if median is None else fig(median, *PAY_SRC),
            "sdg": [{"goal": "8.5", "reason": self.rep._sdg85(masco, pay), "trend": trend and trend["text"],
                     "src": [sdg.SRC, "esco-onet-isco"] + (["dosm-wages-occupation"] if pay is not None or trend else [])}],
            "contribution": self.contribution(steps, served, masco, cur_major, pay, trend, provider + practice),
            "prioritized": [{"dwa_id": g["dwa_id"], "name": g["name"], "weight": fig(g["weight"], *ONET_SRC),
                             "served": g["dwa_id"] in served, "has_resource": g["dwa_id"] in b["supported"], "sdg": ["4.4"]}
                            for g in b["prioritized"]],
            "resources": learn,
            "supported_share": fig(pct(len(b["supported"]) / len(b["prioritized"])) if b["prioritized"] else 0.0, CAT, *ONET_SRC),
            "block": {"steps": steps, "effort_hours": fig(round(provider + practice, 1), CAT),
                      "provider_hours": fig(round(provider, 1), CAT), "practice_hours": fig(round(practice, 1), CAT, estimate=True),
                      "cost_rm": fig(round(spent, 2), CAT), "capacity_hours": fig(o["capacity"], o["input_src"]),
                      "gaps_served": sorted(served), "uncovered": [{"dwa_id": g["dwa_id"], "name": g["name"]} for g in uncovered],
                      "status": ("covers all prioritized gaps that have a resource" if all_supported_served else "partial"),
                      "scenario_differs": differ,
                      "scenario_extra_hours": {s: fig(round(sum(effort(c) for c in extra[s]), 1), CAT) for s in outlook.SCENARIOS},
                      "base_hours": fig(round(b["used"], 1), CAT),
                      "horizon": {"months": fig(round(months, 1), o["input_src"], estimate=True), "basis": basis}},
        }

    def contribution(self, steps, served, masco, cur_major, pay, trend, hours):
        """How this path contributes (SPEC 11.5): counts from the block and the skill-level step, with the national
        baseline and SDG 5 context alongside. Nothing here is a score."""
        cur = self.rep.masco_major(cur_major)
        n_ai = sum(1 for s in steps if s["form"] == "ai" and s["kind"] != "ai_practice")
        n_practice = sum(1 for s in steps if s["kind"] == "ai_practice")
        base = self.rep._baseline() or {}
        return {"s85": {"from": cur, "to": masco, "step": None if pay is None else fig(pay, *PAY_SRC)},
                "s44": {"gaps": fig(len(served), *ONET_SRC, CAT), "hours": fig(round(hours, 1), CAT)},
                "s43": {"free": fig(sum(1 for s in steps if s["cost_rm"]["v"] == 0), CAT), "of": fig(len(steps), CAT)},
                "s82": {"checkpoints": fig(n_ai, CAT, *[f"aei-{r}" for r in self.snaps.evidence.ids]),
                        "practice": fig(n_practice, CAT)},
                "trend": trend, "baseline": outlook.sru_trend(self.snaps),
                "s5": base and {"female": base["rate_female"], "male": base["rate_male"], "date": base["date"]},
                "src": [sdg.SRC]}

    # ---- compare ---------------------------------------------------------------------------------
    def routes(self, profile, opts, today=None, explore=False, with_radar=True):
        o = self.options(opts, profile)
        o["input_src"] = EXPLORE_SRC if explore else "your-input"
        verified = pathway.verified_courses(self.snaps.catalogue, today or dt.date.today())
        courses = [c for c in verified if c.get("checkpoint") and not c.get("ai_skill")]
        # AI-practice entries that teach AI use on a named activity (copies; the snapshot stays untouched)
        ai_courses = [{**c, "practice_hours": c.get("practice_hours", 0.5),
                       "checkpoint": c.get("checkpoint") or "Use the AI tool on one task from your own work and check every "
                                                             "output it gives against your own answer."}
                      for c in verified if c.get("ai_skill") and c.get("dwas")]
        prof = self.m.lane_profile(profile["activities"], "all")
        fits = []
        for code in sorted(self.dests):
            if code == profile.get("title_code"):
                continue
            direct, related = self.credited(code, prof)
            ov = self.m.overlap(code, prof)
            if ov > 0 and len(direct) + len(related) >= MIN_DONE:
                fits.append((ov, code))
        fits.sort(key=lambda t: (-t[0], t[1]))
        chosen = sorted(self.dests) if explore else [c for _, c in fits[:MAX_ROUTES]]
        if o["target"] and o["target"] not in chosen:
            chosen.append(o["target"])
        routes = [self.route(c, prof, profile, courses, ai_courses, o) for c in chosen]
        src = o["input_src"]
        return {"routes": routes, "shared": None if explore else self.shared(routes), "target": o["target"], "personal": not explore,
                "weeks": fig(o["weeks"], "your-input"), "capacity_hours": fig(o["capacity"], src),
                "budget_rm": fig(o["budget"], src), "scenario": o["scenario"], "release": o["release"],
                "threshold": fig(o["threshold"], "your-input"), "willing_long": o["willing_long"],
                "fewer_note": (None if explore or len(fits) >= MAX_ROUTES else
                               f"{len(fits)} supported route{'s' if len(fits) != 1 else ''} qualified. Explore all roles to see others, "
                               "without complete-route support."),
                "supported_destinations": fig(len(self.dests), ROUTES_SRC),
                "decent_order": decent_order(routes),
                "exploration": [] if explore or len(fits) >= 2 or not with_radar else self.exploration_cards(profile, set(chosen), today or dt.date.today()),
                "forecast": self.rep._forecast_meta(o["threshold"]),
                "radar": self.radar(routes, prof, o, explore) if with_radar else None}

    # ---- Future Skills Radar (SPEC 11.4) ---------------------------------------------------------------
    def radar(self, routes, prof, o, explore):
        """The Radar tab: jobs (F1 with F4 per family), the person's skills (F2 against F3), the gaps whose
        checkpoint adds AI checking, and how sure each signal is. Signals stay separate; nothing is combined."""
        onet = self.snaps.onet
        target = next((r for r in routes if r["code"] == o["target"]), None)
        if o["horizon"] != "plan":
            months, basis, hz = o["horizon"], "your choice", fig(o["horizon"], "your-input", estimate=True)
        elif target:
            hz = target["block"]["horizon"]["months"]
            months, basis = hz["v"], "your plan's estimated planning horizon"
        else:
            months, basis = outlook.MIN_HORIZON_MONTHS, "minimum look-ahead (assumption)"
            hz = fig(months, "your-input", estimate=True)
        picked = {}
        if explore:
            for r in routes:
                top = sorted(self.m.w[r["code"]].items(), key=lambda kv: (-kv[1], kv[0]))[:RADAR_MAIN]
                for d, _ in top:
                    picked.setdefault(d, {"kind": "main", "routes": []})["routes"].append(r["code"])
        else:
            for d in sorted(prof):
                picked[d] = {"kind": "you", "routes": [r["code"] for r in routes if d in self.m.w[r["code"]]]}
            for r in routes:
                for g in r["prioritized"]:
                    if g["dwa_id"] not in prof:
                        picked.setdefault(g["dwa_id"], {"kind": "gap", "routes": []})["routes"].append(r["code"])
        kind_text = {"you": "you do this", "gap": "still to build", "main": "main activity"}
        skills = []
        for d, p in picked.items():
            row = self.rep.skill_row(d, onet.dwa_names[d], o["release"], months, o["threshold"], o["pace"], kind_text[p["kind"]])
            skills.append({**row, "group": p["kind"], "routes": p["routes"]})

        jobs = []
        for r in routes:
            jo = r["job_outlook"]
            jobs.append({"code": r["code"], "title": r["title"], "family": r["family"], "family_label": r["family_label"],
                         "group": job_group(jo.get("band")), "band": jo.get("band"), "period": jo.get("period"),
                         "openings": jo.get("openings"), "openings_period": jo.get("openings_period"),
                         "bright": jo.get("bright", []), "src": jo["src"]})
        trends, seen = [], set()
        for r in routes:
            major = r["masco"].get("major")
            if (r["family"], major) in seen or not major:
                continue
            seen.add((r["family"], major))
            t = outlook.group_trend(self.snaps, major)
            if t:
                trends.append({"family": r["family"], "family_label": r["family_label"], **t})

        # Gaps whose block checkpoint adds AI checking in the selected scenario, once per activity with every route
        # and step it appears in (each route's own planning horizon decided it).
        changing = {}
        for r in routes:
            for s in r["block"]["steps"]:
                if s["form"] != "ai":
                    continue
                for g in s["reason"]:
                    if g["projected"][o["scenario"]]["v"] < o["threshold"]:
                        continue
                    c = changing.setdefault(g["dwa_id"], {
                        "dwa_id": g["dwa_id"], "name": g["name"], "now": g["now"], "weak": g["weak"], "where": [],
                        "ttt": self.rep.ttt(g["now"]["v"], g["now"]["src"], o["threshold"], o["pace"])[o["scenario"]]})
                    c["where"].append({"route": r["code"], "route_title": r["title"], "step": s["title"],
                                       "projected": g["projected"][o["scenario"]],
                                       "horizon_months": r["block"]["horizon"]["months"]})
        changing = sorted(changing.values(), key=lambda c: (-c["now"]["v"], c["dwa_id"]))
        return {"horizon": {"months": hz, "basis": basis},
                "scenario": o["scenario"], "threshold": fig(o["threshold"], "your-input"), "release": o["release"],
                "jobs": jobs, "trends": trends, "skills": skills, "changing": changing,
                "sru_trend": outlook.sru_trend(self.snaps), "sure": self.rep.forecast_basis(o["pace"])}

    def exploration_cards(self, profile, shown, today):
        """No dead end (SPEC 11.7): with fewer than two qualifying routes, up to three of the closest skilled roles
        from the exploration ranking (sections 3-7, all lanes) that have no curated first block, each opening to its
        exploration detail. Ranked by activity overlap, like the exploration view itself."""
        try:
            rep = self.rep.report(profile, {"lane": "all"}, today=today)
        except ReportError:
            return []
        onet = self.snaps.onet
        out = []
        for r in rep["roles"]:
            if r["code"] in shown or r["code"] in self.dests:
                continue
            desc = onet.descriptions.get(r["code"], "")
            out.append({"code": r["code"], "title": r["title"], "label": EXPLORATION_LABEL,
                        "description": {"first": desc.split(". ")[0].rstrip(".") + "." if desc else "", "src": ["onet31-occupations"]},
                        "overlap": r["overlap"], "done_count": r["done_count"], "total_count": r["total_count"],
                        "job_outlook": r["job_outlook"], "masco": r["masco"], "rank": r["rank"]})
            if len(out) == EXPLORATION_CARDS:
                break
        return out

    def explore(self, opts, today=None):
        """Every supported role before any work is described: nothing counts as done, requirements the profile
        would settle are left to the person, and the first block uses the starting assumptions."""
        return self.routes(EXPLORE_PROFILE, opts, today, explore=True)

    def shared(self, routes):
        if len(routes) < 2:
            return None
        sets = {r["code"]: {s["id"]: s for s in r["block"]["steps"] if s["kind"] == "learn" and s["serves"]} for r in routes}
        common = set.intersection(*(set(v) for v in sets.values()))
        any_two = {i for i in set().union(*sets.values()) if sum(i in v for v in sets.values()) >= 2}
        first = next(iter(sets.values()))

        def eff(step):
            return step["provider_hours"]["v"] + step["practice_hours"]["v"]

        common_hours = round(sum(eff(first[i]) for i in common if not first[i]["completed"]), 1)
        return {"common_steps": sorted(common), "shared_steps": sorted(any_two),
                "common_hours": fig(common_hours, CAT),
                "extra_hours": [{"code": r["code"], "title": r["title"],
                                 "hours": fig(round(r["block"]["effort_hours"]["v"] - common_hours, 1), CAT)} for r in routes]}

    # ---- what changed ----------------------------------------------------------------------------
    def compare(self, profile, opts, today=None):
        if not opts.get("target"):
            raise ReportError({"target": "Choose a target route first."})
        ids = self.snaps.evidence.ids
        frm, to = opts.get("from_release", ids[-2] if len(ids) > 1 else ids[-1]), opts.get("to_release", ids[-1])
        if frm not in ids or to not in ids:
            raise ReportError({"release": "Choose two of the bundled releases."})
        before = self.routes(profile, {**opts, "release": frm}, today, with_radar=False)
        after = self.routes(profile, {**opts, "release": to}, today, with_radar=False)
        a = next(r for r in before["routes"] if r["code"] == opts["target"])
        b = next(r for r in after["routes"] if r["code"] == opts["target"])
        old = {s["id"]: s for s in a["block"]["steps"]}
        new = {s["id"]: s for s in b["block"]["steps"]}
        changes = []
        for sid in list(old) + [i for i in new if i not in old]:
            s0, s1 = old.get(sid), new.get(sid)
            if s0 and not s1:
                kind = "removed"
            elif s1 and not s0:
                kind = "added"
            elif s0["form"] != s1["form"]:
                kind = "changed form"
            else:
                kind = "retained"
            ref = s1 or s0
            changes.append({"id": sid, "title": ref["title"], "change": kind, "completed": ref["completed"],
                            "before": s0 and {"form": s0["form"], "reason": s0["reason"]},
                            "after": s1 and {"form": s1["form"], "reason": s1["reason"]}})
        moved = [c for c in changes if c["change"] != "retained"]
        return {"target": opts["target"], "title": b["title"], "from_release": frm, "to_release": to,
                "changes": changes, "changed_count": len(moved),
                "summary": (f"{len(moved)} step{'s' if len(moved) != 1 else ''} changed between the "
                                                f"{frm} and {to} evidence." if moved else
                                                f"No change: the {frm} and {to} evidence lead to the same plan for this target."),
                "hours_before": a["block"]["effort_hours"], "hours_after": b["block"]["effort_hours"],
                "cost_before": a["block"]["cost_rm"], "cost_after": b["block"]["cost_rm"],
                "comparability_note": aei.COMPARABILITY.get(to)}

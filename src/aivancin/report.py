"""Assemble the ranked roles with their evidence (SPEC sections 3 to 7).

Every displayed number is a figure {"v": value, "src": [source_id, ...]}; source ids resolve through
the manifest (plus "your-input" for what the user entered). Explanations are templates over computed facts.
"""
import datetime as dt

from . import aei, pathway
from .matching import LANES, soc6
from .snapshots import MASCO_GROUPS, skill_level

ONET_SRC = ["onet31-task-dwas", "onet31-tasks"]
PAY_SRC = ["dosm-wages-occupation", "esco-onet-isco"]
ROLE_WEAK_COVERAGE = 0.25  # initial: role evidence is weak when non-weak task evidence covers under a quarter of its task weight
SCENARIOS = ("moderate", "faster")


class ReportError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def fig(v, *src, **extra):
    return {"v": v, "src": list(src), **extra}


def pct(x):
    return round(100 * x, 1)


class Reporter:
    def __init__(self, snaps, matcher):
        self.snaps = snaps
        self.m = matcher
        onet = snaps.onet
        self.task_weights = {}
        for (code, tid), t in onet.tasks.items():
            self.task_weights.setdefault(code, []).append((tid, 0.5 if t["type"] == "Supplemental" else 1.0))
        self._ai_cache = {}

    # ---- AI-usage evidence -------------------------------------------------------------------------
    def role_ai(self, code, release):
        key = (code, release)
        if key in self._ai_cache:
            return self._ai_cache[key]
        ev = self.snaps.evidence
        total = num = covered = strong = 0.0
        cells = []
        for tid, w in self.task_weights.get(code, []):
            total += w
            e = ev.task(tid, release)
            if e is None:
                continue
            covered += w
            num += w * e["share"]
            if not e["weak"]:
                strong += w
            cells.append({"task_id": tid, "task": self.snaps.onet.tasks[(code, tid)]["task"], "weight": w, **e})
        if not covered:
            out = None
        else:
            srcs = sorted({f"aei-{r['release']}" for c in cells for r in c["releases"]})
            cells.sort(key=lambda c: (-c["weight"], -c["share"], c["task_id"]))
            out = {"share": fig(round(num / covered, 1), *srcs),
                   "coverage": fig(pct(covered / total), *srcs, "onet31-tasks"),
                   "weak": strong < ROLE_WEAK_COVERAGE * total,
                   "tasks": [{**c, "share": fig(c["share"], *[f"aei-{r['release']}" for r in c["releases"]])}
                             for c in cells]}
        self._ai_cache[key] = out
        return out

    def ai_share_value(self, release):
        def f(code):
            r = self.role_ai(code, release)
            return r["share"]["v"] if r else None
        return f

    # ---- Pay step and skill level ------------------------------------------------------------------
    def masco(self, code):
        m = self.snaps.isco.get(code)
        major = m["major"] if m else None
        if not major or major not in MASCO_GROUPS:
            return {"major": None, "label": "skill level unmapped", "mapping": m["mapping"] if m else "no ESCO match"}
        return {"major": major, "group": MASCO_GROUPS[major][1], "skill": skill_level(major), "mapping": m["mapping"],
                "label": f"MASCO major group {major}, {MASCO_GROUPS[major][1]} ({skill_level(major)})"}

    def median(self, major):
        if not major or major not in MASCO_GROUPS:
            return None
        rec = self.snaps.wages.get((MASCO_GROUPS[major][0], "both"))
        return rec["median"] if rec else None

    def pay_step_fn(self, current_code):
        cur = self.masco(current_code)["major"] if current_code else None
        base = self.median(cur)

        def f(code):
            tgt = self.median(self.masco(code)["major"])
            return None if base is None or tgt is None else tgt - base
        return f, cur, base

    # ---- Requirement labels ------------------------------------------------------------------------
    def labels(self, code, profile):
        onet = self.snaps.onet
        out = []
        cip = self.snaps.cip.get(profile.get("cip_code") or "")
        if cip and soc6(code) in cip["soc"]:
            out.append({"id": "fits_studies", "text": f"Fits your studies ({cip['title']})", "src": ["nces-cip-soc"]})
        if onet.job_zones.get(code) == 4 and profile["degree_level"] == "diploma":
            out.append({"id": "needs_degree", "text": "Usually needs a bachelor's degree (O*NET Job Zone 4)",
                        "src": ["onet31-job-zones"]})
        rw = onet.experience.get(code)
        if onet.occupations[code].startswith("First-Line Supervisors"):
            out.append({"id": "needs_experience", "text": "Needs related experience (a supervisor role)",
                        "src": ["onet31-occupations"]})
        elif rw and rw[0] >= 6:
            out.append({"id": "needs_experience",
                        "text": f"Needs related experience (most common answer: over {EXPERIENCE_YEARS[rw[0]]})",
                        "src": ["onet31-experience"], "pct": fig(rw[1], "onet31-experience")})
        for req in self.snaps.catalogue.get("requirements", []):
            if req["code"] == code:
                out.append({"id": "needs_licence", "text": req["text"], "src": ["aivancin-catalogue"], "url": req["url"]})
        return out

    # ---- Full report -------------------------------------------------------------------------------
    def report(self, profile, opts, today=None):
        errors = {}
        lane = opts.get("lane", "all")
        if lane not in LANES:
            errors["lane"] = "Choose a lane: current job, other work, studies or all."
        lam = _number(opts.get("lambda", 0.0), 0.0, 0.5, "lambda", "The AI-evidence weight must be between 0 and 0.5.", errors)
        mu = _number(opts.get("mu", 0.0), 0.0, 1.0, "mu", "The decent-work priority must be between 0 and 1.", errors)
        threshold = _number(opts.get("threshold", pathway.DEFAULT_THRESHOLD), 1, 100, "threshold",
                            "The threshold must be between 1 and 100.", errors)
        pace = _number(opts.get("pace", pathway.DEFAULT_PACE), 1.0, 3.0, "pace",
                       "The faster-scenario pace must be between 1.0 and 3.0.", errors)
        scenario = opts.get("scenario", "moderate")
        if scenario not in SCENARIOS:
            errors["scenario"] = "Choose the moderate or faster scenario."
        ev = self.snaps.evidence
        release = opts.get("release") or (ev.ids[-1] if ev.ids else None)
        if release and release not in ev.ids:
            errors["release"] = "Choose one of the bundled releases."
        if lane == "studies" and not profile.get("cip_code"):
            errors["lane"] = "Choose what you studied to use the studies lane."
        if errors:
            raise ReportError(errors)

        known_tools = {t.lower() for t in opts.get("known_tools") or []}
        current = profile.get("title_code")
        pay_step, cur_major, cur_median = self.pay_step_fn(current)
        prof = self.m.lane_profile(profile["activities"], lane)
        if lane != "studies" and not prof:
            raise ReportError({"lane": "No confirmed activity is in this lane yet. Choose another lane or tick more activities."})
        ai_share = self.ai_share_value(release)
        args = dict(current_code=current, cip_code=profile.get("cip_code"), ai_share=ai_share, pay_step=pay_step)
        ranked = self.m.rank(prof, lane, lam=lam, mu=mu, **args)
        base = self.m.rank(prof, lane, lam=0.0, mu=0.0, **args)
        base_pos = {r["code"]: i + 1 for i, r in enumerate(base["ranked"])}

        catalogue_today = today or dt.date.today()
        courses = pathway.verified_courses(self.snaps.catalogue, catalogue_today)
        roles = []
        for i, r in enumerate(ranked["roles"], 1):
            roles.append(self._role(r, i, base_pos, prof, profile, opts, release, scenario, threshold, pace,
                                    pay_step, known_tools, courses))

        replay = self._replay(prof, lane, lam, mu, args, release, ranked)
        return {
            "heading": "closest roles to explore",
            "lane": lane, "release": release, "scenario": scenario,
            "weights": {"lambda": fig(lam, "your-input"), "mu": fig(mu, "your-input")},
            "threshold": fig(threshold, "your-input"), "pace": fig(pace, "aei-report-2511.15080", "your-input"),
            "without_ai_suggestions": profile.get("without_ai_suggestions", False),
            "profile_size": fig(len(prof), "your-input"),
            "candidate_pool": fig(ranked["pool_size"], "onet31-job-zones", "onet31-task-dwas"),
            "no_overlap_listed": ranked["no_overlap_listed"],
            "current": self._current(current, cur_major, cur_median),
            "roles": roles,
            "replay": replay,
            "baseline": self._baseline(),
            "rejected_releases": self.snaps.rejected,
        }

    def _role(self, r, rank, base_pos, prof, profile, opts, release, scenario, threshold, pace, pay_step,
              known_tools, courses):
        code = r["code"]
        onet = self.snaps.onet
        done, missing = self.m.done_and_missing(code, prof)
        total = len(done) + len(missing)
        tools = [t for t in onet.software.get(code, []) if t["name"].lower() not in known_tools]
        tool_gaps = [{"name": t["name"], "commodity": t["commodity"], "hot": t["hot"]} for t in tools]
        ai = self.role_ai(code, release)
        occ_fig = self.snaps.evidence.occupation(code, release)
        masco = self.masco(code)
        step = pay_step(code)
        tgt_median = self.median(masco["major"])
        plan = pathway.build_plan(missing, tool_gaps, courses, profile["budget_rm"], profile["weekly_hours"])
        checks = pathway.scenario_check(code, missing, self.m, self.snaps.evidence, release, plan["_months"],
                                        threshold, pace)
        extra = pathway.ai_steps(checks, scenario, courses, profile["budget_rm"] - plan["_spent"])
        changes = [c for c in checks if c["crosses"]["moderate"] != c["crosses"]["faster"]]
        sdg = [{"goal": "8.5", "reason": self._sdg85(masco, step)}]
        sdg_src = ["dosm-wages-occupation", "esco-onet-isco"] if step is not None else ["esco-onet-isco"]
        overlap = fig(pct(r["overlap"]), *ONET_SRC)
        return {
            "rank": rank, "rank_without_weights": base_pos.get(code), "code": code, "title": onet.occupations[code],
            "job_zone": fig(onet.job_zones.get(code), "onet31-job-zones"),
            "overlap": overlap,
            "done_count": fig(len(done), *ONET_SRC), "total_count": fig(total, *ONET_SRC),
            "summary": f"You do {len(done)} of its {total} recorded activities (activity overlap {overlap['v']}%).",
            "score": round(r["score"], 4),
            "done": [{**d, "weight": fig(d["weight"], *ONET_SRC)} for d in done],
            "missing": [{**d, "weight": fig(d["weight"], *ONET_SRC)} for d in missing],
            "tool_gaps": [{**t, "src": ["onet31-software"]} for t in tool_gaps],
            "labels": self.labels(code, profile),
            "masco": masco,
            "ai": ai and {**ai, "tasks": ai["tasks"][:12], "task_count": len(ai["tasks"])},
            "ai_occupation": occ_fig and {"share": fig(occ_fig["share"], f"aei-{occ_fig['release']}"),
                                          "release": occ_fig["release"]},
            "pay": None if step is None else {"step": fig(step, *PAY_SRC), "target_median": fig(tgt_median, *PAY_SRC)},
            "emerging": {"tasks": [{**t, "src": ["onet31-emerging-tasks"]} for t in onet.emerging.get(code, [])],
                         "technologies": [{**t, "src": ["onet31-software"]} for t in onet.software.get(code, [])]},
            "sdg": [{**s, "src": sdg_src} for s in sdg],
            "plan": {k: v for k, v in plan.items() if not k.startswith("_")} | {"ai_steps": extra},
            "scenario_checks": checks,
            "scenario_notes": [f"In the faster scenario, {c['name'].rstrip('.')} reaches the {threshold:g}% threshold by "
                               f"the ready date, so the plan adds a step to direct and check AI output on it."
                               for c in changes if c["crosses"]["faster"]] or
                              ["No change needed in this scenario: no gap activity in the plan crosses the threshold "
                               "differently in the moderate and faster scenarios."],
        }

    def _sdg85(self, masco, step):
        if not masco["major"]:
            return "Skill level unmapped for this role, so no SDG 8.5 skill-level claim is made."
        level = masco["skill"]
        text = f"A {level} job (MASCO major group {masco['major']})"
        if level == "skilled":
            text += ", which counts as skill-matched work in DOSM's skill-related underemployment measure"
        if step is not None:
            sign = "+" if step >= 0 else "-"
            text += f"; DOSM 2025 median pay step {sign}RM{abs(step):,} a month against your current group"
        return text + "."

    def _current(self, code, major, median):
        if not code:
            return {"title": None, "note": "Choose your current job title to see the pay step."}
        m = self.masco(code)
        return {"title": self.snaps.onet.occupations[code], "masco": m,
                "median": None if median is None else fig(median, *PAY_SRC)}

    def _replay(self, prof, lane, lam, mu, args, release, ranked):
        ids = self.snaps.evidence.ids
        if not release or ids.index(release) == 0:
            return {"release": release, "previous": None, "position_changes": None, "deltas": [],
                    "comparability_note": aei.COMPARABILITY.get(release)}
        prev = ids[ids.index(release) - 1]
        before = self.m.rank(prof, lane, lam=lam, mu=mu, **{**args, "ai_share": self.ai_share_value(prev)})
        prev_order = [r["code"] for r in before["roles"]]
        now_order = [r["code"] for r in ranked["roles"]]
        deltas = []
        for code in now_order:
            a, b = self.role_ai(code, release), self.role_ai(code, prev)
            if a and b:
                deltas.append({"code": code, "delta": fig(round(a["share"]["v"] - b["share"]["v"], 1),
                                                          *sorted(set(a["share"]["src"]) | set(b["share"]["src"])))})
        changed = prev_order != now_order
        return {"release": release, "previous": prev, "position_changes": changed,
                "message": "Positions changed with this release." if changed else
                           "No position changes with this release." + ("" if lam > 0 else
                                                                        " (The AI-evidence weight is 0, so releases never reorder roles.)"),
                "deltas": deltas, "comparability_note": aei.COMPARABILITY.get(release)}

    def _baseline(self):
        sru = self.snaps.sru
        if not sru:
            return None
        rate, date = sru["age"].get(("rate", "25-34"), (None, None))
        persons, _ = sru["age"].get(("persons", "25-34"), (None, None))
        f, _ = sru["sex"].get(("rate", "female"), (None, None))
        m, _ = sru["sex"].get(("rate", "male"), (None, None))
        return {"date": date, "rate_25_34": fig(rate, "dosm-sru-age"), "persons_25_34_k": fig(persons, "dosm-sru-age"),
                "rate_female": fig(f, "dosm-sru-sex"), "rate_male": fig(m, "dosm-sru-sex")}


EXPERIENCE_YEARS = {6: "1 year", 7: "2 years", 8: "4 years", 9: "6 years", 10: "8 years", 11: "10 years"}


def _number(value, lo, hi, key, message, errors):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        errors[key] = message
        return lo
    return float(value)

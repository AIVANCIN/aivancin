"""Assemble the ranked roles with their evidence (SPEC sections 3 to 7).

Every displayed number is a figure {"v": value, "src": [source_id, ...]}; source ids resolve through
the manifest (plus "your-input" for what the user entered). Explanations are templates over computed facts.
"""
import datetime as dt

from . import aei, outlook, pathway
from .matching import LANES, soc6
from .snapshots import MASCO_GROUPS, skill_level

ONET_SRC = ["onet31-task-dwas", "onet31-tasks"]
PAY_SRC = ["dosm-wages-occupation", "esco-onet-isco"]
ROLE_WEAK_COVERAGE = 0.25  # initial: role evidence is weak when non-weak task evidence covers under a quarter of its task weight
SCENARIOS = outlook.SCENARIOS
OUTLOOK_SRC = "onet-online-outlook"
BACKTEST_SRC = "aivancin-backtest"
BLS_EVAL_SRC = "bls-eval-2014-2024"  # wording checked on bls.gov, 21 Oct 2026
REGISTERED_SRC = "aivancin-registered-forecasts"


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
        self.skills = outlook.SkillOutlook(snaps)
        self.demand = outlook.DemandOutlook(snaps, matcher)

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
        nu = _number(opts.get("nu", 0.0), 0.0, 1.0, "nu", "The job-outlook weight must be between 0 and 1.", errors)
        scenario = opts.get("scenario", "moderate")
        if scenario not in SCENARIOS:
            errors["scenario"] = "Choose the slower, moderate or faster scenario."
        horizon = opts.get("horizon", "plan")
        if horizon != "plan" and horizon not in outlook.HORIZON_CHOICES:
            errors["horizon"] = "Choose the estimated planning horizon or 12, 24, 36 or 60 months."
        scope = opts.get("scope", "skilled")
        if scope not in ("skilled", "all"):
            errors["scope"] = "Choose skilled roles only or all roles."
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
        args = dict(current_code=current, cip_code=profile.get("cip_code"), ai_share=ai_share, pay_step=pay_step,
                    outlook=self.outlook_score, allow=self.is_skilled if scope == "skilled" else None)
        ranked = self.m.rank(prof, lane, lam=lam, mu=mu, nu=nu, **args)
        base = self.m.rank(prof, lane, lam=0.0, mu=0.0, nu=0.0, **args)
        base_pos = {r["code"]: i + 1 for i, r in enumerate(base["ranked"])}

        catalogue_today = today or dt.date.today()
        courses = pathway.verified_courses(self.snaps.catalogue, catalogue_today)
        roles = []
        for i, r in enumerate(ranked["roles"], 1):
            roles.append(self._role(r, i, base_pos, prof, profile, opts, release, scenario, threshold, pace,
                                    pay_step, known_tools, courses, horizon))

        replay = self._replay(prof, lane, lam, mu, nu, args, release, ranked)
        return {
            "heading": "closest roles to explore",
            "lane": lane, "release": release, "scenario": scenario,
            "weights": {"lambda": fig(lam, "your-input"), "mu": fig(mu, "your-input"), "nu": fig(nu, "your-input")},
            "order": "outlook" if nu > 0 else "closest",
            "scope": scope,
            "scope_note": ("Skilled roles only (MASCO major groups 1-3, the DOSM skilled category); roles whose skill level "
                           "is unmapped are left out rather than guessed." if scope == "skilled" else
                           "Exploration: includes semi-skilled and unmapped roles; their skill level is shown on each role."),
            "horizon": horizon,
            "forecast": self._forecast_meta(threshold),
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

    def is_skilled(self, code):
        return self.masco(code).get("skill") == "skilled"

    def outlook_score(self, code):
        rec = self.snaps.outlook.get(code)
        return outlook.band_score(rec["band"]) if rec else 0.0

    def job_outlook(self, code):
        rec = self.snaps.outlook.get(code)
        if not rec or not rec["band"]:
            return {"band": None, "note": "No projection", "bright": rec["bright"] if rec else [], "src": [OUTLOOK_SRC]}
        return {"band": rec["band"], "period": rec["period"], "score": outlook.band_score(rec["band"]),
                "openings": fig(rec["openings"], OUTLOOK_SRC) if rec["openings"] is not None else None,
                "openings_period": rec["openings_period"], "bright": rec["bright"], "scope": "US projection",
                "src": [OUTLOOK_SRC]}

    def skill_row(self, dwa_id, name, release, months, threshold, pace, kind):
        o = self.skills.outlook(dwa_id, release)
        row = {"dwa_id": dwa_id, "name": name, "kind": kind, "demand": self.demand_fig(dwa_id)}
        if o is None:
            return {**row, "now": None, "classes": {s: outlook.classify(None, threshold) for s in SCENARIOS},
                    "ttt": self.ttt(None, [], threshold, pace)}
        proj = {s: round(self.skills.project(o["now"], s, months, pace), 1) for s in SCENARIOS}
        src = o["src"]
        return {**row, "now": fig(o["now"], *src), "weak": o["weak"],
                "releases": [{**x, "share": fig(x["share"], f"aei-{x['release']}")} for x in o["releases"]],
                # slower's drop is computed from the bundled releases; faster's pace is the cited report's, editable
                "projected": {s: fig(v, *src, *(["aei-report-2511.15080", "your-input"] if s == "faster" else []))
                              for s, v in proj.items()},
                "classes": {s: outlook.classify(v, threshold) for s, v in proj.items()},
                "ttt": self.ttt(o["now"], src, threshold, pace)}

    def ttt(self, now, src, threshold, pace):
        """Time to threshold per scenario (SPEC 11.1), an estimate under each stated scenario."""
        out = {}
        for s in SCENARIOS:
            months, text = outlook.time_to_threshold(now, threshold, s, pace, self.skills.drop)
            extra = ["aei-report-2511.15080", "your-input"] if s == "faster" else ["your-input"]
            out[s] = {"months": None if months is None else fig(months, *src, *extra, estimate=True), "text": text}
        return out

    def demand_fig(self, dwa_id):
        """F2 with every number as a sourced figure."""
        d = self.demand.get(dwa_id)
        src = d["src"]
        if not d["roles"]:
            return {"label": d["label"], "roles": fig(0, *src), "src": src}
        return {"label": d["label"], "grow": fig(pct(d["grow"]), *src), "decline": fig(pct(d["decline"]), *src),
                "net": fig(d["net"], *src), "roles": fig(d["roles"], *src), "few": d["few"],
                "openings": fig(d["openings"], *src), "openings_period": d["openings_period"], "src": src}

    def _forecast_meta(self, threshold):
        bt = self.snaps.backtest or {}
        p = bt.get("primary", {})
        return {"error_mae": fig(p.get("mae", {}).get("mean"), BACKTEST_SRC) if p else None,
                "error_p80": fig(p.get("mean_abs_error_p80"), BACKTEST_SRC) if p else None,
                "calibrated": bt.get("coverage", {}).get("calibrated"),
                "direction_labels": bool(bt.get("direction_gate_pass")),
                "slower_drop": fig(self.skills.drop, *[f"aei-{r}" for r in self.snaps.evidence.ids]),
                "lower_cut": fig(outlook.LOWER_CUT, "your-input"), "threshold": fig(threshold, "your-input"),
                "degree_months": fig(outlook.DEGREE_TOPUP_MONTHS, "your-input")}

    def forecast_basis(self, pace):
        """How sure each signal is (SPEC 11.4): the backtest, BLS's own evaluation, the registered forecast,
        the scenario bases and the release means, each with its source and date."""
        bt = self.snaps.backtest or {}
        p = bt.get("primary", {})
        ev = self.snaps.evidence
        rel_src = [f"aei-{r}" for r in ev.ids]
        reg = self.snaps.registered or {}
        reg_rec = next((r for r in self.snaps.manifest["snapshots"] if r["source_id"] == "aivancin-registered-forecasts"), None)
        drop = self.skills.drop
        n = self.skills.common_tasks
        return {
            "backtest": p and {"units": fig(bt.get("units"), BACKTEST_SRC), "target": p.get("target"),
                               "mae_mean": fig(p["mae"]["mean"], BACKTEST_SRC), "mae_persistence": fig(p["mae"]["persistence"], BACKTEST_SRC),
                               "mae_line": fig(p["mae"]["line"], BACKTEST_SRC), "p80": fig(p["mean_abs_error_p80"], BACKTEST_SRC),
                               "calibrated": bt.get("coverage", {}).get("calibrated"),
                               "inside": fig(pct(bt.get("coverage", {}).get("share_inside", 0)), BACKTEST_SRC),
                               "direction_share": fig(pct(bt.get("direction", {}).get("same_sign_share") or 0), BACKTEST_SRC),
                               "direction_eligible": fig(bt.get("direction", {}).get("eligible"), BACKTEST_SRC),
                               "direction_labels": bool(bt.get("direction_gate_pass")), "run_date": bt.get("run_date")},
            "bls": {"quote": "BLS correctly projected whether an occupational group would grow or decline 86 percent of the time.",
                    "detailed": {"bls": fig(19.6, BLS_EVAL_SRC), "naive": fig(21.1, BLS_EVAL_SRC),
                                 "bls_better": fig(475, BLS_EVAL_SRC), "naive_better": fig(345, BLS_EVAL_SRC)},
                    "src": [BLS_EVAL_SRC]},
            "registered": reg and {"date": reg["registered_on"], "count": fig(reg["count"], REGISTERED_SRC),
                                   "latest_release": reg["latest_release"],
                                   "sha256": reg_rec["derived_sha256"] if reg_rec else None, "src": [REGISTERED_SRC]},
            "bases": {"slower": {"drop": fig(drop, *rel_src), "tasks": fig(n, *rel_src),
                                 "text": f"Slower (assumption): the now value minus {drop:g} points, the largest fall between "
                                         f"consecutive bundled releases in the mean share of the {n} tasks present in all of them."},
                      "moderate": {"text": "Moderate: the activity's own average across the bundled releases holds. In the "
                                           "backtest it was closer than the last value or a trend line."},
                      "faster": {"pace": fig(pace, "aei-report-2511.15080", "your-input"),
                                 "text": f"Faster (assumption): the now value grows {pace:g} times every eight months, the "
                                         "fastest recorded aggregate pace (directive use 27% to 39% in eight months), "
                                         "capped at 100%."}},
            "release_means": [{"release": rid, "mean": fig(round(m, 1), f"aei-{rid}", "onet31-tasks"),
                               "note": aei.COMPARABILITY.get(rid)} for rid, m in self.skills.means],
            "common_tasks": fig(n, *rel_src),
        }

    def _role(self, r, rank, base_pos, prof, profile, opts, release, scenario, threshold, pace, pay_step,
              known_tools, courses, horizon):
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
        labels = self.labels(code, profile)
        if horizon == "plan":
            months, basis = outlook.planning_months(plan["_months"], labels, onet.experience.get(code))
        else:
            months, basis = horizon, "your choice"
        hz_src = ["your-input"] + (["aivancin-catalogue"] if basis == "plan" else
                                    ["onet31-experience"] if basis == "related experience" else
                                    ["onet31-job-zones"] if basis.startswith("degree") else [])
        checks = pathway.scenario_check(missing, self.skills, release, months, threshold, pace)
        extra = pathway.ai_steps(checks, scenario, courses, profile["budget_rm"] - plan["_spent"], plan["_chosen"])
        sums = pathway.totals(plan["_chosen"], profile["weekly_hours"])
        radar = ([self.skill_row(d["dwa_id"], d["name"], release, months, threshold, pace, "you do this") for d in done]
                 + [self.skill_row(g["dwa_id"], g["name"], release, months, threshold, pace, "still to build")
                    for g in missing[:pathway.PLAN_GAPS]])
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
            "done": [{**d, "weight": fig(d["weight"], *ONET_SRC), "sdg": ["4.4"]} for d in done],
            "missing": [{**d, "weight": fig(d["weight"], *ONET_SRC), "sdg": ["4.4"]} for d in missing],
            "tool_gaps": [{**t, "src": ["onet31-software"]} for t in tool_gaps],
            "labels": labels,
            "job_outlook": self.job_outlook(code),
            "horizon": {"months": fig(round(months, 1), *hz_src, estimate=True), "basis": basis},
            "radar": radar,
            "masco": masco,
            "ai": ai and {**ai, "tasks": ai["tasks"][:12], "task_count": len(ai["tasks"])},
            "ai_occupation": occ_fig and {"share": fig(occ_fig["share"], f"aei-{occ_fig['release']}"),
                                          "release": occ_fig["release"]},
            "pay": None if step is None else {"step": fig(step, *PAY_SRC), "target_median": fig(tgt_median, *PAY_SRC)},
            "emerging": {"tasks": [{**t, "src": ["onet31-emerging-tasks"]} for t in onet.emerging.get(code, [])],
                         "technologies": [{**t, "src": ["onet31-software"]} for t in onet.software.get(code, [])]},
            "sdg": [{**s, "src": sdg_src} for s in sdg],
            "plan": {"steps": plan["steps"], "ai_steps": extra, **sums},
            "scenario_checks": checks,
            "scenario_notes": self._scenario_notes(checks, scenario, threshold),
        }

    def _scenario_notes(self, checks, scenario, threshold):
        notes = []
        for c in checks:
            yes = [s for s in SCENARIOS if c["crosses"][s]]
            if yes and len(yes) < len(SCENARIOS):
                notes.append(f"{c['name'].rstrip('.')}: reaches the {threshold:g}% threshold by the horizon only in the "
                             f"{' and '.join(yes)} scenario{'s' if len(yes) > 1 else ''}, so its direct-and-check step "
                             f"{'is in' if scenario in yes else 'is not in'} this plan.")
        if not notes:
            notes.append("No change needed across scenarios: every gap activity in the plan is on the same side of the "
                         "threshold in the slower, moderate and faster scenarios.")
        return notes

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

    def _replay(self, prof, lane, lam, mu, nu, args, release, ranked):
        ids = self.snaps.evidence.ids
        if not release or ids.index(release) == 0:
            return {"release": release, "previous": None, "position_changes": None, "deltas": [],
                    "comparability_note": aei.COMPARABILITY.get(release)}
        prev = ids[ids.index(release) - 1]
        before = self.m.rank(prof, lane, lam=lam, mu=mu, nu=nu, **{**args, "ai_share": self.ai_share_value(prev)})
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
                                                                        " (The AI-evidence weight is 0, so releases never reorder roles; "
                                                                        "the skill outlook and plan still use the chosen release.)"),
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

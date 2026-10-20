"""Reskilling plan from verified catalogue entries, and the scenario check on gap activities (SPEC sections 6 and 7)."""
import datetime as dt

PLAN_GAPS = 6
PLAN_TOOLS = 3
WEEKS_PER_MONTH = 52 / 12
DEFAULT_PACE = 1.444  # 27% to 39% directive use in eight months (AEI report, arXiv 2511.15080)
DEFAULT_THRESHOLD = 50.0
CATALOGUE_SRC = "aivancin-catalogue"
SCENARIOS = ("slower", "moderate", "faster")


def is_verified(course, today, validity_days):
    check = course.get("link_check") or {}
    if not check.get("ok") or not check.get("checked_on"):
        return False
    age = (today - dt.date.fromisoformat(check["checked_on"])).days
    return 0 <= age <= validity_days


def verified_courses(catalogue, today=None):
    today = today or dt.date.today()
    days = catalogue.get("validity_days", 60)
    return [c for c in catalogue.get("courses", []) if is_verified(c, today, days)]


def _course_view(c):
    return {"id": c["id"], "title": c["title"], "provider": c["provider"], "url": c["url"],
            "cost_rm": {"v": c["cost_rm"], "src": [CATALOGUE_SRC]}, "hours": {"v": c["hours"], "src": [CATALOGUE_SRC]},
            "checked_on": c["link_check"]["checked_on"]}


def build_plan(gaps, tool_gaps, courses, budget, weekly_hours):
    """Ordered learning steps for the largest-weight gaps, then up to three named tools.

    Every activity gap in the plan gets a row (a course or "no verified resource"); a tool joins the
    plan only when a verified entry teaches it, since roles list up to dozens of tools.
    """
    steps, chosen, spent, hours = [], {}, 0.0, 0.0
    taught = {x.lower() for c in courses for x in c.get("tools", [])}
    tools = [t for t in tool_gaps if t["name"].lower() in taught][:PLAN_TOOLS]
    targets = [("activity", g) for g in gaps[:PLAN_GAPS]] + [("tool", t) for t in tools]
    for kind, gap in targets:
        if kind == "activity":
            covering = [c for c in courses if gap["dwa_id"] in c.get("dwas", [])]
        else:
            covering = [c for c in courses if gap["name"].lower() in (x.lower() for x in c.get("tools", []))]
        already = next((c for c in covering if c["id"] in chosen), None)
        if already:
            steps.append({"kind": kind, "gap": gap, "course": _course_view(already), "status": "covered by an earlier step",
                          "sdg": ["4.4"]})
            continue
        affordable = sorted((c for c in covering if spent + c["cost_rm"] <= budget),
                            key=lambda c: (c["cost_rm"], c["hours"], c["id"]))
        if not affordable:
            status = "no verified resource within your budget" if covering else "no verified resource"
            steps.append({"kind": kind, "gap": gap, "course": None, "status": status, "sdg": ["4.4"]})
            continue
        c = affordable[0]
        chosen[c["id"]] = c
        spent += c["cost_rm"]
        hours += c["hours"]
        steps.append({"kind": kind, "gap": gap, "course": _course_view(c), "status": "verified", "sdg": ["4.4"],
                      "cum_hours": {"v": round(hours, 1), "src": [CATALOGUE_SRC]},
                      "cum_weeks": {"v": round(hours / weekly_hours, 1), "src": [CATALOGUE_SRC, "your-input"]}})
    months = hours / weekly_hours / WEEKS_PER_MONTH if weekly_hours else 0.0
    return {"steps": steps, "_months": months, "_spent": spent, "_chosen": chosen}


def scenario_check(gaps, skill_outlook, release, months, threshold, pace):
    """For each gap activity in the plan: its outlook now and at the horizon under every scenario."""
    out = []
    for g in gaps[:PLAN_GAPS]:
        o = skill_outlook.outlook(g["dwa_id"], release)
        if o is None:
            out.append({"dwa_id": g["dwa_id"], "name": g["name"], "outlook": None,
                        "crosses": {s: False for s in SCENARIOS}})
            continue
        proj = {s: round(skill_outlook.project(o["now"], s, months, pace), 1) for s in SCENARIOS}
        out.append({"dwa_id": g["dwa_id"], "name": g["name"], "outlook": o, "projected": proj,
                    "crosses": {s: proj[s] >= threshold for s in SCENARIOS}})
    return out


def ai_steps(checks, scenario, courses, budget_left, chosen):
    """Direct-and-check steps for gap activities whose projected share reaches the threshold.

    An AI-practice entry that teaches the activity itself is preferred; otherwise a general entry on
    directing and checking AI output (one with no activity or tool links) is offered and labelled general.
    A course already in the plan is not counted again.
    """
    ai = [c for c in courses if c.get("ai_skill")]
    general = sorted((c for c in ai if not c.get("dwas") and not c.get("tools")),
                     key=lambda c: (c["cost_rm"], c["hours"], c["id"]))
    steps, spent = [], 0.0
    for chk in checks:
        if not chk["crosses"][scenario]:
            continue
        specific = sorted((c for c in ai if chk["dwa_id"] in c.get("dwas", [])),
                          key=lambda c: (c["cost_rm"], c["hours"], c["id"]))
        match, pool = ("activity", specific) if specific else ("general", general)
        course = next((c for c in pool if c["id"] in chosen), None)
        if course is None:
            course = next((c for c in pool if spent + c["cost_rm"] <= budget_left), None)
        status = "no verified resource"
        if course is not None:
            if course["id"] in chosen:
                status = "covered by an earlier step"
            else:
                chosen[course["id"]] = course
                spent += course["cost_rm"]
                status = "verified"
        steps.append({"kind": "direct_ai", "gap": {"dwa_id": chk["dwa_id"], "name": chk["name"]},
                      "reason": {"now": chk["outlook"]["now"], "projected": chk["projected"][scenario],
                                 "releases": [r["release"] for r in chk["outlook"]["releases"]],
                                 "weak": chk["outlook"]["weak"], "src": chk["outlook"]["src"]},
                      "course": _course_view(course) if course else None, "match": match if course else None,
                      "status": status, "sdg": ["4.4"]})
    return steps


def totals(chosen, weekly_hours):
    """Hours and cost of the plan, counting each unique course once."""
    hours = sum(c["hours"] for c in chosen.values())
    cost = sum(c["cost_rm"] for c in chosen.values())
    months = hours / weekly_hours / WEEKS_PER_MONTH if weekly_hours else 0.0
    return {"total_hours": {"v": round(hours, 1), "src": [CATALOGUE_SRC]},
            "total_cost_rm": {"v": round(cost, 2), "src": [CATALOGUE_SRC]},
            "study_months": {"v": round(months, 1), "src": [CATALOGUE_SRC, "your-input"], "estimate": True},
            "unique_courses": len(chosen)}

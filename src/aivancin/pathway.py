"""Reskilling plan from verified catalogue entries, and the scenario check on gap activities (SPEC sections 6 and 7)."""
import datetime as dt

PLAN_GAPS = 6
PLAN_TOOLS = 3
WEEKS_PER_MONTH = 52 / 12
DEFAULT_PACE = 1.444  # 27% to 39% directive use in eight months (AEI report, arXiv 2511.15080)
PACE_MONTHS = 8
DEFAULT_THRESHOLD = 50.0
CATALOGUE_SRC = "aivancin-catalogue"


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


def projected(a0, scenario, months, pace=DEFAULT_PACE):
    if scenario == "faster":
        return min(100.0, a0 * pace ** (months / PACE_MONTHS))
    return a0


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
    return {"steps": steps, "total_hours": {"v": round(hours, 1), "src": [CATALOGUE_SRC]},
            "total_cost_rm": {"v": round(spent, 2), "src": [CATALOGUE_SRC]},
            "months_to_ready": {"v": round(months, 1), "src": [CATALOGUE_SRC, "your-input"], "estimate": True},
            "_months": months, "_spent": spent}


def scenario_check(code, gaps, matcher, evidence, release, months, threshold, pace):
    """For each gap activity: does any linked task of the role reach the threshold by the ready date?"""
    onet = matcher.snaps.onet
    out = []
    for g in gaps[:PLAN_GAPS]:
        tasks = []
        for tid in matcher.tasks_by_dwa.get((code, g["dwa_id"]), []):
            ev = evidence.task(tid, release)
            if ev is None:
                continue
            tasks.append({"task_id": tid, "task": onet.tasks[(code, tid)]["task"], "a0": ev["share"], "weak": ev["weak"],
                          "releases": [r["release"] for r in ev["releases"]],
                          "moderate": round(projected(ev["share"], "moderate", months, pace), 1),
                          "faster": round(projected(ev["share"], "faster", months, pace), 1)})
        status = {s: any(t[s] >= threshold for t in tasks) for s in ("moderate", "faster")}
        out.append({"dwa_id": g["dwa_id"], "name": g["name"], "tasks": tasks, "crosses": status,
                    "evidence": "none" if not tasks else "ok"})
    return out


def ai_steps(checks, scenario, courses, budget_left):
    """Plan additions for gap activities that cross the threshold in the selected scenario."""
    ai_courses = sorted((c for c in courses if c.get("ai_skill") and c["cost_rm"] <= budget_left),
                        key=lambda c: (c["cost_rm"], c["hours"], c["id"]))
    steps = []
    for chk in checks:
        if not chk["crosses"][scenario]:
            continue
        top = max(chk["tasks"], key=lambda t: (t[scenario], t["task_id"]))
        steps.append({"kind": "direct_ai", "gap": {"dwa_id": chk["dwa_id"], "name": chk["name"]},
                      "reason": top, "course": _course_view(ai_courses[0]) if ai_courses else None,
                      "status": "verified" if ai_courses else "no verified resource", "sdg": ["4.4"]})
    return steps

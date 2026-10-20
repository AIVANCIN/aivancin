"""B7 Phase 2 target (SPEC section 10.8) on tests/acceptance/b7_profiles.json, offline.

python tools/checks/b7_target.py pre|routes [result.json] [profiles.json]
pre:    the pre-revision build: its top three roles (default skilled scope, other-work lane) and their plans.
routes: the adopted revision: the routes and their first four-week blocks.
A profile passes with two or more relevant candidates, each with a sourced requirement and a block that serves
at least three prioritized gaps with verified resources within 4 x weekly hours and the budget.
profiles.json defaults to tests/acceptance/b7_profiles.json; tests/acceptance/b7_profiles_2.json is the second fresh set.
"""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aivancin import intake, snapshots  # noqa: E402
from aivancin.encoder import load_ai  # noqa: E402
from aivancin.matching import Matcher  # noqa: E402
from aivancin.report import Reporter, ReportError  # noqa: E402

SETS = ROOT / "tests" / "acceptance" / "b7_profiles.json"
WEEKS = 4


def confirmed_profile(p, encoder, vectors, snaps):
    req = {"other_work": p["other_work"], "title_code": p["job"], "degree_level": p["level"],
           "weekly_hours": p["hours"], "budget_rm": p["budget"]}
    sug = intake.suggest(req, encoder, vectors, snaps.onet)
    picks = {d for s in sug["sentences"] if s["guard"] is None for d in s["top"]} & set(p["true"])
    req["confirmed"] = [{"dwa_id": d, "source": "suggested"} for d in sorted(picks)]
    return req, intake.build_profile(req, snaps.onet, sug, snaps.cip), len(picks)


def pre_candidates(rep, prof, p, today):
    """The pre-revision report's top three roles, judged with its own plan rows."""
    out = []
    res = rep.report(prof, {"lane": "other_work"}, today=today)
    capacity = WEEKS * p["hours"]
    for r in res["roles"][:3]:
        seen, hours, served = {}, 0.0, 0
        for s in r["plan"]["steps"]:
            if s["kind"] != "activity" or not s["course"]:
                continue
            cid = s["course"]["id"]
            if cid not in seen:
                seen[cid] = s["course"]["hours"]["v"]
                hours += seen[cid]
            if hours <= capacity:
                served += 1
        cost = r["plan"]["total_cost_rm"]["v"]
        out.append({"code": r["code"], "title": r["title"], "requirements": len(r["labels"]),
                    "gaps_served": served, "within_budget": cost <= p["budget"]})
    return out


def route_candidates(rep, prof, p, today):
    from aivancin.routes import Planner  # imported here so "pre" also runs on commits before the route code
    res = Planner(rep.snaps, rep.m, rep).routes(prof, {"weeks": WEEKS}, today=today)
    out = []
    for r in res["routes"]:
        b = r["block"]
        out.append({"code": r["code"], "title": r["title"], "requirements": len(r["requirements"]),
                    "gaps_served": len(b["gaps_served"]), "within_budget": b["cost_rm"]["v"] <= p["budget"]
                    and b["effort_hours"]["v"] <= WEEKS * p["hours"]})
    return out


def main():
    mode = sys.argv[1]
    path = Path(sys.argv[3]) if len(sys.argv) > 3 else SETS
    sets = json.loads(path.read_text(encoding="utf-8"))
    snaps = snapshots.load()
    encoder, vectors = load_ai(snaps)
    rep = Reporter(snaps, Matcher(snaps))
    today = dt.date(2026, 10, 21)
    judge = pre_candidates if mode == "pre" else route_candidates
    rows = []
    for group in ("in_scope", "out_of_scope"):
        for p in sets[group]:
            req, prof, n = confirmed_profile(p, encoder, vectors, snaps)
            fam = tuple(sets["families"].get(p["family"], []))
            # A profile the app cannot answer (for example no confirmed activity) stays in the count and fails.
            try:
                cands, error = judge(rep, prof, p, today), None
            except ReportError as exc:
                cands, error = [], "; ".join(f"{k}: {v}" for k, v in exc.errors.items())
            good = [c for c in cands if fam and c["code"].startswith(fam) and c["requirements"] >= 1
                    and c["gaps_served"] >= 3 and c["within_budget"]]
            rows.append({"id": p["id"], "group": group, "confirmed": n, "candidates": cands, "error": error,
                         "qualifying": len(good), "pass": len(good) >= 2})
    ins = [r for r in rows if r["group"] == "in_scope"]
    res = {"mode": mode, "sets": path.name, "sets_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "run_date": dt.date.today().isoformat(),
           "in_scope_passed": sum(r["pass"] for r in ins), "in_scope_of": len(ins),
           "share": round(sum(r["pass"] for r in ins) / len(ins), 3), "target_met": sum(r["pass"] for r in ins) >= 0.8 * len(ins),
           "out_of_scope": [{"id": r["id"], "qualifying": r["qualifying"]} for r in rows if r["group"] == "out_of_scope"],
           "profiles": rows}
    text = json.dumps(res, indent=1)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("mode", "in_scope_passed", "in_scope_of", "share", "target_met", "out_of_scope")}))
    for r in rows:
        print(r["id"], r["group"], "confirmed", r["confirmed"], "qualifying", r["qualifying"], r["error"] or "", "|",
              "; ".join(f"{c['title'][:30]} req{c['requirements']} gaps{c['gaps_served']}{'' if c['within_budget'] else ' over'}"
                        for c in r["candidates"]))


if __name__ == "__main__":
    main()

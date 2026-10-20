"""Reproducible evidence-to-plan replay (SPEC section 10.6, check B6) on tests/acceptance/replay_hafiz.json, offline.

python tools/checks/replay.py [fixture.json] [result.json]
Runs the actual application (intake, matching, routes and the update comparison) for the same profile, constraints,
target and completed step under two dated AI-usage evidence bundles, and checks that:
the bundled release files are the pinned ones; identical inputs give identical output for each bundle; the app's
figures and every step's checkpoint form match a separate recomputation from the raw CSV files; exactly the expected
step changes, with dated provenance; both plans fit the time and budget with each entry counted once; the completed
step and the target are kept; and an evidence change for an activity outside the plan leaves the plan unchanged.
Prints the original plan, the evidence change and the revised plan. Exit code 1 if any check fails.
"""
import copy
import csv
import datetime as dt
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aivancin import intake, snapshots  # noqa: E402
from aivancin.encoder import load_ai  # noqa: E402
from aivancin.matching import Matcher  # noqa: E402
from aivancin.report import Reporter  # noqa: E402
from aivancin.routes import Planner  # noqa: E402

FIXTURE = ROOT / "tests" / "acceptance" / "replay_hafiz.json"
DATA = ROOT / "data"


# ---- separate recomputation from the raw files (does not use the app's evidence or outlook code) ---------------
def raw_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class RawEvidence:
    """Activity value per release = mean, over the activity's distinct task statements, of the share of the first
    linked task ID the release reports; the value as of a release = mean over the pooled releases."""

    def __init__(self, releases):
        text = {(r["code"], r["task_id"]): " ".join(r["task"].lower().split()).rstrip(".").strip()
                for r in raw_rows(DATA / "onet" / "tasks.csv")}
        statements = defaultdict(lambda: defaultdict(set))
        for r in raw_rows(DATA / "onet" / "task_dwas.csv"):
            statements[r["dwa_id"]][text[(r["code"], r["task_id"])]].add(r["task_id"])
        self.statements = statements
        rows = {rid: [r for r in raw_rows(DATA / "aei" / f"{rid}.csv") if r["level"] == "task"] for rid in releases}
        self.shares = {rid: {r["key"]: float(r["share"]) for r in rs} for rid, rs in rows.items()}
        self.counts = {rid: {r["key"]: int(r["classified"]) if r["classified"] else None for r in rs} for rid, rs in rows.items()}

    def release_value(self, dwa, rid):
        vals = []
        for ids in self.statements.get(dwa, {}).values():
            hit = next((self.shares[rid][t] for t in sorted(ids, key=int) if t in self.shares[rid]), None)
            if hit is not None:
                vals.append(hit)
        return sum(vals) / len(vals) if vals else None

    def conversations(self, dwa, rid):
        counts = [next((self.counts[rid][t] for t in sorted(ids, key=int) if t in self.counts[rid]), "none")
                  for ids in self.statements.get(dwa, {}).values()]
        counts = [c for c in counts if c != "none"]
        return None if not counts or any(c is None for c in counts) else sum(counts)

    def value(self, dwa, pooled):
        vals = [v for v in (self.release_value(dwa, rid) for rid in pooled) if v is not None]
        return sum(vals) / len(vals) if vals else None


# ---- the application ---------------------------------------------------------------------------------------------
def build(fx):
    snaps = snapshots.load()
    encoder, vectors = load_ai(snaps)
    persona = next(p for p in json.loads((DATA / "personas.json").read_text(encoding="utf-8"))
                   if p["id"] == fx["profile"]["persona"])
    req = {**persona, "weekly_hours": fx["profile"]["weekly_hours"], "budget_rm": fx["profile"]["budget_rm"],
           "confirmed": [{"dwa_id": d, "source": "suggested"} for d in fx["profile"]["confirmed"]]}
    prof = intake.build_profile(req, snaps.onet, intake.suggest(req, encoder, vectors, snaps.onet), snaps.cip)
    m = Matcher(snaps)
    return snaps, prof, Planner(snaps, m, Reporter(snaps, m))


def target_route(planner, prof, opts, today):
    res = planner.routes(prof, opts, today=today)
    return next(r for r in res["routes"] if r["code"] == opts["target"])


def plain(route):
    return [(s["id"], s["form"], s["completed"]) for s in route["block"]["steps"]]


def run(fixture=FIXTURE):
    fx = json.loads(Path(fixture).read_text(encoding="utf-8"))
    today = dt.date.fromisoformat(fx["today"])
    before, after = fx["evidence"]["before"], fx["evidence"]["after"]
    snaps, prof, planner = build(fx)
    base = dict(fx["options"])
    done = fx["expected"]["completed_step"]
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    # 1. pinned evidence files
    for side in (before, after):
        h = hashlib.sha256((DATA / "aei" / f"{side['release']}.csv").read_bytes()).hexdigest()
        check(f"pinned evidence {side['release']}", h == side["derived_sha256"], h[:12])

    # 2. identical inputs give identical output, per bundle and for the comparison
    opts = {**base, "completed": [done]}
    r_before = target_route(planner, prof, {**opts, "release": before["release"]}, today)
    r_after = target_route(planner, prof, {**opts, "release": after["release"]}, today)
    check("repeatable before", r_before == target_route(planner, prof, {**opts, "release": before["release"]}, today))
    check("repeatable after", r_after == target_route(planner, prof, {**opts, "release": after["release"]}, today))
    cmp_opts = {**opts, "from_release": before["release"], "to_release": after["release"]}
    comparison = planner.compare(prof, cmp_opts, today=today)
    check("repeatable comparison", comparison == planner.compare(prof, cmp_opts, today=today))

    # 3-4. figures and checkpoint forms against the raw-file recomputation (moderate projection = value as of release)
    raw = RawEvidence(sorted(set(before["pooled"]) | set(after["pooled"])))
    threshold = float(base["threshold"])
    fig_ok, form_ok, notes = True, True, []
    for route, side in ((r_before, before), (r_after, after)):
        for s in route["block"]["steps"]:
            if s["kind"] != "learn":
                continue
            vals = {g["dwa_id"]: raw.value(g["dwa_id"], side["pooled"]) for g in s["serves"]}
            for r in s["reason"]:
                if vals.get(r["dwa_id"]) is None or abs(vals[r["dwa_id"]] - r["now"]["v"]) > 0.051:
                    fig_ok = False
                    notes.append(f"{s['id']} {r['dwa_id']}: app {r['now']['v']} raw {vals.get(r['dwa_id'])}")
            expected = "ai" if any(v is not None and v >= threshold for v in vals.values()) else "plain"
            if expected != s["form"]:
                form_ok = False
                notes.append(f"{s['id']} as of {side['release']}: app {s['form']} raw {expected}")
    check("figures match raw files", fig_ok, "; ".join(notes[:4]))
    check("checkpoint forms match raw files", form_ok, "; ".join(notes[:4]))

    # 5-6. exactly the expected change, with dated provenance
    exp = {(c["step"], c["from"], c["to"]) for c in fx["expected"]["changed"]}
    got = {(c["id"], c["before"]["form"], c["after"]["form"]) for c in comparison["changes"] if c["change"] == "changed form"}
    others = {c["change"] for c in comparison["changes"] if c["change"] != "changed form"}
    check("expected change only", got == exp and others <= {"retained"}, f"got {sorted(got)}, other kinds {sorted(others)}")
    prov = True
    for c in fx["expected"]["changed"]:
        step = next((x for x in comparison["changes"] if x["id"] == c["step"]), None)
        reason = next((r for r in (step or {}).get("after", {}).get("reason", []) if r["dwa_id"] == c["activity"]), None)
        prov = prov and reason is not None and f"aei-{after['release']}" in reason["now"]["src"]
    check("dated provenance on the change", prov)

    # 7. both plans fit; totals equal the shown open steps, each entry once
    weeks_hours = base["weeks"] * fx["profile"]["weekly_hours"]
    fits = True
    for route in (r_before, r_after):
        b = route["block"]
        ids = [s["id"] for s in b["steps"]]
        open_effort = sum(s["provider_hours"]["v"] + s["practice_hours"]["v"] for s in b["steps"] if not s["completed"])
        fits = fits and len(ids) == len(set(ids)) and b["effort_hours"]["v"] <= weeks_hours + 1e-9 \
            and b["cost_rm"]["v"] <= fx["profile"]["budget_rm"] + 1e-9 and abs(b["effort_hours"]["v"] - open_effort) < 0.051
    check("fits time and budget, each entry once", fits,
          f"{r_before['block']['effort_hours']['v']} h and {r_after['block']['effort_hours']['v']} h of {weeks_hours} h")

    # 8. completed work and target kept
    kept = all(any(s["id"] == done and s["completed"] for s in r["block"]["steps"]) for r in (r_before, r_after))
    kept = kept and comparison["target"] == base["target"] and \
        any(c["id"] == done and c["completed"] for c in comparison["changes"])
    check("completed step and target kept", kept)

    # 9. an evidence change outside the plan changes nothing
    in_plan = {g["dwa_id"] for r in (r_before, r_after) for s in r["block"]["steps"] for g in s["serves"]}
    outside = next(d for d in sorted(snaps.onet.dwa_ids)
                   if d not in in_plan and planner.rep.skills.outlook(d, after["release"]) is not None)
    snaps2 = copy.copy(snaps)
    linked = {tid for ids in planner.rep.skills.groups[outside] for tid in ids}
    releases = []
    for rid, tasks, occ in snaps.evidence.releases:
        if rid == after["release"]:
            tasks = {k: ({**v, "share": 100.0} if k in linked else v) for k, v in tasks.items()}
        releases.append((rid, tasks, occ))
    snaps2.evidence = type(snaps.evidence)(releases)
    m2 = Matcher(snaps2)
    p2 = Planner(snaps2, m2, Reporter(snaps2, m2))
    moved = p2.rep.skills.outlook(outside, after["release"])["now"] != planner.rep.skills.outlook(outside, after["release"])["now"]
    same = plain(target_route(p2, prof, {**opts, "release": after["release"]}, today)) == plain(r_after)
    check("change outside the plan changes nothing", moved and same, f"perturbed {outside}")

    story = {"target": r_after["title"], "constraints": f"{base['weeks']} weeks x {fx['profile']['weekly_hours']} h, "
                                                         f"RM{fx['profile']['budget_rm']}, {base['scenario']} scenario",
             "before": {"release": before["release"], "steps": [(s["title"], s["form"]) for s in r_before["block"]["steps"]],
                        "effort_hours": r_before["block"]["effort_hours"]["v"], "cost_rm": r_before["block"]["cost_rm"]["v"]},
             "after": {"release": after["release"], "steps": [(s["title"], s["form"]) for s in r_after["block"]["steps"]],
                       "effort_hours": r_after["block"]["effort_hours"]["v"], "cost_rm": r_after["block"]["cost_rm"]["v"]},
             "evidence": [{"activity": snaps.onet.dwa_names[c["activity"]],
                           "per_release": {rid: {"value": round(raw.release_value(c["activity"], rid), 1),
                                                 "conversations": raw.conversations(c["activity"], rid)} for rid in after["pooled"]},
                           "as_of_before": round(raw.value(c["activity"], before["pooled"]), 1),
                           "as_of_after": round(raw.value(c["activity"], after["pooled"]), 1), "threshold": threshold}
                          for c in fx["expected"]["changed"]],
             "summary": comparison["summary"], "limits": fx["limits"]}
    return {"fixture": Path(fixture).name, "fixture_sha256": hashlib.sha256(Path(fixture).read_bytes()).hexdigest(),
            "checks": checks, "passed": sum(c["pass"] for c in checks), "of": len(checks),
            "all_pass": all(c["pass"] for c in checks), "story": story}


def main():
    fixture = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE
    res = run(fixture)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    s = res["story"]
    print(f"Target: {s['target']} ({s['constraints']})")
    for side in ("before", "after"):
        b = s[side]
        print(f"\nPlan with evidence as of {b['release']}: {b['effort_hours']} h, RM{b['cost_rm']}")
        for title, form in b["steps"]:
            print(f"  - {title} [{'with AI checking' if form == 'ai' else 'plain checkpoint'}]")
        if side == "before":
            for e in s["evidence"]:
                per = "; ".join(f"{rid}: {x['value']}% of {x['conversations']} conversations" for rid, x in e["per_release"].items())
                print(f"\nEvidence change: {e['activity']} by release ({per}); as of the earlier bundle "
                      f"{e['as_of_before']}%, as of the later {e['as_of_after']}% (threshold {e['threshold']}%)")
    print(f"\n{s['summary']}\nLimits: {s['limits']}\n")
    for c in res["checks"]:
        print(f"{'PASS' if c['pass'] else 'FAIL'}  {c['check']}{'  (' + c['detail'] + ')' if c['detail'] else ''}")
    print(f"\n{res['passed']} of {res['of']} checks pass")
    sys.exit(0 if res["all_pass"] else 1)


if __name__ == "__main__":
    main()

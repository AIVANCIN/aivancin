"""Fresh predeclared evaluation (tests/acceptance/b7_profiles_8.json), offline: the shipped intake against same-input
title-only and keyword (BM25) baselines, with B7's pass rule and per-person results.

python tools/checks/fresh_eval.py [result.json] [profiles.json]
Modes, each on the same people, constraints, catalogue and four-week block:
  model:      the shipped reading of the person's text, strict simulated confirmation, plus the job title's activities
  title_only: only the job title's activities (no text read)
  keyword:    BM25 keyword suggestions (top five per sentence, guard-flagged sentences skipped) in place of the model,
              same confirmation, plus the job title's activities
A profile the app cannot answer stays in the count and fails. Unexpected errors are recorded per person, never dropped.
"""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from a1_a3 import BM25  # noqa: E402
from aivancin import intake, snapshots  # noqa: E402
from aivancin.encoder import load_ai  # noqa: E402
from aivancin.matching import Matcher  # noqa: E402
from aivancin.report import Reporter, ReportError  # noqa: E402
from aivancin.routes import Planner  # noqa: E402

SET = ROOT / "tests" / "acceptance" / "b7_profiles_8.json"
WEEKS = 4


def request(p, mode, encoder, vectors, snaps, bm25):
    title = [a["dwa_id"] for a in intake.title_activities(snaps.onet, p["job"])]
    req = {"title_code": p["job"], "affirmed_title_activities": title, "degree_level": p["level"],
           "weekly_hours": p["hours"], "budget_rm": p["budget"]}
    if mode == "title_only":
        return req, None, 0
    req["other_work"] = p["other_work"]
    sug = intake.suggest(req, encoder, vectors, snaps.onet)
    if mode == "model":
        picks = {d for s in sug["sentences"] if s["guard"] is None for d in s["top"]} & set(p["true"])
        req["confirmed"] = [{"dwa_id": d, "source": "suggested"} for d in sorted(picks)]
        return req, sug, len(picks)
    picks = {d for s in sug["sentences"] if s["guard"] is None for d in bm25.top(s["text"])} & set(p["true"])
    req["confirmed"] = [{"dwa_id": d, "source": "added_by_you", "lane": "other_work"} for d in sorted(picks)]
    return req, None, len(picks)


def run(mode, sets, encoder, vectors, snaps, planner, bm25, today):
    rows = []
    for group in ("in_scope", "out_of_scope"):
        for p in sets[group]:
            fam = tuple(sets["families"].get(p["family"], []))
            req, sug, n = request(p, mode, encoder, vectors, snaps, bm25)
            try:
                prof = intake.build_profile(req, snaps.onet, sug, snaps.cip)
                res = planner.routes(prof, {"weeks": WEEKS}, today=today)
                error = None
            except (intake.IntakeError, ReportError) as exc:
                res, error = {"routes": []}, "; ".join(f"{k}: {v}" for k, v in exc.errors.items())
            except Exception as exc:  # unexpected: recorded and failed, never dropped
                res, error = {"routes": []}, f"unexpected {type(exc).__name__}: {exc}"
            cands = []
            for r in res["routes"]:
                b = r["block"]
                cands.append({"code": r["code"], "title": r["title"], "family": r["family"],
                              "relevant": bool(fam) and r["code"].startswith(fam), "requirements": len(r["requirements"]),
                              "gaps_served": len(b["gaps_served"]), "prioritized": len(r["prioritized"]),
                              "hours": b["effort_hours"]["v"], "capacity": WEEKS * p["hours"], "cost": b["cost_rm"]["v"],
                              "within": b["cost_rm"]["v"] <= p["budget"] and b["effort_hours"]["v"] <= WEEKS * p["hours"] + 1e-9})
            good = [c for c in cands if c["relevant"] and c["requirements"] >= 1 and c["gaps_served"] >= 3 and c["within"]]
            if group == "in_scope":
                ok = len(good) >= 2
            else:
                ok = len(cands) < 2 and not any(c["family"] in p.get("no_family", []) for c in cands)
            rows.append({"id": p["id"], "group": group, "family": p["family"], "confirmed_true": n, "true": len(p["true"]),
                         "error": error, "routes": cands, "relevant": sum(c["relevant"] for c in cands),
                         "unsupported": sum(not c["relevant"] for c in cands) if group == "in_scope" else len(cands),
                         "qualifying": len(good), "invalid_blocks": sum(not c["within"] for c in cands),
                         "fallback": (res.get("fallback") or {}).get("kind"), "pass": ok})
    return rows


def summary(rows):
    ins = [r for r in rows if r["group"] == "in_scope"]
    outs = [r for r in rows if r["group"] == "out_of_scope"]
    return {"in_scope_passed": sum(r["pass"] for r in ins), "in_scope_of": len(ins),
            "target_met": sum(r["pass"] for r in ins) >= 0.8 * len(ins),
            "relevant_routes": sum(r["relevant"] for r in ins), "unsupported_routes_in_scope": sum(r["unsupported"] for r in ins),
            "invalid_blocks": sum(r["invalid_blocks"] for r in rows), "errors": [r["id"] for r in rows if r["error"]],
            "confirmed_true": sum(r["confirmed_true"] for r in ins), "true_total": sum(r["true"] for r in ins),
            "out_of_scope_passed": sum(r["pass"] for r in outs), "out_of_scope_of": len(outs)}


def main():
    path = Path(sys.argv[2]) if len(sys.argv) > 2 else SET
    sets = json.loads(path.read_text(encoding="utf-8"))
    snaps = snapshots.load()
    encoder, vectors = load_ai(snaps)
    m = Matcher(snaps)
    planner = Planner(snaps, m, Reporter(snaps, m))
    bm25 = BM25(snaps.onet.dwa_ids, [snaps.onet.dwa_names[d] for d in snaps.onet.dwa_ids])
    today = dt.date(2026, 10, 23)
    res = {"sets": path.name, "sets_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "run_date": dt.date.today().isoformat(),
           "modes": {}}
    for mode in ("model", "title_only", "keyword"):
        rows = run(mode, sets, encoder, vectors, snaps, planner, bm25, today)
        res["modes"][mode] = {"summary": summary(rows), "profiles": rows}
    text = json.dumps(res, indent=1)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
    for mode, v in res["modes"].items():
        print(mode, json.dumps(v["summary"]))
    for i, r in enumerate(res["modes"]["model"]["profiles"]):
        t, k = res["modes"]["title_only"]["profiles"][i], res["modes"]["keyword"]["profiles"][i]
        print(f"{r['id']} {r['group'][:3]} model {'PASS' if r['pass'] else 'fail'} true {r['confirmed_true']}/{r['true']} "
              f"rel {r['relevant']} off {r['unsupported']} q {r['qualifying']} fb {r['fallback']} {r['error'] or ''}| "
              f"title {'PASS' if t['pass'] else 'fail'} rel {t['relevant']} | keyword {'PASS' if k['pass'] else 'fail'} "
              f"true {k['confirmed_true']} rel {k['relevant']} | " + "; ".join(f"{c['title'][:28]}{'' if c['relevant'] else '*'} g{c['gaps_served']}" for c in r["routes"]))


if __name__ == "__main__":
    main()

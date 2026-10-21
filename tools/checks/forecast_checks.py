"""C1-C5 (SPEC 11.9): the forecast signals, recomputed separately from the bundled files, offline.

python tools/checks/forecast_checks.py [result.json]
C1 recomputes skill demand (F2) from the CSVs with its own code; C2 reports how many labels change with net
thresholds of 0.15 and 0.35 (not a gate); C3 checks time to threshold on every Radar row for Hafiz and Priya;
C4 recomputes the Malaysian group trend (F4) from the DOSM file and checks that pay steps match the latest year;
C5 re-exports the registered forecasts and compares bytes and the manifest hash, then scores a fixture release.
"""
import csv
import datetime as dt
import hashlib
import json
import math
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "checks"))

DATA = ROOT / "data"
TODAY = dt.date(2026, 10, 21)
HAFIZ = ["4.A.3.b.1.d.1", "4.A.3.b.1.d.3", "4.A.3.b.1.c.1", "4.A.3.b.1.b.2", "4.A.3.b.1.f.4", "4.A.2.b.2.b.8"]
PRIYA = ["4.A.4.c.1.c.6", "4.A.3.b.6.a.1", "4.A.4.c.3.d.1", "4.A.2.a.3.c.2", "4.A.3.b.6.j.3"]
GROW = ("Faster than average", "Much faster than average")


def rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def f2_raw(data=DATA, cut=0.25):
    """Skill demand from the CSVs alone: weights from task types, the pool from Job Zones, codes and bands."""
    ttype = {(r["code"], r["task_id"]): r["task_type"] for r in rows(data / "onet" / "tasks.csv")}
    w = defaultdict(lambda: defaultdict(float))
    for r in rows(data / "onet" / "task_dwas.csv"):
        w[r["code"]][r["dwa_id"]] += 0.5 if ttype.get((r["code"], r["task_id"])) == "Supplemental" else 1.0
    jz = {r["code"]: r["job_zone"] for r in rows(data / "onet" / "job_zones.csv")}
    out = {r["code"]: r for r in rows(data / "onet" / "outlook.csv")}
    pool = [c for c in w if jz.get(c) in ("3", "4") and c[:3] not in ("11-", "55-") and out.get(c, {}).get("growth_band")]
    res = {}
    by_dwa = defaultdict(list)
    for c in pool:
        for d, x in w[c].items():
            if x > 0:
                by_dwa[d].append((c, x))
    for d, items in by_dwa.items():
        W = sum(x for _, x in items)
        g = sum(x for c, x in items if out[c]["growth_band"].split(" (")[0] in GROW) / W
        k = sum(x for c, x in items if out[c]["growth_band"].split(" (")[0] == "Decline") / W
        units = {(out[c]["employment"], out[c]["growth_band"], out[c]["openings"]): int(out[c]["openings"])
                 for c, _ in items if out[c]["openings"]}
        net = g - k
        label = "mostly in growing jobs" if net >= cut else "mostly in declining jobs" if net <= -cut else "mixed"
        res[d] = {"grow": round(g, 3), "decline": round(k, 3), "net": round(net, 3), "roles": len(items),
                  "openings": sum(units.values()), "label": label}
    return res


def c1(demand, raw):
    keys = ("grow", "decline", "net", "roles", "openings", "label")
    mismatches = []
    for d, want in raw.items():
        got = demand.get(d)
        if any(got.get(k) != want[k] for k in keys):
            mismatches.append({"dwa_id": d, "app": {k: got.get(k) for k in keys}, "raw": want})
    no_proj_app = sum(1 for d in demand.acc if d not in raw)
    return {"check": "C1", "activities_with_projection": len(raw), "mismatches": len(mismatches),
            "examples": mismatches[:5], "app_only": no_proj_app, "pass": not mismatches and not no_proj_app}


def c2(snaps, matcher):
    from aivancin.outlook import DemandOutlook
    base = DemandOutlook(snaps, matcher)
    labels = {d: base.get(d)["label"] for d in base.acc}
    out = {"check": "C2", "activities": len(labels), "gate": "none (reported)"}
    for cut in (0.15, 0.35):
        alt = DemandOutlook(snaps, matcher, net_cut=cut)
        changed = sum(alt.get(d)["label"] != labels[d] for d in labels)
        out[f"changed_at_{cut}"] = changed
    from collections import Counter
    out["labels_at_0.25"] = dict(sorted(Counter(labels.values()).items()))
    return out


def ttt_formula(a0, T, scenario, pace, D):
    """SPEC 11.1, written out again for the check."""
    if a0 is None:
        return None, "no evidence in the bundled releases"
    v = max(0.0, a0 - D) if scenario == "slower" else a0
    if v >= T:
        return 0, "already at or above the threshold"
    if scenario in ("moderate", "slower"):
        return None, "not reached in this scenario"
    if a0 == 0 or pace <= 1:
        return None, "not reached in this scenario"
    t = 8 * math.log(T / a0) / math.log(pace)
    if t > 60:
        return None, "not within five years"
    return math.floor(t + 0.5), None


def c3(planner, profiles):
    D = planner.rep.skills.drop
    bad, seen = [], defaultdict(int)
    n = 0
    for name, prof in profiles.items():
        for opts in ({}, {"scenario": "faster"}, {"threshold": 30}, {"pace": 2.0}):
            res = planner.routes(prof, opts, today=TODAY)
            rd = res["radar"]
            T, pace = rd["threshold"]["v"], opts.get("pace", 1.444)
            rows_ = rd["skills"] + [x for r in res["routes"] for x in r["ai_use"]]
            for row in rows_:
                a0 = row["now"]["v"] if row["now"] else None
                for s, got in row["ttt"].items():
                    n += 1
                    months, text = ttt_formula(a0, T, s, pace, D)
                    gm = got["months"]["v"] if got["months"] else None
                    ok = gm == months and (text is None or got["text"] == text)
                    seen[got["text"] if text else "months"] += 1
                    if not ok:
                        bad.append({"profile": name, "dwa": row["dwa_id"], "scenario": s, "a0": a0, "got": got, "want": [months, text]})
    cases = dict(seen)
    # "Not within five years" needs a now value below T / pace^7.5 (3.2% at 50% and pace 1.444); persona rows rarely
    # have one, so the branch is also checked on constructed values on both sides of that cut.
    from aivancin.outlook import time_to_threshold
    cut = 50 / 1.444 ** 7.5
    constructed = {a0: time_to_threshold(a0, 50, "faster", 1.444, D) for a0 in (round(cut - 0.1, 2), round(cut + 0.1, 2))}
    constructed_ok = (constructed[round(cut - 0.1, 2)] == (None, "not within five years")
                      and 0 < (constructed[round(cut + 0.1, 2)][0] or 0) <= 60)
    lowest = min((r["now"]["v"] for name, prof in profiles.items()
                  for r in planner.routes(prof, {}, today=TODAY)["radar"]["skills"] if r["now"] and r["now"]["v"] > 0), default=None)
    return {"check": "C3", "rows_checked": n, "mismatches": len(bad), "examples": bad[:5], "cases": cases,
            "lowest_positive_now_in_persona_radar": lowest, "five_year_cut_at_50_and_1.444": round(cut, 2),
            "constructed": {str(k): v for k, v in constructed.items()}, "constructed_ok": constructed_ok,
            "pass": not bad and cases.get("already at or above the threshold", 0) > 0 and constructed_ok}


def c4(snaps, reporter):
    from aivancin.outlook import group_trend
    from aivancin.snapshots import MASCO_GROUPS
    raw_path = ROOT / ".cache" / "dosm-wages-occupation" / "wages_occupation.csv"
    raw = rows(raw_path) if raw_path.exists() else None
    src = rows(DATA / "dosm" / "wages_occupation.csv")
    bad = []
    for major, (key, _) in MASCO_GROUPS.items():
        series = sorted((r for r in src if r["group"] == key and r["sex"] == "both" and int(r["date"][:4]) >= 2016),
                        key=lambda r: r["date"])
        a, b = series[0], series[-1]
        e0, e1, m0, m1 = float(a["recipients_k"]), float(b["recipients_k"]), int(a["median_rm"]), int(b["median_rm"])
        yrs = int(b["date"][:4]) - int(a["date"][:4])
        want = {"start": int(a["date"][:4]), "end": int(b["date"][:4]),
                "emp": (e0, e1, round(100 * (e1 - e0) / e0, 1), round(100 * ((e1 / e0) ** (1 / yrs) - 1), 1)),
                "pay": (m0, m1, round(100 * (m1 - m0) / m0, 1), round(100 * ((m1 / m0) ** (1 / yrs) - 1), 1))}
        t = group_trend(snaps, major)
        got = {"start": t["start"], "end": t["end"],
               "emp": tuple(t["employees_k"][k] for k in ("from", "to", "pct", "cagr")),
               "pay": tuple(t["median_rm"][k] for k in ("from", "to", "pct", "cagr"))}
        if got != want or not t["window_note"] or (want["start"] < 2020 < want["end"] and not t["notes"]):
            bad.append({"major": major, "got": got, "want": want})
    latest = max(r["date"] for r in src)
    pay_bad = [(major, reporter.median(major)) for major, (key, _) in MASCO_GROUPS.items()
               if reporter.median(major) != int(next(r["median_rm"] for r in src if r["date"] == latest and r["group"] == key
                                                     and r["sex"] == "both"))]
    raw_same = None
    if raw is not None:
        raw_same = sorted((r["date"], r["sex"], r["occupation"], r["recipients"], r["wages_median"]) for r in raw) == \
            sorted((r["date"], r["sex"], r["group"], r["recipients_k"], r["median_rm"]) for r in src)
    return {"check": "C4", "groups": len(MASCO_GROUPS), "mismatches": len(bad), "examples": bad[:3],
            "pay_steps_latest_year": latest[:4], "pay_mismatches": pay_bad, "derived_equals_raw_cache": raw_same,
            "pass": not bad and not pay_bad and raw_same is not False}


def c5(snaps):
    import export_forecasts
    import a18_backtest
    reg = snaps.registered
    rec = snaps.source("aivancin-registered-forecasts")
    bundled = DATA / rec["derived_path"]
    with tempfile.TemporaryDirectory() as tmp:
        out = export_forecasts.main(["--date", reg["registered_on"], "--out", tmp])
        same = out.read_bytes() == bundled.read_bytes()
        # A fixture release: every task's latest share plus 10 points (capped), in the builder's derived format.
        fixture = Path(tmp) / "fixture-release.csv"
        latest = snaps.evidence.releases[-1][1]
        with open(fixture, "w", encoding="utf-8", newline="") as f:
            wr = csv.writer(f, lineterminator="\n")
            wr.writerow(["level", "key", "share", "classified", "automation"])
            for tid, e in sorted(latest.items()):
                wr.writerow(["task", tid, f"{min(100.0, e['share'] + 10):.2f}", "", ""])
        pro = a18_backtest.prospective(bundled, fixture, snaps)
    return {"check": "C5", "byte_identical": same, "manifest_hash_matches": snapshots_sha(bundled) == rec["derived_sha256"],
            "registered_on": reg["registered_on"], "count": reg["count"], "prospective_fixture": pro,
            "pass": same and snapshots_sha(bundled) == rec["derived_sha256"] and pro["units"] > 0}


def snapshots_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run():
    from aivancin import intake, snapshots
    from aivancin.encoder import load_ai
    from aivancin.matching import Matcher
    from aivancin.report import Reporter
    from aivancin.routes import Planner
    snaps = snapshots.load(DATA)
    m = Matcher(snaps)
    rep = Reporter(snaps, m)
    planner = Planner(snaps, m, rep)
    encoder, vectors = load_ai(snaps)
    personas = {p["id"]: p for p in json.loads((DATA / "personas.json").read_text(encoding="utf-8"))}
    profiles = {}
    for pid, picks in (("hafiz", HAFIZ), ("priya", PRIYA)):
        req = {**personas[pid], "confirmed": [{"dwa_id": d, "source": "suggested"} for d in picks]}
        profiles[pid] = intake.build_profile(req, snaps.onet, intake.suggest(req, encoder, vectors, snaps.onet), snaps.cip)
    results = [c1(rep.demand, f2_raw()), c2(snaps, m), c3(planner, profiles), c4(snaps, rep), c5(snaps)]
    gated = [r for r in results if "pass" in r]
    return {"run_date": dt.date.today().isoformat(), "results": results,
            "all_pass": all(r["pass"] for r in gated), "of": len(gated)}


def main():
    res = run()
    text = json.dumps(res, indent=1)
    print(text)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
    sys.exit(0 if res["all_pass"] else 1)


if __name__ == "__main__":
    main()

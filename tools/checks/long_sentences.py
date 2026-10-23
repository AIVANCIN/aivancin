"""Long single-sentence intake check on tests/acceptance/long_sentences_set.json, offline.

python tools/checks/long_sentences.py [result.json]
Runs the set twice in one process: with long sentences read whole (the reading before 23 Oct; intake.LONG_WORDS set
to None) and with the shipped reading. Reports per sentence the true activities suggested, whether the guard flags it,
per profile the routes in family, and whether any sentence of the other acceptance sets changes its suggestions.
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
from aivancin.report import Reporter  # noqa: E402
from aivancin.routes import Planner  # noqa: E402

SET = ROOT / "tests" / "acceptance" / "long_sentences_set.json"
OTHER = ["a1_a3_sets.json", "a1_set_2.json", "heldout_casual.json", "work_terms_set.json", "ownership_pairs.json",
         "a6_guard_set.json", "a6_guard_set_2.json", "a6_guard_set_3.json", "a6_guard_set_4.json", "b7_profiles_7.json"]


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


def run(d, encoder, vectors, snaps, planner, today):
    rows = {}
    for s in d["own_work"] + d["controls"]:
        sug = intake.suggest({"other_work": s["text"]}, encoder, vectors, snaps.onet)
        sent = sug["sentences"]
        top = [x for t in sent for x in t["top"]]
        true = set(s.get("true", []))
        rows[s["id"]] = {"top": top, "hits": sorted(true & set(top)), "late_hits": sorted(set(s.get("late", [])) & set(top)),
                         "flagged": any(t["guard"] for t in sent), "sentences": len(sent)}
    profiles = []
    fam = d.get("families") or {}
    for p in d["profiles"]:
        text = " ".join(next(s["text"] for s in d["own_work"] if s["id"] == i) + "." for i in p["sentences"])
        req = {"other_work": text, "degree_level": p["level"], "weekly_hours": 6, "budget_rm": 0}
        sug = intake.suggest(req, encoder, vectors, snaps.onet)
        true = set().union(*(set(next(s for s in d["own_work"] if s["id"] == i)["true"]) for i in p["sentences"]))
        picks = {x for t in sug["sentences"] if t["guard"] is None for x in t["top"]} & true
        req["confirmed"] = [{"dwa_id": x, "source": "suggested"} for x in sorted(picks)]
        try:
            prof = intake.build_profile(req, snaps.onet, sug, snaps.cip)
            res = planner.routes(prof, {"weeks": 4}, today=today)
            routes = [(r["code"], r["family"]) for r in res["routes"]]
        except (intake.IntakeError, Exception) as exc:  # a profile the app cannot answer stays in the count
            routes, prof = [], None
            print("profile", p["id"], "error", exc)
        profiles.append({"id": p["id"], "confirmed": len(picks), "routes": routes,
                         "in_family": sum(f == p["family"] for _, f in routes)})
    return rows, profiles


def main():
    d = json.loads(SET.read_text(encoding="utf-8"))
    snaps = snapshots.load()
    encoder, vectors = load_ai(snaps)
    m = Matcher(snaps)
    planner = Planner(snaps, m, Reporter(snaps, m))
    today = dt.date(2026, 10, 23)
    shipped = intake.LONG_WORDS
    others = {}
    for name in OTHER:
        others[name] = list(dict.fromkeys(t for t in strings(json.loads((ROOT / "tests" / "acceptance" / name).read_text(encoding="utf-8")))
                                          if len(t.split()) >= 3))
    out = {}
    for label, value in (("before", None), ("after", shipped)):
        intake.LONG_WORDS = value
        rows, profiles = run(d, encoder, vectors, snaps, planner, today)
        other = {}
        for name, texts in others.items():
            other[name] = [intake.suggest({"other_work": t}, encoder, vectors, snaps.onet)["suggestions"] for t in texts]
        out[label] = {"rows": rows, "profiles": profiles, "other": other}
    intake.LONG_WORDS = shipped
    own = [s["id"] for s in d["own_work"]]
    late_needed = [s["id"] for s in d["own_work"] if s["late"]]
    b, a = out["before"]["rows"], out["after"]["rows"]
    changed_other = {n: sum(x != y for x, y in zip(out["before"]["other"][n], out["after"]["other"][n])) for n in OTHER}
    res = {"set_sha256": hashlib.sha256(SET.read_bytes()).hexdigest(),
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "run_date": dt.date.today().isoformat(),
           "long_words": shipped, "true_total": sum(len(s["true"]) for s in d["own_work"]),
           "hits_before": sum(len(b[i]["hits"]) for i in own), "hits_after": sum(len(a[i]["hits"]) for i in own),
           "late_reached_before": sum(bool(b[i]["late_hits"]) for i in late_needed),
           "late_reached_after": sum(bool(a[i]["late_hits"]) for i in late_needed), "late_of": len(late_needed),
           "own_flagged_before": [i for i in own if b[i]["flagged"]], "own_flagged_after": [i for i in own if a[i]["flagged"]],
           "controls_flagged_after": {c["id"]: a[c["id"]]["flagged"] for c in d["controls"]},
           "profile_in_family_before": sum(p["in_family"] for p in out["before"]["profiles"]),
           "profile_in_family_after": sum(p["in_family"] for p in out["after"]["profiles"]),
           "other_sets_changed": changed_other,
           "per_sentence": {i: {"before": b[i]["hits"], "after": a[i]["hits"], "flagged_after": a[i]["flagged"]} for i in b},
           "profiles": {"before": out["before"]["profiles"], "after": out["after"]["profiles"]}}
    text = json.dumps(res, indent=1)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("per_sentence", "profiles")}, indent=1))
    for i, r in res["per_sentence"].items():
        print(i, "before", len(r["before"]), "after", len(r["after"]), "flagged" if r["flagged_after"] else "")
    for x, y in zip(res["profiles"]["before"], res["profiles"]["after"]):
        print(x["id"], "confirmed", x["confirmed"], "->", y["confirmed"], "in family", x["in_family"], "->", y["in_family"], y["routes"])


if __name__ == "__main__":
    main()

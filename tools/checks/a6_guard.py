"""A6 guard check (SPEC section 9) on tests/acceptance/a6_guard_set.json, offline.

python tools/checks/a6_guard.py [result.json] [sets.json]
For each sentence, every suggested activity is submitted as confirmed without the deliberate second
action, through the same validation the API uses. A sentence passes when every such attempt is refused.
sets.json defaults to tests/acceptance/a6_guard_set.json; tests/acceptance/a6_guard_set_2.json (C12) adds controls:
own-work sentences that pass when they are not flagged and their suggestions stay confirmable with one tick.
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

SETS = ROOT / "tests" / "acceptance" / "a6_guard_set.json"


def main():
    path = Path(sys.argv[2]) if len(sys.argv) > 2 else SETS
    sets = json.loads(path.read_text(encoding="utf-8"))
    snaps = snapshots.load()
    encoder, vectors = load_ai(snaps)
    rows = []
    cases = sets.get("s2_k1_negatives", []) + sets.get("fresh", []) + sets.get("uncaught", [])
    for case in cases + [{**c, "kind": "control"} for c in sets.get("controls", [])]:
        req = {"other_work": case["text"], "weekly_hours": 5, "budget_rm": 0, "degree_level": "degree"}
        sug = intake.suggest(req, encoder, vectors, snaps.onet)
        accepted = []
        for s in sug["suggestions"]:
            try:
                intake.build_profile({**req, "confirmed": [{"dwa_id": s["dwa_id"], "source": "suggested"}]},
                                     snaps.onet, sug, snaps.cip)
                accepted.append(s["name"])
            except intake.IntakeError:
                pass
        flags = [x["guard"] for x in sug["sentences"]]
        control = case["kind"] == "control"
        ok = (not any(flags) and len(accepted) == len(sug["suggestions"])) if control else not accepted
        rows.append({"id": case["id"], "kind": case["kind"], "guard": flags, "suggestions": len(sug["suggestions"]),
                     "confirmable_without_override": accepted, "pass": ok})
    fresh = [r for r in rows if r["id"].startswith(("f", "g"))]
    controls = [r for r in rows if r["kind"] == "control"]
    res = {"sets": path.name, "sets_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "run_date": dt.date.today().isoformat(),
           "passed": sum(r["pass"] for r in rows), "of": len(rows),
           "fresh_passed": sum(r["pass"] for r in fresh), "fresh_of": len(fresh),
           "controls_passed": sum(r["pass"] for r in controls), "controls_of": len(controls),
           "pass": all(r["pass"] for r in rows), "cases": rows}
    text = json.dumps(res, indent=1, ensure_ascii=False)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("passed", "of", "fresh_passed", "fresh_of", "controls_passed", "controls_of", "pass")}))
    for r in rows:
        flag = r["guard"][0]["cue"] if r["guard"] and r["guard"][0] else "-"
        print(r["id"], r["kind"], "cue:", flag, "PASS" if r["pass"] else f"FAIL ({len(r['confirmable_without_override'])} confirmable)")


if __name__ == "__main__":
    main()

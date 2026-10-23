"""Negation scope check on tests/acceptance/negation_scope_set.json, offline (no model needed).

python tools/checks/negation_scope.py [result.json]
Runs the guard with the scope rule (intake.NEGATION_SCOPE True) and without it (any cue counts, the rule before 23 Oct)
on the set, and lists every sentence of the other acceptance sets whose guard reading changes, so a newly unflagged
negation cannot pass unnoticed.
"""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aivancin import intake  # noqa: E402

SET = ROOT / "tests" / "acceptance" / "negation_scope_set.json"


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


def reading(text, scope):
    intake.NEGATION_SCOPE = scope
    return [(b, intake.guard(b)) for _, _, b in intake.split_sentences(text)]


def main():
    d = json.loads(SET.read_text(encoding="utf-8"))
    shipped = intake.NEGATION_SCOPE
    rows = {"own_work": [], "negated": []}
    for group in rows:
        for t in d[group]:
            before, after = reading(t, False)[0][1], reading(t, True)[0][1]
            ok = (after is None or after["kind"] != "negated") if group == "own_work" else after is not None
            rows[group].append({"text": t, "before": before, "after": after, "pass": ok})
    changed = []
    for f in sorted((ROOT / "tests" / "acceptance").glob("*.json")):
        if f == SET:
            continue
        seen = set()
        for text in strings(json.loads(f.read_text(encoding="utf-8"))):
            for (b, g0), (_, g1) in zip(reading(text, False), reading(text, True)):
                if b not in seen and len(b.split()) >= 3 and g0 != g1:
                    changed.append({"set": f.name, "sentence": b, "before": g0, "after": g1})
                seen.add(b)
    intake.NEGATION_SCOPE = shipped
    res = {"set_sha256": hashlib.sha256(SET.read_bytes()).hexdigest(), "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "run_date": dt.date.today().isoformat(),
           "own_work_unflagged": sum(r["pass"] for r in rows["own_work"]), "own_work_of": len(rows["own_work"]),
           "own_work_unflagged_before": sum(r["before"] is None for r in rows["own_work"]),
           "negated_held": sum(r["pass"] for r in rows["negated"]), "negated_of": len(rows["negated"]),
           "other_sets_changed": changed, "rows": rows}
    text = json.dumps(res, indent=1)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("rows", "other_sets_changed")}))
    for r in rows["own_work"] + rows["negated"]:
        print("PASS" if r["pass"] else "FAIL", r["text"][:70], "|", r["before"] and r["before"]["kind"], "->", r["after"] and r["after"]["kind"])
    print(len(changed), "sentences of other sets change:")
    for c in changed:
        print(" ", c["set"], "|", c["sentence"][:90], "|", c["before"] and c["before"]["kind"], "->", c["after"] and c["after"]["kind"])


if __name__ == "__main__":
    main()

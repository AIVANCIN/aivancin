"""Ownership pairs and self-introductions (tests/acceptance/ownership_pairs.json), offline.

python tools/checks/ownership.py [result.json]
Pairs: each sentence's guard verdict against its expectation (own: no guard; not_own: a guard that keeps it out;
unclear: the "unclear who" question). Names: for each template, whether every name gives identical suggestions
(same activities, same order, sentence by sentence). Prints the failures and the counts.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aivancin import intake, snapshots  # noqa: E402
from aivancin.encoder import load_ai  # noqa: E402

SET = ROOT / "tests" / "acceptance" / "ownership_pairs.json"


def verdict(sentence):
    g = intake.guard(sentence)
    if g is None:
        return "own", None
    return ("unclear" if g["kind"] == "unclear who" else "not_own"), g


def ok(expected, got):
    return got == expected or (expected == "not_own" and got == "unclear")


def main():
    data = json.loads(SET.read_text(encoding="utf-8"))
    rows, fails = [], []
    for p in data["pairs"]:
        for side in ("a", "b"):
            text, expected = p[side]
            got, g = verdict(text)
            rows.append({"id": f"{p['id']}{side}", "text": text, "expected": expected, "got": got, "guard": g,
                         "pass": ok(expected, got)})
            if not ok(expected, got):
                fails.append(rows[-1])
    snaps = snapshots.load()
    encoder, vectors = load_ai(snaps)
    names = []
    for t in data["names"]["templates"]:
        seen = {}
        for n in data["names"]["names"]:
            res = intake.suggest({"current_job": "", "other_work": t.replace("{N}", n), "studies": ""}, encoder, vectors,
                                 snaps.onet)
            seen[n] = [s["top"] for s in res["sentences"]]
        variants = {json.dumps(v) for v in seen.values()}
        names.append({"template": t, "identical": len(variants) == 1, "variants": len(variants),
                      "differ": [n for n in seen if seen[n] != seen[data["names"]["names"][0]]]})
    pairs_pass = sum(r["pass"] for r in rows)
    own = [r for r in rows if r["expected"] == "own"]
    out = {"pairs_pass": pairs_pass, "sentences": len(rows), "own_controls_pass": sum(r["pass"] for r in own),
           "own_controls": len(own), "names_identical": sum(n["identical"] for n in names), "templates": len(names),
           "fails": fails, "names": names}
    text = json.dumps(out, indent=1)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
    for f in fails:
        print("FAIL", f["id"], f["expected"], "got", f["got"], f["text"], f["guard"])
    for n in names:
        if not n["identical"]:
            print("NAMES DIFFER", n["variants"], n["template"], n["differ"])
    print(f"pairs {pairs_pass}/{len(rows)} (own controls {out['own_controls_pass']}/{len(own)}); "
          f"name templates identical {out['names_identical']}/{len(names)}")


if __name__ == "__main__":
    main()

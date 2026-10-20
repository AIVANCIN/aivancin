"""A1-A3 acceptance checks (SPEC section 9) on tests/acceptance/a1_a3_sets.json, offline.

python tools/checks/a1_a3.py [result.json]
A1: intake accuracy. A2: the complete chain with strict simulated confirmation. A3: the same chain
with BM25 keyword suggestions instead of the model. Pass rules are in the sets file, written first.
"""
import datetime as dt
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aivancin import intake, snapshots  # noqa: E402
from aivancin.encoder import load_ai  # noqa: E402
from aivancin.matching import Matcher  # noqa: E402

SETS = ROOT / "tests" / "acceptance" / "a1_a3_sets.json"
STOP = set(("a an the and or of to in on for with at by from as is are be this that it its into than then so do does "
            "did i we you they he she my our their your me us them was were has have had not no any all some each every "
            "one can will would should could about up out over under just only also when while if but because").split())


class BM25:
    """Keyword baseline over activity names (k1 1.5, b 0.75), as in the selection probes."""

    def __init__(self, ids, names, k1=1.5, b=0.75):
        self.ids = ids
        self.docs = [self.toks(n) for n in names]
        self.avg = sum(map(len, self.docs)) / len(self.docs)
        self.df = Counter(t for d in self.docs for t in set(d))
        self.tfs = [Counter(d) for d in self.docs]
        self.k1, self.b = k1, b

    @staticmethod
    def toks(s):
        return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP]

    def top(self, query, k=5):
        n = len(self.docs)
        scores = [0.0] * n
        for t in self.toks(query):
            if t not in self.df:
                continue
            idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for i, tf in enumerate(self.tfs):
                f = tf.get(t, 0)
                if f:
                    scores[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(self.docs[i]) / self.avg))
        order = sorted((i for i in range(n) if scores[i] > 0), key=lambda i: (-scores[i], self.ids[i]))[:k]
        return [self.ids[i] for i in order]


def relevant(code, title, rule):
    return code.startswith(tuple(rule["relevant_soc"])) or re.search(rule["relevant_words"], title, re.I) is not None


def main():
    sets = json.loads(SETS.read_text(encoding="utf-8"))
    snaps = snapshots.load()
    encoder, vectors = load_ai(snaps)
    onet = snaps.onet
    matcher = Matcher(snaps)
    bm25 = BM25(onet.dwa_ids, [onet.dwa_names[d] for d in onet.dwa_ids])

    a1 = []
    for case in sets["a1"]:
        sug = intake.suggest({"other_work": case["text"]}, encoder, vectors, onet)
        top = [d for s in sug["sentences"] for d in s["top"]]
        hit = bool(set(top) & set(case["labels"]))
        a1.append({"id": case["id"], "pass": hit, "top5": [onet.dwa_names[d] for d in top]})

    def chain(p, confirmed):
        prof = set(confirmed)
        top = matcher.rank(prof, "other_work", current_code=p["job"], limit=5)["roles"]
        rel = [relevant(r["code"], onet.occupations[r["code"]], p) for r in top]
        return {"confirmed": len(prof), "true_size": len(p["true"]), "top5": [onet.occupations[r["code"]] for r in top],
                "relevant": sum(rel)}

    a2, a3 = [], []
    for p in sets["a2"]:
        sug = intake.suggest({"other_work": p["other_work"]}, encoder, vectors, onet)
        model = {d for s in sug["sentences"] if s["guard"] is None for d in s["top"]} & set(p["true"])
        keyword = {d for s in sug["sentences"] if s["guard"] is None for d in bm25.top(s["text"])} & set(p["true"])
        a2.append({"id": p["id"], "name": p["name"], **chain(p, model)})
        a3.append({"id": p["id"], "name": p["name"], **chain(p, keyword)})

    a1_n = sum(r["pass"] for r in a1)
    a2_rel = sum(r["relevant"] for r in a2)
    a2_ok = sum(r["relevant"] >= 3 for r in a2)
    a3_rel = sum(r["relevant"] for r in a3)
    res = {
        "sets_sha256": hashlib.sha256(SETS.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "run_date": dt.date.today().isoformat(),
        "A1": {"passed": a1_n, "of": len(a1), "pass": a1_n >= 16, "cases": a1},
        "A2": {"relevant_top5": a2_rel, "of": 5 * len(a2), "profiles_3plus": a2_ok,
               "pass": a2_rel >= 30 and a2_ok >= 7, "profiles": a2},
        "A3": {"model_relevant": a2_rel, "keyword_relevant": a3_rel, "margin": a2_rel - a3_rel,
               "pass": a2_rel - a3_rel >= 10, "profiles": a3},
    }
    text = json.dumps(res, indent=1, ensure_ascii=False)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: {x: v for x, v in res[k].items() if x not in ("cases", "profiles")} for k in ("A1", "A2", "A3")},
                     indent=1))
    for k in ("A2", "A3"):
        for r in res[k]["profiles"]:
            print(k, r["name"], f"confirmed {r['confirmed']}/{r['true_size']}", "relevant", r["relevant"], "|", "; ".join(r["top5"]))
    for r in a1:
        if not r["pass"]:
            print("A1 miss", r["id"], "|", "; ".join(r["top5"]))


if __name__ == "__main__":
    main()

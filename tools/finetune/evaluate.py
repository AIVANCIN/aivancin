"""The fine-tuning gate (SPEC 8, A17), run locally on the CPU with the app's own encoder and scoring.

python tools/finetune/evaluate.py --candidate <dir> [--control <dir>] [--k7 <dir>] [--out result.json]
<dir> holds model.onnx and tokenizer.json (from the pod's export). The candidate is compared with the shipped model
(data/model) on the held-out casual set (tests/acceptance/heldout_casual.json): top-five accuracy, the app's
activity score (0.7 name, 0.3 closest linked task statement) with vectors recomputed for each model, one text per
run. --control is the pod's re-export of the base model through the same export code, so an export effect shows
apart from training. --k7 is the folder holding the S2-K7 regression inputs (profiles_k6.json, narratives_k7.json;
kept outside the repo); the chain must not fall below the shipped model's. The A1 sets are reported as information.
Gate: candidate held-out hits above both the shipped model's and the control's (the export alone can move the score),
and the S2-K7 chain not lower than the shipped model's. Without --k7 the gate is incomplete.
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aivancin import intake, snapshots  # noqa: E402
from aivancin.encoder import ActivityIndex, Encoder, load_ai, task_statements  # noqa: E402
from aivancin.matching import Matcher  # noqa: E402

HELDOUT = ROOT / "tests" / "acceptance" / "heldout_casual.json"
A1_SETS = [ROOT / "tests" / "acceptance" / "a1_a3_sets.json", ROOT / "tests" / "acceptance" / "a1_set_2.json"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def candidate_ai(model_dir, snaps):
    """Encoder and activity index for a model directory, with activity and task vectors recomputed for it."""
    d = Path(model_dir)
    enc = Encoder(d / "model.onnx", d / "tokenizer.json")
    onet = snaps.onet
    names = enc.encode([onet.dwa_names[i] for i in onet.dwa_ids])
    texts, links, _ = task_statements(onet)
    tasks = np.vstack([enc.encode([t]) for t in texts])
    return enc, ActivityIndex(names, tasks, links, onet.dwa_ids)


def top5(text, ai, onet):
    enc, idx = ai
    sug = intake.suggest({"other_work": text}, enc, idx, onet)
    return [d for s in sug["sentences"] for d in s["top"]]


def heldout(ai, onet):
    cases = json.loads(HELDOUT.read_text(encoding="utf-8"))["cases"]
    rows = [{"id": c["id"], "hit": bool(set(top5(c["text"], ai, onet)) & set(c["labels"]))} for c in cases]
    return {"hits": sum(r["hit"] for r in rows), "of": len(rows), "missed": [r["id"] for r in rows if not r["hit"]]}


def a1(ai, onet):
    out = {}
    for path in A1_SETS:
        cases = json.loads(path.read_text(encoding="utf-8"))["a1"]
        out[path.name] = sum(bool(set(top5(c["text"], ai, onet)) & set(c["labels"])) for c in cases)
    return out


def k7_chain(ai, snaps, folder):
    """The A4 regression's S2-K7 chain arm in the probe's original pool (Job Zone 3-4, no skill-level filter)."""
    folder = Path(folder)
    profiles = json.loads((folder / "profiles_k6.json").read_text(encoding="utf-8"))["profiles"]
    narr = json.loads((folder / "narratives_k7.json").read_text(encoding="utf-8"))["narratives"]
    m = Matcher(snaps)
    enc, idx = ai
    total = ok = 0
    for p in profiles:
        lane = set(p["lane"])
        conf = set()
        for s in narr[p["name"]]:
            sug = intake.suggest({"other_work": s}, enc, idx, snaps.onet)
            conf |= {d for x in sug["sentences"] if x["guard"] is None for d in x["top"]} & lane
        top = m.rank(conf, "other_work", current_code=p["job"], limit=5)["roles"]
        rel = sum(any(r["code"].startswith(s) for s in p["soc"]) or
                  re.search(p["words"], snaps.onet.occupations[r["code"]], re.I) is not None for r in top)
        total += rel
        ok += rel >= 3
    return {"relevant_top5": total, "profiles_3plus": ok,
            "inputs": {f: sha(folder / f) for f in ("profiles_k6.json", "narratives_k7.json")}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--control")
    ap.add_argument("--k7")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    snaps = snapshots.load()
    onet = snaps.onet
    models = {"shipped": load_ai(snaps)}
    if a.control:
        models["control"] = candidate_ai(a.control, snaps)
    models["candidate"] = candidate_ai(a.candidate, snaps)
    res = {"run_date": dt.date.today().isoformat(), "heldout_sha256": sha(HELDOUT),
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
           "models": {"shipped": snaps.source("minilm-onnx")["derived_sha256"],
                      "candidate": sha(Path(a.candidate) / "model.onnx"),
                      **({"control": sha(Path(a.control) / "model.onnx")} if a.control else {})},
           "heldout": {}, "a1": {}, "k7": {}}
    for name, ai in models.items():
        res["heldout"][name] = heldout(ai, onet)
        res["a1"][name] = a1(ai, onet)
        if a.k7:
            res["k7"][name] = k7_chain(ai, snaps, a.k7)
    # The export alone moved the held-out score by one in the CPU smoke test (base re-export 40/60, shipped 39/60), so a
    # candidate must also beat the base model put through the same export: the gain has to come from training.
    better = res["heldout"]["candidate"]["hits"] > res["heldout"]["shipped"]["hits"] and         (not a.control or res["heldout"]["candidate"]["hits"] > res["heldout"]["control"]["hits"])
    if a.k7:
        kept = res["k7"]["candidate"]["relevant_top5"] >= res["k7"]["shipped"]["relevant_top5"]
        res["gate"] = {"heldout_better": better, "k7_not_lower": kept, "ship": better and kept}
    else:
        res["gate"] = {"heldout_better": better, "k7_not_lower": None, "ship": False,
                       "note": "incomplete: run with --k7 before shipping"}
    text = json.dumps(res, indent=1)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    return res


if __name__ == "__main__":
    main()

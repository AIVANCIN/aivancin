"""Work-term check (SPEC 11.12) on tests/acceptance/work_terms_set.json, offline.

python tools/checks/work_terms.py result.json [baseline.json]
Writes each sentence's five suggestions and counts target hits (any expected activity among the five). With a
baseline (an earlier result.json), each control's five suggestions are compared with the baseline's.
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

SET = ROOT / "tests" / "acceptance" / "work_terms_set.json"


def top5(text, encoder, index, onet):
    s = intake.suggest({"other_work": text}, encoder, index, onet)["sentences"]
    return [d for x in s for d in x["top"]][:5], [x["guard"] for x in s]


def main():
    data = json.loads(SET.read_text(encoding="utf-8"))
    snaps = snapshots.load()
    encoder, index = load_ai(snaps)
    base = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")) if len(sys.argv) > 2 else None
    rows = []
    for t in data["targets"]:
        top, guards = top5(t["text"], encoder, index, snaps.onet)
        hit = any(d in data["expected"][t["kind"]] for d in top)
        rows.append({"id": t["id"], "group": "target", "text": t["text"], "top": top, "guards": guards, "hit": hit})
    for c in data["controls"]:
        top, guards = top5(c["text"], encoder, index, snaps.onet)
        same = None if base is None else top == next(r["top"] for r in base["rows"] if r["id"] == c["id"])
        rows.append({"id": c["id"], "group": "control", "text": c["text"], "top": top, "guards": guards, "same": same})
    targets = [r for r in rows if r["group"] == "target"]
    controls = [r for r in rows if r["group"] == "control"]
    res = {"set_sha256": hashlib.sha256(SET.read_bytes()).hexdigest(),
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "run_date": dt.date.today().isoformat(),
           "target_hits": sum(r["hit"] for r in targets), "targets": len(targets),
           "controls_same": None if base is None else sum(bool(r["same"]) for r in controls), "controls": len(controls),
           "rows": rows}
    Path(sys.argv[1]).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    names = snaps.onet.dwa_names
    for r in rows:
        mark = ("HIT " if r["hit"] else "miss") if r["group"] == "target" else \
               ("----" if r["same"] is None else ("same" if r["same"] else "DIFF"))
        print(mark, r["id"], r["text"][:70], "|", names[r["top"][0]][:50] if r["top"] else "(none)")
    print(json.dumps({k: res[k] for k in ("target_hits", "targets", "controls_same", "controls")}))


if __name__ == "__main__":
    main()

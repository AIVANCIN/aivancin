"""C13 fairness check (SPEC 11.7) on tests/acceptance/fairness_pairs.json, offline.

python tools/checks/c13_fairness.py [result.json] [pairs.json]
Each base sentence is varied by name (with the matching his/her and he/she), by gendered kinship words and by
stated age; every variant must give the same suggestions (activities and order, per sentence) and the same guard
flag. Each profile is varied the same way and must give the same routes after strict confirmation (a suggestion is
ticked only if it is in the profile's true set). Differences are listed.
"""
import datetime as dt
import hashlib
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aivancin import intake, snapshots  # noqa: E402
from aivancin.encoder import load_ai  # noqa: E402
from aivancin.matching import Matcher  # noqa: E402
from aivancin.report import Reporter, ReportError  # noqa: E402
from aivancin.routes import Planner  # noqa: E402

PAIRS = ROOT / "tests" / "acceptance" / "fairness_pairs.json"
TODAY = dt.date(2026, 10, 21)


def variants(template, pairs):
    """Every combination of the slots the template uses; his/he follow the name when there is one, else the kin."""
    dims = []
    if "{name}" in template:
        dims.append([{"name": n["name"], "his": n["his"], "he": n["he"], "_who": f"{n['name']} ({n['background']})"}
                     for n in pairs["names"]])
    if "{sib}" in template or "{par}" in template:
        dims.append([{"sib": k["sib"], "par": k["par"], "kin_his": k["his"], "_kin": k["sib"] + "/" + k["par"]} for k in pairs["kin"]])
    if "{age}" in template:
        dims.append([{"age": a, "_age": str(a)} for a in pairs["ages"]])
    out = []
    for combo in itertools.product(*dims) if dims else [()]:
        slots = {}
        for part in combo:
            slots.update(part)
        if "his" not in slots and "kin_his" in slots:
            slots["his"] = slots["kin_his"]
        label = " / ".join(slots[k] for k in ("_who", "_kin", "_age") if k in slots)
        text = template
        for k in ("name", "sib", "par", "his", "he", "age"):
            text = text.replace("{" + k + "}", str(slots.get(k, "{" + k + "}")))
        out.append((label, text))
    return out


def suggestion_key(text, encoder, vectors, onet):
    sug = intake.suggest({"other_work": text}, encoder, vectors, onet)
    return [s["top"] for s in sug["sentences"]], [(s["guard"] or {}).get("kind") for s in sug["sentences"]], sug


def run(pairs_path=PAIRS):
    pairs = json.loads(Path(pairs_path).read_text(encoding="utf-8"))
    snaps = snapshots.load()
    encoder, vectors = load_ai(snaps)
    onet = snaps.onet
    m = Matcher(snaps)
    planner = Planner(snaps, m, Reporter(snaps, m))
    sentence_rows = []
    for base in pairs["sentences"]:
        vs = variants(base["text"], pairs)
        ref_label, ref_text = vs[0]
        ref_top, ref_guard, _ = suggestion_key(ref_text, encoder, vectors, onet)
        diffs = []
        for label, text in vs[1:]:
            top, flags, _ = suggestion_key(text, encoder, vectors, onet)
            if top != ref_top or flags != ref_guard:
                same_set = [set(a) == set(b) for a, b in zip(top, ref_top)]
                diffs.append({"variant": label, "text": text, "same_activities_other_order": all(same_set) and len(top) == len(ref_top),
                              "top": [[onet.dwa_names[d] for d in s] for s in top], "guard": flags})
        sentence_rows.append({"id": base["id"], "variants": len(vs), "reference": ref_label,
                              "reference_top": [[onet.dwa_names[d] for d in s] for s in ref_top],
                              "identical": not diffs, "differences": diffs})
    profile_rows = []
    for p in pairs["profiles"]:
        vs = variants(p["other_work"], pairs)
        results = []
        for label, text in vs:
            req = {"other_work": text, "title_code": p["job"], "degree_level": p["level"], "weekly_hours": p["hours"],
                   "budget_rm": p["budget"]}
            sug = intake.suggest(req, encoder, vectors, onet)
            picks = {d for s in sug["sentences"] if s["guard"] is None for d in s["top"]} & set(p["true"])
            req["confirmed"] = [{"dwa_id": d, "source": "suggested"} for d in sorted(picks)]
            try:
                prof = intake.build_profile(req, onet, sug, snaps.cip)
                codes = [r["code"] for r in planner.routes(prof, {"weeks": 4}, today=TODAY)["routes"]]
            except (intake.IntakeError, ReportError) as exc:
                codes = [f"error: {exc}"]
            results.append({"variant": label, "confirmed": sorted(picks), "routes": codes})
        ref = results[0]
        diffs = [r for r in results[1:] if r["routes"] != ref["routes"] or r["confirmed"] != ref["confirmed"]]
        profile_rows.append({"id": p["id"], "variants": len(vs), "reference": ref, "identical": not diffs,
                             "differences": diffs})
    return {"pairs": Path(pairs_path).name, "pairs_sha256": hashlib.sha256(Path(pairs_path).read_bytes()).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
            "run_date": dt.date.today().isoformat(),
            "sentences_identical": sum(r["identical"] for r in sentence_rows), "sentences_of": len(sentence_rows),
            "sentence_variants": sum(r["variants"] for r in sentence_rows),
            "variants_differing": sum(len(r["differences"]) for r in sentence_rows),
            "profiles_identical": sum(r["identical"] for r in profile_rows), "profiles_of": len(profile_rows),
            "pass": all(r["identical"] for r in sentence_rows + profile_rows),
            "sentences": sentence_rows, "profiles": profile_rows}


def main():
    res = run(Path(sys.argv[2]) if len(sys.argv) > 2 else PAIRS)
    text = json.dumps(res, indent=1, ensure_ascii=False)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("sentences_identical", "sentences_of", "sentence_variants", "variants_differing",
                                          "profiles_identical", "profiles_of", "pass")}))
    for r in res["sentences"]:
        for d in r["differences"]:
            print(r["id"], "|", d["variant"], "| reorder only" if d["same_activities_other_order"] else "| different activities",
                  "| guard", d["guard"])
    for r in res["profiles"]:
        for d in r["differences"]:
            print(r["id"], "|", d["variant"], "| routes", d["routes"], "vs", r["reference"]["routes"])


if __name__ == "__main__":
    main()

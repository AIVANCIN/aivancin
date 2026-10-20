"""Build data/routes/destinations.json: supported destinations with sourced requirements (SPEC section 10.1).

Run from the repo root after tools/build_catalogue.py: python tools/build_routes.py
then python tools/build_snapshots.py so the manifest records the file's hash.
Requirements come from the bundled O*NET 31.0 extracts and O*NET's own reference text (cached by the
snapshot builder); they are United States data and are labelled so. The admission rule is applied here and
every result, pass or fail, is written to the file.
"""
import csv
import datetime as dt
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aivancin import snapshots  # noqa: E402
from aivancin.matching import Matcher  # noqa: E402
from aivancin.pathway import verified_courses  # noqa: E402
from aivancin.report import Reporter  # noqa: E402
from aivancin.routes import fits_family  # noqa: E402

CURATION = ROOT / "tools" / "destinations_curation.json"
OUT = ROOT / "data" / "routes" / "destinations.json"
ONET_URL = "https://www.onetonline.org/link/summary/"
TOP_ACTIVITIES = 10


def raw(path):
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))


def main():
    cur = json.loads(CURATION.read_text(encoding="utf-8"))
    snaps = snapshots.load()
    m = Matcher(snaps)
    rep = Reporter(snaps, m)
    onet = snaps.onet
    data = ROOT / "data" / "onet"
    zones = {r["job_zone"]: {"Education": r["education"]} for r in raw(data / "job_zone_reference.csv")}
    rl_names = {int(r["category"]): r["description"] for r in raw(data / "education_categories.csv")}
    rw_names = {int(r["category"]): r["description"] for r in raw(data / "experience_categories.csv")}
    courses = [c for c in verified_courses(snaps.catalogue, dt.date.today()) if c.get("checkpoint")]
    out = []
    for d in cur["destinations"]:
        code = d["code"]
        jz = onet.job_zones.get(code)
        masco = rep.masco(code)
        weights = m.w.get(code, {})
        top = [a for a, _ in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_ACTIVITIES]]
        usable = [c for c in courses if fits_family(c, d["family"])]
        covered = sorted({a for a in top for c in usable if a in c.get("dwas", [])})
        reqs = []
        if jz and str(jz) in zones:
            reqs.append({"id": "job_zone_education", "kind": "qualification", "scope": "US",
                         "text": f"O*NET Job Zone {jz}: {zones[str(jz)]['Education']}",
                         "judge": "degree" if jz >= 4 else "you_decide",
                         "src": ["onet31-job-zones", "onet31-job-zone-reference"], "url": ONET_URL + code})
        rl = onet.education.get(code)
        if rl:
            reqs.append({"id": "typical_education", "kind": "qualification", "scope": "US",
                         "text": f"Most common required education in the US: {rl_names.get(rl[0], rl[0])} ({rl[1]:.0f}% of respondents)",
                         "judge": "degree" if rl[0] >= 6 else "you_decide", "src": ["onet31-education", "onet31-education-categories"],
                         "url": ONET_URL + code})
        rw = onet.experience.get(code)
        if rw:
            reqs.append({"id": "related_experience", "kind": "experience", "scope": "US",
                         "text": f"Most common related work experience in the US: {rw_names.get(rw[0], rw[0])} ({rw[1]:.0f}% of respondents)",
                         "judge": "you_decide" if rw[0] >= 2 else "none", "src": ["onet31-experience", "onet31-experience-categories"],
                         "url": ONET_URL + code})
        for lr in d.get("local_requirements", []):
            reqs.append({**lr, "scope": "Malaysia"})
        checks = {"job_zone_3_or_4": jz in (3, 4), "masco_skilled": masco.get("skill") == "skilled",
                  "sourced_requirement": bool(reqs), "activity_links_10_plus": len(weights) >= 10,
                  "resources_for_3_top_activities": len(covered) >= 3}
        out.append({"code": code, "title": onet.occupations[code], "family": d["family"], "family_label": cur["families"][d["family"]],
                    "job_zone": jz, "masco": masco, "requirements": reqs, "top_activities": top,
                    "top_activities_with_resources": covered, "admission": checks, "admitted": all(checks.values())})
    result = {"built_on": dt.date.today().isoformat(), "curated_on": cur["curated_on"], "families": cur["families"],
              "destinations": out}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    for d in out:
        print("ADMITTED" if d["admitted"] else "EXCLUDED", d["code"], d["title"], len(d["top_activities_with_resources"]),
              [k for k, v in d["admission"].items() if not v])


if __name__ == "__main__":
    main()

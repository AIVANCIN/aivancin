"""A7 requirement-label check (SPEC section 9) against the raw O*NET 31.0 downloads in .cache/.

python tools/checks/a7_labels.py [result.json]   (run tools/build_snapshots.py first so .cache/ holds the raw files)

Sample, fixed before the first run: the ranked candidate pool (Job Zone 3-4, no skill-level filter) in
code order, taking positions round(i x (n - 1) / 19) for i = 0..19. For a diploma holder, "usually needs a
bachelor's degree" must appear exactly when the raw Job Zone is 4, and "needs related experience" exactly
when the raw modal Related Work Experience category (ties to the lower category) is above one year
(category 6 or more) or the title begins "First-Line Supervisors". The displayed Job Zone must equal the raw one.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aivancin import snapshots  # noqa: E402
from aivancin.matching import Matcher  # noqa: E402
from aivancin.report import Reporter  # noqa: E402

RAW_JZ = ROOT / ".cache" / "onet31-job-zones" / "job_zones.csv"
RAW_TE = ROOT / ".cache" / "onet31-experience" / "training_and_experience.csv"


def raw_rows(path):
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))


def main():
    jz = {r["O*NET-SOC Code"]: int(r["Job Zone"]) for r in raw_rows(RAW_JZ)}
    rw = defaultdict(list)
    for r in raw_rows(RAW_TE):
        if r["Scale ID"] == "RW":
            rw[r["O*NET-SOC Code"]].append((float(r["Data Value"]), int(r["Category"])))
    modal = {c: min(v, key=lambda x: (-x[0], x[1]))[1] for c, v in rw.items()}

    snaps = snapshots.load()
    matcher = Matcher(snaps)
    rep = Reporter(snaps, matcher)
    pool = matcher.candidates()
    n = len(pool)
    sample = [pool[round(i * (n - 1) / 19)] for i in range(20)]
    profile = {"degree_level": "diploma", "cip_code": None}
    rows = []
    for code in sample:
        title = snaps.onet.occupations[code]
        ids = {lab["id"] for lab in rep.labels(code, profile)}
        exp_expected = title.startswith("First-Line Supervisors") or modal.get(code, 0) >= 6
        checks = {"job_zone": snaps.onet.job_zones.get(code) == jz.get(code),
                  "needs_degree": ("needs_degree" in ids) == (jz.get(code) == 4),
                  "needs_experience": ("needs_experience" in ids) == exp_expected}
        rows.append({"code": code, "title": title, "raw_job_zone": jz.get(code), "raw_rw_modal": modal.get(code),
                     "labels": sorted(ids), **checks, "pass": all(checks.values())})
    res = {"script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "raw_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (RAW_JZ, RAW_TE)},
           "run_date": dt.date.today().isoformat(), "pool": n,
           "passed": sum(r["pass"] for r in rows), "of": len(rows), "pass": all(r["pass"] for r in rows), "roles": rows}
    text = json.dumps(res, indent=1)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("pool", "passed", "of", "pass")}))
    for r in rows:
        print("PASS" if r["pass"] else "FAIL", r["code"], r["title"][:48], "JZ", r["raw_job_zone"], "RW", r["raw_rw_modal"], r["labels"])


if __name__ == "__main__":
    main()

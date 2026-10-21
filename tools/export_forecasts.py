"""Register the current skill-outlook forecasts (SPEC 11.2) so the next AI-usage release can score them.

python tools/export_forecasts.py [--date YYYY-MM-DD] [--out DIR]
Writes data/forecast/registered-<date>.json from the bundled snapshots only. A rerun with the same date on the
same bundled data reproduces the file byte for byte. Rebuild the manifest afterwards (tools/build_snapshots.py) so
the app shows the registered file's date and hash. Score it later with
python tools/checks/a18_backtest.py --prospective <file> <release.csv>.
"""
import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aivancin import pathway, snapshots  # noqa: E402
from aivancin.outlook import SCENARIOS, SkillOutlook  # noqa: E402

HORIZONS = (6, 12)
INPUTS = ("aei-", "onet31-tasks", "onet31-task-dwas", "aivancin-backtest")


def script_sha256():
    return hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def build(snaps, registered_on):
    so = SkillOutlook(snaps)
    ev = snaps.evidence
    latest = ev.ids[-1]
    p80 = snaps.backtest["primary"]["mean_abs_error_p80"]
    pace, threshold = pathway.DEFAULT_PACE, pathway.DEFAULT_THRESHOLD
    acts = {}
    for d in snaps.onet.dwa_ids:
        o = so.outlook(d, latest)
        if o is None:
            continue
        last = next((r["share"] for r in o["releases"] if r["release"] == latest), None)
        a0 = o["now"]
        acts[d] = {"moderate": a0, "band": [round(max(0.0, a0 - p80), 1), round(min(100.0, a0 + p80), 1)],
                   "persistence": last, "weak": o["weak"],
                   "slower": {str(m): round(so.project(a0, "slower", m, pace), 1) for m in HORIZONS},
                   "faster": {str(m): round(so.project(a0, "faster", m, pace), 1) for m in HORIZONS}}
    return {"registered_on": registered_on, "latest_release": latest, "releases": ev.ids,
            "method": {"moderate": "equal-weight mean of the activity's release values (SPEC 4b, the A18 winner)",
                       "band": "moderate plus or minus the A18 primary 80th-percentile absolute error",
                       "persistence": "the activity's value in the latest release (the comparison forecast)",
                       "slower": "now value minus the largest fall between consecutive releases",
                       "faster": "now value times the pace per eight months"},
            "pace": pace, "threshold": threshold, "drop": so.drop, "band_p80": p80, "horizons_months": list(HORIZONS),
            "scenarios": list(SCENARIOS), "count": len(acts), "activities": acts,
            "inputs": {r["source_id"]: r["derived_sha256"] for r in snaps.manifest["snapshots"]
                       if "derived_sha256" in r and r["source_id"].startswith(INPUTS)},
            "script_sha256": script_sha256()}


def text(doc):
    """Readable JSON with one activity per line, the activities last."""
    head = json.dumps({k: v for k, v in doc.items() if k != "activities"}, indent=1)
    rows = [f"  {json.dumps(d)}: {json.dumps(f)}" for d, f in doc["activities"].items()]
    return head[:-2] + ',\n "activities": {\n' + ",\n".join(rows) + "\n }\n}\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--out", default=str(ROOT / "data" / "forecast"))
    ap.add_argument("--data", default=str(snapshots.DEFAULT_DATA_DIR))
    a = ap.parse_args(argv)
    doc = build(snapshots.load(a.data), a.date)
    out = Path(a.out) / f"registered-{a.date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(text(doc).encode("utf-8"))
    print(f"{out}: {doc['count']} activities, latest release {doc['latest_release']}, "
          f"sha256 {hashlib.sha256(out.read_bytes()).hexdigest()}")
    return out


if __name__ == "__main__":
    main()

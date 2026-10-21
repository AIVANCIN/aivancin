"""A18: skill-outlook backtest (SPEC section 9), run offline on the bundled snapshots.

python tools/checks/a18_backtest.py [result.json]
The protocol and thresholds were fixed before the first run; this script only executes them. The app
shows the measured error from data/forecast/a18_backtest.json (rebuild the manifest after rerunning).

python tools/checks/a18_backtest.py --prospective <registered.json> <release.csv> [result.json]
Scores registered forecasts (tools/export_forecasts.py) against a later release in the derived format the snapshot
builder writes (level, key, share, classified, automation). Reported, not a gate (SPEC 11.2).
"""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aivancin import aei, snapshots  # noqa: E402
from aivancin.outlook import SkillOutlook, window_mid  # noqa: E402

T1, T2, T3, T4 = "2025-09-15", "2026-01-15", "2026-03-24", "2026-06-26"


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def mae(errs):
    return round(sum(abs(e) for e in errs) / len(errs), 2) if errs else None


def line_forecast(points, x_new):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    return min(100.0, max(0.0, my + slope * (x_new - mx)))


def main():
    snaps = snapshots.load()
    so = SkillOutlook(snaps)
    assert snaps.evidence.ids == [T1, T2, T3, T4], snaps.evidence.ids
    day = {r: window_mid(r).toordinal() for r in (T1, T2, T3, T4)}
    units = {}
    for dwa in snaps.onet.dwa_ids:
        vals = {r: v for r, v, _ in so.release_values(dwa)}
        if all(r in vals for r in (T1, T2, T3, T4)):
            units[dwa] = vals

    em, ep, el, abs_m = [], [], [], []
    momentum = []
    sec_m, sec_p = [], []
    for v in units.values():
        m = (v[T1] + v[T2] + v[T3]) / 3
        line = line_forecast([(day[T1], v[T1]), (day[T2], v[T2]), (day[T3], v[T3])], day[T4])
        em.append(v[T4] - m)
        ep.append(v[T4] - v[T3])
        el.append(v[T4] - line)
        abs_m.append(abs(v[T4] - m))
        sec_m.append(v[T3] - (v[T1] + v[T2]) / 2)
        sec_p.append(v[T3] - v[T2])
        if abs(v[T3] - v[T1]) >= 5 and v[T4] != v[T3]:
            momentum.append((v[T4] - v[T3] > 0) == (v[T3] - v[T1] > 0))

    q80_primary = pct(abs_m, 0.8)
    q80_secondary = pct([abs(e) for e in sec_m], 0.8)
    coverage = sum(a <= q80_secondary for a in abs_m) / len(abs_m)
    mae_m, mae_l = mae(em), mae(el)
    mom = sum(momentum) / len(momentum) if momentum else None
    direction_gate = bool(mae_l <= 0.95 * mae_m and mom is not None and mom >= 0.60 and len(momentum) >= 50)
    res = {
        "check": "A18", "units": len(units),
        "primary": {"target": T4, "mae": {"mean": mae_m, "persistence": mae(ep), "line": mae_l},
                    "mean_abs_error_p80": round(q80_primary, 2), "mean_bias": round(sum(em) / len(em), 2)},
        "coverage": {"band_from_secondary_p80": round(q80_secondary, 2), "share_inside": round(coverage, 3),
                     "calibrated": 0.70 <= coverage <= 0.90},
        "direction": {"eligible": len(momentum), "same_sign_share": None if mom is None else round(mom, 3)},
        "direction_gate_pass": direction_gate,
        "secondary": {"target": T3, "mae": {"mean": mae(sec_m), "persistence": mae(sec_p)}},
        "drop_D_for_slower": so.drop,
        "inputs": {r["source_id"]: r["derived_sha256"] for r in snaps.manifest["snapshots"] if "derived_sha256" in r
                   and r["source_id"].startswith(("aei-", "onet31-tasks", "onet31-task-dwas"))},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "run_date": dt.date.today().isoformat(),
    }
    text = json.dumps(res, indent=1)
    print(text)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")


def activity_values(so, tasks):
    """The release value of every activity (SPEC 4b) in one release's task table."""
    out = {}
    for dwa, groups in so.groups.items():
        shares = [next((tasks[t]["share"] for t in ids if t in tasks), None) for ids in groups]
        shares = [s for s in shares if s is not None]
        if shares:
            out[dwa] = sum(shares) / len(shares)
    return out


def prospective(registered_path, release_path, snaps=None):
    reg = json.loads(Path(registered_path).read_text(encoding="utf-8"))
    snaps = snaps or snapshots.load()
    so = SkillOutlook(snaps)
    tasks, _ = aei.load_release(release_path)
    actual = activity_values(so, tasks)
    units = [(d, f, actual[d]) for d, f in reg["activities"].items() if d in actual and f["persistence"] is not None]
    t = reg["threshold"]
    reached = {}
    for s in ("slower", "moderate", "faster"):
        for m in reg["horizons_months"]:
            above = [(d, a) for d, f, a in units
                     if (f["moderate"] if s == "moderate" else f[s][str(m)]) >= t]
            reached[f"{s}_{m}"] = {"projected_at_or_above": len(above), "reached": sum(a >= t for _, a in above)}
    return {"check": "registered forecast, prospective", "registered_on": reg["registered_on"],
            "registered_sha256": hashlib.sha256(Path(registered_path).read_bytes()).hexdigest(),
            "release_file": Path(release_path).name, "release_sha256": snapshots.sha256_file(release_path),
            "units": len(units), "registered": reg["count"],
            "mae": {"moderate": mae([a - f["moderate"] for _, f, a in units]),
                    "persistence": mae([a - f["persistence"] for _, f, a in units])},
            "inside_band": round(sum(f["band"][0] <= a <= f["band"][1] for _, f, a in units) / len(units), 3) if units else None,
            "threshold": t, "threshold_reached": reached, "gate": "none (reported)"}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--prospective":
        out = prospective(sys.argv[2], sys.argv[3])
        text = json.dumps(out, indent=1)
        print(text)
        if len(sys.argv) > 4:
            Path(sys.argv[4]).write_text(text + "\n", encoding="utf-8")
    else:
        main()

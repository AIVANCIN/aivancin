"""Forecast signals and the Radar (SPEC 11.1, 11.2, 11.4; checks C1-C5)."""
import csv
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from aivancin import outlook
from aivancin.matching import Matcher
from aivancin.report import Reporter
from aivancin.routes import Planner

from .test_routes import TODAY, profile, HAFIZ_SIDE_WORK, PRIYA_SIDE_WORK

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load_tool(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


checks = load_tool("forecast_checks", ROOT / "tools" / "checks" / "forecast_checks.py")


@pytest.fixture(scope="module")
def planner(snaps):
    m = Matcher(snaps)
    return Planner(snaps, m, Reporter(snaps, m))


@pytest.fixture(scope="module")
def hafiz(snaps, ai, personas):
    return profile(snaps, ai, personas["hafiz"], HAFIZ_SIDE_WORK)


@pytest.fixture(scope="module")
def priya(snaps, ai, personas):
    return profile(snaps, ai, personas["priya"], PRIYA_SIDE_WORK)


# ---- C1, C2: skill demand (F2) -----------------------------------------------------------------
def test_c1_demand_reproduces_from_the_csvs(planner):
    res = checks.c1(planner.rep.demand, checks.f2_raw())
    assert res["pass"], res["examples"]
    assert res["activities_with_projection"] > 1000


def test_c1_catches_a_changed_band(planner, tmp_path):
    """Fault injection: one pool role's band flipped in a copy of the outlook file must show up as mismatches."""
    data = tmp_path / "data"
    (data / "onet").mkdir(parents=True)
    for f in ("tasks.csv", "task_dwas.csv", "job_zones.csv"):
        shutil.copy(DATA / "onet" / f, data / "onet" / f)
    rows = list(csv.DictReader(open(DATA / "onet" / "outlook.csv", encoding="utf-8", newline="")))
    hit = next(r for r in rows if r["code"] == "15-1232.00")
    hit["growth_band"] = "Much faster than average (7% or higher)" if hit["growth_band"].startswith("Decline") else "Decline (-1% or lower)"
    with open(data / "onet" / "outlook.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    res = checks.c1(planner.rep.demand, checks.f2_raw(data))
    assert not res["pass"] and res["mismatches"] > 0


def test_demand_counts_each_bls_unit_once(snaps, planner):
    """The nine 15-1299 occupations share one BLS projection (27,000 openings); an activity they share must not
    count those openings nine times."""
    m = planner.m
    codes = [c for c in planner.rep.demand.pool if c.startswith("15-1299.")]
    assert len(codes) >= 2
    shared = set.intersection(*(set(m.w[c]) for c in codes[:2]))
    d = sorted(shared)[0]
    got = planner.rep.demand.get(d)
    units = {}
    for c in planner.rep.demand.pool:
        if m.w[c].get(d, 0) > 0:
            rec = snaps.outlook[c]
            units[(rec["employment"], rec["band"], rec["openings"])] = rec["openings"]
    assert got["openings"] == sum(units.values())
    assert got["openings"] < sum(snaps.outlook[c]["openings"] for c in planner.rep.demand.pool if m.w[c].get(d, 0) > 0)


def test_demand_labels_and_ranks(planner):
    assert outlook.demand_label(0.25, 5) == "mostly in growing jobs"
    assert outlook.demand_label(-0.25, 5) == "mostly in declining jobs"
    assert outlook.demand_label(0.249, 5) == "mixed"
    assert outlook.demand_label(0.9, 0) == "no projection"
    assert planner.rep.demand.get("no-such-activity")["label"] == "no projection"
    assert outlook.DEMAND_RANK["mostly in growing jobs"] < outlook.DEMAND_RANK["mixed"] < \
        outlook.DEMAND_RANK["no projection"] < outlook.DEMAND_RANK["mostly in declining jobs"]


def test_c2_sensitivity_is_reported(snaps, planner):
    res = checks.c2(snaps, planner.m)
    assert res["changed_at_0.15"] > 0 and res["changed_at_0.35"] > 0 and "pass" not in res


# ---- C3: time to threshold ---------------------------------------------------------------------
@pytest.mark.parametrize("a0,scenario,want", [
    (None, "faster", (None, "no evidence in the bundled releases")),
    (55.0, "moderate", (0, "already at or above the threshold")),
    (55.0, "slower", (None, "not reached in this scenario")),  # 55 - 7.1 is below 50
    (60.0, "slower", (0, "already at or above the threshold")),
    (40.0, "moderate", (None, "not reached in this scenario")),
    (10.0, "faster", (35, "about 35 months")),  # 8 ln(5) / ln(1.444) = 35.04
    (3.0, "faster", (None, "not within five years")),
    (0.0, "faster", (None, "not reached in this scenario")),
])
def test_time_to_threshold_formula(a0, scenario, want):
    assert outlook.time_to_threshold(a0, 50, scenario, 1.444, 7.1) == want


def test_time_to_threshold_without_growth():
    assert outlook.time_to_threshold(20.0, 50, "faster", 1.0, 7.1) == (None, "not reached in this scenario")


def test_c3_every_radar_row_matches_the_formula(planner, hafiz, priya):
    res = checks.c3(planner, {"hafiz": hafiz, "priya": priya})
    assert res["pass"], res["examples"]


# ---- C4: Malaysian group trend (F4) ------------------------------------------------------------
def test_c4_group_trend_reproduces_and_pay_steps_use_the_latest_year(snaps, planner):
    res = checks.c4(snaps, planner.rep)
    assert res["pass"], res
    t = outlook.group_trend(snaps, "2")
    assert t["start"] == 2016 and t["end"] == 2025
    assert t["employees_k"]["from"] == 1557.1 and t["employees_k"]["to"] == 1945.4
    assert "Group level, not this occupation." in t["text"] and t["notes"]
    assert planner.rep.median("2") == 6124  # the 2025 edition, as before the series change


def test_trend_is_never_projected(snaps):
    for major in "123456789":
        t = outlook.group_trend(snaps, major)
        assert t["end"] <= 2025 and "will" not in t["text"] and "projected" not in t["text"].lower()
    assert outlook.group_trend(snaps, None) is None


def test_sru_trend_first_and_latest_quarter(snaps):
    t = outlook.sru_trend(snaps)
    assert t["from"]["date"] == "2017-01-01" and t["to"]["date"] == "2026-04-01"
    assert t["to"]["rate"] == snaps.sru["age"][("rate", "25-34")][0]


# ---- C5: registered forecasts ------------------------------------------------------------------
def test_c5_registered_forecasts_reproduce_and_score(snaps):
    res = checks.c5(snaps)
    assert res["pass"], res
    pro = res["prospective_fixture"]
    # The fixture adds 10 points to every task, so the moderate forecast's error is about 10 points or more.
    assert pro["mae"]["moderate"] > 0 and pro["inside_band"] is not None


def test_registered_forecast_values(snaps):
    reg = snaps.registered
    p80 = snaps.backtest["primary"]["mean_abs_error_p80"]
    for d, f in list(reg["activities"].items())[:50]:
        assert f["band"] == [round(max(0.0, f["moderate"] - p80), 1), round(min(100.0, f["moderate"] + p80), 1)]
        assert f["slower"]["6"] <= f["moderate"] <= f["faster"]["12"]


# ---- Radar and Future (C6 partial; the rendered half is C16) ------------------------------------
def all_figs(obj, path=""):
    if isinstance(obj, dict):
        if "v" in obj and "src" in obj:
            yield path, obj
        for k, v in obj.items():
            yield from all_figs(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from all_figs(v, f"{path}[{i}]")


def test_radar_parts_and_provenance(planner, hafiz, snaps):
    res = planner.routes(hafiz, {}, today=TODAY)
    rd = res["radar"]
    ids = {r["source_id"] for r in snaps.manifest["snapshots"]} | {"your-input", "explore-defaults"}
    for path, f in all_figs(rd):
        assert f["src"] and set(f["src"]) <= ids, path
    assert {s["group"] for s in rd["skills"]} == {"you", "gap"}
    you = {s["dwa_id"] for s in rd["skills"] if s["group"] == "you"}
    assert you == {a["dwa_id"] for a in hafiz["activities"]}
    gaps = {g["dwa_id"] for r in res["routes"] for g in r["prioritized"]} - you
    assert {s["dwa_id"] for s in rd["skills"] if s["group"] == "gap"} == gaps
    for j in rd["jobs"]:
        band = snaps.outlook[j["code"]]["band"]
        assert j["group"] == ("growing" if band.startswith(("Faster", "Much faster")) else
                              "declining" if band.startswith("Decline") else "steady")
    assert rd["horizon"]["basis"] == "minimum look-ahead (assumption)" and rd["horizon"]["months"]["v"] == 12
    assert rd["sure"]["bls"]["quote"].endswith("86 percent of the time.")
    assert rd["sure"]["registered"]["sha256"] == snaps.source("aivancin-registered-forecasts")["derived_sha256"]
    assert [m["release"] for m in rd["sure"]["release_means"]] == snaps.evidence.ids


def test_radar_classes_agree_with_projections_and_threshold(planner, hafiz):
    for opts in ({}, {"scenario": "faster", "horizon": 36}, {"threshold": 30}):
        rd = planner.routes(hafiz, opts, today=TODAY)["radar"]
        for s in rd["skills"]:
            if not s["now"]:
                assert set(s["classes"].values()) == {"no evidence in the bundled releases"}
                continue
            for sc, v in s["projected"].items():
                assert s["classes"][sc] == outlook.classify(v["v"], rd["threshold"]["v"])


def test_radar_changing_lists_only_ai_checkpoints(planner, priya):
    for scenario in ("slower", "moderate", "faster"):
        res = planner.routes(priya, {"scenario": scenario}, today=TODAY)
        rd = res["radar"]
        want = {g["dwa_id"] for r in res["routes"] for s in r["block"]["steps"] if s["form"] == "ai"
                for g in s["reason"] if g["projected"][scenario]["v"] >= rd["threshold"]["v"]}
        assert {c["dwa_id"] for c in rd["changing"]} == want
        for c in rd["changing"]:
            assert all(w["projected"]["v"] >= rd["threshold"]["v"] for w in c["where"])


def test_radar_horizon_follows_the_target_or_the_choice(planner, hafiz):
    res = planner.routes(hafiz, {}, today=TODAY)
    target = res["routes"][0]
    chosen = planner.routes(hafiz, {"target": target["code"]}, today=TODAY)["radar"]
    t = next(r for r in planner.routes(hafiz, {"target": target["code"]}, today=TODAY)["routes"] if r["code"] == target["code"])
    assert chosen["horizon"]["months"]["v"] == t["block"]["horizon"]["months"]["v"]
    fixed = planner.routes(hafiz, {"horizon": 60}, today=TODAY)["radar"]
    assert fixed["horizon"]["months"]["v"] == 60 and fixed["horizon"]["basis"] == "your choice"


def test_explore_radar_shows_main_activities(planner):
    res = planner.explore({}, today=TODAY)
    rd = res["radar"]
    assert {s["group"] for s in rd["skills"]} == {"main"}
    assert len(rd["jobs"]) == len(planner.dests)
    m = planner.m
    want = {d for code in planner.dests for d, _ in sorted(m.w[code].items(), key=lambda kv: (-kv[1], kv[0]))[:3]}
    assert {s["dwa_id"] for s in rd["skills"]} == want


def test_route_future_tab_parts(planner, hafiz, snaps):
    for r in planner.routes(hafiz, {}, today=TODAY)["routes"]:
        fut = r["future"]
        assert fut["trend"]["major"] == r["masco"]["major"]
        assert len(fut["emerging"]["technologies"]) == len(snaps.onet.software.get(r["code"], []))
        for a in r["ai_use"]:
            assert "demand" in a and set(a["ttt"]) == set(outlook.SCENARIOS)
            if a["now"]:
                assert a["projected"]["slower"]["v"] <= a["projected"]["moderate"]["v"] <= a["projected"]["faster"]["v"]


def test_demand_never_changes_routes_or_order(planner, hafiz, monkeypatch):
    """F2 is shown, never used for overlap, qualification or the default order."""
    before = [(r["code"], r["overlap"]["v"]) for r in planner.routes(hafiz, {}, today=TODAY)["routes"]]
    monkeypatch.setattr(planner.rep.demand, "get", lambda d: {"label": "mostly in declining jobs", "roles": 9, "grow": 0,
                                                               "decline": 1, "net": -1, "openings": 0, "few": False,
                                                               "openings_period": None, "src": outlook.DEMAND_SRC})
    after = [(r["code"], r["overlap"]["v"]) for r in planner.routes(hafiz, {}, today=TODAY)["routes"]]
    assert before == after

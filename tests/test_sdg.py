"""SDG reasons and the Decent work order (SPEC 11.5; checks C8, C9)."""
import csv
from pathlib import Path

import pytest

from aivancin import sdg
from aivancin.matching import Matcher
from aivancin.report import Reporter
from aivancin.routes import Planner, decent_order

from .test_routes import TODAY, profile, HAFIZ_SIDE_WORK, PRIYA_SIDE_WORK

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def planner(snaps):
    m = Matcher(snaps)
    return Planner(snaps, m, Reporter(snaps, m))


@pytest.fixture(scope="module")
def cases(snaps, ai, personas, planner):
    out = {"hafiz": profile(snaps, ai, personas["hafiz"], HAFIZ_SIDE_WORK),
           "priya": profile(snaps, ai, personas["priya"], PRIYA_SIDE_WORK)}
    return out


def all_results(planner, cases):
    for scenario in ("slower", "moderate", "faster"):
        for weeks, budget in ((4, None), (12, 200.0)):
            for name, prof in cases.items():
                p = prof if budget is None else {**prof, "budget_rm": budget}
                yield name, scenario, planner.routes(p, {"scenario": scenario, "weeks": weeks}, today=TODAY)
        yield "explore", scenario, planner.explore({"scenario": scenario}, today=TODAY)


def test_c8_every_tag_has_a_reason_and_source(snaps, planner, cases):
    ids = {r["source_id"] for r in snaps.manifest["snapshots"]}
    assert sdg.SRC in ids
    seen = {"4.3": 0, "8.2": 0, "steps": 0}
    for name, scenario, res in all_results(planner, cases):
        for r in res["routes"]:
            for t in r["sdg"]:
                assert t["goal"] == "8.5" and t["reason"] and set(t["src"]) <= ids and sdg.SRC in t["src"]
            for s in r["block"]["steps"]:
                seen["steps"] += 1
                goals = [t["goal"] for t in s["sdg"]]
                assert goals[0] == "4.4", (name, s["id"])
                for t in s["sdg"]:
                    assert t["reason"] and t["src"] and set(t["src"]) <= ids and sdg.SRC in t["src"], (name, s["id"], t)
                assert ("4.3" in goals) == (s["cost_rm"]["v"] == 0), (name, s["id"])
                assert ("8.2" in goals) == (s["form"] == "ai" or s["kind"] == "ai_practice"), (name, scenario, s["id"])
                if s["serves"] and s["kind"] == "learn":
                    assert all(g["name"].rstrip(".") in s["sdg"][0]["reason"] for g in s["serves"])
                seen["4.3"] += "4.3" in goals
                seen["8.2"] += "8.2" in goals
    # Both conditional tags occur, and 4.3 is absent somewhere (the RM200 budgets admit paid steps only if any exist).
    assert seen["4.3"] > 0 and seen["8.2"] > 0 and seen["steps"] > 100


def test_c8_paid_step_gets_no_43(planner, cases):
    """Fault injection: a step with a price must not carry 4.3, and one without AI checking must not carry 8.2."""
    step = {"kind": "learn", "serves": [{"name": "Test software performance."}], "cost_rm": {"v": 49.0}, "form": "plain"}
    goals = [t["goal"] for t in sdg.step_tags(step, "aivancin-catalogue")]
    assert goals == ["4.4"]
    step = {**step, "cost_rm": {"v": 0}, "form": "ai"}
    assert [t["goal"] for t in sdg.step_tags(step, "aivancin-catalogue")] == ["4.4", "4.3", "8.2"]


def raw_medians():
    rows = list(csv.DictReader(open(DATA / "dosm" / "wages_occupation.csv", encoding="utf-8", newline="")))
    latest = max(r["date"] for r in rows)
    return {r["group"]: int(r["median_rm"]) for r in rows if r["date"] == latest and r["sex"] == "both"}


GROUP_KEY = {"1": "managers", "2": "professionals", "3": "technicians", "4": "clerical", "5": "service_sales",
             "6": "agriculture", "7": "craft", "8": "operators", "9": "elementary"}


def test_c9_decent_order_reproduces_the_rule(snaps, planner, cases):
    med = raw_medians()
    dests = {d["code"]: d for d in snaps.routes["destinations"]}
    for name, scenario, res in all_results(planner, cases):
        if scenario != "moderate":
            continue
        want = sorted(res["routes"], key=lambda r: (-med[GROUP_KEY[dests[r["code"]]["masco"]["major"]]], -r["overlap"]["v"], r["code"]))
        assert res["decent_order"] == [r["code"] for r in want], name
        for r in res["routes"]:
            assert r["median"]["v"] == med[GROUP_KEY[dests[r["code"]]["masco"]["major"]]]


def test_decent_order_puts_missing_medians_last():
    routes = [{"code": "b", "median": None, "overlap": {"v": 50}}, {"code": "a", "median": {"v": 3000}, "overlap": {"v": 1}},
              {"code": "c", "median": {"v": 3000}, "overlap": {"v": 9}}]
    assert decent_order(routes) == ["c", "a", "b"]


def test_contribution_counts_follow_the_block(planner, cases):
    for name, scenario, res in all_results(planner, cases):
        for r in res["routes"]:
            c, steps = r["contribution"], r["block"]["steps"]
            assert c["s43"]["free"]["v"] == sum(1 for s in steps if s["cost_rm"]["v"] == 0) and c["s43"]["of"]["v"] == len(steps)
            assert c["s82"]["checkpoints"]["v"] == sum(1 for s in steps if s["form"] == "ai" and s["kind"] != "ai_practice")
            assert c["s44"]["gaps"]["v"] == len(r["block"]["gaps_served"])
            assert c["s85"]["to"]["major"] == r["masco"]["major"]
            assert (c["s85"]["step"] is None) == (r["pay"] is None)


def test_sdg5_context_never_changes_routes(planner, cases, monkeypatch):
    before = [(r["code"], r["overlap"]["v"]) for r in planner.routes(cases["priya"], {}, today=TODAY)["routes"]]
    order = planner.routes(cases["priya"], {}, today=TODAY)["decent_order"]
    sru = planner.snaps.sru
    monkeypatch.setitem(sru, "sex", {k: (99.0 if k[1] == "female" else 1.0, d) for k, (v, d) in sru["sex"].items()})
    res = planner.routes(cases["priya"], {}, today=TODAY)
    assert [(r["code"], r["overlap"]["v"]) for r in res["routes"]] == before and res["decent_order"] == order
    assert res["routes"][0]["contribution"]["s5"]["female"]["v"] == 99.0

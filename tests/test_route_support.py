"""Route support and update evidence beside the decision (22 Oct): what stands behind each route, and whether a
proposed plan change rests on weak evidence or on a scenario assumption."""
import pytest

from aivancin.matching import Matcher
from aivancin.report import Reporter
from aivancin.routes import MIN_DONE, Planner

from .test_routes import HAFIZ_SIDE_WORK, PRIYA_SIDE_WORK, TODAY, profile


@pytest.fixture(scope="module")
def planner(snaps):
    m = Matcher(snaps)
    return Planner(snaps, m, Reporter(snaps, m))


@pytest.fixture(scope="module")
def people(snaps, ai, personas):
    return {"hafiz": profile(snaps, ai, personas["hafiz"], HAFIZ_SIDE_WORK),
            "priya": profile(snaps, ai, personas["priya"], PRIYA_SIDE_WORK)}


def test_each_route_says_what_supports_it(planner, people):
    for prof in people.values():
        out = planner.routes(prof, {}, TODAY)
        for r in out["routes"]:
            sp = r["support"]
            assert sp["exact"]["v"] == len(r["done"]) == r["done_count"]["v"]
            assert sp["related"]["v"] == len(r["related"]) and sp["once"]["v"] == len(r["counted_once"])
            # each role activity counts once (22 Oct): the counted ones are distinct, and so are the twins behind them
            stands = [x["dwa_id"] for x in r["done"]] + [x["role_dwa_id"] for x in r["related"]]
            assert len(stands) == len(set(stands)) == sp["counted"]["v"]
            assert all(x["role_dwa_id"] in stands for x in r["counted_once"])
            assert sp["counted"]["v"] == sp["exact"]["v"] + sp["related"]["v"] >= MIN_DONE  # the rule still admits it
            assert sp["limited"] == (sp["counted"]["v"] <= MIN_DONE or sp["exact"]["v"] < MIN_DONE)
            assert all(x["src"] for x in (sp["exact"], sp["related"], sp["once"], sp["counted"], sp["needed"]))


def test_a_route_on_the_minimum_is_marked_limited_and_kept(snaps, planner):
    """Two confirmed finance activities: routes qualify on exactly two and stay listed, marked limited."""
    prof = {"title_code": None, "degree_field": "", "cip_code": None, "degree_level": "degree", "weekly_hours": 6.0,
            "budget_rm": 0.0, "without_ai_suggestions": False,
            "activities": [{"dwa_id": d, "name": snaps.onet.dwa_names[d], "sources": ["added_by_you"], "lanes": ["other_work"],
                            "guard_override": False} for d in ("4.A.3.b.6.a.2", "4.A.2.a.3.c.2")]}
    out = planner.routes(prof, {}, TODAY)
    assert out["routes"] and all(r["support"]["limited"] for r in out["routes"])
    explore = planner.explore({}, TODAY)
    assert not any(r["support"]["limited"] for r in explore["routes"])  # nothing confirmed: nothing to judge


def _change(planner, prof, target, frm, to, scenario="moderate"):
    return planner.compare(prof, {"target": target, "scenario": scenario, "from_release": frm, "to_release": to}, TODAY)


def test_weak_evidence_change_is_marked(planner, people):
    """Priya, Loan Officers, January to March 2026: the SQL step's AI checking rests on weak evidence."""
    c = _change(planner, people["priya"], "13-2072.00", "2026-01-15", "2026-03-24")
    moved = [x for x in c["changes"] if x["change"] != "retained"]
    sql = next(x for x in moved if "Transact-SQL" in x["title"])
    assert sql["weak"] and sql["weak_rows"]
    assert any(r["weak"] for r in sql["after"]["reason"])
    assert c["weak_count"] == sum(x["weak"] for x in moved) >= 1


def test_measured_change_and_steps_useful_in_every_scenario(planner, people):
    """Hafiz, systems administration, January to March 2026 (the replay pair): measured evidence."""
    c = _change(planner, people["hafiz"], "15-1244.00", "2026-01-15", "2026-03-24")
    moved = [x for x in c["changes"] if x["change"] != "retained"]
    assert moved and not any(x["weak"] for x in moved) and c["weak_count"] == 0
    kept = [x for x in c["changes"] if x["change"] == "retained"]
    assert c["steady_count"] == sum(x["every_scenario"] for x in kept)
    for x in c["changes"]:
        assert "moderate" in x["scenarios"]  # the moderate plan is the one compared
        assert x["every_scenario"] == (len(x["scenarios"]) == 3)


def test_scenario_dependent_change_names_its_scenarios(planner, people):
    """Hafiz, systems administration, slower scenario, September 2025 to June 2026: the security steps change form
    only under the slower assumption."""
    c = _change(planner, people["hafiz"], "15-1244.00", "2025-09-15", "2026-06-26", "slower")
    moved = [x for x in c["changes"] if x["change"] != "retained"]
    assert moved and c["assumption_count"] == sum(not x["every_scenario"] for x in moved) >= 1
    only = [x for x in moved if x["scenarios"] == ["slower"]]
    assert only and all(not x["every_scenario"] for x in only)


def test_unrelated_update_leaves_the_plan_unchanged(planner, people):
    """The same release on both sides changes nothing, and every step is retained with its basis."""
    c = _change(planner, people["hafiz"], "15-1244.00", "2026-06-26", "2026-06-26")
    assert c["changed_count"] == 0 and c["weak_count"] == 0 and c["assumption_count"] == 0
    assert all(x["change"] == "retained" for x in c["changes"])

"""No dead end (SPEC 11.7; check C15, server half): with fewer than two qualifying routes, up to three of the closest
skilled roles from the exploration ranking, without a curated first block, each one that the exploration view lists."""
import importlib.util
import json
from pathlib import Path

import pytest

from aivancin.matching import Matcher
from aivancin.report import Reporter
from aivancin.routes import EXPLORATION_CARDS, EXPLORATION_LABEL, Planner

from .test_routes import TODAY, profile, HAFIZ_SIDE_WORK

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b7_target", ROOT / "tools" / "checks" / "b7_target.py")
b7 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b7)


@pytest.fixture(scope="module")
def planner(snaps):
    m = Matcher(snaps)
    return Planner(snaps, m, Reporter(snaps, m))


def out_of_scope(snaps, ai):
    encoder, vectors = ai
    for f in ("b7_profiles_3.json", "b7_profiles_4.json"):
        for p in json.loads((ROOT / "tests" / "acceptance" / f).read_text(encoding="utf-8"))["out_of_scope"]:
            yield p["id"], b7.confirmed_profile(p, encoder, vectors, snaps)[1]


def test_c15_out_of_scope_profiles_get_exploration_cards(snaps, ai, planner):
    seen = 0
    for pid, prof in out_of_scope(snaps, ai):
        res = planner.routes(prof, {"weeks": 4}, today=TODAY)
        cards = res["exploration"]
        assert len(res["routes"]) < 2 and 1 <= len(cards) <= EXPLORATION_CARDS, pid
        listed = [r["code"] for r in planner.rep.report(prof, {"lane": "all"}, today=TODAY)["roles"]]
        shown = {r["code"] for r in res["routes"]}
        for c in cards:
            assert c["label"] == EXPLORATION_LABEL
            assert c["code"] not in planner.dests and c["code"] not in shown, (pid, c["code"])
            assert c["code"] in listed, (pid, c["code"])  # opens to its detail in the exploration view
            assert planner.rep.is_skilled(c["code"])
            assert c["overlap"]["src"] and c["done_count"]["src"]
        # Cards follow the exploration order.
        assert [c["code"] for c in cards] == [x for x in listed if x not in planner.dests and x not in shown][:EXPLORATION_CARDS]
        seen += 1
    assert seen == 6


def test_no_cards_with_two_or_more_routes_or_while_exploring(snaps, ai, personas, planner):
    hafiz = profile(snaps, ai, personas["hafiz"], HAFIZ_SIDE_WORK)
    res = planner.routes(hafiz, {}, today=TODAY)
    assert len(res["routes"]) >= 2 and res["exploration"] == []
    assert planner.explore({}, today=TODAY)["exploration"] == []

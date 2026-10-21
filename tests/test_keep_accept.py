"""Keep or accept new evidence (SPEC 11.6; check C10). The plan records its AI-usage release; the notice's count is
the update comparison's; accepting moves the plan to the latest release, keeping leaves it as it was."""
import json

import pytest

from aivancin.matching import Matcher
from aivancin.report import Reporter
from aivancin.routes import Planner

from .test_routes import TODAY, profile, HAFIZ_SIDE_WORK, PRIYA_SIDE_WORK


@pytest.fixture(scope="module")
def planner(snaps):
    m = Matcher(snaps)
    return Planner(snaps, m, Reporter(snaps, m))


@pytest.fixture(scope="module")
def people(snaps, ai, personas):
    return {"hafiz": profile(snaps, ai, personas["hafiz"], HAFIZ_SIDE_WORK),
            "priya": profile(snaps, ai, personas["priya"], PRIYA_SIDE_WORK)}


def target_block(planner, prof, opts):
    res = planner.routes(prof, opts, today=TODAY)
    return next(r for r in res["routes"] if r["code"] == opts["target"])["block"]


def plain(block):
    return [(s["id"], s["form"], s["completed"]) for s in block["steps"]]


def cases(planner, people):
    ids = planner.snaps.evidence.ids
    for name, prof in people.items():
        for target in [r["code"] for r in planner.routes(prof, {}, today=TODAY)["routes"]]:
            for old in ids[:-1]:
                for scenario in ("moderate", "faster"):
                    yield name, prof, {"target": target, "release": old, "scenario": scenario}, ids[-1]


def test_c10_identical_inputs_identical_plans(planner, people):
    for name, prof, opts, latest in list(cases(planner, people))[:6]:
        a = planner.routes(prof, opts, today=TODAY)
        b = planner.routes(prof, opts, today=TODAY)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_c10_notice_count_equals_the_comparison(planner, people):
    nonzero = 0
    for name, prof, opts, latest in cases(planner, people):
        old = plain(target_block(planner, prof, opts))
        new = plain(target_block(planner, prof, {**opts, "release": latest}))
        before, after = {s[0]: s for s in old}, {s[0]: s for s in new}
        want = sum(1 for i in set(before) | set(after) if i not in before or i not in after or before[i][1] != after[i][1])
        cmp = planner.compare(prof, {**opts, "from_release": opts["release"], "to_release": latest}, today=TODAY)
        assert cmp["changed_count"] == want, (name, opts)
        nonzero += want > 0
    assert nonzero > 0, "no case where newer evidence changes a plan; the check would be vacuous"


def test_c10_accepting_moves_to_latest_and_keeping_preserves(planner, people):
    ids = planner.snaps.evidence.ids
    prof = people["hafiz"]
    target = planner.routes(prof, {}, today=TODAY)["routes"][0]["code"]
    first = target_block(planner, prof, {"target": target, "release": ids[0]})["steps"][0]["id"]
    kept_opts = {"target": target, "release": ids[0], "completed": [first]}
    kept = target_block(planner, prof, kept_opts)
    # Keeping: the plan rests only on releases up to its own.
    for s in kept["steps"]:
        for g in s["reason"]:
            assert set(g["releases"]) <= {ids[0]}
    assert kept["steps"][0]["id"] == first and kept["steps"][0]["completed"]
    # Accepting: identical to a plan without a recorded release (records without one use the latest).
    accepted = target_block(planner, prof, {**kept_opts, "release": ids[-1]})
    default = target_block(planner, prof, {"target": target, "completed": [first]})
    assert plain(accepted) == plain(default)
    assert any(s["id"] == first and s["completed"] for s in accepted["steps"])


def test_c10_unknown_release_is_refused(planner, people):
    from aivancin.report import ReportError
    with pytest.raises(ReportError):
        planner.routes(people["priya"], {"release": "2024-01-01"}, today=TODAY)

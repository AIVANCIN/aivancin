"""Routes, first learning blocks, shared foundations and the update comparison (SPEC section 10, checks B1-B6)."""
import copy
import datetime as dt

import pytest

from aivancin import intake, pathway
from aivancin.matching import Matcher
from aivancin.report import Reporter, ReportError
from aivancin.routes import MAX_ROUTES, MIN_DONE, PER_GAP, Planner, effort

TODAY = dt.date(2026, 10, 21)
HAFIZ_SIDE_WORK = ["4.A.3.b.1.d.1", "4.A.3.b.1.d.3", "4.A.3.b.1.c.1", "4.A.3.b.1.b.2", "4.A.3.b.1.f.4", "4.A.2.b.2.b.8"]
PRIYA_SIDE_WORK = ["4.A.4.c.1.c.6", "4.A.3.b.6.a.1", "4.A.4.c.3.d.1", "4.A.2.a.3.c.2", "4.A.3.b.6.j.3"]


@pytest.fixture(scope="module")
def planner(snaps):
    m = Matcher(snaps)
    return Planner(snaps, m, Reporter(snaps, m))


def profile(snaps, ai, persona, picks, **extra):
    encoder, vectors = ai
    req = {**persona, "confirmed": [{"dwa_id": d, "source": "suggested"} for d in picks], **extra}
    sug = intake.suggest(req, encoder, vectors, snaps.onet)
    return intake.build_profile(req, snaps.onet, sug, snaps.cip)


@pytest.fixture(scope="module")
def hafiz(snaps, ai, personas):
    return profile(snaps, ai, personas["hafiz"], HAFIZ_SIDE_WORK)


@pytest.fixture(scope="module")
def priya(snaps, ai, personas):
    return profile(snaps, ai, personas["priya"], PRIYA_SIDE_WORK)


def test_b1_every_supported_destination_passes_admission(snaps, planner):
    dests = snaps.routes["destinations"]
    assert len(dests) == 12 and all(d["admitted"] for d in dests)
    m, rep = planner.m, planner.rep
    courses = [c for c in pathway.verified_courses(snaps.catalogue, TODAY) if c.get("checkpoint")]
    for d in dests:
        code = d["code"]
        assert snaps.onet.job_zones[code] in (3, 4)
        assert rep.masco(code).get("skill") == "skilled"
        assert d["requirements"] and all(r["src"] and r["url"] for r in d["requirements"])
        assert len(m.w[code]) >= 10
        top = [a for a, _ in sorted(m.w[code].items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
        assert len({a for a in top for c in courses if a in c["dwas"]}) >= 3


def test_b2_routes_are_supported_overlapping_and_ordered(snaps, planner, hafiz, priya):
    for prof in (hafiz, priya):
        res = planner.routes(prof, {"weeks": 4}, today=TODAY)
        routes = res["routes"]
        assert 1 <= len(routes) <= MAX_ROUTES
        overlaps = [r["overlap"]["v"] for r in routes]
        assert overlaps == sorted(overlaps, reverse=True)
        for r in routes:
            assert r["code"] in planner.dests
            assert r["overlap"]["v"] > 0 and r["done_count"]["v"] >= MIN_DONE
            # dimensions are reported separately, never folded into one score
            assert "score" not in r
            for key in ("requirements", "job_outlook", "block", "supported_share", "local_evidence"):
                assert key in r


def test_b2_families_follow_the_work(planner, hafiz, priya):
    h = {r["family"] for r in planner.routes(hafiz, {"weeks": 4}, today=TODAY)["routes"]}
    p = {r["family"] for r in planner.routes(priya, {"weeks": 4}, today=TODAY)["routes"]}
    assert h <= {"it_support", "software"} and "finance_analytics" in p


@pytest.mark.parametrize("weeks", [4, 8, 12])
def test_b3_block_fits_time_and_budget_and_counts_each_entry_once(snaps, planner, hafiz, weeks):
    res = planner.routes(hafiz, {"weeks": weeks, "scenario": "faster", "threshold": 30}, today=TODAY)
    by_id = {c["id"]: c for c in snaps.catalogue["courses"]}
    capacity = weeks * hafiz["weekly_hours"]
    for r in res["routes"]:
        b = r["block"]
        ids = [s["id"] for s in b["steps"]]
        assert len(ids) == len(set(ids))
        open_steps = [s for s in b["steps"] if not s["completed"]]
        assert b["effort_hours"]["v"] == round(sum(effort(by_id[s["id"]]) if by_id[s["id"]].get("practice_hours")
                                                   else s["provider_hours"]["v"] + s["practice_hours"]["v"] for s in open_steps), 1)
        assert b["effort_hours"]["v"] <= capacity + 1e-9
        assert b["cost_rm"]["v"] <= hafiz["budget_rm"]
        assert all(by_id[s["id"]]["link_check"]["ok"] for s in b["steps"])
        served = {g["dwa_id"]: 0 for g in r["prioritized"]}
        for s in b["steps"]:
            if s["kind"] == "learn":
                for g in s["serves"]:
                    served[g["dwa_id"]] += 1
        assert max(served.values()) <= PER_GAP


def test_b3_capacity_grows_with_weeks(planner, hafiz):
    hours = [planner.routes(hafiz, {"weeks": w}, today=TODAY)["routes"][0]["block"]["effort_hours"]["v"] for w in (4, 8, 12)]
    assert hours == sorted(hours)


def test_b3_partial_block_says_so_and_lists_uncovered_gaps(planner, hafiz):
    for r in planner.routes(hafiz, {"weeks": 4}, today=TODAY)["routes"]:
        b = r["block"]
        unserved_with_resource = [g for g in r["prioritized"] if g["has_resource"] and not g["served"]]
        assert (b["status"] == "partial") == bool(unserved_with_resource)
        assert {g["dwa_id"] for g in b["uncovered"]} == {g["dwa_id"] for g in r["prioritized"] if not g["served"]}


def test_b4_no_step_before_an_entry_it_requires(planner, hafiz, priya):
    for prof in (hafiz, priya):
        for weeks in (4, 12):
            for r in planner.routes(prof, {"weeks": weeks}, today=TODAY)["routes"]:
                seen = set()
                for s in r["block"]["steps"]:
                    assert all(req in seen for req in s["requires"] if req in {x["id"] for x in r["block"]["steps"]}), s["id"]
                    missing = [req for req in s["requires"] if req not in seen]
                    assert not missing or s["completed"], (s["id"], missing)
                    seen.add(s["id"])


def test_b4_completed_steps_are_kept_and_not_counted(planner, hafiz):
    first = planner.routes(hafiz, {"weeks": 4}, today=TODAY)["routes"][0]
    done_id = first["block"]["steps"][0]["id"]
    again = planner.routes(hafiz, {"weeks": 4, "completed": [done_id]}, today=TODAY)["routes"][0]
    step = next(s for s in again["block"]["steps"] if s["id"] == done_id)
    assert step["completed"]
    assert again["block"]["effort_hours"]["v"] <= first["block"]["effort_hours"]["v"] + 4 * hafiz["weekly_hours"]


def test_b5_shared_foundations_reproduce_from_the_blocks(planner, hafiz):
    res = planner.routes(hafiz, {"weeks": 8}, today=TODAY)
    shared = res["shared"]
    assert shared is not None
    blocks = {r["code"]: {s["id"]: s for s in r["block"]["steps"] if s["kind"] == "learn" and s["serves"]} for r in res["routes"]}
    common = set.intersection(*(set(v) for v in blocks.values()))
    assert set(shared["common_steps"]) == common
    first = next(iter(blocks.values()))
    hours = round(sum(first[i]["provider_hours"]["v"] + first[i]["practice_hours"]["v"] for i in common), 1)
    assert shared["common_hours"]["v"] == hours
    for e in shared["extra_hours"]:
        r = next(x for x in res["routes"] if x["code"] == e["code"])
        assert e["hours"]["v"] == round(r["block"]["effort_hours"]["v"] - hours, 1)
    for sid in shared["shared_steps"]:
        for r in res["routes"]:
            if sid in blocks[r["code"]]:
                assert blocks[r["code"]][sid]["serves"]  # serves a prioritized gap of each route that shares it


def test_b6_update_comparison_is_repeatable_and_keeps_completed_work(planner, hafiz):
    target = planner.routes(hafiz, {"weeks": 4}, today=TODAY)["routes"][0]["code"]
    opts = {"weeks": 4, "target": target, "from_release": "2025-09-15", "to_release": "2026-06-26"}
    a = planner.compare(hafiz, opts, today=TODAY)
    b = planner.compare(hafiz, opts, today=TODAY)
    assert a == b
    done = a["changes"][0]["id"]
    c = planner.compare(hafiz, {**opts, "completed": [done]}, today=TODAY)
    kept = next(x for x in c["changes"] if x["id"] == done)
    assert kept["completed"] and c["target"] == target


def test_b6_a_real_dated_change_changes_a_step_form_with_provenance(planner, hafiz):
    target = planner.routes(hafiz, {"weeks": 4}, today=TODAY)["routes"][0]["code"]
    res = planner.compare(hafiz, {"weeks": 4, "target": target, "from_release": "2025-09-15", "to_release": "2026-06-26"},
                          today=TODAY)
    changed = [c for c in res["changes"] if c["change"] == "changed form"]
    assert changed, res["summary"]
    for c in changed:
        assert c["before"]["form"] != c["after"]["form"]
        for r in c["after"]["reason"]:
            assert r["now"]["src"] and all(s.startswith(("aei-", "onet31-")) for s in r["now"]["src"])


def test_b6_a_change_outside_the_plan_changes_nothing(snaps, ai, personas):
    """Perturb the AEI evidence of an activity no step serves: the plan must not move."""
    snaps2 = copy.copy(snaps)
    m = Matcher(snaps2)
    p2 = Planner(snaps2, m, Reporter(snaps2, m))
    prof = profile(snaps2, ai, personas["hafiz"], HAFIZ_SIDE_WORK)
    base = p2.routes(prof, {"weeks": 4}, today=TODAY)
    route = base["routes"][0]
    in_plan = {g["dwa_id"] for s in route["block"]["steps"] for g in s["serves"]}
    outside = next(d for d in snaps.onet.dwa_ids if d not in in_plan and p2.rep.skills.outlook(d) is not None)
    # rebuild the evidence with every task linked to `outside` pushed to 100% in the newest release
    rid, tasks, occ = snaps2.evidence.releases[-1]
    linked = {tid for ids in p2.rep.skills.groups[outside] for tid in ids}
    bumped = {k: ({**v, "share": 100.0} if k in linked else v) for k, v in tasks.items()}
    snaps2.evidence = type(snaps2.evidence)(snaps2.evidence.releases[:-1] + [(rid, bumped, occ)])
    p3 = Planner(snaps2, m, Reporter(snaps2, m))
    after = p3.routes(prof, {"weeks": 4}, today=TODAY)
    assert p3.rep.skills.outlook(outside)["now"] != p2.rep.skills.outlook(outside)["now"]
    strip = lambda r: [(s["id"], s["form"]) for s in r["block"]["steps"]]  # noqa: E731
    shared_ids = {s["id"] for s in route["block"]["steps"]}
    after_route = next(r for r in after["routes"] if r["code"] == route["code"])
    assert strip(after_route) == strip(route) and {s["id"] for s in after_route["block"]["steps"]} == shared_ids


def test_options_are_validated(planner, hafiz):
    with pytest.raises(ReportError) as e:
        planner.routes(hafiz, {"weeks": 5, "scenario": "x", "target": "00-0000.00", "completed": "a"}, today=TODAY)
    assert set(e.value.errors) == {"weeks", "scenario", "target", "completed"}
    with pytest.raises(ReportError):
        planner.compare(hafiz, {"weeks": 4}, today=TODAY)


def test_requirements_are_judged_only_from_the_intake(planner, hafiz, priya):
    for prof, level in ((hafiz, "diploma"), (priya, "degree")):
        for r in planner.routes(prof, {"weeks": 4}, today=TODAY)["routes"]:
            for q in r["requirements"]:
                if q["judge"] == "degree":
                    assert q["status"] == ("met" if level == "degree" else "missing")
                elif q["judge"] == "you_decide":
                    assert q["status"] == "you decide"
    longer = planner.routes(hafiz, {"weeks": 4, "willing_long": True}, today=TODAY)["routes"]
    assert all(q["status"] == "planned" for r in longer for q in r["requirements"] if q["judge"] == "degree")

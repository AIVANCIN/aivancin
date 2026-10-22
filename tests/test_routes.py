"""Routes, first learning blocks, shared foundations and the update comparison (SPEC section 10, checks B1-B6)."""
import copy
import csv
import datetime as dt

import pytest

from aivancin import intake, pathway
from aivancin.matching import Matcher
from aivancin.report import Reporter, ReportError
from aivancin import routes as routes_mod
from aivancin.routes import MAX_ROUTES, MIN_DONE, PER_GAP, Planner, effort, fits_family

TODAY = dt.date(2026, 10, 22)
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
    # 12 roles adopted 20 Oct plus the finance and data candidates of SPEC 11.7 (21 Oct); a candidate that fails the
    # rule is listed with its reason and never offered as a route.
    all_dests = snaps.routes["destinations"]
    dests = [d for d in all_dests if d["admitted"]]
    excluded = {d["code"]: [k for k, v in d["admission"].items() if not v] for d in all_dests if not d["admitted"]}
    assert len(dests) == 17 and excluded == {"13-2054.00": ["resources_for_3_top_activities"]}
    assert set(planner.dests) == {d["code"] for d in dests}
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
            assert r["overlap"]["v"] > 0 and r["done_count"]["v"] + len(r["related"]) >= MIN_DONE
            for x in r["related"]:
                assert x["dwa_id"] in planner.dests[r["code"]]["siblings"][x["role_dwa_id"]]
                assert x["role_dwa_id"] in planner.m.w[r["code"]] and x["dwa_id"] not in planner.m.w[r["code"]]
            # dimensions are reported separately, never folded into one score
            assert "score" not in r
            for key in ("requirements", "job_outlook", "block", "supported_share", "local_evidence"):
                assert key in r


def _manual_profile(snaps, dwas):
    req = {"weekly_hours": 6, "budget_rm": 0, "degree_level": "degree",
           "confirmed": [{"dwa_id": d, "source": "added_by_you", "lane": "other_work"} for d in dwas]}
    return intake.build_profile(req, snaps.onet, None, snaps.cip)


def test_b2_a_sibling_activity_counts_toward_the_two_activity_rule(snaps, planner, monkeypatch):
    """O*NET links "Design software applications" to Software Developers and its near-twin "Develop computer or
    online applications" to Computer Programmers only. Groups are read here from the raw activity file."""
    iwa = {r["dwa_id"]: r["iwa_id"] for r in csv.DictReader(open(snaps.data_dir / "onet" / "dwas.csv", encoding="utf-8"))}
    role, direct, sibling, other = "15-1252.00", "4.A.2.b.2.b.10", "4.A.2.b.2.b.4", "4.A.3.a.2.c.7"  # other: Cook foods
    w = planner.m.w[role]
    assert direct in w and sibling not in w and other not in w and iwa[direct] == iwa[sibling] != iwa[other]
    # Changed 22 Oct (each role activity counts once): this example used to open the route by counting "Design
    # software applications" twice, once as itself and once through its twin. The twin now counts toward a role
    # activity nobody else stands for; here it duplicates the confirmed one, so it is listed as counted once.
    modify = "4.A.2.b.2.b.7"  # Modify software programs to improve performance, recorded for the role
    assert modify in w and iwa[modify] == iwa[sibling]
    got_direct, related, once = planner.credited(role, {modify, sibling, other})
    assert got_direct == [modify] and related == [(sibling, direct)] and once == []
    assert iwa[related[0][1]] == iwa[sibling] and related[0][1] in w

    prof = _manual_profile(snaps, [modify, sibling])
    codes = [r["code"] for r in planner.routes(prof, {"weeks": 4}, today=TODAY)["routes"]]
    assert role in codes
    monkeypatch.setattr(routes_mod, "SIBLING_CREDIT", False)
    assert role not in [r["code"] for r in planner.routes(prof, {"weeks": 4}, today=TODAY)["routes"]]


def test_b2_one_role_activity_never_counts_twice(snaps, planner):
    """Confirming an activity and its near-twin is one piece of the role's work, not two: no route, and the role
    stays visible as close to the work with the duplicate named (22 Oct)."""
    role, direct, sibling = "15-1252.00", "4.A.2.b.2.b.10", "4.A.2.b.2.b.4"
    assert planner.credited(role, {direct, sibling}) == ([direct], [], [(sibling, direct)])
    out = planner.routes(_manual_profile(snaps, [direct, sibling]), {"weeks": 4}, today=TODAY)
    assert role not in [r["code"] for r in out["routes"]]
    near = {r["code"]: r for r in out["near_routes"]}
    assert near[role]["near"]["counted"]["v"] == 1 and near[role]["support"]["once"]["v"] == 1
    assert near[role]["counted_once"] == [{"dwa_id": sibling, "name": snaps.onet.dwa_names[sibling],
                                          "role_dwa_id": direct, "role_name": snaps.onet.dwa_names[direct]}]


def test_b2_near_twins_take_distinct_role_activities():
    """Two near-twins of overlapping role activities each count when a one-to-one assignment exists (the largest
    matching), and a third twin of the same activities counts once."""
    class Fake(Planner):
        def __init__(self):
            self.m = type("M", (), {"w": {"R": {"a": 3.0, "b": 2.0, "c": 1.0}}})()
            self.dests = {"R": {"siblings": {"a": ["x", "y", "z"], "b": ["x"]}}}
    p = Fake()
    assert p.credited("R", {"y", "x"}) == ([], [("x", "b"), ("y", "a")], [])
    assert p.credited("R", {"x", "y", "z"}) == ([], [("x", "b"), ("y", "a")], [("z", "a")])
    assert p.credited("R", {"a", "x", "y"}) == (["a"], [("x", "b")], [("y", "a")])
    assert p.credited("R", {"c", "z"}) == (["c"], [("z", "a")], [])


def test_b2_sibling_tables_hold_only_near_twins(snaps, planner, ai):
    """Every table entry shares the raw file's intermediate work activity and has name similarity >= the rule's."""
    iwa = {r["dwa_id"]: r["iwa_id"] for r in csv.DictReader(open(snaps.data_dir / "onet" / "dwas.csv", encoding="utf-8"))}
    names = ai[1].names
    pos = {d: i for i, d in enumerate(snaps.onet.dwa_ids)}
    floor = snaps.routes["sibling_rule"]["min_name_similarity"]
    n = 0
    for code, dest in planner.dests.items():
        for d, twins in dest["siblings"].items():
            assert d in planner.m.w[code]
            for x in twins:
                n += 1
                assert x not in planner.m.w[code] and iwa[x] == iwa[d]
                assert float(names[pos[d]] @ names[pos[x]]) >= floor - 1e-6
    assert n > 0


def test_b2_a_loose_group_member_does_not_count(snaps, planner):
    """Same O*NET group, different work: a driver's "Read maps to determine routes" is not a near-twin of
    "Read documents to gather technical information" (User Support)."""
    role, maps, docs = "15-1232.00", "4.A.1.a.1.b.2", "4.A.1.a.1.b.7"
    assert snaps.onet.dwa_names[maps].startswith("Read maps") and docs in planner.m.w[role]
    assert snaps.onet.dwa_iwa[maps] == snaps.onet.dwa_iwa[docs]
    _, related, once = planner.credited(role, {maps})
    assert related == [] and once == []

def test_b2_a_route_still_needs_one_exact_activity(snaps, planner):
    """Siblings alone never open a route: overlap with the role's own activities must be above zero."""
    prof = _manual_profile(snaps, ["4.A.2.b.2.b.4", "4.A.2.b.2.b.1"])  # both absent from Software Developers
    for r in planner.routes(prof, {"weeks": 4}, today=TODAY)["routes"]:
        assert r["overlap"]["v"] > 0

def test_b2_families_follow_the_work(planner, hafiz, priya):
    h = {r["family"] for r in planner.routes(hafiz, {"weeks": 4}, today=TODAY)["routes"]}
    p = {r["family"] for r in planner.routes(priya, {"weeks": 4}, today=TODAY)["routes"]}
    assert h <= {"it_support", "software"} and "finance_analytics" in p


@pytest.mark.parametrize("order", ["default", "growing"])  # C7: B3 under both gap orders
@pytest.mark.parametrize("weeks", [4, 8, 12])
def test_b3_block_fits_time_and_budget_and_counts_each_entry_once(snaps, planner, hafiz, weeks, order):
    res = planner.routes(hafiz, {"weeks": weeks, "scenario": "faster", "threshold": 30, "gap_order": order}, today=TODAY)
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



def test_b3_blocks_use_only_entries_meant_for_the_destination_family(snaps, planner, hafiz, priya):
    """Unit-testing modules teach "Test software performance" for developers, not for IT support."""
    entries = {c["id"]: c for c in snaps.catalogue["courses"]}
    assert any(c.get("families") for c in entries.values())
    for prof in (hafiz, priya):
        for code, dest in planner.dests.items():
            res = planner.routes(prof, {"weeks": 12, "target": code}, today=TODAY)
            route = next(r for r in res["routes"] if r["code"] == code)
            for s in route["block"]["steps"]:
                assert fits_family(entries[s["id"]], dest["family"]), (code, s["id"])


def _entry(cid, gap, hours, practice):
    return {"id": cid, "dwas": [gap], "cost_rm": 0, "hours": hours, "practice_hours": practice, "requires": [],
            "ai_skill": False, "families": []}


@pytest.mark.parametrize("capacity,expected", [(20, {"short-g1", "short-g2", "short-g3"}),
                                               (30, {"short-g1", "short-g2", "short-g3", "long-g1"})])
def test_b3_a_long_entry_waits_for_shorter_ones(planner, capacity, expected):
    """A 15-hour course on the heaviest gap must not crowd out three 3-hour courses for three gaps.

    Worked by hand: shorter entries first use 9 hours; the long one then fits only when 24 <= capacity.
    """
    missing = [{"dwa_id": "g1", "weight": 4.0, "name": "g1"}, {"dwa_id": "g2", "weight": 1.0, "name": "g2"},
               {"dwa_id": "g3", "weight": 1.0, "name": "g3"}]
    courses = [_entry("long-g1", "g1", 10.0, 5.0), _entry("short-g1", "g1", 2.0, 1.0),
               _entry("short-g2", "g2", 2.0, 1.0), _entry("short-g3", "g3", 2.0, 1.0)]
    b = planner.block("15-1231.00", missing, courses, {"capacity": capacity, "budget": 0, "completed": set()})
    assert {x["id"] for x in b["steps"]} == expected
    assert b["served"] == {"g1", "g2", "g3"} and b["used"] <= capacity

@pytest.mark.parametrize("order", ["default", "growing"])  # C7: B4 under both gap orders
def test_b4_no_step_before_an_entry_it_requires(planner, hafiz, priya, order):
    for prof in (hafiz, priya):
        for weeks in (4, 8, 12):
            for r in planner.routes(prof, {"weeks": weeks, "gap_order": order}, today=TODAY)["routes"]:
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
    assert shared["common_count"]["v"] == len(common) and shared["common_count"]["src"]
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


def test_routes_carry_the_onet_description_and_ai_use_by_threshold(snaps, planner, hafiz):
    with open(snaps.data_dir / "onet" / "occupations.csv", encoding="utf-8", newline="") as f:
        described = {r["code"]: r["description"] for r in csv.DictReader(f)}
    prof = planner.m.lane_profile(hafiz["activities"], "all")
    for threshold in (50, 80):
        res = planner.routes(hafiz, {"threshold": threshold}, TODAY)
        for r in res["routes"]:
            d = r["description"]
            assert d["text"] == described[r["code"]] and d["text"].startswith(d["first"][:-1]) and d["src"] == ["onet31-occupations"]
            weights = planner.m.w[r["code"]]
            assert [a["dwa_id"] for a in r["ai_use"]] == sorted(weights, key=lambda x: (-weights[x], x))[:routes_mod.AI_USE_ACTIVITIES]
            for a in r["ai_use"]:
                assert a["done"] == (a["dwa_id"] in prof)
                if a["now"] is None:
                    assert a["class"] == "no evidence in the bundled releases"
                elif a["now"]["v"] >= threshold:
                    assert a["class"] == "AI does most of it directly"
                else:
                    assert a["class"] in ("shared", "people mostly work with AI")
    # Raising the threshold moves activities out of "AI does most of it directly", never into it.
    low = {a["dwa_id"] for r in planner.routes(hafiz, {"threshold": 50}, TODAY)["routes"] for a in r["ai_use"] if a["class"].startswith("AI")}
    high = {a["dwa_id"] for r in planner.routes(hafiz, {"threshold": 80}, TODAY)["routes"] for a in r["ai_use"] if a["class"].startswith("AI")}
    assert high < low


def test_learn_lists_every_verified_entry_per_gap_with_block_entries_first(snaps, planner, hafiz, priya):
    verified = pathway.verified_courses(snaps.catalogue, TODAY)
    usable = [c for c in verified if (c.get("checkpoint") and not c.get("ai_skill")) or (c.get("ai_skill") and c.get("dwas"))]
    left_out = 0
    for prof in (hafiz, priya):
        for r in planner.routes(prof, {"weeks": 4}, TODAY)["routes"]:
            block = {s["id"] for s in r["block"]["steps"]}
            assert [g["dwa_id"] for g in r["resources"]] == [g["dwa_id"] for g in r["prioritized"]]
            for g in r["resources"]:
                expected = {c["id"] for c in usable if g["dwa_id"] in c.get("dwas", []) and fits_family(c, r["family"])}
                ids = [o["id"] for o in g["options"]]
                assert set(ids) == expected and len(ids) == len(expected)
                assert all(o["in_block"] == (o["id"] in block) for o in g["options"])
                flags = [o["in_block"] for o in g["options"]]
                assert flags == sorted(flags, reverse=True)
                assert g["served"] == (g["dwa_id"] in r["block"]["gaps_served"])
                left_out += sum(not f for f in flags)
    # The point of the list: alternatives the four-week block leaves out are visible.
    assert left_out > 0


def test_explore_lists_every_supported_role_with_nothing_counted_and_assumptions_labelled(planner):
    d = planner.explore({}, TODAY)
    assert d["personal"] is False and d["shared"] is None and d["fewer_note"] is None
    assert [r["code"] for r in d["routes"]] == sorted(planner.dests)
    assert d["capacity_hours"] == {"v": 24.0, "src": ["explore-defaults"]} and d["budget_rm"]["v"] == 0
    for r in d["routes"]:
        assert r["done_count"]["v"] == 0 and r["overlap"]["v"] == 0 and r["done"] == [] and r["related"] == []
        # Before any work is described, the gaps are simply the role's largest activities.
        top = [k for k, _ in sorted(planner.m.w[r["code"]].items(), key=lambda kv: (-kv[1], kv[0]))[:routes_mod.PRIORITY_GAPS]]
        assert [g["dwa_id"] for g in r["prioritized"]] == top
        # Nothing about the person is assumed: degree requirements are left to them, never met or missing.
        for q in r["requirements"]:
            if q["judge"] == "degree":
                assert q["status"] == "you decide"
            assert q["status"] != "missing"
        b = r["block"]
        assert b["capacity_hours"]["src"] == ["explore-defaults"] and b["horizon"]["months"]["src"] == ["explore-defaults"]
        assert b["cost_rm"]["v"] == 0 and b["effort_hours"]["v"] <= 24.0 + 1e-9
        assert all(not s["completed"] for s in b["steps"])
    with_target = planner.explore({"target": "15-1254.00", "weeks": 8}, TODAY)
    assert with_target["target"] == "15-1254.00" and with_target["capacity_hours"]["v"] == 48.0
    with pytest.raises(ReportError):
        planner.explore({"weeks": 5}, TODAY)


# ---- C7: "Skills in growing jobs first" (SPEC 11.3) ------------------------------------------------
@pytest.mark.parametrize("order,expected", [("default", {"short-g1"}), ("growing", {"short-g2"})])
def test_c7_growing_order_puts_gaps_in_growing_jobs_first(planner, monkeypatch, order, expected):
    """Worked by hand: 3 hours fit one 3-hour entry. By weight the heavier gap g1 goes first; with the option, g2,
    whose activity has more growth than decline, goes before g1, whose activity has more decline than growth."""
    labels = {"g1": "more decline than growth", "g2": "more growth than decline"}
    monkeypatch.setattr(planner.rep.demand, "get", lambda d: {"label": labels[d]})
    missing = [{"dwa_id": "g1", "weight": 4.0, "name": "g1"}, {"dwa_id": "g2", "weight": 1.0, "name": "g2"}]
    courses = [_entry("short-g1", "g1", 2.0, 1.0), _entry("short-g2", "g2", 2.0, 1.0)]
    b = planner.block("15-1231.00", missing, courses, {"capacity": 3, "budget": 0, "completed": set(), "gap_order": order})
    assert {x["id"] for x in b["steps"]} == expected


def test_c7_option_off_is_the_default_and_the_plan_names_the_difference(planner, hafiz, priya):
    changed = 0
    for prof in (hafiz, priya):
        for weeks in (4, 8, 12):
            off = planner.routes(prof, {"weeks": weeks}, today=TODAY)
            assert planner.routes(prof, {"weeks": weeks, "gap_order": "default"}, today=TODAY) == off
            on = planner.routes(prof, {"weeks": weeks, "gap_order": "growing"}, today=TODAY)
            assert [r["code"] for r in on["routes"]] == [r["code"] for r in off["routes"]]
            for a, b in zip(off["routes"], on["routes"]):
                assert a["block"]["order_diff"] is None and a["block"]["gap_order"] == "default"
                before = {s["title"] for s in a["block"]["steps"] if s["kind"] != "ai_practice"}
                after = {s["title"] for s in b["block"]["steps"] if s["kind"] != "ai_practice"}
                diff = b["block"]["order_diff"]
                assert (set(diff["added"]), set(diff["removed"])) == (after - before, before - after)
                assert diff["reordered"] == ([s["id"] for s in a["block"]["steps"] if s["kind"] != "ai_practice"]
                                             != [s["id"] for s in b["block"]["steps"] if s["kind"] != "ai_practice"])
                # 22 Oct: since the tie-break by priority-gap weight, the option reorders these blocks without
                # changing their steps, so a reorder counts as the option's effect too
                changed += bool(diff["added"] or diff["removed"] or diff["reordered"])
    assert changed > 0  # the option changes at least one persona block


def test_c7_unknown_gap_order_is_refused(planner, hafiz):
    with pytest.raises(ReportError) as err:
        planner.routes(hafiz, {"gap_order": "fastest"}, today=TODAY)
    assert "gap_order" in err.value.errors

"""Fixes from the 23 Oct preliminary review (PROJECT "Preliminary product improvements toward 95"): each test
reproduces a failure found by the review's probes and checks the repaired behaviour."""
import json
import math

import pytest
from fastapi.testclient import TestClient

from aivancin import intake, outlook, pathway
from aivancin.app import create_app
from aivancin.matching import Matcher
from aivancin.report import Reporter
from aivancin.routes import EXPLORE_PROFILE, Planner

from .conftest import DATA

HAFIZ_TICKS = ["4.A.3.b.1.b.2", "4.A.3.b.1.a.3", "4.A.3.b.1.c.1", "4.A.3.b.1.f.4", "4.A.3.b.1.d.1"]


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(DATA), raise_server_exceptions=False)


@pytest.fixture(scope="module")
def planner(snaps):
    m = Matcher(snaps)
    return Planner(snaps, m, Reporter(snaps, m))


@pytest.fixture(scope="module")
def hafiz_req(personas):
    return {**personas["hafiz"], "confirmed": [{"dwa_id": d, "source": "added_by_you", "lane": "other_work"} for d in HAFIZ_TICKS]}


# ---- item 14: malformed input is a field error, never a crash or a silent default ----------------------------------
@pytest.mark.parametrize("path, change, field", [
    ("/api/suggest", {"current_job": ["x"]}, "current_job"),
    ("/api/routes", {"confirmed": ["wrong type"]}, "confirmed[0]"),
    ("/api/routes", {"options": []}, "options"),
    ("/api/routes", {"options": {"target": ["15-1254.00"]}}, "target"),
    ("/api/routes", {"cip_code": ["11.0101"]}, "cip_code"),
    ("/api/results", {"options": {"known_tools": [1]}}, "known_tools"),
    ("/api/routes", {"unticked_title_activities": "4.A.3.b.1.c.1"}, "unticked_title_activities"),
    ("/api/routes", {"weekly_hours": 10 ** 9}, "weekly_hours"),
    ("/api/routes", {"budget_rm": -0.5}, "budget_rm"),
])
def test_malformed_requests_get_field_errors(client, hafiz_req, path, change, field):
    r = client.post(path, json={**hafiz_req, **change})
    assert r.status_code == 422, r.text
    assert field in r.json()["errors"]


def test_non_finite_numbers_are_refused(client, hafiz_req):
    body = json.dumps({**hafiz_req, "weekly_hours": math.nan}, allow_nan=True)
    r = client.post("/api/routes", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422 and "weekly_hours" in r.json()["errors"]
    body = json.dumps({**hafiz_req, "options": {"threshold": math.inf}}, allow_nan=True)
    r = client.post("/api/routes", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422 and "threshold" in r.json()["errors"]


def test_oversized_text_is_refused_with_a_message_not_cut(client):
    long = "I fix laptops for my neighbours. " * 200
    r = client.post("/api/suggest", json={"other_work": long})
    assert r.status_code == 422 and "nothing was read" in r.json()["errors"]["other_work"]
    many = "I fix laptops. " * (intake.SENTENCE_LIMIT + 1)
    assert client.post("/api/suggest", json={"other_work": many}).status_code == 422
    assert client.get("/api/activities", params={"q": "x" * 201}).status_code == 422
    # Text within the bounds is read as before.
    assert client.post("/api/suggest", json={"other_work": "I fix laptops for my neighbours."}).status_code == 200


# ---- item 12: completed work takes none of the remaining time ------------------------------------------------------
def _ai_case(planner, completed, scenario="faster"):
    profile = {**EXPLORE_PROFILE, "weekly_hours": 5}
    o = {**planner.options({"scenario": scenario, "weeks": 4, "completed": completed}, profile), "input_src": "your-input"}
    verified = pathway.verified_courses(planner.snaps.catalogue)
    courses = [c for c in verified if c.get("checkpoint") and not c.get("ai_skill")]
    ai = [{**c, "practice_hours": c.get("practice_hours", 0.5), "checkpoint": c.get("checkpoint") or "Check."}
          for c in verified if c.get("ai_skill") and c.get("dwas")]
    return planner.route("13-2011.00", set(), profile, courses, ai, o)


def test_completed_ai_practice_frees_its_hours_for_the_next_practice(planner):
    """The review's reproduction: Accountants, 5 h a week, 4 weeks, RM0, faster. With the finance Copilot practice
    done, the 0.8-hour sales-data practice used to stay out of a 20-hour block holding 19 hours of open work."""
    done = "learn.dpu.work-smarter-copilot-finance"
    r = _ai_case(planner, [done])
    ids = {s["id"]: s for s in r["block"]["steps"]}
    assert ids[done]["completed"] and ids[done]["kind"] == "ai_practice"
    assert "learn.dpu.summarize-sales-data" in ids
    open_hours = sum(s["provider_hours"]["v"] + s["practice_hours"]["v"] for s in r["block"]["steps"] if not s["completed"])
    assert open_hours <= r["block"]["capacity_hours"]["v"] + 1e-9
    assert r["block"]["effort_hours"]["v"] == pytest.approx(round(open_hours, 1))


def test_completed_ai_practice_stays_visible_in_every_scenario(planner):
    done = "learn.dpu.work-smarter-copilot-finance"
    for scenario in outlook.SCENARIOS:
        steps = {s["id"]: s for s in _ai_case(planner, [done], scenario)["block"]["steps"]}
        assert steps[done]["completed"], scenario


def test_a_completed_prerequisite_stays_visible_before_the_step_that_needs_it(planner, snaps):
    by_id = {c["id"]: c for c in snaps.catalogue["courses"]}
    profile = {**EXPLORE_PROFILE, "weekly_hours": 10}
    for code in planner.dests:
        r = planner.routes(profile, {"weeks": 12, "target": code}, explore=True)
        route = next(x for x in r["routes"] if x["code"] == code)
        needs = [(s["id"], q) for s in route["block"]["steps"] for q in s["requires"] if q in by_id]
        if not needs:
            continue
        step, pre = needs[0]
        r2 = planner.routes(profile, {"weeks": 12, "target": code, "completed": [pre]}, explore=True)
        steps = [s["id"] for s in next(x for x in r2["routes"] if x["code"] == code)["block"]["steps"]]
        assert pre in steps and step in steps and steps.index(pre) < steps.index(step)
        return
    pytest.fail("no supported route has a prerequisite to test")


# ---- item 6: the survey's most common answer is not a met requirement ----------------------------------------------
def test_no_experience_survey_answer_is_not_marked_met(planner, personas):
    profile = {**EXPLORE_PROFILE, "degree_level": "degree"}
    o = {**planner.options({}, profile), "input_src": "your-input"}
    for code, dest in planner.dests.items():
        for req in dest["requirements"]:
            if req["judge"] != "none":
                continue
            status, why = planner.requirement_status(req, profile, o)
            assert status == "you decide", code
            share = req["text"].split("(")[1].split("%")[0]
            assert f"{share}% of respondents" in why and "Each employer decides" in why


def test_an_unanswered_level_is_unknown_not_a_diploma(planner, snaps, ai, hafiz_req):
    req = {**hafiz_req, "degree_level": None}
    prof = intake.build_profile(req, snaps.onet, intake.suggest(req, *ai, snaps.onet), snaps.cip)
    assert prof["degree_level"] is None
    res = planner.routes(prof, {})
    for r in res["routes"]:
        degree = [q for q in r["requirements"] if q["judge"] == "degree"]
        assert all(q["status"] == "you decide" and "have not said" in q["why"] for q in degree)
    assert all(lab["id"] != "needs_degree" for r in res["routes"] for lab in planner.rep.labels(r["code"], prof))
    with pytest.raises(intake.IntakeError):
        intake.build_profile({**req, "degree_level": "phd"}, snaps.onet, None, snaps.cip)


# ---- item 2: job-title activities count only when affirmed -------------------------------------------------------
def test_title_activities_count_only_when_ticked(snaps):
    code = "53-3054.00"  # taxi drivers
    listed = [a["dwa_id"] for a in intake.title_activities(snaps.onet, code)]
    base = {"title_code": code, "degree_level": "degree", "weekly_hours": 6, "budget_rm": 0}
    with pytest.raises(intake.IntakeError) as e:
        intake.build_profile({**base, "affirmed_title_activities": []}, snaps.onet, None, snaps.cip)
    assert "activities" in e.value.errors
    prof = intake.build_profile({**base, "affirmed_title_activities": listed[:2]}, snaps.onet, None, snaps.cip)
    assert [a["dwa_id"] for a in prof["activities"]] == sorted(listed[:2])
    assert all(a["sources"] == ["title"] for a in prof["activities"])
    with pytest.raises(intake.IntakeError) as e:
        intake.build_profile({**base, "affirmed_title_activities": ["4.A.3.b.1.c.4"]}, snaps.onet, None, snaps.cip)
    assert "affirmed_title_activities" in e.value.errors
    # Without the affirmed list (older callers, the title-only comparison) every listed activity counts as before.
    legacy = intake.build_profile(base, snaps.onet, None, snaps.cip)
    assert len(legacy["activities"]) == len(listed)


# ---- item 5: the person's order applies before the three routes are picked -----------------------------------------
def test_outlook_order_can_bring_in_a_route_the_overlap_order_leaves_out(planner, snaps, ai, hafiz_req):
    prof = intake.build_profile(hafiz_req, snaps.onet, intake.suggest(hafiz_req, *ai, snaps.onet), snaps.cip)
    by_overlap = planner.routes(prof, {})
    by_outlook = planner.routes(prof, {"order": "outlook"})
    assert by_overlap["qualifying"]["v"] == by_outlook["qualifying"]["v"] >= 4
    shown = [r["code"] for r in by_overlap["routes"]]
    assert "15-1253.00" not in shown and "15-1253.00" in [r["code"] for r in by_outlook["routes"]]
    bands = [outlook.band_score(r["job_outlook"].get("band")) for r in by_outlook["routes"]]
    assert bands == sorted(bands, reverse=True)
    # A chosen route outside the three stays in the comparison.
    kept = planner.routes(prof, {"order": "outlook", "target": shown[0]})
    assert shown[0] in [r["code"] for r in kept["routes"]]
    with pytest.raises(Exception):
        planner.routes(prof, {"order": "random"})


@pytest.mark.parametrize("order", ["entry", "decent"])
def test_other_orders_rank_every_qualifying_route(planner, snaps, ai, hafiz_req, order):
    prof = intake.build_profile(hafiz_req, snaps.onet, intake.suggest(hafiz_req, *ai, snaps.onet), snaps.cip)
    res = planner.routes(prof, {"order": order})
    assert res["order"] == order and len(res["routes"]) == 3


# ---- item 7: the latest observation is reported beside the average baseline ---------------------------------------
def test_latest_observation_is_separate_from_the_average_baseline(planner, snaps):
    skills = planner.rep.skills
    d = next(x for x, n in snaps.onet.dwa_names.items() if n.startswith("Develop computer or information security policies"))
    o = skills.outlook(d)
    vals = skills.release_values(d)
    assert o["now"] == round(sum(v for _, v, _ in vals) / len(vals), 1)
    assert o["latest"] == {"release": vals[-1][0], "share": round(vals[-1][1], 1)}
    assert abs(o["latest"]["share"] - o["now"]) > 10
    row = planner.rep.skill_row(d, "x", None, 12, 50, 1.444, "gap")
    assert row["latest"]["v"] == o["latest"]["share"] and row["latest"]["release"] == vals[-1][0]
    assert row["latest"]["src"] == [f"aei-{vals[-1][0]}", "onet31-task-dwas"]
    # At an earlier release the latest is that release's own value.
    first = snaps.evidence.ids[0]
    early = skills.outlook(d, first)
    if early:
        assert early["latest"]["release"] == first


# ---- item 9: the slower fall is as known at the chosen release ------------------------------------------------------
def test_slower_fall_uses_only_releases_up_to_the_chosen_one(planner, snaps):
    skills = planner.rep.skills
    ids = snaps.evidence.ids
    assert skills.drop_at(ids[0]) == 0.0
    assert skills.drop_at(ids[-1]) == skills.drop == skills.drop_at(None)
    # (not monotone: an earlier release has a different set of tasks common to the releases up to it)
    for rid in ids:
        assert skills.project(50.0, "slower", 12, 1.444, rid) == pytest.approx(max(0.0, 50.0 - skills.drop_at(rid)))
    meta = planner.rep._forecast_meta(50, ids[0])
    assert meta["slower_drop"]["v"] == 0.0 and meta["slower_drop"]["src"] == [f"aei-{ids[0]}"]


# ---- items 1 and 8: a long sentence is read to its end ----------------------------------------------------------------
def test_the_end_of_a_long_sentence_is_read(snaps, ai):
    """The review's boundary probe: two 305-word sentences sharing a prefix used to get identical suggestions."""
    prefix = "I answer customer questions about product availability and delivery times " * 30
    a = intake.suggest({"current_job": prefix + "and I configure computer networks"}, *ai, snaps.onet)
    b = intake.suggest({"current_job": prefix + "and I reconcile financial accounts"}, *ai, snaps.onet)
    assert a["sentences"][0]["parts"] > 1
    assert "4.A.3.b.1.c.4" in a["sentences"][0]["top"]  # Configure computer networks.
    assert "4.A.2.a.2.d.1" in b["sentences"][0]["top"]  # Reconcile records of sales or other financial transactions.
    assert len(a["sentences"][0]["top"]) <= intake.LONG_K


def test_short_sentences_are_read_whole_as_before(snaps, ai, personas):
    sug = intake.suggest(personas["hafiz"], *ai, snaps.onet)
    assert all(s.get("parts", 0) == 0 and len(s["top"]) <= intake.TOP_K for s in sug["sentences"])
    assert intake.windows("one two three") == []


def test_two_peoples_work_in_one_sentence_asks_who_did_it():
    s = "My manager reconciles the sales records on her laptop while I stack the shelves and check the expiry dates"
    assert intake.guard(s) == {"kind": "unclear who", "cue": "My manager"}
    # The person's own work, with or without someone else, is unchanged.
    assert intake.guard("I reconcile the sales records while my manager checks the shelves") is None
    assert intake.guard("He showed me how to build spreadsheets, and now I make them") is None


# ---- item 13: a fallback that says why there is no route --------------------------------------------------------
def test_fallback_names_why_fewer_than_two_routes_qualify(planner, snaps):
    base = {"degree_level": "degree", "weekly_hours": 6, "budget_rm": 0}
    taxi = [a["dwa_id"] for a in intake.title_activities(snaps.onet, "53-3054.00")]
    prof = intake.build_profile({**base, "title_code": "53-3054.00", "affirmed_title_activities": taxi}, snaps.onet, None, snaps.cip)
    res = planner.routes(prof, {})
    assert res["fallback"]["kind"] == "title_only" and res["fallback"]["own"]["v"] == 0
    # One heavily weighted driving activity no longer makes "Airfield Operations Specialists" a card.
    assert all(c["done_count"]["v"] >= 2 for c in res["exploration"])
    assert "53-2022.00" not in [c["code"] for c in res["exploration"]]

    def own(*ids):
        req = {**base, "confirmed": [{"dwa_id": d, "source": "added_by_you", "lane": "other_work"} for d in ids]}
        return planner.routes(intake.build_profile(req, snaps.onet, None, snaps.cip), {})
    cleaning = next(d for d, n in snaps.onet.dwa_names.items() if n.startswith("Clean facilities or work areas"))
    assert own(cleaning)["fallback"]["kind"] == "outside_supported"
    assert own("4.A.3.b.1.d.1")["fallback"]["kind"] == "thin_support"  # Resolve computer network problems.
    assert planner.routes(intake.build_profile({**base, "confirmed": [
        {"dwa_id": d, "source": "added_by_you", "lane": "other_work"} for d in HAFIZ_TICKS]}, snaps.onet, None, snaps.cip), {})["fallback"] is None


# ---- item 1: a negation about a thing does not hold back the person's own work --------------------------------------
@pytest.mark.parametrize("sentence, held", [
    ("I am the person my church calls when the projector or the laptop will not connect.", False),
    ("I also cleaned the data so that duplicate calls are not counted twice.", False),
    ("I reset passwords for staff who cannot log in to the shared drive.", False),
    ("I fix the office printer whenever it will not print from the laptops.", False),
    ("I have not set up a network for anyone yet.", True),
    ("Never built a website, but I would like to.", True),
    ("It is not my job to reconcile the bank statements.", True),
    ("We do not repair computers at my workplace.", True),
])
def test_negation_counts_only_for_the_persons_own_work(sentence, held):
    g = intake.guard(sentence)
    assert (g is not None and g["kind"] == "negated") == held
    intake.NEGATION_SCOPE = False
    try:
        assert intake.guard(sentence)["kind"] == "negated"  # the rule before 23 Oct held every one back
    finally:
        intake.NEGATION_SCOPE = True

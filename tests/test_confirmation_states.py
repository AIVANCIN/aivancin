"""Confirmation, qualification and fallback states: a rejected sentence keeps none of its ticks, a planned
qualification stays unfinished, one degree expectation counts once, and limited overlap gets an accurate fallback."""
import datetime as dt

import pytest

from aivancin import intake
from aivancin.matching import Matcher
from aivancin.report import Reporter
from aivancin.routes import MIN_DONE, Planner, merged_requirements

TODAY = dt.date(2026, 10, 21)
TEXT = {"current_job": "I drive a van for a courier company.",
        "other_work": "I fix my friends' laptops at the weekend. I also fix laptops for my neighbours.",
        "studies": "", "weekly_hours": 6, "budget_rm": 0, "degree_level": "diploma"}


@pytest.fixture(scope="module")
def planner(snaps):
    m = Matcher(snaps)
    return Planner(snaps, m, Reporter(snaps, m))


@pytest.fixture(scope="module")
def sug(snaps, ai):
    encoder, vectors = ai
    return intake.suggest(TEXT, encoder, vectors, snaps.onet)


def _shared(sug):
    """An activity suggested for both laptop sentences (sentences 1 and 2)."""
    one, two = sug["sentences"][1]["top"], sug["sentences"][2]["top"]
    return next(d for d in one if d in two)


def test_an_activity_ticked_on_one_sentence_counts_only_through_that_sentence(snaps, sug):
    d = _shared(sug)
    prof = intake.build_profile({**TEXT, "confirmed": [{"dwa_id": d, "source": "suggested", "sentences": [1]}]},
                                snaps.onet, sug)
    assert [a["dwa_id"] for a in prof["activities"]] == [d]
    assert prof["activities"][0]["lanes"] == ["other_work"]


def test_lanes_come_from_the_sentences_it_was_ticked_on(snaps, sug):
    """A van-driving activity also suggested elsewhere keeps only the lane of the sentence that confirmed it."""
    job = sug["sentences"][0]["top"][0]
    prof = intake.build_profile({**TEXT, "confirmed": [{"dwa_id": job, "source": "suggested", "sentences": [0]}]},
                                snaps.onet, sug)
    assert prof["activities"][0]["lanes"] == ["current_job"]


def test_a_tick_on_a_sentence_that_did_not_suggest_it_is_refused(snaps, sug):
    job = sug["sentences"][0]["top"][0]
    wrong = next(s["index"] for s in sug["sentences"] if job not in s["top"])
    for sentences in ([wrong], [], [99], ["1"], [True]):
        with pytest.raises(intake.IntakeError) as e:
            intake.build_profile({**TEXT, "confirmed": [{"dwa_id": job, "source": "suggested", "sentences": sentences}]},
                                 snaps.onet, sug)
        assert "confirmed[0]" in e.value.errors


def test_ticks_without_sentences_keep_the_earlier_behaviour(snaps, sug):
    d = _shared(sug)
    prof = intake.build_profile({**TEXT, "confirmed": [{"dwa_id": d, "source": "suggested"}]}, snaps.onet, sug)
    assert prof["activities"][0]["lanes"] == ["other_work"]


def test_held_back_sentence_still_needs_the_second_action_when_named(snaps, ai):
    encoder, vectors = ai
    text = {**TEXT, "other_work": "I fix laptops for my friends. I have never set up a network."}
    sug = intake.suggest(text, encoder, vectors, snaps.onet)
    guarded = next(s for s in sug["sentences"] if s["guard"])
    only_there = next(d for d in guarded["top"]
                      if all(t["guarded"] for t in next(x for x in sug["suggestions"] if x["dwa_id"] == d)["triggers"]))
    item = {"dwa_id": only_there, "source": "suggested", "sentences": [guarded["index"]]}
    with pytest.raises(intake.IntakeError):
        intake.build_profile({**text, "confirmed": [item]}, snaps.onet, sug)
    prof = intake.build_profile({**text, "confirmed": [{**item, "guard_override": True}]}, snaps.onet, sug)
    assert prof["activities"][0]["guard_override"] is True


# ---- qualifications ------------------------------------------------------------------------------------------------
def _profile(snaps, ids, level="diploma"):
    return {"title_code": None, "degree_field": "", "cip_code": None, "degree_level": level, "weekly_hours": 6.0,
            "budget_rm": 0.0, "without_ai_suggestions": False,
            "activities": [{"dwa_id": d, "name": snaps.onet.dwa_names[d], "sources": ["added_by_you"],
                            "lanes": ["other_work"], "guard_override": False} for d in ids]}


def test_one_degree_expectation_counts_once_with_both_sources(snaps):
    """Job Zone 4 and the education survey both say "a bachelor's degree": one requirement, two pieces of evidence."""
    twice = [d for d in snaps.routes["destinations"] if sum(r["judge"] == "degree" for r in d["requirements"]) == 2]
    assert twice  # the defect's precondition is in the data
    for d in snaps.routes["destinations"]:
        degree = [r for r in d["requirements"] if r["judge"] == "degree"]
        assert all("bachelor" in r["text"].lower() for r in degree)
        merged = merged_requirements(d["requirements"])
        got = [r for r in merged if r["judge"] == "degree"]
        assert len(got) == (1 if degree else 0)
        if degree:
            assert [e["text"] for e in got[0]["evidence"]] == [r["text"] for r in degree]
            assert set(got[0]["src"]) == {s for r in degree for s in r["src"]}
        assert len(merged) == len(d["requirements"]) - max(len(degree) - 1, 0)


def test_willing_to_study_is_planned_never_met(snaps, planner):
    hafiz_side = ["4.A.3.b.1.d.1", "4.A.3.b.1.d.3", "4.A.3.b.1.c.1", "4.A.3.b.1.b.2", "4.A.3.b.1.f.4", "4.A.2.b.2.b.8"]
    for willing, expected in ((True, "planned"), (False, "missing")):
        out = planner.routes(_profile(snaps, hafiz_side), {"willing_long": willing}, TODAY)
        degree = [q for r in out["routes"] for q in r["requirements"] if q["judge"] == "degree"]
        assert degree and {q["status"] for q in degree} == {expected}
        # one degree requirement per route, whatever the number of sources
        assert all(sum(q["judge"] == "degree" for q in r["requirements"]) <= 1 for r in out["routes"])
    out = planner.routes(_profile(snaps, hafiz_side, level="degree"), {}, TODAY)
    assert {q["status"] for r in out["routes"] for q in r["requirements"] if q["judge"] == "degree"} == {"met"}


# ---- limited evidence ----------------------------------------------------------------------------------------------
def test_limited_overlap_lists_the_supported_roles_it_touches(snaps, planner):
    """Driving plus one finance activity: three finance roles record it, none counts two, so there is no route. The
    fallback names those roles (with the activity that counts) instead of only unrelated exploration roles."""
    out = planner.routes(_profile(snaps, ["4.A.3.a.4.a.10", "4.A.3.b.6.a.2"]), {}, TODAY)
    assert out["routes"] == []
    near = {r["code"]: r for r in out["near_routes"]}
    assert set(near) == {"13-2031.00", "13-2041.00", "13-2011.00"}
    for r in near.values():
        assert r["near"]["counted"]["v"] == 1 and r["near"]["needed"]["v"] == MIN_DONE
        assert [x["dwa_id"] for x in r["done"]] == ["4.A.3.b.6.a.2"]
        assert r["near"]["counted"]["src"] and r["near"]["needed"]["src"]
    assert out["min_done"]["v"] == MIN_DONE


def test_near_roles_never_count_as_routes_and_need_real_overlap(snaps, planner):
    # related credit alone (no activity the role itself records) is not overlap and is not listed
    out = planner.routes(_profile(snaps, ["4.A.3.b.6.h.9", "4.A.3.b.6.h.7"]), {}, TODAY)
    assert out["routes"] == [] and out["near_routes"] == []
    # with two or more routes there is no fallback list
    hafiz_side = ["4.A.3.b.1.d.1", "4.A.3.b.1.d.3", "4.A.3.b.1.c.1", "4.A.3.b.1.b.2", "4.A.3.b.1.f.4", "4.A.2.b.2.b.8"]
    out = planner.routes(_profile(snaps, hafiz_side), {}, TODAY)
    assert len(out["routes"]) >= 2 and out["near_routes"] == []

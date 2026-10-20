import json

import pytest

from aivancin import intake

NEGATED = [
    "I have never repaired a laptop.",
    "I don't manage the shop's accounts.",
    "I haven’t built a website yet.",
    "I no longer drive at night.",
    "I can't fix printers.",
    "My boss doesn't let me handle the cash.",
    "I did not set up the office network.",
]
ASPIRATIONAL = [
    "I would like to learn networking.",
    "I want to become a web developer.",
    "I hope to work in IT support.",
    "I plan to study for a cloud certificate.",
    "I'm learning to code in Python.",
]
PLAIN = [
    "I plan my route each morning.",
    "I install software and clean out viruses.",
    "Nothing breaks when I update the site.",
    "I notify customers when a parcel is late.",
]


def texts(**kw):
    return {f: kw.get(f, "") for f in intake.FIELDS}


def test_split_keeps_offsets_into_the_original_text():
    text = "  I drive a van. I load it at 7.30 am!\nOn weekends I fix laptops;   I also set up wifi\n\n"
    spans = intake.split_sentences(text)
    assert [s[2] for s in spans] == ["I drive a van.", "I load it at 7.30 am!", "On weekends I fix laptops;",
                                     "I also set up wifi"]
    for start, end, body in spans:
        assert text[start:end] == body


def test_split_ignores_empty_and_punctuation_only_input():
    assert intake.split_sentences("") == []
    assert intake.split_sentences("  \n ... \n") == []


@pytest.mark.parametrize("sentence", NEGATED)
def test_guard_flags_negated_sentences(sentence):
    assert intake.guard(sentence)["kind"] == "negated"


@pytest.mark.parametrize("sentence", ASPIRATIONAL)
def test_guard_flags_aspirational_sentences(sentence):
    assert intake.guard(sentence)["kind"] == "aspirational"


@pytest.mark.parametrize("sentence", PLAIN)
def test_guard_leaves_plain_statements_alone(sentence):
    assert intake.guard(sentence) is None


def test_hafiz_side_work_suggests_computer_support_activities(snaps, ai, personas):
    enc, vec = ai
    result = intake.suggest(personas["hafiz"], enc, vec, snaps.onet)
    by_sentence = {s["text"]: s["top"] for s in result["sentences"]}
    assert len(by_sentence["I install software and clean out viruses."]) == intake.TOP_K
    assert "4.A.3.b.1.d.1" in by_sentence["On weekends I fix my friends' laptops and set up their home wifi."]
    assert "4.A.3.b.1.c.1" in by_sentence["I install software and clean out viruses."]
    assert "4.A.3.b.1.b.2" in by_sentence["I back up their phones and laptops to external drives."]
    assert "4.A.3.b.1.f.4" in by_sentence["I built a website for my uncle's hardware shop and I keep it updated."]


def test_each_activity_is_shown_once_with_all_its_sentences(snaps, ai, personas):
    enc, vec = ai
    result = intake.suggest(personas["hafiz"], enc, vec, snaps.onet)
    ids = [s["dwa_id"] for s in result["suggestions"]]
    assert len(ids) == len(set(ids))
    code = next(s for s in result["suggestions"] if s["dwa_id"] == "4.A.3.b.1.a.3")  # Write computer programming code
    assert sorted(t["sentence"] for t in code["triggers"]) == [7, 8]
    scores = [s["best_score"] for s in result["suggestions"]]
    assert scores == sorted(scores, reverse=True)


def test_suggestions_are_deterministic(snaps, ai, personas):
    enc, vec = ai
    runs = [json.dumps(intake.suggest(personas["priya"], enc, vec, snaps.onet), sort_keys=True) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


def test_sentence_scores_do_not_depend_on_other_input(snaps, ai):
    enc, vec = ai
    alone = intake.suggest(texts(other_work="I install software and clean out viruses."), enc, vec, snaps.onet)
    mixed = intake.suggest(texts(current_job="I drive a van delivering parcels around Shah Alam.",
                                 other_work="I install software and clean out viruses."), enc, vec, snaps.onet)
    assert alone["sentences"][0]["top"] == mixed["sentences"][1]["top"]


def base_request(**kw):
    req = {"weekly_hours": 8, "budget_rm": 200, "degree_level": "diploma", "degree_field": "Computer science"}
    req.update(kw)
    return req


def test_guarded_suggestion_needs_a_second_deliberate_action(snaps, ai):
    enc, vec = ai
    t = texts(other_work="I have never repaired a laptop.")
    result = intake.suggest(t, enc, vec, snaps.onet)
    assert all(s["needs_override"] for s in result["suggestions"])
    first = result["suggestions"][0]["dwa_id"]
    with pytest.raises(intake.IntakeError) as err:
        intake.build_profile(base_request(**t, confirmed=[{"dwa_id": first, "source": "suggested"}]), snaps.onet, result)
    assert "not done this yet" in err.value.errors["confirmed[0]"]
    profile = intake.build_profile(
        base_request(**t, confirmed=[{"dwa_id": first, "source": "suggested", "guard_override": True}]), snaps.onet, result)
    assert profile["activities"][0]["guard_override"] is True


def test_activity_also_said_plainly_does_not_need_the_override(snaps, ai):
    enc, vec = ai
    t = texts(other_work="I install software and clean out viruses. I have never installed software for a company.")
    result = intake.suggest(t, enc, vec, snaps.onet)
    s = next(s for s in result["suggestions"] if s["dwa_id"] == "4.A.3.b.1.c.1")
    assert {tr["guarded"] for tr in s["triggers"]} == {True, False}
    assert s["needs_override"] is False
    profile = intake.build_profile(base_request(**t, confirmed=[{"dwa_id": s["dwa_id"], "source": "suggested"}]),
                                   snaps.onet, result)
    assert profile["activities"][0]["lanes"] == ["other_work"]


def test_cannot_confirm_an_activity_that_was_not_suggested(snaps, ai):
    enc, vec = ai
    t = texts(other_work="I install software and clean out viruses.")
    result = intake.suggest(t, enc, vec, snaps.onet)
    with pytest.raises(intake.IntakeError) as err:
        intake.build_profile(base_request(**t, confirmed=[{"dwa_id": "4.A.3.a.4.a.14", "source": "suggested"}]),
                             snaps.onet, result)
    assert "was not suggested" in err.value.errors["confirmed[0]"]


def test_invalid_intake_is_blocked_field_by_field(snaps):
    with pytest.raises(intake.IntakeError) as err:
        intake.build_profile({"weekly_hours": 0, "budget_rm": -5, "degree_level": "phd"}, snaps.onet, None)
    assert set(err.value.errors) == {"weekly_hours", "budget_rm", "degree_level", "activities"}


def test_title_activities_form_the_current_job_lane_and_can_be_unticked(snaps):
    title = intake.title_activities(snaps.onet, "53-3033.00")
    assert len(title) > 5
    drop = title[0]["dwa_id"]
    profile = intake.build_profile(base_request(title_code="53-3033.00", unticked_title_activities=[drop]),
                                   snaps.onet, None)
    ids = {a["dwa_id"] for a in profile["activities"]}
    assert drop not in ids and len(ids) == len(title) - 1
    assert all(a["lanes"] == ["current_job"] and a["sources"] == ["title"] for a in profile["activities"])


def test_manual_search_adds_labelled_activities(snaps):
    hits = intake.search_activities(snaps.onet, "website")
    assert any(h["dwa_id"] == "4.A.3.b.1.f.4" for h in hits)
    profile = intake.build_profile(
        base_request(confirmed=[{"dwa_id": "4.A.3.b.1.f.4", "source": "added_by_you", "lane": "other_work"}]),
        snaps.onet, None)
    assert profile["activities"][0]["sources"] == ["added_by_you"]
    assert profile["without_ai_suggestions"] is True
    assert intake.search_occupations(snaps.onet, "light truck")[0]["code"] == "53-3033.00"


def test_network_is_blocked_during_tests():
    import urllib.request
    with pytest.raises(OSError):
        urllib.request.urlopen("https://www.onetcenter.org/", timeout=5)

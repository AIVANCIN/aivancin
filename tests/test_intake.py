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


@pytest.mark.parametrize("sentence, expected", [
    ("I built an IOC enricher.", ["IOC"]),
    ("I built a SIEM dashboard and triage IOCs.", ["SIEM", "IOC"]),
    ("I do KYC checks and R&D on the POS system.", ["KYC", "R&D", "POS"]),
    ("I handle the POS and EOD reports.", ["POS", "EOD"]),
    ("I give IT support and update CVs.", ["IT", "CV"]),
])
def test_short_forms_are_flagged_for_plain_words(sentence, expected):
    assert intake.short_forms(sentence) == expected


@pytest.mark.parametrize("sentence", [
    "I fix PCs for the HR team and check CCTV and GPS logs.",
    "I drive a van delivering parcels around Shah Alam.",
    "I DRIVE A VAN AND LOAD IT EVERY MORNING.",
    "I wrote a script in Python. A B C.",
])
def test_short_forms_leave_everyday_words_and_shouting_alone(sentence):
    assert intake.short_forms(sentence) == []


def test_suggest_reports_short_forms_per_sentence(snaps, ai):
    enc, vec = ai
    result = intake.suggest(texts(other_work="I built an IOC enricher. I fix laptops."), enc, vec, snaps.onet)
    assert [s["short_forms"] for s in result["sentences"]] == [["IOC"], []]


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
    # Each activity lists exactly the sentences that suggested it; Hafiz has activities suggested by several.
    for s in result["suggestions"]:
        assert sorted(t["sentence"] for t in s["triggers"]) == [
            i for i, x in enumerate(result["sentences"]) if s["dwa_id"] in x["top"]]
    assert any(len(s["triggers"]) >= 2 for s in result["suggestions"])
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


# Casual words that share no word with the activity a person means; targets chosen by reading the activity list.
CASUAL_SEARCHES = {
    "fix wifi": "4.A.3.b.1.d.1",  # Resolve computer network problems.
    "fix laptops": "4.A.3.b.5.b.3",  # Repair electronic equipment.
    "deal with angry customers": "4.A.4.a.8.c.5",  # Resolve customer complaints or problems.
    "I keep the books": "4.A.3.b.6.j.3",  # Maintain financial or account records.
}


def test_manual_search_ranks_casual_words_by_meaning(snaps, ai):
    encoder, index = ai
    for q, target in CASUAL_SEARCHES.items():
        assert intake.search_activities(snaps.onet, q) == []  # the keyword fallback finds nothing
        hits = intake.search_activities(snaps.onet, q, encoder=encoder, index=index)
        assert target in [h["dwa_id"] for h in hits], q
        assert all(h["match"] == "meaning" for h in hits)
        assert hits == intake.search_activities(snaps.onet, q, encoder=encoder, index=index)
    assert intake.search_activities(snaps.onet, "x", encoder=encoder, index=index) == []


def test_manual_search_keeps_name_matches_first(snaps, ai):
    encoder, index = ai
    keyword = {h["dwa_id"] for h in intake.search_activities(snaps.onet, "website")}
    hits = intake.search_activities(snaps.onet, "website", encoder=encoder, index=index)
    assert keyword and {h["dwa_id"] for h in hits[:len(keyword)]} == keyword
    assert all(h["match"] == "keyword" for h in hits[:len(keyword)])
    assert all(h["match"] == "meaning" for h in hits[len(keyword):])
    assert len(hits) == 10


def test_network_is_blocked_during_tests():
    import urllib.request
    with pytest.raises(OSError):
        urllib.request.urlopen("https://www.onetcenter.org/", timeout=5)


def test_activity_score_mixes_name_and_closest_task_statement():
    """Worked by hand for a query along the first axis, with w = NAME_WEIGHT:
    a0 name 1.0, best statement 1.0 -> 1.0; a1 name 0.0, statements 0.0 and 0.6 -> (1 - w) x 0.6;
    a2 name 0.6, statement 0.0 -> w x 0.6; a3 links to no statement -> its name, 0.8."""
    import numpy as np
    from aivancin.encoder import NAME_WEIGHT, ActivityIndex
    w = NAME_WEIGHT
    names = np.array([[1, 0], [0, 1], [0.6, 0.8], [0.8, 0.6]], dtype=np.float32)
    tasks = np.array([[0, 1], [1, 0], [0.6, 0.8]], dtype=np.float32)
    links = [["a1", "a2"], ["a0"], ["a1"]]
    idx = ActivityIndex(names, tasks, links, ["a0", "a1", "a2", "a3"])
    got = idx.scores(np.array([1, 0], dtype=np.float32))
    assert np.allclose(got, [1.0, (1 - w) * 0.6, w * 0.6, 0.8], atol=1e-6)


def test_plain_statements_reach_their_activities(snaps, ai):
    encoder, index = ai
    cases = {"I test new versions of our ticketing app before they go live and report the bugs.": "4.A.1.b.2.h.3",
             "I built a website for my uncle's hardware shop and I keep it updated.": "4.A.2.b.2.b.8"}
    for text, dwa in cases.items():
        top = intake.suggest({"other_work": text}, encoder, index, snaps.onet)["sentences"][0]["top"]
        assert dwa in top, (text, [snaps.onet.dwa_names[d] for d in top])


@pytest.mark.parametrize("text,kind,cue", [
    ("My brother sells phone cases online.", "someone else", "My brother"),
    ("My wife teaches piano to children.", "someone else", "My wife"),
    ("She manages the warehouse stock.", "someone else", "She"),
    ("They clean the offices at night.", "someone else", "They"),
    ("My dream is to work in a bank.", "aspirational", "My dream is"),
    ("My goal is to become an auditor.", "aspirational", "My goal is to"),
    ("Someday I will study accounting.", "aspirational", "Someday"),
    ("I'm thinking of learning Python.", "aspirational", "I'm thinking of"),
])
def test_guard_catches_someone_elses_work_and_plans_without_a_listed_cue(text, kind, cue):
    """SPEC 11.7: third-person subjects at sentence start and plans phrased without the first list's cues."""
    assert intake.guard(text) == {"kind": kind, "cue": cue}


@pytest.mark.parametrize("text", [
    "My sister and I run a small bakery.",          # a shared subject
    "My uncle's shop pays me to fix its tills.",    # a possessive, and the person is paid
    "She asks me to check her shop's accounts.",    # the person does the work (refined 21 Oct, C12)
    "He taught me the basics, and I now repair bikes myself.",
    "My manager lets me plan the delivery routes.",  # not a listed relative
    "I fix my cousin's laptop when it breaks.",
])
def test_guard_leaves_own_work_alone(text):
    assert intake.guard(text) is None

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
    # IT is not flagged since 22 Oct: the encoder's copy spells it out (SPEC 11.12)
    ("I give IT support and update CVs.", ["CV"]),
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


@pytest.mark.parametrize("text,kind,cue", [
    ("Next year I will start studying graphic design.", "aspirational", "Next year"),
    ("I'm starting a course next month.", "aspirational", "next month"),
    ("I'm going to apply for data analyst jobs after Raya.", "aspirational", "I'm going to"),
    ("Once I finish my diploma, I'm going to look for an IT job.", "aspirational", "I'm going to"),
    ("I will be starting a part-time diploma in January.", "aspirational", "I will be starting"),
    ("I failed my driving test twice, so I take the bus to work.", "did not work out", "I failed"),
    ("I tried to learn Excel macros but I gave up after a week.", "did not work out", "gave up"),
    ("I applied for a bank teller job and was rejected.", "did not work out", "was rejected"),
    ("At weekends I play badminton and watch films.", "free time", "play badminton"),
    ("In the evenings I watch Korean dramas with my sister.", "free time", "watch Korean dramas"),
    ("I enjoy reading mystery novels before bed.", "free time", "reading mystery novels"),
    ("I go hiking at Bukit Gasing most weekends.", "free time", "go hiking"),
])
def test_guard_catches_future_plans_failed_attempts_and_free_time(text, kind, cue):
    """A6 sets 3 and 4 (21 Oct): plans phrased as a dated or "going to" future, attempts that did not work out, and
    free time."""
    assert intake.guard(text) == {"kind": kind, "cue": cue}


@pytest.mark.parametrize("text", [
    "I will usually reconcile the cash at closing time.",            # a habit, not a plan
    "I'll answer customer messages on WhatsApp during my lunch break.",
    "Customers know I will fix their phones the same day.",
    "I write a monthly report on what I'm going to buy for the shop.",
    "At the end of the year I put together the budget for the next year.",
    "I guess next month's sales from the last few months to plan orders.",
    "When a laptop fails, I back up the files and reinstall Windows.",
    "I gave up my weekends to build the booking system for my father's clinic.",
    "I play the keyboard at weddings and get paid for each event.",
    "I watch the CCTV screens and report anything unusual.",
    "I coach the under-12 badminton team every Saturday.",
    "I read the supplier contracts and check the prices before we sign.",
])
def test_guard_leaves_own_work_with_the_same_words_alone(text):
    assert intake.guard(text) is None


# ---- ownership and self-introductions (22 Oct) ----------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "My friend repairs laptops for me.", "I watch him install software.", "I get my car serviced at a workshop.",
    "I pay someone to file my taxes.", "Our neighbour designs posters for me.", "My father teaches me to repair engines.",
    "My wife handles the online orders and I watch.", "I see my manager prepare the weekly sales report."])
def test_work_someone_else_does_is_not_counted(text):
    assert intake.guard(text)["kind"] == "someone else"


@pytest.mark.parametrize("text", [
    "I repair laptops for my friends.", "I install software while he checks the settings.", "He and I fix bikes on Saturdays.",
    "My boss checks the bank reconciliation that I prepare.", "My manager lets me run the stock count.",
    "They pay me to set up their home wifi.", "I watch the CCTV screens during the night shift.",
    "I look at the numbers to spot which stall is losing money.", "I help my brother with the accounts.",
    "I pay the suppliers every Friday."])
def test_own_work_with_similar_words_is_counted(text):
    assert intake.guard(text) is None


def test_help_from_someone_else_asks_who_did_the_work():
    assert intake.guard("My brother helps me with the accounts.")["kind"] == "unclear who"


@pytest.mark.parametrize("text, kept", [
    ("My name is May and I keep the books for our family shop.", "I keep the books for our family shop."),
    ("Hello, my name is Nurul Ain, I drive a van.", "I drive a van."),
    ("I'm Will, and I fix laptops for my neighbours.", "I fix laptops for my neighbours."),
    ("Bill here, I install software.", "I install software."),
    ("Hi, I am Grace.", ""),
    ("I'm IT support for my church.", "I'm IT support for my church."),
    ("I am responsible for the stock.", "I am responsible for the stock.")])
def test_self_introductions_are_not_read(text, kept):
    assert intake.strip_intro(text) == kept


def test_a_name_that_is_also_a_word_does_not_change_suggestions(snaps, ai):
    encoder, vectors = ai
    tops = {}
    for name in ("Siti", "May", "Will", "Grace", "Wei Ling"):
        res = intake.suggest({"other_work": f"Hi, I am {name}. My name is {name} and I keep the books for our shop."},
                             encoder, vectors, snaps.onet)
        assert res["sentences"][0]["intro"] is True and res["sentences"][0]["top"] == []
        tops[name] = [s["top"] for s in res["sentences"]]
    assert len({str(v) for v in tops.values()}) == 1


@pytest.mark.parametrize("text, copy", [
    ("I do the bookkeeping for our stall.", "I do the financial record keeping for our stall."),
    ("Book-keeping for two shops.", "financial record keeping for two shops."),
    ("I work as a bookkeeper.", "I work as a financial record keeper."),
    ("I keep the books for my mum.", "I keep the financial records for my mum."),
    ("She did her books every month.", "She did her financial records every month."),
    ("I give IT support at my church.", "I give information technology support at my church."),
    ("I studied I.T. at college.", "I studied information technology at college."),
    ("I look after their cyber-security.", "I look after their computer and information security."),
    # left alone: physical books, lower-case "it", a sentence in capitals
    ("I keep the books tidy on the shelves.", "I keep the books tidy on the shelves."),
    ("I put the books back on the shelves.", "I put the books back on the shelves."),
    ("I read books and it is fun.", "I read books and it is fun."),
    ("IT WAS A LONG DAY.", "IT WAS A LONG DAY."),
])
def test_misread_work_terms_are_written_in_onet_words(text, copy):
    assert intake.work_terms(text) == copy


def test_bookkeeping_suggests_financial_records_and_the_person_keeps_their_words(snaps, ai):
    encoder, vectors = ai
    for text in ("I do the bookkeeping for my sister's stall.", "I keep the books for our family shop."):
        res = intake.suggest({"other_work": text}, encoder, vectors, snaps.onet)
        s = res["sentences"][0]
        assert s["text"] == text
        assert "4.A.3.b.6.j.3" in s["top"]  # Maintain financial or account records.
        assert "4.A.3.a.1.c.18" not in s["top"]  # Perform housekeeping duties.

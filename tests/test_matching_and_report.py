"""Matching, requirement labels, AI evidence, scenarios, plan and provenance (SPEC sections 3 to 7)."""
import csv
import datetime as dt
import json
import math
import re
from collections import defaultdict

import pytest

from aivancin import aei, intake, pathway
from aivancin.matching import Matcher
from aivancin.report import Reporter, ReportError

from .conftest import DATA

# Activities a careful Hafiz ticks from his own weekend-work sentences (all are suggested for that text).
HAFIZ_SIDE_WORK = ["4.A.3.b.1.d.1", "4.A.3.b.1.d.3", "4.A.3.b.1.c.1", "4.A.3.b.1.b.2", "4.A.3.b.1.f.4", "4.A.2.b.2.b.8"]
PRIYA_SIDE_WORK = ["4.A.4.c.1.c.6", "4.A.3.b.6.a.1", "4.A.4.c.3.d.1", "4.A.2.a.3.c.2", "4.A.3.b.6.j.3"]
# S2-K3 relevance rule for Hafiz, fixed before that probe ran.
HAFIZ_RELEVANT = re.compile(r"computer|network|software|web|database|information|systems", re.I)


@pytest.fixture(scope="module")
def matcher(snaps):
    return Matcher(snaps)


@pytest.fixture(scope="module")
def reporter(snaps, matcher):
    return Reporter(snaps, matcher)


def profile_for(snaps, ai, persona, picks, **extra):
    encoder, vectors = ai
    req = {**persona, "confirmed": [{"dwa_id": d, "source": "suggested"} for d in picks], **extra}
    sug = intake.suggest(req, encoder, vectors, snaps.onet)
    return intake.build_profile(req, snaps.onet, sug, snaps.cip)


def hafiz_relevant(code, title):
    return code.startswith(("15-1", "49-2011")) or bool(HAFIZ_RELEVANT.search(title))


def test_weights_match_an_independent_count_from_the_csv_files(matcher):
    types = {}
    with open(DATA / "onet/tasks.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            types[(r["code"], r["task_id"])] = r["task_type"]
    expected = defaultdict(float)
    with open(DATA / "onet/task_dwas.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["code"] == "15-1254.00":
                expected[r["dwa_id"]] += 0.5 if types.get((r["code"], r["task_id"])) == "Supplemental" else 1.0
    assert matcher.w["15-1254.00"] == dict(expected)


def test_candidates_follow_the_job_zone_and_exclusion_rules(snaps, matcher):
    pool = matcher.candidates(exclude="15-1254.00")
    assert pool and "15-1254.00" not in pool
    assert all(snaps.onet.job_zones[c] in (3, 4) for c in pool)
    assert not any(c.startswith(("11-", "55-")) for c in pool)


def test_hafiz_contrast_title_only_versus_confirmed_side_work(snaps, ai, personas, matcher):
    """A5: the job title alone gives no relevant roles; the confirmed weekend work gives at least 6 of 10."""
    hafiz = personas["hafiz"]
    title_only = profile_for(snaps, ai, hafiz, [])
    prof = matcher.lane_profile(title_only["activities"], "all")
    top = matcher.rank(prof, "all", current_code=hafiz["title_code"], limit=10)["roles"]
    assert sum(hafiz_relevant(r["code"], snaps.onet.occupations[r["code"]]) for r in top) == 0
    with_side = profile_for(snaps, ai, hafiz, HAFIZ_SIDE_WORK)
    for lane in ("all", "other_work"):
        prof = matcher.lane_profile(with_side["activities"], lane)
        top = matcher.rank(prof, lane, current_code=hafiz["title_code"], limit=10)["roles"]
        assert sum(hafiz_relevant(r["code"], snaps.onet.occupations[r["code"]]) for r in top) >= 6, lane


def test_overlap_is_weighted_share_of_recorded_activity_links(matcher):
    prof = set(HAFIZ_SIDE_WORK)
    w = matcher.w["15-1254.00"]
    assert matcher.overlap("15-1254.00", prof) == pytest.approx(sum(v for d, v in w.items() if d in prof) / sum(w.values()))


@pytest.fixture(scope="module")
def hafiz_profile(snaps, ai, personas):
    return profile_for(snaps, ai, personas["hafiz"], HAFIZ_SIDE_WORK, cip_code="11.0701")


def test_report_default_order_is_the_overlap_order_and_repeats_exactly(reporter, hafiz_profile):
    a = reporter.report(hafiz_profile, {"lane": "other_work"}, today=dt.date(2026, 10, 21))
    b = reporter.report(hafiz_profile, {"lane": "other_work"}, today=dt.date(2026, 10, 21))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)  # A8
    overlaps = [r["overlap"]["v"] for r in a["roles"]]
    assert overlaps == sorted(overlaps, reverse=True)  # A11: lambda 0 keeps the overlap order
    assert 3 <= len(a["roles"]) <= 6
    assert a["heading"] == "closest roles to explore"
    assert all(r["rank"] == r["rank_without_weights"] for r in a["roles"])


def test_role_counts_and_summary_use_activity_overlap_wording(reporter, hafiz_profile):
    rep = reporter.report(hafiz_profile, {"lane": "other_work"})
    r = rep["roles"][0]
    assert r["done_count"]["v"] + len(r["missing"]) == r["total_count"]["v"]
    assert r["summary"].startswith(f"You do {r['done_count']['v']} of its {r['total_count']['v']} recorded activities")
    text = json.dumps(rep).lower()
    for banned in ("of this job you can do", "job loss", "risk", "safe"):
        assert banned not in text


def test_requirement_labels_agree_with_job_zone_and_crosswalk(snaps, reporter, hafiz_profile):
    rep = reporter.report(hafiz_profile, {"lane": "other_work"})
    cip = snaps.cip["11.0701"]
    for r in rep["roles"]:
        ids = {lab["id"] for lab in r["labels"]}
        assert ("needs_degree" in ids) == (snaps.onet.job_zones[r["code"]] == 4)  # Hafiz holds a diploma
        assert ("fits_studies" in ids) == (r["code"].split(".")[0] in cip["soc"])
        rw = snaps.onet.experience.get(r["code"])
        expected_exp = r["title"].startswith("First-Line Supervisors") or bool(rw and rw[0] >= 6)
        assert ("needs_experience" in ids) == expected_exp


def test_studies_lane_restricts_to_the_degree_crosswalk(snaps, reporter, hafiz_profile):
    rep = reporter.report(hafiz_profile, {"lane": "studies"})
    socs = snaps.cip["11.0701"]["soc"]
    assert rep["roles"] and all(r["code"].split(".")[0] in socs for r in rep["roles"])


def figures(node, path="$"):
    if isinstance(node, dict):
        if "v" in node and "src" in node:
            yield path, node
        for k, v in node.items():
            yield from figures(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from figures(v, f"{path}[{i}]")


def test_every_figure_has_a_known_source(snaps, reporter, hafiz_profile):
    """A9 at the API level: every displayed number names a source in the manifest (or the user's input)."""
    known = {r["source_id"] for r in snaps.manifest["snapshots"]} | {"your-input"}
    rep = reporter.report(hafiz_profile, {"lane": "all", "lambda": 0.3, "mu": 0.5, "scenario": "faster"})
    figs = list(figures(rep))
    assert len(figs) > 50
    for path, f in figs:
        assert f["src"], path
        assert set(f["src"]) <= known, (path, f["src"])


def test_ai_weight_and_decent_work_priority_can_reorder_and_say_so(reporter, hafiz_profile):
    plain = reporter.report(hafiz_profile, {"lane": "all"})
    weighted = reporter.report(hafiz_profile, {"lane": "all", "lambda": 0.5, "mu": 1.0})
    assert weighted["weights"]["lambda"]["v"] == 0.5
    if [r["code"] for r in plain["roles"]] != [r["code"] for r in weighted["roles"]]:
        assert any(r["rank"] != r["rank_without_weights"] for r in weighted["roles"])


def test_invalid_options_are_rejected_with_field_messages(reporter, hafiz_profile):
    with pytest.raises(ReportError) as e:
        reporter.report(hafiz_profile, {"lane": "nowhere", "lambda": 2, "mu": -1, "scenario": "x", "release": "1999"})
    assert set(e.value.errors) == {"lane", "lambda", "mu", "scenario", "release"}


def test_plan_shows_only_verified_entries_within_budget(reporter, hafiz_profile, snaps):
    rep = reporter.report(hafiz_profile, {"lane": "other_work"}, today=dt.date(2026, 10, 21))
    by_id = {c["id"]: c for c in snaps.catalogue["courses"]}
    for role in rep["roles"]:
        spent = 0
        for step in role["plan"]["steps"] + role["plan"]["ai_steps"]:
            if step["course"] is None:
                assert step["status"].startswith("no verified resource")
            else:
                c = by_id[step["course"]["id"]]
                assert c["link_check"]["ok"]
                if step["status"] == "verified" and step["kind"] != "direct_ai":
                    spent += c["cost_rm"]
        assert spent <= hafiz_profile["budget_rm"]


def test_expired_link_checks_show_no_verified_resource(reporter, hafiz_profile):
    """A10: once the catalogue's validity window has passed, nothing displays as verified."""
    rep = reporter.report(hafiz_profile, {"lane": "other_work"}, today=dt.date(2027, 6, 1))
    steps = [s for r in rep["roles"] for s in r["plan"]["steps"] + r["plan"]["ai_steps"]]
    assert steps and all(s["course"] is None and s["status"].startswith("no verified resource") for s in steps)


def test_failed_link_check_is_never_shown_as_verified(snaps):
    failed = [c for c in snaps.catalogue["courses"] if not c["link_check"]["ok"]]
    shown = {c["id"] for c in pathway.verified_courses(snaps.catalogue, dt.date(2026, 10, 21))}
    assert failed and not shown & {c["id"] for c in failed}


def test_scenario_steps_follow_the_threshold(reporter, hafiz_profile):
    """A12: steps are added only for gap activities that reach the displayed threshold."""
    for threshold in (100, 1):
        for scenario in ("moderate", "faster"):
            rep = reporter.report(hafiz_profile, {"lane": "other_work", "threshold": threshold, "scenario": scenario})
            for role in rep["roles"]:
                crossing = {c["dwa_id"] for c in role["scenario_checks"] if c["crosses"][scenario]}
                added = {s["gap"]["dwa_id"] for s in role["plan"]["ai_steps"]}
                assert added == crossing
                for c in role["scenario_checks"]:
                    for t in c["tasks"]:
                        assert t["faster"] >= t["moderate"] == t["a0"]
                    assert c["crosses"][scenario] == any(t[scenario] >= threshold for t in c["tasks"])
            for role in rep["roles"]:
                differs = [c for c in role["scenario_checks"] if c["crosses"]["moderate"] != c["crosses"]["faster"]]
                assert role["scenario_notes"][0].startswith("No change needed") == (not differs)


def test_faster_projection_formula():
    assert pathway.projected(40.0, "faster", 8) == pytest.approx(40.0 * 1.444)
    assert pathway.projected(90.0, "faster", 24) == 100.0
    assert pathway.projected(40.0, "moderate", 24) == 40.0


def test_replay_reports_deltas_and_no_position_change_at_zero_weight(reporter, hafiz_profile, snaps):
    ids = snaps.evidence.ids
    assert ids == aei.RELEASE_IDS
    rep = reporter.report(hafiz_profile, {"lane": "other_work", "release": ids[-1]})
    assert rep["replay"]["previous"] == ids[-2]
    assert rep["replay"]["position_changes"] is False
    assert rep["replay"]["message"].startswith("No position changes")
    assert rep["replay"]["comparability_note"]  # the June 2026 format boundary
    first = reporter.report(hafiz_profile, {"lane": "other_work", "release": ids[0]})
    assert first["replay"]["previous"] is None


def test_wilson_interval_matches_the_textbook_formula():
    k, n, z = 30, 100, 1.96
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    assert aei.wilson(k, n) == [round(100 * (centre - half), 1), round(100 * (centre + half), 1)]
    assert aei.wilson(0, 0) is None


def test_task_evidence_pools_releases_and_flags_weak_cells():
    t1 = {"7": {"share": 40.0, "classified": 50, "automation": 20}, "8": {"share": 60.0, "classified": 10, "automation": 6}}
    t2 = {"7": {"share": 50.0, "classified": None, "automation": None}}
    ev = aei.Evidence([("2025-09-15", t1, {}), ("2026-06-26", t2, {})])
    seven = ev.task("7")
    assert seven["share"] == 45.0 and seven["pooled_classified"] == 50 and not seven["weak"]
    assert ev.task("8")["weak"]  # one release, ten conversations
    assert ev.task("7", "2025-09-15")["share"] == 40.0
    assert ev.task("9") is None


def test_ai_evidence_wording_never_mentions_risk(reporter, hafiz_profile):
    rep = reporter.report(hafiz_profile, {"lane": "other_work", "lambda": 0.2})
    assert any(r["ai"] for r in rep["roles"])
    for r in rep["roles"]:
        if r["ai"]:
            assert 0 <= r["ai"]["share"]["v"] <= 100
            assert all(src.startswith("aei-") for src in r["ai"]["share"]["src"])


def test_priya_side_work_reaches_accounting_roles(snaps, ai, personas, reporter):
    prof = profile_for(snaps, ai, personas["priya"], PRIYA_SIDE_WORK, cip_code="52.0301")
    rep = reporter.report(prof, {"lane": "other_work"})
    titles = [r["title"] for r in rep["roles"]]
    assert "Accountants and Auditors" in titles
    assert rep["current"]["masco"]["major"] == "8"  # taxi driver: plant and machine operators

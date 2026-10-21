"""The catalogue builder's Saylor adapter, on fixture pages (no network)."""
import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_catalogue", ROOT / "tools" / "build_catalogue.py")
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)

COURSE = """<html><head><title>Course: PRDV004: Spreadsheets | Saylor University</title></head><body>
<div>Time: 5 hours</div><div>CEUs: 0.5</div><div>Free Certificate</div>
<a href="https://learn.saylor.org/mod/page/view.php?id=90764"><span>Course Syllabus</span></a></body></html>"""
SYLLABUS = """<h4>Course Learning Outcomes</h4><p>Upon successful completion of this course, you will be able to:</p>
<ul><li><span><strong class="mr-2"> 0a </strong> Describe the basic components of a spreadsheet;</span></li>
<li>Perform basic calculations; and</li><li>Use spreadsheets for budgets and invoices.</li></ul>
<p>Throughout this course, you will also see learning outcomes in each unit.</p>"""


def serve(pages):
    def get(url, timeout=60):
        if url not in pages:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return 200, pages[url].encode("utf-8")
    return get


def test_saylor_page_gives_stated_hours_title_and_clean_outcomes(monkeypatch):
    monkeypatch.setattr(bc, "get", serve({bc.SAYLOR + "prdv004": COURSE,
                                          "https://learn.saylor.org/mod/page/view.php?id=90764": SYLLABUS}))
    url, title, hours, objectives, check = bc.saylor_facts("prdv004")
    assert url == "https://learn.saylor.org/course/prdv004"
    assert title == "PRDV004: Spreadsheets" and hours == 5.0
    assert objectives == ["Describe the basic components of a spreadsheet", "Perform basic calculations",
                          "Use spreadsheets for budgets and invoices"]
    assert check["ok"] is True and check["status"] == 200


def test_saylor_page_without_stated_time_is_recorded_as_failing(monkeypatch):
    monkeypatch.setattr(bc, "get", serve({bc.SAYLOR + "prdv004": COURSE.replace("Time: 5 hours", ""),
                                          "https://learn.saylor.org/mod/page/view.php?id=90764": SYLLABUS}))
    _, _, hours, _, check = bc.saylor_facts("prdv004")
    assert hours is None and check["ok"] is False


def test_saylor_course_that_stopped_being_free_fails_its_check(monkeypatch):
    monkeypatch.setattr(bc, "get", serve({bc.SAYLOR + "prdv004": COURSE.replace("Free Certificate", "Paid certificate"),
                                          "https://learn.saylor.org/mod/page/view.php?id=90764": SYLLABUS}))
    assert bc.saylor_facts("prdv004")[4]["ok"] is False


def test_missing_saylor_page_is_kept_as_unverified_not_dropped(monkeypatch):
    monkeypatch.setattr(bc, "get", serve({}))
    url, title, hours, objectives, check = bc.saylor_facts("prdv999")
    assert check == {"ok": False, "status": 404, "detail": "HTTPError"} and title is None and objectives == []


def test_bundled_saylor_entries_carry_provider_facts_and_access_note():
    cat = json.loads((ROOT / "data" / "catalogue" / "courses.json").read_text(encoding="utf-8"))
    saylor = [c for c in cat["courses"] if c["provider"] == "Saylor University"]
    assert saylor
    for c in saylor:
        assert c["hours"] > 0 and c["objectives"] and c["access"] and c["cost_rm"] == 0
        if c["kind"] == "course unit":  # SPEC 11.7: one unit of a course, with the unit's stated time
            code, unit = c["id"].split(".")[1:]
            assert "stated for this unit on the provider's course page" in c["hours_source"]
            assert c["url"].startswith(bc.SAYLOR + code + "#section-") and c["title"].startswith(code.upper() + " Unit " + unit[1:])
        else:
            assert "stated on the provider's course page" in c["hours_source"]
            assert c["url"] == bc.SAYLOR + c["id"].split(".", 1)[1]
    ids = {c["id"] for c in cat["courses"]}
    assert all(r in ids for c in cat["courses"] for r in c.get("requires", []))


@pytest.mark.parametrize("hours,expected", [(0.4, 0.5), (5.0, 2.5), (63.0, 31.5)])
def test_practice_estimate_rule(hours, expected):
    assert bc.practice_hours(hours) == expected


UNIT_COURSE = """<html><body><div>Time: 33 hours</div><div>Free Certificate</div>
<li id="section-13" class="section"><a aria-label="2.4: Other">x</a> Completing this unit should take you approximately 9 hours.</li>
<li id="section-14" class="section"><a role="button" aria-label="Unit 3: Time Value of Money">x</a>
<h3>Unit 3: Time Value of Money</h3><p>The time value of money is a core principle of finance.
Completing this unit should take you approximately 3 hours.</p>
<a href="https://learn.saylor.org/mod/page/view.php?id=90257"><span>Unit 3 Learning Outcomes</span></a></li>
<li id="section-15" class="section"><a aria-label="3.1: Next">x</a></li></body></html>"""
UNIT_OUTCOMES = """<head><title>Unit 3 Learning Outcomes</title><script>var t = "Learning Outcomes";</script></head>
<div role="main"><p>Upon successful completion of this unit, you will be able to:</p><p>[1] explain the time value of money;
[2] calculate present values, future values, and payments</p><a>Back to 'Unit 3: Time Value of Money'</a></div>"""


def test_saylor_unit_gives_its_own_time_title_and_outcomes(monkeypatch):
    """SPEC 11.7: a unit's stated time, title and outcomes come from its own section, not the whole course."""
    monkeypatch.setattr(bc, "get", serve({bc.SAYLOR + "bus202": UNIT_COURSE,
                                          "https://learn.saylor.org/mod/page/view.php?id=90257": UNIT_OUTCOMES}))
    bc._course_pages.clear()
    url, title, hours, objectives, check = bc.saylor_unit_facts("bus202", 14, "Unit 3")
    assert url == bc.SAYLOR + "bus202#section-14" and title == "Unit 3: Time Value of Money" and hours == 3.0
    assert objectives == ["explain the time value of money", "calculate present values, future values, and payments"]
    assert check["ok"] is True


def test_saylor_unit_that_moved_fails_its_check(monkeypatch):
    """The curated section now holds another unit (or none): the entry is kept but unverified."""
    monkeypatch.setattr(bc, "get", serve({bc.SAYLOR + "bus202": UNIT_COURSE,
                                          "https://learn.saylor.org/mod/page/view.php?id=90257": UNIT_OUTCOMES}))
    bc._course_pages.clear()
    assert bc.saylor_unit_facts("bus202", 14, "Unit 4")[4]["ok"] is False
    assert bc.saylor_unit_facts("bus202", 13, "Unit 3")[4]["ok"] is False

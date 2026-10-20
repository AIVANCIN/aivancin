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
        assert c["hours"] > 0 and "stated on the provider's course page" in c["hours_source"]
        assert c["objectives"] and c["access"] and c["cost_rm"] == 0
        assert c["url"] == bc.SAYLOR + c["id"].split(".", 1)[1]
    ids = {c["id"] for c in cat["courses"]}
    assert all(r in ids for c in cat["courses"] for r in c.get("requires", []))


@pytest.mark.parametrize("hours,expected", [(0.4, 0.5), (5.0, 2.5), (63.0, 31.5)])
def test_practice_estimate_rule(hours, expected):
    assert bc.practice_hours(hours) == expected

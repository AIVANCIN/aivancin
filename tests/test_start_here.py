"""Start here (22 Oct): the plan names the next session exactly, from the provider's own unit list: the unit to open,
what it needs, the time and the output to keep. Expected units are read from the catalogue file's unit lists here."""
import pytest

from aivancin.matching import Matcher
from aivancin.report import Reporter
from aivancin.routes import Planner, first_task

from .test_course_access import _courses, _profile
from .test_routes import TODAY, profile

WEB_DEV, VSCODE, LEDGER = "15-1254.00", "learn-windows.introduction-to-web-development", "learn.wwl.explore-general-ledger-dynamics-365"


@pytest.fixture(scope="module")
def planner(snaps):
    m = Matcher(snaps)
    return Planner(snaps, m, Reporter(snaps, m))


def test_every_route_entry_says_what_its_checkpoint_needs(snaps):
    for c in snaps.catalogue["courses"]:
        if c.get("checkpoint"):
            assert c.get("setup"), c["id"]
        if c["provider"] == "Microsoft Learn":
            assert c["units"] and all(u["title"] and u["url"].startswith("https://learn.microsoft.com/") for u in c["units"]), c["id"]


def test_first_task_is_the_first_exercise_or_lesson(snaps):
    c = _courses(snaps)
    units = c[VSCODE]["units"]
    exercises = [u for u in units if u["title"].startswith("Exercise")]
    t = first_task(c[VSCODE])
    assert t["kind"] == "exercise" and t["title"] == exercises[0]["title"].split(" - ", 1)[1]
    assert t["minutes"]["v"] == exercises[0]["minutes"] and t["url"] == exercises[0]["url"]
    assert t["exercises"] == len(exercises) and t["exercise_minutes"]["v"] == sum(u["minutes"] for u in exercises)
    # no exercises: the first lesson after the introduction
    ledger = c[LEDGER]["units"]
    assert ledger[0]["title"] == "Introduction" and not any(u["title"].startswith("Exercise") for u in ledger)
    t = first_task(c[LEDGER])
    assert (t["kind"], t["title"], t["url"]) == ("lesson", ledger[1]["title"], ledger[1]["url"])
    # a learning path starts with its first module; a lesson whose title only begins with "Introduction" is a lesson
    path = c["learn-dynamics.create-financial-reports-dynamics-365-business-central"]
    assert first_task(path)["kind"] == "module" and first_task(path)["title"] == path["units"][0]["title"]
    rec = c["learn-dynamics.bank-reconciliation-dynamics-365-business-central"]
    assert first_task(rec)["title"] == rec["units"][0]["title"] == "Introduction to and completing the reconciliation process"
    # a course without a unit list: its focus units, or the course itself
    assert first_task(c["saylor.bus105"])["kind"] == "focus" and first_task(c["saylor.bus202.u3"])["kind"] == "entry"


def test_start_is_the_first_open_step_and_moves_when_it_is_done(snaps, planner):
    prof = _profile(snaps, ["4.A.3.b.1.a.3", "4.A.2.b.2.b.8", "4.A.3.b.1.c.1"], 8.0)
    b = next(r for r in planner.routes(prof, {"target": WEB_DEV}, TODAY)["routes"] if r["code"] == WEB_DEV)["block"]
    assert b["start"] == b["steps"][0]["id"] and b["weekly_hours"]["v"] == 8.0 and b["weekly_hours"]["src"]
    s = b["steps"][0]
    assert s["output"] == _courses(snaps)[s["id"]]["checkpoint"] and s["setup"] and s["first_task"]["url"]
    b2 = next(r for r in planner.routes(prof, {"target": WEB_DEV, "completed": [b["start"]]}, TODAY)["routes"]
              if r["code"] == WEB_DEV)["block"]
    assert b2["start"] != b["start"] and b2["start"] == next(x["id"] for x in b2["steps"] if not x["completed"])


DEMO_TICKS = ["4.A.3.b.1.b.2", "4.A.3.b.1.a.3", "4.A.3.b.1.c.1", "4.A.3.b.1.f.4", "4.A.3.b.1.d.1"]  # the demo's five ticks


def test_an_entry_reaching_more_of_the_largest_gaps_comes_first(snaps, ai, personas, planner):
    """Hafiz as in the demo: Web Developers' two largest gaps weigh the same; the hands-on web module serves both, the
    shorter modules one each, so it now opens the block (22 Oct; before, the cheaper and shorter entry and then the
    identifier decided)."""
    hafiz = profile(snaps, ai, personas["hafiz"], DEMO_TICKS)
    r = next(x for x in planner.routes(hafiz, {"target": WEB_DEV}, TODAY)["routes"] if x["code"] == WEB_DEV)
    top = r["prioritized"][:2]
    assert top[0]["weight"]["v"] == top[1]["weight"]["v"]
    assert {g["dwa_id"] for g in top} <= set(_courses(snaps)[VSCODE]["dwas"])
    assert r["block"]["steps"][0]["id"] == VSCODE

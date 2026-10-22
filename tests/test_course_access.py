"""Course prerequisites, licensed practice and finance blocks (22 Oct): dependent material comes after what it
needs, a paid product's exercises are disclosed with a free checkpoint, and gaps no verified course teaches stay
visible instead of being filled by loosely related courses."""
import pytest

from aivancin import pathway
from aivancin.matching import Matcher
from aivancin.report import Reporter
from aivancin.routes import Planner, expects

from .test_routes import TODAY


@pytest.fixture(scope="module")
def planner(snaps):
    m = Matcher(snaps)
    return Planner(snaps, m, Reporter(snaps, m))


def _profile(snaps, ids, hours=6.0):
    return {"title_code": None, "degree_field": "", "cip_code": None, "degree_level": "degree", "weekly_hours": hours,
            "budget_rm": 0.0, "without_ai_suggestions": False,
            "activities": [{"dwa_id": d, "name": snaps.onet.dwa_names[d], "sources": ["added_by_you"],
                            "lanes": ["other_work"], "guard_override": False} for d in ids]}


def _courses(snaps):
    return {c["id"]: c for c in snaps.catalogue["courses"]}


def test_python_testing_needs_python_first(snaps, planner):
    """The pytest module's provider prerequisite is the ability to write Python scripts."""
    c = _courses(snaps)
    assert c["learn.test-python-with-pytest"]["requires"] == ["learn.wwl.get-started-with-python-fundamentals"]
    assert "Python scripts" in c["learn.test-python-with-pytest"]["provider_prerequisites"]
    seen = 0
    for code in ("15-1252.00", "15-1253.00", "15-1251.00"):
        for weeks in (4, 8, 12):
            out = planner.routes(_profile(snaps, ["4.A.3.b.1.a.3", "4.A.2.b.2.b.8"], 8.0), {"target": code, "weeks": weeks}, TODAY)
            steps = [s["id"] for s in next(r for r in out["routes"] if r["code"] == code)["block"]["steps"]]
            for i, sid in enumerate(steps):
                for need in c[sid].get("requires", []):
                    assert need in steps[:i], (code, weeks, sid, need)
            seen += "learn.test-python-with-pytest" in steps
    assert seen  # the dependent step does occur, after its prerequisite


def test_licensed_practice_is_disclosed_and_the_checkpoint_is_free(snaps):
    licensed = [x for x in snaps.catalogue["courses"] if x.get("practice_product")]
    assert {x["practice_product"] for x in licensed} >= {"Dynamics 365", "Microsoft 365 Copilot"}
    for x in licensed:
        assert x["cost_rm"] == 0 and x["access"].startswith("Free to read")
        assert x["practice_product"] in x["access"]
        if x.get("checkpoint"):
            assert x["practice_product"].split()[0] not in x["checkpoint"]  # the checkpoint never needs the product
    # every Dynamics module in the catalogue is marked
    assert all(x.get("practice_product") for x in snaps.catalogue["courses"] if "dynamics-365" in x["url"] or "dynamics" in x["id"])


def test_steps_and_learn_options_carry_access_and_what_the_provider_expects(snaps, planner):
    out = planner.routes(_profile(snaps, ["4.A.3.b.6.a.1", "4.A.2.a.3.c.2", "4.A.4.c.1.c.6"]), {"target": "13-2082.00"}, TODAY)
    tax = next(r for r in out["routes"] if r["code"] == "13-2082.00")
    steps = {s["id"]: s for s in tax["block"]["steps"]}
    dyn = steps["learn-dynamics.work-taxes-finance"]
    assert dyn["practice_product"] == "Dynamics 365" and dyn["provider_exercise"] is True
    assert "Dynamics 365 Finance" in dyn["expects"] and " min" not in dyn["expects"]
    opts = [o for g in tax["resources"] for o in g["options"]]
    assert all("expects" in o and "practice_product" in o for o in opts)
    assert expects({"provider_prerequisites": "None stated by the provider"}) == ""


def test_tax_block_teaches_tax_set_up_before_tax_work_and_names_what_it_lacks(snaps, planner):
    out = planner.routes(_profile(snaps, ["4.A.3.b.6.a.1", "4.A.2.a.3.c.2", "4.A.4.c.1.c.6"]), {"target": "13-2082.00"}, TODAY)
    tax = next(r for r in out["routes"] if r["code"] == "13-2082.00")
    ids = [s["id"] for s in tax["block"]["steps"]]
    assert ids.index("learn-dynamics.set-up-taxes-finance") < ids.index("learn-dynamics.work-taxes-finance")
    assert tax["block_limits"] and "income tax returns" in tax["block_limits"][0]["text"] and tax["block_limits"][0]["src"]


def test_budget_route_offers_variance_teaching_and_drops_loose_mappings(snaps, planner):
    c = _courses(snaps)
    bus105 = c["saylor.bus105"]
    assert bus105["link_check"]["ok"] and bus105["hours"] == 53.0 and "Variance" in bus105["focus"]
    assert "variance" in bus105["checkpoint"].lower() and "actual" in bus105["checkpoint"].lower()
    # loose mappings removed: report development, a feature tour, and personal budgeting as budget analysis
    assert c["learn-dynamics.explore-reporting-tools-dynamics-365-finance-operations"]["dwas"] == []
    assert "4.A.2.a.4.k.1" not in c["learn.wwl.describe-expense-management-fixed-asset-management"]["dwas"]
    assert "4.A.2.a.4.k.1" not in c["learn.philanthropies.intro-to-budgeting-for-students"]["dwas"]
    out = planner.routes(_profile(snaps, ["4.A.3.b.6.a.1", "4.A.2.a.3.c.2"]), {"target": "13-2031.00"}, TODAY)
    budget = next(r for r in out["routes"] if r["code"] == "13-2031.00")
    analyse = next(g for g in budget["resources"] if g["dwa_id"] == "4.A.2.a.4.k.1")
    assert "saylor.bus105" in [o["id"] for o in analyse["options"]]
    assert budget["block_limits"] and "BUS105" in budget["block_limits"][0]["text"]
    # with enough time the variance course joins the block (12 h a week since 22 Oct: the bank reconciliation module
    # now serves "Verify accuracy of financial information" first, so 120 hours leave 2.2 too few for BUS105)
    long = planner.routes(_profile(snaps, ["4.A.3.b.6.a.1", "4.A.2.a.3.c.2"], 12.0), {"target": "13-2031.00", "weeks": 12}, TODAY)
    assert "saylor.bus105" in [s["id"] for s in next(r for r in long["routes"] if r["code"] == "13-2031.00")["block"]["steps"]]


def test_every_verified_entry_still_passes_its_link_check(snaps):
    shown = pathway.verified_courses(snaps.catalogue, TODAY)
    assert {"saylor.bus105", "learn-dynamics.set-up-taxes-finance"} <= {c["id"] for c in shown}


def test_short_entries_serve_the_near_twin_finance_gaps(snaps, planner):
    """22 Oct: bank reconciliation also teaches "Verify accuracy of financial information" and Information Literacy
    "Update professional knowledge", the twins of activities they already served; finance blocks use them."""
    c = _courses(snaps)
    assert "4.A.2.a.2.a.5" in c["learn-dynamics.bank-reconciliation-dynamics-365-business-central"]["dwas"]
    assert "4.A.2.b.3.a.21" in c["saylor.prdv201"]["dwas"]
    out = planner.routes(_profile(snaps, ["4.A.3.b.6.a.1", "4.A.2.a.3.c.2"]), {"target": "13-2011.00"}, TODAY)
    acc = next(r for r in out["routes"] if r["code"] == "13-2011.00")
    verify = next(g for g in acc["resources"] if g["dwa_id"] == "4.A.2.a.2.a.5")
    assert verify["served"] and verify["options"][0]["id"] == "learn-dynamics.bank-reconciliation-dynamics-365-business-central"

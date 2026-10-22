"""Cohort view (SPEC 11.10, checks K1-K3), recomputed here from each member's planner output."""
import datetime as dt
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aivancin import intake
from aivancin.app import create_app
from aivancin.cohort import Cohort
from aivancin.matching import Matcher
from aivancin.report import Reporter
from aivancin.routes import Planner

from .conftest import DATA
from .test_forecast import USER_SRC, shown_numbers

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 22)
CUTS = (25, 50, 75)  # SPEC 11.10, initial


@pytest.fixture(scope="module")
def planner(snaps):
    m = Matcher(snaps)
    return Planner(snaps, m, Reporter(snaps, m))


@pytest.fixture(scope="module")
def summary(snaps, planner):
    return Cohort(snaps, planner).summary(TODAY)


@pytest.fixture(scope="module")
def member_routes(snaps, planner):
    """Each member's routes, built here from the bundled cohort file the way SPEC 11.10 states."""
    out = []
    for m in snaps.cohort["members"]:
        req = {"title_code": m["job"], "degree_level": m["level"], "weekly_hours": m["hours"], "budget_rm": m["budget"],
               "confirmed": [{"dwa_id": d, "source": "added_by_you", "lane": "other_work"} for d in m["activities"]]}
        prof = intake.build_profile(req, snaps.onet, None, snaps.cip)
        # Keyed by position, not by the file's ids, so a repeated id cannot merge two members here too.
        out.append((len(out), m["job"], planner.routes(prof, {"weeks": 4}, TODAY, with_radar=False)["routes"]))
    return out


def first(routes):
    return sorted(routes, key=lambda r: (-r["overlap"]["v"], r["code"]))[0] if routes else None


def test_cohort_file_reproduces_and_is_synthetic(snaps):
    spec = importlib.util.spec_from_file_location("build_cohort", ROOT / "tools" / "build_cohort.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    built = (json.dumps(tool.build(), indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    assert built == (DATA / "cohort" / "demo.json").read_bytes()
    assert snaps.cohort["synthetic"] is True and len(snaps.cohort["members"]) == 60
    assert len({m["id"] for m in snaps.cohort["members"]}) == 60
    assert snaps.source("aivancin-cohort")["derived_rows"] == 60


def test_k1_summary_and_families(summary, member_routes, snaps):
    with_route = [x for x in member_routes if x[2]]
    assert summary["members"]["v"] == 60
    assert summary["with_route"]["v"] == len(with_route) and summary["without_route"]["v"] == 60 - len(with_route)
    fam_members = defaultdict(set)
    for mid, _, routes in member_routes:
        for r in routes:
            fam_members[r["family"]].add(mid)
    firsts = Counter(first(rs)["family"] for _, _, rs in with_route)
    want = [(f, len(fam_members[f]), firsts[f]) for f in snaps.routes["families"] if fam_members[f]]
    assert [(f["id"], f["members"]["v"], f["first"]["v"]) for f in summary["families"]] == want


def test_k1_heatmap_and_by_route(summary, member_routes, snaps):
    names = snaps.onet.dwa_names
    fams = [f["id"] for f in summary["families"]]
    in_fam = defaultdict(set)
    gap_fam = defaultdict(lambda: defaultdict(set))
    gap_any = defaultdict(set)
    for mid, _, routes in member_routes:
        for r in routes:
            in_fam[r["family"]].add(mid)
            for g in r["prioritized"]:
                gap_fam[g["dwa_id"]][r["family"]].add(mid)
                gap_any[g["dwa_id"]].add(mid)
    rows = []
    for f in fams:
        ranked = sorted((d for d in gap_fam if gap_fam[d][f]), key=lambda d: (-len(gap_fam[d][f]), names[d]))
        rows += [d for d in ranked if d not in rows][:4]
    assert [r["dwa_id"] for r in summary["heatmap"]] == rows and len(rows) == 4 * len(fams)
    shades = Counter()
    for r in summary["heatmap"]:
        assert r["members"]["v"] == len(gap_any[r["dwa_id"]])
        for f in fams:
            n, of = len(gap_fam[r["dwa_id"]][f]), len(in_fam[f])
            share = 100 * n / of
            want = 0 if n == 0 else 1 if share < CUTS[0] else 2 if share < CUTS[1] else 3 if share < CUTS[2] else 4
            cell = r["cells"][f]
            assert (cell["count"]["v"], cell["shade"]) == (n, want) and abs(cell["share"]["v"] - share) <= 0.05
            shades[want] += 1
    assert shades[0] and shades[4]  # both ends of the scale occur
    by_route = defaultdict(lambda: Counter())
    holders = defaultdict(set)
    for mid, _, routes in member_routes:
        for r in routes:
            holders[r["code"]].add(mid)
            by_route[r["code"]].update(g["dwa_id"] for g in r["prioritized"])
    order = sorted(holders, key=lambda c: (-len(holders[c]), next(x["title"] for x in summary["by_route"] if x["code"] == c)))
    assert [x["code"] for x in summary["by_route"]] == order
    for x in summary["by_route"]:
        top = sorted(by_route[x["code"]].items(), key=lambda kv: (-kv[1], names[kv[0]]))[:3]
        assert x["members"]["v"] == len(holders[x["code"]])
        assert [(g["dwa_id"], g["members"]["v"]) for g in x["gaps"]] == top


def test_k1_foundations_and_sdg(summary, member_routes, planner):
    firsts = [(mid, job, first(rs)) for mid, job, rs in member_routes if rs]
    holders, titles = defaultdict(set), {}
    for mid, _, r in firsts:
        for s in r["block"]["steps"]:
            holders[s["id"]].add(mid)
            titles[s["id"]] = s["title"]
    want = sorted((i for i in holders if len(holders[i]) >= 2), key=lambda i: (-len(holders[i]), titles[i]))[:8]
    assert [(f["id"], f["members"]["v"]) for f in summary["foundations"]] == [(i, len(holders[i])) for i in want]
    up = [r for _, job, r in firsts if r["masco"].get("skill") == "skilled"
          and planner.rep.masco(job).get("skill") in ("semi-skilled", "low-skilled")]
    sd = summary["sdg"]
    assert sd["s85"]["members"]["v"] == len(up) and sd["s85"]["of"]["v"] == len(firsts)
    steps = [r["pay"]["step"]["v"] for r in up if r["pay"]]
    assert (sd["s85"]["pay_low"]["v"], sd["s85"]["pay_high"]["v"]) == (min(steps), max(steps))
    blocks = [r["block"]["steps"] for _, _, r in firsts]
    assert sd["s43"]["of"]["v"] == sum(len(b) for b in blocks)
    assert sd["s43"]["free"]["v"] == sum(1 for b in blocks for s in b if s["cost_rm"]["v"] == 0)
    assert sd["s82"]["checkpoints"]["v"] == sum(1 for b in blocks for s in b if s["form"] == "ai" and s["kind"] != "ai_practice")
    assert sd["s44"]["gaps"]["v"] == sum(len(r["block"]["gaps_served"]) if isinstance(r["block"]["gaps_served"], list)
                                         else r["block"]["gaps_served"]["v"] for _, _, r in firsts)


def test_k2_every_cohort_figure_has_a_dated_source(summary, snaps):
    recs = {r["source_id"]: r for r in snaps.manifest["snapshots"]}
    dated = {s for s, r in recs.items() if r.get("release") or r.get("access_date")}
    numbers = list(shown_numbers(summary))
    bad = [(p, v, s) for p, v, s in numbers if not s or not set(s) <= dated | USER_SRC]
    assert len(numbers) > 200 and not bad, bad[:5]
    # Counts of the cohort cite it; a course's own hours and the national context are facts, cited as such.
    facts = (".sdg.baseline", ".sdg.s5", ".foundations")
    assert all("aivancin-cohort" in s for p, v, s in numbers if not p.startswith(facts) or p.endswith(".members"))


def test_k3_no_input_and_no_effect_on_a_person():
    client = TestClient(create_app(DATA))
    hafiz = next(p for p in client.get("/api/personas").json() if p["id"] == "hafiz")
    sug = client.post("/api/suggest", json=hafiz).json()
    pick = [s["dwa_id"] for s in sug["suggestions"] if s["lanes"] == ["other_work"]][:5]
    req = {**hafiz, "confirmed": [{"dwa_id": d, "source": "suggested"} for d in pick]}
    before = client.post("/api/routes", json=req).json()
    got = client.get("/api/cohort")
    assert got.status_code == 200 and got.json()["synthetic"] is True
    assert client.post("/api/cohort", json={"members": []}).status_code == 405
    assert client.get("/api/cohort", params={"members": "x"}).json() == got.json()
    assert client.post("/api/routes", json=req).json() == before

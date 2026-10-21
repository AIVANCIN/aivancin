"""Cohort view (SPEC 11.10): where a bundled synthetic cohort's routes lead, the priority gaps its members share, the
first-block steps they share, and what their first blocks contribute to the SDG targets.

Counts only, never a score. Each member's routes are the planner's routes for one person; nothing here changes anyone's
routes, and the endpoint takes no input.
"""
from collections import Counter, defaultdict

from . import intake, outlook
from .report import ONET_SRC, PAY_SRC, fig

SRC = "aivancin-cohort"
ROUTES_SRC = "aivancin-routes"
CAT_SRC = "aivancin-catalogue"
WEEKS = 4
ROWS_PER_FAMILY = 4  # initial (SPEC 11.10): each family's most common priority gaps, merged
TOP_STEPS = 8  # initial
ROUTE_GAPS = 3
SHADE_CUTS = (0.25, 0.5, 0.75)  # initial: shades 0, under 25%, 25-49%, 50-74%, 75% or more
FAMILY_SHORT = {"it_support": "IT support", "software": "Software", "finance_analytics": "Finance and data"}
LOWER_SKILL = ("semi-skilled", "low-skilled")


def shade(count, of):
    if not count:
        return 0
    return 1 + sum(count / of >= c for c in SHADE_CUTS)


def first_route(routes):
    """The first route in the default order (overlap, then code), as on the Routes screen."""
    return min(routes, key=lambda r: (-r["overlap"]["v"], r["code"])) if routes else None


class Cohort:
    def __init__(self, snaps, planner):
        self.snaps, self.pl = snaps, planner
        self.doc = snaps.cohort or {}

    def profile(self, m):
        req = {"title_code": m["job"], "degree_level": m["level"], "weekly_hours": m["hours"], "budget_rm": m["budget"],
               "confirmed": [{"dwa_id": d, "source": "added_by_you", "lane": "other_work"} for d in m["activities"]]}
        return intake.build_profile(req, self.snaps.onet, None, self.snaps.cip)

    def runs(self, today=None):
        """Each member with their routes, as the planner gives them to one person."""
        return [(m, self.pl.routes(self.profile(m), {"weeks": WEEKS}, today, with_radar=False)["routes"])
                for m in self.doc.get("members", [])]

    def summary(self, today=None):
        if not self.doc.get("members"):
            return None
        runs = self.runs(today)
        fam_labels = (self.snaps.routes or {}).get("families", {})
        gap_src = [SRC, ROUTES_SRC, *ONET_SRC]
        names = dict(self.snaps.onet.dwa_names)

        fam_members = defaultdict(set)
        gap_fam = defaultdict(lambda: defaultdict(set))
        gap_any = defaultdict(set)
        by_route = {}
        for m, routes in runs:
            for r in routes:
                fam_members[r["family"]].add(m["id"])
                br = by_route.setdefault(r["code"], {"code": r["code"], "title": r["title"], "family": r["family"],
                                                     "members": set(), "gaps": Counter()})
                br["members"].add(m["id"])
                for g in r["prioritized"]:
                    gap_fam[g["dwa_id"]][r["family"]].add(m["id"])
                    gap_any[g["dwa_id"]].add(m["id"])
                    br["gaps"][g["dwa_id"]] += 1
        fams = [f for f in fam_labels if fam_members[f]]

        rows = []
        for f in fams:
            top = sorted((d for d in gap_fam if gap_fam[d][f]), key=lambda d: (-len(gap_fam[d][f]), names[d]))
            rows += [d for d in top if d not in rows][:ROWS_PER_FAMILY]
        heatmap = [{"dwa_id": d, "name": names[d], "members": fig(len(gap_any[d]), *gap_src),
                    "cells": {f: {"count": fig(len(gap_fam[d][f]), *gap_src),
                                  "share": fig(round(100 * len(gap_fam[d][f]) / len(fam_members[f]), 1), *gap_src),
                                  "shade": shade(len(gap_fam[d][f]), len(fam_members[f]))} for f in fams}}
                   for d in rows]
        routes_out = [{"code": b["code"], "title": b["title"], "family": b["family"], "members": fig(len(b["members"]), *gap_src),
                       "gaps": [{"dwa_id": d, "name": names[d], "members": fig(k, *gap_src)}
                                for d, k in sorted(b["gaps"].items(), key=lambda kv: (-kv[1], names[kv[0]]))[:ROUTE_GAPS]]}
                      for b in sorted(by_route.values(), key=lambda b: (-len(b["members"]), b["title"]))]

        firsts = [(m, first_route(routes)) for m, routes in runs if routes]
        step_members, steps, step_gaps = defaultdict(set), {}, defaultdict(set)
        for m, r in firsts:
            for s in r["block"]["steps"]:
                step_members[s["id"]].add(m["id"])
                steps.setdefault(s["id"], s)
                step_gaps[s["id"]].update(g if isinstance(g, str) else g["dwa_id"] for g in s["serves"])
        shared = sorted((i for i in step_members if len(step_members[i]) >= 2),
                        key=lambda i: (-len(step_members[i]), steps[i]["title"]))[:TOP_STEPS]
        foundations = [{"id": i, "title": steps[i]["title"], "provider": steps[i]["provider"], "url": steps[i]["url"],
                        "hours": steps[i]["provider_hours"], "members": fig(len(step_members[i]), SRC, ROUTES_SRC, CAT_SRC),
                        "gaps": sorted(names[d] for d in step_gaps[i] if d in names)} for i in shared]

        up, pay = 0, []
        gaps = hours = free = of = ai = 0
        for m, r in firsts:
            cur = self.pl.rep.masco(m["job"]).get("skill")
            if r["masco"].get("skill") == "skilled" and cur in LOWER_SKILL:
                up += 1
                if r["pay"]:
                    pay.append(r["pay"]["step"]["v"])
            c = r["contribution"]
            gaps += c["s44"]["gaps"]["v"]
            hours += c["s44"]["hours"]["v"]
            free += c["s43"]["free"]["v"]
            of += c["s43"]["of"]["v"]
            ai += c["s82"]["checkpoints"]["v"]
        first_fam = Counter(r["family"] for _, r in firsts)
        aei_src = [f"aei-{x}" for x in self.snaps.evidence.ids]
        base = self.pl.rep._baseline() or {}
        return {
            "synthetic": True, "title": self.doc["title"], "confirmation": self.doc["confirmation"],
            "weeks": fig(WEEKS, SRC), "members": fig(len(runs), SRC),
            "with_route": fig(len(firsts), SRC, ROUTES_SRC), "without_route": fig(len(runs) - len(firsts), SRC, ROUTES_SRC),
            "families": [{"id": f, "label": fam_labels[f], "short": FAMILY_SHORT.get(f, fam_labels[f]),
                          "members": fig(len(fam_members[f]), SRC, ROUTES_SRC), "first": fig(first_fam[f], SRC, ROUTES_SRC)}
                         for f in fams],
            "heatmap": heatmap, "shade_cuts": [round(100 * c) for c in SHADE_CUTS], "rows_per_family": ROWS_PER_FAMILY,
            "by_route": routes_out, "foundations": foundations,
            "sdg": {"s85": {"members": fig(up, SRC, "esco-onet-isco"), "of": fig(len(firsts), SRC, ROUTES_SRC),
                            "pay_low": fig(min(pay), SRC, *PAY_SRC) if pay else None,
                            "pay_high": fig(max(pay), SRC, *PAY_SRC) if pay else None},
                    "s44": {"gaps": fig(gaps, SRC, *ONET_SRC, CAT_SRC), "hours": fig(round(hours, 1), SRC, CAT_SRC)},
                    "s43": {"free": fig(free, SRC, CAT_SRC), "of": fig(of, SRC, CAT_SRC)},
                    "s82": {"checkpoints": fig(ai, SRC, CAT_SRC, *aei_src)},
                    "baseline": outlook.sru_trend(self.snaps),
                    "s5": base and {"female": base["rate_female"], "male": base["rate_male"], "date": base["date"]},
                    "src": ["un-sdg-targets"]},
            "src": [SRC]}

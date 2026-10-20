"""Skill outlook per O*NET activity and the job-outlook order (SPEC section 4b).

A projection is an estimate under a named scenario. It describes how much of an activity AI does
directly in observed Claude use, not employment, risk or labour demand.
"""
import datetime as dt
from collections import defaultdict

from . import aei

SCENARIOS = ("slower", "moderate", "faster")
LOWER_CUT = 30.0  # initial: below this, people mostly work with AI rather than handing the activity over
PACE_MONTHS = 8
DEGREE_TOPUP_MONTHS = 24  # assumption, shown
MIN_HORIZON_MONTHS = 12  # assumption, shown: a career switch is not judged on a look-ahead shorter than a year
EXPERIENCE_MONTHS = {6: 12, 7: 24, 8: 48, 9: 72, 10: 96, 11: 120}
HORIZON_CHOICES = (12, 24, 36, 60)
BAND_SCORE = {"Decline": -1.0, "Little or no change": 0.0, "Slower than average": 0.25, "Average": 0.5,
              "Faster than average": 0.75, "Much faster than average": 1.0}
OUTLOOK_WEIGHT = 0.3  # initial nu when the user picks the outlook order


def band_score(band):
    if not band:
        return 0.0
    name = band.split(" (")[0]
    if name not in BAND_SCORE:
        raise ValueError(f"unknown projected-growth band: {band}")
    return BAND_SCORE[name]


def window_mid(release_id):
    r = next(x for x in aei.RELEASES if x["release"] == release_id)
    a, b = dt.date.fromisoformat(r["window_start"]), dt.date.fromisoformat(r["window_end"])
    return a + (b - a) / 2


class SkillOutlook:
    def __init__(self, snaps):
        self.snaps = snaps
        self.ev = snaps.evidence
        onet = snaps.onet
        groups = defaultdict(lambda: defaultdict(list))
        for code, links in onet.occupation_dwas.items():
            for tid, dwa in links:
                text = aei.norm_task(onet.tasks[(code, tid)]["task"])
                groups[dwa][text].append(tid)
        # distinct task statements per activity, each with its task IDs in a fixed order
        self.groups = {d: [sorted(set(ids), key=int) for _, ids in sorted(g.items())] for d, g in groups.items()}
        self.drop = self._largest_drop()
        self._cache = {}

    def _largest_drop(self):
        rel = self.ev.releases
        if len(rel) < 2:
            return 0.0
        common = set(rel[0][1])
        for _, tasks, _ in rel[1:]:
            common &= set(tasks)
        if not common:
            return 0.0
        means = [sum(tasks[t]["share"] for t in common) / len(common) for _, tasks, _ in rel]
        return round(max(0.0, max(a - b for a, b in zip(means, means[1:]))), 1)

    def release_values(self, dwa):
        """[(release_id, value, tasks_used)] for every bundled release that covers the activity."""
        out = []
        for rid, tasks, _ in self.ev.releases:
            shares = []
            for ids in self.groups.get(dwa, []):
                hit = next((tasks[t]["share"] for t in ids if t in tasks), None)
                if hit is not None:
                    shares.append(hit)
            if shares:
                out.append((rid, sum(shares) / len(shares), len(shares)))
        return out

    def outlook(self, dwa, release=None):
        key = (dwa, release)
        if key in self._cache:
            return self._cache[key]
        ids = self.ev.ids if release is None else self.ev.ids[: self.ev.ids.index(release) + 1]
        vals = [v for v in self.release_values(dwa) if v[0] in ids]
        if not vals:
            res = None
        else:
            now = sum(v for _, v, _ in vals) / len(vals)
            res = {"now": round(now, 1), "releases": [{"release": r, "share": round(v, 1), "tasks": n} for r, v, n in vals],
                   "weak": len(vals) < 2 or all(n == 1 for _, _, n in vals),
                   "src": [f"aei-{r}" for r, _, _ in vals] + ["onet31-task-dwas"]}
        self._cache[key] = res
        return res

    def project(self, a0, scenario, months, pace):
        if scenario == "faster":
            return min(100.0, a0 * pace ** (months / PACE_MONTHS))
        if scenario == "slower":
            return max(0.0, a0 - self.drop) if months > 0 else a0
        return a0


def classify(value, threshold):
    if value is None:
        return "no evidence in the bundled releases"
    if value >= threshold:
        return "AI does most of it directly"
    if value < LOWER_CUT:
        return "people mostly work with AI"
    return "shared"


def planning_months(plan_months, labels, experience):
    """The default planning horizon: the longest of a one-year minimum, plan time, a degree top-up and related
    experience. It is a look-ahead for the scenarios, not a date by which anyone is ready for the role."""
    parts = {"minimum look-ahead (assumption)": MIN_HORIZON_MONTHS, "plan": plan_months}
    ids = {lab["id"] for lab in labels}
    if "needs_degree" in ids:
        parts["degree top-up (assumption)"] = DEGREE_TOPUP_MONTHS
    if "needs_experience" in ids:
        parts["related experience"] = EXPERIENCE_MONTHS.get(experience[0], 12) if experience else 12
    key = max(parts, key=lambda k: (parts[k], k))
    return parts[key], key

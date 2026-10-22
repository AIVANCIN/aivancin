"""Skill outlook per O*NET activity, skill demand, the Malaysian group trend and the job-outlook order
(SPEC sections 4b and 11.1).

A projection is an estimate under a named scenario. The AI shift describes how much of an activity AI does
directly in observed Claude use, not employment, risk or labour demand. Skill demand carries BLS's published
projection to activities; it is not an AIVANCIN employment forecast. The Malaysian trend is observed, never projected.
"""
import datetime as dt
import math
from collections import defaultdict

from . import aei
from .snapshots import MASCO_GROUPS

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
GROW_BANDS = ("Faster than average", "Much faster than average")
DECLINE_BANDS = ("Decline",)
# initial (SPEC 11.1): a net share (growing minus declining) at or beyond this reads "more growth than decline" or
# "more decline than growth". The net says only which side is larger, not that most of the activity sits in growing jobs.
NET_CUT = 0.25
FEW_ROLES = 3  # initial: fewer pool roles than this are shown as "based on N roles"
GROWTH, DECLINE = "more growth than decline", "more decline than growth"
DEMAND_RANK = {GROWTH: 0, "mixed": 1, "no projection": 2, DECLINE: 3}
DEMAND_SRC = ["onet-online-outlook", "onet31-task-dwas", "onet31-tasks", "onet31-job-zones"]
TTT_MAX_MONTHS = 60
TREND_START = 2016  # initial (SPEC 11.1): the series shows breaks between 2010 and 2011 and between 2015 and 2016
TREND_SRC = ["dosm-wages-occupation"]


def band_name(band):
    return band.split(" (")[0] if band else None


def demand_label(net, roles, cut=NET_CUT):
    if not roles:
        return "no projection"
    if net >= cut:
        return GROWTH
    if net <= -cut:
        return DECLINE
    return "mixed"


class DemandOutlook:
    """F2: BLS's published projection carried to each O*NET activity through its task links.

    Pool: the matcher's candidate roles (Job Zone 3 or 4, not SOC 11 or 55, with activity links) whose outlook record
    has a band. For activity d, the shares are weighted by w(c, d). Openings are summed once per BLS projection unit:
    O*NET OnLine shows each occupation the employment, band and openings of the BLS unit it belongs to, so detailed
    occupations sharing one unit (for example the nine 15-1299 codes) would otherwise be counted several times."""

    def __init__(self, snaps, matcher, net_cut=None):
        self.cut = NET_CUT if net_cut is None else net_cut
        records = snaps.outlook or {}
        pool = [c for c in matcher.candidates() if records.get(c, {}).get("band")]
        acc = defaultdict(lambda: {"W": 0.0, "grow": 0.0, "decline": 0.0, "roles": [], "units": {}})
        for c in pool:
            rec = records[c]
            name = band_name(rec["band"])
            unit = (rec.get("employment"), rec["band"], rec["openings"])
            for d, w in matcher.w[c].items():
                if w <= 0:
                    continue
                a = acc[d]
                a["W"] += w
                a["grow"] += w if name in GROW_BANDS else 0.0
                a["decline"] += w if name in DECLINE_BANDS else 0.0
                a["roles"].append(c)
                if rec["openings"] is not None:
                    a["units"][unit] = rec["openings"]
        self.period = next((records[c]["openings_period"] for c in pool if records[c].get("openings_period")), None)
        self.pool = pool
        self.acc = dict(acc)

    def get(self, dwa):
        a = self.acc.get(dwa)
        if not a:
            return {"label": "no projection", "roles": 0, "src": DEMAND_SRC}
        grow, decline = a["grow"] / a["W"], a["decline"] / a["W"]
        net = grow - decline
        roles = len(a["roles"])
        label = demand_label(net, roles, self.cut)
        return {"grow": round(grow, 3), "decline": round(decline, 3), "net": round(net, 3), "roles": roles,
                "openings": sum(a["units"].values()), "openings_period": self.period, "label": label,
                "few": roles < FEW_ROLES, "src": DEMAND_SRC}

    def rank(self, dwa):
        return DEMAND_RANK[self.get(dwa)["label"]]


def time_to_threshold(a0, threshold, scenario, pace, drop):
    """SPEC 11.1: months until the activity's projected share reaches the threshold under one scenario.
    Returns (months or None, text). An estimate under the stated scenario, not a prediction of any date."""
    if a0 is None:
        return None, "no evidence in the bundled releases"
    value = max(0.0, a0 - drop) if scenario == "slower" else a0
    if value >= threshold:
        return 0, "already at or above the threshold"
    if scenario != "faster" or a0 <= 0 or pace <= 1.0:
        return None, "not reached in this scenario"
    t = PACE_MONTHS * math.log(threshold / a0) / math.log(pace)
    if t > TTT_MAX_MONTHS:
        return None, "not within five years"
    months = int(t + 0.5)
    return months, f"about {months} month{'s' if months != 1 else ''}"


def group_trend(snaps, major, start=TREND_START):
    """F4: observed DOSM change for one MASCO major group (sex both), from the window's first to its latest year.
    Group level and past tense; never projected."""
    if not major or major not in MASCO_GROUPS:
        return None
    key, name = MASCO_GROUPS[major]
    rows = [r for r in snaps.wage_series.get((key, "both"), []) if int(r["date"][:4]) >= start]
    if len(rows) < 2:
        return None
    a, b = rows[0], rows[-1]
    y0, y1 = int(a["date"][:4]), int(b["date"][:4])
    years = y1 - y0

    def change(x0, x1):
        return {"from": x0, "to": x1, "pct": round(100 * (x1 - x0) / x0, 1),
                "cagr": round(100 * ((x1 / x0) ** (1 / years) - 1), 1)}

    emp = change(a["recipients_k"], b["recipients_k"])
    pay = change(a["median"], b["median"])
    by_year = {int(r["date"][:4]): r for r in rows}
    notes = []
    if 2020 in by_year and 2019 in by_year and y0 < 2020 < y1:
        m19, m20 = by_year[2019]["median"], by_year[2020]["median"]
        e19, e20 = by_year[2019]["recipients_k"], by_year[2020]["recipients_k"]
        everyone = all(len(s) and _year(s, 2020) and _year(s, 2019) and _year(s, 2020)["median"] < _year(s, 2019)["median"]
                       for (g, sex), s in snaps.wage_series.items() if sex == "both" and g != "overall")
        notes.append(f"The 2020 edition is out of line: this group's median went from RM{m19:,} (2019) to RM{m20:,} "
                     f"and its employees from {e19:,.0f}k to {e20:,.0f}k"
                     + ("; medians fell in every group that year" if everyone else "") +
                     ". The window's first and latest years are not affected.")
    sign = "rose" if emp["to"] >= emp["from"] else "fell"
    psign = "rose" if pay["to"] >= pay["from"] else "fell"
    text = (f"{name} in Malaysia: employees {sign} from {emp['from']:,.0f}k to {emp['to']:,.0f}k "
            f"({emp['pct']:+.0f}%) and the median monthly wage {psign} from RM{pay['from']:,} to RM{pay['to']:,}, "
            f"{y0}-{y1} (DOSM). Group level, not this occupation.")
    return {"major": major, "group": name, "start": y0, "end": y1, "employees_k": emp, "median_rm": pay,
            "text": text, "notes": notes, "window_note": f"From {start}: the series has breaks between 2010 and 2011 "
            "and between 2015 and 2016.", "src": TREND_SRC}


def _year(series, year):
    return next((r for r in series if int(r["date"][:4]) == year), None)


def sru_trend(snaps, age="25-34"):
    """The 25-34 skill-related underemployment rate at the first and latest bundled quarter (observed)."""
    rows = (snaps.sru_series or {}).get("age", {}).get(("rate", age), [])
    if len(rows) < 2:
        return None
    (d0, v0), (d1, v1) = rows[0], rows[-1]
    return {"age": age, "from": {"date": d0, "rate": v0}, "to": {"date": d1, "rate": v1}, "src": ["dosm-sru-age"]}


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
        self.means, self.common_tasks = self._release_means()
        self.drop = self._largest_drop()
        self._cache = {}

    def _release_means(self):
        """Mean automation share per release over the tasks present in every bundled release."""
        rel = self.ev.releases
        if not rel:
            return [], 0
        common = set(rel[0][1])
        for _, tasks, _ in rel[1:]:
            common &= set(tasks)
        if not common:
            return [], 0
        return [(rid, sum(tasks[t]["share"] for t in common) / len(common)) for rid, tasks, _ in rel], len(common)

    def _largest_drop(self):
        means = [m for _, m in self.means]
        if len(means) < 2:
            return 0.0
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

    def release_conversations(self, dwa):
        """{release_id: classified conversations behind the release value}, None where a release publishes no counts."""
        out = {}
        for rid, tasks, _ in self.ev.releases:
            counts = []
            for ids in self.groups.get(dwa, []):
                hit = next((tasks[t] for t in ids if t in tasks), None)
                if hit is not None:
                    counts.append(hit.get("classified"))
            if counts:
                out[rid] = None if any(c is None for c in counts) else sum(counts)
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
            conv = self.release_conversations(dwa)
            res = {"now": round(now, 1), "releases": [{"release": r, "share": round(v, 1), "tasks": n, "conversations": conv.get(r)}
                                                      for r, v, n in vals],
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

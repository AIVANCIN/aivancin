"""Activity-overlap matching (SPEC section 3).

The score is overlap in recorded O*NET activity links, never a share of a job or of time.
"""
from collections import defaultdict

LANES = ("current_job", "other_work", "studies", "all")
CORE, SUPPLEMENTAL = 1.0, 0.5
MAX_ROLES = 6


def soc6(code):
    return code.split(".")[0]


class Matcher:
    def __init__(self, snaps):
        self.snaps = snaps
        onet = snaps.onet
        w = defaultdict(lambda: defaultdict(float))
        task_w = {}
        for code, links in onet.occupation_dwas.items():
            for task_id, dwa_id in links:
                t = onet.tasks.get((code, task_id))
                weight = SUPPLEMENTAL if t and t["type"] == "Supplemental" else CORE
                w[code][dwa_id] += weight
                task_w[(code, task_id)] = weight
        self.w = {c: dict(d) for c, d in w.items()}
        self.total = {c: sum(d.values()) for c, d in self.w.items()}
        self.task_w = task_w
        self.tasks_by_dwa = defaultdict(list)
        self.tasks_by_code = defaultdict(dict)
        for code, links in onet.occupation_dwas.items():
            for task_id, dwa_id in links:
                self.tasks_by_dwa[(code, dwa_id)].append(task_id)
                self.tasks_by_code[code][task_id] = task_w[(code, task_id)]

    def candidates(self, exclude=None, allow=None):
        """Job Zone 3-4 roles outside SOC 11 and 55 with activity links; `allow(code)` narrows them further."""
        onet = self.snaps.onet
        out = []
        for code in self.w:
            if onet.job_zones.get(code) not in (3, 4) or code.startswith(("11-", "55-")) or code == exclude:
                continue
            if allow and not allow(code):
                continue
            out.append(code)
        return sorted(out)

    def lane_profile(self, activities, lane):
        if lane in ("all", "studies"):
            return {a["dwa_id"] for a in activities}
        return {a["dwa_id"] for a in activities if lane in a["lanes"]}

    def overlap(self, code, profile):
        mine = sum(v for d, v in self.w[code].items() if d in profile)
        return mine / self.total[code] if self.total[code] else 0.0

    def tasks(self, code):
        """(task_id, weight) for the role's tasks that have activity links."""
        return sorted(self.tasks_by_code.get(code, {}).items(), key=lambda kv: int(kv[0]))

    def rank(self, profile, lane, current_code=None, cip_code=None, ai_share=None, pay_step=None,
             outlook=None, allow=None, lam=0.0, mu=0.0, nu=0.0, limit=MAX_ROLES):
        """Return ranked roles and the candidate-set facts used for normalisation.

        ai_share(code) -> automation share or None; pay_step(code) -> RM step or None;
        outlook(code) -> projected-growth band score in [-1, 1] (0 without a projection).
        """
        pool = self.candidates(exclude=current_code, allow=allow)
        crosswalk = None
        if lane == "studies":
            socs = self.snaps.cip.get(cip_code, {}).get("soc", set()) if cip_code else set()
            crosswalk = [c for c in pool if soc6(c) in socs]
            pool = crosswalk
        scored = [(self.overlap(c, profile), c) for c in pool]
        positive = [(o, c) for o, c in scored if o > 0]
        no_overlap_listed = False
        if lane == "studies" and len(positive) < 3:
            # Too little overlap inside the degree's crosswalk: list its roles, labelled, rather than nothing.
            positive = positive + [(0.0, c) for o, c in scored if o == 0]
            no_overlap_listed = True
        max_o = max((o for o, _ in positive), default=0.0)
        steps = {c: (pay_step(c) if pay_step else None) for _, c in positive}
        max_step = max((s for s in steps.values() if s and s > 0), default=0)
        rows = []
        for o, c in positive:
            f = o / max_o if max_o else 0.0
            a = ai_share(c) if ai_share else None
            p = (steps[c] / max_step) if (max_step and steps[c] and steps[c] > 0) else 0.0
            b = outlook(c) if outlook else 0.0
            s = f - (lam * a / 100 if a is not None else 0.0) + mu * p + nu * b
            rows.append({"code": c, "overlap": o, "fit": f, "ai_share": a, "pay_norm": p, "outlook": b, "score": s})
        rows.sort(key=lambda r: (-r["score"], r["code"]))
        return {"roles": rows[:limit], "ranked": rows, "pool_size": len(pool), "max_overlap": max_o,
                "no_overlap_listed": no_overlap_listed, "crosswalk_size": None if crosswalk is None else len(crosswalk)}

    def done_and_missing(self, code, profile):
        names = self.snaps.onet.dwa_names
        items = sorted(self.w[code].items(), key=lambda kv: (-kv[1], kv[0]))
        done = [{"dwa_id": d, "name": names[d], "weight": v} for d, v in items if d in profile]
        missing = [{"dwa_id": d, "name": names[d], "weight": v} for d, v in items if d not in profile]
        return done, missing

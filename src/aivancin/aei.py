"""Anthropic Economic Index: per-release adapters (build time) and pooled task evidence (run time).

Automation share = directive + feedback loop over the five classified collaboration patterns
(directive, feedback loop, task iteration, validation, learning). The 2026-06-26 release publishes
that split directly as automation and augmentation buckets, without counts.
"""
import csv
import math
import re
from collections import defaultdict

RELEASES = [
    {"release": "2025-09-15", "kind": "raw_v1", "window_start": "2025-08-04", "window_end": "2025-08-11",
     "path": "release_2025_09_15/data/intermediate/aei_raw_claude_ai_2025-08-04_to_2025-08-11.csv",
     "file": "aei_raw_claude_ai_2025-08-04_to_2025-08-11.csv",
     "sha256": "c8ef9c5eee0c42febc73e358ecc7d2358e0a0ce3b50122c0c15ae8ec569aceff"},
    {"release": "2026-01-15", "kind": "raw_v1", "window_start": "2025-11-13", "window_end": "2025-11-20",
     "path": "release_2026_01_15/data/intermediate/aei_raw_claude_ai_2025-11-13_to_2025-11-20.csv",
     "file": "aei_raw_claude_ai_2025-11-13_to_2025-11-20.csv",
     "sha256": "70dc16780061b84d3f34ddf7fe5c978d65c99a5ef58afd78f9315f886a6a5873"},
    {"release": "2026-03-24", "kind": "raw_v1", "window_start": "2026-02-05", "window_end": "2026-02-12",
     "path": "release_2026_03_24/data/aei_raw_claude_ai_2026-02-05_to_2026-02-12.csv",
     "file": "aei_raw_claude_ai_2026-02-05_to_2026-02-12.csv",
     "sha256": "69ebf6f9afc1e3be45caab172a84ae09e78bf41b97e1c310100d81c8a35a7433"},
    {"release": "2026-06-26", "kind": "monthly_v2", "window_start": "2026-05-01", "window_end": "2026-06-01",
     "path": "release_2026_06_26/data/aei_claude_ai_2026-06-26.csv",
     "file": "aei_claude_ai_2026-06-26.csv",
     "sha256": "f974b358bce0e5a8417510c61da4342234cd0de9d9d0b62acf4c6dbcf8ec7b68"},
]
RELEASE_IDS = [r["release"] for r in RELEASES]
COMPARABILITY = {
    "2026-06-26": "This release uses calendar-month windows and a new published format (automation and augmentation "
                  "buckets, no counts) instead of one-week samples, so a change across this boundary may reflect method, "
                  "not use.",
}
CLASSIFIED = {"directive", "feedback_loop", "task_iteration", "validation", "learning"}
AUTOMATION = {"directive", "feedback_loop"}
WEAK_COUNT_PER_RELEASE = 30  # initial (SPEC section 4)


class ReleaseFormatError(ValueError):
    pass


def norm_task(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower()).rstrip(".").strip()


def _require(fieldnames, needed, release):
    missing = [c for c in needed if c not in (fieldnames or [])]
    if missing:
        raise ReleaseFormatError(f"release {release}: missing columns {missing}")


def adapt_release(spec, path, onet_task_rows):
    """Normalise one release to rows (level, key, share, classified, automation) and join statistics.

    Older releases name tasks by statement text, joined to O*NET 31.0 task IDs by normalised text;
    the 2026-06-26 release carries O*NET task IDs. Unmatched tasks are counted, never guessed.
    """
    ids_by_text = defaultdict(set)
    known_ids = set()
    for r in onet_task_rows:
        ids_by_text[norm_task(r["task"])].add(r["task_id"])
        known_ids.add(r["task_id"])
    rows, stats = [], {}
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if spec["kind"] == "raw_v1":
            _require(reader.fieldnames, ("geo_id", "facet", "variable", "cluster_name", "value"), spec["release"])
            counts = defaultdict(lambda: defaultdict(float))
            for r in reader:
                if r["geo_id"] == "GLOBAL" and r["facet"] == "onet_task::collaboration" \
                        and r["variable"] == "onet_task_collaboration_count":
                    task, _, pattern = r["cluster_name"].rpartition("::")
                    counts[norm_task(task)][pattern.strip().lower().replace(" ", "_")] += float(r["value"])
            matched = unmatched = 0
            for key, d in sorted(counts.items()):
                n = sum(v for p, v in d.items() if p in CLASSIFIED)
                if n <= 0:
                    continue
                k = sum(d.get(p, 0) for p in AUTOMATION)
                ids = ids_by_text.get(key)
                if not ids:
                    unmatched += 1
                    continue
                matched += 1
                for tid in sorted(ids, key=int):
                    rows.append(("task", tid, f"{100 * k / n:.2f}", int(round(n)), int(round(k))))
            stats = {"aei_tasks_with_classified": matched + unmatched, "joined": matched, "unmatched": unmatched}
        elif spec["kind"] == "monthly_v2":
            _require(reader.fieldnames, ("date_start", "geo_id", "category_name", "hierarchy_level", "metric_id", "value",
                                         "node_external_id"), spec["release"])
            cells = defaultdict(dict)
            for r in reader:
                if r["geo_id"] != "GLOBAL" or r["date_start"] != spec["window_start"] or r["hierarchy_level"] != "0":
                    continue
                if r["category_name"] in ("onet", "soc_occupation") and r["metric_id"] in (
                        "collaboration_bucket_automation_pct", "collaboration_bucket_augmentation_pct"):
                    cells[(r["category_name"], r["node_external_id"])][r["metric_id"]] = float(r["value"])
            matched = unmatched = occ = 0
            for (cat, key), m in sorted(cells.items()):
                a, g = m.get("collaboration_bucket_automation_pct"), m.get("collaboration_bucket_augmentation_pct")
                if a is None or g is None or a + g <= 0:
                    continue
                share = f"{100 * a / (a + g):.2f}"
                if cat == "soc_occupation":
                    occ += 1
                    rows.append(("occupation", key, share, "", ""))
                elif key in known_ids:
                    matched += 1
                    rows.append(("task", key, share, "", ""))
                else:
                    unmatched += 1
            stats = {"aei_tasks_with_buckets": matched + unmatched, "joined": matched, "unmatched": unmatched,
                     "occupations": occ}
        else:
            raise ReleaseFormatError(f"release {spec['release']}: unknown adapter {spec['kind']}")
    if not any(r[0] == "task" for r in rows):
        raise ReleaseFormatError(f"release {spec['release']}: no task rows parsed")
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows, stats


def load_release(path):
    tasks, occupations = {}, {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            entry = {"share": float(r["share"]),
                     "classified": int(r["classified"]) if r["classified"] else None,
                     "automation": int(r["automation"]) if r["automation"] else None}
            (tasks if r["level"] == "task" else occupations)[r["key"]] = entry
    return tasks, occupations


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(100 * max(0.0, c - h), 1), round(100 * min(1.0, c + h), 1)]


class Evidence:
    """Task-level automation shares across releases, pooled up to a chosen release (the replay)."""

    def __init__(self, releases):
        # releases: list of (release_id, tasks, occupations) in date order
        self.releases = releases
        self.ids = [r[0] for r in releases]

    def upto(self, release=None):
        if release is None:
            return self.releases
        if release not in self.ids:
            raise KeyError(release)
        return self.releases[: self.ids.index(release) + 1]

    def task(self, task_id, release=None):
        per = [(rid, t[task_id]) for rid, t, _ in self.upto(release) if task_id in t]
        if not per:
            return None
        # Equal weight per release: averaging history beat every forecaster tried (S2-K2); June has no counts.
        share = sum(e["share"] for _, e in per) / len(per)
        counted = [e for _, e in per if e["classified"] is not None]
        n = sum(e["classified"] for e in counted)
        k = sum(e["automation"] for e in counted)
        weak = len(per) < 2 or (counted and n < WEAK_COUNT_PER_RELEASE * len(counted)) or not counted
        return {"share": round(share, 1), "releases": [{"release": rid, "share": e["share"], "classified": e["classified"]}
                                                       for rid, e in per],
                "pooled_classified": n or None, "interval": wilson(k, n), "weak": bool(weak)}

    def occupation(self, code, release=None):
        for rid, _, occ in reversed(self.upto(release)):
            if code in occ:
                return {"release": rid, "share": occ[code]["share"]}
        return None

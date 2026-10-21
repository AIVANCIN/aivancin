"""Build the synthetic cohort for the cohort view (SPEC 11.10).

python tools/build_cohort.py      # writes data/cohort/demo.json; then python tools/build_snapshots.py records it
The members are the 60 agent-written profiles of the B7 sets 1-4 (tests/acceptance/b7_profiles*.json): 48 written in
scope and 12 out of scope. Each member's labelled activities count as confirmed. No real person is in it. A rerun from
the same sets reproduces the file byte for byte.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETS = ["b7_profiles.json", "b7_profiles_2.json", "b7_profiles_3.json", "b7_profiles_4.json"]
OUT = ROOT / "data" / "cohort" / "demo.json"
BUILT_ON = "2026-10-21"


def build():
    members, built_from = [], {}
    for name in SETS:
        path = ROOT / "tests" / "acceptance" / name
        built_from[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        sets = json.loads(path.read_text(encoding="utf-8"))
        for scope in ("in_scope", "out_of_scope"):
            for p in sets[scope]:
                members.append({"id": p["id"], "written": scope.replace("_", " "), "job": p["job"], "level": p["level"],
                                "hours": p["hours"], "budget": p["budget"], "activities": sorted(p["true"])})
    return {"title": "Synthetic cohort: the agent-written profiles of the B7 test sets 1-4", "built_on": BUILT_ON,
            "synthetic": True, "confirmation": "Each member's labelled activities count as confirmed (added by the member, "
            "other-work lane); the job title adds its O*NET activities as for anyone.", "built_from": built_from,
            "members": members}


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes((json.dumps(build(), indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"{OUT.relative_to(ROOT)}: {len(build()['members'])} members")


if __name__ == "__main__":
    main()

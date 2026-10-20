"""Build data/catalogue/courses.json from tools/catalogue_curation.json and the Microsoft Learn catalog API.

Run from the repo root: python tools/build_catalogue.py
Each entry takes its title, URL and stated duration from the provider's catalog API and gets a fresh
link check (HTTP 200 and not a "not found" page). An entry whose check fails is still written, with
the failure recorded, so the app shows it as unverified instead of silently dropping it.
Run python tools/build_snapshots.py afterwards so the manifest records the new catalogue hash.
"""
import datetime as dt
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURATION = ROOT / "tools" / "catalogue_curation.json"
OUT = ROOT / "data" / "catalogue" / "courses.json"
API = "https://learn.microsoft.com/api/catalog/?type=modules,learningPaths&locale=en-us"
UA = "Mozilla/5.0 (aivancin-catalogue-builder)"
VALIDITY_DAYS = 60


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def link_check(url, title):
    try:
        status, body = get(url)
    except Exception as exc:
        return {"ok": False, "status": getattr(exc, "code", None), "detail": type(exc).__name__}
    page_title = re.search(rb"<title>([^<]*)", body)
    page_title = page_title.group(1).decode("utf-8", "replace") if page_title else ""
    ok = status == 200 and "not found" not in page_title.lower() and title.lower()[:30] in page_title.lower()
    return {"ok": ok, "status": status, "page_title": page_title.strip()}


def main():
    curation = json.loads(CURATION.read_text(encoding="utf-8"))
    status, body = get(API, timeout=120)
    catalog = json.loads(body)
    items = {p["uid"]: p for p in catalog["modules"] + catalog["learningPaths"]}
    dwa_ids = {line.split(",", 1)[0] for line in (ROOT / "data/onet/dwas.csv").read_text(encoding="utf-8").splitlines()[1:]}
    today = dt.date.today().isoformat()
    missing = [c["uid"] for c in curation["items"] if c["uid"] not in items]
    unknown = sorted({d for c in curation["items"] for d in c.get("dwas", []) if d not in dwa_ids})
    if missing or unknown:
        sys.exit(f"curation does not match its sources: missing items {missing}, unknown activities {unknown}")

    def build(c):
        p = items[c["uid"]]
        url = p["url"].split("?")[0]
        return {"id": c["uid"], "title": p["title"], "provider": "Microsoft Learn", "url": url,
                "kind": "learning path" if p["type"] == "learningPath" else "module",
                "level": p.get("levels", []), "cost_rm": 0,
                "hours": round(p["duration_in_minutes"] / 60, 1),
                "hours_source": f"{p['duration_in_minutes']} minutes stated by the provider's catalog",
                "provider_last_modified": p.get("last_modified", "")[:10],
                "dwas": c.get("dwas", []), "tools": c.get("tools", []), "ai_skill": bool(c.get("ai_skill")),
                "verified_on": today, "link_check": {**link_check(url, p["title"]), "checked_on": today}}

    with ThreadPoolExecutor(max_workers=6) as ex:
        courses = list(ex.map(build, curation["items"]))
    courses.sort(key=lambda c: c["id"])
    out = {"catalogue_date": today, "validity_days": VALIDITY_DAYS, "curated_on": curation["curated_on"],
           "source": "Microsoft Learn catalog API (" + API + ")", "courses": courses, "requirements": []}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    bad = [c["id"] for c in courses if not c["link_check"]["ok"]]
    print(f"{len(courses)} entries, {len(courses) - len(bad)} passing link checks")
    for b in bad:
        print("  FAILED", b, next(c["link_check"] for c in courses if c["id"] == b))


if __name__ == "__main__":
    main()

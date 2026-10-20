"""Build data/catalogue/courses.json from tools/catalogue_curation.json, the Microsoft Learn catalog API
and Saylor Academy course pages.

Run from the repo root: python tools/build_catalogue.py
Each entry takes its title, URL and stated duration from the provider (the Microsoft Learn catalog API, or
the "Time: N hours" line of a Saylor course page, with outcomes from its syllabus) and gets a fresh
link check (HTTP 200 and not a "not found" page). An entry whose check fails is still written, with
the failure recorded, so the app shows it as unverified instead of silently dropping it.
Run python tools/build_snapshots.py afterwards so the manifest records the new catalogue hash.
"""
import datetime as dt
import json
import math
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURATION = ROOT / "tools" / "catalogue_curation.json"
OUT = ROOT / "data" / "catalogue" / "courses.json"
API = "https://learn.microsoft.com/api/catalog/?type=modules,learningPaths&locale=en-us"
SAYLOR = "https://learn.saylor.org/course/"
SAYLOR_ACCESS = "Free; enrolling needs a free Saylor Academy account"
UA = "Mozilla/5.0 (aivancin-catalogue-builder)"
VALIDITY_DAYS = 60


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _text(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def page_facts(html):
    """The provider's stated learning objectives and prerequisites, from the module or path page."""
    objectives, prereq = [], ""
    m = re.search(r"Learning objectives(.*?)(?:Prerequisites|<h2|$)", html, re.S)
    if m:
        objectives = [_text(li) for li in re.findall(r"<li[^>]*>(.*?)</li>", m.group(1), re.S)]
        objectives = [o for o in objectives if o][:8]
    m = re.search(r"Prerequisites\s*</h2>(.*?)(?:<h2|Get started with Azure|$)", html, re.S) or \
        re.search(r"Prerequisites(.*?)(?:<h2|Get started with Azure|$)", html, re.S)
    if m:
        prereq = _text(m.group(1))
        prereq = re.split(r"\s(?:Introduction|Exercise|Module assessment|Knowledge check)\s+min", prereq)[0][:400].strip()
    return objectives, prereq


def link_check(url, title):
    try:
        status, body = get(url)
    except Exception as exc:
        return {"ok": False, "status": getattr(exc, "code", None), "detail": type(exc).__name__}, "", ""
    html = body.decode("utf-8", "replace")
    page_title = re.search(r"<title>([^<]*)", html)
    page_title = page_title.group(1) if page_title else ""
    ok = status == 200 and "not found" not in page_title.lower() and title.lower()[:30] in page_title.lower()
    objectives, prereq = page_facts(html)
    return {"ok": ok, "status": status, "page_title": page_title.strip()}, objectives, prereq


def saylor_facts(code):
    """Title, stated hours and course learning outcomes from a Saylor course page and its syllabus."""
    url = SAYLOR + code
    try:
        status, body = get(url)
    except Exception as exc:
        return url, None, None, [], {"ok": False, "status": getattr(exc, "code", None), "detail": type(exc).__name__}
    html = body.decode("utf-8", "replace")
    page_title = re.search(r"<title>([^<]*)", html)
    page_title = page_title.group(1).strip() if page_title else ""
    title = re.search(r"Course:\s*" + code.upper() + r":\s*([^|]+)", page_title, re.I)
    hours = re.search(r"Time:\s*([\d.]+)\s*hours", _text(html))
    free = "Free Certificate" in _text(html)
    ok = status == 200 and bool(title) and bool(hours) and free
    objectives = []
    syl = re.search(r'href="(https://learn.saylor.org/mod/page/view.php\?id=\d+)"[^>]*>(?:(?!</a>).)*Course Syllabus', html, re.S)
    if syl:
        try:
            page = get(syl.group(1))[1].decode("utf-8", "replace")
            m = re.search(r"Course Learning Outcomes(.*?)(?:Throughout this course|</ul>|</ol>)", page, re.S)
            if m:
                # Some syllabi number outcomes in a <strong> marker ("0a"); the marker is not part of the outcome.
                items = [re.sub(r"<strong[^>]*>\s*\d+[a-z]?\s*</strong>", " ", li)
                         for li in re.findall(r"<li[^>]*>(.*?)</li>", m.group(1), re.S)]
                objectives = [o for o in (_text(li).rstrip(";.").removesuffix(" and").rstrip(";") for li in items) if o][:8]
        except Exception:
            objectives = []
    check = {"ok": ok and bool(objectives), "status": status, "page_title": page_title}
    return url, (f"{code.upper()}: {title.group(1).strip()}" if title else None),         (float(hours.group(1)) if hours else None), objectives, check


def practice_hours(provider_hours):
    """Curated estimate for the practice checkpoint: half the provider's time, at least 30 minutes, in half hours."""
    return max(0.5, math.ceil(provider_hours) / 2 if provider_hours >= 1 else 0.5)


def main():
    curation = json.loads(CURATION.read_text(encoding="utf-8"))
    status, body = get(API, timeout=120)
    catalog = json.loads(body)
    items = {p["uid"]: p for p in catalog["modules"] + catalog["learningPaths"]}
    dwa_ids = {line.split(",", 1)[0] for line in (ROOT / "data/onet/dwas.csv").read_text(encoding="utf-8").splitlines()[1:]}
    today = dt.date.today().isoformat()
    missing = [c["uid"] for c in curation["items"] if c.get("provider") != "saylor" and c["uid"] not in items]
    unknown = sorted({d for c in curation["items"] for d in c.get("dwas", []) if d not in dwa_ids})
    if missing or unknown:
        sys.exit(f"curation does not match its sources: missing items {missing}, unknown activities {unknown}")

    def route_fields(c, hours):
        if not c.get("checkpoint"):
            return {}
        return {"coverage": c["coverage"], "checkpoint": c["checkpoint"], "requires": c.get("requires", []),
                "practice_hours": practice_hours(hours),
                "practice_source": "curated estimate: half the provider's stated time, at least 30 minutes"}

    def build_saylor(c):
        url, title, hours, objectives, check = saylor_facts(c["code"])
        entry = {"id": c["uid"], "title": title or c["code"].upper(), "provider": "Saylor Academy", "url": url,
                 "kind": "course", "level": [], "cost_rm": 0, "hours": hours or 0.0,
                 "hours_source": f"{hours:g} hours stated on the provider's course page" if hours else "not stated",
                 "provider_last_modified": "", "access": SAYLOR_ACCESS,
                 "dwas": c.get("dwas", []), "tools": c.get("tools", []), "ai_skill": bool(c.get("ai_skill")),
                 "objectives": objectives, "provider_prerequisites": "None stated by the provider",
                 "verified_on": today, "link_check": {**check, "checked_on": today}}
        entry.update(route_fields(c, hours or 0.0))
        return entry

    def build(c):
        if c.get("provider") == "saylor":
            return build_saylor(c)
        p = items[c["uid"]]
        url = p["url"].split("?")[0]
        hours = round(p["duration_in_minutes"] / 60, 1)
        check, objectives, prereq = link_check(url, p["title"])
        entry = {"id": c["uid"], "title": p["title"], "provider": "Microsoft Learn", "url": url,
                 "kind": "learning path" if p["type"] == "learningPath" else "module",
                 "level": p.get("levels", []), "cost_rm": 0, "hours": hours,
                 "hours_source": f"{p['duration_in_minutes']} minutes stated by the provider's catalog",
                 "provider_last_modified": p.get("last_modified", "")[:10],
                 "dwas": c.get("dwas", []), "tools": c.get("tools", []), "ai_skill": bool(c.get("ai_skill")),
                 "objectives": objectives or [p.get("summary", "")], "provider_prerequisites": prereq,
                 "verified_on": today, "link_check": {**check, "checked_on": today}}
        entry.update(route_fields(c, hours))
        return entry

    with ThreadPoolExecutor(max_workers=6) as ex:
        courses = list(ex.map(build, curation["items"]))
    courses.sort(key=lambda c: c["id"])
    out = {"catalogue_date": today, "validity_days": VALIDITY_DAYS, "curated_on": curation["curated_on"],
           "source": "Microsoft Learn catalog API (" + API + ") and Saylor Academy course pages (" + SAYLOR + ")",
           "courses": courses, "requirements": []}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    bad = [c["id"] for c in courses if not c["link_check"]["ok"]]
    print(f"{len(courses)} entries, {len(courses) - len(bad)} passing link checks")
    for b in bad:
        print("  FAILED", b, next(c["link_check"] for c in courses if c["id"] == b))


if __name__ == "__main__":
    main()

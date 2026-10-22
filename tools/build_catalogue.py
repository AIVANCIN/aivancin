"""Build data/catalogue/courses.json from tools/catalogue_curation.json, the Microsoft Learn catalog API
and Saylor University course pages.

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
UNITS_API = "https://learn.microsoft.com/api/catalog/?type=units&locale=en-us&uid="
SAYLOR = "https://learn.saylor.org/course/"
SAYLOR_ACCESS = "Free; enrolling needs a free Saylor account"
UA = "Mozilla/5.0 (aivancin-catalogue-builder)"
VALIDITY_DAYS = 60


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _text(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def has_exercise(html):
    """Whether the module page lists an exercise unit (Microsoft Learn names them "Exercise - ...")."""
    return bool(re.search(r">\s*Exercise\b", html))


def access_text(product, exercise):
    """What the person needs for an entry whose provider practice uses a paid product (curated practice_access)."""
    if exercise:
        return (f"Free to read. The provider's exercises need {product} (paid, or a trial that may need a work "
                f"account); the checkpoint here works in a free spreadsheet or on paper instead.")
    return f"Free to read; its walkthroughs use {product}, which you do not need for the checkpoint here."


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
        # List items end with a semicolon so "processes" and "The ability" do not run together.
        prereq = _text(re.sub(r"</li>", "; </li>", m.group(1)))
        prereq = re.split(r"\s(?:Introduction|Exercise|Module assessment|Knowledge check)\s+min", prereq)[0][:400].strip().rstrip(";")
    return objectives, prereq


def link_check(url, title):
    try:
        status, body = get(url)
    except Exception as exc:
        return {"ok": False, "status": getattr(exc, "code", None), "detail": type(exc).__name__}, "", "", False, {}
    html = body.decode("utf-8", "replace")
    page_title = re.search(r"<title>([^<]*)", html)
    page_title = page_title.group(1) if page_title else ""
    ok = status == 200 and "not found" not in page_title.lower() and title.lower()[:30] in page_title.lower()
    objectives, prereq = page_facts(html)
    return {"ok": ok, "status": status, "page_title": page_title.strip()}, objectives, prereq, has_exercise(html), unit_links(html)


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


_course_pages = {}


def saylor_unit_facts(code, section, label):
    """One unit of a Saylor course (SPEC 11.7): its title and "should take you approximately N hours" from the course
    page's section, and its outcomes from the unit's Learning Outcomes page (else the provider's unit summary)."""
    url = f"{SAYLOR}{code}#section-{section}"
    try:
        if code not in _course_pages:
            _course_pages[code] = get(SAYLOR + code)
        status, body = _course_pages[code]
    except Exception as exc:
        return url, None, None, [], {"ok": False, "status": getattr(exc, "code", None), "detail": type(exc).__name__}
    html = body.decode("utf-8", "replace")
    parts = re.split(r'<li id="section-(\d+)"', html)
    block = next((parts[k + 1] for k in range(1, len(parts), 2) if parts[k] == str(section)), "")
    title = re.search(r'aria-label="(Unit \d+:[^"]+)"', block)
    title = title.group(1).replace("&amp;", "&").strip() if title else None
    text = _text(block)
    hours = re.search(r"should take you approximately ([\d.]+) hours", text)
    free = "Free Certificate" in _text(html)
    objectives = []
    link = re.search(r'href="(https://learn.saylor.org/mod/page/view.php\?id=\d+)"[^>]*>(?:(?!</a>).)*Learning Outcomes', block, re.S)
    if link:
        try:
            page = get(link.group(1))[1].decode("utf-8", "replace")
            main = _text(page[page.find('role="main"'):])
            # "Upon successful completion of this unit, you will be able to: [1] explain ...; [2] compare ..."
            m = re.search(r"will be able to:?\s*(.*?)\s*Back to", main)
            if m:
                objectives = [o.strip(" ;.") for o in re.split(r"\[\d+\]", m.group(1)) if o.strip(" ;.")][:8]
        except Exception:
            objectives = []
    if not objectives and title:
        summary = text.split(title, 1)[-1].split("Completing this unit")[0].strip()
        objectives = [summary[:400]] if summary else []
    ok = status == 200 and bool(title) and title.lower().startswith(label.lower()) and bool(hours) and free and bool(objectives)
    return url, title, (float(hours.group(1)) if hours else None), objectives, \
        {"ok": ok, "status": status, "page_title": title or ""}


def unit_facts(uids, batch=15):
    """Title and stated minutes of Microsoft Learn units, from the catalog API, by unit uid."""
    out = {}
    for i in range(0, len(uids), batch):
        _, body = get(UNITS_API + ",".join(uids[i:i + batch]), timeout=120)
        for u in json.loads(body).get("units", []):
            out[u["uid"]] = {"title": u["title"], "minutes": u.get("duration_in_minutes")}
    return out


def unit_links(html):
    """Each unit's link on the module page, by unit uid (a unit's page name need not match its uid)."""
    return dict(re.findall(r'data-unit-uid="([^"]+)".*?class="unit-title[^"]*"\s+href="([^"]+)"', html, re.S))


def parts(p, url, items, units, links):
    """A module's units, or a learning path's modules, in the provider's order with their stated minutes, so the plan
    can name the exact first task. A unit without a link on the module page points to the module page."""
    if p["type"] == "learningPath":
        return [{"title": items[m]["title"], "minutes": items[m].get("duration_in_minutes"), "url": items[m]["url"].split("?")[0],
                 "kind": "module"} for m in p.get("modules", []) if m in items]
    return [{**units[u], "url": url.rstrip("/") + "/" + links[u] if u in links else url, "kind": "unit"}
            for u in p.get("units", []) if u in units]


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
    missing = [c["uid"] for c in curation["items"] if c.get("provider") not in ("saylor", "saylor_unit") and c["uid"] not in items]
    unknown = sorted({d for c in curation["items"] for d in c.get("dwas", []) if d not in dwa_ids})
    if missing or unknown:
        sys.exit(f"curation does not match its sources: missing items {missing}, unknown activities {unknown}")
    units = unit_facts(sorted({u for c in curation["items"] if c["uid"] in items for u in items[c["uid"]].get("units", [])}))

    def route_fields(c, hours):
        if not c.get("checkpoint"):
            return {}
        return {"coverage": c["coverage"], "checkpoint": c["checkpoint"], "requires": c.get("requires", []),
                **({"setup": c["setup"]} if c.get("setup") else {}),
                **({"focus": c["focus"]} if c.get("focus") else {}),
                "practice_hours": practice_hours(hours),
                "practice_source": "curated estimate: half the provider's stated time, at least 30 minutes"}

    def build_saylor(c):
        url, title, hours, objectives, check = saylor_facts(c["code"])
        entry = {"id": c["uid"], "title": title or c["code"].upper(), "provider": "Saylor University", "url": url,
                 "kind": "course", "level": [], "cost_rm": 0, "hours": hours or 0.0,
                 "hours_source": f"{hours:g} hours stated on the provider's course page" if hours else "not stated",
                 "provider_last_modified": "", "access": SAYLOR_ACCESS,
                 "dwas": c.get("dwas", []), "tools": c.get("tools", []), "ai_skill": bool(c.get("ai_skill")),
                 "families": c.get("families", []),
                 "objectives": objectives, "provider_prerequisites": "None stated by the provider",
                 "verified_on": today, "link_check": {**check, "checked_on": today}}
        entry.update(route_fields(c, hours or 0.0))
        return entry

    def build_saylor_unit(c):
        url, title, hours, objectives, check = saylor_unit_facts(c["code"], c["section"], c["unit"])
        entry = {"id": c["uid"], "title": f"{c['code'].upper()} {title}" if title else f"{c['code'].upper()} {c['unit']}",
                 "provider": "Saylor University", "url": url, "kind": "course unit", "level": [], "cost_rm": 0,
                 "hours": hours or 0.0,
                 "hours_source": f"{hours:g} hours stated for this unit on the provider's course page" if hours else "not stated",
                 "provider_last_modified": "", "access": SAYLOR_ACCESS,
                 "dwas": c.get("dwas", []), "tools": c.get("tools", []), "ai_skill": bool(c.get("ai_skill")),
                 "families": c.get("families", []),
                 "objectives": objectives, "provider_prerequisites": "None stated by the provider",
                 "verified_on": today, "link_check": {**check, "checked_on": today}}
        entry.update(route_fields(c, hours or 0.0))
        return entry

    def build(c):
        if c.get("provider") == "saylor":
            return build_saylor(c)
        if c.get("provider") == "saylor_unit":
            return build_saylor_unit(c)
        p = items[c["uid"]]
        url = p["url"].split("?")[0]
        hours = round(p["duration_in_minutes"] / 60, 1)
        check, objectives, prereq, exercise, links = link_check(url, p["title"])
        entry = {"id": c["uid"], "title": p["title"], "provider": "Microsoft Learn", "url": url,
                 "kind": "learning path" if p["type"] == "learningPath" else "module",
                 "level": p.get("levels", []), "cost_rm": 0, "hours": hours,
                 "hours_source": f"{p['duration_in_minutes']} minutes stated by the provider's catalog",
                 "provider_last_modified": p.get("last_modified", "")[:10],
                 "dwas": c.get("dwas", []), "tools": c.get("tools", []), "ai_skill": bool(c.get("ai_skill")),
                 "families": c.get("families", []),
                 "objectives": objectives or [p.get("summary", "")], "provider_prerequisites": prereq,
                 "provider_exercise": exercise, "units": parts(p, url, items, units, links),
                 **({"access": access_text(c["practice_access"], exercise), "practice_product": c["practice_access"]}
                    if c.get("practice_access") else {}),
                 "verified_on": today, "link_check": {**check, "checked_on": today}}
        entry.update(route_fields(c, hours))
        return entry

    with ThreadPoolExecutor(max_workers=6) as ex:
        courses = list(ex.map(build, curation["items"]))
    courses.sort(key=lambda c: c["id"])
    out = {"catalogue_date": today, "validity_days": VALIDITY_DAYS, "curated_on": curation["curated_on"],
           "source": "Microsoft Learn catalog API (" + API + ") and Saylor University course pages (" + SAYLOR + ")",
           "courses": courses, "requirements": []}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    bad = [c["id"] for c in courses if not c["link_check"]["ok"]]
    print(f"{len(courses)} entries, {len(courses) - len(bad)} passing link checks")
    for b in bad:
        print("  FAILED", b, next(c["link_check"] for c in courses if c["id"] == b))


if __name__ == "__main__":
    main()

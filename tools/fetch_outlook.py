"""Fetch job-outlook facts from O*NET OnLine (CC BY 4.0) into .cache/onet-online/, one page per occupation.

Run from the repo root: python tools/fetch_outlook.py
Pages are cached, so an interrupted run resumes. BLS's own projection files block automated
download, so the BLS 2025-2035 projected-growth band is read from each O*NET OnLine summary page.
build_snapshots.py derives data/onet/outlook.csv from the cache.
"""
import csv
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "onet-online"
BASE = "https://www.onetonline.org/link/summary/"
BRIGHT_URL = "https://www.onetonline.org/find/bright/All_Bright_Outlook_Occupations.csv?b=0&fmt=csv"
UA = "Mozilla/5.0 (aivancin-snapshot-builder)"
GROWTH = re.compile(r"Projected growth \((\d{4}-\d{4})\) (.{0,80}?) Projected job openings \((\d{4}-\d{4})\) ([0-9,]+)")


def candidate_codes(data=ROOT / "data"):
    """Occupations the matcher can rank: Job Zone 3 or 4, not SOC 11 or 55, with activity links."""
    with open(data / "onet/job_zones.csv", encoding="utf-8") as f:
        jz = {r["code"]: r["job_zone"] for r in csv.DictReader(f)}
    with open(data / "onet/task_dwas.csv", encoding="utf-8") as f:
        linked = {r["code"] for r in csv.DictReader(f)}
    return sorted(c for c in linked if jz.get(c) in ("3", "4") and not c.startswith(("11-", "55-")))


def get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_page(code):
    dest = CACHE / f"{code}.html"
    if dest.exists():
        return code, "cached"
    for attempt in range(4):
        try:
            body = get(BASE + code)
            dest.write_bytes(body)
            return code, "fetched"
        except Exception as exc:
            if attempt == 3:
                return code, f"failed: {type(exc).__name__}"
            time.sleep(5 * (attempt + 1))


def parse_page(path):
    """(period, band, openings_period, openings) or None when the page has no projection."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", path.read_text(encoding="utf-8", errors="replace")))
    m = GROWTH.search(text)
    return m.groups() if m else None


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    bright = CACHE / "All_Bright_Outlook_Occupations.csv"
    if not bright.exists():
        bright.write_bytes(get(BRIGHT_URL))
    codes = candidate_codes()
    print(f"{len(codes)} candidate occupations", flush=True)
    done = 0
    # O*NET OnLine answers slowly (25-45 s a page); eight requests in flight is still under one every 3 s.
    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, status in ex.map(fetch_page, codes):
            done += 1
            if status.startswith("failed") or done % 25 == 0:
                print(done, code, status, flush=True)
    missing = [c for c in codes if not (CACHE / f"{c}.html").exists()]
    print(f"cached {len(codes) - len(missing)} of {len(codes)}; missing {missing[:10]}")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()

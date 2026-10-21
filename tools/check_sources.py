"""Source watch (SPEC 11.7, C14): is a newer version of a bundled source available upstream?

python tools/check_sources.py            # checks upstream, writes data/source_status.json
Then python tools/build_snapshots.py so the manifest records the status file. Each source is "current",
"newer available" or "unknown" (the check failed or the page changed shape), with the detail. Nothing here
changes a snapshot: a newer release still enters only through the snapshot builder and its adapters (section 1).
"""
import csv
import datetime as dt
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aivancin import aei  # noqa: E402

DATA = ROOT / "data"
OUT = DATA / "source_status.json"
UA = "Mozilla/5.0 (aivancin-source-watch)"
AEI_TREE = "https://huggingface.co/api/datasets/Anthropic/EconomicIndex/tree/main"
ONET_DB = "https://www.onetcenter.org/database.html"
ONET_BUNDLED = "31.0"
DOSM = {"dosm-wages-occupation": "https://storage.dosm.gov.my/labour/wages_occupation.csv",
        "dosm-sru-age": "https://storage.dosm.gov.my/labour/lfs_qtr_sru_age.csv",
        "dosm-sru-sex": "https://storage.dosm.gov.my/labour/lfs_qtr_sru_sex.csv"}
OUTLOOK_PAGE = "https://www.onetonline.org/link/summary/15-1232.00"


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def status(source, state, bundled, upstream, detail, url):
    return {"source": source, "status": state, "bundled": bundled, "upstream": upstream, "detail": detail, "url": url}


def check_aei(get):
    """Release folders on Hugging Face (release_YYYY_MM_DD) against the releases the app has adapters for."""
    bundled = aei.RELEASE_IDS[-1]
    try:
        tree = json.loads(get(AEI_TREE))
        found = sorted(re.sub(r"release_(\d{4})_(\d{2})_(\d{2})$", r"\1-\2-\3", e["path"]) for e in tree
                       if e.get("type") == "directory" and re.fullmatch(r"release_\d{4}_\d{2}_\d{2}", e.get("path", "")))
    except Exception as exc:
        return status("aei", "unknown", bundled, None, f"check failed ({type(exc).__name__})", AEI_TREE)
    if not found:
        return status("aei", "unknown", bundled, None, "no release folders found; the listing may have changed", AEI_TREE)
    newer = [r for r in found if r > bundled]
    if newer:
        return status("aei", "newer available", bundled, newer[-1],
                      f"release folders newer than {bundled}: {', '.join(newer)}; each needs an adapter in the snapshot builder",
                      AEI_TREE)
    return status("aei", "current", bundled, found[-1], f"latest release folder {found[-1]}", AEI_TREE)


def check_onet(get):
    try:
        versions = re.findall(r"O\*NET\s+(\d+\.\d+)\s+Database", get(ONET_DB))
    except Exception as exc:
        return status("onet", "unknown", ONET_BUNDLED, None, f"check failed ({type(exc).__name__})", ONET_DB)
    if not versions:
        return status("onet", "unknown", ONET_BUNDLED, None, "no database version found on the page", ONET_DB)
    latest = max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))
    newer = tuple(int(x) for x in latest.split(".")) > tuple(int(x) for x in ONET_BUNDLED.split("."))
    return status("onet", "newer available" if newer else "current", ONET_BUNDLED, latest,
                  f"O*NET {latest} is the latest database listed", ONET_DB)


def bundled_latest(source_id):
    path = {"dosm-wages-occupation": "dosm/wages_occupation.csv", "dosm-sru-age": "dosm/sru_age.csv",
            "dosm-sru-sex": "dosm/sru_sex.csv"}[source_id]
    with open(DATA / path, encoding="utf-8", newline="") as f:
        return max(r["date"] for r in csv.DictReader(f))


def check_dosm(get, source_id, bundled=None):
    url = DOSM[source_id]
    bundled = bundled or bundled_latest(source_id)
    try:
        rows = list(csv.DictReader(io.StringIO(get(url).lstrip("﻿"))))
        latest = max(r["date"] for r in rows)
    except Exception as exc:
        return status(source_id, "unknown", bundled, None, f"check failed ({type(exc).__name__})", url)
    if latest > bundled:
        return status(source_id, "newer available", bundled, latest, f"upstream has data to {latest}; bundled to {bundled}", url)
    return status(source_id, "current", bundled, latest, f"upstream and bundled both end {latest}", url)


def bundled_period():
    with open(DATA / "onet" / "outlook.csv", encoding="utf-8", newline="") as f:
        return next((r["growth_period"] for r in csv.DictReader(f) if r["growth_period"]), None)


def check_outlook(get, bundled=None):
    bundled = bundled or bundled_period()
    try:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", get(OUTLOOK_PAGE)))
        m = re.search(r"Projected growth \((\d{4}-\d{4})\)", text)
    except Exception as exc:
        return status("onet-online-outlook", "unknown", bundled, None, f"check failed ({type(exc).__name__})", OUTLOOK_PAGE)
    if not m:
        return status("onet-online-outlook", "unknown", bundled, None, "no projection period found on the page", OUTLOOK_PAGE)
    period = m.group(1)
    if period != bundled:
        return status("onet-online-outlook", "newer available", bundled, period,
                      f"O*NET OnLine now shows the {period} projection; bundled {bundled}", OUTLOOK_PAGE)
    return status("onet-online-outlook", "current", bundled, period, f"projection period {period}", OUTLOOK_PAGE)


def check_all(get=fetch, today=None):
    results = [check_aei(get), check_onet(get)] + [check_dosm(get, s) for s in DOSM] + [check_outlook(get)]
    return {"checked_on": (today or dt.date.today()).isoformat(), "script": "tools/check_sources.py", "sources": results}


def main():
    res = check_all(get=fetch)
    OUT.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8", newline="\n")
    for r in res["sources"]:
        print(f"{r['source']:24} {r['status']:16} {r['detail']}")


if __name__ == "__main__":
    main()

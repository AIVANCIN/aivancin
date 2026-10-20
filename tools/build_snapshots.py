"""Download pinned sources, write derived extracts, DWA vectors and the manifest.

Run from the repo root: python tools/build_snapshots.py
Raw downloads are cached in .cache/ (not committed); everything the app needs is
written to data/. The app itself never fetches anything.

Each source is derived independently. If one fails (for example an AEI release whose
schema no longer parses), its previous extract and manifest record stay in place, the
failure is reported and the script exits non-zero.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from aivancin.aei import adapt_release, RELEASES  # noqa: E402
from aivancin.encoder import Encoder  # noqa: E402
from aivancin.outlook import band_score  # noqa: E402
from fetch_outlook import CACHE as OUTLOOK_CACHE, candidate_codes, parse_page  # noqa: E402
from aivancin.snapshots import sha256_file  # noqa: E402

CACHE = ROOT / ".cache"
DATA = ROOT / "data"
csv.field_size_limit(1 << 30)

ONET_BASE = "https://www.onetcenter.org/dl_files/database/db_31_0_csv/"
ONET_COMMON = {
    "publisher": "U.S. Department of Labor, Employment and Training Administration (USDOL/ETA)",
    "release": "O*NET 31.0 (files dated 2026-08-25)",
    "licence": "CC BY 4.0",
}
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MODEL_BASE = f"https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/{MODEL_REVISION}/"
MODEL_COMMON = {
    "publisher": "sentence-transformers (Hugging Face)",
    "release": f"revision {MODEL_REVISION}",
    "licence": "Apache-2.0",
}
AEI_BASE = "https://huggingface.co/datasets/Anthropic/EconomicIndex/resolve/main/"
AEI_COMMON = {"publisher": "Anthropic (Anthropic Economic Index)", "licence": "CC BY"}
DOSM_BASE = "https://storage.dosm.gov.my/labour/"
DOSM_COMMON = {"publisher": "Department of Statistics Malaysia (OpenDOSM)", "licence": "CC BY 4.0"}

# Pinned hashes (Hugging Face LFS object ids for the model and AEI files); a different download is rejected.
SOURCES = [
    {"source_id": "onet31-dwas", "title": "O*NET 31.0 GWAs to IWAs to DWAs", "file": "gwas_to_iwas_to_dwas.csv", **ONET_COMMON},
    {"source_id": "onet31-occupations", "title": "O*NET 31.0 Occupation Data", "file": "occupation_data.csv", **ONET_COMMON},
    {"source_id": "onet31-tasks", "title": "O*NET 31.0 Task Statements", "file": "task_statements.csv", **ONET_COMMON},
    {"source_id": "onet31-task-dwas", "title": "O*NET 31.0 Tasks to DWAs", "file": "tasks_to_dwas.csv", **ONET_COMMON},
    {"source_id": "onet31-job-zones", "title": "O*NET 31.0 Job Zones", "file": "job_zones.csv", **ONET_COMMON},
    {"source_id": "onet31-education", "title": "O*NET 31.0 Education (Required Level of Education)", "file": "education.csv", **ONET_COMMON},
    {"source_id": "onet31-experience", "title": "O*NET 31.0 Training and Experience (Related Work Experience)",
     "file": "training_and_experience.csv", **ONET_COMMON},
    {"source_id": "onet31-emerging-tasks", "title": "O*NET 31.0 Emerging Tasks", "file": "emerging_tasks.csv", **ONET_COMMON},
    {"source_id": "onet31-software", "title": "O*NET 31.0 Software Skills (In Demand, Hot Technology)",
     "file": "software_skills.csv", **ONET_COMMON},
    {"source_id": "onet31-job-zone-reference", "title": "O*NET 31.0 Job Zone Reference", "file": "job_zone_reference.csv", **ONET_COMMON},
    {"source_id": "onet31-education-categories", "title": "O*NET 31.0 Education Categories", "file": "education_categories.csv", **ONET_COMMON},
    {"source_id": "onet31-experience-categories", "title": "O*NET 31.0 Training and Experience Categories",
     "file": "training_and_experience_categories.csv", **ONET_COMMON},
    {"source_id": "minilm-onnx", "title": "all-MiniLM-L6-v2 ONNX (quantized, AVX2)", "file": "onnx/model_quint8_avx2.onnx",
     "url": MODEL_BASE + "onnx/model_quint8_avx2.onnx",
     "sha256": "b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21", **MODEL_COMMON},
    {"source_id": "minilm-tokenizer", "title": "all-MiniLM-L6-v2 tokenizer", "file": "tokenizer.json",
     "url": MODEL_BASE + "tokenizer.json",
     "sha256": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037", **MODEL_COMMON},
    {"source_id": "dosm-wages-occupation", "title": "OpenDOSM wages_occupation (median monthly wages by MASCO major group)",
     "file": "wages_occupation.csv", "release": "annual series to 2025", **DOSM_COMMON},
    {"source_id": "dosm-sru-age", "title": "OpenDOSM lfs_qtr_sru_age (skill-related underemployment by age)",
     "file": "lfs_qtr_sru_age.csv", "release": "quarterly series", **DOSM_COMMON},
    {"source_id": "dosm-sru-sex", "title": "OpenDOSM lfs_qtr_sru_sex (skill-related underemployment by sex)",
     "file": "lfs_qtr_sru_sex.csv", "release": "quarterly series", **DOSM_COMMON},
    {"source_id": "nces-cip-soc", "title": "NCES CIP 2020 to SOC 2018 crosswalk", "file": "CIP2020_SOC2018_Crosswalk.xlsx",
     "url": "https://nces.ed.gov/ipeds/cipcode/Files/CIP2020_SOC2018_Crosswalk.xlsx",
     "publisher": "U.S. National Center for Education Statistics and Bureau of Labor Statistics",
     "release": "CIP 2020 to SOC 2018", "licence": "U.S. federal government work (public domain)"},
    {"source_id": "esco-onet-isco", "title": "ESCO to O*NET occupation crosswalk, with ISCO-08 codes from the ESCO API",
     "file": "ONET_(Occupations)_0_updated.csv",
     "url": "https://esco.ec.europa.eu/system/files/2023-08/ONET_%28Occupations%29_0_updated.csv",
     "publisher": "European Commission (ESCO) with U.S. DOL/ETA",
     "release": "ESCO v1.1.0 to O*NET-SOC 2019, version 1 (Sep 2022)",
     "licence": "Reuse under Commission Decision 2011/833/EU (attribution)"},
]
for r in RELEASES:
    SOURCES.append({"source_id": f"aei-{r['release']}", "title": f"Anthropic Economic Index release {r['release']} (Claude.ai)",
                    "file": r["file"], "url": AEI_BASE + r["path"], "sha256": r["sha256"],
                    "release": r["release"], "window_start": r["window_start"], "window_end": r["window_end"], **AEI_COMMON})
for s in SOURCES:
    if "url" not in s:
        s["url"] = (DOSM_BASE if s["source_id"].startswith("dosm") else ONET_BASE) + s["file"]

CHUNK = 1 << 22
UA = "Mozilla/5.0 (aivancin-snapshot-builder)"


def _get(url, headers=None, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def download(url):
    """Full GET, falling back to byte ranges when the server truncates the body."""
    try:
        status, headers, body = _get(url)
        expected = int(headers.get("Content-Length", len(body)))
        if len(body) == expected:
            return body
    except Exception as exc:  # truncated or timed-out reads are retried by range
        print(f"  full GET failed ({exc}); retrying by range", file=sys.stderr)
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers["Content-Length"])

    def part(start):
        end = min(start + CHUNK, total) - 1
        for _ in range(4):
            try:
                status, _, b = _get(url, {"Range": f"bytes={start}-{end}"})
                if status == 206 and len(b) == end - start + 1:
                    return start, b
            except Exception:
                pass
        raise RuntimeError(f"range {start}-{end} of {url} failed")

    with ThreadPoolExecutor(max_workers=6) as ex:
        parts = sorted(ex.map(part, range(0, total, CHUNK)))
    body = b"".join(b for _, b in parts)
    if len(body) != total:
        raise RuntimeError(f"{url}: got {len(body)} of {total} bytes")
    return body


def fetch(source):
    dest = CACHE / source["source_id"] / Path(source["file"]).name
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {source['url']}")
        dest.write_bytes(download(source["url"]))
        (dest.parent / "accessed.txt").write_text(dt.date.today().isoformat(), encoding="utf-8")
    digest = sha256_file(dest)
    if source.get("sha256") and digest != source["sha256"]:
        raise RuntimeError(f"{source['source_id']}: sha256 {digest} does not match pinned {source['sha256']}")
    accessed = (dest.parent / "accessed.txt").read_text(encoding="utf-8").strip()
    return dest, digest, accessed


def read_csv(path):
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(path)
    return len(rows)


def xlsx_sheet(path, name):
    """Rows of one worksheet as lists of strings (enough for the NCES crosswalk; no dependency)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    z = zipfile.ZipFile(path)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    sheet = next(s for s in wb.find("m:sheets", ns) if s.get("name") == name)
    sheet_path = "xl/" + target[sheet.get(f"{{{ns['r']}}}id")].lstrip("/").removeprefix("xl/")
    strings = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t"))
               for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
    rows = []
    for row in ET.fromstring(z.read(sheet_path)).find("m:sheetData", ns):
        vals = []
        for c in row:
            v = c.find("m:v", ns)
            vals.append("" if v is None else strings[int(v.text)] if c.get("t") == "s" else v.text)
        rows.append(vals)
    return rows


def modal_category(rows, scale):
    """Most common category per occupation for one O*NET scale, with its percentage (ties go to the lower category)."""
    by = defaultdict(list)
    for r in rows:
        if r["Scale ID"] == scale:
            by[r["O*NET-SOC Code"]].append((float(r["Data Value"]), -int(r["Category"]), r["Date"]))
    out = {}
    for code, vals in by.items():
        pct, neg_cat, date = max(vals)
        out[code] = (-neg_cat, f"{pct:.2f}", date)
    return out


def derive_onet(raw):
    """Keep only the columns the app uses, sorted for byte-stable output."""
    out = {}
    rows = read_csv(raw["onet31-dwas"])
    dwas = sorted({(r["DWA Element ID"], r["DWA Element Name"], r["IWA Element ID"], r["GWA Element Name"]) for r in rows})
    out["onet31-dwas"] = ("onet/dwas.csv", ["dwa_id", "dwa_name", "iwa_id", "gwa_name"], dwas)
    rows = read_csv(raw["onet31-occupations"])
    out["onet31-occupations"] = ("onet/occupations.csv", ["code", "title"],
                                 sorted((r["O*NET-SOC Code"], r["Title"]) for r in rows))
    rows = read_csv(raw["onet31-tasks"])
    out["onet31-tasks"] = ("onet/tasks.csv", ["code", "task_id", "task", "task_type"],
                           sorted(((r["O*NET-SOC Code"], r["Task ID"], r["Task"], r["Task Type"]) for r in rows),
                                  key=lambda t: (t[0], int(t[1]))))
    rows = read_csv(raw["onet31-task-dwas"])
    out["onet31-task-dwas"] = ("onet/task_dwas.csv", ["code", "task_id", "dwa_id"],
                               sorted({(r["O*NET-SOC Code"], r["Task ID"], r["DWA Element ID"]) for r in rows},
                                      key=lambda t: (t[0], int(t[1]), t[2])))
    rows = read_csv(raw["onet31-job-zones"])
    out["onet31-job-zones"] = ("onet/job_zones.csv", ["code", "job_zone"],
                               sorted((r["O*NET-SOC Code"], r["Job Zone"]) for r in rows))
    rl = modal_category(read_csv(raw["onet31-education"]), "RL")
    out["onet31-education"] = ("onet/education.csv", ["code", "rl_category", "rl_pct", "date"],
                               sorted((c, *v) for c, v in rl.items()))
    rw = modal_category(read_csv(raw["onet31-experience"]), "RW")
    out["onet31-experience"] = ("onet/experience.csv", ["code", "rw_category", "rw_pct", "date"],
                                sorted((c, *v) for c, v in rw.items()))
    rows = read_csv(raw["onet31-job-zone-reference"])
    out["onet31-job-zone-reference"] = ("onet/job_zone_reference.csv", ["job_zone", "name", "education", "experience"],
                                        sorted((r["Job Zone"], r["Name"], r["Education"], r["Experience"]) for r in rows))
    for sid, scale, rel in (("onet31-education-categories", "RL", "onet/education_categories.csv"),
                            ("onet31-experience-categories", "RW", "onet/experience_categories.csv")):
        rows = read_csv(raw[sid])
        out[sid] = (rel, ["category", "description"],
                    sorted(((r["Category"], r["Category Description"]) for r in rows if r["Scale ID"] == scale), key=lambda t: int(t[0])))
    rows = read_csv(raw["onet31-emerging-tasks"])
    out["onet31-emerging-tasks"] = ("onet/emerging_tasks.csv", ["code", "task", "category", "date"],
                                    sorted((r["O*NET-SOC Code"], r["Task"], r["Category"], r["Date"]) for r in rows))
    rows = read_csv(raw["onet31-software"])
    out["onet31-software"] = ("onet/in_demand_software.csv", ["code", "example", "commodity", "hot"],
                              sorted({(r["O*NET-SOC Code"], r["Workplace Example"], r["Element Name"], r["Hot Technology"])
                                      for r in rows if r["In Demand"] == "Y"}))
    return out


def derive_dosm(raw):
    out = {}
    rows = read_csv(raw["dosm-wages-occupation"])
    latest = max(r["date"] for r in rows)
    out["dosm-wages-occupation"] = ("dosm/wages_occupation.csv", ["date", "sex", "group", "recipients_k", "median_rm", "mean_rm"],
                                    sorted((r["date"], r["sex"], r["occupation"], r["recipients"], r["wages_median"], r["wages_mean"])
                                           for r in rows if r["date"] == latest))
    for sid, col in (("dosm-sru-age", "age"), ("dosm-sru-sex", "sex")):
        rows = read_csv(raw[sid])
        latest = max(r["date"] for r in rows)
        out[sid] = (f"dosm/{sid.removeprefix('dosm-').replace('-', '_')}.csv", ["date", "variable", col, "value"],
                    sorted((r["date"], r["variable"], r[col], r["sru"]) for r in rows if r["date"] == latest))
    return out


def derive_cip_soc(raw):
    rows = xlsx_sheet(raw["nces-cip-soc"], "CIP-SOC")
    header, body = rows[0], rows[1:]
    i = {h: n for n, h in enumerate(header)}
    keep = sorted({(r[i["CIP2020Code"]], r[i["CIP2020Title"]].rstrip("."), r[i["SOC2018Code"]]) for r in body
                   if len(r) > i["SOC2018Code"] and r[i["SOC2018Code"]] not in ("", "99-9999")})
    return {"nces-cip-soc": ("crosswalk/cip_soc.csv", ["cip", "cip_title", "soc"], keep)}


MATCH_TIERS = ("exactISCO", "exactMatch", "closeMatch", "narrowMatch", "broadMatch")


def esco_isco(uri):
    """ISCO-08 unit group of one ESCO occupation, via the ESCO API; cached on disk."""
    cache = CACHE / "esco-api" / (uri.rstrip("/").rsplit("/", 1)[-1] + ".json")
    if not cache.exists():
        url = "https://ec.europa.eu/esco/api/resource/occupation?" + urllib.parse.urlencode({"uri": uri, "language": "en"})
        for attempt in range(5):
            try:
                _, _, body = _get(url, timeout=60)
                break
            except urllib.error.HTTPError as exc:
                # A retired concept (404) or one the API keeps failing on stays unresolved rather than guessed.
                if exc.code == 404 or (exc.code >= 500 and attempt == 4):
                    body = json.dumps({"unresolved": exc.code}).encode()
                    break
                if attempt == 4:
                    raise
            except Exception:
                if attempt == 4:
                    raise
            time.sleep(2 ** attempt)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(body)
    d = json.loads(cache.read_text(encoding="utf-8"))
    # An ESCO occupation code starts with its ISCO-08 unit group (3512.1 is under 3512); narrower
    # occupations link to a parent occupation rather than directly to the ISCO group.
    code = str(d.get("code") or "").split(".")[0]
    if len(code) == 4 and code.isdigit():
        return code
    groups = d.get("_links", {}).get("broaderIscoGroup") or []
    return groups[0]["code"] if groups else None


def derive_isco(raw):
    """O*NET-SOC to ISCO-08 major group via the ESCO crosswalk, using the strongest match type present."""
    text = raw["esco-onet-isco"].read_text(encoding="utf-8-sig").splitlines()
    start = next(n for n, line in enumerate(text) if line.startswith("O*NET Id,"))
    rows = list(csv.DictReader(io.StringIO("\n".join(text[start:]))))
    by_code = defaultdict(lambda: defaultdict(set))
    for r in rows:
        by_code[r["O*NET Id"]][r["Type of Match"]].add(r["ESCO or ISCO URI"])
    # Only the strongest match tier present for each occupation is used, so only those concepts are looked up.
    top_tier = {code: next(t for t in MATCH_TIERS if tiers.get(t)) for code, tiers in by_code.items()}
    uris = sorted({u for code, tiers in by_code.items() for u in tiers[top_tier[code]]})
    isco = {}

    def resolve(u):
        if "/isco/" in u:
            return u, u.rsplit("/", 1)[-1].lstrip("C")
        return u, esco_isco(u)

    with ThreadPoolExecutor(max_workers=4) as ex:
        for u, code in ex.map(resolve, uris):
            isco[u] = code
    out = []
    for code, tiers in sorted(by_code.items()):
        tier = top_tier[code]
        codes = sorted({isco[u] for u in tiers[tier] if isco.get(u)})
        if not codes:
            out.append((code, "", f"unmapped ({tier} concepts unresolved)", ""))
            continue
        majors = Counter(c[0] for c in codes)
        (top, n), *rest = majors.most_common()
        if rest and rest[0][1] == n:
            major, kind = "", f"unmapped ({tier} matches disagree)"
        else:
            major, kind = top, f"ESCO {tier}" + ("" if not rest else f", {n} of {len(codes)} unit groups")
        out.append((code, major, kind, " ".join(codes)))
    return {"esco-onet-isco": ("crosswalk/onet_isco.csv", ["code", "isco_major", "mapping", "isco_unit_groups"], out)}


def derive_aei(raw, onet_tasks_path):
    out, failures = {}, {}
    task_rows = read_csv(onet_tasks_path)
    for r in RELEASES:
        sid = f"aei-{r['release']}"
        try:
            rows, stats = adapt_release(r, raw[sid], task_rows)
        except Exception as exc:
            failures[sid] = f"{type(exc).__name__}: {exc}"
            continue
        out[sid] = (f"aei/{r['release']}.csv", ["level", "key", "share", "classified", "automation"], rows, stats)
    return out, failures


def derive_outlook():
    """Job outlook from the cached O*NET OnLine pages (tools/fetch_outlook.py) and its Bright Outlook list."""
    bright_path = OUTLOOK_CACHE / "All_Bright_Outlook_Occupations.csv"
    codes = candidate_codes(DATA)
    pages = {c: OUTLOOK_CACHE / f"{c}.html" for c in codes}
    missing = [c for c, path in pages.items() if not path.exists()]
    if missing or not bright_path.exists():
        raise RuntimeError(f"outlook cache incomplete ({len(missing)} pages missing); run tools/fetch_outlook.py")
    bright = {r["Code"]: r["Categories"] for r in read_csv(bright_path)}
    rows, digest = [], hashlib.sha256()
    for code in codes:
        digest.update(code.encode() + bytes.fromhex(sha256_file(pages[code])))
        parsed = parse_page(pages[code])
        period, band, open_period, openings = parsed if parsed else ("", "", "", "")
        band_score(band)  # an unknown band label fails the build
        rows.append((code, period, band, open_period, openings.replace(",", ""), bright.get(code, "")))
    digest.update(bytes.fromhex(sha256_file(bright_path)))
    accessed = max(dt.date.fromtimestamp(p.stat().st_mtime) for p in [*pages.values(), bright_path]).isoformat()
    rec = {"source_id": "onet-online-outlook", "title": "O*NET OnLine job outlook: BLS projected growth and openings, "
           "Bright Outlook", "publisher": "U.S. Department of Labor, Employment and Training Administration (O*NET OnLine); "
           "projections by the U.S. Bureau of Labor Statistics", "url": "https://www.onetonline.org/find/bright",
           "release": "BLS projections 2025-2035", "licence": "CC BY 4.0", "access_date": accessed,
           "original_sha256": digest.hexdigest()}
    return rec, ("onet/outlook.csv", ["code", "growth_period", "growth_band", "openings_period", "openings", "bright_outlook"], rows)


def main():
    old = {}
    if (DATA / "manifest.json").exists():
        old = {r["source_id"]: r for r in json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))["snapshots"]}
    raw, records, failures = {}, {}, {}
    for s in SOURCES:
        try:
            path, digest, accessed = fetch(s)
        except Exception as exc:
            failures[s["source_id"]] = f"fetch: {exc}"
            continue
        raw[s["source_id"]] = path
        records[s["source_id"]] = ({k: s[k] for k in ("source_id", "title", "publisher", "url", "release", "licence")}
                                   | {k: s[k] for k in ("window_start", "window_end") if k in s}
                                   | {"access_date": accessed, "original_sha256": digest})

    derived = derive_onet(raw)
    derived.update(derive_dosm(raw))
    derived.update(derive_cip_soc(raw))
    derived.update(derive_isco(raw))
    tasks_path = DATA / "onet/tasks.csv"
    write_csv(tasks_path, *derived["onet31-tasks"][1:])
    aei, aei_failures = derive_aei(raw, tasks_path)
    failures.update(aei_failures)

    model_dir = DATA / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(raw["minilm-onnx"], model_dir / "model.onnx")
    shutil.copyfile(raw["minilm-tokenizer"], model_dir / "tokenizer.json")

    final = []
    for s in SOURCES:
        sid = s["source_id"]
        if sid not in records or sid in failures:
            if sid in old:
                final.append(old[sid])  # last good snapshot stays active
            continue
        rec = records[sid]
        if sid in derived:
            rel, header, rows = derived[sid]
            rec["derived_rows"] = write_csv(DATA / rel, header, rows)
        elif sid in aei:
            rel, header, rows, stats = aei[sid]
            rec["derived_rows"] = write_csv(DATA / rel, header, rows)
            rec["join"] = stats
        else:
            rel = "model/model.onnx" if sid == "minilm-onnx" else "model/tokenizer.json"
            rec["derived_rows"] = None
        rec["derived_path"] = rel
        rec["derived_sha256"] = sha256_file(DATA / rel)
        final.append(rec)

    try:
        rec, (rel, header, rows) = derive_outlook()
        rec["derived_rows"] = write_csv(DATA / rel, header, rows)
        rec["derived_path"] = rel
        rec["derived_sha256"] = sha256_file(DATA / rel)
        final.append(rec)
    except Exception as exc:
        failures["onet-online-outlook"] = str(exc)
        if "onet-online-outlook" in old:
            final.append(old["onet-online-outlook"])

    bt = DATA / "forecast" / "a18_backtest.json"
    if bt.exists():
        final.append({"source_id": "aivancin-backtest", "title": "AIVANCIN skill-outlook backtest (A18): error of the "
                      "historical-average projection on the 2026-06-26 release",
                      "publisher": "AIVANCIN (team DOVUN), computed from the bundled AEI and O*NET snapshots",
                      "url": "tools/checks/a18_backtest.py", "release": json.loads(bt.read_text(encoding="utf-8"))["run_date"],
                      "licence": "Derived from CC BY sources", "access_date": dt.date.today().isoformat(),
                      "original_sha256": sha256_file(bt), "derived_rows": None,
                      "derived_path": "forecast/a18_backtest.json", "derived_sha256": sha256_file(bt)})

    routes = DATA / "routes" / "destinations.json"
    if routes.exists():
        final.append({"source_id": "aivancin-routes", "title": "AIVANCIN supported destinations and their sourced requirements",
                      "publisher": "AIVANCIN (team DOVUN), from O*NET 31.0 and the curated catalogue",
                      "url": "tools/destinations_curation.json", "release": json.loads(routes.read_text(encoding="utf-8"))["built_on"],
                      "licence": "Derived from CC BY sources", "access_date": dt.date.today().isoformat(),
                      "original_sha256": sha256_file(routes), "derived_rows": None,
                      "derived_path": "routes/destinations.json", "derived_sha256": sha256_file(routes)})

    cat = DATA / "catalogue" / "courses.json"
    if cat.exists():
        final.append({"source_id": "aivancin-catalogue", "title": "AIVANCIN curated course catalogue",
                      "publisher": "AIVANCIN (team DOVUN)", "url": "data/catalogue/courses.json",
                      "release": json.loads(cat.read_text(encoding="utf-8"))["catalogue_date"],
                      "licence": "Links to third-party pages; see each entry",
                      "access_date": dt.date.today().isoformat(), "original_sha256": sha256_file(cat),
                      "derived_rows": len(json.loads(cat.read_text(encoding="utf-8"))["courses"]),
                      "derived_path": "catalogue/courses.json", "derived_sha256": sha256_file(cat)})

    # Cited figures that are not bundled files.
    final.append({"source_id": "aei-report-2511.15080", "title": "Anthropic Economic Index report: Uneven geographic "
                  "and enterprise AI adoption (directive use 27% to 39% in eight months)",
                  "publisher": "Anthropic (arXiv 2511.15080)", "url": "https://arxiv.org/abs/2511.15080",
                  "release": "2025-11-19", "licence": "cited figure", "access_date": "2026-10-20", "kind": "reference"})

    # Precomputed, L2-normalised DWA name vectors, tied to the model that made them.
    dwa_path = DATA / derived["onet31-dwas"][0]
    dwa_rows = read_csv(dwa_path)
    enc = Encoder(model_dir / "model.onnx", model_dir / "tokenizer.json")
    vectors = enc.encode([r["dwa_name"] for r in dwa_rows])
    vec_dir = DATA / "vectors"
    vec_dir.mkdir(parents=True, exist_ok=True)
    import numpy as np
    np.save(vec_dir / "dwa_vectors.npy", vectors.astype(np.float32), allow_pickle=False)
    meta = {"model_sha256": enc.model_sha256, "tokenizer_sha256": enc.tokenizer_sha256,
            "dwa_extract_sha256": sha256_file(dwa_path), "count": int(vectors.shape[0]), "dim": int(vectors.shape[1]),
            "vectors_sha256": sha256_file(vec_dir / "dwa_vectors.npy")}
    (vec_dir / "dwa_vectors.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")

    manifest = {"snapshots": final}
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({r["source_id"]: r.get("derived_rows") for r in final}, indent=2))
    for r in final:
        if "join" in r:
            print(r["source_id"], json.dumps(r["join"]))
    print(f"vectors: {meta['count']} x {meta['dim']}")
    if failures:
        print("FAILED (previous snapshot kept where one existed):", json.dumps(failures, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

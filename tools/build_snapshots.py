"""Download pinned sources, write derived extracts, DWA vectors and the manifest.

Run from the repo root: python tools/build_snapshots.py
Raw downloads are cached in .cache/ (not committed); everything the app needs is
written to data/. The app itself never fetches anything.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import shutil
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aivancin.encoder import Encoder  # noqa: E402
from aivancin.snapshots import sha256_file  # noqa: E402

CACHE = ROOT / ".cache"
DATA = ROOT / "data"

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

# Pinned hashes for the model files; a different download is rejected.
SOURCES = [
    {"source_id": "onet31-dwas", "title": "O*NET 31.0 GWAs to IWAs to DWAs", "file": "gwas_to_iwas_to_dwas.csv", **ONET_COMMON},
    {"source_id": "onet31-occupations", "title": "O*NET 31.0 Occupation Data", "file": "occupation_data.csv", **ONET_COMMON},
    {"source_id": "onet31-tasks", "title": "O*NET 31.0 Task Statements", "file": "task_statements.csv", **ONET_COMMON},
    {"source_id": "onet31-task-dwas", "title": "O*NET 31.0 Tasks to DWAs", "file": "tasks_to_dwas.csv", **ONET_COMMON},
    {"source_id": "onet31-job-zones", "title": "O*NET 31.0 Job Zones", "file": "job_zones.csv", **ONET_COMMON},
    {"source_id": "minilm-onnx", "title": "all-MiniLM-L6-v2 ONNX (quantized, AVX2)", "file": "onnx/model_quint8_avx2.onnx",
     "sha256": "b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21", **MODEL_COMMON},
    {"source_id": "minilm-tokenizer", "title": "all-MiniLM-L6-v2 tokenizer", "file": "tokenizer.json",
     "sha256": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037", **MODEL_COMMON},
]
for s in SOURCES:
    s["url"] = (MODEL_BASE if s["source_id"].startswith("minilm") else ONET_BASE) + s["file"]

CHUNK = 262144


def _get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "aivancin-snapshot-builder", **(headers or {})})
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
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "aivancin-snapshot-builder"})
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
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


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
    return out


def main():
    raw, records = {}, []
    for s in SOURCES:
        path, digest, accessed = fetch(s)
        raw[s["source_id"]] = path
        records.append({k: s[k] for k in ("source_id", "title", "publisher", "url", "release", "licence")}
                       | {"access_date": accessed, "original_sha256": digest})

    derived = derive_onet(raw)
    model_dir = DATA / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(raw["minilm-onnx"], model_dir / "model.onnx")
    shutil.copyfile(raw["minilm-tokenizer"], model_dir / "tokenizer.json")

    for rec in records:
        sid = rec["source_id"]
        if sid in derived:
            rel, header, rows = derived[sid]
            rec["derived_rows"] = write_csv(DATA / rel, header, rows)
        else:
            rel = "model/model.onnx" if sid == "minilm-onnx" else "model/tokenizer.json"
            rec["derived_rows"] = None
        rec["derived_path"] = rel
        rec["derived_sha256"] = sha256_file(DATA / rel)

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

    manifest = {"snapshots": records}
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({r["source_id"]: r["derived_rows"] for r in records}, indent=2))
    print(f"vectors: {meta['count']} x {meta['dim']}")


if __name__ == "__main__":
    main()

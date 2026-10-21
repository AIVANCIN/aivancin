"""Training pairs for the encoder fine-tuning (SPEC 8 and 11.8), from the bundled O*NET 31.0 files only.

python tools/finetune/prepare_pairs.py
Writes tools/finetune/build/: pairs_train.jsonl and pairs_val.jsonl ({"anchor": task statement, "positive": activity
name, "dwa_id": ...}), dwa_names.json (every activity, for the in-domain check), pairs_manifest.json (counts and
hashes), and runpod_bundle.zip (these files plus train.py and requirements-runpod.txt) to upload to the pod.

A pair links an O*NET task statement to an activity it is linked to; statements are deduplicated by normalised text.
About 5% of statements (chosen by a hash of the text, so the split is fixed) are kept out of training for an
in-domain recall check. The held-out casual set (tests/acceptance/heldout_casual.json) is never bundled: the gate
runs locally after the pod's models come back.
"""
import csv
import hashlib
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aivancin.aei import norm_task  # noqa: E402
from aivancin.snapshots import sha256_file  # noqa: E402

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
DATA = ROOT / "data" / "onet"
VAL_PERCENT = 5


def rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def in_val(key):
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % 100 < VAL_PERCENT


def write_jsonl(path, items):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False, sort_keys=True) + "\n")


def main(build=BUILD, manifest_dir=HERE):
    build.mkdir(parents=True, exist_ok=True)
    names = {r["dwa_id"]: r["dwa_name"] for r in rows(DATA / "dwas.csv")}
    text = {(r["code"], r["task_id"]): r["task"].strip() for r in rows(DATA / "tasks.csv")}
    links = defaultdict(set)
    shown = {}
    n_links = 0
    for r in rows(DATA / "task_dwas.csv"):
        t = text.get((r["code"], r["task_id"]))
        if not t or r["dwa_id"] not in names:
            continue
        n_links += 1
        key = norm_task(t)
        shown.setdefault(key, t)
        links[key].add(r["dwa_id"])
    train, val = [], []
    for key in sorted(links):
        for d in sorted(links[key]):
            pair = {"anchor": shown[key], "positive": names[d], "dwa_id": d}
            (val if in_val(key) else train).append(pair)
    write_jsonl(build / "pairs_train.jsonl", train)
    write_jsonl(build / "pairs_val.jsonl", val)
    (build / "dwa_names.json").write_text(json.dumps(names, indent=0, ensure_ascii=False, sort_keys=True) + "\n",
                                          encoding="utf-8", newline="\n")
    manifest = {
        "source_links": n_links, "statements": len(links),
        "train_pairs": len(train), "val_pairs": len(val), "val_statements": sum(1 for k in links if in_val(k)),
        "val_rule": f"sha256(normalised statement) mod 100 < {VAL_PERCENT}",
        "inputs": {f: sha256_file(DATA / f) for f in ("dwas.csv", "tasks.csv", "task_dwas.csv")},
        "outputs": {f: sha256_file(build / f) for f in ("pairs_train.jsonl", "pairs_val.jsonl", "dwa_names.json")},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
    }
    (manifest_dir / "pairs_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8", newline="\n")
    bundle = build / "runpod_bundle.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for f in ("pairs_train.jsonl", "pairs_val.jsonl", "dwa_names.json"):
            z.write(build / f, f)
        for f in ("train.py", "requirements-runpod.txt"):
            z.write(HERE / f, f)
        z.write(manifest_dir / "pairs_manifest.json", "pairs_manifest.json")
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("inputs", "outputs")}, indent=1))
    print(f"bundle: {bundle} ({bundle.stat().st_size // 1024} KB), sha256 {sha256_file(bundle)}")
    return manifest


if __name__ == "__main__":
    main()

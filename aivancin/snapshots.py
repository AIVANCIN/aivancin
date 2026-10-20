"""Bundled, hashed data snapshots. Loading verifies every derived file against the manifest."""
import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class SnapshotError(RuntimeError):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@dataclass
class Onet:
    dwa_ids: list
    dwa_names: dict
    occupations: dict
    job_zones: dict
    tasks: dict = field(repr=False)
    occupation_dwas: dict = field(repr=False)


@dataclass
class Snapshots:
    data_dir: Path
    manifest: dict
    onet: Onet

    def source(self, source_id):
        for rec in self.manifest["snapshots"]:
            if rec["source_id"] == source_id:
                return rec
        raise KeyError(source_id)


def load(data_dir=DEFAULT_DATA_DIR):
    data_dir = Path(data_dir)
    manifest_path = data_dir / "manifest.json"
    if not manifest_path.exists():
        raise SnapshotError(f"no manifest at {manifest_path}; run tools/build_snapshots.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = {}
    for rec in manifest["snapshots"]:
        p = data_dir / rec["derived_path"]
        # The model files are checked by the encoder loader, which reports instead of failing the app.
        if rec["source_id"].startswith("minilm"):
            continue
        if not p.exists():
            raise SnapshotError(f"{rec['source_id']}: missing {rec['derived_path']}")
        if sha256_file(p) != rec["derived_sha256"]:
            raise SnapshotError(f"{rec['source_id']}: {rec['derived_path']} does not match its manifest hash")
        paths[rec["source_id"]] = p

    dwas = _rows(paths["onet31-dwas"])
    tasks = {}
    for r in _rows(paths["onet31-tasks"]):
        tasks[(r["code"], r["task_id"])] = {"task": r["task"], "type": r["task_type"]}
    occupation_dwas = defaultdict(list)
    for r in _rows(paths["onet31-task-dwas"]):
        occupation_dwas[r["code"]].append((r["task_id"], r["dwa_id"]))
    onet = Onet(
        dwa_ids=[r["dwa_id"] for r in dwas],
        dwa_names={r["dwa_id"]: r["dwa_name"] for r in dwas},
        occupations={r["code"]: r["title"] for r in _rows(paths["onet31-occupations"])},
        job_zones={r["code"]: int(r["job_zone"]) for r in _rows(paths["onet31-job-zones"])},
        tasks=tasks,
        occupation_dwas=dict(occupation_dwas),
    )
    return Snapshots(data_dir=data_dir, manifest=manifest, onet=onet)

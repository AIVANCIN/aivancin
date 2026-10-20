"""Bundled, hashed data snapshots. Loading verifies every derived file against the manifest."""
import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import aei

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# DOSM wage groups by MASCO 2020 major group (MASCO is ISCO-08 based).
MASCO_GROUPS = {
    "1": ("managers", "Managers"),
    "2": ("professionals", "Professionals"),
    "3": ("technicians", "Technicians and associate professionals"),
    "4": ("clerical", "Clerical support workers"),
    "5": ("service_sales", "Service and sales workers"),
    "6": ("agriculture", "Skilled agricultural, forestry and fishery workers"),
    "7": ("craft", "Craft and related trades workers"),
    "8": ("operators", "Plant and machine operators and assemblers"),
    "9": ("elementary", "Elementary occupations"),
}


def skill_level(major):
    """DOSM's skill-related underemployment grouping of MASCO major groups."""
    if major in ("1", "2", "3"):
        return "skilled"
    if major in ("4", "5", "6", "7", "8"):
        return "semi-skilled"
    if major == "9":
        return "low-skilled"
    return None


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
    education: dict = field(default_factory=dict, repr=False)
    experience: dict = field(default_factory=dict, repr=False)
    emerging: dict = field(default_factory=dict, repr=False)
    software: dict = field(default_factory=dict, repr=False)


@dataclass
class Snapshots:
    data_dir: Path
    manifest: dict
    onet: Onet
    isco: dict = field(default_factory=dict, repr=False)
    cip: dict = field(default_factory=dict, repr=False)
    wages: dict = field(default_factory=dict, repr=False)
    sru: dict = field(default_factory=dict, repr=False)
    evidence: object = None
    catalogue: dict = field(default_factory=dict, repr=False)
    outlook: dict = field(default_factory=dict, repr=False)
    backtest: dict = field(default_factory=dict, repr=False)
    routes: dict = field(default_factory=dict, repr=False)
    rejected: dict = field(default_factory=dict)

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
    paths, rejected = {}, {}
    for rec in manifest["snapshots"]:
        if "derived_path" not in rec:
            continue  # a cited reference, not a bundled file
        p = data_dir / rec["derived_path"]
        # The model files are checked by the encoder loader, which reports instead of failing the app.
        if rec["source_id"].startswith("minilm"):
            continue
        problem = None
        if not p.exists():
            problem = f"missing {rec['derived_path']}"
        elif sha256_file(p) != rec["derived_sha256"]:
            problem = f"{rec['derived_path']} does not match its manifest hash"
        if problem:
            # An AEI release that fails its check is dropped; the other releases stay active.
            if rec["source_id"].startswith("aei-"):
                rejected[rec["source_id"]] = problem
                continue
            raise SnapshotError(f"{rec['source_id']}: {problem}")
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
    snaps = Snapshots(data_dir=data_dir, manifest=manifest, onet=onet, rejected=rejected)
    if "onet31-education" not in paths:
        return snaps  # intake-only snapshot set (older manifests and focused test fixtures)

    onet.education = {r["code"]: (int(r["rl_category"]), float(r["rl_pct"]), r["date"]) for r in _rows(paths["onet31-education"])}
    onet.experience = {r["code"]: (int(r["rw_category"]), float(r["rw_pct"]), r["date"]) for r in _rows(paths["onet31-experience"])}
    emerging = defaultdict(list)
    for r in _rows(paths["onet31-emerging-tasks"]):
        emerging[r["code"]].append({"task": r["task"], "category": r["category"], "date": r["date"]})
    onet.emerging = dict(emerging)
    software = defaultdict(list)
    for r in _rows(paths["onet31-software"]):
        software[r["code"]].append({"name": r["example"], "commodity": r["commodity"], "hot": r["hot"] == "Y"})
    onet.software = dict(software)

    snaps.isco = {r["code"]: {"major": r["isco_major"] or None, "mapping": r["mapping"], "units": r["isco_unit_groups"].split()}
                  for r in _rows(paths["esco-onet-isco"])}
    cip = {}
    for r in _rows(paths["nces-cip-soc"]):
        cip.setdefault(r["cip"], {"cip": r["cip"], "title": r["cip_title"], "soc": set()})["soc"].add(r["soc"])
    snaps.cip = cip
    wages = {}
    for r in _rows(paths["dosm-wages-occupation"]):
        wages[(r["group"], r["sex"])] = {"median": int(float(r["median_rm"])), "date": r["date"]}
    snaps.wages = wages
    snaps.sru = {"age": {(r["variable"], r["age"]): (float(r["value"]), r["date"]) for r in _rows(paths["dosm-sru-age"])},
                 "sex": {(r["variable"], r["sex"]): (float(r["value"]), r["date"]) for r in _rows(paths["dosm-sru-sex"])}}

    releases = []
    for rid in aei.RELEASE_IDS:
        sid = f"aei-{rid}"
        if sid in paths:
            try:
                t, o = aei.load_release(paths[sid])
            except (KeyError, ValueError) as exc:
                rejected[sid] = f"unreadable ({type(exc).__name__})"
                continue
            releases.append((rid, t, o))
    snaps.evidence = aei.Evidence(releases)
    if "onet-online-outlook" in paths:
        snaps.outlook = {r["code"]: {"period": r["growth_period"], "band": r["growth_band"],
                                     "openings": int(r["openings"]) if r["openings"] else None,
                                     "openings_period": r["openings_period"],
                                     "bright": [x.strip() for x in r["bright_outlook"].split(";") if x.strip()]}
                         for r in _rows(paths["onet-online-outlook"])}
    if "aivancin-routes" in paths:
        snaps.routes = json.loads(paths["aivancin-routes"].read_text(encoding="utf-8"))
    if "aivancin-backtest" in paths:
        snaps.backtest = json.loads(paths["aivancin-backtest"].read_text(encoding="utf-8"))
    if "aivancin-catalogue" in paths:
        snaps.catalogue = json.loads(paths["aivancin-catalogue"].read_text(encoding="utf-8"))
    return snaps

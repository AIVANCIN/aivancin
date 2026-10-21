"""The shipped fine-tuned encoder (SPEC 11.8): the files match their record and the manifest, and the snapshot builder
keeps them only while they do, instead of silently restoring the base model or shipping an unrecorded one."""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from aivancin.snapshots import sha256_file

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "data" / "model"
spec = importlib.util.spec_from_file_location("build_snapshots", ROOT / "tools" / "build_snapshots.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_shipped_model_matches_its_record_and_the_manifest():
    rec = json.loads((MODEL / "finetune.json").read_text(encoding="utf-8"))
    assert rec["gate"]["ship"] is True
    assert rec["gate"]["heldout_top5"]["tuned"] > max(rec["gate"]["heldout_top5"]["previous"], rec["gate"]["heldout_top5"]["base_reexport"])
    assert rec["gate"]["s2_k7_relevant_top5"]["tuned"] >= rec["gate"]["s2_k7_relevant_top5"]["previous"]
    assert sha256_file(MODEL / "model.onnx") == rec["model_sha256"]
    assert sha256_file(MODEL / "tokenizer.json") == rec["tokenizer_sha256"]
    manifest = {r["source_id"]: r for r in json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))["snapshots"]}
    assert manifest["minilm-onnx"]["derived_sha256"] == rec["model_sha256"]
    assert manifest["minilm-onnx"]["original_sha256"] == rec["base"]["previous_shipped_model_sha256"]
    assert manifest["minilm-onnx"]["fine_tune_sha256"] == sha256_file(MODEL / "finetune.json")


def test_builder_keeps_only_the_recorded_model(tmp_path):
    for f in ("model.onnx", "tokenizer.json", "finetune.json"):
        shutil.copyfile(MODEL / f, tmp_path / f)
    assert build.fine_tune(tmp_path)["model_sha256"] == sha256_file(tmp_path / "model.onnx")
    with (tmp_path / "tokenizer.json").open("ab") as f:
        f.write(b" ")
    with pytest.raises(RuntimeError, match="tokenizer.json does not match"):
        build.fine_tune(tmp_path)
    (tmp_path / "finetune.json").unlink()
    assert build.fine_tune(tmp_path) is None  # no record: the pinned base model is copied in

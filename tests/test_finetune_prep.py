"""Fine-tuning preparation (SPEC 8, 11.8): fixed pairs from the bundled O*NET files, a fixed validation split, and
no held-out casual sentence anywhere in what goes to the pod."""
import importlib.util
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_pairs", ROOT / "tools" / "finetune" / "prepare_pairs.py")
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def test_pairs_are_reproducible_and_match_the_committed_manifest(tmp_path):
    a = prep.main(tmp_path / "a", tmp_path / "a")
    b = prep.main(tmp_path / "b", tmp_path / "b")
    assert a["outputs"] == b["outputs"]
    committed = json.loads((ROOT / "tools" / "finetune" / "pairs_manifest.json").read_text(encoding="utf-8"))
    assert a["outputs"] == committed["outputs"] and a["inputs"] == committed["inputs"]
    assert a["source_links"] == 24087 and a["train_pairs"] + a["val_pairs"] >= a["statements"]


def test_split_is_by_statement_and_heldout_text_never_ships(tmp_path):
    prep.main(tmp_path, tmp_path)
    load = lambda f: [json.loads(x) for x in (tmp_path / f).read_text(encoding="utf-8").splitlines()]  # noqa: E731
    train, val = load("pairs_train.jsonl"), load("pairs_val.jsonl")
    assert not {p["anchor"] for p in train} & {p["anchor"] for p in val}
    held = json.loads((ROOT / "tests" / "acceptance" / "heldout_casual.json").read_text(encoding="utf-8"))["cases"]
    with zipfile.ZipFile(tmp_path / "runpod_bundle.zip") as z:
        assert sorted(z.namelist()) == ["dwa_names.json", "pairs_manifest.json", "pairs_train.jsonl", "pairs_val.jsonl",
                                        "requirements-runpod.txt", "train.py"]
        blob = b"".join(z.read(n) for n in z.namelist()).decode("utf-8").lower()
    for c in held:
        assert c["text"].lower() not in blob, c["id"]

"""Contrastive fine-tuning of all-MiniLM-L6-v2 on O*NET task-to-activity pairs (SPEC 8 and 11.8). Runs on a RunPod
GPU pod from runpod_bundle.zip (tools/finetune/prepare_pairs.py); also runs on a CPU for a smoke test.

    pip install -r requirements-runpod.txt
    python train.py --out out            # full run
    python train.py --out smoke --limit 256 --epochs 1   # quick check

Writes out/export/base and out/export/tuned, each with model.onnx (dynamic quint8, as the app loads) and
tokenizer.json, produced by the same export code so the base model is a control for the export itself; out/run.json
records the pod, GPU, runtime, versions, data and model hashes and an in-domain recall check on the validation
statements; out/aivancin_finetune_<run>.zip holds what to download. The product gate runs locally afterwards
(tools/finetune/evaluate.py) on held-out casual descriptions that never leave the repo.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import random
import shutil
import time
from collections import defaultdict
from pathlib import Path

BASE = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"  # the revision the app ships (tools/build_snapshots.py)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_jsonl(path, limit=None):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            out.append(json.loads(line))
    return out[:limit] if limit else out


def recall_at5(model, val, names):
    """Share of validation statements with one of their linked activities among the five closest activity names."""
    import numpy as np
    ids = sorted(names)
    nv = model.encode([names[i] for i in ids], normalize_embeddings=True, batch_size=256, convert_to_numpy=True)
    by_anchor = defaultdict(set)
    for p in val:
        by_anchor[p["anchor"]].add(p["dwa_id"])
    anchors = sorted(by_anchor)
    av = model.encode(anchors, normalize_embeddings=True, batch_size=256, convert_to_numpy=True)
    top = np.argsort(-(av @ nv.T), axis=1)[:, :5]
    hits = sum(1 for a, row in zip(anchors, top) if by_anchor[a] & {ids[j] for j in row})
    return {"statements": len(anchors), "hits": hits, "recall_at_5": round(hits / len(anchors), 4) if anchors else None}


def export_onnx(st_model, out_dir):
    """The HF encoder's last_hidden_state as ONNX (inputs input_ids, attention_mask, token_type_ids, as the app
    feeds them), then dynamic quint8 quantization. The app applies masked mean pooling and L2 normalisation."""
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic

    out_dir.mkdir(parents=True, exist_ok=True)
    hf = st_model[0].auto_model.eval().to("cpu")
    tok = st_model.tokenizer
    enc = tok(["I fix my friends' laptops at the weekend."], return_tensors="pt")
    tti = enc.get("token_type_ids", torch.zeros_like(enc["input_ids"]))

    class LastHidden(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask, token_type_ids):
            return self.m(input_ids=input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids).last_hidden_state

    fp32 = out_dir / "model_fp32.onnx"
    kwargs = dict(input_names=["input_ids", "attention_mask", "token_type_ids"], output_names=["last_hidden_state"],
                  dynamic_axes={"input_ids": {0: "batch", 1: "seq"}, "attention_mask": {0: "batch", 1: "seq"},
                                "token_type_ids": {0: "batch", 1: "seq"}, "last_hidden_state": {0: "batch", 1: "seq"}},
                  opset_version=17, do_constant_folding=True)
    with torch.no_grad():
        try:
            torch.onnx.export(LastHidden(hf), (enc["input_ids"], enc["attention_mask"], tti), str(fp32), dynamo=False, **kwargs)
        except TypeError:  # torch versions without the dynamo switch
            torch.onnx.export(LastHidden(hf), (enc["input_ids"], enc["attention_mask"], tti), str(fp32), **kwargs)
    quantize_dynamic(str(fp32), str(out_dir / "model.onnx"), weight_type=QuantType.QUInt8)
    tok.save_pretrained(str(out_dir / "tok"))
    shutil.copyfile(out_dir / "tok" / "tokenizer.json", out_dir / "tokenizer.json")
    shutil.rmtree(out_dir / "tok")
    return {"model.onnx": sha256(out_dir / "model.onnx"), "model_fp32.onnx": sha256(fp32),
            "tokenizer.json": sha256(out_dir / "tokenizer.json")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="pairs_train.jsonl")
    ap.add_argument("--val", default="pairs_val.jsonl")
    ap.add_argument("--names", default="dwa_names.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--warmup", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=None, help="use only the first N training pairs (smoke test)")
    a = ap.parse_args()

    import torch
    import sentence_transformers
    import transformers
    from datasets import Dataset
    from sentence_transformers import SentenceTransformer, SentenceTransformerTrainer, SentenceTransformerTrainingArguments, losses
    from sentence_transformers.training_args import BatchSamplers

    started = time.time()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    random.seed(a.seed)
    torch.manual_seed(a.seed)
    cuda = torch.cuda.is_available()
    run = {"started_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "pod_id": os.environ.get("RUNPOD_POD_ID"), "gpu": torch.cuda.get_device_name(0) if cuda else None,
           "gpu_count": torch.cuda.device_count() if cuda else 0, "cuda": torch.version.cuda,
           "python": platform.python_version(), "torch": torch.__version__,
           "sentence_transformers": sentence_transformers.__version__, "transformers": transformers.__version__,
           "base": BASE, "revision": REVISION,
           "params": {k: getattr(a, k) for k in ("epochs", "batch", "lr", "warmup", "seed", "limit")},
           "data": {f: sha256(f) for f in (a.pairs, a.val, a.names)}}
    train = read_jsonl(a.pairs, a.limit)
    val = read_jsonl(a.val)
    names = json.loads(Path(a.names).read_text(encoding="utf-8"))
    run["train_pairs"], run["val_pairs"] = len(train), len(val)

    base = SentenceTransformer(BASE, revision=REVISION, device="cuda" if cuda else "cpu")
    run["val_base"] = recall_at5(base, val, names)
    print("base in-domain", run["val_base"], flush=True)

    model = SentenceTransformer(BASE, revision=REVISION, device="cuda" if cuda else "cpu")
    ds = Dataset.from_dict({"anchor": [p["anchor"] for p in train], "positive": [p["positive"] for p in train]})
    args = SentenceTransformerTrainingArguments(
        output_dir=str(out / "checkpoints"), num_train_epochs=a.epochs, per_device_train_batch_size=a.batch,
        learning_rate=a.lr, warmup_ratio=a.warmup, fp16=cuda, seed=a.seed, logging_steps=25,
        save_strategy="no", report_to="none",
        # Many statements share an activity name; duplicates in a batch would be scored as negatives of each other.
        batch_sampler=BatchSamplers.NO_DUPLICATES)
    trainer = SentenceTransformerTrainer(model=model, args=args, train_dataset=ds,
                                         loss=losses.MultipleNegativesRankingLoss(model))
    t0 = time.time()
    result = trainer.train()
    run["train_seconds"] = round(time.time() - t0, 1)
    run["train_loss"] = round(float(result.training_loss), 5)
    run["loss_log"] = [{k: v for k, v in h.items() if k in ("step", "loss", "learning_rate")}
                       for h in trainer.state.log_history if "loss" in h]
    run["val_tuned"] = recall_at5(model, val, names)
    print("tuned in-domain", run["val_tuned"], flush=True)
    model.save(str(out / "tuned_st"))

    export = out / "export"
    run["export"] = {"base": export_onnx(base, export / "base"), "tuned": export_onnx(model, export / "tuned")}
    run["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    run["runtime_seconds"] = round(time.time() - started, 1)
    (out / "run.json").write_text(json.dumps(run, indent=1) + "\n", encoding="utf-8")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")
    for sub in ("base", "tuned"):
        (export / sub / "model_fp32.onnx").unlink()  # the quantized files are what the app loads
    shutil.copyfile(out / "run.json", export / "run.json")
    archive = shutil.make_archive(str(out / f"aivancin_finetune_{stamp}"), "zip", root_dir=export)
    print(json.dumps({k: run[k] for k in ("pod_id", "gpu", "runtime_seconds", "train_loss", "val_base", "val_tuned")}, indent=1))
    print("download:", archive, sha256(archive))


if __name__ == "__main__":
    main()

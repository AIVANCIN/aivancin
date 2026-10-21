# Encoder fine-tuning on RunPod (prepared; not yet run)

AIVANCIN's AI step is a small sentence encoder (all-MiniLM-L6-v2) that matches a person's own sentences to O\*NET work activities. This folder fine-tunes it on a RunPod GPU with O\*NET's links between task statements and activities, so the model learns how O\*NET's activities are described in task-level wording. The fine-tuned model replaces the shipped one only if it passes a gate fixed before training (SPEC 8, check A17): more held-out casual descriptions with a correct activity in the top five than the shipped model, without lowering the earlier complete-chain regression (S2-K7). Otherwise the shipped model stays, and the run is reported as done work that did not ship.

## What is here

| File | Purpose |
| --- | --- |
| `prepare_pairs.py` | Builds the training pairs from the bundled O\*NET 31.0 files: 24,087 task-to-activity links become 21,397 training pairs and 1,078 validation pairs (17,578 distinct statements; about 5% kept out by a fixed hash rule for an in-domain check). Writes `build/runpod_bundle.zip` |
| `pairs_manifest.json` | Counts and SHA-256 hashes of the inputs and the pairs |
| `train.py` | Runs on the pod: contrastive training (multiple-negatives ranking loss, batches without duplicate texts), then exports the base and the tuned model through the same ONNX and quint8 code, so the base re-export is a control for the export itself. Records the pod ID, GPU, runtime, versions and hashes in `run.json` |
| `requirements-runpod.txt` | Training libraries for the pod; the app does not need them |
| `evaluate.py` | Runs locally on the CPU with the app's own scoring: the gate on `tests/acceptance/heldout_casual.json` (60 sentences, written and committed before any training), the S2-K7 chain, and the A1 sets for information |

The held-out sentences never go to the pod.

## Running it

1. **Locally:** `python tools/finetune/prepare_pairs.py` (prints the bundle's SHA-256).
2. **On RunPod:** start a GPU pod from a PyTorch template (any recent CUDA GPU; the run is small), upload `tools/finetune/build/runpod_bundle.zip`, then in the pod's terminal:

   ```bash
   mkdir -p /workspace/aivancin && cd /workspace/aivancin
   unzip -o /workspace/runpod_bundle.zip
   pip install -r requirements-runpod.txt
   python train.py --out out
   ```

   It prints the GPU, runtime and in-domain recall, and the path of `out/aivancin_finetune_<time>.zip`. Download that zip, take the dashboard screenshot showing the pod ID, GPU type and runtime, then stop and terminate the pod.
3. **Locally:** unzip it, then run the gate:

   ```bash
   python tools/finetune/evaluate.py --candidate <unzipped>/tuned --control <unzipped>/base --k7 <folder with profiles_k6.json and narratives_k7.json> --out gate.json
   ```

   `"ship": true` (held-out score above both the shipped model and the base re-export, and the S2-K7 chain not lower) means the tuned model may replace `data/model/model.onnx`; the snapshot builder then recomputes the activity and task vectors for it, and the manifest records the new model's hash and the run.

## Checks before any GPU time

- `tests/test_finetune_prep.py`: the pairs reproduce from the bundled files and match `pairs_manifest.json`; the validation split is by statement; no held-out sentence is in the bundle.
- `evaluate.py` with the shipped model as the candidate reproduces the shipped results exactly (held-out 39 of 60, A1 sets 18 and 19 of 20, S2-K7 chain 31 of 50, after the 21 Oct fairness change) and does not ship it. The shipped model's 39 of 60 is the bar a fine-tuned model must beat.
- CPU smoke test (21 Oct 2026, scratch environment with the pinned libraries, `python train.py --out out --limit 256 --epochs 1 --batch 32` from the bundle): training, both exports, `run.json` and the download zip worked in 76 seconds; both exported models load in the app's encoder. The base model's re-export scored 40 of 60 on the held-out set against the shipped model's 39, so the export alone can move the score by one: the gate therefore also requires the tuned model to beat that control. The smoke run's 8 training steps say nothing about the model's quality (its tuned model scored 39 of 60).

"""Local sentence encoder (all-MiniLM-L6-v2, ONNX on CPU) and the precomputed activity and task-statement vectors."""
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

from .aei import norm_task
from .snapshots import sha256_file

os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

MAX_TOKENS = 256
NAME_WEIGHT = 0.7  # an activity's score: this share from its name, the rest from its closest linked task statement


class AIUnavailable(RuntimeError):
    pass


class Encoder:
    def __init__(self, model_path, tokenizer_path, threads=4):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.model_sha256 = sha256_file(model_path)
        self.tokenizer_sha256 = sha256_file(tokenizer_path)
        self._tok = Tokenizer.from_file(str(tokenizer_path))
        self._tok.enable_truncation(MAX_TOKENS)
        self._tok.enable_padding(pad_id=0, pad_token="[PAD]")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        self._sess = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._sess.get_inputs()}

    def encode(self, texts, batch_size=1):
        """Masked mean pooling over token states, then L2 normalisation.

        One text per run by default: the quantized model scales activations per batch, so batching would
        make a text's vector depend on the other texts beside it (found by the A4 regression check).
        """
        out = []
        for i in range(0, len(texts), batch_size):
            enc = self._tok.encode_batch(list(texts[i:i + batch_size]))
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            states = self._sess.run(None, feed)[0]
            m = mask[..., None].astype(np.float32)
            v = (states * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            out.append(v / np.linalg.norm(v, axis=1, keepdims=True))
        return np.vstack(out).astype(np.float32)


def task_statements(onet):
    """Distinct O*NET task statements (normalised, sorted) and the activities each is linked to."""
    links = defaultdict(set)
    text = {}
    for code, pairs in onet.occupation_dwas.items():
        for tid, dwa in pairs:
            key = norm_task(onet.tasks[(code, tid)]["task"])
            text.setdefault(key, onet.tasks[(code, tid)]["task"].strip())
            links[key].add(dwa)
    keys = sorted(links)
    return [text[k] for k in keys], [sorted(links[k]) for k in keys], keys


def statements_sha256(keys):
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


class ActivityIndex:
    """Scores every activity for one encoded sentence: NAME_WEIGHT x cosine with the activity's name plus the rest
    x the best cosine with an O*NET task statement linked to it. Task statements carry the concrete wording people
    use ("test new versions of software"); an activity no task links to is scored by its name alone."""

    def __init__(self, names, tasks, links, dwa_ids):
        self.names = names
        self.tasks = tasks
        pos = {d: i for i, d in enumerate(dwa_ids)}
        pairs = [(j, pos[d]) for j, ds in enumerate(links) for d in ds if d in pos]
        self.rows = np.array([j for j, _ in pairs], dtype=np.int64)
        self.cols = np.array([i for _, i in pairs], dtype=np.int64)
        self.linked = np.zeros(len(dwa_ids), dtype=bool)
        self.linked[self.cols] = True

    def scores(self, q):
        name = self.names @ q
        best = np.full(name.shape, -np.inf, dtype=np.float32)
        np.maximum.at(best, self.cols, (self.tasks @ q)[self.rows])
        best = np.where(self.linked, best, name)
        return NAME_WEIGHT * name + (1 - NAME_WEIGHT) * best


def load_ai(snapshots):
    """Return (encoder, ActivityIndex) or raise AIUnavailable with a reason."""
    data_dir = Path(snapshots.data_dir)
    model_rec = snapshots.source("minilm-onnx")
    tok_rec = snapshots.source("minilm-tokenizer")
    model_path = data_dir / model_rec["derived_path"]
    tok_path = data_dir / tok_rec["derived_path"]
    vec_path = data_dir / "vectors" / "dwa_vectors.npy"
    meta_path = data_dir / "vectors" / "dwa_vectors.json"
    tvec_path = data_dir / "vectors" / "task_vectors.npy"
    tmeta_path = data_dir / "vectors" / "task_vectors.json"
    for p in (model_path, tok_path, vec_path, meta_path, tvec_path, tmeta_path):
        if not p.exists():
            raise AIUnavailable(f"missing {p.relative_to(data_dir).as_posix()}")
    if sha256_file(model_path) != model_rec["derived_sha256"]:
        raise AIUnavailable("model file does not match its manifest hash")
    if sha256_file(tok_path) != tok_rec["derived_sha256"]:
        raise AIUnavailable("tokenizer file does not match its manifest hash")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("model_sha256") != model_rec["derived_sha256"]:
        raise AIUnavailable("activity vectors were computed with a different model")
    if meta.get("dwa_extract_sha256") != snapshots.source("onet31-dwas")["derived_sha256"]:
        raise AIUnavailable("activity vectors were computed from a different activity list")
    if sha256_file(vec_path) != meta.get("vectors_sha256"):
        raise AIUnavailable("activity vector file is corrupted")
    vectors = np.load(vec_path, allow_pickle=False)
    if vectors.shape != (len(snapshots.onet.dwa_ids), meta.get("dim")):
        raise AIUnavailable("activity vectors do not match the activity list")
    tmeta = json.loads(tmeta_path.read_text(encoding="utf-8"))
    _, links, keys = task_statements(snapshots.onet)
    if tmeta.get("model_sha256") != model_rec["derived_sha256"]:
        raise AIUnavailable("task-statement vectors were computed with a different model")
    if tmeta.get("statements_sha256") != statements_sha256(keys):
        raise AIUnavailable("task-statement vectors were computed from a different task list")
    if sha256_file(tvec_path) != tmeta.get("vectors_sha256"):
        raise AIUnavailable("task-statement vector file is corrupted")
    tasks = np.load(tvec_path, allow_pickle=False).astype(np.float32)
    if tasks.shape != (len(keys), meta.get("dim")):
        raise AIUnavailable("task-statement vectors do not match the task list")
    try:
        encoder = Encoder(model_path, tok_path)
    except Exception as exc:
        raise AIUnavailable(f"model failed to load ({type(exc).__name__})") from exc
    return encoder, ActivityIndex(vectors, tasks, links, snapshots.onet.dwa_ids)

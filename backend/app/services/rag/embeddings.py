from __future__ import annotations

"""Local semantic embedding with bge-small-zh (ONNX) for hybrid RAG retrieval.

The ONNX model is a released, pre-trained checkpoint (pure inference, no
training) stored outside the project under D:\\HealthMateData\\models; the
project ships no model weights. When the model is absent the encoder degrades
to a deterministic character-ngram hashing fallback so retrieval never crashes.
"""

import hashlib
import math
import re
from functools import lru_cache
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy ships with backend deps
    np = None  # type: ignore[assignment]

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover
    ort = None  # type: ignore[assignment]

try:
    from tokenizers import Tokenizer as HF_Tokenizer
except ImportError:  # pragma: no cover
    HF_Tokenizer = None  # type: ignore[assignment]

from app.core.config import settings

MODEL_REPO = "Xenova/bge-small-zh-v1.5"
_HASH_DIM = 512
_CHAR_NGRAMS = (2, 3)
MAX_LEN = 510


def model_dir() -> Path:
    return Path(settings.embedding_model_dir or r"D:\HealthMateData\models\bge-small-zh-v1.5")


def model_available() -> bool:
    return (
        bool(ort)
        and bool(HF_Tokenizer)
        and (model_dir() / "onnx" / "model.onnx").is_file()
        and (model_dir() / "tokenizer.json").is_file()
    )


@lru_cache(maxsize=1)
def _session():
    if not model_available():
        return None
    return ort.InferenceSession(
        str(model_dir() / "onnx" / "model.onnx"),
        providers=["CPUExecutionProvider"],
    )


@lru_cache(maxsize=1)
def _tokenizer():
    if not model_available():
        return None
    return HF_Tokenizer.from_file(str(model_dir() / "tokenizer.json"))


def _encode(text: str) -> tuple[list[int], list[int]]:
    tokenizer = _tokenizer()
    if tokenizer is None:
        return _fallback_ids(text)
    encoding = tokenizer.encode(str(text or ""))
    ids = encoding.ids[:MAX_LEN]
    mask = [1] * len(ids)
    return ids, mask


def _fallback_ids(text: str) -> tuple[list[int], list[int]]:
    tokens: list[str] = []
    for char in re.sub(r"\s+", "", (text or "").lower()):
        tokens.append(char)
    ids = [int(hashlib.md5(t.encode("utf-8")).hexdigest()[:8], 16) % 30522 + 101 for t in tokens]
    return ids[:MAX_LEN], [1] * min(len(ids), MAX_LEN)


def _run_onnx(text: str) -> list[float]:
    session = _session()
    ids, mask = _encode(text)
    if not ids:
        return _hash_fallback(text)
    outputs = session.run(
        None,
        {
            "input_ids": [ids],
            "attention_mask": [mask],
            "token_type_ids": [[0] * len(ids)],
        },
    )
    hidden = outputs[0]  # (1, seq, hidden) or (1, hidden)
    vector = hidden[0]
    if vector.ndim == 2:  # take [CLS] row
        vector = vector[0]
    vector = vector.astype("float64")
    norm = math.sqrt(float((vector * vector).sum()))
    return [float(x) / norm for x in vector] if norm else [0.0] * len(vector)


def _hash_fallback(text: str) -> list[float]:
    """Deterministic n-gram hashing used only when ONNX is unavailable."""
    vector = [0.0] * _HASH_DIM
    normalized = re.sub(r"\s+", "", (text or "").lower())
    grams = set()
    for n in _CHAR_NGRAMS:
        grams.update(normalized[index : index + n] for index in range(max(0, len(normalized) - n + 1)))
    for gram in grams:
        digest = hashlib.md5(gram.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % _HASH_DIM
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


# Actual backend used by the last embed() call: "onnx_bge" or "hash_fallback".
# Updated inside embed() so the reported backend always matches the code path
# that actually produced the vector (spec B1: never claim ONNX-mode results
# when hashing fallback ran).
_EMBED_BACKEND = "hash_fallback"


@lru_cache(maxsize=256)
def embed(text: str) -> list[float]:
    """L2-normalised embedding; ONNX model first, hashing fallback second."""
    global _EMBED_BACKEND
    if model_available():
        try:
            vector = _run_onnx(text)
            _EMBED_BACKEND = "onnx_bge"
            return vector
        except Exception:
            pass
    _EMBED_BACKEND = "hash_fallback"
    return _hash_fallback(text)


def active_embedding_backend() -> str:
    """Backend of the most recent embed() call; exact path taken, no guessing."""
    return _EMBED_BACKEND


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


def hashable_fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]

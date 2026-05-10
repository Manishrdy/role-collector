"""Sentence-Transformers wrapper for description embeddings.

Used by the semantic-duplicate node to score description similarity.
The model file is ~90MB and takes a few seconds to import + load on
first call, so we lazy-import and cache a single instance per process.

Tests should monkeypatch :func:`encode_texts` rather than load the
real model.
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)

# all-MiniLM-L6-v2 — 384-dim, ~80MB, fast on CPU. Good baseline for
# short job-description similarity. Configurable later if needed.
DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def _load_model(name: str = DEFAULT_MODEL_NAME) -> SentenceTransformer:
    """Import + load the encoder once per process."""
    from sentence_transformers import SentenceTransformer

    log.info("loading sentence-transformer model: %s", name)
    model: SentenceTransformer = SentenceTransformer(name)
    return model


def encode_texts(
    texts: list[str], *, model_name: str = DEFAULT_MODEL_NAME
) -> list[list[float]]:
    """Encode a batch of texts to fixed-dim embeddings.

    Returns one Python list per input (JSON-serialisable). Empty inputs
    yield zero-vectors of the same dim so downstream cosine still works.
    """
    if not texts:
        return []
    model = _load_model(model_name)
    raw = model.encode(texts, normalize_embeddings=True)
    out: list[list[float]] = []
    for vec in raw:
        out.append([float(x) for x in vec.tolist()])
    return out


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    """Cosine similarity in [0, 1] (assuming both vectors normalised).

    Returns 0.0 if either side is missing or zero-norm — a defensive
    default that lets the caller skip the comparison cleanly.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    # Inputs come from `normalize_embeddings=True`, so |a| == |b| == 1.
    # Guard anyway against arbitrary callers.
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    sim = dot / (norm_a * norm_b)
    # Clamp to [0, 1] — embeddings can produce slightly out-of-range cosines
    # due to float arithmetic, and our scoring formula expects [0, 1].
    return max(0.0, min(1.0, sim))


def reset_cache() -> None:
    """Drop the cached model. Tests may need this between fixtures."""
    _load_model.cache_clear()


# Public alias re-exported for cleaner imports in callers.
def model_loaded(name: str = DEFAULT_MODEL_NAME) -> bool:
    """Tell whether the encoder has already been loaded — useful for log emit."""
    cached: Any = _load_model.cache_info()
    return cached.currsize > 0 and name in [DEFAULT_MODEL_NAME]

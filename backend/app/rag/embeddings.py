"""Local embeddings for semantic retrieval. They cost no LLM quota.

The model is optional: if it has not been downloaded, retrieval runs on keyword
search alone and says so, rather than blocking a request on a multi-minute download.
Run scripts/fetch_embedding_model.py ahead of time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import numpy as np

from ..config import get_settings

log = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def _unit(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.clip(norms, 1e-9, None)


def model_is_cached(model_name: str, cache_dir: Path) -> bool:
    """True if the ONNX weights for this model are already on disk (no network needed)."""
    try:
        from fastembed import TextEmbedding

        for spec in TextEmbedding.list_supported_models():
            if spec["model"] == model_name:
                repo = spec["sources"]["hf"].replace("/", "--")
                snaps = Path(cache_dir) / f"models--{repo}" / "snapshots"
                return snaps.exists() and any(snaps.glob(f"*/{spec['model_file']}"))
    except Exception:  # noqa: BLE001  (any failure means "not usable")
        log.exception("could not inspect embedding model cache")
    return False


class FastEmbedder:
    def __init__(self, model_name: str, cache_dir: Path):
        from fastembed import TextEmbedding

        self.name = model_name
        self._model = TextEmbedding(model_name, cache_dir=str(cache_dir))

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return _unit(np.array(list(self._model.embed(texts))))

    def embed_query(self, text: str) -> np.ndarray:
        return _unit(np.array(list(self._model.query_embed([text]))))[0]


_embedder: Embedder | None = None
_checked = False


def get_embedder() -> Embedder | None:
    """The shared embedder, or None when the model is not available locally."""
    global _embedder, _checked
    if not _checked:
        s = get_settings()
        if not s.embeddings_enabled:
            log.info("embeddings are switched off (EMBEDDINGS_ENABLED=false); retrieval is keyword-only")
        elif model_is_cached(s.embedding_model, s.models_dir):
            _embedder = FastEmbedder(s.embedding_model, s.models_dir)
        else:
            log.warning("embedding model not cached; retrieval is keyword-only. "
                        "Run scripts/fetch_embedding_model.py")
        _checked = True
    return _embedder


def reset_embedder() -> None:
    global _embedder, _checked
    _embedder, _checked = None, False

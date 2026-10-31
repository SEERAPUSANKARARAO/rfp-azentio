"""Download the local embedding model once, into rfp-ai/data/models.

    cd rfp-ai/backend && .venv/bin/python ../scripts/fetch_embedding_model.py

Retrieval uses this model so it costs no LLM quota. The download is about 35 MB
and can take several minutes on a slow link, so run it before the demo, not during.
Retrieval falls back to keyword search alone if the model is not present.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402


def main() -> int:
    s = get_settings()
    from fastembed import TextEmbedding

    t0 = time.time()
    print(f"fetching {s.embedding_model} into {s.models_dir} ...", flush=True)
    model = TextEmbedding(s.embedding_model, cache_dir=str(s.models_dir))
    vec = next(iter(model.embed(["Murabaha purchase and possession before sale"])))
    print(f"ready: dim={len(vec)} in {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# rfp-ai/backend/app/config.py -> parents[2] is the rfp-ai/ project root
ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Comma-separated key lists, one pool per provider. Never log these.
    gemini_api_keys: str = ""
    groq_api_keys: str = ""
    openrouter_api_keys: str = ""

    db_path: Path = ROOT / "data" / "rfp_ai.db"
    upload_dir: Path = ROOT / "data" / "uploads"
    kb_upload_dir: Path = ROOT / "data" / "kb_uploads"
    export_dir: Path = ROOT / "data" / "exports"
    models_file: Path = ROOT / "models.yaml"
    models_dir: Path = ROOT / "data" / "models"  # local embedding model cache
    sample_dir: Path = ROOT / "sample_data"

    # Evidence freshness rules (see freshness.py)
    stale_months: int = 18
    current_product_version: str = "6.2"
    # Pin "today" (YYYY-MM-DD) so staleness, and the golden eval built on it, stay reproducible.
    as_of_date: date | None = None

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    draft_concurrency: int = 3
    draft_max_tokens: int = 2000
    retrieval_k: int = 6
    default_word_limit: int = 150
    review_confidence: float = 0.7  # below this an answer is routed to review
    vendor_name: str = "Vendor"  # fills <VendorName> in the RFP's required file name
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    def keys_for(self, provider: str) -> list[str]:
        raw = getattr(self, f"{provider}_api_keys", "")
        seen: list[str] = []
        for k in raw.split(","):
            k = k.strip()
            if k and k not in seen:
                seen.append(k)
        return seen


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.db_path.parent, s.upload_dir, s.kb_upload_dir, s.export_dir, s.models_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s

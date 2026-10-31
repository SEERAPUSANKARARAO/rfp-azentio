from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import init_db, make_engine
from app.rag.indexer import index_kb_dir
from tests.helpers import SAMPLES


@pytest.fixture
def client(tmp_path, monkeypatch):
    """App wired to a throwaway database and upload dir, with no API keys."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "ex"))
    monkeypatch.setenv("AS_OF_DATE", "2026-10-31")
    for k in ("GEMINI_API_KEYS", "GROQ_API_KEYS", "OPENROUTER_API_KEYS"):
        monkeypatch.setenv(k, "")

    from app.config import get_settings
    from app.db import get_engine
    from app.llm import get_gateway

    for fn in (get_settings, get_engine, get_gateway):
        fn.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    for fn in (get_settings, get_engine, get_gateway):
        fn.cache_clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fresh database with the sample knowledge base indexed (keyword search only, so it is fast and
    deterministic) and today pinned so staleness is reproducible."""
    monkeypatch.setenv("AS_OF_DATE", "2026-10-31")
    from app.config import get_settings

    get_settings.cache_clear()
    engine = make_engine(tmp_path / "p.db")
    init_db(engine)
    with Session(engine) as s:
        index_kb_dir(s, SAMPLES / "kb", None)
    yield engine
    get_settings.cache_clear()

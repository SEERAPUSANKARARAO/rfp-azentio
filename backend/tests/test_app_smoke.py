from __future__ import annotations

from sqlalchemy import inspect, text


def test_health_reports_no_keys(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["keys_configured"] == {"gemini": 0, "groq": 0, "openrouter": 0}


def test_llm_status_works_with_zero_keys(client):
    r = client.get("/api/llm/status")
    assert r.status_code == 200
    body = r.json()
    assert {p["provider"] for p in body["providers"]} == {"gemini", "groq", "openrouter"}
    assert set(body["tasks"]) == {"structure", "draft", "assess", "fast"}


def test_schema_and_fts_created(client):
    from app.db import get_engine

    eng = get_engine()
    tables = set(inspect(eng).get_table_names())
    for t in ("rfp", "section", "requirement", "kbdocument", "kbchunk", "answer",
              "citation", "clarificationquery", "conflict", "auditevent", "chatmessage", "job"):
        assert t in tables, t
    with eng.connect() as c:
        c.execute(text("INSERT INTO kb_chunk_fts(rowid, text) VALUES (1, 'Murabaha financing')"))
        hit = c.execute(
            text("SELECT rowid FROM kb_chunk_fts WHERE kb_chunk_fts MATCH 'murabaha'")
        ).fetchall()
    assert hit == [(1,)]

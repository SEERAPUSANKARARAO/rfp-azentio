"""SQLite persistence for the LLM layer: response cache, key state, usage log.

Kept separate from the application database so the cache and the "this key is
spent until midnight" knowledge survive when the RFP database is reset.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY, text TEXT NOT NULL, provider TEXT, model TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, created_at REAL
);
CREATE TABLE IF NOT EXISTS key_state (
    provider TEXT NOT NULL, fp TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
    blocked_until REAL DEFAULT 0, blocked_kind TEXT,
    disabled INTEGER DEFAULT 0, last_error TEXT,
    PRIMARY KEY (provider, fp, model)
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, provider TEXT, key_alias TEXT,
    model TEXT, task TEXT, status TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,
    latency_ms INTEGER, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON llm_usage(ts);
"""


class LlmStore:
    def __init__(self, path: str | Path = ":memory:"):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    # -- cache ---------------------------------------------------------
    def cache_get(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM llm_cache WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def cache_put(
        self, key: str, text: str, provider: str, model: str, pt: int, ct: int
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?,?,?)",
                (key, text, provider, model, pt, ct, time.time()),
            )
            self._db.commit()

    def cache_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]

    def cache_clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM llm_cache")
            self._db.commit()

    # -- key state -----------------------------------------------------
    def state_rows(self, provider: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM key_state WHERE provider=?", (provider,)
            ).fetchall()
        return [dict(r) for r in rows]

    def state_set(
        self,
        provider: str,
        fp: str,
        model: str,
        *,
        blocked_until: float = 0.0,
        blocked_kind: str | None = None,
        disabled: bool = False,
        last_error: str | None = None,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO key_state VALUES (?,?,?,?,?,?,?)",
                (provider, fp, model, blocked_until, blocked_kind, int(disabled), last_error),
            )
            self._db.commit()

    def state_reset(self, provider: str | None = None) -> None:
        with self._lock:
            if provider:
                self._db.execute("DELETE FROM key_state WHERE provider=?", (provider,))
            else:
                self._db.execute("DELETE FROM key_state")
            self._db.commit()

    # -- usage ---------------------------------------------------------
    def usage_add(
        self,
        *,
        provider: str,
        key_alias: str,
        model: str,
        task: str,
        status: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        error: str | None = None,
        ts: float | None = None,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO llm_usage (ts,provider,key_alias,model,task,status,"
                "prompt_tokens,completion_tokens,latency_ms,error) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts if ts is not None else time.time(), provider, key_alias, model, task, status,
                 prompt_tokens, completion_tokens, latency_ms, error),
            )
            self._db.commit()

    def usage_summary(self, since_ts: float) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT provider, key_alias, model, status, COUNT(*) AS calls, "
                "COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens),0) AS completion_tokens "
                "FROM llm_usage WHERE ts>=? GROUP BY provider, key_alias, model, status",
                (since_ts,),
            ).fetchall()
        return [dict(r) for r in rows]

    def usage_recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM llm_usage ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

"""Hybrid retrieval: keyword (SQLite FTS5 BM25) fused with local embeddings by reciprocal rank.

Two hard filters apply to every query and cannot be switched off: documents with
reuse_permitted=False (customer confidentiality) and answerable=False (internal
commercial/legal positions) are never returned. Stale and deprecated documents ARE
returned, flagged, because the agent must see an old claim to notice it conflicts
with a new one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
from sqlalchemy import text as sql
from sqlmodel import Session

from ..freshness import staleness_reasons
from .embeddings import Embedder, get_embedder
from .glossary import expand_terms

_AUTO = object()
RRF_K = 60

_WORD = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9'\-/.]*[A-Za-z0-9])?")
_STOP = frozenset(
    "a an and are as at be been by can confirm describe do does for from has have how if in is it its of on "
    "or our provide state stating that the their there this to we what which will with you your must should "
    "shall may please detail details include including solution platform vendor bidder".split()
)


@dataclass
class Passage:
    chunk_id: int
    doc_id: int
    doc_ref: str  # file stem, e.g. "dr_policy_v3"
    doc_title: str  # customer-redacted
    doc_kind: str
    doc_date: date | None
    version: str | None
    status: str
    canonical: bool
    location: str
    text: str
    score: float = 0.0
    bm25: float | None = None
    vec_sim: float | None = None
    bm25_rank: int | None = None
    vec_rank: int | None = None
    stale_reasons: list[str] = field(default_factory=list)


def query_terms(query: str) -> list[str]:
    """Search terms: the query's own content words plus glossary synonyms for Islamic finance terms."""
    own: list[str] = []
    for w in _WORD.findall(query):
        w = re.sub(r"'s$", "", w).strip("'")
        if w.lower() not in _STOP and len(w) > 1 and w.lower() not in (o.lower() for o in own):
            own.append(w)
    return own[:24] + [t for t in expand_terms(query) if t.lower() not in _STOP][:12]


def _fts_match(terms: list[str]) -> str:
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


# A conflict an SME has resolved names the canonical document. The other side's passage is superseded: it stays
# in the knowledge base (for the audit trail and the conflict dashboard) but is never offered to the model.
_SUPERSEDED = (
    "NOT EXISTS (SELECT 1 FROM conflict x WHERE x.status = 'resolved' AND x.canonical_doc_id IS NOT NULL "
    "AND x.canonical_doc_id != d.id AND (x.chunk_a_id = c.id OR x.chunk_b_id = c.id))"
)
_ELIGIBLE = f"d.reuse_permitted = 1 AND d.answerable = 1 AND {_SUPERSEDED}"


def _bm25(session: Session, terms: list[str], n: int) -> list[tuple[int, float]]:
    if not terms:
        return []
    rows = session.exec(  # type: ignore[call-overload]
        sql(
            "SELECT f.rowid AS id, bm25(kb_chunk_fts) AS s FROM kb_chunk_fts f "
            "JOIN kbchunk c ON c.id = f.rowid JOIN kbdocument d ON d.id = c.doc_id "
            f"WHERE kb_chunk_fts MATCH :q AND {_ELIGIBLE} ORDER BY s LIMIT :n"
        ).bindparams(q=_fts_match(terms), n=n)
    ).all()
    return [(r.id, -float(r.s)) for r in rows]  # FTS5 bm25 is lower-is-better and negative


def _vector(session: Session, query: str, emb: Embedder, n: int) -> list[tuple[int, float]]:
    rows = session.exec(  # type: ignore[call-overload]
        sql(
            "SELECT c.id AS id, c.embedding AS e FROM kbchunk c JOIN kbdocument d ON d.id = c.doc_id "
            f"WHERE c.embedding IS NOT NULL AND {_ELIGIBLE}"
        )
    ).all()
    if not rows:
        return []
    mat = np.vstack([np.frombuffer(r.e, dtype=np.float32) for r in rows])
    sims = mat @ emb.embed_query(query)
    order = np.argsort(-sims)[:n]
    return [(rows[i].id, float(sims[i])) for i in order]


def retrieval_mode(session: Session) -> str:
    has_vectors = session.exec(sql("SELECT 1 FROM kbchunk WHERE embedding IS NOT NULL LIMIT 1")).first()  # type: ignore[call-overload]
    return "hybrid" if (has_vectors and get_embedder() is not None) else "keyword"


def retrieve(
    session: Session, query: str, *, k: int = 6, embedder=_AUTO, pool: int = 30
) -> list[Passage]:
    emb: Embedder | None = get_embedder() if embedder is _AUTO else embedder  # type: ignore[assignment]
    terms = query_terms(query)
    bm = _bm25(session, terms, pool)
    vec = _vector(session, query, emb, pool) if emb is not None else []

    scores: dict[int, Passage] = {}
    fused: dict[int, float] = {}
    bm_rank = {cid: i + 1 for i, (cid, _) in enumerate(bm)}
    vec_rank = {cid: i + 1 for i, (cid, _) in enumerate(vec)}
    for cid, r in bm_rank.items():
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + r)
    for cid, r in vec_rank.items():
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + r)
    top = sorted(fused, key=lambda c: -fused[c])[:k]
    if not top:
        return []

    bm_score, vec_score = dict(bm), dict(vec)
    marks = ",".join(str(int(c)) for c in top)
    rows = session.exec(  # type: ignore[call-overload]
        sql(
            "SELECT c.id AS cid, c.text, c.location, d.id AS did, d.filename, d.display_title, d.kind, "
            "d.doc_date, d.version, d.status, d.canonical FROM kbchunk c "
            f"JOIN kbdocument d ON d.id = c.doc_id WHERE c.id IN ({marks})"
        )
    ).all()
    by_id = {r.cid: r for r in rows}
    out: list[Passage] = []
    for cid in top:
        r = by_id[cid]
        doc_date = date.fromisoformat(r.doc_date) if isinstance(r.doc_date, str) else r.doc_date
        out.append(Passage(
            chunk_id=cid, doc_id=r.did, doc_ref=Path(r.filename).stem, doc_title=r.display_title,
            doc_kind=r.kind, doc_date=doc_date, version=r.version, status=r.status,
            canonical=bool(r.canonical), location=r.location, text=r.text, score=fused[cid],
            bm25=bm_score.get(cid), vec_sim=vec_score.get(cid),
            bm25_rank=bm_rank.get(cid), vec_rank=vec_rank.get(cid),
            stale_reasons=staleness_reasons(doc_date, r.version, r.status),
        ))
    return out

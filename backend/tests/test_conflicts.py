"""Index-time conflict detection, the offline parts: which pairs get judged, how verdicts become rows, and
that a resolved conflict stops the superseded passage being retrieved."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text as sql
from sqlmodel import Session, select

from app import enums
from app.models import Conflict, KbChunk, KbDocument
from app.rag import conflicts as cf
from app.rag.embeddings import get_embedder
from app.rag.indexer import index_kb_dir
from app.rag.retrieve import retrieve
from tests.helpers import SAMPLES

PLANTED = [
    ("dr_policy_v3", "security_whitepaper_v4_2024"), ("soc2_summary_2026", "security_whitepaper_v4_2024"),
    ("certifications_register_2026", "security_whitepaper_v4_2024"), ("dr_policy_v3", "rfp_gulf_2024"),
]


def refs(session):
    return {r.id: Path(r.filename).stem for r in session.exec(sql("SELECT id, filename FROM kbdocument")).all()}  # type: ignore[call-overload]


@pytest.mark.parametrize("use_embeddings", [False, True], ids=["keyword", "hybrid"])
def test_every_planted_contradiction_is_among_the_candidate_pairs(env, use_embeddings):
    emb = get_embedder() if use_embeddings else None
    if use_embeddings and emb is None:
        pytest.skip("local embedding model not downloaded")
    with Session(env) as s:
        if use_embeddings:  # the shared fixture indexes keyword-only; re-embed for this case
            for d in s.exec(select(KbDocument)).all():
                d.content_hash = ""
            index_kb_dir(s, SAMPLES / "kb", emb)
        pairs = cf.candidate_pairs(cf._load(s), 40)
        names = refs(s)
    got = {frozenset((names[a.doc_id], names[b.doc_id])) for a, b, _ in pairs}
    for want in PLANTED:
        assert frozenset(want) in got, want
    assert len(pairs) <= 40


def test_candidates_never_pair_a_document_with_itself_or_include_forbidden_sources(env):
    with Session(env) as s:
        pairs = cf.candidate_pairs(cf._load(s), 500)
        names = refs(s)
    assert all(a.doc_id != b.doc_id for a, b, _ in pairs)
    used = {names[c.doc_id] for a, b, _ in pairs for c in (a, b)}
    assert not used & {"rfp_crescent_2026", "commercial_legal_positions", "glossary"}


def test_fact_kinds_recognise_what_makes_two_passages_comparable():
    assert {"hour", "minute", "rto"} <= cf.fact_kinds("The RTO is 1 hour and the RPO is 5 minutes.")
    assert "iso27001" in cf.fact_kinds("ISO/IEC 27001 certificate expired") and "certif" in cf.fact_kinds("certificate")
    assert cf.fact_kinds("Murabaha is a sale") == set()


class JudgeGateway:
    """Calls every pair contradictory except those mentioning 'teams'."""

    def __init__(self):
        self.prompts = []

    async def complete(self, task, prompt, *, system="", schema=None, max_tokens=0, **kw):
        self.prompts.append(prompt)
        bad = "RTO" in prompt or "ISO" in prompt
        return SimpleNamespace(data=cf.Verdict(contradiction=bad, topic="x", summary="differ", newer="B"))


async def test_verdicts_become_open_conflict_rows_oldest_first_and_rebuild_replaces_them(env):
    gw = JudgeGateway()
    with Session(env) as s:
        res = await cf.detect_conflicts(s, gw, None)
        rows = s.exec(select(Conflict)).all()
        assert res["contradictions"] == len(rows) > 0 and res["judge_failures"] == 0
        docs = {d.id: d for d in s.exec(select(KbDocument)).all()}
        for c in rows:
            assert c.status == enums.CONFLICT_OPEN
            older, newer = docs[c.doc_a_id].doc_date, docs[c.doc_b_id].doc_date
            assert older <= newer, "doc A is the older side, so the dashboard reads stale vs current"
        first = {c.id for c in rows}
        await cf.detect_conflicts(s, gw, None)
        assert len(s.exec(select(Conflict)).all()) == len(first)  # rebuilt, not duplicated


async def test_a_judge_that_fails_is_counted_and_never_invents_a_conflict(env):
    class Down:
        async def complete(self, *a, **k):
            from app.llm.errors import LLMError
            raise LLMError("quota")

    with Session(env) as s:
        res = await cf.detect_conflicts(s, Down(), None)
        assert res["contradictions"] == 0 and res["judge_failures"] == res["candidate_pairs"] > 0
        assert s.exec(select(Conflict)).all() == []


# ---------------------------------------------------------------- resolution takes the stale passage out of play
def chunk_of(session, stem: str, contains: str) -> KbChunk:
    doc = session.exec(select(KbDocument).where(KbDocument.filename.like(f"{stem}.%"))).one()
    return next(c for c in session.exec(select(KbChunk).where(KbChunk.doc_id == doc.id)).all() if contains in c.text)


def test_a_resolved_conflict_hides_the_superseded_passage_from_retrieval_but_keeps_it_stored(env):
    with Session(env) as s:
        old = chunk_of(s, "security_whitepaper_v4_2024", "4 hours")
        new = chunk_of(s, "dr_policy_v3", "1 hour")
        before = {p.chunk_id for p in retrieve(s, "State the RTO and RPO for production services", k=10, embedder=None)}
        assert old.id in before and new.id in before

        c = Conflict(kind="conflict", topic="RTO", chunk_a_id=old.id, chunk_b_id=new.id, doc_a_id=old.doc_id,
                     doc_b_id=new.doc_id, detail="4h vs 1h")
        s.add(c)
        s.commit()
        # still open: both are retrieved so the contradiction stays visible to the agent
        assert old.id in {p.chunk_id for p in retrieve(s, "State the RTO and RPO", k=10, embedder=None)}

        c.status, c.canonical_doc_id = enums.CONFLICT_RESOLVED, new.doc_id
        s.commit()
        after = {p.chunk_id for p in retrieve(s, "State the RTO and RPO for production services", k=10, embedder=None)}
        assert old.id not in after and new.id in after
        assert s.get(KbChunk, old.id) is not None  # superseded, not deleted

        # the canonical side never loses its own passages
        c.canonical_doc_id = old.doc_id
        s.commit()
        again = {p.chunk_id for p in retrieve(s, "State the RTO and RPO for production services", k=10, embedder=None)}
        assert old.id in again and new.id not in again

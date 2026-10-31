"""Level 1 requirement 02: retrieve relevant passages from the approved library and product/security
documents, with the source and date shown for each, and without ever leaking forbidden sources.

Recall is measured against the golden `ev` lists. Note the limit of that measure: the RFPs and the
KB were written together, so their vocabulary overlaps. The paraphrase test is the harder check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text as sql
from sqlmodel import Session, select

from app.db import init_db, make_engine
from app.ingest.chunker import split_text
from app.models import KbChunk, KbDocument
from app.rag.embeddings import get_embedder
from app.rag.glossary import expand_terms
from app.rag.indexer import index_kb_dir
from app.rag.retrieve import retrieval_mode, retrieve

SAMPLE_KB = Path(__file__).resolve().parents[2] / "sample_data" / "kb"
GOLDEN = [json.loads(p.read_text()) for p in sorted((Path(__file__).parent / "golden").glob("*.json"))]
FORBIDDEN = set(GOLDEN[0]["forbidden_docs"])
CUSTOMERS = ["Gulf Islamic Bank", "Al-Noor Finance", "Pearl Investment Bank", "Crescent Microfinance"]

needs_model = pytest.mark.skipif(get_embedder() is None, reason="local embedding model not downloaded")


@pytest.fixture(scope="module")
def kb(tmp_path_factory):
    eng = make_engine(tmp_path_factory.mktemp("kb") / "kb.db")
    init_db(eng)
    with Session(eng) as s:
        res = index_kb_dir(s, SAMPLE_KB, get_embedder())
    return eng, res


@pytest.fixture(scope="module")
def kb_keyword_only(tmp_path_factory):
    eng = make_engine(tmp_path_factory.mktemp("kw") / "kw.db")
    init_db(eng)
    with Session(eng) as s:
        index_kb_dir(s, SAMPLE_KB, None)
    return eng


def refs(passages):
    return [p.doc_ref for p in passages]


def evidence_found(ev, got) -> bool:
    """Every expected entry is present; an entry that is a list is satisfied by any one of its members."""
    return all(any(d in got for d in (e if isinstance(e, list) else [e])) for e in ev)


def recall_at(eng, embedder, k):
    hit = total = 0
    with Session(eng) as s:
        for g in GOLDEN:
            for q in g["requirements"]:
                ev = q["expect"]["ev"]
                if not ev:
                    continue
                total += 1
                got = refs(retrieve(s, q["text"], k=k, embedder=embedder))
                hit += evidence_found(ev, got)
    return hit, total


# ------------------------------------------------------------ indexing
def test_index_all_documents_and_chunks(kb):
    eng, res = kb
    assert len(res.added) == 25 and res.chunks > 60
    with Session(eng) as s:
        n_chunks = len(s.exec(select(KbChunk.id)).all())
        n_fts = s.exec(sql("SELECT COUNT(*) FROM kb_chunk_fts")).one()[0]  # type: ignore[call-overload]
    assert n_chunks == res.chunks == n_fts


def test_reindex_is_idempotent_and_detects_changes(tmp_path):
    eng = make_engine(tmp_path / "i.db")
    init_db(eng)
    with Session(eng) as s:
        first = index_kb_dir(s, SAMPLE_KB, None)
    with Session(eng) as s:
        second = index_kb_dir(s, SAMPLE_KB, None)
        n_after = s.exec(sql("SELECT COUNT(*) FROM kbchunk")).one()[0]  # type: ignore[call-overload]
    assert len(second.unchanged) == 25 and not second.added and not second.updated
    assert n_after == first.chunks  # no duplicates, no leaked FTS rows

    # a metadata change (e.g. an SME marks a doc deprecated) re-indexes just that document
    manifest = json.loads((SAMPLE_KB / "manifest.json").read_text())
    work = tmp_path / "kb"
    work.mkdir()
    for m in manifest:
        (work / m["file"]).write_bytes((SAMPLE_KB / m["file"]).read_bytes())
    for m in manifest:
        if m["slug"] == "dr_policy_v3":
            m["status"] = "deprecated"
    (work / "manifest.json").write_text(json.dumps(manifest))
    with Session(eng) as s:
        third = index_kb_dir(s, work, None)
        fts = s.exec(sql("SELECT COUNT(*) FROM kb_chunk_fts")).one()[0]  # type: ignore[call-overload]
        total = s.exec(sql("SELECT COUNT(*) FROM kbchunk")).one()[0]  # type: ignore[call-overload]
    assert third.updated == ["dr_policy_v3.pdf"] and len(third.unchanged) == 24
    assert fts == total


# ------------------------------------------------------------ recall (Level 1 req 02)
def test_keyword_recall_on_golden(kb_keyword_only):
    hit, total = recall_at(kb_keyword_only, None, 6)
    assert total == 58 and hit == total, f"{hit}/{total}"


@needs_model
def test_hybrid_recall_on_golden(kb):
    eng, _ = kb
    hit, total = recall_at(eng, get_embedder(), 6)
    assert hit == total, f"{hit}/{total}"


# Queries written to avoid the KB's own wording; accepted = every document that really holds the answer.
PARAPHRASES = [
    ("The bank must hold title to the goods before reselling them to the customer at a markup", {"murabaha_guide_v62"}),
    ("How are penalty fees on overdue instalments kept out of the bank's own income?", {"murabaha_guide_v62", "shariah_governance_v62"}),
    ("Can the system run in our own datacentre rather than on a public cloud?", {"product_overview_v62"}),
    ("Do you keep our customers' records inside the country?", {"data_residency_2026", "security_whitepaper_v6"}),
    ("What happens to service if an entire cloud region goes offline?", {"dr_policy_v3", "soc2_summary_2026"}),
    ("Which independent parties have audited your controls?", {"soc2_summary_2026", "certifications_register_2026", "pentest_summary_2026"}),
    ("Who signs off new financing products from a religious-compliance standpoint?", {"shariah_governance_v62"}),
    ("Can you run the register and coupon payments for investment certificates we have already issued?", {"sukuk_servicing_guide_v62", "rfp_pearl_2025", "sow_pearl_2025"}),
    ("Can insurance on the financed vehicle be linked to the loan record?", {"takaful_integration_v62"}),
    ("How long does it take to roll out a consumer financing product end to end?", {"sow_alnoor_2025"}),
    ("What if a customer wants to repay a deferred-sale contract ahead of schedule?", {"murabaha_guide_v62"}),
    ("Which languages and text directions does the user interface support?", {"product_overview_v62"}),
]


def paraphrase_hits(eng, embedder):
    with Session(eng) as s:
        return sum(bool(ok & set(refs(retrieve(s, q, k=6, embedder=embedder)))) for q, ok in PARAPHRASES)


@needs_model
def test_embeddings_help_on_paraphrased_queries(kb, kb_keyword_only):
    eng, _ = kb
    keyword = paraphrase_hits(kb_keyword_only, None)
    hybrid = paraphrase_hits(eng, get_embedder())
    assert hybrid >= keyword, "semantic search must not make retrieval worse"
    assert hybrid >= 11, f"hybrid {hybrid}/12, keyword {keyword}/12"  # measured 12/12 hybrid, 11/12 keyword
    assert keyword >= 10, f"keyword-only fell to {keyword}/12"


# Written before the glossary vocabulary was extended and never used to tune it: a check that the
# extra vocabulary generalises instead of only fixing the queries it was written for.
HELD_OUT = [
    ("What encryption is applied to stored customer information?", {"security_whitepaper_v6", "security_whitepaper_v4_2024", "soc2_summary_2026"}),
    ("Is a late-payment penalty treated as revenue by the bank?", {"murabaha_guide_v62", "shariah_governance_v62"}),
    ("Which team members will the vendor field at the busiest point of the project?", {"sow_alnoor_2025", "sow_gulf_2023", "sow_pearl_2025"}),
    ("How often is the failover to the standby region exercised?", {"dr_policy_v3", "soc2_summary_2026"}),
    ("Does the product work in right-to-left scripts?", {"product_overview_v62"}),
    ("Can outside engineers reach production data without our approval?", {"security_whitepaper_v6"}),
    ("How is the partner's share reduced over time in a home purchase partnership?", {"partnership_guide_v62"}),
    ("How long are system logs kept?", {"security_whitepaper_v6"}),
    ("Does the vendor test its defences with external hackers?", {"pentest_summary_2026", "soc2_summary_2026"}),
    ("Which calendar options exist for repayment schedules?", {"product_overview_v62", "murabaha_guide_v62"}),
]


def held_out_hits(eng, embedder):
    with Session(eng) as s:
        return sum(bool(ok & set(refs(retrieve(s, q, k=6, embedder=embedder)))) for q, ok in HELD_OUT)


def test_keyword_only_held_out_floor(kb_keyword_only):
    # measured 9/10; the miss is a pure vocabulary gap ("outside engineers" vs "vendor staff")
    assert held_out_hits(kb_keyword_only, None) >= 8


@needs_model
def test_hybrid_held_out_floor(kb):
    eng, _ = kb
    assert held_out_hits(eng, get_embedder()) >= 9  # measured 10/10


# ------------------------------------------------------------ guardrails
GUARD_QUERIES = [
    "Does the platform include native AI credit scoring such as ScoreIQ?",
    "What is the limitation of liability and indemnity position?",
    "Provide pricing and discounts for licences",
    "Crescent Microfinance digital lending response",
    "What does Sukuk mean?",
    "definition of Murabaha",
]


def test_forbidden_documents_are_indexed_so_the_filter_is_what_excludes_them(kb_keyword_only):
    """Control for the tests below: the forbidden text IS in the index and does match raw search."""
    with Session(kb_keyword_only) as s:
        for needle, slug in (("ScoreIQ", "rfp_crescent_2026"), ("liability", "commercial_legal_positions"),
                             ("Sukuk", "glossary")):
            owners = {
                r[0] for r in s.exec(sql(  # type: ignore[call-overload]
                    "SELECT d.filename FROM kb_chunk_fts f JOIN kbchunk c ON c.id = f.rowid "
                    "JOIN kbdocument d ON d.id = c.doc_id WHERE kb_chunk_fts MATCH :q"
                ).bindparams(q=f'"{needle}"')).all()
            }
            assert any(slug in o for o in owners), (needle, owners)


def test_forbidden_documents_never_retrieved_keyword(kb_keyword_only):
    with Session(kb_keyword_only) as s:
        for g in GOLDEN:
            for q in g["requirements"]:
                assert not FORBIDDEN & set(refs(retrieve(s, q["text"], k=10, embedder=None))), q["req_id"]
        for q in GUARD_QUERIES:
            assert not FORBIDDEN & set(refs(retrieve(s, q, k=10, embedder=None))), q


@needs_model
def test_forbidden_documents_never_retrieved_hybrid(kb):
    eng, _ = kb
    with Session(eng) as s:
        for q in GUARD_QUERIES + [r["text"] for g in GOLDEN for r in g["requirements"]]:
            assert not FORBIDDEN & set(refs(retrieve(s, q, k=10, embedder=get_embedder()))), q


def test_customer_names_are_redacted_everywhere_they_could_leak(kb_keyword_only):
    with Session(kb_keyword_only) as s:
        blobs = [c.text + c.location for c in s.exec(select(KbChunk)).all()]
        blobs += [d.display_title for d in s.exec(select(KbDocument)).all()]
        fts = [r[0] for r in s.exec(sql("SELECT text FROM kb_chunk_fts")).all()]  # type: ignore[call-overload]
        sow = s.exec(select(KbDocument).where(KbDocument.filename == "sow_gulf_2023.docx")).one()
        sow_text = " ".join(c.text for c in s.exec(select(KbChunk).where(KbChunk.doc_id == sow.id)).all())
        assert sow.customer == "Gulf Islamic Bank"  # kept internally, never displayed
    for name in CUSTOMERS:
        assert not any(name.lower() in b.lower() for b in blobs + fts), name
    assert "[Customer]" in sow_text
    assert "([Customer])" in sow.display_title


def test_gap_topics_have_no_evidence_in_the_retrieved_text(kb_keyword_only):
    """The agent's 'missing' flag relies on this: ask about something the KB lacks and the
    specific thing asked about is simply not in any passage."""
    probes = {"ISO 22301 business continuity certification": "22301",
              "PCI DSS cardholder data compliance": "PCI",
              "UAE Federal Decree-Law No. 45 of 2021 personal data": "45 of 2021",
              "central bank open finance framework API standards": "open finance"}
    with Session(kb_keyword_only) as s:
        for q, needle in probes.items():
            ps = retrieve(s, q, k=10, embedder=None)
            # Not vacuous: retrieval does return plausible-looking passages (generic word overlap),
            # which is exactly why the agent needs an explicit missing-evidence check.
            assert ps, q
            assert all(needle.lower() not in p.text.lower() for p in ps), q


# ------------------------------------------------------------ traceability
def test_every_passage_carries_source_date_and_location(kb_keyword_only):
    with Session(kb_keyword_only) as s:
        ps = retrieve(s, "disaster recovery RTO RPO", k=8, embedder=None)
    assert ps
    for p in ps:
        assert p.doc_title and p.doc_date and p.location and p.text and p.doc_kind
    soc2 = next(p for p in ps if p.doc_ref == "soc2_summary_2026")
    assert soc2.location.startswith("p.1, Section 3.2")  # a section a reviewer can open and verify


def test_stale_sources_are_returned_but_flagged(kb_keyword_only):
    with Session(kb_keyword_only) as s:
        ps = retrieve(s, "State the RTO and RPO for production services", k=8, embedder=None)
    by = {p.doc_ref: p for p in ps}
    assert {"dr_policy_v3", "soc2_summary_2026", "security_whitepaper_v4_2024", "rfp_gulf_2024"} <= set(by)
    assert by["dr_policy_v3"].stale_reasons == [] and by["soc2_summary_2026"].stale_reasons == []
    assert len(by["security_whitepaper_v4_2024"].stale_reasons) == 2  # age and product version
    assert by["rfp_gulf_2024"].stale_reasons


def test_retrieval_mode_reports_keyword_only_without_embeddings(kb_keyword_only):
    with Session(kb_keyword_only) as s:
        assert retrieval_mode(s) == "keyword"


# ------------------------------------------------------------ glossary and chunking
def test_glossary_expands_synonyms_to_canonical_terms():
    assert "Murabaha" in expand_terms("a cost-plus sale on deferred payment")
    assert "Gharamah" in expand_terms("how do you treat a late fee?")
    assert "Takaful" in expand_terms("islamic insurance on the asset")
    assert expand_terms("What colour is the logo?") == []


def test_chunker_keeps_short_text_whole_and_splits_long_text_with_overlap():
    short = "One sentence only."
    assert split_text(short) == [short]
    long = " ".join(f"Sentence number {i} says something specific about topic {i}." for i in range(60))
    pieces = split_text(long, max_chars=300)
    assert len(pieces) > 5 and all(len(p) <= 300 for p in pieces)
    assert pieces[0].split(". ")[-1].strip(".") in pieces[1]  # last sentence overlaps into the next piece
    assert split_text("") == []


# ------------------------------------------------------------ API
def test_kb_api_index_status_documents_search(client):
    r = client.post("/api/kb/index-samples")
    assert r.status_code == 200 and len(r.json()["added"]) == 25

    st = client.get("/api/kb/status").json()
    assert st["documents"] == 25 and st["retrieval_mode"] in ("hybrid", "keyword")

    docs = client.get("/api/kb/documents").json()
    by = {d["title"]: d for d in docs}
    assert by["Security and Infrastructure Whitepaper v4"]["stale_reasons"]
    assert not by["Disaster Recovery and Business Continuity Policy v3"]["stale_reasons"]
    assert not any("Gulf Islamic Bank" in d["title"] for d in docs)  # redacted in the listing too

    hits = client.get("/api/kb/search", params={"q": "data residency in India and the EU"}).json()
    assert hits and hits[0]["doc_ref"] in ("security_whitepaper_v6", "data_residency_2026")
    assert all(h["doc_ref"] not in FORBIDDEN for h in hits)

    assert client.post("/api/kb/index-samples").json()["added"] == []  # idempotent over HTTP

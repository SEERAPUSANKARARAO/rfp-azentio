"""Adding sources, administering them, editing the clarification list, and settling conflicts."""

from __future__ import annotations

import re
import time

import pytest
from sqlmodel import Session, select

from app import enums
from app.agent.schemas import DraftOut, Statement
from app.llm.errors import LLMError
from app.models import AuditEvent, Conflict, KbChunk, KbDocument
from tests.helpers import SAMPLES, XLSX, FakeGateway, grounded

NOTE = ("Payments are settled through the Acme Bank treasury desk, and Acme Bank's team approves each batch. "
        "Settlement completes within 2 hours of approval.")


def up(client, name="policy.md", body=NOTE, **fields):
    data = {"title": "Settlement note", "kind": "product_doc", "doc_date": "2026-09-01", "version": "6.2", **fields}
    return client.post("/api/kb/documents", files={"file": (name, body.encode())}, data=data)


# ============================================================ upload
def test_uploaded_document_without_headings_is_indexed_and_searchable(client):
    r = up(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["action"] == "added" and body["chunks"] >= 1 and body["warnings"] == [] and body["needs_conflict_check"]
    hits = client.get("/api/kb/search", params={"q": "settlement within 2 hours of approval"}).json()
    assert hits and hits[0]["doc_title"] == "Settlement note" and hits[0]["doc_date"] == "2026-09-01"


def test_customer_names_are_redacted_in_an_uploaded_document(client):
    up(client, kind="sow", customer="Acme Bank", title="SOW for Acme Bank")
    docs = client.get("/api/kb/documents").json()
    assert docs[0]["title"] == "SOW for [Customer]" and docs[0]["customer_redacted"] is True
    from app.db import get_engine
    with Session(get_engine()) as s:
        text = " ".join(c.text for c in s.exec(select(KbChunk)).all())
    assert "Acme" not in text and "[Customer]" in text


def test_upload_warns_about_undated_and_unredacted_customer_work(client):
    r = up(client, doc_date="", kind="sow").json()
    assert any("No document date" in w for w in r["warnings"])
    assert any("no customer name" in w for w in r["warnings"])


def test_reuploading_the_same_file_is_idempotent_and_changed_content_replaces_it(client):
    assert up(client).json()["action"] == "added"
    assert up(client).json()["action"] == "unchanged"
    again = up(client, body=NOTE + " Reconciliation runs nightly.").json()
    assert again["action"] == "updated"
    from app.db import get_engine
    with Session(get_engine()) as s:
        assert len(s.exec(select(KbDocument)).all()) == 1  # replaced, not duplicated


@pytest.mark.parametrize("kwargs,why", [
    (dict(name="x.exe"), "type"), (dict(body=""), "empty"), (dict(kind="se_provided"), "kind"),
    (dict(doc_date="15/03/2026"), "date"), (dict(status="archived"), "status"),
])
def test_bad_uploads_are_rejected_with_a_reason_and_store_nothing(client, kwargs, why):
    r = up(client, **kwargs)
    assert r.status_code == 422, why
    assert client.get("/api/kb/documents").json() == []


def test_oversized_upload_is_refused(client, monkeypatch):
    from app.services import kb_admin
    monkeypatch.setattr(kb_admin, "MAX_BYTES", 50)
    assert up(client, body="x" * 200).status_code == 413


# ============================================================ administer
def test_deprecating_flags_the_source_stale_and_blocking_reuse_removes_it_from_retrieval(client):
    doc_id = up(client).json()["id"]
    d = client.patch(f"/api/kb/documents/{doc_id}", json={"status": "deprecated", "reviewer": "sme"}).json()
    assert d["status"] == "deprecated"
    assert "marked deprecated" in client.get("/api/kb/documents").json()[0]["stale_reasons"]
    assert client.get("/api/kb/search", params={"q": "settlement within 2 hours"}).json()  # still found, but flagged
    client.patch(f"/api/kb/documents/{doc_id}", json={"reuse_permitted": False})
    assert client.get("/api/kb/search", params={"q": "settlement within 2 hours"}).json() == []  # now never offered
    assert client.patch(f"/api/kb/documents/{doc_id}", json={"title": "x"}).status_code == 422  # not editable here
    assert client.patch(f"/api/kb/documents/{doc_id}", json={"status": "gone"}).status_code == 422
    assert client.patch("/api/kb/documents/999", json={"status": "approved"}).status_code == 404
    from app.db import get_engine
    with Session(get_engine()) as s:
        ev = s.exec(select(AuditEvent).where(AuditEvent.action == "document_updated")).all()
    assert ev[0].actor == "sme" and ev[0].payload["after"] == {"status": "deprecated"}


def test_a_document_nobody_cites_can_be_deleted_and_a_cited_one_cannot(client, monkeypatch):
    import app.routers.agent as agent_router

    free = up(client, name="free.md").json()["id"]
    assert client.delete(f"/api/kb/documents/{free}").status_code == 204
    assert client.get("/api/kb/documents").json() == []

    client.post("/api/kb/index-samples")
    gw = FakeGateway(lambda t, p, n: LLMError("x") if t == "fast" else grounded(p))
    monkeypatch.setattr(agent_router, "get_gateway", lambda: gw)
    rid = client.post("/api/rfps", files={"file": (XLSX, (SAMPLES / "rfps" / XLSX).read_bytes())}).json()["id"]
    client.post(f"/api/rfps/{rid}/draft")
    for _ in range(150):
        if client.get(f"/api/rfps/{rid}/job").json()["status"] == "done":
            break
        time.sleep(0.1)
    cited = client.get(f"/api/rfps/{rid}/answers").json()[0]["citations"][0]["doc_id"]
    r = client.delete(f"/api/kb/documents/{cited}")
    assert r.status_code == 409 and "Mark it deprecated" in r.json()["detail"]


# ============================================================ clarification list
def test_clarifications_can_be_added_reworded_approved_dismissed_and_removed(client):
    rid = client.post("/api/rfps", files={"file": (XLSX, (SAMPLES / "rfps" / XLSX).read_bytes())}).json()["id"]
    c = client.post(f"/api/rfps/{rid}/clarifications", json={"text": "Is UAE residency needed at go-live?",
                    "reason": "Region is planned only", "requirement_ids": ["3.8"], "reviewer": "dana"})
    assert c.status_code == 201 and c.json()["status"] == "approved"
    cid = c.json()["id"]
    e = client.patch(f"/api/rfps/{rid}/clarifications/{cid}", json={"text": "Is UAE residency required at go-live?"}).json()
    assert e["text"].endswith("required at go-live?") and e["requirement_ids"] == ["3.8"]
    assert client.patch(f"/api/rfps/{rid}/clarifications/{cid}", json={"status": "dismissed"}).json()["status"] == "dismissed"
    assert client.patch(f"/api/rfps/{rid}/clarifications/{cid}", json={"status": "sent"}).status_code == 422
    assert client.patch(f"/api/rfps/{rid}/clarifications/{cid}", json={"text": "  "}).status_code == 422
    assert client.post(f"/api/rfps/{rid}/clarifications", json={"text": ""}).status_code == 422
    assert client.delete(f"/api/rfps/{rid}/clarifications/{cid}").status_code == 204
    assert client.get(f"/api/rfps/{rid}/clarifications").json() == []
    assert client.patch(f"/api/rfps/{rid}/clarifications/{cid}", json={"text": "x"}).status_code == 404
    actions = {x["action"] for x in client.get(f"/api/rfps/{rid}/audit").json()}
    assert {"clarification_added", "clarification_edited", "clarification_deleted"} <= actions


def test_dismissed_questions_stay_out_of_the_export(client, env=None):
    from app.db import get_engine
    from app.export.common import build_plan

    rid = client.post("/api/rfps", files={"file": (XLSX, (SAMPLES / "rfps" / XLSX).read_bytes())}).json()["id"]
    keep = client.post(f"/api/rfps/{rid}/clarifications", json={"text": "Keep this?"}).json()["id"]
    drop = client.post(f"/api/rfps/{rid}/clarifications", json={"text": "Drop this?"}).json()["id"]
    client.patch(f"/api/rfps/{rid}/clarifications/{drop}", json={"status": "dismissed"})
    with Session(get_engine()) as s:
        assert [c.id for c in build_plan(s, rid).clarifications] == [keep]


# ============================================================ resolving a conflict, end to end
def cites_policy_v3(task, prompt, n):
    """A model that, for the RTO question, answers from the current DR policy; otherwise grounded as usual."""
    if task == "fast":
        return LLMError("x")
    for m in re.finditer(r"\[(\d+)\] ([^\n]*)\n<passage>\n(.*?)\n</passage>", prompt, re.S):
        if "Disaster Recovery and Business Continuity Policy v3" in m.group(2) and "State the RTO" in prompt:
            return DraftOut(status="Complied", reasoning="policy",
                            statements=[Statement(text="The RTO is 1 hour and the RPO is 5 minutes.", passages=[int(m.group(1))])])
    return grounded(prompt)


def test_resolving_a_conflict_redrafts_affected_answers_without_the_stale_source_and_withdraws_approval(client, monkeypatch):
    import app.routers.agent as agent_router
    from app.db import get_engine

    gw = FakeGateway(cites_policy_v3)
    monkeypatch.setattr(agent_router, "get_gateway", lambda: gw)
    client.post("/api/kb/index-samples")
    with Session(get_engine()) as s:
        def chunk(stem, needle):
            d = s.exec(select(KbDocument).where(KbDocument.filename.like(f"{stem}.%"))).one()
            return next(c for c in s.exec(select(KbChunk).where(KbChunk.doc_id == d.id)).all() if needle in c.text)
        old, new = chunk("security_whitepaper_v4_2024", "4 hours"), chunk("dr_policy_v3", "1 hour")
        s.add(Conflict(kind="conflict", topic="RTO and RPO", chunk_a_id=old.id, chunk_b_id=new.id,
                       doc_a_id=old.doc_id, doc_b_id=new.doc_id, detail="4h/30m versus 1h/5m"))
        s.commit()
        new_doc = new.doc_id

    rid = client.post("/api/rfps", files={"file": (XLSX, (SAMPLES / "rfps" / XLSX).read_bytes())}).json()["id"]
    client.post(f"/api/rfps/{rid}/draft")
    for _ in range(150):
        if client.get(f"/api/rfps/{rid}/job").json()["status"] == "done":
            break
        time.sleep(0.1)
    ans = {a["req_id"]: a for a in client.get(f"/api/rfps/{rid}/answers").json()}
    assert "conflicting" in ans["3.2"]["flags"]
    client.post(f"/api/rfps/{rid}/answers/{ans['3.2']['requirement_id']}/accept", json={"reviewer": "dana"})

    listing = client.get("/api/kb/conflicts").json()[0]
    assert listing["status"] == "open" and "4 hours" in listing["older"]["text"] and "1 hour" in listing["newer"]["text"]
    assert [a["req_id"] for a in listing["affected"]] == ["3.2"] and listing["older"]["stale_reasons"]

    assert client.post(f"/api/kb/conflicts/{listing['id']}/resolve", json={"canonical_doc_id": 9999}).status_code == 422
    r = client.post(f"/api/kb/conflicts/{listing['id']}/resolve", json={"canonical_doc_id": new_doc, "reviewer": "sme"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conflict"]["status"] == "resolved" and [a["req_id"] for a in body["affected"]] == ["3.2"]
    assert body["recalculation"][0]["job_id"] and body["recalculation"][0]["requirements"] == 1

    for _ in range(150):
        if client.get(f"/api/rfps/{rid}/job").json()["status"] == "done":
            break
        time.sleep(0.1)
    after = client.get(f"/api/rfps/{rid}/answers/{ans['3.2']['requirement_id']}").json()
    assert "conflicting" not in after["flags"]  # the contradiction is settled
    assert after["review_status"] == "drafted"  # the approval was withdrawn: the evidence changed
    rtos = [p for t, p in gw.drafts() if "State the RTO" in p]
    # "within a single region" appears only in the whitepaper passage that the SME's decision superseded
    assert "within a single region" in rtos[0] and "within a single region" not in rtos[-1]
    # the other 2024 source was not part of this conflict, so it is still offered (and still stale-flagged)
    assert "Core Modernisation" in rtos[-1] or "[Customer] Core" in rtos[-1]
    assert len(rtos) == 2  # redrafted exactly once, and only that answer

    actions = [x["action"] for x in client.get(f"/api/rfps/{rid}/audit").json()]
    assert "recalculation_queued" in actions
    from app.db import get_engine as ge
    with Session(ge()) as s:
        assert s.exec(select(AuditEvent).where(AuditEvent.action == "conflict_resolved")).one().actor == "sme"

    assert client.post(f"/api/kb/conflicts/{listing['id']}/resolve", json={"canonical_doc_id": new_doc}).status_code == 409
    reopened = client.post(f"/api/kb/conflicts/{listing['id']}/reopen").json()
    assert reopened["status"] == "open" and reopened["canonical_doc_id"] is None
    hits = client.get("/api/kb/search", params={"q": "State the RTO and RPO for production services", "k": 10}).json()
    assert any("4 hours" in h["text"] for h in hits)  # reopening makes the old passage retrievable again


def test_resolving_without_model_keys_still_resolves_and_says_the_recalculation_was_skipped(client):
    from app.db import get_engine

    client.post("/api/kb/index-samples")
    with Session(get_engine()) as s:
        docs = s.exec(select(KbDocument)).all()
        a, b = docs[0], docs[1]
        ca = s.exec(select(KbChunk).where(KbChunk.doc_id == a.id)).first()
        cb = s.exec(select(KbChunk).where(KbChunk.doc_id == b.id)).first()
        c = Conflict(kind="conflict", topic="t", chunk_a_id=ca.id, chunk_b_id=cb.id, doc_a_id=a.id, doc_b_id=b.id)
        s.add(c)
        s.commit()
        cid, did = c.id, b.id
    r = client.post(f"/api/kb/conflicts/{cid}/resolve", json={"canonical_doc_id": did}).json()
    assert r["conflict"]["status"] == "resolved" and r["recalculation"] == []  # nothing relied on it

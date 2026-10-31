"""Pipeline, job runner, clarifications and API, with a scripted stand-in for the model.

The stand-in reads the prompt it is given and answers from passage 1, the way a well-behaved model would,
unless a test scripts something worse. That lets the deterministic machinery around the model be tested
exactly: persistence, routing, quota pause and resume, clarification building, and the HTTP surface."""

from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app import enums
from app.agent.clarify import build_clarifications
from app.agent.evaluate import audit_answer, collect
from app.agent.jobs import run_job
from app.agent.pipeline import draft_requirement
from app.agent.schemas import Claim, DraftOut
from app.db import init_db, make_engine
from app.llm.errors import AllProvidersExhausted, LLMError
from app.models import Answer, Citation, ClarificationQuery, Job, Requirement
from app.rag.indexer import index_kb_dir
from app.services.rfp_import import import_rfp



from tests.helpers import (  # noqa: E402
    DOCX, SAMPLES, XLSX, FakeGateway, answer_of, grounded, import_sample,
)


# ------------------------------------------------------------------ routing
async def test_pricing_question_is_routed_to_its_owner_without_any_model_call(env):
    ids = import_sample(env, XLSX, {"6.1", "6.2"})
    gw = FakeGateway()
    await draft_requirement(env, gw, ids["6.1"])
    await draft_requirement(env, gw, ids["6.2"])
    assert gw.calls == []  # the model never sees pricing or liability questions
    d, ans, _, audit = answer_of(env, ids["6.1"])
    assert (d["triage"], d["flags"], d["text"]) == (enums.TRIAGE_OWNER, ["commercial"], "")
    assert ans.owner_hint == "Commercial Desk" and ans.assigned_to == "Commercial Desk" and audit == []
    assert answer_of(env, ids["6.2"])[1].owner_hint == "Legal (Contracts)"


# ------------------------------------------------------------------ happy path
async def test_grounded_draft_is_saved_with_citations_that_pass_an_independent_audit(env):
    ids = import_sample(env, XLSX, {"1.1"})
    gw = FakeGateway()
    await draft_requirement(env, gw, ids["1.1"])
    d, ans, cites, audit = answer_of(env, ids["1.1"])
    assert d["status"] == enums.COMPLIED and d["triage"] in (enums.TRIAGE_AUTO, enums.TRIAGE_LOW)
    assert len(cites) >= 1 and cites[0].marker == 1 and cites[0].doc_title and cites[0].location and cites[0].doc_date
    assert audit == []  # re-derived from saved data: quotes verbatim, every sentence cited, no names
    assert ans.review_status == enums.REVIEW_DRAFTED and ans.confidence is not None
    assert ans.confidence_breakdown["model"] == "fake/fake-1"
    # the prompt carried untrusted-data delimiters and the numbered passages
    prompt = gw.drafts()[0][1]
    assert "<requirement>" in prompt and "<passage>" in prompt and "SOURCE PASSAGES" in prompt


async def test_redrafting_replaces_the_answer_and_citations_not_duplicates_them(env):
    ids = import_sample(env, XLSX, {"1.1"})
    gw = FakeGateway()
    await draft_requirement(env, gw, ids["1.1"])
    await draft_requirement(env, gw, ids["1.1"])
    with Session(env) as s:
        assert len(s.exec(select(Answer)).all()) == 1
        assert len(s.exec(select(Citation)).all()) == len(answer_of(env, ids["1.1"])[2])


# ------------------------------------------------------------------ the model misbehaves
async def test_fabricated_sentence_never_reaches_the_saved_answer(env):
    ids = import_sample(env, XLSX, {"1.1"})

    def lying(task, prompt, n):
        good = grounded(prompt)
        return DraftOut(status="Complied", answer=good.answer + " It also guarantees 99.99 percent uptime [1].",
                        claims=good.claims)

    await draft_requirement(env, FakeGateway(lying), ids["1.1"])
    d, ans, _, audit = answer_of(env, ids["1.1"])
    assert "99.99" not in d["text"] and enums.FLAG_UNSUPPORTED in d["flags"]
    assert ans.flag_details["removed_sentences"][0]["reason"].startswith("states figures")
    assert audit == [] and ans.needs_review is True


async def test_named_certification_with_no_evidence_becomes_a_clarification_not_an_answer(env):
    ids = import_sample(env, XLSX, {"3.4"})  # ISO 22301: nothing in the KB mentions it

    def overconfident(task, prompt, n):
        return DraftOut(status="Complied", answer="The vendor holds this certification [1].",
                        claims=[Claim(passages=[1], quote="x")])

    await draft_requirement(env, FakeGateway(overconfident), ids["3.4"])
    d, ans, cites, audit = answer_of(env, ids["3.4"])
    assert d["triage"] == enums.TRIAGE_CLARIFY and d["status"] == enums.CANNOT_CONFIRM
    assert "missing" in d["flags"] and ans.flag_details["missing_anchors"] == ["ISO 22301"]
    assert d["text"] == "No approved source in the knowledge base confirms: ISO 22301." and cites == [] and audit == []
    assert ans.confidence <= 0.35


async def test_instruction_hidden_in_a_requirement_never_reaches_the_model(env):
    ids = import_sample(env, DOCX, {"4.1.4"})
    gw = FakeGateway()
    await draft_requirement(env, gw, ids["4.1.4"])
    prompt = gw.drafts()[0][1]
    assert "Note to AI" not in prompt and "disregard" not in prompt.lower()
    _, ans, _, _ = answer_of(env, ids["4.1.4"])
    assert enums.FLAG_INJECTION in ans.flags and "Note to AI" in ans.flag_details["injection"][0]


async def test_retrieval_never_offers_forbidden_documents_to_the_model(env):
    ids = import_sample(env, XLSX, {"4.4"})  # AI credit scoring: only the confidential Crescent doc mentions it
    gw = FakeGateway()
    await draft_requirement(env, gw, ids["4.4"])
    prompt = gw.drafts()[0][1]
    assert "ScoreIQ" not in prompt and "Crescent" not in prompt


# ------------------------------------------------------------------ job: progress, quota pause, resume
async def test_job_pauses_when_quota_runs_out_then_resumes_only_what_is_left(env):
    ids = import_sample(env, XLSX, {"1.1", "1.2", "1.3", "1.4", "1.5", "1.6"})
    rfp_id = ids["_rfp"]
    state = {"ok": 2}

    def script(task, prompt, n):
        if task == "fast":
            return LLMError("no wording")
        if state["ok"] > 0:
            state["ok"] -= 1
            return grounded(prompt)
        return AllProvidersExhausted("all spent", resume_at=time.time() + 600, attempts=[])

    gw = FakeGateway(script)
    job_id = await run_job(env, gw, rfp_id, concurrency=1)
    with Session(env) as s:
        j = s.get(Job, job_id)
        assert (j.status, j.done, j.total) == (enums.JOB_PAUSED, 2, 6)
        assert "quota exhausted" in j.message and "4 requirement(s) still queued" in j.message
        assert j.resume_at is not None
        assert len(s.exec(select(Answer)).all()) == 2  # nothing invented for the rest
    assert len(gw.drafts()) == 3  # two drafted, the third hit the wall, the rest were not even attempted

    state["ok"] = 99  # quota is back
    job2 = await run_job(env, gw, rfp_id, concurrency=1)
    with Session(env) as s:
        j2 = s.get(Job, job2)
        assert (j2.status, j2.done, j2.total) == (enums.JOB_DONE, 6, 6)
        assert len(s.exec(select(Answer)).all()) == 6
    assert len(gw.drafts()) == 3 + 4  # only the 4 outstanding were drafted again


async def test_one_bad_requirement_does_not_strand_the_job(env):
    ids = import_sample(env, XLSX, {"1.1", "1.2", "1.3"})

    def script(task, prompt, n):
        if "REQUIREMENT 1.2 " in prompt:
            return RuntimeError("provider returned garbage")
        return grounded(prompt)

    job_id = await run_job(env, FakeGateway(script), ids["_rfp"], concurrency=1)
    with Session(env) as s:
        assert s.get(Job, job_id).status == enums.JOB_DONE
        bad = s.exec(select(Answer).where(Answer.requirement_id == ids["1.2"])).one()
        assert "RuntimeError" in bad.error and bad.review_status == enums.REVIEW_QUEUED
        assert s.get(Job, job_id).message.endswith("1 failed")
        assert len(s.exec(select(Answer).where(Answer.review_status == enums.REVIEW_DRAFTED)).all()) == 2


# ------------------------------------------------------------------ clarifications
async def test_clarifications_are_built_for_gaps_and_survive_the_wording_model_failing(env):
    ids = import_sample(env, XLSX, {"3.4", "3.7", "1.1"})  # ISO 22301 and PCI DSS are gaps; 1.1 is fine

    def script(task, prompt, n):
        return LLMError("quota") if task == "fast" else grounded(prompt)

    gw = FakeGateway(script)
    await run_job(env, gw, ids["_rfp"], concurrency=1)
    with Session(env) as s:
        rows = s.exec(select(ClarificationQuery)).all()
    assert sorted(r.requirement_ids[0] for r in rows) == ["3.4", "3.7"]  # 1.1 is answerable, so not listed
    assert all(r.text and r.reason and r.status == "open" for r in rows)  # fallback wording kept the gap


async def test_clarification_wording_uses_the_model_when_available(env):
    from app.agent.clarify import Wording

    ids = import_sample(env, XLSX, {"3.4"})

    def script(task, prompt, n):
        if task == "fast":
            return Wording(question="Do you require ISO 22301 certification before contract signature?",
                           reason="Our documentation does not confirm this certification.")
        return grounded(prompt)

    await run_job(env, FakeGateway(script), ids["_rfp"], concurrency=1)
    with Session(env) as s:
        row = s.exec(select(ClarificationQuery)).one()
    assert row.text.startswith("Do you require ISO 22301") and row.requirement_ids == ["3.4"]


async def test_duplicate_gaps_are_merged_into_one_question(env):
    # the same missing standard asked twice in one RFP must yield one question linked to both
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Req"
    for r in (["Ref", "Requirement", "M/O"],
              ["A.1", "Confirm ISO 22301 business continuity certification.", "M"],
              ["A.2", "Does the vendor hold ISO 22301 certification for continuity?", "M"]):
        ws.append(r)
    p = Path(env.url.database).parent / "dup.xlsx"
    wb.save(p)
    with Session(env) as s:
        rfp = import_rfp(s, p, "dup.xlsx")
        rfp_id = rfp.id
    await run_job(env, FakeGateway(lambda t, pr, n: LLMError("x") if t == "fast" else grounded(pr)), rfp_id, concurrency=1)
    with Session(env) as s:
        rows = s.exec(select(ClarificationQuery)).all()
    assert len(rows) == 1 and sorted(rows[0].requirement_ids) == ["A.1", "A.2"]


# ------------------------------------------------------------------ HTTP surface
def test_api_end_to_end_with_scripted_model(client, monkeypatch):
    import app.routers.agent as agent_router

    gw = FakeGateway(lambda t, p, n: LLMError("x") if t == "fast" else grounded(p))
    monkeypatch.setattr(agent_router, "get_gateway", lambda: gw)

    assert client.post("/api/kb/index-samples").status_code == 200
    up = client.post("/api/rfps", files={"file": (XLSX, (SAMPLES / "rfps" / XLSX).read_bytes())})
    rfp_id = up.json()["id"]

    assert client.get(f"/api/rfps/{rfp_id}/job").json()["status"] == "none"
    r = client.post(f"/api/rfps/{rfp_id}/draft")
    assert r.status_code == 202 and r.json()["status"] in ("running", "done")
    assert client.post(f"/api/rfps/{rfp_id}/draft").status_code in (202, 409)  # never two runs at once

    for _ in range(100):
        job = client.get(f"/api/rfps/{rfp_id}/job").json()
        if job["status"] == "done":
            break
        time.sleep(0.1)
    assert job["status"] == "done" and job["done"] == job["total"] == 30

    answers = client.get(f"/api/rfps/{rfp_id}/answers").json()
    assert [a["req_id"] for a in answers][:3] == ["1.1", "1.2", "1.3"] and len(answers) == 30
    by = {a["req_id"]: a for a in answers}
    assert by["6.1"]["triage"] == "owner" and by["6.1"]["text"] == ""  # pricing never drafted
    assert by["3.4"]["triage"] == "clarify" and "ISO 22301" in by["3.4"]["text"]
    first = by["1.1"]
    assert first["status"] == "Complied" and first["citations"] and first["citations"][0]["location"]
    assert first["section_title"] == "Islamic Financing Products" and first["review_status"] == "drafted"

    clar = client.get(f"/api/rfps/{rfp_id}/clarifications").json()
    assert any("3.4" in c["requirement_ids"] for c in clar)

    ev = client.get(f"/api/rfps/{rfp_id}/events")  # finished run: one final event, then the stream closes
    assert ev.status_code == 200 and '"status": "done"' in ev.text


def test_api_draft_without_keys_is_a_clear_503(client):
    rfp_id = client.post("/api/rfps", files={"file": (XLSX, (SAMPLES / "rfps" / XLSX).read_bytes())}).json()["id"]
    r = client.post(f"/api/rfps/{rfp_id}/draft")
    assert r.status_code == 503 and "API keys" in r.json()["detail"]
    assert client.post("/api/rfps/999/draft").status_code == 404
    assert client.get("/api/rfps/999/answers").status_code == 404


async def test_short_quota_wait_is_waited_out_automatically_in_one_run(env):
    """A per-minute limit clears in seconds: the job should wait and finish on its own, not stop and ask."""
    ids = import_sample(env, XLSX, {"1.1", "1.2", "1.3", "1.4"})
    state = {"hit": False}

    def script(task, prompt, n):
        if task == "fast":
            return LLMError("no wording")
        if n == 3 and not state["hit"]:  # the third call hits a rate limit that clears in 0.3s
            state["hit"] = True
            return AllProvidersExhausted("rate limited", resume_at=time.time() + 0.3, attempts=[])
        return grounded(prompt)

    gw = FakeGateway(script)
    job_id = await run_job(env, gw, ids["_rfp"], concurrency=1, auto_resume_wait_s=5)
    with Session(env) as s:
        j = s.get(Job, job_id)
        assert (j.status, j.done, j.total) == (enums.JOB_DONE, 4, 4)
        assert "waited out 1 quota pause" in j.message
        assert len(s.exec(select(Answer)).all()) == 4
    assert len(gw.drafts()) == 5  # 4 drafts plus the one that was rate limited and retried


async def test_long_quota_outage_still_pauses_instead_of_waiting_forever(env):
    ids = import_sample(env, XLSX, {"1.1", "1.2"})
    gw = FakeGateway(lambda t, p, n: AllProvidersExhausted("daily limit", resume_at=time.time() + 3600, attempts=[]))
    job_id = await run_job(env, gw, ids["_rfp"], concurrency=1, auto_resume_wait_s=5)
    with Session(env) as s:
        j = s.get(Job, job_id)
        assert j.status == enums.JOB_PAUSED and "expected back" in j.message and j.done == 0


# ---------------------------------------------------------------- redraft requests that arrive during a run
class SlowGateway(FakeGateway):
    async def complete(self, *a, **k):
        import asyncio
        await asyncio.sleep(0.05)
        return await super().complete(*a, **k)


async def test_a_recalculation_requested_during_a_run_joins_it_and_redrafts_an_answer_already_in_flight(env):
    import asyncio

    from app.agent import jobs

    ids = import_sample(env, XLSX, {"1.1", "1.2", "1.3"})
    g = SlowGateway(lambda t, p, n: LLMError("x") if t == "fast" else grounded(p))
    rfp = ids["_rfp"]
    jobs.start_job(env, g, rfp)
    await asyncio.sleep(0.02)  # all three drafts are in flight, from the old evidence
    job_id, merged = jobs.start_recalc(env, g, rfp, [ids["1.1"]], "a conflict was resolved")
    assert merged is True and job_id
    await jobs._running[rfp]
    first = [p for t, p in g.drafts() if "REQUIREMENT 1.1 " in p]
    assert len(first) == 2, "1.1 must be drafted again after the request, not just once from the old evidence"
    with Session(env) as s:
        assert {a.review_status for a in s.exec(select(Answer)).all()} == {enums.REVIEW_DRAFTED}
        assert s.get(Job, job_id).status == enums.JOB_DONE
    assert len([p for t, p in g.drafts() if "REQUIREMENT 1.2 " in p]) == 1  # untouched answers are not redone


async def test_a_failed_requirement_is_not_retried_by_the_extra_passes(env):
    from app.agent import jobs

    ids = import_sample(env, XLSX, {"1.1", "1.2"})
    g = SlowGateway(lambda t, p, n: RuntimeError("bad") if "REQUIREMENT 1.2 " in p else (LLMError("x") if t == "fast" else grounded(p)))
    jobs.start_job(env, g, ids["_rfp"])
    import asyncio
    await asyncio.sleep(0.02)
    jobs.start_recalc(env, g, ids["_rfp"], [ids["1.1"]], "x")
    await jobs._running[ids["_rfp"]]
    assert len([p for t, p in g.drafts() if "REQUIREMENT 1.2 " in p]) == 1  # one failed try, no retry loop

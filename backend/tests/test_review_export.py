"""Level 1 requirement 04: a reviewer edits or accepts each answer, and the completed response is exported
with requirement IDs and source references, in the RFP's own format.

The export tests re-open the files that were produced and check what is in them, not what the code meant to
write. Human control is the point: nothing unapproved is submitted, and a draft copy cannot be mistaken for one."""

from __future__ import annotations

import time
from pathlib import Path

import docx
import openpyxl
import pytest
from sqlmodel import Session, select

from app import enums
from app.agent.jobs import run_job
from app.models import Answer, AuditEvent, Requirement
from app.services import export_service, review
from tests.helpers import DOCX, SAMPLES, XLSX, FakeGateway, grounded, import_sample

PDF = "GHF_PGF-RFP-0426_Digital_Lending.pdf"


async def drafted(env, name: str, keep: set[str] | None = None) -> dict[str, int]:
    ids = import_sample(env, name, keep)
    await run_job(env, FakeGateway(lambda t, p, n: __import__("app.llm.errors", fromlist=["LLMError"]).LLMError("x")
                                   if t == "fast" else grounded(p)), ids["_rfp"], concurrency=1)
    return ids


def accept_all_with_text(env, ids: dict[str, int], who: str = "sam") -> list[str]:
    done = []
    with Session(env) as s:
        for req_id, pk in ids.items():
            if req_id == "_rfp":
                continue
            a = s.exec(select(Answer).where(Answer.requirement_id == pk)).first()
            if a and review.current_text(a).strip():
                review.accept(s, ids["_rfp"], pk, who)
                done.append(req_id)
    return done


# ====================================================================== review actions
async def test_accept_needs_text_and_records_who(env):
    ids = await drafted(env, XLSX, {"1.1", "6.1"})
    with Session(env) as s:
        with pytest.raises(review.ReviewError, match="no answer text"):
            review.accept(s, ids["_rfp"], ids["6.1"], "sam")  # pricing: owner-routed, nothing drafted
        a = review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        assert (a.review_status, a.reviewer) == (enums.REVIEW_ACCEPTED, "sam") and a.reviewed_at
        ev = s.exec(select(AuditEvent).where(AuditEvent.action == "accepted")).one()
        assert ev.actor == "sam" and ev.payload["req_id"] == "1.1"


async def test_editing_an_accepted_answer_withdraws_the_approval(env):
    ids = await drafted(env, XLSX, {"1.1"})
    with Session(env) as s:
        review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        a = review.edit(s, ids["_rfp"], ids["1.1"], "MIBS supports Murabaha with possession evidence.", "sam")
        assert a.review_status == enums.REVIEW_DRAFTED and a.reviewed_at is None and a.edited
        assert review.current_text(a) == "MIBS supports Murabaha with possession evidence."
        ev = s.exec(select(AuditEvent).where(AuditEvent.action == "edited")).one()
        assert ev.payload["approval_withdrawn"] is True and ev.payload["before"] and ev.payload["after"]


async def test_se_figures_not_in_the_sources_are_marked_as_the_ses_own(env):
    ids = await drafted(env, XLSX, {"1.1"})
    with Session(env) as s:
        a = review.edit(s, ids["_rfp"], ids["1.1"], "MIBS supports Murabaha and goes live in 6 weeks.", "sam")
        assert enums.FLAG_SE_PROVIDED in a.flags and a.flag_details["se_edit_figures"] == ["6 week"]
        a = review.edit(s, ids["_rfp"], ids["1.1"], "MIBS supports Murabaha.", "sam")  # figure removed again
        assert enums.FLAG_SE_PROVIDED not in a.flags and "se_edit_figures" not in a.flag_details


async def test_editing_to_identical_text_changes_nothing_and_empty_text_is_refused(env):
    ids = await drafted(env, XLSX, {"1.1"})
    with Session(env) as s:
        a0 = s.exec(select(Answer)).one()
        same = review.edit(s, ids["_rfp"], ids["1.1"], review.current_text(a0), "sam")
        assert not same.edited
        with pytest.raises(review.ReviewError):
            review.edit(s, ids["_rfp"], ids["1.1"], "   ", "sam")


async def test_revert_restores_the_agents_draft(env):
    ids = await drafted(env, XLSX, {"1.1"})
    with Session(env) as s:
        original = review.current_text(s.exec(select(Answer)).one())
        review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        review.edit(s, ids["_rfp"], ids["1.1"], "Something else entirely about Murabaha.", "sam")
        a = review.revert_edit(s, ids["_rfp"], ids["1.1"], "sam")
        assert review.current_text(a) == original and not a.edited


async def test_flagging_hands_off_and_withdraws_approval_and_rejects_unknown_owners(env):
    ids = await drafted(env, XLSX, {"1.1"})
    with Session(env) as s:
        review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        with pytest.raises(review.ReviewError, match="Unknown assignee"):
            review.flag(s, ids["_rfp"], ids["1.1"], "The Intern", "", "sam")
        a = review.flag(s, ids["_rfp"], ids["1.1"], "Security Officer", "please confirm", "sam")
        assert a.assigned_to == "Security Officer" and a.review_status == enums.REVIEW_DRAFTED
        assert a.flag_details["handoff_note"] == "please confirm"
        assert review.flag(s, ids["_rfp"], ids["1.1"], None, "", "sam").assigned_to is None


async def test_changing_the_status_needs_a_fresh_approval_and_is_audited(env):
    ids = await drafted(env, XLSX, {"1.1"})
    with Session(env) as s:
        review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        a = review.set_status(s, ids["_rfp"], ids["1.1"], enums.PARTIAL, "sam")
        assert a.compliance_status == enums.PARTIAL and a.review_status == enums.REVIEW_DRAFTED
        with pytest.raises(review.ReviewError):
            review.set_status(s, ids["_rfp"], ids["1.1"], "Mostly fine", "sam")
        ev = s.exec(select(AuditEvent).where(AuditEvent.action == "status_changed")).one()
        assert ev.payload["after"] == enums.PARTIAL


async def test_summary_counts_drive_the_queue_filters(env):
    ids = await drafted(env, XLSX, {"1.1", "1.2", "3.4", "6.1"})
    with Session(env) as s:
        review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        sm = review.summary(s, ids["_rfp"])
    assert sm["total"] == 4 and sm["drafted"] == 4 and sm["accepted"] == 1
    assert sm["owner"] == 1 and sm["clarify"] == 1 and sm["unanswered"] == 0


async def test_review_actions_refuse_a_requirement_that_was_never_drafted_or_belongs_elsewhere(env):
    ids = import_sample(env, XLSX, {"1.1"})
    with Session(env) as s:
        with pytest.raises(review.ReviewError, match="not been drafted"):
            review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        with pytest.raises(review.ReviewError, match="not found"):
            review.accept(s, ids["_rfp"] + 99, ids["1.1"], "sam")


# ====================================================================== SE-supplied facts
async def test_se_fact_is_stored_as_a_dated_authored_source_and_cited_as_such(env):
    import re

    from app.agent.pipeline import draft_requirement
    from app.agent.schemas import DraftOut, Statement
    from app.models import Citation
    from app.services import facts

    def cite_the_se_fact(task, prompt, n):
        """A model that finds the SE's fact among the passages and answers from it."""
        for m in re.finditer(r"\[(\d+)\] ([^\n]*)\n<passage>\n(.*?)\n</passage>", prompt, re.S):
            if "SE-provided fact" in m.group(2):
                body = m.group(3).split("\n")[-1]
                return DraftOut(status="Complied", statements=[Statement(text=body, passages=[int(m.group(1))])],
                                reasoning="from the engineer's fact")
        return DraftOut(status="Cannot Confirm", missing_info=["no passage"])

    ids = import_sample(env, XLSX, {"4.4"})  # AI credit scoring: nothing in the KB, so the answer is a gap
    gw = FakeGateway(cite_the_se_fact)
    await draft_requirement(env, gw, ids["4.4"])
    with Session(env) as s:
        assert s.exec(select(Answer)).one().triage == enums.TRIAGE_CLARIFY  # before the fact: a gap

    with Session(env) as s:
        req = s.get(Requirement, ids["4.4"])
        doc = facts.add_se_fact(
            s, req, "The platform includes a rules-based credit scoring module for retail lending, not machine learning.", "sam")
        assert doc.kind == enums.KB_SE_PROVIDED and doc.owner == "sam" and doc.doc_date and doc.label == "SE-PROVIDED"
    await draft_requirement(env, gw, ids["4.4"], use_cache=False)
    with Session(env) as s:
        a = s.exec(select(Answer)).one()
        assert enums.FLAG_SE_PROVIDED in a.flags and a.compliance_status == enums.COMPLIED
        assert a.flag_details["se_provided"][0]["doc"].startswith("SE-provided fact for 4.4 (sam,")
        assert "rules-based credit scoring" in a.draft_text
        assert any("SE-provided" in c.doc_title for c in s.exec(select(Citation)).all())
    assert "SE-provided fact for 4.4 (sam, 2026-10-31)" in gw.drafts()[-1][1]


async def test_a_trivial_fact_is_refused(env):
    from app.services import facts

    ids = import_sample(env, XLSX, {"4.4"})
    with Session(env) as s, pytest.raises(ValueError):
        facts.add_se_fact(s, s.get(Requirement, ids["4.4"]), "yes", "sam")


# ====================================================================== export: XLSX
async def test_xlsx_export_fills_only_approved_answers_in_place_with_the_rfps_own_codes(env):
    ids = await drafted(env, XLSX)
    with Session(env) as s:
        for rid in ("1.1", "1.2", "3.5"):
            review.accept(s, ids["_rfp"], ids[rid], "sam")
        res = export_service.export_response(s, ids["_rfp"], approved_only=True)
    assert res.filename == "Vendor_RFP-ASIB-2026-017.xlsx" and not res.plan.draft_copy

    wb = openpyxl.load_workbook(res.path)
    ws = wb["Requirements"]
    rows = {str(r[0].value): r for r in ws.iter_rows(min_row=6) if r[0].value and not str(r[0].value).startswith("Section")}
    assert list(rows)[:3] == ["1.1", "1.2", "1.3"] and len(rows) == 30  # every ID, in the RFP's order
    for rid in ("1.1", "1.2", "3.5"):
        assert rows[rid][3].value and "[1]" in rows[rid][3].value and rows[rid][4].value == "C"
        assert rows[rid][5].value.startswith("Sources: [1] ")
    assert rows["1.3"][3].value is None and rows["1.3"][4].value is None  # not approved: left blank
    assert rows["6.1"][3].value is None  # owner-routed: never written

    assert len(ws.merged_cells.ranges) == 6  # the buyer's section bands survived
    assert ws.data_validations.dataValidation  # so did the C/PC/NC drop-down
    assert [c.value for c in ws[5]][:6] == ["Ref", "Requirement", "M/O", "Vendor Response", "Compliance (C / PC / NC)", "Comments"]
    for name in ("Compliance Matrix", "Clarifications", "Source Index", "Export Notes"):
        assert name in wb.sheetnames
    notes = {r[0].value: r[1].value for r in wb["Export Notes"].iter_rows()}
    assert notes["Status"] == "Approved answers only" and notes["Approved"] == 3


async def test_a_review_copy_marks_every_unapproved_answer_and_the_file_name_says_draft(env):
    ids = await drafted(env, XLSX, {"1.1", "1.2"})
    with Session(env) as s:
        review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        res = export_service.export_response(s, ids["_rfp"], approved_only=False)
    assert res.filename == "Vendor_RFP-ASIB-2026-017_DRAFT.xlsx" and res.plan.draft_copy
    ws = openpyxl.load_workbook(res.path)["Requirements"]
    cells = {str(r[0].value): r[3].value for r in ws.iter_rows(min_row=6) if r[0].value}
    assert not cells["1.1"].startswith("[DRAFT") and cells["1.2"].startswith("[DRAFT - not approved] ")
    notes = {r[0].value: r[1].value for r in openpyxl.load_workbook(res.path)["Export Notes"].iter_rows()}
    assert notes["Status"].startswith("DRAFT")


async def test_cannot_confirm_never_gets_a_compliance_code_even_when_accepted(env):
    ids = await drafted(env, XLSX, {"3.4"})  # ISO 22301: no evidence
    with Session(env) as s:
        review.accept(s, ids["_rfp"], ids["3.4"], "sam")
        res = export_service.export_response(s, ids["_rfp"])
    ws = openpyxl.load_workbook(res.path)["Requirements"]
    row = next(r for r in ws.iter_rows(min_row=6) if r[0].value == "3.4")
    assert "No approved source" in row[3].value and row[4].value is None


async def test_an_answer_over_the_rfps_word_limit_blocks_a_submission_export(env):
    ids = await drafted(env, XLSX, {"1.1"})
    long_text = " ".join(["word"] * 130)  # the RFP allows 120
    with Session(env) as s:
        review.edit(s, ids["_rfp"], ids["1.1"], long_text, "sam")
        review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        chk = export_service.check(s, ids["_rfp"])
        assert chk["ready"] is False
        blocking = [i for i in chk["issues"] if i["severity"] == "blocking"][0]
        assert blocking["code"] == "over_word_limit" and blocking["requirements"] == ["1.1"] and "120-word" in blocking["message"]
        with pytest.raises(export_service.ExportBlocked):
            export_service.export_response(s, ids["_rfp"])
        assert export_service.export_response(s, ids["_rfp"], allow_blocking=True).path.exists()  # SE's call


async def test_check_warns_about_unanswered_mandatory_items_and_unconfirmed_rules(env):
    ids = await drafted(env, XLSX, {"1.1", "1.2", "6.1"})
    with Session(env) as s:
        review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        chk = export_service.check(s, ids["_rfp"])
    by = {i["code"]: i for i in chk["issues"]}
    assert chk["ready"] and by["mandatory_unanswered"]["requirements"] == ["1.2", "6.1"]
    assert "rules_unconfirmed" in by and chk["counts"]["approved"] == 1


async def test_compliance_matrix_has_a_row_per_requirement_with_review_state(env):
    ids = await drafted(env, XLSX, {"1.1", "1.2", "6.1"})
    with Session(env) as s:
        review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        res = export_service.export_compliance_matrix(s, ids["_rfp"])
    ws = openpyxl.load_workbook(res.path)["Compliance Matrix"]
    head = [c.value for c in ws[1]]
    col = {name: i for i, name in enumerate(head)}  # by name, so adding a column never breaks this
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
    assert [r[0] for r in rows] == ["1.1", "1.2", "6.1"]
    states = {r[0]: r[col["Export state"]] for r in rows}
    assert states == {"1.1": "Approved", "1.2": "Draft", "6.1": "Not included"}
    assert rows[0][col["Approved by"]] == "sam"


# ====================================================================== export: DOCX
async def test_docx_export_writes_responses_under_each_requirement_and_into_the_table(env):
    ids = await drafted(env, DOCX)
    accept_all_with_text(env, ids)
    with Session(env) as s:
        res = export_service.export_response(s, ids["_rfp"])
    assert res.filename == "Vendor_NFH-RFP-2026-09_Response.docx"
    d = docx.Document(str(res.path))
    texts = [p.text for p in d.paragraphs]

    i = next(n for n, t in enumerate(texts) if t.startswith("2.1.1 "))
    assert texts[i + 1].startswith("Response: ") and texts[i + 2] == "Compliance: Fully Compliant"
    assert texts[i + 3].startswith("Sources: [1] ")
    assert texts[i + 4].startswith("2.1.2 ")  # the next requirement follows, nothing displaced

    t = d.tables[0]  # section 4 was a table: three columns were added
    assert [c.text for c in t.rows[0].cells] == ["Ref", "Requirement", "Priority", "Response", "Compliance", "Sources"]
    row = next(r for r in t.rows[1:] if r.cells[0].text == "4.1.2")
    assert row.cells[3].text and row.cells[4].text and row.cells[5].text.startswith("[1] ")
    assert "Source Index" in texts and any(h == "Clarification Questions" for h in texts)


async def test_docx_export_of_a_pdf_rfp_rebuilds_the_structure_with_every_id_in_order(env):
    ids = await drafted(env, PDF)
    accept_all_with_text(env, ids)
    with Session(env) as s:
        res = export_service.export_response(s, ids["_rfp"])
    assert res.filename == "Vendor_PGF-RFP-0426.docx"  # a PDF RFP is answered in Word, as it asked
    d = docx.Document(str(res.path))
    headings = [p.text for p in d.paragraphs if p.style.name.startswith("Heading")]
    assert headings[:4] == ["Section TR: Technical Requirements", "Section SC: Security and Compliance",
                            "Section DL: Delivery", "Section CM: Commercial"]
    refs = [r.cells[0].text for t in d.tables[:4] for r in t.rows[1:]]
    assert refs == ["TR-01", "TR-02", "TR-03", "TR-04", "TR-05", "TR-06", "TR-07", "TR-08", "SC-01", "SC-02", "SC-03",
                    "SC-04", "SC-05", "SC-06", "SC-07", "DL-01", "DL-02", "CM-01", "CM-02", "CM-03"]
    assert [c.text for c in d.tables[0].rows[0].cells] == ["Ref", "Requirement", "Response", "Compliance", "Sources"]


async def test_docx_review_copy_carries_a_visible_draft_banner(env):
    ids = await drafted(env, DOCX, {"2.1.1", "2.1.2"})
    with Session(env) as s:
        res = export_service.export_response(s, ids["_rfp"], approved_only=False)
    assert res.filename.endswith("_DRAFT.docx")
    d = docx.Document(str(res.path))
    assert d.paragraphs[0].text.startswith("DRAFT: this copy contains answers that have not been approved")
    assert any(p.text.startswith("Response: [DRAFT - not approved]") for p in d.paragraphs)


# ====================================================================== the HTTP surface
def test_review_and_export_api_end_to_end(client, monkeypatch):
    import app.routers.agent as agent_router
    import app.routers.review as review_router
    from app.llm.errors import LLMError

    gw = FakeGateway(lambda t, p, n: LLMError("x") if t == "fast" else grounded(p))
    monkeypatch.setattr(agent_router, "get_gateway", lambda: gw)
    monkeypatch.setattr(review_router, "get_gateway", lambda: gw)

    client.post("/api/kb/index-samples")
    rid = client.post("/api/rfps", files={"file": (XLSX, (SAMPLES / "rfps" / XLSX).read_bytes())}).json()["id"]
    client.post(f"/api/rfps/{rid}/draft")
    for _ in range(150):
        if client.get(f"/api/rfps/{rid}/job").json()["status"] == "done":
            break
        time.sleep(0.1)
    reqs = {a["req_id"]: a["requirement_id"] for a in client.get(f"/api/rfps/{rid}/answers").json()}

    assert client.post(f"/api/rfps/{rid}/answers/{reqs['6.1']}/accept").status_code == 422  # nothing to accept
    a = client.post(f"/api/rfps/{rid}/answers/{reqs['1.1']}/accept", json={"reviewer": "dana"}).json()
    assert a["review_status"] == "accepted"
    e = client.post(f"/api/rfps/{rid}/answers/{reqs['1.1']}/edit", json={"text": "MIBS supports Murabaha.", "reviewer": "dana"}).json()
    assert e["review_status"] == "drafted" and e["text"] == "MIBS supports Murabaha." and e["edited"]
    client.post(f"/api/rfps/{rid}/answers/{reqs['1.1']}/accept", json={"reviewer": "dana"})
    f = client.post(f"/api/rfps/{rid}/answers/{reqs['1.2']}/flag", json={"assignee": "SME", "note": "check"}).json()
    assert f["assigned_to"] == "SME"
    assert client.post(f"/api/rfps/{rid}/answers/{reqs['1.2']}/flag", json={"assignee": "Nobody"}).status_code == 422
    assert client.post(f"/api/rfps/{rid}/answers/{reqs['1.3']}/status", json={"status": "Mostly"}).status_code == 422

    summ = client.get(f"/api/rfps/{rid}/summary").json()
    # "flagged" = waiting on someone else: the 5 owner-routed items (pricing, SLA, liability, payment, references)
    # plus the one the SE handed to an SME
    assert summ["accepted"] == 1 and summ["owner"] == 5 and summ["flagged"] == 6 and summ["total"] == 30

    chk = client.get(f"/api/rfps/{rid}/export/check").json()
    assert chk["ready"] and chk["counts"]["approved"] == 1
    r = client.post(f"/api/rfps/{rid}/export")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    assert 'filename="Vendor_RFP-ASIB-2026-017.xlsx"' in r.headers["content-disposition"] and r.headers["x-export-draft"] == "false"
    out = Path(client.app.state.__dict__.get("_x", "")) if False else None  # noqa: F841
    wb = openpyxl.load_workbook(__import__("io").BytesIO(r.content))
    assert "Requirements" in wb.sheetnames

    d = client.post(f"/api/rfps/{rid}/export?approved_only=false")
    assert d.headers["x-export-draft"] == "true" and "_DRAFT.xlsx" in d.headers["content-disposition"]
    assert client.get(f"/api/rfps/{rid}/export/matrix").status_code == 200

    audit = client.get(f"/api/rfps/{rid}/audit").json()
    actions = {x["action"] for x in audit}
    assert {"imported", "drafted", "accepted", "edited", "flagged", "exported"} <= actions
    assert next(x for x in audit if x["action"] == "accepted")["actor"] == "dana"


def test_putting_format_rules_marks_them_confirmed_and_export_blocks_on_a_tight_limit(client):
    rid = client.post("/api/rfps", files={"file": (XLSX, (SAMPLES / "rfps" / XLSX).read_bytes())}).json()["id"]
    rules = client.get(f"/api/rfps/{rid}/format-rules").json()
    assert rules["word_limit"] == 120 and rules["confirmed"] is False
    saved = client.put(f"/api/rfps/{rid}/format-rules", json={**rules, "word_limit": 40}).json()
    assert saved["confirmed"] is True and saved["word_limit"] == 40 and saved["keep_ids"] is True
    assert client.get(f"/api/rfps/{rid}").json()["format_rules"]["word_limit"] == 40
    assert client.get("/api/rfps/999/format-rules").status_code == 404


async def test_a_review_copy_only_warns_about_word_limits_while_a_submission_copy_is_blocked(env):
    ids = await drafted(env, XLSX, {"1.1"})
    with Session(env) as s:
        review.edit(s, ids["_rfp"], ids["1.1"], " ".join(["word"] * 130), "sam")
        draft_check = export_service.check(s, ids["_rfp"], approved_only=False)
        sev = {i["code"]: i["severity"] for i in draft_check["issues"]}
        assert draft_check["ready"] is True and sev["over_word_limit"] == "warning"
        assert export_service.export_response(s, ids["_rfp"], approved_only=False).filename.endswith("_DRAFT.xlsx")

        review.accept(s, ids["_rfp"], ids["1.1"], "sam")
        final = export_service.check(s, ids["_rfp"], approved_only=True)
        assert final["ready"] is False and {i["code"]: i["severity"] for i in final["issues"]}["over_word_limit"] == "blocking"

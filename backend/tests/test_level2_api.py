"""Level 2 through the API: matrix, gaps, validation, the review gate, the checklist, prioritised clarifications."""

import asyncio

import pytest
from sqlmodel import Session, select

from app import enums
from app.agent.assess import AssessOut, Part, assess_requirement
from app.agent.priority import impact_of, priority_of
from app.db import get_engine
from app.models import Answer, AuditEvent, ClarificationQuery, Requirement, Rfp
from tests.helpers import FakeGateway
from tests.test_assess import make


def assessed(client, key="std", **kw):
    """Seed one drafted requirement in the running app's database and assess it with a scripted model."""
    eng = get_engine()
    pk, quote = make(eng, key, **kw)
    cap = {"std": "standard", "cfg": "configuration", "dev": "development", "third": "third_party", "not": "not_available"}[key]
    gw = FakeGateway(lambda t, p, n: AssessOut(parts=[Part(what="the thing", capability=cap, passages=[1], quote=quote)]))
    asyncio.run(assess_requirement(eng, gw, pk))
    with Session(eng) as s:
        return s.get(Requirement, pk).rfp_id, pk


def audit_actions(rfp_id):
    with Session(get_engine()) as s:
        return [e.action for e in s.exec(select(AuditEvent).where(AuditEvent.rfp_id == rfp_id)).all()]


def test_the_matrix_joins_the_requirement_its_answer_and_its_assessment(client):
    rfp, pk = assessed(client, "third")
    body = client.get(f"/api/rfps/{rfp}/assessments").json()
    assert body["scheme"]["kind"] == "capability" and body["assessing"] is False
    row = body["rows"][0]
    assert (row["req_id"], row["capability"], row["code"], row["gap_owner"]) == ("1.1", "third_party", "T", "Solution Architecture")
    assert row["assessment_current"] is True and row["status"] == "Complied"


def test_an_assessment_goes_out_of_date_when_the_answer_changes_but_not_when_it_is_accepted(client):
    rfp, pk = assessed(client, "std")
    client.post(f"/api/rfps/{rfp}/answers/{pk}/accept", json={"reviewer": "se"})
    assert client.get(f"/api/rfps/{rfp}/assessments").json()["rows"][0]["assessment_current"] is True
    client.post(f"/api/rfps/{rfp}/answers/{pk}/edit", json={"text": "SLC supports Murabaha for SME customers only.", "reviewer": "se"})
    assert client.get(f"/api/rfps/{rfp}/assessments").json()["rows"][0]["assessment_current"] is False


def test_a_person_can_validate_correct_and_complete_a_suggested_code(client):
    rfp, pk = assessed(client, "dev", status=enums.PARTIAL)
    url = f"/api/rfps/{rfp}/assessments/{pk}"
    r = client.patch(url, json={"estimate": "15 person-days", "attachments": ["design-note.pdf"], "confirm": True, "reviewer": "ana"})
    row = r.json()
    assert (row["confirmed"], row["confirmed_by"], row["estimate"], row["attachments"]) == (True, "ana", "15 person-days", ["design-note.pdf"])
    r = client.patch(url, json={"capability": "configuration", "reviewer": "ana"}).json()  # an expert overrides the model
    assert (r["capability"], r["code"], r["needs"]) == ("configuration", "C", ["explanation"])
    assert r["gaps"] == [] and r["parts"][0]["reason"] == "Chosen by ana"
    assert "assessment_confirmed" in audit_actions(rfp) and "assessment_edited" in audit_actions(rfp)


def test_a_code_the_rfp_does_not_have_and_a_capability_that_does_not_exist_are_refused(client):
    rfp, pk = assessed(client, "std")
    url = f"/api/rfps/{rfp}/assessments/{pk}"
    assert client.patch(url, json={"code": "Z"}).status_code == 422
    assert client.patch(url, json={"capability": "magic"}).status_code == 422
    assert client.patch(f"/api/rfps/{rfp}/assessments/99999", json={"confirm": True}).status_code == 404


def test_gaps_keep_a_confirmed_product_gap_apart_from_a_knowledge_gap(client):
    eng = get_engine()
    rfp, _ = assessed(client, "not", status=enums.NOT_COMPLIED, text="SLC does not support Tawarruq. [1]")
    # a second requirement in another RFP would not show here, so add an unevidenced one to the same RFP
    with Session(eng) as s:
        req = Requirement(rfp_id=rfp, req_id="1.2", text="Provide voice biometrics.", order=2, mandatory=True)
        s.add(req); s.flush()
        s.add(Answer(requirement_id=req.id, draft_text="No approved source confirms this.", compliance_status=enums.CANNOT_CONFIRM,
                     flags=[enums.FLAG_MISSING], triage=enums.TRIAGE_CLARIFY, review_status=enums.REVIEW_DRAFTED, confidence=0.2))
        s.commit(); rid = req.id
    asyncio.run(assess_requirement(eng, FakeGateway(), rid))
    g = client.get(f"/api/rfps/{rfp}/gaps").json()
    assert g["counts"][enums.GAP_CONFIRMED] == 1 and g["counts"][enums.GAP_KNOWLEDGE] == 1
    by_req = {i["req_id"]: i["type"] for i in g["items"]}
    assert by_req == {"1.1": enums.GAP_CONFIRMED, "1.2": enums.GAP_KNOWLEDGE}
    assert g["by_owner"]["Product Management"] == ["1.1", "1.2"]


def test_the_review_gate_blocks_an_edit_that_adds_a_claim_no_source_states(client):
    rfp, pk = assessed(client, "std")
    assert client.get(f"/api/rfps/{rfp}/readiness").json()["can_proceed"] is True
    client.post(f"/api/rfps/{rfp}/answers/{pk}/edit", json={"reviewer": "se",
        "text": "SLC supports Murabaha for retail and SME customers. It also holds a PCI DSS Level 1 attestation issued in 2025."})
    state = client.get(f"/api/rfps/{rfp}/readiness").json()
    assert state["can_proceed"] is False
    assert [b["code"] for b in state["blockers"]] == ["unsupported_claims"] and state["blockers"][0]["requirements"] == ["1.1"]


def test_proceeding_over_a_blocker_needs_a_reason_and_is_recorded(client):
    rfp, pk = assessed(client, "std")
    client.post(f"/api/rfps/{rfp}/answers/{pk}/edit", json={"reviewer": "se", "text": "SLC holds ISO 22301 certification issued in 2026."})
    url = f"/api/rfps/{rfp}/proceed-to-review"
    r = client.post(url, json={})
    assert r.status_code == 409 and r.json()["detail"]["blockers"][0]["code"] == "unsupported_claims"
    assert client.post(url, json={"acknowledge": True, "reason": "  "}).status_code == 409  # a reason is required
    ok = client.post(url, json={"acknowledge": True, "reason": "Owner is confirming the certificate", "reviewer": "ana"})
    assert ok.status_code == 200 and ok.json()["in_review"] is True
    with Session(get_engine()) as s:
        ev = s.exec(select(AuditEvent).where(AuditEvent.action == "proceeded_to_review")).one()
        assert ev.actor == "ana" and ev.payload["acknowledged_blockers"] == ["unsupported_claims"]
        assert s.get(Rfp, rfp).status == "in_review"


def test_a_mandatory_requirement_nobody_is_dealing_with_blocks_but_one_with_an_owner_does_not(client):
    rfp, _ = assessed(client, "std")
    eng = get_engine()
    with Session(eng) as s:
        for rid, assigned in (("1.2", None), ("1.3", "Commercial Desk")):
            req = Requirement(rfp_id=rfp, req_id=rid, text="Provide pricing.", order=2, mandatory=True)
            s.add(req); s.flush()
            s.add(Answer(requirement_id=req.id, draft_text="", compliance_status=enums.PENDING, assigned_to=assigned,
                         triage=enums.TRIAGE_OWNER if assigned else enums.TRIAGE_AUTO, review_status=enums.REVIEW_DRAFTED))
        s.commit()
    state = client.get(f"/api/rfps/{rfp}/readiness").json()
    blocker = next(b for b in state["blockers"] if b["code"] == "mandatory_unanswered")
    assert blocker["requirements"] == ["1.2"]
    assert any(i["code"] == "awaiting_owner" and i["requirements"] == ["1.3"] for i in state["info"])


def test_codes_resting_on_incomplete_evidence_are_warnings_until_a_person_validates_them(client):
    rfp, pk = assessed(client, "std", flags=[enums.FLAG_STALE])
    warn = {w["code"] for w in client.get(f"/api/rfps/{rfp}/readiness").json()["warnings"]}
    assert {"codes_need_validation", "stale_or_conflicting"} <= warn
    client.patch(f"/api/rfps/{rfp}/assessments/{pk}", json={"confirm": True})
    warn = {w["code"] for w in client.get(f"/api/rfps/{rfp}/readiness").json()["warnings"]}
    assert "codes_need_validation" not in warn


def test_the_submission_checklist_lists_the_annexures_the_instructions_ask_for(client):
    rfp, _ = assessed(client, "std")
    with Session(get_engine()) as s:
        s.get(Rfp, rfp).submission_instructions = ("Submit by 15 December 2026. Attach a copy of your ISO 27001 certificate "
                                                   "as Annexure A. Include the company profile.")
        s.commit()
    cl = client.get(f"/api/rfps/{rfp}/checklist").json()
    manual = [i for i in cl["items"] if i["kind"] == "manual"]
    assert len(manual) == 2 and all(i["status"] == "outstanding" for i in manual) and cl["complete"] is False
    after = client.put(f"/api/rfps/{rfp}/checklist/{manual[0]['key']}", json={"done": True, "reviewer": "ana"}).json()
    assert next(i for i in after["items"] if i["key"] == manual[0]["key"])["status"] == "done"
    assert "checklist_item_ticked" in audit_actions(rfp)
    assert client.put(f"/api/rfps/{rfp}/checklist/nope", json={}).status_code == 404


def test_assessing_without_model_keys_says_so(client):
    rfp, _ = assessed(client, "std")
    assert client.post(f"/api/rfps/{rfp}/assess").status_code == 503


def test_clarifications_carry_priority_and_a_customer_reply_settles_them(client):
    rfp, _ = assessed(client, "std")
    with Session(get_engine()) as s:
        for text, prio in (("Low one?", "low"), ("High one?", "high")):
            s.add(ClarificationQuery(rfp_id=rfp, text=text, reason="r", requirement_ids=["1.1"], priority=prio))
        s.commit()
    listed = client.get(f"/api/rfps/{rfp}/clarifications").json()
    assert [c["priority"] for c in listed] == ["high", "low"]  # most consequential first
    cid = listed[0]["id"]
    url = f"/api/rfps/{rfp}/clarifications/{cid}"
    assert client.patch(url, json={"priority": "urgent"}).status_code == 422
    assert client.patch(url, json={"assigned_to": "Nobody"}).status_code == 422
    r = client.patch(url, json={"answer": "Yes, from day one.", "assigned_to": "Solution Architecture"}).json()
    assert (r["status"], r["answer"], r["assigned_to"]) == ("answered", "Yes, from day one.", "Solution Architecture")


@pytest.mark.parametrize("mandatory,n,impact,source,want", [
    (True, 1, "", "missing_evidence", "medium"),
    (True, 1, "design", "missing_evidence", "medium"),
    (True, 2, "design", "missing_evidence", "high"),
    (True, 1, "design", "ambiguity", "high"),
    (False, 1, "compliance", "missing_evidence", "low"),
    (False, 3, "commercial", "ambiguity", "high"),
])
def test_priority_rules(mandatory, n, impact, source, want):
    assert priority_of(mandatory=mandatory, n_requirements=n, impact=impact, source=source)[0] == want


@pytest.mark.parametrize("text,want", [
    ("How many concurrent users and transactions per day?", "commercial"),
    ("Describe the integration with our core banking system.", "design"),
    ("What is the expected go-live date and training plan?", "delivery"),
    ("Confirm the AAOIFI standards that apply.", "compliance"),
    ("Provide a description.", ""),
    ("Late-payment charges must be directed to charity.", ""),  # "payment" alone is a product feature, not pricing
])
def test_impact_rules(text, want):
    assert impact_of(text) == want

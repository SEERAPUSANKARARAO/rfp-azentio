"""Understanding the requirements: dependencies, components, unclear wording, contradictions between answers."""

import asyncio

import pytest
from sqlmodel import Session, select

from app import enums
from app.agent.clarify import build_clarifications
from app.agent.crosscheck import inconsistent_answers, unsupported_claims
from app.agent.interpret import (InterpOut, Problem, ReqInterp, cross_requirement_findings, interpret_rfp,
                                 rule_findings, validate_model)
from app.db import init_db, make_engine
from app.llm.errors import AllProvidersExhausted
from app.models import Answer, ClarificationQuery, Requirement, Rfp
from tests.helpers import FakeGateway

KNOWN = {"1.1", "1.2", "4.3", "4.4"}


# ---------------------------------------------------------------- rules
def test_vague_words_are_flagged_as_not_measurable():
    f, _, _ = rule_findings("Provide adequate reporting capabilities as required by the bank.", "4.3", KNOWN)
    details = " ".join(x["detail"] for x in f)
    assert "'adequate'" in details and "'as required'" in details and all(x["kind"] == "ambiguous" for x in f)


def test_a_quantity_with_no_number_is_incomplete_but_a_stated_one_is_not():
    vague, _, _ = rule_findings("The solution must support high concurrent users with fast response times.", "4.2", KNOWN)
    assert any(x["kind"] == "incomplete" for x in vague)
    clear, _, _ = rule_findings("The solution must support 5,000 concurrent users.", "4.2", KNOWN)
    assert clear == []


def test_a_document_that_is_referred_to_but_not_supplied_is_found():
    f, _, ext = rule_findings("Integrate with the core banking system described in Annexure B.", "2.4", KNOWN)
    assert ext == ["Annexure B"] and any("not part of the RFP file" in x["detail"] for x in f)


def test_an_explicit_reference_to_another_requirement_is_a_dependency_only_if_that_requirement_exists():
    _, deps, _ = rule_findings("The approval workflow must work exactly as in requirement 1.2 and clause 9.9.", "4.4", KNOWN)
    assert deps == [{"req_id": "1.2", "how": "explicit"}]


def test_a_clear_requirement_has_no_findings():
    assert rule_findings("The solution must support Murabaha financing for SME customers.", "1.1", KNOWN) == ([], [], [])


# ---------------------------------------------------------------- what the model says is checked
TEXTS = {"1.1": "Funds must be available adequately.", "1.2": "Funds must never be available.", "4.3": "Provide adequate reports."}


def test_a_problem_must_quote_the_requirements_own_words():
    out = InterpOut(requirements=[ReqInterp(req_id="4.3", problems=[
        Problem(kind="ambiguous", detail="'adequate' cannot be measured"),
        Problem(kind="ambiguous", detail="'world-class' cannot be measured"),  # not in the text: invented
        Problem(kind="ambiguous", detail="It is unclear."),  # no quote at all
    ])])
    f = validate_model(out, TEXTS)["4.3"]["findings"]
    assert [x["detail"] for x in f] == ["'adequate' cannot be measured"]


def test_a_contradiction_must_name_the_other_requirement_or_it_is_only_an_ambiguity():
    out = InterpOut(requirements=[ReqInterp(req_id="1.1", problems=[
        Problem(kind="contradictory", detail="'available' conflicts with 1.2", with_ids=["1.2"]),
        Problem(kind="contradictory", detail="'adequately' conflicts with something"),
    ])])
    f = validate_model(out, TEXTS)["1.1"]["findings"]
    assert [(x["kind"], x["with"]) for x in f] == [("contradictory", ["1.2"]), ("ambiguous", [])]


def test_invented_ids_and_self_references_are_dropped_and_components_are_capped():
    out = InterpOut(requirements=[
        ReqInterp(req_id="1.1", depends_on=["1.2", "9.9", "1.1"], components=["A", "B", "C", "D", "a"]),
        ReqInterp(req_id="7.7", depends_on=["1.1"])])  # a requirement that does not exist
    got = validate_model(out, TEXTS)
    assert list(got) == ["1.1"] and got["1.1"]["depends_on"] == ["1.2"] and got["1.1"]["components"] == ["A", "B", "C"]


# ---------------------------------------------------------------- over a whole RFP
@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path / "i.db")
    init_db(eng)
    with Session(eng) as s:
        rfp = Rfp(name="R", filename="r.xlsx", file_type="xlsx", stored_path="x")
        s.add(rfp); s.flush()
        for i, (rid, text) in enumerate([
            ("1.2", "Approval workflows must be configurable per product."),
            ("4.3", "Provide adequate reporting capabilities as required by the bank."),
            ("4.4", "The approval workflow must work exactly as in requirement 1.2."),
        ], 1):
            s.add(Requirement(rfp_id=rfp.id, req_id=rid, text=text, order=i, mandatory=True))
        s.commit()
    return eng


def interp(engine):
    with Session(engine) as s:
        return {r.req_id: r.interpretation for r in s.exec(select(Requirement)).all()}


def test_the_rules_alone_find_the_dependency_and_the_vague_wording_and_invert_the_dependency(engine):
    res = asyncio.run(interpret_rfp(engine, None, 1))
    got = interp(engine)
    assert got["4.4"]["depends_on"] == [{"req_id": "1.2", "how": "explicit"}]
    assert got["1.2"]["depended_on_by"] == ["4.4"]
    assert any("'adequate'" in f["detail"] for f in got["4.3"]["findings"])
    assert "rules only" in res["mode"] and res["with_dependencies"] == 1


def test_the_model_adds_implied_dependencies_components_and_cross_requirement_problems(engine):
    def script(task, prompt, n):
        assert task == "structure"
        return InterpOut(requirements=[ReqInterp(req_id="4.3", depends_on=["1.2"], components=["Reporting", "Workflow"],
                                                 problems=[Problem(kind="ambiguous", detail="'as required' is open")])])
    res = asyncio.run(interpret_rfp(engine, FakeGateway(script), 1))
    got = interp(engine)
    assert {"req_id": "1.2", "how": "implied"} in got["4.3"]["depends_on"]
    assert got["4.3"]["components"] == ["Reporting", "Workflow"] and got["4.3"]["spans_multiple"] is True
    assert res["mode"] == "rules and model" and res["spanning_components"] == 1
    assert sum(f["detail"].startswith("'as required'") for f in got["4.3"]["findings"]) == 1  # the rule found it too: merged


@pytest.mark.parametrize("reply,mode", [
    (AllProvidersExhausted("spent", resume_at=1.0, attempts=[]), "quota"),
    (object(), "not usable"),
])
def test_when_the_model_is_unavailable_the_rule_findings_still_land(engine, reply, mode):
    asyncio.run(interpret_rfp(engine, FakeGateway(lambda t, p, n: reply), 1))
    got = interp(engine)
    assert got["4.4"]["depends_on"] and got["4.3"]["findings"]
    with Session(engine) as s:
        from app.models import AuditEvent
        assert mode in s.exec(select(AuditEvent).where(AuditEvent.action == "requirements_interpreted")).one().payload["mode"]


# ---------------------------------------------------------------- unclear requirements become clarification questions
def test_an_unclear_requirement_is_asked_about_once_with_its_unmet_evidence_merged_in(engine):
    asyncio.run(interpret_rfp(engine, None, 1))
    with Session(engine) as s:
        for r in s.exec(select(Requirement)).all():
            if r.req_id == "4.3":  # also has a documentation gap: the two reasons must become ONE question
                s.add(Answer(requirement_id=r.id, draft_text="No source confirms this.", compliance_status=enums.CANNOT_CONFIRM,
                             triage=enums.TRIAGE_CLARIFY, flags=[enums.FLAG_MISSING], review_status=enums.REVIEW_DRAFTED,
                             flag_details={"missing_info": ["the reporting catalogue"]}))
            else:
                s.add(Answer(requirement_id=r.id, draft_text="Fine.", compliance_status=enums.COMPLIED, review_status=enums.REVIEW_DRAFTED))
        s.commit()
    n = asyncio.run(build_clarifications(engine, None, 1))
    with Session(engine) as s:
        qs = s.exec(select(ClarificationQuery)).all()
    assert n == 1 and len(qs) == 1
    q = qs[0]
    assert q.requirement_ids == ["4.3"] and q.source == "ambiguity" and q.priority in ("medium", "high")


# ---------------------------------------------------------------- answers must not contradict each other
def row(rid, text):
    return {"req_id": rid, "answer_text": text}


def test_different_figures_for_the_same_thing_in_two_answers_are_found():
    out = inconsistent_answers([row("3.2", "The RTO is 2 hours and the RPO is 15 minutes [1]."),
                                row("6.5", "Failover targets an RTO of 4 hours. The RPO is 15 minutes.")])
    assert len(out) == 1 and out[0]["topic"] == "RTO" and out[0]["requirements"] == ["3.2", "6.5"]
    assert out[0]["values"] == {"2 hours": ["3.2"], "4 hours": ["6.5"]}  # RPO agrees, so it is not reported


def test_agreement_and_a_single_answers_own_two_figures_are_not_a_contradiction():
    assert inconsistent_answers([row("1", "RTO is 2 hours."), row("2", "The RTO is 2 hours and the RPO 15 minutes.")]) == []
    assert inconsistent_answers([row("1", "Backups are kept for 12 months and for 7 years in the archive.")]) == []


def test_encryption_key_sizes_that_differ_between_answers_are_found():
    out = inconsistent_answers([row("3.1", "Data at rest uses AES-256."), row("7.2", "Backups use AES-128.")])
    assert out and out[0]["topic"] == "encryption key size"


def test_the_gate_blocks_on_contradicting_answers(client):
    from app.db import get_engine
    from tests.test_assess import make
    eng = get_engine()
    pk, _ = make(eng, "std", text="The RTO is 2 hours. [1]")
    with Session(eng) as s:
        rfp_id = s.get(Requirement, pk).rfp_id
        req = Requirement(rfp_id=rfp_id, req_id="9.1", text="State the RTO.", order=2, mandatory=True)
        s.add(req); s.flush()
        s.add(Answer(requirement_id=req.id, draft_text="The RTO is 4 hours.", compliance_status=enums.COMPLIED,
                     review_status=enums.REVIEW_DRAFTED, confidence=0.9))
        s.commit()
    codes = [b["code"] for b in client.get(f"/api/rfps/{rfp_id}/readiness").json()["blockers"]]
    assert "inconsistent_answers" in codes


# ---------------------------------------------------------------- edited text is checked against its sources
def test_a_sentence_no_cited_source_supports_is_found_in_the_current_text():
    evidence = ["Guide\nSLC supports Murabaha for retail and SME customers."]
    assert unsupported_claims("SLC supports Murabaha for retail customers.", evidence) == []
    bad = unsupported_claims("SLC supports Murabaha for retail customers. It holds a PCI DSS Level 1 attestation issued in 2025.", evidence)
    assert bad == ["It holds a PCI DSS Level 1 attestation issued in 2025."]
    assert unsupported_claims("Anything at all.", []) == []  # with no evidence there is nothing to judge here


# ---------------------------------------------------------------- requirements that cannot both be met (by rule)
CLASH = {"3.4": "All customer data must be hosted in the State of Qatar.",
         "3.5": "Customer data must remain hosted in the United Arab Emirates at all times.",
         "3.6": "Backups must be stored in the UAE.",
         "9.1": "The RTO must not exceed 1 hour.", "9.2": "Recovery must meet an RTO of 4 hours.", "9.3": "The RPO is 15 minutes."}


def test_a_required_location_that_another_requirement_rules_out_is_a_contradiction():
    got = cross_requirement_findings(CLASH)
    assert {f["with"][0] for f in got["3.4"]} == {"3.5", "3.6"}
    assert all(f["kind"] == "contradictory" and f["source"] == "rule" for fs in got.values() for f in fs)


def test_the_same_place_under_two_names_is_not_a_contradiction():
    assert "3.5" not in {f["with"][0] for f in cross_requirement_findings(CLASH).get("3.6", [])}  # "UAE" is the United Arab Emirates


def test_different_figures_for_the_same_target_are_a_contradiction_but_different_targets_are_not():
    got = cross_requirement_findings(CLASH)
    assert got["9.1"][0]["with"] == ["9.2"] and "RTO" in got["9.1"][0]["detail"]
    assert "9.3" not in got  # an RPO is a different thing from an RTO


def test_requirements_that_agree_produce_nothing():
    assert cross_requirement_findings({"1": "Data must be hosted in Qatar.", "2": "Backups are stored in Qatar. RTO 2 hours.",
                                       "3": "The RTO is 2 hours."}) == {}


def test_a_model_finding_about_the_same_words_as_a_rule_finding_is_not_added_twice():
    texts = {"1": "All data must be hosted in the State of Qatar.", "2": "Data must remain hosted in the United Arab Emirates."}
    rule = cross_requirement_findings(texts)
    from app.agent.interpret import _merge
    model = [{"kind": "contradictory", "source": "model", "with": ["1"], "detail": '"remain hosted in the United Arab Emirates"'}]
    assert len(_merge(rule["2"], model)) == 1


def test_two_requirements_that_contradict_each_other_raise_one_question_not_two(engine):
    with Session(engine) as s:
        reqs = {r.req_id: r for r in s.exec(select(Requirement)).all()}
        a, b = reqs["1.2"], reqs["4.3"]
        for me, other in ((a, "4.3"), (b, "1.2")):
            me.interpretation = {"findings": [{"kind": "contradictory", "source": "rule", "with": [other],
                                               "detail": '"hosted in Qatar" and "hosted in the UAE" cannot both be met.'}]}
        for r in reqs.values():
            s.add(Answer(requirement_id=r.id, draft_text="Fine.", compliance_status=enums.COMPLIED, review_status=enums.REVIEW_DRAFTED))
        s.commit()
    assert asyncio.run(build_clarifications(engine, None, 1)) == 1
    with Session(engine) as s:
        q = s.exec(select(ClarificationQuery)).one()
    assert sorted(q.requirement_ids) == ["1.2", "4.3"] and q.source == "contradiction" and q.priority == "high"

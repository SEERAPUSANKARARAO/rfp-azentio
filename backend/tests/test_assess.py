"""Capability classification: the model proposes, code decides what stands."""

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app import enums
from app.agent.assess import AssessOut, Part, assess_requirement, assess_rfp, norm_capability
from app.agent.scheme import detect_scheme
from app.db import init_db, make_engine
from app.llm.errors import AllProvidersExhausted, ProviderError
from app.models import Answer, Assessment, Citation, KbChunk, KbDocument, Requirement, Rfp, Section
from tests.helpers import FakeGateway

SCHEME_TEXT = ("Use S = Standard capability, C = Configuration required, D = Development required, "
               "T = Third-party dependency, N = Not available. Codes C and D need an explanation. "
               "D also needs an effort estimate.")

PASSAGES = {
    "std": "Murabaha\nSLC supports Murabaha for retail and SME customers.",
    "cfg": "Late payment\nA late-payment charge can be configured for each product.",
    "dev": "Roadmap\nProfit-rate benchmarking by custom development is planned for a later release.",
    "third": "Takaful\nTakaful on financed assets is supported through API integration with the operator's system.",
    "not": "Exclusions\nSLC does not support Tawarruq-based or Commodity Murabaha products.",
    "plain": "Late payment\nCharges collected are posted to a charity account.",
    "manual": "Early settlement\nSLC calculates an indicative rebate. The rebate must be approved manually by an authorised officer before it is applied.",
}


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path / "a.db")
    init_db(eng)
    return eng


def make(engine, key: str, *, status=enums.COMPLIED, flags=(), text=None, confidence=0.9, scheme_text=SCHEME_TEXT,
         cited=True, triage=enums.TRIAGE_AUTO, req="Support this capability.", mandatory=True):
    with Session(engine) as s:
        rfp = Rfp(name="R", filename="r.xlsx", file_type="xlsx", stored_path="x",
                  format_rules={"response_scheme": detect_scheme(scheme_text)} if scheme_text else {})
        s.add(rfp); s.flush()
        sec = Section(rfp_id=rfp.id, code="1", title="S", order=1); s.add(sec); s.flush()
        r = Requirement(rfp_id=rfp.id, section_id=sec.id, req_id="1.1", text=req, order=1, mandatory=mandatory)
        s.add(r); s.flush()
        doc = KbDocument(title="Guide", display_title="Guide", kind="product_doc", filename="g.md", version="6.2",
                         doc_date=date(2026, 7, 1))
        s.add(doc); s.flush()
        chunk = KbChunk(doc_id=doc.id, ordinal=0, text=PASSAGES[key], location="Section")
        s.add(chunk); s.flush()
        quote = PASSAGES[key].split("\n", 1)[1]
        ans = Answer(requirement_id=r.id, draft_text=text if text is not None else f"{quote} [1]",
                     compliance_status=status, flags=list(flags), confidence=confidence, triage=triage,
                     review_status=enums.REVIEW_DRAFTED)
        s.add(ans); s.flush()
        if cited:
            s.add(Citation(answer_id=ans.id, chunk_id=chunk.id, doc_id=doc.id, marker=1, quote=quote,
                           doc_title="Guide", doc_date=doc.doc_date, location="Section"))
        s.commit()
        return r.id, quote


def run(engine, req_pk, parts, basis="b"):
    gw = FakeGateway(lambda task, prompt, n: AssessOut(parts=parts, basis=basis))
    outcome = asyncio.run(assess_requirement(engine, gw, req_pk))
    with Session(engine) as s:
        a = s.exec(select(Assessment).where(Assessment.requirement_id == req_pk)).first()
        return outcome, a, gw


def part(what, cap, quote, n=1):
    return Part(what=what, capability=cap, passages=[n], quote=quote)


def test_a_supported_standard_capability_gets_the_rfps_own_code_and_needs_no_validation(engine):
    pk, q = make(engine, "std")
    outcome, a, gw = run(engine, pk, [part("Murabaha", "standard", q)])
    assert outcome == "assessed" and gw.calls[0][0] == "assess"
    assert (a.capability, a.code, a.needs, a.needs_validation) == ("standard", "S", [], False)
    assert a.gaps == [] and a.gap_owner == ""


def test_several_parts_take_the_least_favourable_evidenced_part(engine):
    pk, q = make(engine, "cfg")
    out = [part("Murabaha", "standard", "SLC supports Murabaha for retail and SME customers."), part("Late charge", "configuration", q)]
    with Session(engine) as s:  # make the first quote findable too
        c = s.exec(select(KbChunk)).first()
        c.text += " SLC supports Murabaha for retail and SME customers."
        s.commit()
    _, a, _ = run(engine, pk, out)
    assert a.capability == "configuration" and a.code == "C" and a.needs == ["explanation"]


def test_a_class_the_passage_does_not_show_is_downgraded_to_unknown_and_gets_no_code(engine):
    pk, q = make(engine, "std")  # the passage says "supports": it says nothing about a partner
    _, a, _ = run(engine, pk, [part("Murabaha", "third_party", q)])
    assert a.capability == "unknown" and a.code == "" and a.needs_validation
    assert "does not show" in a.parts[0]["reason"]
    assert [g["type"] for g in a.gaps][0] == enums.GAP_KNOWLEDGE


def test_a_quote_that_is_not_in_the_passage_is_not_accepted(engine):
    pk, _ = make(engine, "std")
    _, a, _ = run(engine, pk, [part("Murabaha", "standard", "SLC supports every Islamic structure out of the box.")])
    assert a.capability == "unknown" and a.code == "" and "not found" in a.parts[0]["reason"]


def test_an_explicit_exclusion_is_a_confirmed_gap_owned_by_product_management(engine):
    pk, q = make(engine, "not", status=enums.NOT_COMPLIED, text="SLC does not support Tawarruq-based products. [1]")
    _, a, _ = run(engine, pk, [part("Tawarruq", "not_available", q)])
    assert (a.capability, a.code) == ("not_available", "N")
    assert a.gaps[0]["type"] == enums.GAP_CONFIRMED and a.gap_owner == "Product Management"
    assert not a.needs_validation


def test_absence_of_evidence_is_a_knowledge_gap_never_a_confirmed_gap_and_costs_no_model_call(engine):
    pk, _ = make(engine, "std", status=enums.CANNOT_CONFIRM, text="No approved source confirms this.", cited=False,
                 flags=[enums.FLAG_MISSING], confidence=0.2, triage=enums.TRIAGE_CLARIFY)
    outcome, a, gw = run(engine, pk, [])
    assert gw.calls == [] and outcome == "assessed"
    assert a.capability == "unknown" and a.code == "" and a.needs_validation
    types = [g["type"] for g in a.gaps]
    assert enums.GAP_KNOWLEDGE in types and enums.GAP_CONFIRMED not in types


def test_owner_routed_requirements_are_not_classified(engine):
    pk, _ = make(engine, "std", triage=enums.TRIAGE_OWNER, text="")
    outcome, a, gw = run(engine, pk, [])
    assert gw.calls == [] and outcome == "skipped" and a.capability == "" and a.gaps == []


def test_third_party_dependency_goes_to_solution_architecture(engine):
    pk, q = make(engine, "third")
    _, a, _ = run(engine, pk, [part("Takaful", "third_party", q)])
    assert (a.capability, a.code) == ("third_party", "T")
    assert a.gaps[0]["type"] == enums.GAP_INTEGRATION and a.gap_owner == "Solution Architecture"


def test_development_needs_an_explanation_and_an_estimate_and_goes_to_engineering(engine):
    pk, q = make(engine, "dev", status=enums.PARTIAL)
    _, a, _ = run(engine, pk, [part("Benchmarking", "development", q)])
    assert (a.capability, a.code) == ("development", "D") and set(a.needs) == {"explanation", "estimate"}
    assert a.gap_owner == "Engineering"


def test_an_explanation_that_contradicts_the_class_needs_a_person(engine):
    pk, q = make(engine, "std", text="SLC supports Murabaha, delivered through a partner. [1]")
    _, a, _ = run(engine, pk, [part("Murabaha", "standard", q)])
    assert a.needs_validation and any("third-party" in r for r in a.validation_reasons)


def test_stale_conflicting_or_typed_in_evidence_needs_a_person(engine):
    pk, q = make(engine, "std", flags=[enums.FLAG_STALE, enums.FLAG_SE_PROVIDED])
    _, a, _ = run(engine, pk, [part("Murabaha", "standard", q)])
    assert a.needs_validation
    reasons = " ".join(a.validation_reasons)
    assert "stale" in reasons and "typed in" in reasons


def test_answer_status_that_contradicts_the_evidence_is_flagged(engine):
    pk, q = make(engine, "not", status=enums.COMPLIED, text="SLC does not support Tawarruq. [1]")
    _, a, _ = run(engine, pk, [part("Tawarruq", "not_available", q)])
    assert a.needs_validation and any("Complied" in i for i in a.issues)


def test_no_code_is_invented_when_the_rfp_has_no_code_for_the_class(engine):
    pk, q = make(engine, "not", status=enums.NOT_COMPLIED, scheme_text="Use S = Standard, C = Configuration, "
                                                                       "D = Development, T = Third party.")
    _, a, _ = run(engine, pk, [part("Tawarruq", "not_available", q)])
    assert a.capability == "not_available" and a.code == "" and a.needs_validation
    assert any("no response code" in r for r in a.validation_reasons)


def test_an_rfp_with_no_scheme_still_gets_the_assessment_but_no_code(engine):
    pk, q = make(engine, "std", scheme_text="")
    _, a, _ = run(engine, pk, [part("Murabaha", "standard", q)])
    assert a.capability == "standard" and a.code == ""


def test_reassessing_keeps_a_confirmation_only_while_the_result_is_unchanged(engine):
    pk, q = make(engine, "std")
    run(engine, pk, [part("Murabaha", "standard", q)])
    with Session(engine) as s:
        a = s.exec(select(Assessment)).first(); a.confirmed, a.confirmed_by = True, "se"; s.commit()
    _, a, _ = run(engine, pk, [part("Murabaha", "standard", q)])
    assert a.confirmed and a.confirmed_by == "se"
    _, a, _ = run(engine, pk, [part("Murabaha", "configuration", q)])  # downgraded to unknown: the result changed
    assert not a.confirmed


def test_a_failed_call_does_not_overwrite_an_earlier_assessment(engine):
    pk, q = make(engine, "std")
    run(engine, pk, [part("Murabaha", "standard", q)])
    gw = FakeGateway(lambda t, p, n: ProviderError("boom"))
    assert asyncio.run(assess_requirement(engine, gw, pk)) == "failed"
    with Session(engine) as s:
        a = s.exec(select(Assessment)).first()
    assert a.capability == "standard" and a.error


def test_a_reply_that_is_not_a_classification_is_a_failure_not_a_result(engine):
    pk, _ = make(engine, "std")
    gw = FakeGateway(lambda t, p, n: SimpleNamespace(oops=True))
    assert asyncio.run(assess_requirement(engine, gw, pk)) == "failed"
    with Session(engine) as s:
        a = s.exec(select(Assessment)).first()
    assert a.capability == "unknown" and a.needs_validation and a.error


def test_a_whole_rfp_stops_cleanly_when_quota_runs_out(engine):
    pk, q = make(engine, "std")
    with Session(engine) as s:
        rfp_id = s.get(Requirement, pk).rfp_id
    gw = FakeGateway(lambda t, p, n: AllProvidersExhausted("spent", resume_at=123.0, attempts=[]))
    res = asyncio.run(assess_rfp(engine, gw, rfp_id))
    assert res["quota_exhausted"] and res["resume_at"] == 123.0 and res.get("assessed", 0) == 0


@pytest.mark.parametrize("raw,want", [
    ("Third Party", "third_party"), ("third-party", "third_party"), ("CONFIG", "configuration"),
    ("Customisation", "development"), ("Out of the box", "standard"), ("Unsupported", "not_available"),
    ("???", "unknown"), (None, "unknown"),
])
def test_capability_words_are_normalised(raw, want):
    assert norm_capability(raw) == want


# ---- found by running the real models on a held-out RFP
def test_a_plain_statement_of_behaviour_is_accepted_as_standard(engine):
    # "posted" is not on any list of capability verbs; rejecting it made a clear standard capability look unevidenced
    pk, q = make(engine, "plain")
    _, a, _ = run(engine, pk, [part("Charges go to charity", "standard", q)])
    assert (a.capability, a.code) == ("standard", "S") and a.parts[0]["reason"] == ""


def test_a_requirement_for_automation_is_not_met_by_a_passage_saying_it_must_be_done_manually(engine):
    pk, _ = make(engine, "manual", status=enums.PARTIAL)
    quote = "The rebate must be approved manually by an authorised officer before it is applied."
    _, a, _ = run(engine, pk, [part("Apply it automatically", "not_available", quote)])
    assert (a.capability, a.code) == ("not_available", "N")
    assert a.gaps[0]["type"] == enums.GAP_CONFIRMED


def test_no_code_is_suggested_while_any_part_is_unevidenced_even_if_the_rest_is_standard(engine):
    # Suggesting S for a requirement whose second part we cannot evidence would be the optimistic guess
    pk, q = make(engine, "plain", status=enums.PARTIAL)
    _, a, _ = run(engine, pk, [part("Charges go to charity", "standard", q),
                               part("Reported to the Shariah board", "standard", "A Shariah board report lists every posting.")])
    assert a.capability == "standard" and a.code == "" and a.needs_validation
    assert any("No code is suggested" in r for r in a.validation_reasons)


def test_a_cannot_confirm_answer_gets_no_code_even_when_it_cites_something_related(engine):
    # Live run: "integrate as described in Annexure B" retrieved a passage about adapters and was given code C
    pk, _ = make(engine, "std", status=enums.CANNOT_CONFIRM, text="No approved source confirms this. [1]",
                 flags=[enums.FLAG_MISSING], confidence=0.3)
    outcome, a, gw = run(engine, pk, [])
    assert gw.calls == [] and a.capability == "unknown" and a.code == "" and a.needs_validation


def test_data_hosting_location_counts_as_a_security_concern():
    from app.agent.gaps import analyse
    gaps, owner = analyse("All customer data must be hosted in the State of Qatar.", True, "Not Complied", [], "unknown", ["hosting"])
    assert enums.GAP_SECURITY in [g["type"] for g in gaps] and owner == "Security Officer"

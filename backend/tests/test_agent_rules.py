"""The deterministic rules that run around the model: owner triage, evidence anchors, figure and
support checks. All offline. Triage and anchors are checked against every golden requirement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.entities import anchors, missing_anchors
from app.agent.textutil import (
    best_quote,
    content_stems,
    dates,
    number_units,
    quote_supported,
    split_sentences,
    standards,
    support_score,
    unsupported_figures,
)
from app.agent.triage import triage_owner

GOLDEN = [json.loads(p.read_text()) for p in sorted((Path(__file__).parent / "golden").glob("*.json"))]
ALL = [(g["key"], r) for g in GOLDEN for r in g["requirements"]]


# ------------------------------------------------------------------ owner triage
@pytest.mark.parametrize("key,req", ALL, ids=[f"{k[:4]}-{r['req_id']}" for k, r in ALL])
def test_owner_triage_matches_golden_for_every_requirement(key, req):
    got = triage_owner(req["text"])
    assert (got is not None) == (req["expect"]["triage"] == "owner"), req["text"]
    if got:
        assert got.flag in req["expect"]["must_flag"] or not req["expect"]["must_flag"]


def test_owner_triage_names_the_right_owner():
    assert triage_owner("Provide pricing for licences").owner == "Commercial Desk"
    assert triage_owner("State your limitation of liability").owner == "Legal (Contracts)"
    assert triage_owner("Provide three named customer references").owner == "Account Management"
    assert "credits" in triage_owner("What service credits apply?").reason


@pytest.mark.parametrize("text", [
    "Describe how late-payment charges are handled in line with Shariah principles.",
    "State the RTO and RPO for production services.",
    "Confirm that data residency in the UAE will be available at go-live.",
    "Describe your support for Sukuk issuance structuring and administration.",
    "Describe the training you provide to bank staff.",
])
def test_ordinary_technical_questions_are_not_diverted_to_owners(text):
    assert triage_owner(text) is None


# ------------------------------------------------------------------ evidence anchors
def test_anchors_found_in_requirement_text():
    labels = {a.label for a in anchors("Confirm ISO 22301 and ISO/IEC 27001, PCI DSS, SOC 2 Type II and GDPR")}
    assert labels == {"ISO 22301", "ISO 27001", "PCI DSS", "SOC 2", "GDPR"}
    assert {a.label for a in anchors("UAE Federal Decree-Law No. 45 of 2021 on personal data")} == {"Decree-Law 45 of 2021"}
    assert anchors("Describe the APIs, REST and ISO 20022 messaging") != []  # ISO 20022 is a standard too
    assert anchors("Describe your hosting model") == []


def test_missing_anchor_detected_only_when_no_evidence_names_it():
    evidence = ["ISO/IEC 27001: the certificate expired on 31 January 2026.", "SOC 2 Type II: current."]
    assert missing_anchors("Confirm ISO 22301 certification", evidence) == ["ISO 22301"]
    assert missing_anchors("Confirm ISO/IEC 27001 certification", evidence) == []
    assert missing_anchors("Confirm SOC 2 Type II and PCI DSS", evidence) == ["PCI DSS"]
    assert missing_anchors("Confirm UAE Federal Decree-Law No. 45 of 2021", evidence) == ["Decree-Law 45 of 2021"]
    # a different standard's number must not satisfy the anchor
    assert missing_anchors("Confirm ISO 22301", ["ISO 27001 and ISO 9001 are held"]) == ["ISO 22301"]


def test_golden_gap_requirements_have_a_missing_anchor_against_the_real_kb_where_expected():
    """The four named-standard gaps in the golden set must be caught by the anchor rule alone."""
    kb = "\n".join(p.read_text(errors="ignore") for p in (Path(__file__).resolve().parents[2] / "sample_data" / "kb").glob("*.md"))
    for text, expected in [
        ("Confirm that the vendor holds ISO 22301 business continuity certification.", ["ISO 22301"]),
        ("Confirm PCI DSS compliance where cardholder data is processed.", ["PCI DSS"]),
        ("Provide an attestation of compliance with UAE Federal Decree-Law No. 45 of 2021 on the protection of personal data.", ["Decree-Law 45 of 2021"]),
        ("Confirm that the vendor currently holds ISO/IEC 27001 certification.", []),
    ]:
        assert missing_anchors(text, [kb]) == expected, text


# ------------------------------------------------------------------ figures
def test_number_unit_normalisation():
    assert number_units("RTO of 1 hour and RPO of 5 minutes") == {("1", "hour"), ("5", "minute")}
    assert number_units("recovered in 48 minutes; 13 months of logs; 9 full-time staff") == {
        ("48", "minute"), ("13", "month"), ("9", "staff")}
    assert number_units("peak team of 9 staff") == number_units("a peak team of 9 full-time staff")
    assert number_units("99.9 percent availability") == {("99.9", "percent")}


def test_dates_and_standards_extracted_and_normalised():
    assert dates("tested on 22 May 2026 and again in Q1 2027") == {"22 may 2026", "q1 2027"}
    assert standards("AES-256 at rest, TLS 1.2 or higher, ISO/IEC 27001 and ap-south-1") == {
        "aes256", "tls12", "iso27001", "apsouth1"}


def test_unsupported_figures_flags_only_what_the_evidence_lacks():
    ev = "The RTO is 1 hour and the RPO is 5 minutes. Last tested on 22 May 2026. Data uses AES-256."
    assert unsupported_figures("The RTO is 1 hour [1].", ev) == []
    assert unsupported_figures("The RTO is 4 hours [1].", ev) == ["4 hour"]
    assert unsupported_figures("Tested on 3 March 2026 [1].", ev) == ["3 march 2026"]
    assert unsupported_figures("It uses AES-128 [1].", ev) == ["aes128"]
    assert unsupported_figures("It uses AES-256 [1][2].", ev) == []  # markers are never mistaken for figures
    assert unsupported_figures("MIBS supports Murabaha [1].", ev) == []


# ------------------------------------------------------------------ support
def test_support_score_separates_grounded_from_invented():
    ev = "MIBS blocks generation of the sale contract until a purchase invoice and a possession confirmation are attached."
    grounded = "The platform blocks contract generation until the purchase invoice and possession confirmation are attached."
    invented = "The platform automatically negotiates supplier discounts using machine learning pricing models."
    assert support_score(grounded, ev) > 0.6
    assert support_score(invented, ev) < 0.2


def test_quote_supported_accepts_near_verbatim_and_rejects_fabrication():
    p = ["Data at rest is encrypted with AES-256 using AWS KMS. Customer-managed keys are optional."]
    assert quote_supported("Data at rest is encrypted with AES-256 using AWS KMS", p)
    assert quote_supported("data at rest is encrypted with AES-256, using AWS KMS", p)  # punctuation and case
    assert not quote_supported("Data at rest is encrypted with AES-512 using hardware modules", p)
    assert not quote_supported("short", p)


def test_best_quote_is_always_verbatim_source_text():
    passage = "Heading\nThe RTO is 1 hour. The RPO is 5 minutes. Tests run annually."
    q = best_quote("The RPO is five minutes", passage)
    assert q == "The RPO is 5 minutes." and q in passage


def test_split_sentences_keeps_markers_with_their_sentence():
    s = split_sentences("MIBS supports Murabaha [1]. It blocks contracts until possession is shown [2][3]. Done.")
    assert s == ["MIBS supports Murabaha [1].", "It blocks contracts until possession is shown [2][3].", "Done."]
    s = split_sentences("The RTO is 1 hour. [1] The RPO is 5 minutes. [2]")
    assert s == ["The RTO is 1 hour. [1]", "The RPO is 5 minutes. [2]"]
    assert split_sentences("") == []
    assert content_stems("Supports Murabaha financing [1]") >= {"murabaha", "financ"}

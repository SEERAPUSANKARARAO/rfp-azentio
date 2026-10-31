"""Level 1 / Brief B5: detect the RFP's formatting rules and output to match them.
Detection is checked against the ground truth recorded for the three sample RFPs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import enums
from app.agent.formatrules import (
    code_for,
    extract_rules,
    over_limit,
    response_file_name,
    word_count,
)
from app.ingest.parsers import parse_rfp

SAMPLE_RFPS = Path(__file__).resolve().parents[2] / "sample_data" / "rfps"
GOLDEN = sorted((Path(__file__).parent / "golden").glob("*.json"))


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.stem)
def test_detects_every_rule_in_the_ground_truth_from_the_real_file(path):
    g = json.loads(path.read_text())
    got = extract_rules(parse_rfp(SAMPLE_RFPS / g["filename"]).instruction_text)
    for key, want in g["format_rules"].items():
        if key == "keep_ids":
            continue  # an invariant of the tool, not something detected
        assert got.get(key) == want, f"{key}: wanted {want!r}, detected {got.get(key)!r}"


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.stem)
def test_no_rule_is_invented_that_the_ground_truth_does_not_have(path):
    g = json.loads(path.read_text())
    got = extract_rules(parse_rfp(SAMPLE_RFPS / g["filename"]).instruction_text)
    truth = g["format_rules"]
    assert bool(got.get("restate_question")) == bool(truth.get("restate_question"))
    assert bool(got.get("cite_sources")) == bool(truth.get("cite_sources"))


def test_compliance_vocabulary_is_mapped_by_meaning():
    a = extract_rules("State compliance in column E using only: C = Complies, PC = Partially Complies, NC = Does Not Comply.")
    assert a["compliance_codes"] == ["C", "PC", "NC"] and a["compliance_map"] == {"full": "C", "partial": "PC", "none": "NC"}
    assert "PC = Partially Complies" in a["compliance_legend"]
    b = extract_rules("State compliance using only: Fully Compliant, Partially Compliant, Non-Compliant.")
    assert b["compliance_map"] == {"full": "Fully Compliant", "partial": "Partially Compliant", "none": "Non-Compliant"}
    c = extract_rules("State compliance using only: Full, Partial, None.")
    assert c["compliance_map"] == {"full": "Full", "partial": "Partial", "none": "None"}
    d = extract_rules("Indicate compliance using only: No, Partial, Yes.")  # out of the usual order
    assert d["compliance_map"] == {"none": "No", "partial": "Partial", "full": "Yes"}


def test_compliance_codes_written_as_code_then_label_in_brackets():
    # Found by the live-test RFP: this wording was missed, so the export left the Compliance column empty.
    a = extract_rules("Mark each requirement C (Complies), PC (Partially complies) or NC (Does not comply) in the "
                      "Compliance column.")
    assert a["compliance_codes"] == ["C", "PC", "NC"]
    assert a["compliance_map"] == {"full": "C", "partial": "PC", "none": "NC"}
    assert "NC = Does not comply" in a["compliance_legend"]
    b = extract_rules("Indicate compliance as Y (Complies) or N (Does not comply).")
    assert b["compliance_map"] == {"full": "Y", "none": "N"}


@pytest.mark.parametrize("text", [
    "Provide ISO (International Organization for Standardization) and UAE (United Arab Emirates) details to show compliance.",
    "Answer in the compliance column with one code, for example C (Complies).",
    "Use A (Complies) or B (Complies with conditions).",  # two labels meaning the same thing: not a vocabulary
])
def test_bracketed_text_that_is_not_a_status_vocabulary_is_ignored(text):
    r = extract_rules(text)
    assert r["compliance_codes"] == [] and r["compliance_map"] == {}


@pytest.mark.parametrize("text,want", [
    ("Limit each response to 120 words.", 120), ("Each answer must not exceed 150 words.", 150),
    ("Answers should be no more than 80 words.", 80), ("Maximum 200 words per answer.", 200),
])
def test_word_limit_phrasings(text, want):
    assert extract_rules(text)["word_limit"] == want


def test_nothing_is_invented_from_text_with_no_rules():
    got = extract_rules("Welcome to our tender. We look forward to your proposal.")
    assert got == {"keep_ids": True, "restate_question": False, "cite_sources": False,
                   "compliance_codes": [], "compliance_map": {}, "compliance_legend": "", "detected_by": "rules",
                   "confirmed": False}


def test_deadlines_are_told_apart():
    r = extract_rules("Clarification questions must be submitted by 27 November 2026. "
                      "Final submission is due on 22 December 2026, 17:00 GST.")
    assert (r["clarification_deadline"], r["deadline"]) == ("2026-11-27", "2026-12-22")


# ----------------------------------------------------------------- applying the rules
RULES = extract_rules(
    "Limit each response to 10 words. State compliance using only: C = Complies, PC = Partially Complies, NC = Does Not Comply. "
    "Submit the workbook named <VendorName>_RFP-1.xlsx by 15 December 2026.")


def test_status_maps_to_the_rfps_own_codes_and_cannot_confirm_gets_none():
    assert code_for(RULES, enums.COMPLIED) == "C"
    assert code_for(RULES, enums.PARTIAL) == "PC"
    assert code_for(RULES, enums.NOT_COMPLIED) == "NC"
    assert code_for(RULES, enums.CANNOT_CONFIRM) == ""  # never asserts compliance the sources do not support
    assert code_for({}, enums.COMPLIED) == ""


def test_word_limit_ignores_citation_markers_and_reports_the_excess():
    assert word_count("MIBS supports Murabaha [1] and Ijara [2].") == 5
    assert over_limit(RULES, "one two three four five six seven eight nine ten") is None
    assert over_limit(RULES, "one two three four five six seven eight nine ten eleven twelve [1]") == 2
    assert over_limit({}, "any length is fine here") is None


def test_file_name_from_the_rfps_pattern_with_draft_marking():
    assert response_file_name(RULES, "Acme", "xlsx") == "Acme_RFP-1.xlsx"
    assert response_file_name(RULES, "Acme", "xlsx", draft=True) == "Acme_RFP-1_DRAFT.xlsx"
    assert response_file_name({}, "Acme Ltd", "docx") == "Acme_Ltd_Response.docx"


def test_import_stores_the_detected_rules_and_deadline(client):
    g = json.loads(next(p for p in GOLDEN if "asib" in p.name).read_text())
    body = client.post("/api/rfps", files={"file": (g["filename"], (SAMPLE_RFPS / g["filename"]).read_bytes())}).json()
    detail = client.get(f"/api/rfps/{body['id']}").json()
    assert detail["format_rules"]["word_limit"] == 120
    assert detail["format_rules"]["compliance_map"] == {"full": "C", "partial": "PC", "none": "NC"}
    assert detail["deadline"] == "2026-12-15"

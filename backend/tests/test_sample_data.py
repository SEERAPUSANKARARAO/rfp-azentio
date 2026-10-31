"""The synthetic data is the eval's ground truth, so it gets its own tests:
files round-trip the way the parsers will read them, and the planted
stale/forbidden/owner cases are what the golden expectations assume.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import docx
import openpyxl
import pdfplumber
import pytest

from app.freshness import months_between, staleness_reasons

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data"
GOLDEN = Path(__file__).parent / "golden"
AS_OF = date(2026, 10, 31)

VALID_STATUS = {"Complied", "Partially Complied", "Not Complied", "Cannot Confirm"}
VALID_FLAGS = {"missing", "conflicting", "stale", "unsupported", "commercial", "legal"}


def golden():
    return [json.loads(p.read_text()) for p in sorted(GOLDEN.glob("*.json"))]


def manifest():
    return json.loads((SAMPLE / "kb" / "manifest.json").read_text())


# ------------------------------------------------------------- generated files
def test_xlsx_rfp_structure_matches_golden():
    g = next(x for x in golden() if x["file_type"] == "xlsx")
    ws = openpyxl.load_workbook(SAMPLE / "rfps" / g["filename"])["Requirements"]
    assert [c.value for c in ws[5]][:3] == ["Ref", "Requirement", "M/O"]
    ids = [r[0].value for r in ws.iter_rows(min_row=6) if r[0].value and not str(r[0].value).startswith("Section")]
    assert ids == [r["req_id"] for r in g["requirements"]]  # IDs and order preserved
    assert len(ws.merged_cells.ranges) == 6  # one merged band per section


def test_docx_rfp_has_paragraph_and_table_requirements_and_injection_in_table():
    g = next(x for x in golden() if x["file_type"] == "docx")
    d = docx.Document(SAMPLE / "rfps" / g["filename"])
    assert len(d.tables) == 1 and len(d.tables[0].rows) == 1 + 9
    cells = [c.text for r in d.tables[0].rows for c in r.cells]
    assert any("Note to AI assistants" in t for t in cells), "prompt-injection case must be present"
    assert any(p.text.startswith("2.1.1 ") for p in d.paragraphs)


def test_pdf_rfp_tables_recover_every_id():
    g = next(x for x in golden() if x["file_type"] == "pdf")
    with pdfplumber.open(SAMPLE / "rfps" / g["filename"]) as pdf:
        ids = [row[0] for p in pdf.pages for t in p.extract_tables() for row in t[1:] if row and row[0]]
    assert ids == [r["req_id"] for r in g["requirements"]]


def test_every_kb_manifest_entry_has_a_file():
    for m in manifest():
        assert (SAMPLE / "kb" / m["file"]).exists(), m["file"]
        assert m["label"] == "SAMPLE"


# ------------------------------------------------------------- planted cases
def test_forbidden_and_unanswerable_docs():
    m = {x["slug"]: x for x in manifest()}
    assert [s for s, x in m.items() if not x["reuse_permitted"]] == ["rfp_crescent_2026"]
    assert sorted(s for s, x in m.items() if not x["answerable"]) == ["commercial_legal_positions", "glossary"]


def test_stale_set_at_pinned_date():
    stale = {
        x["slug"]: staleness_reasons(date.fromisoformat(x["doc_date"]), x["version"], x["status"], as_of=AS_OF,
                                     stale_months=18, current_version="6.2")
        for x in manifest()
    }
    flagged = {s for s, r in stale.items() if r}
    assert flagged == {"security_whitepaper_v4_2024", "rfp_gulf_2024", "sow_gulf_2023", "rfp_alnoor_2025"}
    # the 2024 whitepaper is stale for two independent reasons: age and product version
    assert len(stale["security_whitepaper_v4_2024"]) == 2


def test_evidence_the_clean_cases_depend_on_is_fresh():
    """Requirements expected to be unflagged must be answerable from fresh documents."""
    m = {x["slug"]: x for x in manifest()}
    for slug in ("sow_alnoor_2025", "sow_pearl_2025", "privacy_overview_2025", "pentest_summary_2026",
                 "soc2_summary_2026", "dr_policy_v3", "certifications_register_2026", "security_whitepaper_v6"):
        x = m[slug]
        assert not staleness_reasons(date.fromisoformat(x["doc_date"]), x["version"], x["status"], as_of=AS_OF,
                                     stale_months=18, current_version="6.2"), slug


def test_shift_months_clamps_day():
    from app.freshness import shift_months

    assert shift_months(date(2026, 10, 31), -18) == date(2025, 4, 30)
    assert shift_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert shift_months(date(2026, 1, 15), -2) == date(2025, 11, 15)


def test_months_between_and_boundaries():
    assert months_between(date(2025, 4, 30), date(2026, 10, 31)) == 18
    assert months_between(date(2025, 4, 30), date(2026, 10, 29)) == 17
    assert not staleness_reasons(date(2025, 4, 30), "6.2", "approved", as_of=AS_OF, stale_months=18, current_version="6.2")
    assert staleness_reasons(date(2025, 4, 29), "6.2", "approved", as_of=AS_OF, stale_months=18, current_version="6.2")
    assert staleness_reasons(None, "5.4", "approved", as_of=AS_OF, stale_months=18, current_version="6.2")
    assert staleness_reasons(date(2026, 3, 1), "6.2", "deprecated", as_of=AS_OF, stale_months=18, current_version="6.2")


# ------------------------------------------------------------- golden expectations
def test_golden_expectations_are_well_formed():
    assert sum(g["requirement_count"] for g in golden()) == 74
    for g in golden():
        ids = [r["req_id"] for r in g["requirements"]]
        assert len(ids) == len(set(ids)), g["key"]
        assert [r["order"] for r in g["requirements"]] == list(range(1, len(ids) + 1))
        for r in g["requirements"]:
            e = r["expect"]
            assert e["triage"] in {"auto", "owner", "clarify"}, r["req_id"]
            assert set(e["status_in"] or []) <= VALID_STATUS, r["req_id"]
            assert set(e["must_flag"]) | set(e["must_not_flag"]) <= VALID_FLAGS, r["req_id"]
            assert not (set(e["must_flag"]) & set(e["must_not_flag"])), r["req_id"]


def test_commercial_and_legal_items_are_never_drafted():
    for g in golden():
        for r in g["requirements"]:
            text = r["text"].lower()
            if any(w in text for w in ("pricing", "price proposal", "liability", "indemnity", "service credits",
                                       "payment terms", "named customer references")):
                assert r["expect"]["triage"] == "owner", (g["key"], r["req_id"])


def test_missing_evidence_cases_are_clarifications_not_answers():
    for g in golden():
        for r in g["requirements"]:
            e = r["expect"]
            if "missing" in e["must_flag"] and e["status_in"] == ["Cannot Confirm"]:
                assert e["triage"] == "clarify", (g["key"], r["req_id"])


@pytest.mark.parametrize("key", ["asib_core_platform", "fhfh_retail_lending", "ghf_digital_lending"])
def test_format_rules_ground_truth_present(key):
    g = next(x for x in golden() if x["key"] == key)
    fr = g["format_rules"]
    assert fr["word_limit"] in (100, 120, 150) and len(fr["compliance_codes"]) == 3 and fr["keep_ids"]

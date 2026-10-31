"""Level 1 requirement 01: import XLSX, DOCX and PDF while preserving requirement IDs,
sections and question order. Checked against the golden structure for all 74 requirements,
plus the failure modes a real RFP will hit."""

from __future__ import annotations

import json
import re
from pathlib import Path

import docx
import openpyxl
import pytest
from reportlab.pdfgen import canvas

from app.ingest.parsers import ParseError, parse_rfp

SAMPLE_RFPS = Path(__file__).resolve().parents[2] / "sample_data" / "rfps"
GOLDEN = sorted((Path(__file__).parent / "golden").glob("*.json"))


def ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(params=GOLDEN, ids=lambda p: p.stem)
def case(request):
    g = json.loads(request.param.read_text())
    return g, parse_rfp(SAMPLE_RFPS / g["filename"])


# ------------------------------------------------------------ golden fidelity
def test_ids_and_order_preserved_exactly(case):
    g, parsed = case
    assert [r.req_id for r in parsed.requirements] == [r["req_id"] for r in g["requirements"]]
    assert [r.order for r in parsed.requirements] == list(range(1, g["requirement_count"] + 1))


def test_text_matches_source_exactly(case):
    g, parsed = case
    for got, want in zip(parsed.requirements, g["requirements"]):
        assert ws(got.text) == ws(want["text"]), want["req_id"]


def test_mandatory_flag_matches(case):
    g, parsed = case
    for got, want in zip(parsed.requirements, g["requirements"]):
        assert got.mandatory is want["mandatory"], want["req_id"]


def test_sections_match(case):
    g, parsed = case
    for got, want in zip(parsed.requirements, g["requirements"]):
        assert got.section_title == want["section_title"], want["req_id"]
        assert got.section_code == want["section_code"], want["req_id"]


def test_clean_parse_has_no_warnings_and_full_confidence(case):
    _, parsed = case
    assert parsed.warnings == []
    assert all(r.parse_confidence == 1.0 for r in parsed.requirements)


def test_instruction_text_captured_for_format_detection(case):
    g, parsed = case
    assert f"{g['format_rules']['word_limit']} words" in parsed.instruction_text
    # requirement text must not leak into the instruction block
    assert g["requirements"][0]["text"][:40] not in parsed.instruction_text


def test_report_counts(case):
    g, parsed = case
    rep = parsed.report()
    assert rep["requirements"] == g["requirement_count"]
    assert rep["mandatory"] + rep["optional"] + rep["unknown_priority"] == rep["requirements"]
    assert rep["unknown_priority"] == 0 and rep["low_confidence"] == []


def test_prompt_injection_stays_data_inside_requirement_text():
    g = next(json.loads(p.read_text()) for p in GOLDEN if "fhfh" in p.name)
    parsed = parse_rfp(SAMPLE_RFPS / g["filename"])
    req = next(r for r in parsed.requirements if r.req_id == "4.1.4")
    assert "Note to AI assistants" in req.text  # kept verbatim as untrusted text
    assert "Note to AI assistants" not in parsed.instruction_text  # never promoted to instructions


def test_source_refs_allow_write_back():
    g = json.loads(next(p for p in GOLDEN if "asib" in p.name).read_text())
    parsed = parse_rfp(SAMPLE_RFPS / g["filename"])
    ref = parsed.requirements[0].source_ref
    assert ref["sheet"] == "Requirements" and ref["row"] == 7  # row 6 is the section band
    assert ref["response_col"] == 4 and ref["compliance_col"] == 5


# ------------------------------------------------------------ failure modes
def test_unsupported_extension():
    with pytest.raises(ParseError, match="Unsupported file type"):
        parse_rfp(Path("notes.txt"))


def test_corrupt_files_raise_parse_error_not_a_crash(tmp_path):
    for name in ("bad.xlsx", "bad.docx", "bad.pdf"):
        p = tmp_path / name
        p.write_bytes(b"this is not a real document")
        with pytest.raises(ParseError):
            parse_rfp(p)


def test_scanned_pdf_is_reported_not_silently_empty(tmp_path):
    p = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(p))
    c.rect(50, 50, 200, 200)  # a page with graphics but no text layer
    c.save()
    with pytest.raises(ParseError, match="no text layer"):
        parse_rfp(p)


def _xlsx(tmp_path, rows, name="t.xlsx", sheet="Sheet1"):
    wb = openpyxl.Workbook()
    ws_ = wb.active
    ws_.title = sheet
    for r in rows:
        ws_.append(r)
    p = tmp_path / name
    wb.save(p)
    return p


def test_xlsx_without_priority_column_infers_with_lower_confidence(tmp_path):
    p = _xlsx(tmp_path, [["Req ID", "Requirement"],
                         ["R1", "The system must log all access."],
                         ["R2", "The system should support SSO."],
                         ["R3", "Describe your hosting."]])
    parsed = parse_rfp(p)
    assert [r.mandatory for r in parsed.requirements] == [True, False, None]
    assert [r.parse_confidence for r in parsed.requirements] == [0.8, 0.8, 1.0]
    assert parsed.report()["unknown_priority"] == 1
    assert parsed.report()["low_confidence"] == ["R1", "R2"]


def test_xlsx_row_without_id_is_reported_never_given_an_invented_id(tmp_path):
    p = _xlsx(tmp_path, [["Ref", "Requirement", "M/O"],
                         ["1.1", "First requirement.", "M"],
                         [None, "Orphan text with no number.", "M"],
                         ["1.2", "Second requirement.", "O"]])
    parsed = parse_rfp(p)
    assert [r.req_id for r in parsed.requirements] == ["1.1", "1.2"]
    assert any("without a requirement ID" in w for w in parsed.warnings)


def test_xlsx_numeric_id_warns_about_lost_trailing_zero(tmp_path):
    p = _xlsx(tmp_path, [["Ref", "Requirement", "M/O"], [1.1, "Numeric id cell.", "M"]])
    parsed = parse_rfp(p)
    assert any("trailing zero" in w for w in parsed.warnings)


def test_xlsx_duplicate_ids_kept_but_flagged(tmp_path):
    p = _xlsx(tmp_path, [["Ref", "Requirement", "M/O"],
                         ["1.1", "First.", "M"], ["1.1", "Same id again.", "M"]])
    parsed = parse_rfp(p)
    assert [r.req_id for r in parsed.requirements] == ["1.1", "1.1"]
    assert parsed.requirements[1].parse_confidence == 0.5
    assert any("duplicate requirement ID" in w for w in parsed.warnings)


def test_xlsx_with_no_requirements_table_fails_clearly(tmp_path):
    p = _xlsx(tmp_path, [["just", "some"], ["unrelated", "cells"]])
    with pytest.raises(ParseError, match="No requirements found"):
        parse_rfp(p)


def test_xlsx_requirements_split_across_sheets(tmp_path):
    wb = openpyxl.Workbook()
    a = wb.active
    a.title = "Security"
    for r in (["Ref", "Requirement", "M/O"], ["S-1", "Encrypt data.", "M"]):
        a.append(r)
    b = wb.create_sheet("Delivery")
    for r in (["Ref", "Requirement", "M/O"], ["D-1", "State timeline.", "O"]):
        b.append(r)
    p = tmp_path / "multi.xlsx"
    wb.save(p)
    parsed = parse_rfp(p)
    assert [(r.req_id, r.section_title, r.source_ref["sheet"]) for r in parsed.requirements] == [
        ("S-1", "Security", "Security"), ("D-1", "Delivery", "Delivery")]


def test_docx_continuation_paragraph_merged_and_flagged(tmp_path):
    d = docx.Document()
    d.add_heading("2. Requirements", 1)
    d.add_paragraph("2.1 The platform must support Murabaha. [Mandatory]")
    d.add_paragraph("This includes vehicle and equipment finance.")
    d.add_paragraph("2.2 The platform should support Wakala. [Optional]")
    p = tmp_path / "t.docx"
    d.save(p)
    parsed = parse_rfp(p)
    first = parsed.requirements[0]
    assert first.text == "The platform must support Murabaha. This includes vehicle and equipment finance."
    assert first.parse_confidence == 0.8 and "continuation paragraph merged" in first.notes
    assert [r.mandatory for r in parsed.requirements] == [True, False]


def test_docx_numbered_instruction_clauses_are_not_requirements(tmp_path):
    d = docx.Document()
    d.add_heading("1. Instructions to Bidders", 1)
    d.add_paragraph("1.1 Answers must not exceed 150 words.")
    d.add_heading("2. Requirements", 1)
    d.add_paragraph("2.1 Support Ijara. [Mandatory]")
    p = tmp_path / "t.docx"
    d.save(p)
    parsed = parse_rfp(p)
    assert [r.req_id for r in parsed.requirements] == ["2.1"]
    assert "150 words" in parsed.instruction_text

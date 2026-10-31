"""Level 2 test material: an RFP that asks bidders to classify every capability as Standard / Configuration /
Development / Third party / Not available, plus one extra knowledge-base document that gives it something to classify.

    cd backend && .venv/bin/python ../live_test/make_level2.py

It is meant to be used on top of the Level 1 live-test knowledge base (see GUIDE_LEVEL2.md). Synthetic throughout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_documents as base  # noqa: E402

KB2 = HERE / "kb_level2"
RFP_NO, RFP_FILE = "BWIF-L2-2026-044", "BWIF_L2-2026-044_Capability_Classification.xlsx"
NAME = "Bank Al-Wafa Islamic Finance: Retail Financing Platform (capability classification)"

DOC = dict(
    file="slc_extensions_2026.md", title="Extensions, Workflows and Reporting 2026", kind="product_doc",
    date="2026-09-10", version="6.2", owner="Product Management",
    blocks=[
        ("h", "1. Benchmark rates"),
        ("p", "Profit-rate benchmarking against an external index is not delivered as standard. It is available as a "
              "customer-specific extension built on the Pricing API by Safa Systems professional services. The effort "
              "depends on the index provider."),
        ("h", "2. Approval workflows"),
        ("p", "Approval workflows are configured for each product by the bank's administrators. No code change is needed."),
        ("h", "3. Reporting"),
        ("p", "A standard set of 40 operational and regulatory reports is included. Additional reports are produced with "
              "the configurable report designer."),
        ("h", "4. Takaful"),
        ("p", "Takaful on financed assets is supported through API integration with the customer's Takaful operator. "
              "Premium, coverage and beneficiary data remain in the operator's system."),
    ])

INSTRUCTIONS = [
    "Classify every requirement using one of these codes: S = Standard capability, C = Configuration required, "
    "D = Development required, T = Third-party dependency, N = Not available.",
    "Codes C and D must be accompanied by an explanation. Code D must also include an effort estimate in person-days.",
    "Responses must not exceed 80 words.",
    "Submit the completed workbook named <VendorName>_BWIF-L2-2026-044.xlsx by 12 February 2027, 17:00 GST.",
    "Clarification questions must be received by 29 January 2027.",
    "Annexure A: attach a copy of your ISO/IEC 27001 certificate.",
]

# (id, text, mandatory, what it tests, expected class(es), expected gap)
SECTIONS = [
    ("1", "Financing products", [
        ("1.1", "The solution must support Murabaha financing for SME customers.", True, "plain standard capability", ["standard"], None),
        ("1.2", "Late-payment charges must be configurable per product and directed to charity.", True, "configurable", ["configuration"], None),
        ("1.3", "The solution must calculate an early-settlement rebate and apply it automatically without manual approval.", True,
         "one part standard, one part not available: least favourable wins", ["not_available"], "confirmed_gap"),
        ("1.4", "The solution must support Tawarruq-based personal financing.", True, "explicit exclusion", ["not_available"], "confirmed_gap"),
    ]),
    ("2", "Integration", [
        ("2.1", "Takaful cover must be linked to each financed asset through the bank's Takaful operator.", True,
         "depends on the operator's system", ["third_party"], "integration_dependency"),
        ("2.2", "The solution must connect to credit bureau and AML screening providers.", True,
         "adapters to third-party providers", ["third_party", "configuration"], None),
        ("2.3", "Profit rates must be benchmarked against an external index feed supplied by the bank.", True,
         "needs a customer-specific extension", ["development"], "development_required"),
        ("2.4", "The solution must integrate with the bank's core banking system as described in Annexure B.", True,
         "refers to a document we do not have", ["unknown"], "knowledge_gap"),
    ]),
    ("3", "Security and resilience", [
        ("3.1", "The solution must support single sign-on with SAML 2.0 or OpenID Connect and multi-factor authentication for administrators.", True,
         "two facts, one source", ["standard"], None),
        ("3.2", "State the encryption used for customer data at rest and in transit.", True,
         "old and new sources disagree", ["standard"], "documentation_gap"),
        ("3.3", "Confirm ISO 22301 certification for business continuity management.", True,
         "no approved source mentions it", ["unknown"], "security_concern"),
        ("3.4", "All customer data must be hosted in the State of Qatar.", True,
         "sources name other countries and are silent on Qatar", ["unknown", "not_available"], "security_concern"),
        ("3.5", "Customer data must remain hosted in the United Arab Emirates at all times.", True,
         "contradicts 3.4", ["unknown", "standard"], None),
    ]),
    ("4", "Scale and reporting", [
        ("4.1", "Provide an AI-based credit scoring engine.", False, "only rule-based scorecards exist", ["configuration", "unknown"], "knowledge_gap"),
        ("4.2", "The solution must support 5,000 concurrent users with sub-second response times.", True,
         "no performance evidence at all", ["unknown"], "knowledge_gap"),
        ("4.3", "Provide adequate reporting capabilities as required by the bank.", True,
         "vague wording: what is adequate? what is required?", ["standard", "configuration", "unknown"], None),
        ("4.4", "The approval workflow must work exactly as described in requirement 1.2.", True,
         "depends on another requirement", ["configuration", "unknown"], None),
    ]),
    ("5", "Commercial", [
        ("5.1", "Provide licence and implementation pricing per module.", True, "owner, never drafted", [], None),
    ]),
]


def render(path: Path) -> None:
    wb = Workbook()
    ins = wb.active
    ins.title = "Instructions"
    ins["A1"], ins["A1"].font = NAME, Font(bold=True, size=14)
    ins["A2"] = f"Reference: {RFP_NO}"
    ins["A4"], ins["A4"].font = "Instructions to bidders", Font(bold=True)
    for i, line in enumerate(INSTRUCTIONS, 1):
        ins.cell(row=4 + i, column=1, value=f"{i}. {line}")
    ins.column_dimensions["A"].width = 140
    for row in ins.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")

    ws = wb.create_sheet("Requirements")
    ws["A1"], ws["A1"].font = NAME, Font(bold=True, size=14)
    ws["A2"] = f"Reference: {RFP_NO}"
    ws["A3"] = "Complete columns D and E for every requirement."
    thin = Side(style="thin", color="BBBBBB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    heads = ["Ref", "Requirement", "M/O", "Vendor Response", "Compliance Code (S / C / D / T / N)", "Comments"]
    for col, h in enumerate(heads, 1):
        c = ws.cell(row=5, column=col, value=h)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor=base.NAVY)
        c.alignment, c.border = Alignment(wrap_text=True, vertical="center"), border
    dv = DataValidation(type="list", formula1='"S,C,D,T,N"', allow_blank=True)
    ws.add_data_validation(dv)
    row = 6
    for code, title, reqs in SECTIONS:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        band = ws.cell(row=row, column=1, value=f"Section {code}: {title}")
        band.font, band.fill = Font(bold=True), PatternFill("solid", fgColor="DCE6F1")
        row += 1
        for rid, text, mandatory, *_ in reqs:
            for col, v in enumerate([rid, text, "M" if mandatory else "O", None, None, None], 1):
                c = ws.cell(row=row, column=col, value=v)
                c.alignment, c.border = Alignment(wrap_text=True, vertical="top"), border
            dv.add(ws.cell(row=row, column=5))
            row += 1
    for col, w in zip("ABCDEF", (8, 70, 7, 60, 22, 30)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A6"
    wb.save(path)


def main() -> None:
    KB2.mkdir(parents=True, exist_ok=True)
    base.render_md(DOC, KB2 / DOC["file"])
    (HERE / "kb_metadata_level2.json").write_text(json.dumps([dict(
        file=DOC["file"], title=DOC["title"], kind=DOC["kind"], doc_date=DOC["date"], version=DOC["version"],
        owner=DOC["owner"], customer="", reuse_permitted=True, answerable=True)], indent=2), encoding="utf-8")
    render(HERE / "rfp" / RFP_FILE)
    expected = [dict(id=i, section=f"{c}. {t}", mandatory=m, tests=tests, capability=cap, gap=gap)
                for c, t, reqs in SECTIONS for i, _, m, tests, cap, gap in reqs]
    (HERE / "expected_level2.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    print(f"1 add-on document, 1 RFP with {len(expected)} requirements")


if __name__ == "__main__":
    main()

"""The customer's own template gets the right code, and only one a person can stand behind."""

from pathlib import Path

import openpyxl
import pytest
from sqlmodel import Session, select

from app import enums
from app.db import init_db, make_engine
from app.models import Answer, Assessment, Requirement
from app.services import export_service, review
from app.services.rfp_import import import_rfp

L2 = Path(__file__).resolve().parents[2] / "live_test" / "rfp" / "BWIF_L2-2026-044_Capability_Classification.xlsx"
pytestmark = pytest.mark.skipif(not L2.exists(), reason="run live_test/make_level2.py first")


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "ex"))
    monkeypatch.setenv("AS_OF_DATE", "2026-10-31")
    from app.config import get_settings
    get_settings.cache_clear()
    eng = make_engine(tmp_path / "e.db")
    init_db(eng)
    with Session(eng) as s:
        rfp = import_rfp(s, L2, L2.name)
        ids = {r.req_id: r.id for r in s.exec(select(Requirement).where(Requirement.rfp_id == rfp.id)).all()}
        # (req, status, capability, code, needs_validation, confirmed, estimate)
        for req, cap, code, validate, confirmed, est in [
            ("1.1", "standard", "S", False, False, None),         # evidence complete: the code is written
            ("1.2", "configuration", "C", True, False, None),      # needs a person and none has validated it: NOT written
            ("2.3", "development", "D", True, True, "15 person-days"),  # validated by a person: written
        ]:
            s.add(Answer(requirement_id=ids[req], draft_text=f"Answer for {req}.", compliance_status=enums.COMPLIED,
                         review_status=enums.REVIEW_DRAFTED, confidence=0.9))
            s.flush()
            s.add(Assessment(requirement_id=ids[req], capability=cap, code=code, needs_validation=validate, confirmed=confirmed,
                             confirmed_by="ana" if confirmed else None, estimate=est,
                             gaps=[{"type": "development_required", "detail": "Needs work.", "owner": "Engineering", "critical": True}] if cap == "development" else [],
                             gap_owner="Engineering" if cap == "development" else ""))
        s.commit()
        for req in ("1.1", "1.2", "2.3"):
            review.accept(s, rfp.id, ids[req], "ana")
        rid = rfp.id
    yield eng, rid
    get_settings.cache_clear()


def sheet_rows(path, name):
    ws = openpyxl.load_workbook(path)[name]
    head = [c.value for c in ws[1]]
    return head, [dict(zip(head, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]


def test_only_a_validated_or_complete_code_is_written_into_the_customers_file(prepared):
    eng, rid = prepared
    with Session(eng) as s:
        res = export_service.export_response(s, rid)
    ws = openpyxl.load_workbook(res.path)["Requirements"]
    head = {ws.cell(row=5, column=c).value: c for c in range(1, ws.max_column + 1)}
    code_col = next(c for n, c in head.items() if str(n).startswith("Compliance Code"))
    cmt_col = head["Comments"]
    cells = {ws.cell(row=r, column=1).value: (ws.cell(row=r, column=code_col).value, ws.cell(row=r, column=cmt_col).value)
             for r in range(6, ws.max_row + 1) if ws.cell(row=r, column=1).value}
    assert cells["1.1"][0] == "S" and cells["2.3"][0] == "D"
    assert cells["1.2"][0] is None and "Suggested response code C is waiting for validation" in cells["1.2"][1]
    assert "Effort estimate: 15 person-days" in cells["2.3"][1]


def test_the_export_check_names_the_answers_that_have_no_code(prepared):
    eng, rid = prepared
    with Session(eng) as s:
        issues = {i["code"]: i for i in export_service.check(s, rid)["issues"]}
    assert issues["response_code_missing"]["requirements"] == ["1.2"]


def test_the_matrix_and_the_new_sheets_carry_capability_gaps_and_the_checklist(prepared):
    eng, rid = prepared
    with Session(eng) as s:
        res = export_service.export_response(s, rid)
    head, rows = sheet_rows(res.path, "Compliance Matrix")
    by = {r["Ref"]: r for r in rows}
    assert {"Capability", "RFP code", "Code state", "Code needs", "Gaps", "Suggested owner"} <= set(head)
    assert by["2.3"]["Capability"] == "development" and by["2.3"]["Code state"] == "Validated"
    assert by["2.3"]["Suggested owner"] == "Engineering"
    assert by["1.2"]["RFP code"] == "C (suggested)" and by["1.2"]["Code state"] == "Needs validation"
    wb = openpyxl.load_workbook(res.path)
    assert {"Gap Analysis", "Submission Checklist", "Clarifications"} <= set(wb.sheetnames)
    _, gaps = sheet_rows(res.path, "Gap Analysis")
    assert gaps[0]["Ref"] == "2.3" and gaps[0]["Suggested owner"] == "Engineering"
    _, checks = sheet_rows(res.path, "Submission Checklist")
    assert any("Annexure A" in r["Item"] and r["Status"] == "Outstanding" for r in checks)

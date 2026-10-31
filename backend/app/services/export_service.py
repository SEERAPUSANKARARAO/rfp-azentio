from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session

from ..agent.formatrules import response_file_name
from ..config import get_settings
from ..export.common import ExportPlan, build_plan, check_plan
from ..export.docx import export_docx
from ..export.xlsx import export_matrix, export_xlsx
from . import audit


class ExportBlocked(Exception):
    def __init__(self, check: dict):
        super().__init__("Export blocked by the RFP's own rules")
        self.check = check


@dataclass
class ExportResult:
    path: Path
    filename: str
    media_type: str
    check: dict
    plan: ExportPlan


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _out_dir(rfp_id: int) -> Path:
    d = get_settings().export_dir / f"rfp_{rfp_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def check(session: Session, rfp_id: int, approved_only: bool = True) -> dict:
    return check_plan(build_plan(session, rfp_id, approved_only=approved_only))


def export_response(
    session: Session, rfp_id: int, *, approved_only: bool = True, allow_blocking: bool = False, actor: str = "se"
) -> ExportResult:
    plan = build_plan(session, rfp_id, approved_only=approved_only)
    result = check_plan(plan)
    if not result["ready"] and not allow_blocking:
        raise ExportBlocked(result)

    xlsx = plan.rfp.file_type == "xlsx"  # an Excel RFP is answered in Excel; Word and PDF RFPs in Word
    ext = "xlsx" if xlsx else "docx"
    name = response_file_name(plan.rules, plan.vendor, ext, draft=plan.draft_copy)
    if not name.lower().endswith("." + ext):  # the RFP asked for a different type than we can write into
        name = name.rsplit(".", 1)[0] + "." + ext
    path = _out_dir(rfp_id) / name
    (export_xlsx if xlsx else export_docx)(plan, path)

    audit.record(session, "export", "exported", rfp_id=rfp_id, actor=actor, payload={
        "file": name, "approved_only": approved_only, "draft_copy": plan.draft_copy, **result["counts"],
        "issues": [i["code"] for i in result["issues"]]})
    session.commit()
    return ExportResult(path, name, XLSX_TYPE if xlsx else DOCX_TYPE, result, plan)


def export_compliance_matrix(session: Session, rfp_id: int, actor: str = "se") -> ExportResult:
    plan = build_plan(session, rfp_id, approved_only=False)
    path = _out_dir(rfp_id) / f"{plan.vendor}_Compliance_Matrix.xlsx"
    export_matrix(plan, path)
    audit.record(session, "export", "matrix_exported", rfp_id=rfp_id, actor=actor)
    session.commit()
    return ExportResult(path, path.name, XLSX_TYPE, check_plan(plan), plan)

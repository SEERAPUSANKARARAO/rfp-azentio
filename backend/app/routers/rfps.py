from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from ..config import get_settings
from ..db import get_session
from ..ingest.parsers import ParseError
from ..models import Rfp
from ..schemas import RfpDetail, RfpSummary
from ..services import rfp_import

router = APIRouter(prefix="/rfps", tags=["rfps"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED = {".xlsx", ".xlsm", ".docx", ".pdf"}


@router.post("", response_model=RfpSummary, status_code=201)
async def upload_rfp(file: UploadFile = File(...), session: Session = Depends(get_session)):
    name = Path(file.filename or "rfp").name
    if Path(name).suffix.lower() not in ALLOWED:
        raise HTTPException(422, "Unsupported file type. Upload an Excel (.xlsx), Word (.docx) or PDF file.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 25 MB.")
    if not data:
        raise HTTPException(422, "The uploaded file is empty.")

    with tempfile.TemporaryDirectory(dir=get_settings().upload_dir) as tmp:
        src = Path(tmp) / name
        src.write_bytes(data)
        try:
            rfp = rfp_import.import_rfp(session, src, name)
        except ParseError as e:
            raise HTTPException(422, str(e)) from None
    return rfp_import.summary(session, rfp)


@router.get("", response_model=list[RfpSummary])
def list_rfps(session: Session = Depends(get_session)):
    rfps = session.exec(select(Rfp).order_by(Rfp.created_at.desc())).all()
    return [rfp_import.summary(session, r) for r in rfps]


@router.get("/{rfp_id}", response_model=RfpDetail)
def get_rfp(rfp_id: int, session: Session = Depends(get_session)):
    rfp = session.get(Rfp, rfp_id)
    if rfp is None:
        raise HTTPException(404, "RFP not found")
    return rfp_import.detail(session, rfp)

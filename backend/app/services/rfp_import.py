from __future__ import annotations

import re
import shutil
from pathlib import Path
from uuid import uuid4

from sqlmodel import Session, select

from ..agent.formatrules import extract_rules
from ..config import get_settings
from ..ingest.parsers import parse_rfp
from ..models import Requirement, Rfp, Section
from ..schemas import RequirementOut, RfpDetail, RfpSummary, SectionOut
from . import audit


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "rfp"


def import_rfp(session: Session, src: Path, original_name: str, *, actor: str = "se") -> Rfp:
    """Parse first, store second: a file that cannot be parsed leaves nothing behind."""
    parsed = parse_rfp(src)  # raises ParseError

    dest = get_settings().upload_dir / f"{uuid4().hex[:8]}_{_safe(original_name)}"
    shutil.copyfile(src, dest)

    rules = extract_rules(parsed.instruction_text)
    rfp = Rfp(
        name=parsed.title or Path(original_name).stem,
        filename=original_name,
        file_type=parsed.file_type,
        stored_path=str(dest),
        submission_instructions=parsed.instruction_text,
        format_rules=rules,
        deadline=rules.get("deadline"),
        parse_report=parsed.report(),
    )
    session.add(rfp)
    session.flush()

    section_ids: dict[tuple[str, str], int] = {}
    for order, (code, title) in enumerate(parsed.sections(), start=1):
        sec = Section(rfp_id=rfp.id, code=code, title=title, order=order)
        session.add(sec)
        session.flush()
        section_ids[(code, title)] = sec.id

    for r in parsed.requirements:
        session.add(Requirement(
            rfp_id=rfp.id, section_id=section_ids[(r.section_code, r.section_title)],
            req_id=r.req_id, text=r.text, order=r.order, mandatory=r.mandatory, kind=r.kind,
            source_ref=r.source_ref, parse_confidence=r.parse_confidence,
        ))

    audit.record(session, "rfp", "imported", rfp_id=rfp.id, entity_id=rfp.id, actor=actor,
                 payload={"filename": original_name, "requirements": len(parsed.requirements)})
    session.commit()
    session.refresh(rfp)
    return rfp


def _count(session: Session, rfp_id: int) -> int:
    return len(session.exec(select(Requirement.id).where(Requirement.rfp_id == rfp_id)).all())


def summary(session: Session, rfp: Rfp) -> RfpSummary:
    return RfpSummary(
        id=rfp.id, name=rfp.name, customer=rfp.customer, filename=rfp.filename,
        file_type=rfp.file_type, status=rfp.status, deadline=rfp.deadline,
        requirement_count=_count(session, rfp.id), parse_report=rfp.parse_report,
        created_at=rfp.created_at,
    )


def detail(session: Session, rfp: Rfp) -> RfpDetail:
    reqs = session.exec(
        select(Requirement).where(Requirement.rfp_id == rfp.id).order_by(Requirement.order)
    ).all()
    by_section: dict[int | None, list[Requirement]] = {}
    for r in reqs:
        by_section.setdefault(r.section_id, []).append(r)
    sections = session.exec(
        select(Section).where(Section.rfp_id == rfp.id).order_by(Section.order)
    ).all()
    base = summary(session, rfp)
    return RfpDetail(
        **base.model_dump(),
        submission_instructions=rfp.submission_instructions,
        format_rules=rfp.format_rules,
        sections=[
            SectionOut(
                id=s.id, code=s.code, title=s.title, order=s.order,
                requirements=[
                    RequirementOut(id=r.id, req_id=r.req_id, text=r.text, order=r.order,
                                   mandatory=r.mandatory, kind=r.kind,
                                   parse_confidence=r.parse_confidence)
                    for r in by_section.get(s.id, [])
                ],
            )
            for s in sections
        ],
    )

"""API response shapes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RequirementOut(BaseModel):
    id: int
    req_id: str
    text: str
    order: int
    mandatory: bool | None
    kind: str
    parse_confidence: float


class SectionOut(BaseModel):
    id: int
    code: str
    title: str
    order: int
    requirements: list[RequirementOut]


class RfpSummary(BaseModel):
    id: int
    name: str
    customer: str
    filename: str
    file_type: str
    status: str
    deadline: str | None
    requirement_count: int
    parse_report: dict
    created_at: datetime


class CitationOut(BaseModel):
    marker: int
    doc_id: int | None
    chunk_id: int | None
    doc_title: str
    doc_date: str | None
    location: str
    quote: str


class AnswerOut(BaseModel):
    requirement_id: int
    req_id: str
    requirement: str
    section_code: str
    section_title: str
    order: int
    mandatory: bool | None
    review_status: str  # queued | drafted | accepted
    triage: str | None = None
    status: str | None = None
    confidence: float | None = None
    confidence_breakdown: dict = {}
    flags: list[str] = []
    flag_details: dict = {}
    text: str = ""  # what to show and export: the SE's edit if there is one, else the draft
    draft_text: str = ""
    edited: bool = False
    needs_review: bool = False
    assigned_to: str | None = None
    reasoning: str = ""
    error: str | None = None
    citations: list[CitationOut] = []


class JobOut(BaseModel):
    id: int | None = None
    rfp_id: int
    status: str  # none | running | paused | done | failed
    total: int = 0
    done: int = 0
    message: str = ""
    resume_at: float | None = None


class ClarificationOut(BaseModel):
    id: int
    text: str
    reason: str
    requirement_ids: list[str]
    status: str
    priority: str = "medium"
    impact: str = ""
    source: str = "missing_evidence"
    assigned_to: str | None = None
    answer: str | None = None


class RfpDetail(RfpSummary):
    submission_instructions: str
    format_rules: dict
    sections: list[SectionOut]

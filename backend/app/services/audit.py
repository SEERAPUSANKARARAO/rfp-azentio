from __future__ import annotations

from sqlmodel import Session

from ..models import AuditEvent


def record(
    session: Session,
    entity: str,
    action: str,
    *,
    rfp_id: int | None = None,
    entity_id: int | None = None,
    actor: str = "system",
    payload: dict | None = None,
) -> None:
    """Append to the audit log. Caller commits, so the event lands with the change it describes."""
    session.add(AuditEvent(
        rfp_id=rfp_id, entity=entity, entity_id=entity_id, action=action, actor=actor,
        payload=payload or {},
    ))

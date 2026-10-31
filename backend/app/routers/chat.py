from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..agent import chat
from ..db import get_engine, get_session
from ..llm import get_gateway
from ..models import ChatMessage, Rfp

router = APIRouter(tags=["chat"])


class ChatIn(BaseModel):
    message: str
    focus_requirement_id: int | None = None
    reviewer: str = "se"


def _out(m: ChatMessage) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content, "meta": m.meta, "ts": m.ts.isoformat()}


@router.get("/rfps/{rfp_id}/chat")
def history(rfp_id: int, limit: int = 100, session: Session = Depends(get_session)) -> list[dict]:
    """The conversation so far. Opening an RFP days later shows where you left off."""
    if session.get(Rfp, rfp_id) is None:
        raise HTTPException(404, "RFP not found")
    rows = session.exec(select(ChatMessage).where(ChatMessage.rfp_id == rfp_id).order_by(ChatMessage.id.desc()).limit(min(limit, 500))).all()
    return [_out(m) for m in reversed(rows)]


@router.post("/rfps/{rfp_id}/chat")
async def send(rfp_id: int, body: ChatIn, session: Session = Depends(get_session)) -> dict:
    if session.get(Rfp, rfp_id) is None:
        raise HTTPException(404, "RFP not found")
    text = body.message.strip()
    if not text:
        raise HTTPException(422, "Say something first.")
    if len(text) > 2000:
        raise HTTPException(422, "Keep messages under 2000 characters.")
    user = chat.save(session, rfp_id, "user", text, {"reviewer": body.reviewer, "focus": body.focus_requirement_id})
    reply = await chat.handle(session, get_engine(), get_gateway(), rfp_id, text, body.focus_requirement_id, body.reviewer)
    bot = chat.save(session, rfp_id, "assistant", reply.text,
                    {"intent": reply.intent, "refs": reply.refs, "sources": reply.sources, "select": reply.select})
    return {"user": _out(user), "assistant": _out(bot)}


@router.delete("/rfps/{rfp_id}/chat", status_code=204)
def clear(rfp_id: int, session: Session = Depends(get_session)):
    from fastapi import Response

    for m in session.exec(select(ChatMessage).where(ChatMessage.rfp_id == rfp_id)).all():
        session.delete(m)
    session.commit()
    return Response(status_code=204)

"""Background drafting run for a whole RFP.

Progress is persisted per requirement, so a run survives a restart, and survives the LLM quota running
out: the job pauses (never invents text), says when the quota is expected back, and a later resume
picks up only the requirements still undrafted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import Engine
from sqlmodel import Session, select

from .. import enums
from ..config import get_settings
from ..llm.errors import AllProvidersExhausted, LLMError
from ..llm.gateway import LLMGateway
from ..models import Answer, Job, Requirement, Rfp
from ..rag.conflicts import conflicts_by_chunk
from ..services import audit
from .assess import assess_rfp
from .clarify import build_clarifications
from .interpret import interpret_rfp
from .pipeline import draft_requirement

log = logging.getLogger(__name__)
_running: dict[int, asyncio.Task] = {}
# Answers whose evidence changed while a run was in progress. They are redrafted when the run's pass ends,
# which also covers an answer that was already being drafted from the old evidence when the request arrived.
_recalc_queue: dict[int, set[int]] = {}
MAX_PASSES = 6
MAX_ROUNDS = 5  # how many times a run goes back to drafting because requests arrived while it was finishing


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_running(rfp_id: int) -> bool:
    t = _running.get(rfp_id)
    return bool(t and not t.done())


def latest_job(session: Session, rfp_id: int) -> Job | None:
    return session.exec(select(Job).where(Job.rfp_id == rfp_id).order_by(Job.id.desc())).first()


def pending_requirement_ids(session: Session, rfp_id: int) -> list[int]:
    """Requirements with no drafted or accepted answer, in the RFP's own order."""
    done = {a.requirement_id for a in session.exec(
        select(Answer).where(Answer.review_status != enums.REVIEW_QUEUED)).all()}
    reqs = session.exec(select(Requirement).where(Requirement.rfp_id == rfp_id).order_by(Requirement.order)).all()
    return [r.id for r in reqs if r.id not in done]


def create_job(engine: Engine, rfp_id: int) -> int:
    """Record a run that is about to start, so callers can report it before any model call is made."""
    with Session(engine) as session:
        todo = pending_requirement_ids(session, rfp_id)
        total = len(session.exec(select(Requirement.id).where(Requirement.rfp_id == rfp_id)).all())
        job = Job(rfp_id=rfp_id, status=enums.JOB_RUNNING, total=total, done=total - len(todo),
                  message=f"Drafting {len(todo)} requirement(s)")
        session.add(job)
        session.get(Rfp, rfp_id).status = "drafting"
        session.commit()
        return job.id


# A per-minute rate limit clears in seconds, so the job waits and carries on by itself. A long wait (a daily
# limit) pauses the job instead and tells the user when the quota is expected back.
AUTO_RESUME_MAX_WAIT_S = 150.0
MAX_AUTO_RESUMES = 40


def _interpreted(engine: Engine, rfp_id: int) -> bool:
    with Session(engine) as session:
        return any(r.interpretation for r in session.exec(select(Requirement).where(Requirement.rfp_id == rfp_id)).all())


def _set_message(engine: Engine, job_id: int, message: str) -> None:
    with Session(engine) as session:
        j = session.get(Job, job_id)
        j.message, j.updated_at = message, _now()
        session.commit()


async def run_job(
    engine: Engine,
    gateway: LLMGateway,
    rfp_id: int,
    *,
    concurrency: int | None = None,
    job_id: int | None = None,
    auto_resume_wait_s: float = AUTO_RESUME_MAX_WAIT_S,
    assess: bool = False,
    _round: int = 0,
) -> int:
    s = get_settings()
    if job_id is None:
        job_id = create_job(engine, rfp_id)

    # Level 2: understand the requirements first. They do not change when a conflict is settled or a run is resumed, so
    # this happens once; "Read the requirements" on the Analysis page reads them again on request.
    if assess and not _interpreted(engine, rfp_id):
        _set_message(engine, job_id, "Reading the requirements for dependencies and unclear wording")
        await interpret_rfp(engine, gateway, rfp_id)

    sem = asyncio.Semaphore(concurrency or s.draft_concurrency)
    errors = 0
    failed: set[int] = set()

    def record_error(req_id: int, e: Exception):
        with Session(engine) as session:
            ans = session.exec(select(Answer).where(Answer.requirement_id == req_id)).first()
            if ans is None:
                ans = Answer(requirement_id=req_id)
                session.add(ans)
            ans.error = f"{type(e).__name__}: {str(e)[:200]}"
            session.commit()

    async def work(req_id: int, conflicts: dict, stop: list):
        nonlocal errors
        async with sem:
            if stop:
                return  # quota is spent: leave this one queued for the resume
            try:
                await draft_requirement(engine, gateway, req_id, conflicts=conflicts)
            except AllProvidersExhausted as e:
                stop.append(e)
                return
            except LLMError as e:
                errors += 1
                failed.add(req_id)
                log.warning("draft failed for requirement %s: %s", req_id, type(e).__name__)
                record_error(req_id, e)
                return
            except Exception as e:  # noqa: BLE001  a bug in one requirement must not strand the whole job
                errors += 1
                failed.add(req_id)
                log.exception("unexpected failure drafting requirement %s", req_id)
                record_error(req_id, e)
                return
            with Session(engine) as session:
                j = session.get(Job, job_id)
                j.done += 1
                j.updated_at = _now()
                session.commit()

    stop: list[AllProvidersExhausted] = []
    resumes = passes = 0
    while True:
        with Session(engine) as session:
            # a requirement that failed earlier in THIS run is not retried by the extra passes
            todo = [i for i in pending_requirement_ids(session, rfp_id) if i not in failed]
            conflicts = conflicts_by_chunk(session)
        stop = []
        await asyncio.gather(*(work(i, conflicts, stop) for i in todo))
        if not stop:
            # Anything queued for redraft while this pass ran (a conflict was resolved mid-run) goes round again.
            late = _recalc_queue.pop(rfp_id, set())
            with Session(engine) as session:
                for rid in late:
                    ans = session.exec(select(Answer).where(Answer.requirement_id == rid)).first()
                    if ans is not None:
                        ans.review_status, ans.reviewed_at = enums.REVIEW_QUEUED, None
                session.commit()
                more = [i for i in pending_requirement_ids(session, rfp_id) if i not in failed]
            passes += 1
            if more and passes < MAX_PASSES:
                _set_message(engine, job_id, f"Redrafting {len(more)} more answer(s) whose evidence changed")
                continue
            break
        wait = (stop[0].resume_at - time.time()) if stop[0].resume_at else None
        if wait is None or wait > auto_resume_wait_s or resumes >= MAX_AUTO_RESUMES:
            break  # a long outage: pause and say when it should be back
        resumes += 1
        resume_clock = datetime.fromtimestamp(time.time() + max(wait, 0)).strftime("%H:%M:%S")
        _set_message(engine, job_id, f"Waiting for LLM quota (back about {resume_clock}), then continuing")
        await asyncio.sleep(max(wait, 0) + 2)

    base_message = ""
    with Session(engine) as session:
        j = session.get(Job, job_id)
        rfp = session.get(Rfp, rfp_id)
        if stop:
            e = stop[0]
            when = datetime.fromtimestamp(e.resume_at).strftime("%H:%M") if e.resume_at else None
            j.status = enums.JOB_PAUSED
            j.resume_at = e.resume_at
            j.message = ("LLM quota exhausted on every provider"
                         + (f"; expected back about {when}" if when else "")
                         + f". {j.total - j.done} requirement(s) still queued.")
            rfp.status = "paused"
        else:
            # Not "done" yet. Clarifications, the assessment and any redraft requested meanwhile still follow, and a page
            # that was told "done" would stop watching and keep showing numbers from before they finished.
            base_message = (f"Drafted {j.done} of {j.total}" + (f"; {errors} failed" if errors else "")
                            + (f"; waited out {resumes} quota pause(s)" if resumes else ""))
            j.message = base_message
            rfp.status = "ready"
        j.updated_at = _now()
        session.commit()
    if stop:
        return job_id

    again = False
    note = ""
    try:
        await build_clarifications(engine, gateway, rfp_id)  # the gap list is only complete once every requirement has an answer
        if assess:  # Level 2: classify each answer's capability and find the gaps. Best effort: drafts are already saved
            _set_message(engine, job_id, f"{base_message}. Assessing capabilities and gaps")
            res = await assess_rfp(engine, gateway, rfp_id)
            note = (f". Assessed {res.get('assessed', 0)} requirement(s)"
                    + ("; quota ran out, run the assessment again to finish" if res.get("quota_exhausted") else "")
                    + (f"; {res['failed']} failed" if res.get("failed") else ""))
        # A redraft requested while we were building clarifications or assessing joined this run, but the drafting loop
        # had already ended, so nothing would ever pick it up. Look again, and draft again with the same job.
        if _round < MAX_ROUNDS:
            with Session(engine) as session:
                for rid in _recalc_queue.pop(rfp_id, set()):
                    ans = session.exec(select(Answer).where(Answer.requirement_id == rid)).first()
                    if ans is not None:
                        ans.review_status, ans.reviewed_at = enums.REVIEW_QUEUED, None
                session.commit()
                more = [i for i in pending_requirement_ids(session, rfp_id) if i not in failed]
            if more:
                again = True
                return await run_job(engine, gateway, rfp_id, concurrency=concurrency, job_id=job_id,
                                     auto_resume_wait_s=auto_resume_wait_s, assess=assess, _round=_round + 1)
    finally:
        if not again:  # the run says it is finished only now, whatever happened on the way
            with Session(engine) as session:
                j = session.get(Job, job_id)
                j.status, j.message, j.updated_at = enums.JOB_DONE, base_message + note, _now()
                session.commit()
    return job_id


def start_recalc(
    engine: Engine, gateway: LLMGateway, rfp_id: int, requirement_ids: list[int], reason: str, *, assess: bool = False
) -> tuple[int | None, bool]:
    """Redraft specific answers after the knowledge base changed under them.

    Their status goes back to queued (so any approval is withdrawn and the UI shows them as pending). If a run is
    already in progress they join it; otherwise a new run starts. Returns (job id, joined_a_running_job).
    """
    with Session(engine) as session:
        for rid in requirement_ids:
            ans = session.exec(select(Answer).where(Answer.requirement_id == rid)).first()
            if ans is not None:
                ans.review_status, ans.reviewed_at, ans.error = enums.REVIEW_QUEUED, None, None
        audit.record(session, "rfp", "recalculation_queued", rfp_id=rfp_id, entity_id=rfp_id,
                     payload={"reason": reason, "requirements": len(requirement_ids)})
        running = latest_job(session, rfp_id) if is_running(rfp_id) else None
        running_id = running.id if running is not None else None  # read before the commit expires the row
        session.commit()
    if running_id is not None:
        _recalc_queue.setdefault(rfp_id, set()).update(requirement_ids)
        return running_id, True
    job_id = create_job(engine, rfp_id)
    _set_message(engine, job_id, f"Recalculating {len(requirement_ids)} answer(s): {reason}")
    _running[rfp_id] = asyncio.create_task(run_job(engine, gateway, rfp_id, job_id=job_id, assess=assess))
    return job_id, False


def start_job(engine: Engine, gateway: LLMGateway, rfp_id: int, *, assess: bool = False) -> int | None:
    """Launch in the background. Returns the job id, or None if one is already running for this RFP."""
    if is_running(rfp_id):
        return None
    job_id = create_job(engine, rfp_id)
    _running[rfp_id] = asyncio.create_task(run_job(engine, gateway, rfp_id, job_id=job_id, assess=assess))
    return job_id

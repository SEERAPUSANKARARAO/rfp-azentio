"""Confidence is computed from checks the code ran, not taken from the model's self-report.

Every component is shown to the reviewer in the evidence drawer, so a number like 62% always comes
with the reasons it is not higher."""

from __future__ import annotations

from .. import enums
from .validate import ValidatedDraft


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def confidence(v: ValidatedDraft, top_vec_sim: float | None) -> tuple[float, dict]:
    # how well the cited sources cover the words of the requirement, blended with semantic similarity
    retrieval = _clamp(v.coverage)
    if top_vec_sim is not None:
        retrieval = 0.5 * retrieval + 0.5 * _clamp((top_vec_sim - 0.5) / 0.35)

    if v.claims_total:
        support = v.claims_supported / v.claims_total
    else:
        support = 1.0 if v.sentences_kept else 0.0
    sentence_support = 1.0 if not v.removed else v.sentences_kept / (v.sentences_kept + len(v.removed))
    support = 0.5 * support + 0.5 * sentence_support

    stale = enums.FLAG_STALE in v.flags
    freshness = 0.4 if stale else 1.0

    base = 0.30 * retrieval + 0.45 * support + 0.25 * freshness
    penalties: dict[str, float] = {}
    if enums.FLAG_CONFLICTING in v.flags:
        penalties["conflicting sources"] = 0.75
    if enums.FLAG_UNSUPPORTED in v.flags:
        penalties["statements removed as unsupported"] = 0.85
    if enums.FLAG_MISSING in v.flags:
        penalties["evidence missing"] = 0.85
    if enums.FLAG_INJECTION in v.flags:
        penalties["instructions found in requirement text"] = 0.8

    score = base
    for f in penalties.values():
        score *= f
    if v.status == enums.CANNOT_CONFIRM:
        score = min(score * 0.5, 0.35)
    score = round(_clamp(score), 2)

    return score, {
        "retrieval": round(retrieval, 2), "support": round(support, 2), "freshness": freshness,
        "base": round(base, 2), "penalties": penalties, "final": score,
    }

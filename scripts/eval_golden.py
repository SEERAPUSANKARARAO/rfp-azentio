"""Run the whole agent LIVE on the sample RFPs and score it against the golden expectations.

    cd rfp-ai/backend
    .venv/bin/python -u ../scripts/eval_golden.py                 # everything
    .venv/bin/python -u ../scripts/eval_golden.py --only asib --limit 8
    .venv/bin/python -u ../scripts/eval_golden.py --skip-conflicts

Uses your real keys and the response cache, so an unchanged re-run costs no quota. Writes a full report
to data/eval/report.json so every answer can be read, not only the score.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlmodel import Session, select  # noqa: E402

from app.agent.evaluate import audit_answer, check_expectations, collect  # noqa: E402
from app.agent.jobs import run_job  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db, make_engine  # noqa: E402
from app.llm import get_gateway  # noqa: E402
from app.models import Requirement  # noqa: E402
from app.rag.conflicts import detect_conflicts  # noqa: E402
from app.rag.embeddings import get_embedder  # noqa: E402
from app.rag.indexer import index_kb_dir  # noqa: E402
from app.services.rfp_import import import_rfp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = sorted((ROOT / "backend" / "tests" / "golden").glob("*.json"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="substring of a golden file name")
    ap.add_argument("--limit", type=int, help="first N requirements per RFP (cheap smoke run)")
    ap.add_argument("--skip-conflicts", action="store_true")
    args = ap.parse_args()

    os.environ.setdefault("AS_OF_DATE", "2026-10-31")  # keep staleness reproducible
    s = get_settings()
    out_dir = ROOT / "data" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("eval.db*"):
        f.unlink()
    engine = make_engine(out_dir / "eval.db")
    init_db(engine)
    gw = get_gateway()
    emb = get_embedder()
    print(f"retrieval: {'hybrid (local embeddings)' if emb else 'keyword only'}", flush=True)

    with Session(engine) as session:
        index_kb_dir(session, s.sample_dir / "kb", emb)
        if not args.skip_conflicts:
            t = time.time()
            print("conflicts:", await detect_conflicts(session, gw, emb), f"({time.time() - t:.0f}s)", flush=True)
            from app.models import Conflict, KbDocument
            docs = {d.id: d.display_title for d in session.exec(select(KbDocument)).all()}
            for c in session.exec(select(Conflict)).all():
                print(f"   [{c.topic}] {docs[c.doc_a_id]}  vs  {docs[c.doc_b_id]}: {c.detail[:110]}", flush=True)

    report, total, passed, audit_failures = [], 0, 0, 0
    for gf in GOLDEN:
        g = json.loads(gf.read_text())
        if args.only and args.only not in gf.name:
            continue
        with Session(engine) as session:
            rfp = import_rfp(session, ROOT / "sample_data" / "rfps" / g["filename"], g["filename"])
            rfp_id = rfp.id  # format rules are detected from the file at import, not taken from the answer key
            if args.limit:
                for r in session.exec(select(Requirement).where(Requirement.rfp_id == rfp_id)).all():
                    if r.order > args.limit:
                        session.delete(r)
                session.commit()
        t = time.time()
        await run_job(engine, gw, rfp_id)
        print(f"\n=== {g['filename']}: drafted in {time.time() - t:.0f}s", flush=True)

        with Session(engine) as session:
            by_id = {r.req_id: r for r in session.exec(select(Requirement).where(Requirement.rfp_id == rfp_id)).all()}
            for q in g["requirements"]:
                req = by_id.get(q["req_id"])
                if req is None:
                    continue
                ans_d, ans, cites = collect(session, req)
                total += 1
                if ans is None:
                    print(f"  FAIL {q['req_id']}: no answer saved", flush=True)
                    report.append({"rfp": g["key"], "req_id": q["req_id"], "failures": ["no answer saved"]})
                    continue
                if ans.error:
                    fails = [f"drafting error: {ans.error}"]
                else:
                    fails = check_expectations(q["expect"], ans_d)
                audit = audit_answer(session, ans, ans_d["text"], cites)
                audit_failures += len(audit)
                if not fails and not audit:
                    passed += 1
                else:
                    print(f"  FAIL {q['req_id']}: " + " | ".join(fails + [f"AUDIT {a}" for a in audit]), flush=True)
                    print(f"       got: {ans_d.get('status')} flags={ans_d.get('flags')} conf={ans_d.get('confidence')} "
                          f"text={ans_d.get('text', '')[:140]!r}", flush=True)
                report.append({"rfp": g["key"], "req_id": q["req_id"], "requirement": q["text"], **ans_d,
                               "reasoning": ans.reasoning, "breakdown": ans.confidence_breakdown,
                               "flag_details": ans.flag_details, "failures": fails, "audit": audit,
                               "citations": [{"marker": c.marker, "doc": c.doc_title, "date": str(c.doc_date),
                                              "location": c.location, "quote": c.quote} for c in cites]})

    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nRESULT: {passed}/{total} requirements met every expectation; "
          f"guardrail audit failures: {audit_failures}", flush=True)
    print(f"full report: {out_dir / 'report.json'}", flush=True)
    return 0 if passed == total and audit_failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

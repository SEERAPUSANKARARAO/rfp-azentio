# RFP AI

Grounded RFP response agent for Sales Engineers (Islamic banking and lending). Imports an RFP,
drafts answers only from approved sources, cites every claim, flags missing, conflicting and stale
evidence, and exports the response in the RFP's own format. The SE approves every answer.

Docs: [`docs/architecture.md`](docs/architecture.md) (components, guardrails, evidence, limits) and
[`docs/demo-script.md`](docs/demo-script.md) (timed walk-through, fallbacks, likely questions).
[`live_test/GUIDE.md`](live_test/GUIDE.md) is a separate set of documents, an RFP and a step-by-step guide for testing
the app yourself in the browser with the real models.

## Status

| Area | State |
|---|---|
| LLM gateway: key pools, per-model quota tracking, provider failover, cache, usage | built, tested |
| DB models, FastAPI app, `/api/health`, `/api/llm/status` | built, tested |
| Synthetic knowledge base (25 docs) and 3 test RFPs (74 requirements) with golden expectations | built, tested |
| RFP parsers (xlsx, docx, pdf) and upload API `/api/rfps`: IDs, sections, order, priority, write-back refs | built, tested (74/74 requirements exact) |
| KB ingestion with page/section locations, customer redaction, hybrid retrieval, `/api/kb/*` | built, tested |
| Agent core: owner triage, grounded drafting with structured per-statement citations, validator, confidence, index-time conflicts, clarification list, background job that waits out short quota limits and pauses on long ones | built, tested live (see "Measured quality") |
| Review actions: accept, edit, revert, set status, flag/assign, regenerate with instruction, SE-supplied facts; audit log | built, tested |
| Format-rule detection (word limit, compliance codes, file name, deadlines) and export: XLSX in place, DOCX in place or rebuilt from a PDF, compliance matrix, draft-copy marking, pre-export checks | built, tested; round-trip checked by re-reading the produced files (not opened in Excel or Word) |
| KB admin (upload, deprecate, reuse switches), conflict resolution with recalculation of affected answers, clarification editing | built, tested |
| Frontend: Response Studio, KB and conflicts, clarifications, audit, settings and quota, export dialog | built, browser-checked |
| Chat workspace: grounded questions and explicit commands, history restored on reopen | built, tested |
| Level 2: response schemes read from the RFP, capability classification (standard / configuration / development / third party / not available) checked against the cited text, gap analysis (confirmed gap vs knowledge gap, with owners), requirement reading (dependencies, components, unclear and contradicting wording), ranked clarifications, review gate and submission checklist, workflow board, redraft-without-touching-approved | built, tested live (see "Level 2") |
| Not built | connector cards (Confluence, Notion, Drive, Jira), approved-answer library feedback loop, login and roles, OCR, Arabic |

Offline test suite: 489 tests, no network or keys needed. Browser check: 31 steps in real Chrome (`e2e/browser_check.py`).

## How the agent keeps answers honest

The model drafts; deterministic code decides what survives (`backend/app/agent/validate.py`).

1. **Owner triage first.** Pricing, liability, indemnity, SLAs and service credits, payment terms and named
   customer references never reach the model. They are routed to an owner (Commercial Desk, Legal, Account Mgmt).
2. **Retrieval** returns only permitted, answerable sources, each with date, location and a stale flag.
3. **One model call per requirement**, returning status, answer with `[n]` citations, claims with quotes, and gaps.
4. **The validator rebuilds the answer.** A sentence survives only if it carries a valid citation (or inherits the
   one directly after it, under a stricter test), states only numbers, dates and standards that the cited text
   states, and overlaps the cited text. Everything else is removed and reported to the reviewer.
5. **A status needs evidence.** No surviving cited sentence means "Cannot Confirm", whatever the model claimed.
   A named certification or regulation (ISO 22301, PCI DSS, ...) that no source mentions can never be "Complied".
6. **Flags** come from facts: `stale` (cited source too old or for an old product version), `conflicting`
   (a cited passage contradicts another, found once at index time), `missing`, `unsupported`, `injection`.
7. **Prompt injection.** Bracketed notes that address an AI are removed from the requirement before the model
   sees it, and reported. The prompt also tells the model that requirement and passage text is untrusted data.
8. **Confidence is computed** (retrieval coverage, claim support, freshness, penalties), never self-reported,
   and shown with its breakdown. Citations shown to the reviewer are always verbatim source text.
9. **Quota exhaustion pauses the job**; it never invents text. Resume drafts only what is still undrafted.

## Measured quality (live models, 74 sample requirements)

| Run | Change | Met every expectation | Guardrail audit failures |
|---|---|---|---|
| 1 | first full run | 50 / 74 | 6 |
| 2 | structured statements, validator and prompt fixes | 58 / 74 | 0 |
| 3 | stale/conflict rules, quota-vs-failure fix, judge fix | 73 / 74 | 0 |
| 3, re-scored | corrected one over-literal check (same cached answers) | 74 / 74 | 0 |

Read these numbers with care:

- It is one sample of a non-deterministic model on data I wrote. The same requirements flipped between
  "Complied" and "Partially Complied" across runs. A cached re-run returns identical answers, so it re-scores;
  it does not re-measure.
- Six expectations were relaxed after run 2 because a careful model was right and my key was too strict (for
  example, a source never says "home finance", so Partially Complied is a fair reading). Each has its reason next
  to it in `scripts/sample_rfps.py`. Against the original strict key, run 3 scores 72 / 74.
- The audit is independent of the validator: it re-reads what was saved and checks that every citation quote is
  verbatim in its source, every sentence is cited, no customer name appears and no forbidden source is cited.
  In run 1 it caught a real bug in the validator (a quote credited to a passage it was not in).
- Confidence is computed from checks, not calibrated against outcomes. The mean on this set is 0.89, which says
  the formula is generous on clean answers, not that 89% of answers are right.
- Groq's free tier allows 8,000 tokens a minute per key and model, so a full run takes minutes and rides out
  rate limits. Gemini was erratic (503s and stalls) and the OpenRouter accounts are free tier.

## Run the agent on the sample RFPs (live; uses your keys)

```bash
cd backend
.venv/bin/python -u ../scripts/eval_golden.py                       # all 74 requirements + conflict detection
.venv/bin/python -u ../scripts/eval_golden.py --only asib --limit 8 # cheap smoke run
```

An unchanged re-run is served from the response cache and costs no quota. The full report, with every
answer, status, flag and citation, is written to `data/eval/report.json`.

API (all under `/api`):

| Area | Endpoints |
|---|---|
| Import | `POST /rfps` upload, `GET /rfps`, `GET /rfps/{id}` |
| Knowledge base | `POST /kb/index-samples`, `GET /kb/documents|search|status`, `POST /kb/documents` (upload), `PATCH|DELETE /kb/documents/{id}` |
| Conflicts | `GET /kb/conflicts`, `POST /kb/detect-conflicts`, `POST /kb/conflicts/{id}/resolve|reopen` |
| Drafting | `POST /rfps/{id}/draft` (also resumes), `GET /rfps/{id}/job|answers|clarifications|summary`, `GET /rfps/{id}/events` (SSE) |
| Clarifications | `POST /rfps/{id}/clarifications`, `PATCH|DELETE /rfps/{id}/clarifications/{cid}` |
| Chat | `GET|POST|DELETE /rfps/{id}/chat` |
| Level 2 analysis | `POST /rfps/{id}/assess`, `POST /rfps/{id}/interpret`, `GET /rfps/{id}/assessments` (the compliance matrix), `PATCH /rfps/{id}/assessments/{req}` (validate or correct), `GET /rfps/{id}/gaps` |
| Level 2 review | `GET /rfps/{id}/readiness`, `POST /rfps/{id}/proceed-to-review`, `GET /rfps/{id}/checklist`, `PUT /rfps/{id}/checklist/{key}`, `GET /rfps/{id}/workflow`, `POST /rfps/{id}/assign-suggested`, `POST /rfps/{id}/regenerate-affected` |
| Review | `POST /rfps/{id}/answers/{req}/accept|edit|revert|status|flag|regenerate|facts`, `GET /rfps/{id}/audit` |
| Format and export | `GET|PUT /rfps/{id}/format-rules`, `GET /rfps/{id}/export/check`, `POST /rfps/{id}/export`, `GET /rfps/{id}/export/matrix` |
| System | `GET /health`, `GET /llm/status`, `POST /llm/reset-keys`, `GET /audit` |

Human control: export defaults to approved answers only. A review copy that includes drafts is named `_DRAFT`,
prefixes every unapproved answer, and carries a banner. An accepted answer that is edited, re-statused or handed
off loses its approval. "Cannot Confirm" never receives a compliance code.

## Level 2

Beyond drafting: the app reads the response codes an RFP asks for, classifies how each requirement is met (and refuses to
suggest a code where the evidence does not settle it), separates confirmed product gaps from gaps in our own documentation,
reads the requirements for dependencies and unclear or contradicting wording, ranks the clarification questions, gates review
on what a reviewer should not have to find, and writes the validated code into the customer's own template.

Measured on a held-out Level 2 RFP with the real models: capability as expected on 17 of 17 assessed requirements and all 10
planted gaps found (the first run scored 14 of 16 and 7 of 10, and exposed four weaknesses in the checks, now fixed). I wrote
both the documents and the expectations, and tuned rules after the first run, so this is not an independent test. Steps and
expected outcomes: [`live_test/GUIDE_LEVEL2.md`](live_test/GUIDE_LEVEL2.md). Design: [`docs/architecture.md`](docs/architecture.md).
Browser check: `e2e/level2_check.py` (12 steps, run on a drafted Level 2 RFP).

## Putting it live

Backend on Render, frontend on Vercel: [`docs/deployment.md`](docs/deployment.md). The repository holds `render.yaml` for
Render and `frontend/vercel.json` for Vercel. The free Render plan sleeps when idle, forgets its data on every restart, and
runs keyword-only search to fit in 512 MB. An optional shared access key (`APP_ACCESS_KEY`) protects the site.

## Setup

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env          # then add your keys; .env is git-ignored
.venv/bin/python ../scripts/fetch_embedding_model.py   # ~35 MB, once; slow links take minutes
.venv/bin/python ../scripts/generate_sample_data.py    # regenerates sample_data/ and tests/golden/
```

Keys go in `.env` as comma-separated lists, for example `GEMINI_API_KEYS=k1,k2,k3,k4,k5`. Never paste
keys into chat or commit them. The UI and API only ever show aliases such as `gemini#3`.

After adding keys, check that the model IDs in `models.yaml` exist for them:

```bash
.venv/bin/python ../scripts/list_models.py
```

## Run and test

```bash
cd backend
.venv/bin/python -m pytest -q                     # offline: no network, no keys needed
.venv/bin/python -m uvicorn app.main:app --port 8000   # API docs: http://localhost:8000/docs

cd frontend
npm install && npm run dev                        # http://localhost:5173 (proxies /api to :8000)
npm run build                                     # type-checks, then bundles to frontend/dist
```

Browser check (real Chrome through Playwright; needs the backend and `npm run dev` running, and uses the live
models for the drafting step):

```bash
cd backend && .venv/bin/python ../e2e/browser_check.py --out /tmp/shots
```

Set `AS_OF_DATE=2026-10-31` in `.env` to pin "today" so staleness results are reproducible.

## How the LLM layer handles daily limits

- Each task (`draft`, `assess`, `structure` and `fast`) has an ordered chain in `models.yaml`.
- Quota is tracked per (key, model). A 429 puts that pair on cooldown, or marks it exhausted until the
  provider's daily reset when the error says "per day". The next key is tried, then the next model.
- State is stored in `data/llm.db`, so a restart does not forget that a key is spent.
- Identical prompts are served from a cache and cost nothing. Failure is explicit: if every key in the
  chain is spent, the call raises `AllProvidersExhausted` with the earliest retry time, and no text is invented.
- Keys from the same Google project, or the same Groq organisation, may share one quota. For five
  independent budgets, use keys from separate projects or accounts.

## Retrieval

- Keyword search (SQLite FTS5 BM25, expanded with `sample_data/glossary.yaml`) fused with local
  embeddings by reciprocal rank. If the embedding model has not been downloaded, retrieval runs on
  keyword search alone and `/api/kb/status` reports `retrieval_mode: keyword`.
- Two filters cannot be turned off: `reuse_permitted=false` (confidential past responses) and
  `answerable=false` (internal commercial/legal positions, and the glossary: a definition is not evidence).
- Stale and deprecated sources are returned but flagged, so the agent can see an old claim that
  conflicts with a new one.
- Customer names are redacted once, at index time. Only `display_title` and the redacted chunk text are
  ever shown or quoted.

## Layout

```
backend/app/llm/        gateway, key pool, rate-limit parsing, cache, provider adapters
backend/app/ingest/     RFP parsers (xlsx, docx, pdf), KB loader, chunker, redaction
backend/app/rag/        embeddings, glossary expansion, indexer, hybrid retrieval
backend/app/agent/      triage, prompts, validator, scoring, drafting job, clarifications, format rules, chat
backend/app/export/     XLSX and DOCX writers, pre-export checks
backend/app/routers/    system, rfps, kb, agent, review, export, chat
backend/app/services/   RFP import, review, SE facts, KB admin, conflict resolution, audit, export
backend/app/models.py   SQLModel tables        backend/app/freshness.py   stale-source rules
backend/tests/          pytest suite           backend/tests/golden/      expected outcomes per requirement
sample_data/            synthetic KB, test RFPs, TRAPS.md   scripts/   generators and utilities
frontend/src/           pages (studio, knowledge, clarifications, audit, settings) and components
e2e/browser_check.py    31-step browser check      docs/   architecture note and demo script
live_test/              held-out documents, RFP, loader and test guide
```

## Known limits

Single user, no login. Scanned PDFs are rejected (no OCR). English only. Synthetic data only; no real RFP has
been tried. Exports were checked by re-reading them with openpyxl and python-docx, not by opening them in Excel
or Word. Confidence is uncalibrated, and model output varies between runs. Full list: `docs/architecture.md`, section 8.

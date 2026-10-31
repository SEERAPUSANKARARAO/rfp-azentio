# RFP AI: architecture note

A Sales Engineer uploads an Islamic banking or lending RFP. RFP AI drafts each answer **only from approved sources**,
cites every statement, flags missing, conflicting and stale evidence, routes pricing and legal questions to their
owners, and exports the response in the RFP's own format. The Sales Engineer (SE) approves every answer. Nothing is
final until they do.

All product content in the demo is **synthetic and labelled SAMPLE** (a fictional product, "Meridian Islamic Banking
Suite"). No real customer or Azentio data is used.

## 1. Components and data flow

```
 Browser  (React 19, Vite, Tailwind, TanStack Query)
   RFP list | Response Studio (queue, answer, evidence drawer, chat) | Knowledge Base & conflicts
   Clarifications | Audit log | Settings (quota)
        |  REST + server-sent events (draft progress)
 FastAPI  -----------------------------------------------------------------------------
   ingest   parsers: xlsx / docx / pdf  ->  Requirement (ID, order, section, priority, write-back ref)
            knowledge base: load -> redact customer -> chunk (per bullet) -> FTS5 + local embeddings
            conflicts: candidate pairs -> LLM judge, once, at index time
   agent    owner triage -> retrieve -> draft (LLM) -> VALIDATE (code) -> score -> persist
   review   accept / edit / flag / set status / regenerate / SE-supplied facts -> audit log
   export   XLSX filled in place | DOCX in place or rebuilt from a PDF | compliance matrix
   chat     explicit command router + grounded question answering
   LLM gateway  task -> model chain | key pools | per-(key, model) quota state | response cache
 SQLite: app.db (RFPs, answers, citations, KB, conflicts, audit, chat)   llm.db (cache, key state, usage)
 Local ONNX embedding model (no API quota)      Files: uploads, exports
```

## 2. One requirement, end to end

1. **Import.** A deterministic parser reads the file. Requirement IDs, section membership, order and
   mandatory/optional come from the document, never from a model. Where to write the answer back is recorded.
2. **Owner triage.** Pricing, liability, indemnity, SLAs and service credits, payment terms and named customer
   references are matched by pattern and **never reach the model**. They go to an owner.
3. **Retrieve.** Keyword search (SQLite FTS5 BM25, expanded with an Islamic-finance glossary) is fused with local
   embeddings by reciprocal rank. Two filters cannot be switched off: sources that must not be reused, and sources
   that must not be used for answers. A passage an SME has superseded is never offered. Every passage carries its
   source, location, date and freshness.
4. **Draft.** One model call per requirement returns a status and a list of statements, each with the passage numbers
   that support it and a verbatim quote. Text inside the requirement that addresses an AI is removed first and reported.
5. **Validate. This is where "no invented facts" is enforced, in code.** A statement survives only if it names a real
   passage, states only numbers, dates and standards that its cited text states, and overlaps that text. A requirement
   that names a certification no source mentions (ISO 22301) can never be "Complied". A status with no surviving
   supported statement becomes "Cannot Confirm". A status that contradicts the answer's own words is corrected.
6. **Flag.** `stale` (source too old or for an old product version), `conflicting` (a cited passage contradicts
   another, found at index time), `missing`, `unsupported`, `injection`, `se_provided`.
7. **Score.** Confidence is computed from retrieval coverage, claim support and freshness, with penalties. It is shown
   with its breakdown. It is not self-reported and not calibrated against outcomes.
8. **Review.** The SE accepts, edits, regenerates, flags to an owner, or supplies a fact the sources lack (stored as a
   dated, authored source and cited as SE-provided). Editing or re-statusing an accepted answer withdraws the approval.
9. **Export.** Approved answers only by default, in the RFP's own format, with its compliance wording, file name and
   per-answer source references. A review copy that includes drafts is named `_DRAFT`, marks every unapproved answer,
   and carries a banner.

## 3. Models and tools

| Purpose | Choice | Notes |
|---|---|---|
| Drafting, structure | Groq `gpt-oss-120b`, then Gemini `3.8-flash`, then Groq `qwen3.8-27b` | Groq first: fast and dependable on the day it was measured |
| Quick calls (conflict judge, clarification wording) | Groq `gpt-oss-20b`, `qwen3.8-27b`, then Gemini flash-lite | |
| Last resort | OpenRouter free models | Slow and capped per account; resilience, not capacity |
| Embeddings | `BAAI/bge-small-en-v1.5` via fastembed (local ONNX) | Costs no quota. If absent, search falls back to keyword only and says so |
| Search | SQLite FTS5 (BM25) + cosine over stored vectors, rank fusion | |
| Parsing | openpyxl, python-docx, pdfplumber | Scanned PDFs are not supported (no OCR) |
| Backend | FastAPI, SQLModel, SQLite (WAL) | |
| Frontend | React 19, TypeScript, Vite, Tailwind 4, TanStack Query | |

Model IDs are in `models.yaml` and were verified with real calls on 2026-10-31. Free-tier availability changes fast:
`scripts/list_models.py --probe` re-checks them.

## 4. The LLM gateway and free-tier limits

Each task (`structure`, `draft`, `fast`) has an ordered chain of models. Quota is tracked per **(key, model)**:

| Event | Behaviour |
|---|---|
| Per-minute rate limit (429) | that key and model cool down; the next key is tried, then the next model |
| Per-day limit | marked exhausted until the provider's reset time |
| Fast 5xx (503, "high demand") | 30 s cooldown for that key and model |
| Timeout | the whole model is skipped on every key for 2 minutes (a stall is model-wide) |
| Every attempt failed (bad output, rejected) | reported as a failure, never as a quota pause |
| All keys rate-limited | the job waits out a short limit by itself, or pauses and says when to resume |

Identical prompts are served from a cache at no quota cost. Measured on the Groq free tier: 8,000 tokens per minute
and 1,000 requests per day per key and model, so a full RFP needs spreading across keys and models. Gemini was
erratic (503s and stalls) and the OpenRouter accounts are free tier. Keys live only in the backend `.env`; the UI shows
aliases such as `groq#2`.

## 5. How this meets the brief's guardrails

| Brief | Where it is enforced |
|---|---|
| SE in control: nothing final until approved | review state machine; export defaults to approved only; edits and hand-offs withdraw approval |
| Pricing, SLA, liability, commitments never auto-answered | pattern triage before any model call; owner routing; "Cannot Confirm" gets no compliance code |
| Confidentiality: reuse only where permitted, names redacted | `reuse_permitted` filter in retrieval (cannot be switched off); redaction at index time, so prompts, quotes and exports only ever see redacted text |
| Source quality: surface outdated sources | rule-based staleness (age, product version, deprecated) on every cited source |
| Agents show reasoning or cite sources | verbatim quotes with location and date; "why this status"; confidence breakdown; audit trail |
| No invention | the validator described in section 2, step 5 |
| Prompt injection in an RFP | bracketed instructions removed before drafting and reported; the prompt marks all document text as untrusted |

## 6. Evidence

| Check | Result |
|---|---|
| Backend tests (offline, no keys) | 357 passing: parsers, retrieval, validator, gateway failover, review, export, chat |
| Live evaluation, 74 sample requirements, 3 formats | 73/74 as scored; 74/74 after correcting one over-literal check; **72/74 against the original stricter key** |
| Independent audit of saved answers (verbatim quotes, every sentence cited, no names, no forbidden source) | 0 failures in the last two runs. It caught a real validator bug in run 1 |
| Real-browser end-to-end (Chrome via Playwright) | 31/31 steps, including WCAG AA contrast in light and dark and a phone-width layout |
| Retrieval, 58 requirement-to-source checks | 100% in the top 6, keyword and hybrid |
| Retrieval on 10 fresh paraphrases | keyword 9/10, hybrid 10/10 |

**What this does not prove.** One sample of a non-deterministic model: the same requirement flipped between
Complied and Partially Complied across runs. The data was written by the same hand as the expected answers.
Six expectations were relaxed after run 2 where a careful model was right and the key was too strict (each is annotated
in `scripts/sample_rfps.py`). Confidence is uncalibrated. No real RFP has been tried. Exported files were verified by
re-reading them with openpyxl and python-docx, not by opening them in Excel or Word.

## 7. Moving to production

| Today | For production |
|---|---|
| Single user, no login; reviewer name is typed | SSO, roles (SE, SME, owner, admin), per-RFP access |
| SQLite and local files | Postgres with pgvector, object storage for uploads and exports |
| Drafting runs in the API process | a worker queue (jobs are already per-requirement and resumable) |
| Free-tier keys rotated by the gateway | paid keys with budgets, plus spend and latency monitoring |
| `.env` for keys | a secrets manager |
| Local embedding model | a hosted embedding service or a pinned model server |
| Evaluation run by hand | the golden evaluation and browser check in CI, with a fixed model version |
| No OCR | an OCR step for scanned RFPs |
| English RFPs only | Arabic RFP and answer support |

## 8. Known limits

- Scanned PDFs are rejected with a clear message. Unusual spreadsheet layouts may need the parse report corrected.
- A PDF RFP is answered in Word (as the sample RFP asked), not as a PDF.
- The chat changes an answer only through explicit commands that name a requirement. Free-form messages are questions.
- Conflict detection compares passages that share a kind of fact; it can miss a contradiction phrased very differently.
- Annexures are not a separate concept: upload them as another RFP, or add them to the knowledge base.
- The simulated connector cards in the original mockup (Confluence, Notion, Drive, Jira) were not built.

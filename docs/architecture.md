# RFP AI: architecture note

A Sales Engineer uploads an Islamic banking or lending RFP. RFP AI drafts each answer **only from approved sources**,
cites every statement, flags missing, conflicting and stale evidence, routes pricing and legal questions to their
owners, and exports the response in the RFP's own format. The Sales Engineer (SE) approves every answer. Nothing is
final until they do.

All product content in the demo is **synthetic and labelled SAMPLE** (a fictional product, "Meridian Islamic Banking
Suite"). No real customer or Azentio data is used.

## 1. Components and data flow

![RFP AI system architecture: a browser app, a FastAPI backend, an LLM gateway over three model providers, and local storage](architecture.svg)

Green boxes are deterministic code. The orange `model` tag marks the only places a language model is called: drafting, the contradiction judge, clarification wording and chat answers. Everything else, including every check on what a model writes, is code. A PNG copy for slides is in `docs/architecture.png`.

### Data model

| Table | Holds |
|---|---|
| `Rfp`, `Section`, `Requirement` | the imported RFP: its ID, order, section, mandatory or optional, where to write the answer back, the parse report, the detected format rules and deadline |
| `KbDocument` | a source: kind, date, product version, `approved` or `deprecated`, `canonical`, `reuse_permitted`, `answerable`, customer, and `display_title` (the customer-redacted title, the only one ever shown or cited) |
| `KbChunk` | one searchable passage: text, location (section or page), embedding. Each has a row in the FTS5 index |
| `Answer` | one per requirement: draft text, the SE's edited text, status, computed confidence and its breakdown, triage route, flags and their details, review status (`queued`, `drafted`, `accepted`), assignee |
| `Citation` | one per cited passage: the verbatim quote, document title, date and location |
| `ClarificationQuery` | a question for the buyer, with its reason and the requirement IDs it covers |
| `Conflict` | two contradicting passages, the topic, `open` or `resolved`, and the canonical document once an SME decides |
| `AuditEvent` | who did what and when: drafts, edits, approvals, flags, regenerations, SE facts, KB changes, conflict resolutions, exports |
| `ChatMessage`, `Job` | the conversation for an RFP; a drafting run (per-requirement state lives on `Answer`) |

The LLM gateway keeps its own file, `llm.db`: the response cache, per-key and per-model quota state, and usage.
There are no schema migrations. At startup the backend checks the database file against these tables and refuses an
outdated one with a clear message, instead of failing later with a server error.

## 2. One requirement, end to end

![How one requirement becomes a checked answer: parse, owner triage, retrieve, draft, validate, score and flag, review, export](pipeline.svg)

A PNG copy is in `docs/pipeline.png`. The steps in words:

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

### Around the pipeline

- **Knowledge base administration.** Sources are added in the UI, one or many files at once (Word, PDF, Markdown or
  text, up to 15 MB each), each with its own date, version, kind and customer. They are sent one after another and
  each reports its own result. An SME can deprecate a source, mark it canonical, or switch off reuse or use for
  answers. These take effect on the next retrieval and are audited. A source that answers cite cannot be deleted,
  only deprecated, so those answers keep their evidence.
- **Settling a conflict.** The SME picks the document that is right. The other side's passage is marked superseded:
  it stays in the knowledge base for the audit trail but is never offered to the model again. Approvals on the
  answers that cited either side, or were flagged because of the conflict, are withdrawn, because the evidence
  changed, and those answers are redrafted.
  If a drafting run is already going, the redraft joins it.
- **Drafting runs.** Each requirement is saved as soon as it is done, three at a time, with progress streamed to the
  browser and a polling fallback. One failing requirement does not stop the others. A rate limit shorter than
  about 2.5 minutes is waited out automatically. A longer outage pauses the run, says when to resume, and keeps
  everything already drafted. Closing the browser or restarting the backend loses nothing.
- **Chat.** A message that matches an explicit command to change something (accept, rewrite, add a fact) acts only
  on a requirement it names or that is selected, and refuses when there is nothing to act on. Summaries, lists and
  "why was 3.4 flagged?" only read. Any other message is a question,
  answered through the same prompt and validator as a draft, with its sources. History is stored and restored.
- **Format rules.** Word limit, compliance wording, required file name and both deadlines are read from the RFP's
  instructions by pattern, not by a model, and shown at import for the SE to confirm at export. Compliance wording
  is recognised as `using only: C = ..., PC = ...` and as `C (Complies), PC (...) or NC (...)`. A rule that is not
  stated is reported as not stated, never guessed.

## 3. Models and tools

| Purpose | Choice | Notes |
|---|---|---|
| Drafting | Groq `gpt-oss-120b`, then Gemini `3.8-flash`, then Groq `qwen3.8-27b` | Groq first: fast and dependable on the day it was measured |
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

Each task (`draft` and `fast`, plus a `structure` chain that is configured but not used yet) has an ordered chain of models. Quota is tracked per **(key, model)**:

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
| Real-browser end-to-end (Chrome via Playwright) | 31/31 steps, including WCAG AA contrast in light and dark and a phone-width layout. Re-run after the latest changes |
| Held-out live test (`live_test/`): a different product, 11 sources, 23 requirements | all 23 statuses in the expected range; 0 guardrail failures; 57 of 57 saved citation quotes verbatim; no restricted or internal source cited; no customer name or internal figure in any answer, chat reply or export |
| Retrieval, 58 requirement-to-source checks | 100% in the top 6, keyword and hybrid |
| Retrieval on 10 fresh paraphrases | keyword 9/10, hybrid 10/10 |

**Defects found after the main evaluation.** Running new material through the app found three that the earlier
checks missed, and each is fixed with a test:

1. The held-out RFP words its compliance codes as `C (Complies), PC (...) or NC (...)`. The detector only knew one
   phrasing, so the exported Compliance column came out empty.
2. The flag and add-fact dialogs lost focus after every keystroke, so only the first character stayed. The browser
   check missed it because it filled fields in one step; it now types key by key.
3. A database file from an early version made every knowledge-base request return a server error. Startup now
   refuses such a file with a clear message.

The first of these means the live-test RFP is no longer a fully unseen test.

**What this does not prove.** One sample of a non-deterministic model: the same requirement flipped between
Complied and Partially Complied across runs. The data was written by the same hand as the expected answers.
Six expectations were relaxed after run 2 where a careful model was right and the key was too strict (each is annotated
in `scripts/sample_rfps.py`). Confidence is uncalibrated. No real RFP has been tried. Exported files were verified by
re-reading them with openpyxl and python-docx, not by opening them in Excel or Word. The live-test documents and
their expectations were also written by the same hand, and its results come from one run through the API.

## 7. Moving to production

| Today | For production |
|---|---|
| Single user, no login; reviewer name is typed | SSO, roles (SE, SME, owner, admin), per-RFP access |
| SQLite and local files, no migrations | Postgres with pgvector, object storage for uploads and exports, and real migrations |
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
- There are no schema migrations: a database from an older version must be deleted and recreated.
- Several knowledge-base files can be added at once, but an RFP is imported one file at a time.
- The simulated connector cards in the original mockup (Confluence, Notion, Drive, Jira) were not built.

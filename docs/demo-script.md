# Demo script (about 9 minutes)

The story: an SE gets an Islamic banking RFP with 30 requirements. In minutes they have grounded, cited drafts, the
traps are caught, and they approve and export in the buyer's own format. Every claim below was checked in the
real-browser run (`e2e/browser_check.py`).

## Before you present

Do this the day before, and again an hour before.

| Step | Command (from `rfp-ai/`) | Why |
|---|---|---|
| Keys in place | check `.env` (3 Groq, 3 Gemini, 3 OpenRouter) | the live model is the point |
| Local embeddings present | `cd backend && .venv/bin/python ../scripts/fetch_embedding_model.py` | slow links take minutes, not seconds |
| Model IDs still valid | `.venv/bin/python ../scripts/list_models.py --probe` | free-tier models change |
| Pin the date | add `AS_OF_DATE=2026-10-31` to `.env` | staleness and the figures below stay reproducible |
| Warm the cache | `.venv/bin/python -u ../scripts/eval_golden.py` (about 3 minutes) | later runs of the same prompts are instant and use no quota |
| Start backend | `cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000` | |
| Start frontend | `cd frontend && npm run dev` then open http://localhost:5173 | |
| Fresh start | delete `data/rfp_ai.db*` and restart the backend | an empty list, like a new user |
| Dry run | `.venv/bin/python ../e2e/browser_check.py --out /tmp/shots` (backend and frontend running) | 31 checks, about 4 minutes |

Be upfront that the warm cache replays identical prompts. The first run is live; that is what the 3-minute warm-up
was. Say so if asked.

## The walk-through

| Min | Screen | Do | Say | You should see |
|---|---|---|---|---|
| 0:00 | Knowledge Base | Click **Load sample knowledge base**, point at the badges | "These are the only sources an answer may come from. Everything is labelled sample. Customer names are already redacted." | 25 sources, 4 stale, `redacted` and `never reused` badges |
| 0:45 | Knowledge Base | **Detect conflicts** | "A model compares passages that talk about the same thing, once, at index time." | about 5 contradictions: four RTO ones and the ISO 27001 one, stale (red) beside current (green) |
| 1:30 | Active RFPs | Drop `ASIB_RFP-2026-017_Core_Islamic_Banking.xlsx` | "It reads the structure first and tells me what it found before it drafts anything." | 30 requirements (25 mandatory), 120 words, C / PC / NC, file name, both deadlines |
| 2:00 | Studio | **Start drafting** | "Progress streams in. If a model rate-limits, it rotates keys and models. If quota runs out, it pauses and never invents text." | `30/30 drafted` |
| 2:30 | Studio | Open **3.1**, click a citation chip | "Two sources, with quote, page, date. Click and you are at the exact sentence." | Complied, about 93%, no flags |
| 3:15 | Studio | Open **3.2** | "The 2024 documents say 4 hours, the 2026 policy says 1 hour. It answers from the current one and flags the contradiction." | answer says 1 hour; red banner; 69% medium |
| 3:45 | Studio | **Accept & next** | "My approval is recorded with my name." | `3.2 approved` |
| 4:00 | Studio | Open **3.3**, **3.4**, **4.4**, **6.1** | 3.3: "It does not claim the expired certificate." 3.4: "Nothing mentions ISO 22301, so it asks instead of guessing." 4.4: "The only mention is in a customer response marked do-not-reuse. It is never retrieved." 6.1: "Pricing never reaches the model." | expired answer; "No approved source ... confirms: ISO 22301"; clarification; owner banner and a disabled Accept |
| 5:15 | Studio | **Edit** an answer; **Add fact** on 4.4 | "I can supply what the sources lack. It is stored under my name and cited as SE-provided, so nobody mistakes it for documentation." | word counter; `SE-provided` banner |
| 6:00 | Knowledge Base | **Mark B canonical** on each RTO conflict | "An SME settles it. The stale passage stops being used, and answers that relied on it are redrafted." | cards turn resolved; toasts say how many answers are redrafting |
| 6:45 | Studio | Open **3.2** again | "Its approval was withdrawn, because the evidence changed." | no conflict banner; Accept enabled again |
| 7:15 | Studio chat | "summarise this RFP", "why was 3.4 flagged?", then "accept 6.1" | "It answers from the sources. It only changes something when I name it, and it refuses when there is nothing to approve." | summary; explanation with sources; "no answer text" |
| 8:00 | Export | **Review copy**, then **Approved answers only** | "A review copy is marked DRAFT. A submission copy refuses to break the buyer's word limit unless I override." | `_DRAFT.xlsx`; a red word-limit check; **Export anyway** |
| 8:30 | the exported file | open it in Excel | "Their workbook, their IDs, their C / PC / NC, with sources on every answer and a matrix, clarifications and source index." | answers in place; extra sheets |
| 9:00 | Audit Log | open it | "Every draft, edit, approval and export, with who did it." | accepted, edited, flagged, conflict resolved, exported |

Optional second RFP (30 seconds): upload `FHFH_NFH-RFP-2026-09_Retail_Lending.docx`. Requirement **4.1.4** hides an
instruction aimed at an AI. It is removed, reported, and ignored.

## If something goes wrong

| Symptom | Likely cause | Do this |
|---|---|---|
| Drafting is slow | cache is cold, or free-tier rate limits | the banner says it is waiting for quota; keep talking, it continues by itself. Warm the cache next time |
| "Quota exhausted ... expected back about HH:MM" | every provider limited | click **Resume** after the time shown; explain the pause is the safety behaviour |
| Search mode says `keyword` | the embedding model is not downloaded | fine for the demo; fetch it with the script above |
| A figure differs from this script | a different model answered | the structure and flags are what matter; the answer text can vary |
| Settings shows a key as `rejected` | a bad key | replace it in `.env`, restart, **Reset key state** |

## Questions to expect

| Question | Honest answer |
|---|---|
| How do you stop hallucination? | A code validator, not a prompt. Every statement must name a real passage, state only figures its source states, and overlap it. What fails is removed and shown to the reviewer. |
| What if the model fabricates a quote? | Quotes are checked verbatim against the passage and a citation always shows real source text. A separate audit re-checks saved answers; it found a bug of mine once. |
| How accurate is it? | 73 of 74 on our sample set, 72 of 74 against the original stricter key, 0 audit failures. One sample, our own data, a non-deterministic model; confidence is not calibrated. |
| Why not hand the whole PDF to a model? | IDs, order and structure must be exact, and each answer needs its own evidence. A parser reads the structure; the model only drafts one requirement at a time. |
| Is this real Azentio data? | No. It is synthetic and labelled sample, as the brief requires. |
| What happens to customer names in past responses? | Redacted at index time. A document marked do-not-reuse is never retrieved. |
| What stops a hidden instruction in an RFP? | Bracketed notes aimed at an AI are removed before drafting and reported; the validator would also reject an unsupported claim. |
| What does not work yet? | Scanned PDFs, Arabic RFPs, real login and roles, and a hosted worker queue. See section 8 of the architecture note. |

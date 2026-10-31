# Demo script (about 10 minutes)

A word-for-word script for presenting RFP AI. **DO** is what your hands do. **SAY** is what you say, in plain
words. The numbers below come from the browser check run against the sample RFP on 31 October 2026. Model answers
can differ a little between runs, so if a figure is off by a few points, that is normal; the flags and the
structure are what matter.

The story: a Sales Engineer gets an Islamic banking RFP with 30 requirements. In minutes they have cited drafts,
the traps are caught, and they approve and export in the buyer's own format. The Sales Engineer stays in control
the whole time.

## Before you go on

Do this the day before, and again an hour before.

| Step | Command (from `rfp-ai/`) | Why |
|---|---|---|
| Keys in place | check `.env` (3 Groq, 3 Gemini, 3 OpenRouter) | the live model is the point |
| Pin the date | add `AS_OF_DATE=2026-10-31` to `.env` | staleness and the figures below stay the same on any day |
| Local embeddings present | `cd backend && .venv/bin/python ../scripts/fetch_embedding_model.py` | slow links take minutes, not seconds |
| Model IDs still valid | `.venv/bin/python ../scripts/list_models.py --probe` | free-tier models change |
| Warm the cache | `.venv/bin/python -u ../scripts/eval_golden.py` (about 3 minutes) | later runs of the same prompts are instant and use no quota |
| Start backend | `cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000` | |
| Start frontend | `cd frontend && npm run dev`, then open http://localhost:5173 | |
| Fresh start | stop the backend, delete `data/rfp_ai.db*`, start it again | an empty list, like a new user |
| Dry run | `.venv/bin/python ../e2e/browser_check.py --out /tmp/shots` | 31 checks, about 4 minutes |

Be upfront if asked: with a warm cache, the drafting step replays answers the models already gave. The first run
was live; that is what the 3-minute warm-up was.

Have ready: the RFP `sample_data/rfps/ASIB_RFP-2026-017_Core_Islamic_Banking.xlsx` on the desktop, and the browser
at full screen. Say "sample data" out loud at least once: every document is synthetic and labelled as such.

## Opening (0:00, 30 seconds)

**SAY:** "Responding to an RFP is slow and risky. A Sales Engineer answers dozens of questions, from product
documents, old responses and security papers, some of which are out of date or contradict each other. One wrong
claim in a bid can cost the deal, or worse. RFP AI drafts every answer only from approved sources, shows exactly
where each sentence came from, and refuses to answer when the evidence isn't there. The engineer approves
everything. Let me show you."

## Scene 1: the knowledge base (0:30, 60 seconds)

**DO:** Open **Knowledge Base**. Click **Load sample knowledge base**. Point at the tiles, then at the badges on
the documents. Click **Detect conflicts**.

**SAY:** "These are the only sources an answer is allowed to use: product guides, security papers, past RFP
responses. All of it is synthetic sample data. Look at the badges. Four sources are stale, because they are
old or describe an old product version. Past responses have the customer's name redacted, and this one is
marked never reused, so it can never be retrieved. Now I ask a model to compare passages that talk about the same
thing. It finds five contradictions: four about disaster-recovery times, one about a certificate. Old on one
side, current on the other."

**YOU SHOULD SEE:** 25 sources, 4 stale, `redacted` and `never reused` badges, about 5 open conflicts.

## Scene 2: import the RFP (1:30, 40 seconds)

**DO:** Go to **Active RFPs** and drop in the ASIB workbook.

**SAY:** "I drop in the buyer's spreadsheet. Before it drafts anything, it tells me what it found: 30
requirements, 25 mandatory, and the formatting rules buried in the instructions: 120 words per answer, their
own compliance codes, the file name they want, and both deadlines. The IDs, the order and the sections come from
the file itself, never from a model, so they can't be invented."

**YOU SHOULD SEE:** 30 requirements (25 mandatory), words per answer 120, compliance wording C / PC / NC, file
name, questions-by and submission dates.

## Scene 3: draft (2:10, 30 seconds)

**DO:** Click **Start drafting**. Keep talking while the progress runs.

**SAY:** "Each requirement is drafted separately. Progress streams in. If a provider rate-limits, it rotates keys
and models by itself; if everything is exhausted it pauses and tells me when to resume. It never fills the gap
with made-up text."

**YOU SHOULD SEE:** `30/30 drafted`.

## Scene 4: a well-supported answer (2:40, 40 seconds)

**DO:** Open **3.1**. Click a citation chip.

**SAY:** "A clean one. Complied, 93 percent, no flags. Every sentence carries a citation. I click one and I'm at
the exact sentence in the source, with the page and the date. Nothing here is paraphrase I have to trust."

**YOU SHOULD SEE:** Complied, about 93%, no flags, the verbatim quote with its source and date.

## Scene 5: two sources disagree (3:20, 50 seconds)

**DO:** Open **3.2**. Point at the red banner. Click **Accept & next**.

**SAY:** "Here the 2024 documents say the recovery time is four hours and the 2026 policy says one hour. The
system answers from the current source and raises a red flag: conflicting evidence. Its confidence drops to 69
percent. I'm happy with the answer, so I approve it. My approval is recorded with my name. Remember this one."

**YOU SHOULD SEE:** the answer says 1 hour, a red conflicting banner, about 69%, then `3.2 approved`.

## Scene 6: the traps (4:10, 90 seconds)

**DO:** Open **3.3**, then **3.4**, then **4.4**, then **6.1**. Say one line for each.

**SAY:**
- **3.3:** "An expired certificate. It does not claim it as current."
- **3.4:** "The buyer asks about ISO 22301. No approved source mentions it, so the answer is 'Cannot Confirm', at 23
  percent. It adds a question for the buyer instead of guessing."
- **4.4:** "A plausible feature. The only place it appears is a customer response marked do-not-reuse. That document is
  never retrieved, so nothing is invented."
- **6.1:** "Pricing. This never reaches the model at all. It goes to the Commercial Desk, and I can't even approve it
  here. Five requirements in this RFP are routed to owners this way."

**YOU SHOULD SEE:** Not Complied for 3.3; Cannot Confirm (about 23%) with a clarification for 3.4 and 4.4; an
owner banner and a disabled Accept button for 6.1.

## Scene 7: the engineer stays in control (5:40, 50 seconds)

**DO:** On **4.4**, click **Add fact**, type: *The platform includes a rules-based credit scoring module for retail
lending, not machine learning.* Click **Save fact**. Then on any answer click **Edit** and show the word counter.

**SAY:** "When I know something the documents don't say, I add it. It is saved as a dated source in my name and
cited as 'SE-provided', so nobody can mistake it for official documentation. I can edit any answer; there is a
word counter against the buyer's 120-word limit. And if I edit or hand off an approved answer, the approval is
withdrawn. Nothing is final until I accept it."

**YOU SHOULD SEE:** a toast "Fact saved as an SE-provided source", then 4.4 redrafted as Not Complied (about 90%)
with an **SE-provided** flag, its citation reading "SE-provided fact" with your name and the date.

## Scene 8: settle the conflict (6:30, 50 seconds)

**DO:** Go to **Knowledge Base**. On each recovery-time conflict card, click **Mark B canonical & redraft...** (B
is the newer document). Then open **3.2** again.

**SAY:** "Now the subject-matter expert decides which document is right. The stale passage stays in the library for
the audit trail but is never offered to the model again, and the answers that relied on it are redrafted.
Look at 3.2: the conflict banner is gone, and my approval has been withdrawn, because the evidence changed under
it. It needs a fresh look."

**YOU SHOULD SEE:** the cards turn resolved; 3.2 is back to *drafted*, no conflict flag, Accept enabled.

## Scene 9: chat (7:20, 40 seconds)

**DO:** In the chat panel type: `summarise this RFP`, then `why was 3.4 flagged?`, then `accept 6.1`.

**SAY:** "I can also just ask. It answers from the sources and shows them. And it only changes something when I
name an answer. Here I tell it to accept 6.1, the pricing one, and it refuses, because there is nothing to
approve."

**YOU SHOULD SEE:** a summary; an explanation naming the missing ISO 22301 evidence; "no answer text to accept".

## Scene 10: export (8:00, 50 seconds)

**DO:** Click **Export**. Choose **Review copy** and download; then choose **Approved answers only**. If you
rehearsed it, set **Words per answer** to a small number to show the check, and click **Export anyway** to override.
Open the downloaded file.

**SAY:** "A review copy is marked DRAFT and every unapproved answer is labelled. The submission copy contains only
what I approved. It is the buyer's own workbook: their IDs, their C, PC and NC codes, the sources on every answer,
and a compliance matrix. If an answer breaks their word limit, it stops me until I override on purpose. 'Cannot
Confirm' never gets a compliance code, because that would claim something the sources don't."

**YOU SHOULD SEE:** a `_DRAFT` file name, the checks, the workbook with answers in place and extra sheets.

## Scene 11: the record (8:50, 30 seconds)

**DO:** Open **Audit Log**, then **Settings**.

**SAY:** "Every draft, edit, approval, flag and export is logged with who did it. And here is the quota panel: it
shows each model key by alias and its status, and never the key itself."

## Close (9:20, 40 seconds)

**SAY:** "To sum up. It drafts a whole RFP in minutes, and every answer comes with its evidence. In our test, 23
requirements took about two minutes. It is accurate because code, not a prompt, checks every sentence against its source. It keeps people in control:
nothing is final until a human approves it, and pricing, legal and commitments never reach the model. It is
built to be audited: every action is recorded. On our sample set it met 73 of 74 expectations, and on a separate
set of documents we hadn't tuned it on, it passed every guardrail check. It also found one real bug, which we
fixed. I'll be honest about the limits: no login yet, no scanned PDFs, and we have only used synthetic data."

## Optional: unseen documents (60 seconds, if you have time)

Load the set in `live_test/` instead, or show the result. **SAY:** "We also tested on a different product, bank and
customers that the system had never seen: 11 sources and a 23-requirement RFP. All 23 statuses were as expected,
no restricted source was cited, no customer name leaked. It also found one real defect, the buyer's compliance
codes were worded in a way we didn't recognise, so the export column came out empty. We fixed it and added a test."
Steps are in `live_test/GUIDE.md`.

## Level 2: capability classification (optional, about 4 minutes)

Use the Level 2 test set (`live_test/GUIDE_LEVEL2.md`): load it with `load_kb.py --level2`, and have the draft and assessment run
finished before you start. It asks bidders to classify every capability as Standard, Configuration, Development, Third party or
Not available.

**DO:** Show the import report, then open **Analysis**.

**SAY:** "This buyer wants every capability classified. The app read the codes from their own instructions, S, C, D, T and N, and which
ones need an explanation or an estimate. A code means what its label says, not its letter, so it works with whatever abbreviations
a customer uses."

**DO:** On the **Compliance matrix**, point at 1.1, 1.4, 2.3.

**SAY:** "Each requirement is broken into parts, and each part is classified from the cited text. The quote has to be in the source and the source has to say
it. Here a plain statement is Standard, an explicit exclusion is Not available, and this one needs a customer-specific extension, so it is Development,
with an estimate required."

**DO:** Open the **Gap analysis** tab. Point at 1.4, then at 3.3 and 4.2.

**SAY:** "This is the distinction that matters. 1.4 is a *confirmed* product gap: our documents say we do not do it. The ISO 22301 one and
the 5,000 users one are *knowledge* gaps: our documents are silent, so someone has to find out. They go to different people. And when the
evidence is unknown, no code is suggested at all."

**DO:** Use the *Unclear wording* filter. Open 2.4, then 3.4 and 3.5. Open **Clarifications**.

**SAY:** "It also reads the requirements themselves. 2.4 refers to an annexure we were never given. 4.3 asks for 'adequate' reporting, which cannot be
measured. And 3.4 wants data in Qatar while 3.5 wants it in the UAE: those cannot both be met, so they raise one question, not two, ranked by what the answer could change."

**DO:** In the Studio, select 2.3, open *Validate or correct*, type an estimate, click **Validate this code**.

**SAY:** "Where the evidence is incomplete, a person decides. I add the estimate and validate the code, in my name. An unvalidated code is never written into the file the customer receives."

**DO:** On **Analysis**, the *Review gate and checklist* tab. If you rehearsed it, edit 3.1 to add an unsupported sentence first.

**SAY:** "Before it goes to review, the gate checks the answers as they stand now. If someone added a claim no source supports, it stops and names the requirement. I can go on
only by writing down why. The checklist tracks what is outstanding, including the annexure to attach."

**YOU SHOULD SEE:** codes for 1.1, 1.4, 2.1, 2.3, 3.1; no code for 2.4, 3.3, 3.4, 3.5, 4.2; a confirmed gap count of 2 beside a knowledge gap count of 7; one question for 3.4 and 3.5.

## If something goes wrong

| Symptom | Likely cause | Do this |
|---|---|---|
| Drafting is slow | cache is cold, or free-tier rate limits | the banner says it is waiting for quota; keep talking, it continues by itself |
| "Quota exhausted ... expected back about HH:MM" | every provider limited | click **Resume** after the time shown; say the pause is the safety behaviour |
| Search mode says `keyword` | the embedding model is not downloaded | fine for the demo; fetch it with the script above |
| A figure differs from this script | a different model answered | the flags and structure are what matter; the answer text can vary |
| A settings key shows as `rejected` | a bad key | replace it in `.env`, restart, **Reset key state** |
| The backend refuses to start: "older version of the app" | an old database file | stop it, delete `data/rfp_ai.db*`, start it again |
| Everything fails | network or keys | walk through the screenshots the dry run saved in its `--out` folder (for example `/tmp/shots`), and say it is a recording of the real flow |

## Questions to expect

| Question | Honest answer |
|---|---|
| How do you stop hallucination? | A code validator, not a prompt. Every statement must name a real passage, state only figures its source states, and overlap it. What fails is removed and shown to the reviewer. |
| What if the model fabricates a quote? | Quotes are checked verbatim against the passage, and a citation always shows real source text. A separate audit re-checks saved answers; it found a bug of ours once. |
| How accurate is it? | 73 of 74 on our sample set, 72 of 74 against the original stricter expectations, 0 audit failures. One sample, our own data, a model that varies between runs; confidence is not calibrated. |
| Did you test on documents you didn't tune on? | Yes: a separate product with 11 sources and 23 requirements. All statuses as expected, 0 guardrail failures, and one real defect found and fixed. We wrote both the documents and the expectations, so it is not an independent test. |
| Why not hand the whole PDF to a model? | IDs, order and structure must be exact, and each answer needs its own evidence. A parser reads the structure; the model only drafts one requirement at a time. |
| Is this real Azentio data? | No. It is synthetic and labelled sample, as the brief requires. |
| What happens to customer names in past responses? | Redacted when the file is indexed. A document marked do-not-reuse is never retrieved. |
| What stops a hidden instruction in an RFP? | Bracketed notes aimed at an AI are removed before drafting and reported; the validator would also reject an unsupported claim. |
| Where do the models run, and what do they see? | Only four places call a model: drafting, the contradiction judge, clarification wording and chat answers. Keys stay in the backend's `.env` and the UI shows aliases only. Search embeddings run locally. |
| Can I add many documents at once? | Yes. Select several files in the Add source form; each has its own date, version and customer. |
| What does not work yet? | Scanned PDFs, Arabic RFPs, real login and roles, and a hosted worker queue. See section 8 of the architecture note. |

## Technical questions: models, chunking, retrieval, agents

Short spoken answers. The reasoning and numbers behind each are in `docs/architecture.md`, section 3, "Design
choices and why".

| Question | Say |
|---|---|
| Which models do you use, and why? | "Three providers, because the brief meant free tiers with daily limits. Groq first: gpt-oss-120b for drafting, quick and reliable JSON on the day we tested, Gemini is next in line, but it was erratic that day, so a second Groq model on its own quota and then OpenRouter's free models stand behind it. Smaller Groq models handle the short jobs." |
| Did you compare the models? Which is most accurate? | "Not head to head, and I won't claim one is best. We picked on speed, reliable JSON and quota, then measured the whole system. That works because correctness doesn't depend on the model alone: code removes anything the sources don't support, so a weaker model costs us completeness, more 'Cannot Confirm', not invented facts." |
| What if a model is down or the quota runs out? | "It rotates keys, then models, then providers. In our test run it hit five rate-limit cooldowns and three timeouts and still finished. If everything is spent, the run pauses and says when to resume. It never fills a gap with made-up text." |
| How do you chunk the documents? | "By the document's own structure: headings first, then sentence boundaries, up to 900 characters with one sentence of overlap. A short section stays whole. Each chunk keeps its heading and its page or section so a citation can point to it, and customer names are redacted before anything is stored." |
| Why one chunk per bullet? | "A list is a list of separate facts. If one item is stale or contradicts another document, it shouldn't taint an answer that cites a different item. Whole-list chunks gave us false conflicts in testing." |
| How does retrieval work? | "Hybrid. Keyword search for exact things like ISO 27001 or AES-256, plus local embeddings for paraphrase, merged by rank. A glossary adds Islamic-finance synonyms. The top six passages go to the model. Not reusable, internal and superseded sources are filtered out inside the query, so they can't slip through." |
| Why hybrid, and does it help? | "Yes, measured. All 58 requirement-to-source checks are found in the top six. On paraphrased questions hybrid found 12 of 12 against 11 for keyword alone, and on ten held-out questions 10 against 9." |
| Why top six? | "It is a setting. Six covers the requirements that need two or three sources and keeps a prompt around 1,800 tokens. We measured recall at six; we did not tune it further." |
| Why no vector database or LangChain? | "At this size, a few thousand passages, SQLite plus a matrix multiply is enough, and fewer moving parts is easier to audit. For production the plan is Postgres with pgvector." |
| How many agents do you have? | "One. It's a fixed pipeline of eight steps, not a multi-agent system. Four places can call a model: the drafter, the contradiction judge, the clarification writer and the chat answerer. No model plans, picks tools or decides what to retrieve." |
| Why not a multi-agent design? | "Every guardrail has to be code to be auditable, and a model that steers the process can be steered by text inside an RFP. A fixed order is also reproducible, and every extra call spends free-tier quota." |
| How is confidence computed? | "From checks, not from the model's own opinion: retrieval strength, how many quoted claims and sentences check out, and source freshness, then reduced for conflicts, removed statements, missing evidence or injected instructions. It is shown with its breakdown. It is not calibrated against outcomes." |
| How big is a prompt and how fast is a draft? | "About 1,800 tokens in and 500 out, around 1.6 seconds a call. Three requirements run at once, so a 23-requirement RFP took about two minutes end to end in our test." |
| How do you find the contradictions? | "At index time, not at answer time. Pairs of passages that are similar and share a kind of fact, such as a duration, a standard or a certificate, go to a model that judges each pair once. A person then decides which document is right." |
| How do you choose the response code? | "From the cited text, part by part. A part only counts if its quote is in the source and the source says it. The least favourable evidenced part decides. If any part is unproven, no code is suggested, and a person validates anything incomplete." |
| What if the customer uses different codes? | "Codes are read from the RFP, and a code means what its label says. If a label cannot be placed, it stays unmapped for the Sales Engineer. Nothing assumes S, C, D, T, N." |
| What is the difference between a confirmed gap and a knowledge gap? | "A confirmed gap is when our documents say the product does not do it. A knowledge gap is when they are silent. The first goes to Product Management; the second to whoever can find out. We never call silence a gap in the product." |
| How do you catch contradictions between requirements? | "By rule for the common ones, a required location or a required figure that differs, and by a model for the rest. The model noticed a planted one in one run and missed it in another, which is why the common ones are rules." |
| Can the agent overwrite an approved answer? | "No. Redrafting what changed leaves approved answers exactly as they are and marks them for a re-check. Approving again clears the mark." |
| What does Level 2 not do? | "The estimates and attachments go into the comments column, not dedicated columns. Ambiguity found by the model is noisy, so questions need approval. It was tested on Excel and on one synthetic RFP." |

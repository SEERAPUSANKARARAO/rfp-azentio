# Level 2 live test guide

An RFP that asks bidders to **classify every capability** as Standard, Configuration, Development, Third party or Not
available, run against the Level 1 live-test knowledge base plus one extra document. Use it to check, in the browser and
with the real models, that the app suggests codes only on evidence, separates a confirmed gap from a knowledge gap,
catches unclear and contradicting requirements, and keeps a person in control.

| | |
|---|---|
| RFP | `live_test/rfp/BWIF_L2-2026-044_Capability_Classification.xlsx`: 18 requirements, 17 mandatory |
| Knowledge base | the 11 Level 1 documents plus `live_test/kb_level2/slc_extensions_2026.md` (12 sources) |
| Time | about 20 minutes; drafting and assessment take about 3 |

Everything is synthetic.

## 0. Load the knowledge base

Start with an **empty** knowledge base (do not load the sample set). One command loads all 12 and runs conflict detection:

```bash
cd backend && .venv/bin/python ../live_test/load_kb.py --level2
```

Or add the files by hand as in `GUIDE.md`, plus the extra one: `kb_level2/slc_extensions_2026.md`, kind *product doc*, date
2026-09-10, version 6.2.

## 1. Import the RFP

Active RFPs, then drop in the workbook. The import report should show:

| Panel | You should see |
|---|---|
| Requirements | **18**, 17 mandatory |
| Formatting rules | 80 words; file name `<VendorName>_BWIF-L2-2026-044.xlsx`; questions by 29 Jan 2027; due 12 Feb 2027 |
| **Response codes** | `S Standard capability`, `C Configuration required (needs explanation)`, `D Development required (needs explanation, estimate)`, `T Third-party dependency`, `N Not available` |

## 2. Draft

**Start drafting.** When it finishes the banner reads "... Assessed 17 requirement(s)". The assessment runs by itself after
drafting. Requirement 5.1 (pricing) goes to its owner and is not assessed.

## 3. Read the Analysis page

Open **Analysis** from the Studio header, then the **Compliance matrix** tab.

"Must" is a guardrail: a failure there is a defect. The last column is what happened in my run on 31 October 2026; a different
run may word things differently or land on a neighbouring class.

| Req | What it tests | Must | My run |
|---|---|---|---|
| 1.1 | plain standard capability | code S | Standard, **S** |
| 1.2 | configurable per product | code C, "needs an explanation" | Configuration, **C** |
| 1.3 | calculates a rebate (standard) but cannot apply it without manual approval | the least favourable part decides; a confirmed gap | Not available, **N**, confirmed gap, Product Management |
| 1.4 | explicit exclusion (Tawarruq) | **N**, a confirmed gap | Not available, **N**, confirmed gap |
| 2.1 | depends on the bank's Takaful operator | **T**, integration dependency, Solution Architecture | Third party, **T** |
| 2.2 | credit bureau and AML through adapters | **C** or **T** | Configuration, **C** |
| 2.3 | needs a customer-specific extension | **D**, "needs an explanation and an estimate", Engineering | Development, **D**, needs a person to validate |
| 2.4 | refers to **Annexure B**, which we do not have | **no code**; a knowledge gap; flagged incomplete | Not evidenced, no code |
| 3.1 | SSO and MFA | **S** | Standard, **S** |
| 3.2 | old and new sources disagree on encryption | **S** from the current source; stale or conflicting noted; needs a person | Standard, **S**, documentation and security gaps |
| 3.3 | ISO 22301: no approved source | **no code**; knowledge gap, not a confirmed gap | Not evidenced, no code |
| 3.4 and 3.5 | hosted in **Qatar** against hosted in the **UAE** | both flagged as contradicting each other; **one** clarification question for the pair | found by rule, one question |
| 4.1 | AI scoring: only rule-based scorecards | no code | Not evidenced, no code |
| 4.2 | 5,000 concurrent users: no performance evidence | no code; a knowledge gap; clarification | Not evidenced, no code |
| 4.3 | "adequate reporting ... as required" | flagged **ambiguous** (use the *Unclear wording* filter) | Configuration, **C**, ambiguous |
| 4.4 | "exactly as in requirement 1.2" | shows a dependency on 1.2 | depends on 1.2 |
| 5.1 | pricing | owner, never classified | owner |

Click a row to see why. The filters (*Needs validation*, *Has a gap*, *Unclear wording*, *Spans components*) narrow the list.

**Gap analysis tab:** a *Confirmed product gap* count (1.3, 1.4) that is separate from *Knowledge gap*; a "who should look" list;
security concerns for 3.2, 3.3, 3.4, 3.5.

## 4. Validate, correct, complete

In the **Response Studio**, select **2.3**. The drawer shows *Capability and response code*.

1. Open **Validate or correct**, type `15 person-days` in the effort estimate, click **Validate this code**. The drawer now
   says "validated by <you>". The Audit Log records it.
2. Change **Capability** to *Configuration* and **Save**. The code becomes C, the gap disappears, and the drawer shows
   "Set by the reviewer". The system never overwrites a person's choice on its own.

## 5. Clarification questions

Open **Clarifications**. They are ranked **high, medium, low**, each with what it could change and an owner:

- 2.4 asks for **Annexure B** (high).
- 3.4 and 3.5 share **one** question about the hosting country.
- 4.3 asks what "adequate" and "as required" mean.

Type a customer reply into one question and click **Record the reply**. Its status becomes *answered*. Then, on the Analysis page, click
**Redraft what changed**: the affected answers are redrafted with the reply as part of the requirement, and **approved answers are
left exactly as they are**, marked for a re-check.

## 6. The review gate and the checklist

On **Analysis**, the *Review gate and checklist* tab lists **Fix before review**, **Worth a look** and **For information**.

To see the gate work: on 3.1, **Edit** the answer and add the sentence *"It also holds a PCI DSS Level 1 attestation issued in
2025."* Reload the gate. It now blocks, naming 3.1, because no cited source supports that sentence. **Proceed to review** then
asks for a written reason, which is recorded.

The checklist includes **Annexure A: attach a copy of your ISO/IEC 27001 certificate**. Tick it when prepared.

## 7. Export

**Export**, then:

- the **Response codes the RFP asks for** panel lists the five codes; fix a label or tick which codes need an estimate, and **Confirm rules**
- choose **Review copy** and open the workbook. In the code column, `S` appears for 1.1 and 3.1, but a code that **needs a person to validate it
  (for example 1.3 or 3.2) is left blank** and the Comments cell says "Suggested response code N is waiting for validation"
- the extra sheets: **Compliance Matrix** (capability, code state, needs, gaps, owner), **Gap Analysis**, **Clarifications** (ranked) and
  **Submission Checklist**

## 8. How to judge it

**Pass or fail, any one failing is a defect:**

1. No requirement whose evidence is unknown has a code (2.4, 3.3, 3.4, 3.5, 4.1, 4.2).
2. 1.4 is a *confirmed* gap and 2.4, 3.3 and 4.2 are *knowledge* gaps, never the other way round.
3. Every quote shown under a capability part appears word for word in its source.
4. 3.4 and 3.5 are flagged as contradicting each other, with one question for both.
5. 2.4 is flagged as referring to a document we do not have.
6. A code that needs validation is never written into the exported file.
7. The gate blocks an edit that adds an unsupported claim, and recording a reason is required to go on.
8. *Redraft what changed* leaves an approved answer's text untouched.

**Judge, don't fail:** exact classes where the evidence allows two readings (2.2, 4.3), confidence numbers and wording.

## What I found when I ran this

On 31 October 2026, with the real models: capability as expected on **17 of 17** assessed requirements and all **10** planted gaps found. The browser
check (12 steps, fields typed key by key) passed 12 of 12.

The first run was weaker (14 of 16, and 7 of 10 gaps) and exposed four weaknesses in my own checks, now fixed with tests: a "standard" check
that rejected plain statements of behaviour, a "not available" check that missed "must be approved manually", an optimistic code for a
requirement with an unevidenced part, and a code for a Cannot Confirm answer. A planted contradiction was also caught by the model in one run and missed in another, so
it is now found by rule.

**What this does not prove.** I wrote the documents and the expectations, and I changed rules after seeing the first run, so it is not an
independent test. The model varies between runs, and part of the scoring replayed cached model answers. Model-found ambiguity is the
noisiest part (it asked what "charity" meant). Estimates and attachments go into the comments column, not dedicated columns.

## Reset

Stop the backend, delete `data/rfp_ai.db*`, start it again. To regenerate the files:
`cd backend && .venv/bin/python ../live_test/make_level2.py`.

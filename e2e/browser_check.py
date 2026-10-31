"""Drive the real UI in a real browser through the whole story, and fail loudly if anything is off.

    # servers running: backend on :8000, frontend (vite) on :5173
    cd rfp-ai/backend
    .venv/bin/python ../e2e/browser_check.py --out /tmp/shots
    .venv/bin/python ../e2e/browser_check.py --out /tmp/shots --headed     # watch it

Needs: pip install playwright, and Google Chrome (or set --chrome). Uses your model keys; with a warm response
cache the drafting steps cost nothing. Every step prints PASS or FAIL, a screenshot is saved per screen, and any
browser console error fails the run.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import openpyxl
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
RESULTS: list[tuple[str, bool, str]] = []
LONG = 240_000


def step(name: str):
    def deco(fn):
        def run(*a, **k):
            try:
                fn(*a, **k)
                RESULTS.append((name, True, ""))
                print(f"PASS  {name}", flush=True)
            except Exception as e:  # noqa: BLE001  one failed step must not hide the rest
                import traceback
                here = [f for f in traceback.extract_tb(e.__traceback__) if f.filename.endswith("browser_check.py")]
                where = f"line {here[-1].lineno}: {here[-1].line}" if here else ""
                detail = " ".join(str(e).split())[:420]
                RESULTS.append((name, False, detail))
                print(f"FAIL  {name}\n      {where}\n      {detail}", flush=True)
                try:
                    a[0].screenshot(path=str(OUT / f"FAIL_{re.sub(r'[^a-z0-9]+', '_', name.lower())}.png"))
                except Exception:  # noqa: BLE001
                    pass
        return run
    return deco


def shot(page: Page, name: str):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)


def toast(page: Page, text: str | re.Pattern, timeout: int = 20_000):
    expect(page.get_by_role("status").filter(has_text=text).first).to_be_visible(timeout=timeout)


def pick(page: Page, req_id: str):
    page.locator('section[aria-label="Question queue"] li button').filter(has_text=re.compile(rf"^\s*{re.escape(req_id)}(?![\d.])")).first.click()
    expect(page.locator('section[aria-label="Answer"]').get_by_text(req_id, exact=True).first).to_be_visible()


# ----------------------------------------------------------------------------------------------- steps
@step("Home page loads")
def home(page: Page, base: str):
    page.goto(base)
    expect(page.get_by_role("heading", name="Active RFPs")).to_be_visible()
    shot(page, "01_home")


@step("Knowledge base: load the sample set, stale sources are marked")
def kb_load(page: Page, base: str):
    page.goto(f"{base}/knowledge")
    page.get_by_role("button", name="Load sample knowledge base").click()
    expect(page.get_by_text("Disaster Recovery and Business Continuity Policy v3")).to_be_visible(timeout=60_000)
    expect(page.get_by_text("stale", exact=True).first).to_be_visible()
    assert page.locator("tbody tr").count() == 25, "all 25 sample sources should be listed"
    expect(page.get_by_text("redacted").first).to_be_visible()  # past RFPs and SOWs show customer redaction
    shot(page, "02_knowledge")


@step("Knowledge base: conflict detection finds the planted contradictions")
def kb_conflicts(page: Page, base: str):
    page.get_by_role("button", name="Detect conflicts").click()
    expect(page.get_by_text("needs a decision").first).to_be_visible(timeout=LONG)
    n = page.get_by_text("needs a decision").count()
    assert n >= 4, f"expected at least the 4 planted conflicts, saw {n}"
    expect(page.get_by_text("RTO", exact=False).first).to_be_visible()
    shot(page, "03_conflicts")


@step("Upload an RFP: structure, rules and warnings are shown before drafting")
def upload(page: Page, base: str, rfp: Path):
    page.goto(base)
    page.set_input_files('input[type="file"]', str(rfp))
    expect(page.get_by_text(f"I read {rfp.name}")).to_be_visible(timeout=60_000)
    panel = page.locator('section[aria-label="RFP analysis"]')
    expect(panel.get_by_text("30", exact=True).first).to_be_visible()
    expect(panel.get_by_text("120").first).to_be_visible()  # word limit detected
    expect(panel.get_by_text("C / PC / NC")).to_be_visible()
    shot(page, "04_analysis")


@step("Start drafting: progress is shown and every requirement gets an answer")
def draft(page: Page):
    page.get_by_role("button", name="Start drafting").click()
    page.wait_for_url(re.compile(r"/rfps/\d+$"))
    expect(page.get_by_text(re.compile(r"30/30 drafted"))).to_be_visible(timeout=LONG)
    expect(page.locator('section[aria-label="Question queue"] li').first).to_be_visible()
    shot(page, "05_studio")


@step("Conflicting RTO: current answer, conflict banner, clickable citation")
def rto(page: Page):
    pick(page, "3.2")
    ans = page.locator('section[aria-label="Answer"]')
    expect(ans.get_by_text("1 hour").first).to_be_visible()
    expect(ans.get_by_text("4 hours")).to_have_count(0)  # the superseded figure is not in the answer
    ev = page.locator('aside[aria-label="Evidence and audit"]')
    expect(ev.get_by_text("Sources contradict each other")).to_be_visible()
    ans.get_by_role("button", name=re.compile(r"^Source 1")).first.click()
    expect(ev.locator("button[aria-expanded=true]")).to_have_count(1)
    expect(ev.get_by_text("Computed from checks")).to_be_visible()
    shot(page, "06_conflict_answer")


@step("Approve 3.2 (it will matter when the conflict is resolved)")
def approve_rto(page: Page):
    page.get_by_role("button", name=re.compile("Accept & next")).click()
    toast(page, "3.2 approved")


@step("Missing evidence: ISO 22301 is a clarification, not an answer")
def iso22301(page: Page):
    pick(page, "3.4")
    ans = page.locator('section[aria-label="Answer"]')
    expect(ans.get_by_text("No approved source in the knowledge base confirms: ISO 22301.")).to_be_visible()
    expect(page.locator('aside[aria-label="Evidence and audit"]').get_by_text("Evidence is missing")).to_be_visible()
    page.get_by_role("tab", name=re.compile("^Clarify")).click()
    expect(page.locator('section[aria-label="Question queue"] li').filter(has_text=re.compile(r"^\s*3\.4\b"))).to_be_visible()
    page.get_by_role("tab", name=re.compile("^All")).click()


@step("Expired certificate: ISO 27001 is not claimed as current")
def iso27001(page: Page):
    pick(page, "3.3")
    ans = page.locator('section[aria-label="Answer"]')
    expect(ans.get_by_text("expired", exact=False).first).to_be_visible()
    expect(ans.get_by_text("Not Complied").first).to_be_visible()


@step("Pricing is routed to its owner and cannot be approved")
def pricing(page: Page):
    pick(page, "6.1")
    expect(page.locator('aside[aria-label="Evidence and audit"]').get_by_text("Routed to Commercial Desk")).to_be_visible()
    expect(page.get_by_role("button", name=re.compile("Accept & next"))).to_be_disabled()
    expect(page.get_by_text("Never drafted automatically")).to_be_visible()


@step("Accept & next: approval is recorded and the queue moves on")
def accept(page: Page):
    pick(page, "1.1")
    page.get_by_role("button", name=re.compile("Accept & next")).click()
    toast(page, "1.1 approved")
    expect(page.get_by_text("30/30 drafted · 2 approved")).to_be_visible()


@step("Keyboard: Ctrl+Enter approves the selected answer")
def keyboard(page: Page):
    pick(page, "1.2")
    page.locator("h1").click()  # take focus off every control
    page.keyboard.press("Control+Enter")
    toast(page, "1.2 approved")


@step("Edit an answer: saved, marked edited, word count shown")
def edit(page: Page):
    pick(page, "1.4")
    page.get_by_role("button", name=re.compile(r"^Edit$")).click()
    box = page.get_by_role("textbox", name="Edit answer")
    box.fill(box.input_value() + " Reviewed by the Sales Engineer.")
    expect(page.get_by_text(re.compile(r"\d+ / 120 words"))).to_be_visible()
    page.get_by_role("button", name="Save edit").click()
    expect(page.get_by_text("Edited", exact=True)).to_be_visible()
    expect(page.get_by_text("Reviewed by the Sales Engineer.")).to_be_visible()


@step("An answer over the RFP's word limit is called out and blocks submission export")
def over_limit(page: Page):
    pick(page, "1.6")
    page.get_by_role("button", name=re.compile(r"^Edit$")).click()
    page.get_by_role("textbox", name="Edit answer").fill(" ".join(["word"] * 130))
    expect(page.get_by_text(re.compile(r"10 over the RFP's limit"))).to_be_visible()
    page.get_by_role("button", name="Save edit").click()
    page.get_by_role("button", name=re.compile("Accept & next")).click()
    toast(page, "1.6 approved")


@step("Flag an answer for an SME")
def flag(page: Page):
    pick(page, "3.8")
    page.get_by_role("button", name=re.compile("Flag for")).click()
    dlg = page.get_by_role("dialog")
    dlg.get_by_label("Who should look at this?").select_option("SME")
    note = "Please confirm the UAE region date."
    box = dlg.get_by_label("Note for them")
    box.click()
    box.press_sequentially(note, delay=15)  # key by key: fill() would hide a dialog that steals focus after each key
    assert box.input_value() == note, f"dialog kept {box.input_value()!r}: it loses focus while typing"
    dlg.get_by_role("button", name="Flag", exact=True).click()
    expect(page.locator('aside[aria-label="Evidence and audit"]').get_by_text("Handed to SME")).to_be_visible()


@step("Add a fact the sources lack: stored as an authored SE-provided source")
def fact(page: Page):
    pick(page, "4.4")
    page.get_by_role("button", name="Add fact").click()
    dlg = page.get_by_role("dialog")
    fact_text = "The platform includes a rules-based credit scoring module for retail lending, not machine learning."
    box = dlg.get_by_label("The fact (one or two plain sentences)")
    box.click()
    box.press_sequentially(fact_text, delay=15)  # key by key, as above
    assert box.input_value() == fact_text, f"dialog kept {box.input_value()!r}: it loses focus while typing"
    dlg.get_by_role("button", name="Save fact").click()
    toast(page, re.compile("Fact saved as an SE-provided source"), 120_000)


@step("Chat: summary, explanation, and a refusal to change anything without a named answer")
def chat(page: Page):
    pick(page, "3.4")
    page.get_by_role("button", name=re.compile("Ask RFP AI")).first.click()
    box = page.get_by_label("Message")

    box.fill("summarise this RFP")
    box.press("Enter")
    log = page.get_by_role("log")
    expect(log.get_by_text("30 requirements")).to_be_visible(timeout=30_000)
    expect(log.get_by_text("120 words per answer")).to_be_visible()

    box.fill("why?")
    box.press("Enter")
    expect(log.get_by_text("ISO 22301").first).to_be_visible(timeout=30_000)

    page.locator('section[aria-label="Question queue"] li button').first.click()
    box.fill("accept 6.1")
    box.press("Enter")
    expect(log.get_by_text("no answer text")).to_be_visible(timeout=30_000)
    shot(page, "07_chat")


@step("Chat history survives a reload (session resume)")
def resume(page: Page):
    page.reload()
    page.get_by_role("button", name=re.compile("Ask RFP AI")).first.click()
    expect(page.get_by_role("log").get_by_text("30 requirements")).to_be_visible(timeout=30_000)


@step("Export: checks are shown; a review copy is clearly a DRAFT")
def export_draft(page: Page, out: Path):
    page.get_by_role("button", name="Export", exact=True).click()
    dlg = page.get_by_role("dialog")
    expect(dlg.get_by_text(re.compile(r"mandatory requirement\(s\) have no approved answer"))).to_be_visible()
    expect(dlg.get_by_text(re.compile(r"exceed the RFP's 120-word limit"))).to_be_visible()
    expect(dlg.get_by_role("button", name="Download response")).to_be_enabled()
    shot(page, "08_export")
    dlg.get_by_label(re.compile("Review copy")).check()
    with page.expect_download() as dl:
        dlg.get_by_role("button", name="Download review copy").click()
    f = out / dl.value.suggested_filename
    dl.value.save_as(f)
    assert f.name == "Vendor_RFP-ASIB-2026-017_DRAFT.xlsx", f.name
    ws = openpyxl.load_workbook(f)["Requirements"]
    rows = {str(r[0].value): r for r in ws.iter_rows(min_row=6) if r[0].value and not str(r[0].value).startswith("Section")}
    assert len(rows) == 30 and list(rows)[:2] == ["1.1", "1.2"]
    assert str(rows["1.5"][3].value or "").startswith("[DRAFT - not approved]"), "an unapproved answer must be marked"
    assert not str(rows["1.1"][3].value).startswith("[DRAFT"), "an approved answer must not be marked"


@step("Export: a word-limit breach blocks the submission copy until the SE overrides")
def export_blocked(page: Page, out: Path):
    dlg = page.get_by_role("dialog")
    dlg.get_by_label(re.compile("Approved answers only")).check()
    dlg.get_by_role("button", name="Download response").click()
    expect(dlg.get_by_text("This breaks a rule the RFP stated.")).to_be_visible()
    with page.expect_download() as dl:
        dlg.get_by_role("button", name="Export anyway").click()
    f = out / dl.value.suggested_filename
    dl.value.save_as(f)
    assert f.name == "Vendor_RFP-ASIB-2026-017.xlsx", f.name
    ws = openpyxl.load_workbook(f)["Requirements"]
    rows = {str(r[0].value): r for r in ws.iter_rows(min_row=6) if r[0].value and not str(r[0].value).startswith("Section")}
    assert rows["1.1"][4].value == "C" and str(rows["1.1"][5].value).startswith("Sources: [1] ")
    assert rows["1.5"][3].value is None, "an unapproved answer must be blank in the submission copy"
    page.keyboard.press("Escape")


@step("Clarifications page: approve, add, and the list persists")
def clarifications(page: Page, base: str):
    page.get_by_role("link", name=re.compile("to clarify")).click()
    expect(page.get_by_role("heading", name="Clarification questions")).to_be_visible()
    page.get_by_role("button", name=re.compile("Approve for the customer")).first.click()
    expect(page.get_by_text("approved", exact=True).first).to_be_visible()
    page.get_by_label("New question").fill("Is UAE data residency required on day one?")
    page.get_by_role("button", name=re.compile("Add question")).click()
    expect(page.get_by_role("textbox").filter(has_text="Is UAE data residency required on day one?")).to_be_visible()
    shot(page, "09_clarifications")


@step("Resolve all the RTO conflicts back to back: stale passages retired, recalculations merge, nothing is lost")
def resolve(page: Page, base: str):
    page.goto(f"{base}/knowledge")
    open_dr = lambda: page.locator("li.card").filter(has_text="needs a decision").filter(has_text=re.compile("disaster", re.I))
    expect(open_dr().first).to_be_visible(timeout=30_000)  # the list loads asynchronously: wait before counting
    n = open_dr().count()
    assert n >= 3, f"expected several related RTO conflicts, saw {n}"
    for _ in range(n):
        before = open_dr().count()
        open_dr().first.get_by_role("button", name=re.compile(r"^Mark B canonical")).click()
        toast(page, re.compile(r"Resolved\."), 30_000)
        expect(open_dr()).to_have_count(before - 1, timeout=30_000)
    expect(page.get_by_text("resolved", exact=True).first).to_be_visible()
    shot(page, "10_resolved")


@step("After resolution 3.2 is redrafted, its conflict flag is gone and it needs a fresh approval")
def after_resolve(page: Page, base: str):
    page.get_by_role("link", name="Response Studio").click()
    expect(page.get_by_text(re.compile(r"30/30 drafted"))).to_be_visible(timeout=60_000)
    pick(page, "3.2")
    ev = page.locator('aside[aria-label="Evidence and audit"]')
    deadline = time.time() + 240
    while time.time() < deadline:  # the redrafts run in the background; wait for the job banner to clear
        if page.get_by_role("status").filter(has_text=re.compile(r"Drafting|Recalculating|Redrafting")).count() == 0 \
                and ev.get_by_text("Sources contradict each other").count() == 0:
            break
        page.wait_for_timeout(2000)
    expect(ev.get_by_text("Sources contradict each other")).to_have_count(0)
    expect(page.get_by_role("button", name=re.compile("Accept & next"))).to_be_enabled()  # the approval was withdrawn
    expect(page.locator('section[aria-label="Answer"]').get_by_text("1 hour").first).to_be_visible()
    shot(page, "11_after_resolve")


@step("Audit log shows who did what")
def audit(page: Page, base: str):
    page.goto(f"{base}/audit")
    for word in ("accepted", "edited", "flagged", "exported", "conflict resolved", "se fact added"):
        expect(page.get_by_role("cell", name=word).first).to_be_visible()
    expect(page.get_by_role("cell", name="se", exact=True).first).to_be_visible()
    shot(page, "12_audit")


@step("Settings: quota panel shows aliases and statuses, never keys")
def settings(page: Page, base: str):
    page.goto(f"{base}/settings")
    expect(page.get_by_text("groq#1")).to_be_visible(timeout=15_000)
    expect(page.get_by_text("gemini#3")).to_be_visible()
    body = page.inner_text("body")
    assert not re.search(r"gsk_|sk-or-|AQ\.Ab", body), "a key must never appear on screen"
    shot(page, "13_settings")


@step("Narrow screen: panels become tabs and nothing overflows sideways")
def mobile(page: Page, base: str):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(page.url if "/rfps/" in page.url else base)
    page.get_by_role("link", name="Response Studio").click()
    expect(page.get_by_role("tab", name="queue")).to_be_visible()
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 1, f"page scrolls sideways by {overflow}px"
    shot(page, "14_mobile")
    page.set_viewport_size({"width": 1500, "height": 950})


CONTRAST_JS = """() => {
  const lum = (r,g,b) => { const f = c => { c/=255; return c<=0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055,2.4) }; return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b) };
  const parse = s => { const m = s.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(',').map(parseFloat); return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1} };
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0.5) return c } return {r:255,g:255,b:255,a:1} };
  const bad = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
  while ((n = w.nextNode())) {
    const text = n.textContent.trim(); if (!text) continue;
    const el = n.parentElement, cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height || r.bottom < 0 || r.top > innerHeight) continue;
    if (el.closest('.sr-only')) continue;
    const fg = parse(cs.color); if (!fg) continue;
    const a = lum(fg.r,fg.g,fg.b), b = lum(...Object.values(bgOf(el)).slice(0,3));
    const ratio = (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05), size = parseFloat(cs.fontSize);
    if (ratio < (size >= 18 ? 3 : 4.5)) bad.push(text.slice(0,28) + ' ' + ratio.toFixed(2));
  }
  return bad;
}"""


def set_theme(page: Page, want: str):
    btn = page.get_by_role("button", name=re.compile("^Theme"))
    for _ in range(4):
        if (page.evaluate("document.documentElement.getAttribute('data-theme')") or "system") == want:
            break
        btn.click()
    assert (page.evaluate("document.documentElement.getAttribute('data-theme')") or "system") == want
    page.wait_for_timeout(600)  # colours transition; measure the settled state


@step("Light theme: text contrast meets WCAG AA on the Studio")
def contrast_light(page: Page):
    set_theme(page, "light")
    pick(page, "3.2")
    bad = page.evaluate(CONTRAST_JS)
    assert not bad, f"{len(bad)} low-contrast text runs, e.g. {bad[:6]}"


@step("Dark theme applies and text contrast meets WCAG AA")
def dark(page: Page):
    set_theme(page, "dark")
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg != "rgb(244, 246, 251)", f"body background did not change: {bg}"
    shot(page, "15_dark")
    bad = page.evaluate(CONTRAST_JS)
    assert not bad, f"{len(bad)} low-contrast text runs, e.g. {bad[:6]}"


@step("Dark theme: Knowledge Base and Settings stay readable too")
def dark_pages(page: Page, base: str):
    for path in ("/knowledge", "/settings", "/audit"):
        page.goto(f"{base}{path}")
        page.wait_for_timeout(700)
        bad = page.evaluate(CONTRAST_JS)
        assert not bad, f"{path}: {len(bad)} low-contrast text runs, e.g. {bad[:6]}"


@step("Every button has an accessible name")
def a11y(page: Page):
    bad = page.evaluate("""() => [...document.querySelectorAll('button,[role=button],a')].filter(el => {
        const n = (el.getAttribute('aria-label') || el.textContent || '').trim(); return !n && !el.querySelector('img[alt]'); })
        .map(el => el.outerHTML.slice(0, 80))""")
    assert not bad, f"unnamed controls: {bad[:3]}"


def main() -> int:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5173")
    ap.add_argument("--out", default="e2e_out")
    ap.add_argument("--rfp", default=str(ROOT / "sample_data" / "rfps" / "ASIB_RFP-2026-017_Core_Islamic_Banking.xlsx"))
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    OUT = Path(a.out)
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=a.chrome, headless=not a.headed)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950}, accept_downloads=True)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(f"uncaught: {e}"))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("response", lambda r: r.status >= 400 and r.status != 409 and errors.append(f"HTTP {r.status} {r.request.method} {r.url}"))
        page.set_default_timeout(20_000)

        home(page, a.base)
        kb_load(page, a.base)
        kb_conflicts(page, a.base)
        upload(page, a.base, Path(a.rfp))
        draft(page)
        rto(page)
        approve_rto(page)
        iso22301(page)
        iso27001(page)
        pricing(page)
        accept(page)
        keyboard(page)
        edit(page)
        over_limit(page)
        flag(page)
        fact(page)
        chat(page)
        resume(page)
        export_draft(page, OUT)
        export_blocked(page, OUT)
        clarifications(page, a.base)
        resolve(page, a.base)
        after_resolve(page, a.base)
        audit(page, a.base)
        settings(page, a.base)
        mobile(page, a.base)
        contrast_light(page)
        dark(page)
        dark_pages(page, a.base)
        a11y(page)

        real = [e for e in errors if "Failed to load resource" not in e]  # the response listener names the URL instead
        RESULTS.append(("No browser console errors", not real, "; ".join(real[:3])[:300]))
        print(("PASS  " if not real else "FAIL  ") + "No browser console errors" + ("" if not real else f"\n      {real[:3]}"))
        browser.close()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed. Screenshots in {OUT}")
    return 1 if failed else 0


OUT = Path("e2e_out")

if __name__ == "__main__":
    sys.exit(main())

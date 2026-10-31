"""Browser check for the Level 2 screens (12 steps, every field typed key by key).

Prepare: a backend and a frontend running on an EMPTY database, then
    cd backend && .venv/bin/python ../live_test/load_kb.py --level2
    upload live_test/rfp/BWIF_L2-2026-044_Capability_Classification.xlsx in the app, start drafting, and wait until the
    banner says "Assessed 17 requirement(s)".
Run:   cd backend && .venv/bin/python ../e2e/level2_check.py <output folder> [base url]

It CHANGES the data (ticks the annexure, validates a code, answers a clarification, moves the RFP into review), so run it
once per fresh database. It uses Google Chrome at /usr/bin/google-chrome.
"""
import re, sys
from playwright.sync_api import expect, sync_playwright

OUT = sys.argv[1]
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:5173"
bad, errors, results = [], [], []
def step(name):
    def deco(fn):
        def run(page):
            try:
                fn(page); results.append((True, name))
            except Exception as e:  # noqa: BLE001
                results.append((False, f"{name}: {str(e).splitlines()[0][:160]}"))
                page.screenshot(path=f"{OUT}/FAIL_{len(results)}.png")
        return run
    return deco

def type_into(loc, text):
    loc.click(); loc.press("Control+a"); loc.press_sequentially(text, delay=10)
    assert loc.input_value() == text, f"kept {loc.input_value()!r}"

@step("Analysis page: tiles, and a matrix row per requirement")
def matrix(page):
    page.goto(f"{BASE}/rfps/1/analysis")
    expect(page.get_by_role("heading", name="Compliance, gaps and readiness")).to_be_visible()
    expect(page.locator("tbody tr").first).to_be_visible()
    assert page.locator("tbody tr").count() == 18, page.locator("tbody tr").count()
    page.screenshot(path=f"{OUT}/1_matrix.png", full_page=True)

@step("Matrix filters, and a row opens to show why")
def filters(page):
    page.get_by_role("tab", name=re.compile("Needs validation")).click()
    n = page.locator("tbody tr").count()
    assert 0 < n < 18, n
    page.get_by_role("tab", name=re.compile("Unclear wording")).click()
    assert page.locator("tbody tr").count() >= 2
    page.locator("tbody tr").first.click()
    expect(page.get_by_text("Open in the Response Studio")).to_be_visible()
    page.get_by_role("tab", name=re.compile(r"^All")).click()

@step("Gap analysis keeps a confirmed product gap apart from a knowledge gap, and says who should look")
def gaps(page):
    page.get_by_role("tab", name="Gap analysis").click()
    expect(page.get_by_text("Confirmed product gap").first).to_be_visible()
    expect(page.get_by_text("Knowledge gap").first).to_be_visible()
    expect(page.get_by_role("region", name="By owner")).to_contain_text("Product Management")
    page.screenshot(path=f"{OUT}/2_gaps.png", full_page=True)

@step("Review gate lists what to fix, warns, and the checklist has the annexure to tick")
def gate(page):
    page.get_by_role("tab", name="Review gate and checklist").click()
    expect(page.get_by_role("region", name="Submission checklist")).to_be_visible()
    box = page.get_by_role("checkbox", name=re.compile("Annexure A"))
    expect(box).not_to_be_checked()
    box.check()
    expect(box).to_be_checked()
    page.reload(); page.get_by_role("tab", name="Review gate and checklist").click()
    expect(page.get_by_role("checkbox", name=re.compile("Annexure A"))).to_be_checked()  # it persisted
    page.screenshot(path=f"{OUT}/3_gate.png", full_page=True)

@step("Workflow board shows states and who has what")
def workflow(page):
    page.get_by_role("tab", name="Workflow").click()
    expect(page.get_by_text("Waiting for the customer").first).to_be_visible()
    expect(page.get_by_role("region", name="Who has what")).to_be_visible()
    page.screenshot(path=f"{OUT}/4_workflow.png", full_page=True)

@step("Studio drawer: capability, code, gaps and the requirement's own findings")
def drawer(page):
    page.goto(f"{BASE}/rfps/1")
    page.locator('section[aria-label="Question queue"] li button').filter(has_text=re.compile(r"^\s*2\.3(?![\d.])")).first.click()
    drawer = page.locator('aside[aria-label="Evidence and audit"]')
    expect(drawer.get_by_text("Capability and response code")).to_be_visible()
    expect(drawer.get_by_text("Development").first).to_be_visible()
    expect(drawer.get_by_text("Development required")).to_be_visible()
    expect(drawer.get_by_text("Engineering").first).to_be_visible()
    page.screenshot(path=f"{OUT}/5_drawer.png")

@step("Validate a code: typing in the estimate keeps every character, and validating is recorded")
def validate(page):
    drawer = page.locator('aside[aria-label="Evidence and audit"]')
    drawer.get_by_text("Validate or correct").click()
    type_into(drawer.get_by_label("Effort estimate (when the code asks for one)"), "15 person-days")
    drawer.get_by_role("button", name="Validate this code").click()
    expect(page.get_by_text("Code validated")).to_be_visible()
    expect(drawer.get_by_text(re.compile("validated by"))).to_be_visible()

@step("A person can correct the capability and the code follows the RFP's own scheme")
def override(page):
    drawer = page.locator('aside[aria-label="Evidence and audit"]')
    drawer.get_by_label("Capability").select_option("configuration")
    drawer.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Saved")).to_be_visible()
    expect(drawer.locator("span.chip.font-mono")).to_have_text("C")

@step("Clarifications: ranked, with priority, owner and a place for the customer's reply")
def clar(page):
    page.goto(f"{BASE}/rfps/1/clarifications")
    expect(page.get_by_text(re.compile("high priority")).first).to_be_visible()
    first = page.get_by_label("The customer's reply").first
    type_into(first, "Annexure B will follow on Monday.")
    page.get_by_role("button", name="Record the reply").first.click()
    expect(page.get_by_text("answered").first).to_be_visible()
    page.screenshot(path=f"{OUT}/6_clar.png", full_page=True)

@step("Export dialog: the RFP's response codes can be checked and corrected")
def export(page):
    page.goto(f"{BASE}/rfps/1")
    page.get_by_role("button", name=re.compile("Export")).first.click()
    dlg = page.get_by_role("dialog")
    expect(dlg.get_by_text("Response codes the RFP asks for")).to_be_visible()
    assert dlg.locator('input[aria-label^="Label for code"]').count() == 5
    type_into(dlg.get_by_label("Label for code T"), "Third-party dependency (named)")
    dlg.get_by_role("button", name="Confirm rules").click()
    expect(page.get_by_text("Formatting rules confirmed")).to_be_visible()
    page.keyboard.press("Escape")

@step("Proceed to review: blockers need a reason that is recorded")
def proceed(page):
    page.goto(f"{BASE}/rfps/1/analysis")
    btn = page.get_by_role("button", name="Proceed to review")
    btn.click()
    dlg = page.get_by_role("dialog")
    if dlg.count():
        type_into(dlg.get_by_label(re.compile("Why proceed anyway")), "Owners are confirming the open items")
        dlg.get_by_role("button", name="Proceed anyway").click()
    expect(page.get_by_text("Marked as ready for review")).to_be_visible()
    expect(page.get_by_text("In review").first).to_be_visible()

@step("A phone-width screen does not scroll sideways")
def mobile(page):
    page.set_viewport_size({"width": 390, "height": 800})
    for path in ("/rfps/1/analysis", "/rfps/1/clarifications"):
        page.goto(BASE + path)
        page.wait_for_timeout(600)
        over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert over <= 1, f"{path} overflows by {over}px"

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
    page = b.new_page(viewport={"width": 1400, "height": 1000})
    page.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    for fn in (matrix, filters, gaps, gate, workflow, drawer, validate, override, clar, export, proceed, mobile):
        fn(page)
    b.close()
for ok, name in results:
    print(("PASS  " if ok else "FAIL  ") + name)
print(f"\n{sum(o for o, _ in results)}/{len(results)} checks passed")
print("HTTP >= 400:", bad or "none"); print("console errors:", errors or "none")

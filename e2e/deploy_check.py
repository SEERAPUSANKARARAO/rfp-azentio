"""Checks a LIVE deployment (frontend and backend at different addresses) the way a visitor would use it.

    cd backend && .venv/bin/python ../e2e/deploy_check.py --site https://your-app.vercel.app --api https://your-api.onrender.com
        [--key ACCESS_KEY] [--draft] [--out folder] [--chrome /usr/bin/google-chrome]

By default it only reads and loads: the page reaches the API, the access prompt works, the sample knowledge base loads,
the sample RFP imports. --draft also drafts it, which uses model quota. It CHANGES the deployment's data (loads the sample
knowledge base and imports an RFP), so use a deployment you can reset.

What it proves that nothing else does: that the page talks to the API at the OTHER address (no call goes to the site's own
/api, which would mean VITE_API_URL is missing), that a refused key is handled, and that an exported file keeps its name
across origins (the backend must expose Content-Disposition).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
results: list[tuple[bool, str]] = []


def step(name):
    def deco(fn):
        def run(*a):
            try:
                fn(*a)
                results.append((True, name))
            except Exception as e:  # noqa: BLE001
                results.append((False, f"{name}: {str(e).splitlines()[0][:200]}"))
        return run
    return deco


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--api", required=True)
    ap.add_argument("--key", default="")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--out", default="deploy_check_out")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    ap.add_argument("--rfp", default=str(ROOT / "sample_data" / "rfps" / "ASIB_RFP-2026-017_Core_Islamic_Banking.xlsx"))
    a = ap.parse_args()
    site, api, out = a.site.rstrip("/"), a.api.rstrip("/"), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    own_api_calls, failed, errors = [], [], []

    @step("The page loads (a sleeping backend is given up to 2 minutes to wake)")
    def load(page: Page):
        page.goto(site)
        expect(page.get_by_role("heading", name="Active RFPs").or_(page.get_by_label("Access key"))).to_be_visible(timeout=120_000)

    @step("The access prompt works: a wrong key is refused and the right one lets you in")
    def gate(page: Page):
        if not page.get_by_label("Access key").count():
            return  # no key is required on this deployment
        assert a.key, "this deployment asks for an access key: pass --key"
        page.get_by_label("Access key").fill("definitely-wrong")
        page.get_by_role("button", name="Continue").click()
        expect(page.get_by_text("That key is not right.")).to_be_visible()
        page.get_by_label("Access key").fill(a.key)
        page.get_by_role("button", name="Continue").click()
        expect(page.get_by_role("heading", name="Active RFPs")).to_be_visible()

    @step("The knowledge base loads through the other address")
    def kb(page: Page):
        page.goto(f"{site}/knowledge")
        page.get_by_role("button", name="Load sample knowledge base").click()
        expect(page.get_by_text("Disaster Recovery and Business Continuity Policy v3")).to_be_visible(timeout=120_000)
        assert page.locator("tbody tr").count() == 25, "all 25 sample sources should be listed"
        if a.draft:  # conflict flags on answers only exist once conflicts have been detected, which uses model quota
            page.get_by_role("button", name="Detect conflicts").click()
            expect(page.get_by_text("needs a decision").first).to_be_visible(timeout=300_000)

    @step("The sample RFP imports and its rules are read")
    def upload(page: Page):
        page.goto(site)
        page.set_input_files('input[type="file"]', a.rfp)
        panel = page.locator('section[aria-label="RFP analysis"]')
        expect(panel.get_by_text("30", exact=True).first).to_be_visible(timeout=120_000)
        expect(panel.get_by_text("C / PC / NC")).to_be_visible()

    @step("Drafting runs and the progress reaches the page")
    def draft(page: Page):
        page.get_by_role("button", name="Start drafting").click()
        page.wait_for_url(re.compile(r"/rfps/\d+$"))
        expect(page.get_by_text(re.compile(r"30/30 drafted"))).to_be_visible(timeout=900_000)
        page.locator('section[aria-label="Question queue"] li button').filter(has_text=re.compile(r"^\s*3\.1(?![\d.])")).first.click()
        expect(page.locator('section[aria-label="Answer"]')).to_contain_text("encrypt", ignore_case=True)
        page.locator('section[aria-label="Question queue"] li button').filter(has_text=re.compile(r"^\s*3\.2(?![\d.])")).first.click()
        expect(page.locator('aside[aria-label="Evidence and audit"]').get_by_text("Sources contradict each other").first).to_be_visible()

    @step("An export downloads with its proper file name")
    def export(page: Page):
        page.get_by_role("button", name=re.compile("Export")).first.click()
        dlg = page.get_by_role("dialog")
        dlg.get_by_label(re.compile("Review copy")).check()
        with page.expect_download(timeout=120_000) as dl:
            dlg.get_by_role("button", name="Download review copy").click()
        name = dl.value.suggested_filename
        assert name.endswith("_DRAFT.xlsx") and name != "response", f"downloaded as {name!r}: Content-Disposition is not readable across origins"
        dl.value.save_as(out / name)

    @step("Every call went to the API address, none to the site's own /api")
    def addresses(page: Page):
        assert not own_api_calls, f"{len(own_api_calls)} call(s) went to the site itself (VITE_API_URL missing at build time?): {own_api_calls[:2]}"

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chrome, headless=True)
        page = b.new_page(viewport={"width": 1400, "height": 950}, accept_downloads=True)
        page.on("request", lambda r: own_api_calls.append(r.url) if r.url.startswith(site + "/api") else None)
        page.on("response", lambda r: failed.append((r.status, r.url)) if r.status >= 400 and r.status != 401 else None)
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        load(page)
        gate(page)
        kb(page)
        upload(page)
        if a.draft:
            draft(page)
            export(page)
        addresses(page)
        page.screenshot(path=str(out / "last_page.png"))
        b.close()

    for ok, name in results:
        print(("PASS  " if ok else "FAIL  ") + name)
    print(f"\n{sum(o for o, _ in results)}/{len(results)} checks passed" + ("" if a.draft else "  (drafting and export skipped: add --draft)"))
    print("failed requests:", failed or "none")
    print("console errors:", [e for e in errors if "401" not in e] or "none")
    return 0 if all(o for o, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())

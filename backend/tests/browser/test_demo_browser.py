"""Exercise the polished demo UI with invented data and explicitly mocked inference."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def test_demo_review_guidance_and_newly_found_impact(page):
    def capture(name):
        if folder := os.environ.get("GRADELENS_DEMO_SCREENSHOTS"):
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target / f"{name}.png"), full_page=True)

    page.goto(page.base + "/?demo#/import")
    expect(page.get_by_role("navigation").get_by_role("link")).to_have_count(5)
    page.get_by_role("button", name="Load demo dataset", exact=True).click()
    expect(page.get_by_text("Demo dataset loaded (8 submissions).")).to_be_visible()
    capture("01-load")
    page.get_by_role("button", name="Continue to runs").click()
    expect(page.get_by_text("7 submissions selected:", exact=False)).to_be_visible()
    page.get_by_role("button", name="Find comparisons", exact=True).click()
    proceed = page.get_by_role("button", name="Continue to review queue")
    expect(proceed).to_be_visible(timeout=60000)
    capture("02-comparisons")
    proceed.click()
    pair = page.locator(".case-item").filter(has_text="D001 ↔ D003")
    expect(pair).to_have_count(1)
    capture("03-queue")
    pair.click()
    expect(page.get_by_text("Recorded score · unchanged")).to_have_count(2)
    expect(page.get_by_role("heading", name="How they differ")).to_be_visible()
    capture("04-evidence")
    page.get_by_role("button", name="Clarify the guidance from this case").click()
    text = ("Printing the required rows with a loop is acceptable. "
            "Evaluate output requirements separately; this clarification alone sets no numeric deduction.")
    page.get_by_label("Comment from the instructor").fill(text)
    page.get_by_role("button", name="Save comment", exact=True).click()
    page.get_by_label("Approved interpretation (required)").fill(text)
    page.get_by_role("radio", name="Clarification of existing expectations").check()
    page.get_by_label("Applicability conditions (optional JSON, deterministic).").fill(
        '{"feature":"loop_count","op":">","value":0}')
    capture("05-approval")
    page.get_by_role("button", name="Approve and scan every in-scope submission").click()
    callout = page.locator(".callout")
    expect(callout).to_contain_text("D006", timeout=60000)
    expect(callout).to_contain_text("Original grades are unchanged")
    capture("06-impact")
    page.get_by_role("link", name="5 · Audit log", exact=True).click()
    expect(page.get_by_role("heading", name="Audit history")).to_be_visible()
    page.get_by_role("button", name="Export moderation report", exact=True).click()
    expect(page.get_by_role("button", name="Download CSV", exact=True)).to_be_visible()
    capture("07-audit")
    assert not page.page_errors

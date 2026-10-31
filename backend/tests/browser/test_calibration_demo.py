"""Polished P1 demo: shared partial-credit policy, unchanged grades, explicit revision."""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def capture(page, name):
    if folder := os.environ.get("GRADELENS_CALIBRATION_SCREENSHOTS"):
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / f"{name}.png"), full_page=True)


def prepare_calibration(page):
    """Invented grader drafts only; deliberately do not request or approve guidance."""
    page.goto(page.base + "/?demo=calibration#/grading")
    page.get_by_role("button", name="Load calibration example", exact=True).click()
    expect(page.locator(".calibration-score")).to_have_count(3)
    for code, points, total, grader in (("P002", "2", "6", "Synthetic Grader A"),
                                       ("P004", "5", "9", "Synthetic Grader B"),
                                       ("P007", "1", "5", "Synthetic Grader C")):
        page.locator(".calibration-score").filter(has_text=code).click()
        expect(page.get_by_role("heading", name=f"{code} · grader draft")).to_be_visible()
        page.get_by_label("Correctness points", exact=True).fill(points)
        page.get_by_label("Assignment total", exact=True).fill(total)
        page.get_by_label("Grader label", exact=True).fill(grader)
        page.get_by_role("button", name="Save draft & check", exact=True).click()
        expect(page.get_by_role("button", name="Ask head TA once", exact=True)).to_be_visible(timeout=40000)
    capture(page, "01-different-drafts")


def test_partial_credit_demo_asks_once_proposes_range_and_requires_human_revision(page):
    prepare_calibration(page)
    expect(page.get_by_role("navigation").get_by_role("link")).to_have_count(4)
    assessment_id = int(page.get_by_label("Selected assessment").input_value())
    queue = page.request.get(page.base + f"/api/assessments/{assessment_id}/grading-queue").json()
    p007 = next(r for r in queue if r["student_id"] == "P007")
    before = page.request.get(page.base + f"/api/submissions/{p007['submission_id']}/workspace").json()
    criterion = next(c["criterion_key"] for c in before["criteria"] if "correctness" in c["criterion_key"] and c["max_points"] == "6")
    page.get_by_role("button", name="Ask head TA once", exact=True).click()
    shared = page.get_by_role("button", name="View shared question · 3 submissions", exact=True)
    expect(shared).to_be_visible(timeout=30000)
    shared.click()
    expect(page.get_by_text("3 submissions · 1 shared question", exact=True)).to_be_visible()
    expect(page.get_by_label("Minimum points")).to_have_value("")
    page.get_by_label("Minimum points").fill("3")
    page.get_by_label("Maximum points").fill("4")
    page.get_by_label("Reason for range").fill("Credit the working search; missing diagonal validation limits correctness. Review other defects separately.")
    page.get_by_role("checkbox", name=re.compile("I reviewed the examples")).check()
    capture(page, "02-one-answer")
    page.get_by_role("button", name="Approve range & check matching work", exact=True).click()
    table = page.get_by_test_id("calibration-impact")
    for code in ("P002", "P004", "P007"):
        expect(table.get_by_role("row").filter(has_text=code)).to_contain_text("3–4", timeout=40000)
    expect(table.get_by_role("row").filter(has_text="P008")).to_contain_text("Outside this rule")
    unchanged = page.request.get(page.base + f"/api/submissions/{p007['submission_id']}/workspace").json()
    assert unchanged["current_revision"] == before["current_revision"]
    assert unchanged["recorded"] == before["recorded"]
    capture(page, "03-impact")
    page.get_by_role("button", name="Review a grade change").click()
    expect(page.get_by_text("Draft revision 1", exact=True)).to_be_visible()
    page.get_by_label("Correctness points", exact=True).fill("3")
    page.get_by_label("Assignment total", exact=True).fill("7")
    page.get_by_role("button", name="Confirm my revision", exact=True).click()
    expect(page.get_by_text("Your revision was recorded.", exact=False)).to_be_visible(timeout=30000)
    expect(page.get_by_text("Draft revision 2", exact=True)).to_be_visible(timeout=30000)
    capture(page, "04-human-revision")
    final = page.request.get(page.base + f"/api/submissions/{p007['submission_id']}/workspace").json()
    assert final["current_revision"]["criterion_points"][criterion] == "3"
    assert float(final["current_revision"]["total"]) == 7
    assert final["recorded"] == before["recorded"]
    page.get_by_role("link", name="1 · Grade & feedback", exact=True).click()
    expect(page.get_by_text("No new head-TA question is needed.", exact=True)).to_be_visible(timeout=40000)
    issues = page.request.get(page.base + f"/api/calibration-issues?assessment_id={assessment_id}").json()
    assert len(issues) == 1 and issues[0]["state"] == "resolved"
    assert not page.page_errors

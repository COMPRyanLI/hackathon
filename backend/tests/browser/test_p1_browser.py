"""Browser end-to-end checks on the SYNTHETIC P1 fixture (plan section 11, items 26 and 41).

Each test gets a fresh server and scratch database. Synthetic assessments use
the labeled mock providers automatically, so no model is called."""
from __future__ import annotations

import re
import time

import pytest

pytestmark = pytest.mark.browser

DONE = re.compile(r"^(completed|partial)$")


def _expect():
    from playwright.sync_api import expect

    return expect


def load_demo(page) -> int:
    expect = _expect()
    page.goto(page.base + "/#/import")
    page.get_by_test_id("load-p1-demo").click()
    expect(page.get_by_text("SYNTHETIC P1 demo ready")).to_be_visible()
    expect(page.get_by_label("Selected assessment")).to_contain_text(re.compile(r"synthetic-p1 . SA3"), timeout=20_000)
    assessments = page.request.get(page.base + "/api/assessments").json()
    return next(a["id"] for a in assessments if a["dataset_key"] == "synthetic-p1" and a["name"] == "SA3")


def open_submission(page, code: str) -> None:
    expect = _expect()
    if "#/grading" not in page.url:
        page.goto(page.base + "/#/grading")
    page.get_by_test_id(f"queue-row-{code}").click()
    expect(page.get_by_test_id("workspace-student")).to_contain_text(code)


def save_draft(page, total: str, grader: str) -> None:
    expect = _expect()
    page.get_by_test_id("draft-total").fill(total)
    page.get_by_test_id("grader-label").fill(grader)
    page.get_by_test_id("save-draft").click()
    expect(page.get_by_test_id("save-status")).to_contain_text("Saved revision")


def wait_feedback(page) -> None:
    _expect()(page.get_by_test_id("feedback-status")).to_have_text(DONE, timeout=30_000)


def card(page, card_type: str):
    return page.get_by_test_id(f"card-{card_type}").first


def wait_for_card(page, code: str, card_type: str, timeout_s: float = 40) -> None:
    """The workspace polls only while feedback is pending; refresh until a background
    refresh (after a head-TA answer) has produced the expected card."""
    deadline = time.monotonic() + timeout_s
    while True:
        open_submission(page, code)
        wait_feedback(page)
        if page.get_by_test_id(f"card-{card_type}").count():
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"no {card_type} card for {code}")
        time.sleep(1)
        page.reload()


def test_41_primary_workflow_save_feedback_shared_question_resolve_reuse_snapshot(page):
    expect = _expect()
    sa3 = load_demo(page)

    # Save a total-only draft and move on while feedback is pending.
    open_submission(page, "P001")
    save_draft(page, "9.0", "Synthetic Grader A")
    status = page.get_by_test_id("save-status")
    expect(status).to_contain_text("Saved revision 1")
    expect(status).to_contain_text("feedback queued")
    page.get_by_test_id("next-submission").click()
    expect(page.get_by_test_id("workspace-student")).to_contain_text("P002")

    # Feedback with source and rubric evidence and one focused question.
    open_submission(page, "P001")
    wait_feedback(page)
    question = card(page, "policy_question")
    expect(question).to_contain_text("Inclusive loop end bound")
    expect(question.get_by_test_id("card-question")).to_contain_text("?")
    expect(question.get_by_test_id("card-evidence").first).to_contain_text("while year <= END_YEAR")
    expect(page.get_by_test_id("coverage-statement")).to_contain_text("not mean the grade was verified correct")
    question.get_by_test_id("action-request-calibration").click()
    expect(page.get_by_test_id("save-status")).to_contain_text("Head-TA question created")

    # Two more graders' matching drafts link to the same question.
    for code, total, grader in (("P002", "10.0", "Synthetic Grader B"), ("P003", "8.5", "Synthetic Grader C")):
        open_submission(page, code)
        save_draft(page, total, grader)
        wait_feedback(page)
        expect(card(page, "policy_question").get_by_test_id("card-body")).to_contain_text("already awaiting head-TA review")

    # Head-TA queue: exactly one shared question with three affected submissions.
    page.goto(page.base + "/#/issues")
    rows = page.locator("[data-testid^='issue-row-']")
    expect(rows).to_have_count(1)
    expect(rows.first.get_by_test_id("issue-affected")).to_have_text("3")
    rows.first.click()
    expect(page.get_by_text("No impact scan exists yet.", exact=False)).to_be_visible()
    expect(page.get_by_test_id("resolve-form")).to_be_visible()
    expect(page.get_by_test_id("resolve-condition")).to_have_value(re.compile("loop_inclusive_bound"))
    expect(page.get_by_test_id("resolve-exceptions")).to_have_value(re.compile("loop_counter_start"))
    page.get_by_test_id("resolve-text").fill(
        "An inclusive end bound that adds one compounding period affects only the final-balance criterion "
        "(award 0 of 1 there). Loop setup is assessed separately.")
    page.get_by_test_id("resolve-treatment").fill('{"action": "award", "points": "0"}')
    page.get_by_test_id("resolve-submit").click()
    expect(page.get_by_text("Resolved.")).to_be_visible()

    # Impact review covers previously unflagged work; the exception stays reviewable.
    expect(page.get_by_test_id("open-impact")).to_be_visible()
    page.locator("[data-testid^='impact-shortcut-']").first.click()
    p006 = page.get_by_test_id("impact-row-P006")
    expect(p006.locator("td").nth(3)).to_contain_text("applies", timeout=40_000)
    expect(p006.locator("td").nth(5)).to_have_text("no")
    expect(page.get_by_test_id("impact-row-P005").locator("td").nth(3)).to_contain_text("uncertain")
    expect(page.get_by_text("No grades were changed")).to_be_visible()

    # A later matching draft reuses the answer without a new question.
    page.goto(page.base + "/#/grading")
    open_submission(page, "P004")
    save_draft(page, "9.0", "Synthetic Grader A")
    wait_feedback(page)
    expect(card(page, "covered_by_guidance")).to_be_visible()
    expect(page.get_by_test_id("card-policy_question")).to_have_count(0)
    issues = page.request.get(page.base + f"/api/calibration-issues?assessment_id={sa3}").json()
    assert len([i for i in issues if i["state"] != "superseded"]) == 1

    # Explicit human revision of P001 through a card action.
    wait_for_card(page, "P001", "covered_by_guidance")
    covered = card(page, "covered_by_guidance")
    covered.get_by_test_id("action-revise").click()
    covered.get_by_test_id("revise-total").fill("8.0")
    covered.get_by_test_id("revise-save").click()
    expect(page.get_by_test_id("save-status")).to_contain_text("Saved revision")

    # Versioned moderation snapshot pins the revision and lists unresolved rows.
    page.goto(page.base + "/#/prerelease")
    page.get_by_test_id("create-snapshot").click()
    expect(page.get_by_test_id("snapshot-version")).to_have_text("Snapshot version 1")
    csv_text = page.get_by_test_id("snapshot-csv").inner_text()
    p001 = next(line for line in csv_text.splitlines() if line.startswith("P001,"))
    assert ",9.0," in p001 and ",8," in p001, p001
    assert page.get_by_test_id("snapshot-unresolved").locator("li").count() >= 1
    assert not page.page_errors, page.page_errors


def test_26_rubric_flags_unknown_evidence_and_prerelease_views(page):
    expect = _expect()
    load_demo(page)

    page.goto(page.base + "/#/rubric")
    expect(page.get_by_test_id("rubric-issue-SA4-arithmetic_conflict")).to_have_count(0)
    expect(page.get_by_test_id("rubric-issue-SA4-structure_ambiguous")).to_have_count(0)
    conflict = page.get_by_test_id("rubric-issue-SA5-arithmetic_conflict")
    expect(conflict).to_contain_text("blocks total proposals")
    expect(conflict).to_contain_text("11")
    expect(page.get_by_test_id("rubric-issue-SA3-exemption_unresolved")).to_be_visible()
    expect(page.get_by_test_id("bound-blocked")).to_contain_text("blocked")

    page.goto(page.base + "/#/import")
    flags = page.get_by_test_id("flags")
    expect(flags).to_contain_text("UNVERIFIED imported leads")
    table = page.get_by_test_id("flags-table")
    expect(table).to_contain_text("unresolved")
    expect(table).to_contain_text(re.compile("exact_(file_in_larger_)?bundle"))
    expect(table).to_contain_text("note_contradicted_by_source")
    expect(table).to_contain_text("—")  # blank similarity is shown as unknown, not zero

    page.get_by_test_id("manifest").locator("summary").click()
    expect(page.get_by_test_id("manifest-row-P012")).to_contain_text("not_in_current_export")
    expect(page.get_by_test_id("manifest")).to_contain_text("confirmed_not_submitted")

    page.goto(page.base + "/#/prerelease")
    expect(page.get_by_test_id("unchecked-pairs")).to_have_text(re.compile(r"^[1-9]\d*$"))
    expect(page.get_by_test_id("pr-unresolved-policy")).to_contain_text("does not apply to SA3")
    expect(page.get_by_test_id("pr-unavailable-evidence")).to_contain_text("screenshot")
    assert not page.page_errors, page.page_errors


def test_source_cards_visible_while_model_is_pending_and_role_can_be_corrected(page):
    expect = _expect()
    load_demo(page)
    open_submission(page, "P001")
    save_draft(page, "9", "Synthetic Grader")
    wait_feedback(page)
    # Controlled UI state: keep real source-check cards, simulate pending model.
    def pending_model(route):
        response = route.fetch()
        data = response.json()
        if data.get("feedback"):
            data["feedback"]["status"] = "running"
            data["feedback"]["stages"]["model_source_analysis"] = {"status":"queued"}
        route.fulfill(response=response, json=data)
    page.route("**/workspace", pending_model)
    page.reload()
    expect(page.get_by_test_id("feedback-status")).to_have_text("running")
    expect(card(page, "policy_question")).to_be_visible()
    expect(page.get_by_text("Source checks are ready below.", exact=False)).to_be_visible()
    from pathlib import Path
    docs = Path(__file__).resolve().parents[3] / "docs"
    page.screenshot(path=str(docs / "p1-feedback-pending.png"), full_page=True)
    page.unroute("**/workspace", pending_model)

    open_submission(page, "P010")
    page.get_by_test_id("component-role").select_option("program_1")
    page.get_by_test_id("confirm-component-role").click()
    expect(page.get_by_text("confirmed", exact=True)).to_be_visible()
    # Confirm persistence after a reload rather than just checking the form value.
    page.reload()
    expect(page.get_by_test_id("component-role")).to_have_value("program_1")
    expect(page.get_by_text("confirmed", exact=True)).to_be_visible()
    # The deliberately mixed fixture should remain unresolved in the saved demo
    # screenshot; the edit above tests persistence, not a real role adjudication.
    page.get_by_test_id("component-role").select_option("ambiguous")
    page.get_by_test_id("confirm-component-role").click()
    expect(page.get_by_text("ambiguous", exact=True)).to_be_visible()
    page.screenshot(path=str(docs / "p1-component-review.png"), full_page=True)
    assert not page.page_errors, page.page_errors

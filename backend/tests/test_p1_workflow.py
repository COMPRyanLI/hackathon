"""P1 end-to-end on the SYNTHETIC fixture: save drafts -> asynchronous feedback ->
one shared head-TA question for three cases -> one answer -> impact scan incl.
unflagged work -> later draft reuses the answer -> exception stays reviewable ->
approval changes no grade -> explicit revision appears in a versioned snapshot."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT
from app.jobs.worker import process_queued
from app.main import create_app
from app.providers.registry import ProviderRegistry
from tests.conftest import make_settings

H = {"X-GradeLens-Client": "1"}
INCLUSIVE = {"component": "program_1", "fact": "loop_inclusive_bound", "op": "==", "value": True}
EXCEPTION = {"description": "counter starts at 1, so the iteration count is unchanged",
             "condition": {"component": "program_1", "fact": "loop_counter_start", "op": "==", "value": 1}}


@pytest.fixture
def p1(tmp_path: Path):
    settings = make_settings(tmp_path)
    registry = ProviderRegistry(settings)
    with TestClient(create_app(settings, registry, start_worker=False)) as client:
        demo = client.post("/api/demo/p1", headers=H).json()
        client.registry = registry
        client.sa3 = demo["assessments"]["SA3"]["assessment_id"]
        client.sa6 = demo["assessments"]["SA6"]["assessment_id"]
        queue = client.get(f"/api/assessments/{client.sa3}/grading-queue").json()
        client.subs = {row["student_id"]: row["submission_id"] for row in queue}
        yield client


def save(client, code: str, total: str, grader: str, expected=None, **extra):
    return client.post(f"/api/submissions/{client.subs[code]}/draft-grades", headers=H,
                       json={"total": total, "grader_label": grader, "expected_revision_id": expected, **extra})


def feedback(client, code: str) -> dict:
    return client.get(f"/api/submissions/{client.subs[code]}/workspace").json()["feedback"]


def cards_of(fb: dict, card_type: str) -> list[dict]:
    return [c for c in fb["all_cards"] if c["type"] == card_type]


def test_primary_workflow_three_cases_one_question_one_answer_reused(p1):
    csv_before = hashlib.sha256((PROJECT_ROOT / "fixtures/synthetic/p1/scores.csv").read_bytes()).hexdigest()

    # 1. Total-only draft saves immediately; feedback is pending while the grader moves on.
    r = save(p1, "P001", "9.0", "Synthetic Grader A")
    assert r.status_code == 200 and r.json()["created"] and r.json()["feedback"]["status"] == "queued"
    assert r.json()["revision"]["criterion_points"] == {}
    assert feedback(p1, "P001")["status"] == "queued"
    process_queued(p1.registry)

    # 2. Feedback cites source and rubric evidence and asks one specific question.
    fb = feedback(p1, "P001")
    assert fb["status"] in ("completed", "partial") and fb["is_current"]
    question = cards_of(fb, "policy_question")
    assert len(question) == 1
    card = question[0]
    assert card["evidence"] and card["evidence"][0]["quote"].strip().startswith("while year <= END_YEAR")
    assert card["rubric_refs"] and card["rubric_refs"][0]["criterion_key"].endswith("correct_final_balance")
    assert "unresolved" in card["body"] and card["question"].endswith("?")
    assert len(fb["initial_cards"]) <= 3
    assert "not mean the grade was verified correct" in fb["coverage"]["statement"]

    # 3. Grader A asks the head TA; later matching drafts from two other graders link to the same issue.
    rev1 = fb["revision_id"]
    out = p1.post(f"/api/feedback-cards/{card['id']}/dispositions", headers=H,
                  json={"action": "request_calibration", "revision_id": rev1}).json()
    issue_id = out["issue_id"]
    assert out["issue_created"] is True
    save(p1, "P002", "10.0", "Synthetic Grader B")
    save(p1, "P003", "8.5", "Synthetic Grader C")
    replay = save(p1, "P003", "8.5", "Synthetic Grader C", idempotency_key="dup-1")
    assert replay.status_code == 409  # stale expected revision is an explicit conflict, not a lost edit
    process_queued(p1.registry)
    issues = p1.get("/api/calibration-issues", params={"assessment_id": p1.sa3}).json()
    assert len(issues) == 1 and issues[0]["id"] == issue_id
    assert issues[0]["affected"] == 3 and issues[0]["state"] == "awaiting_head_ta"
    fb2 = feedback(p1, "P002")
    linked = cards_of(fb2, "policy_question")[0]
    assert linked["issue"]["id"] == issue_id and "already awaiting head-TA review" in linked["body"]

    # Idempotent replays do not inflate counts or add revisions.
    cur3 = feedback(p1, "P003")["revision_id"]
    for _ in range(2):
        again = save(p1, "P003", "8.0", "Synthetic Grader C", expected=cur3, idempotency_key="same-key")
        assert again.status_code == 200
    assert again.json()["created"] is False
    process_queued(p1.registry)
    assert p1.get(f"/api/calibration-issues/{issue_id}").json()["affected"] == 3

    drafts_before = {c: p1.get(f"/api/submissions/{p1.subs[c]}/workspace").json()["history"]
                     for c in ("P001", "P002", "P003")}

    # 4. One answer: typed rule + exception + point treatment; impact scan covers unflagged work.
    detail = p1.get(f"/api/calibration-issues/{issue_id}").json()
    resolved = p1.post(f"/api/calibration-issues/{issue_id}/resolve", headers=H, json={
        "expected_version": detail["version"], "resolution_type": "interpretation",
        "text": "An inclusive end bound that adds one compounding period affects only the final-balance criterion "
                "(award 0 of 1 there). Loop setup is assessed separately.",
        "note_kind": "clarification", "condition": INCLUSIVE, "exception_conditions": [EXCEPTION],
        "point_treatment": {"action": "award", "points": "0"},
        "criterion_key": detail["criterion_key"]})
    assert resolved.status_code == 200, resolved.text
    note_version = resolved.json()["issue"]["resolution_note_version_id"]
    process_queued(p1.registry)
    impact = p1.get(f"/api/instructor-notes/{note_version}/impact").json()
    items = {i["student_id"]: i for i in impact["items"]}
    assert len(items) == 11                                          # every in-scope submission
    assert items["P006"]["status"] == "applies" and items["P006"]["previously_flagged"] is False
    assert items["P006"]["score"]["score"] is None                    # blank recorded score still scanned
    assert items["P007"]["status"] == "does_not_apply"
    assert items["P005"]["status"] == "uncertain" and "exception" in items["P005"]["rationale"]
    assert items["P010"]["status"] == "uncertain"                     # ambiguous component mapping
    assert items["P008"]["status"] == "uncertain"                     # fact unknown (no loop) is not "false"

    # Linked cases now show the reused answer.
    fb1 = feedback(p1, "P001")
    covered = cards_of(fb1, "covered_by_guidance")
    assert covered and "total-only" in covered[0]["body"]
    assert not cards_of(fb1, "policy_question")

    # 7. Approval alone changed no draft or original grade.
    for code, history in drafts_before.items():
        assert p1.get(f"/api/submissions/{p1.subs[code]}/workspace").json()["history"] == history
    assert hashlib.sha256((PROJECT_ROOT / "fixtures/synthetic/p1/scores.csv").read_bytes()).hexdigest() == csv_before

    # 5. A new matching draft reuses the answer without a new question.
    save(p1, "P004", "9.0", "Synthetic Grader A")
    process_queued(p1.registry)
    fb4 = feedback(p1, "P004")
    assert cards_of(fb4, "covered_by_guidance") and not cards_of(fb4, "policy_question")
    assert len([i for i in p1.get("/api/calibration-issues", params={"assessment_id": p1.sa3}).json()
                if i["state"] != "superseded"]) == 1

    # 6. A materially different exception remains reviewable.
    save(p1, "P005", "9.5", "Synthetic Grader B")
    process_queued(p1.registry)
    assert cards_of(feedback(p1, "P005"), "guidance_exception")

    # 8. Explicit human revision -> versioned moderation snapshot with unresolved items.
    covered_card = covered[0]
    rev = p1.post(f"/api/feedback-cards/{covered_card['id']}/dispositions", headers=H, json={
        "action": "revise", "revision_id": fb1["revision_id"], "new_total": "8.0",
        "criterion_points": {detail["criterion_key"]: "0"}, "explanation": "Applied the approved answer."})
    assert rev.status_code == 200, rev.text
    process_queued(p1.registry)
    snap = p1.post(f"/api/assessments/{p1.sa3}/moderation-snapshots", headers=H).json()
    rows = {line.split(",")[0]: line for line in snap["csv_text"].splitlines()[1:]}
    assert snap["version"] == 1 and rev.json()["new_revision_id"] in snap["revision_ids"]
    assert ",9.0," in rows["P001"] and ",8.00," in rows["P001"] or ",8," in rows["P001"]
    assert "revise" in rows["P001"] and "correct_final_balance=0" in rows["P001"]
    assert "uncertain" in rows["P005"]
    assert any(u["student_id"] == "P012" for u in snap["unresolved"])  # no code in export
    snap2 = p1.post(f"/api/assessments/{p1.sa3}/moderation-snapshots", headers=H).json()
    assert snap2["version"] == 2

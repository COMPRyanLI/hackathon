"""Milestone C on the SYNTHETIC SA6-style fixture: discretionary partial credit.

Draft mode first (no invented scale), then one head-TA anchor for one candidate
reason, traceable proposals across matching evidence, a separate question for a
materially different reason, an interacting-failure exception, a draft that
differs from the anchor, and starter-code criteria that stay unresolved."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.jobs.worker import process_queued
from app.main import create_app
from app.providers.registry import ProviderRegistry
from tests.conftest import make_settings

H = {"X-GradeLens-Client": "1"}
MISSING_DIAGONAL = {"component": "whole_assignment", "fact": "diagonal_check_present", "op": "==", "value": False}
NO_BACKTRACK = {"description": "backtracking also missing (interacting failures)",
                "condition": {"component": "whole_assignment", "fact": "backtracking_present", "op": "==",
                              "value": False}}


@pytest.fixture
def sa6(tmp_path: Path):
    settings = make_settings(tmp_path)
    registry = ProviderRegistry(settings)
    with TestClient(create_app(settings, registry, start_worker=False)) as client:
        demo = client.post("/api/demo/p1", headers=H).json()
        client.registry, client.aid = registry, demo["assessments"]["SA6"]["assessment_id"]
        client.subs = {r["student_id"]: r["submission_id"]
                       for r in client.get(f"/api/assessments/{client.aid}/grading-queue").json()}
        yield client


def save(c, code, total, points=None, grader="Synthetic Grader A"):
    cur = c.get(f"/api/submissions/{c.subs[code]}/workspace").json()["current_revision"]
    r = c.post(f"/api/submissions/{c.subs[code]}/draft-grades", headers=H, json={
        "total": total, "grader_label": grader, "expected_revision_id": cur["id"] if cur else None,
        "criterion_points": points})
    assert r.status_code == 200, r.text
    process_queued(c.registry)
    return c.get(f"/api/submissions/{c.subs[code]}/workspace").json()


def typed(ws, card_type):
    return [x for x in ws["feedback"]["all_cards"] if x["type"] == card_type]


def test_sa6_partial_credit_calibration_end_to_end(sa6):
    rubric = sa6.get(f"/api/assessments/{sa6.aid}/rubric").json()
    kinds = {i["kind"] for i in rubric["issues"] if i["assignment"] == "SA6"}
    assert {"partial_credit_undefined", "escalation_indicated", "scaffold_unknown"} <= kinds
    correctness = next(c["criterion_key"] for c in rubric["criteria"] if "correctness" in c["criterion_key"]
                       and c["max_points"] == "6")

    # Draft mode: a candidate reason and a question, but no invented points.
    ws = save(sa6, "P002", "6.0")
    card = typed(ws, "calibration_question")[0]
    assert "no numeric proposal" in card["body"] and "not an official weight" in card["body"]
    assert card["rubric_refs"][0]["criterion_key"] == correctness and card["evidence"] == []  # absence has no line
    unresolved = {u["criterion_key"] for u in ws["feedback"]["coverage"]["unresolved_criteria"]}
    assert any("not_changing_anything" in k for k in unresolved)          # starter code unknown
    assert any("required_files" in k for k in unresolved)                  # required-file manifest unknown

    issue = sa6.post(f"/api/feedback-cards/{card['id']}/dispositions", headers=H,
                     json={"action": "request_calibration", "revision_id": ws["current_revision"]["id"]}).json()
    save(sa6, "P004", "7.0", grader="Synthetic Grader B")
    save(sa6, "P007", "5.0", grader="Synthetic Grader C")
    p008 = save(sa6, "P008", "8.0")
    issues = sa6.get("/api/calibration-issues", params={"assessment_id": sa6.aid}).json()
    missing = next(i for i in issues if i["id"] == issue["issue_id"])
    assert missing["affected"] == 3
    # Incomplete diagonal is a materially different reason: its own card, not merged into "missing".
    other = typed(p008, "calibration_question")[0]
    assert "only one diagonal direction" in other["title"] and other["issue"] is None

    # One anchor for one reason, with an interacting-failure exception and a range treatment.
    detail = sa6.get(f"/api/calibration-issues/{missing['id']}").json()
    res = sa6.post(f"/api/calibration-issues/{missing['id']}/resolve", headers=H, json={
        "expected_version": detail["version"], "resolution_type": "interpretation", "note_kind": "clarification",
        "text": "Missing diagonal checks with otherwise working search: 3-4 of the 6 correctness points.",
        "condition": MISSING_DIAGONAL, "exception_conditions": [NO_BACKTRACK],
        "point_treatment": {"action": "range", "min": "3", "max": "4"}, "criterion_key": correctness,
        "is_anchor": True, "anchor_examples": [{"submission_id": sa6.subs["P002"], "kind": "positive"}]})
    assert res.status_code == 200, res.text
    process_queued(sa6.registry)
    impact = {i["student_id"]: i for i in sa6.get(
        f"/api/instructor-notes/{res.json()['issue']['resolution_note_version_id']}/impact").json()["items"]}
    for code in ("P002", "P004", "P007"):
        assert impact[code]["status"] == "applies"
        proposal = next(e["proposal"] for e in impact[code]["evidence"] if "proposal" in e)
        assert proposal == {"kind": "range", "min": "3", "max": "4", "max_points": "6"}
    for code in ("P001", "P003", "P005", "P006", "P008"):
        assert impact[code]["status"] == "does_not_apply"       # counterexamples are not swept in

    # A draft that records 6/6 for a matching submission is shown the difference; nothing changes automatically.
    ws7 = save(sa6, "P007", "5.0", points={correctness: "6"}, grader="Synthetic Grader C")
    differs = typed(ws7, "guidance_differs")
    assert differs and "3-4" in differs[0]["body"] + str(differs[0]["comparison"])
    assert ws7["current_revision"]["criterion_points"] == {correctness: "6"}

    snap = sa6.post(f"/api/assessments/{sa6.aid}/moderation-snapshots", headers=H).json()
    row = next(line for line in snap["csv_text"].splitlines() if line.startswith("P004"))
    assert f"{correctness}=3-4" in row and ",7.0," in row   # proposal beside the untouched draft
    pre = sa6.get(f"/api/assessments/{sa6.aid}/prerelease").json()
    assert any("not_changing_anything" in k for k in pre["unavailable_evidence"]["external_criteria"])

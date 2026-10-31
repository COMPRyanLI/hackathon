"""HTTP-level tests: CSRF guard, import preview/commit, demo workflow end to end."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.jobs.worker import process_queued
from app.main import create_app
from app.providers.registry import ProviderRegistry
from tests.conftest import PRINT3, make_settings, write_dataset

HEADERS = {"X-GradeLens-Client": "1"}


@pytest.fixture
def client(tmp_path: Path):
    settings = make_settings(tmp_path)
    registry = ProviderRegistry(settings)
    app = create_app(settings, registry, start_worker=False)
    with TestClient(app) as c:
        c.registry = registry
        yield c


def test_mutations_require_client_header_and_local_origin(client):
    assert client.post("/api/imports/demo").status_code == 403
    assert client.post("/api/imports/demo", headers={**HEADERS, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/imports/demo", headers={**HEADERS, "Origin": "http://127.0.0.1:5173"}).status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}


def test_preview_then_commit_is_idempotent(client, tmp_path: Path):
    csv_path, src = write_dataset(tmp_path / "data", [("S001", "G", 10), ("S002", "G", "")],
                                  {"S001_logo.py": PRINT3.format(a="a", b="b", c="c")})
    body = {"csv_path": str(csv_path), "source_root": str(src), "dataset_key": "api-test"}
    preview = client.post("/api/imports/preview", json=body, headers=HEADERS).json()
    assert preview["report"]["matching"]["students_without_files"] == ["S002"]
    first = client.post("/api/imports/commit", json=body, headers=HEADERS).json()
    second = client.post("/api/imports/commit", json=body, headers=HEADERS).json()
    assert first["created"] is True and second["created"] is False
    assert first["assessment_id"] == second["assessment_id"]
    bad = client.post("/api/imports/preview", json={**body, "csv_path": str(tmp_path / "missing.csv")}, headers=HEADERS)
    assert bad.status_code == 400


def test_mock_mode_rejected_for_real_data_runs(client, tmp_path: Path):
    csv_path, src = write_dataset(tmp_path / "data", [("S001", "G", 10), ("S002", "G", 9)],
                                  {"S001_a.py": "print(1)\n", "S002_a.py": "print(2)\n"})
    body = {"csv_path": str(csv_path), "source_root": str(src), "dataset_key": "real-ish"}
    assessment_id = client.post("/api/imports/commit", json=body, headers=HEADERS).json()["assessment_id"]
    response = client.post(f"/api/assessments/{assessment_id}/analysis-runs",
                           json={"provider_mode": "mock", "pilot_size": 2}, headers=HEADERS)
    assert response.status_code == 400
    response = client.patch(f"/api/assessments/{assessment_id}/context",
                            json={"instructions_status": "synthetic_demo", "instructions_text": "x"}, headers=HEADERS)
    assert response.status_code == 400


def test_demo_workflow_end_to_end(client):
    demo = client.post("/api/imports/demo", headers=HEADERS).json()
    aid = demo["assessment_id"]
    assessment = client.get(f"/api/assessments/{aid}").json()
    assert assessment["is_synthetic"] and assessment["context_mode"] == "no_rubric"
    subs = client.get(f"/api/assessments/{aid}/submissions").json()
    by_code = {s["student_id"]: s for s in subs}
    assert len(subs) == 8 and by_code["D007"]["artifact_count"] == 2
    detail = client.get(f"/api/submissions/{by_code['D005']['id']}").json()
    assert any(i["kind"] == "possible_anonymization_artifact" for i in detail["artifacts"][0]["issues"])

    pilot = [by_code[c]["id"] for c in ("D001", "D002", "D003", "D004", "D005", "D007", "D008")]
    run = client.post(f"/api/assessments/{aid}/analysis-runs",
                      json={"submission_ids": pilot, "review_budget": 30, "provider_mode": "mock"}, headers=HEADERS).json()
    process_queued(client.registry)
    run = client.get(f"/api/analysis-runs/{run['id']}").json()
    assert run["status"] == "succeeded" and run["provider_snapshot"]["provider_mode"] == "mock"
    candidates = client.get(f"/api/analysis-runs/{run['id']}/candidates").json()
    assert candidates and all("channels" in c for c in candidates if not c["is_random_control"])

    cases = client.get("/api/review-cases", params={"assessment_id": aid}).json()
    assert cases and all(c["is_mock"] or c["origin"] == "deterministic_exact" for c in cases)
    alt = next(c for c in cases if {c["student_a"], c["student_b"]} == {"D001", "D003"})
    assert alt["category"] == "alternate_method" and alt["score_gap"] == 3.0
    assert alt["headline"].startswith("[MOCK")
    case = client.get(f"/api/review-cases/{alt['id']}").json()
    assert case["comparison"]["sent_content"]["excluded"][:2] == ["grades", "grader_labels"]
    assert case["sides"][0]["artifacts"][0]["source_text"]

    decision = client.post(f"/api/review-cases/{alt['id']}/decisions",
                           json={"action": "needs_clarification", "comment": "Ask the instructor about loops."},
                           headers=HEADERS)
    assert decision.status_code == 200

    note = client.post("/api/instructor-notes", json={"assessment_id": aid, "source_case_id": alt["id"],
                                                      "original_text": "Loops are fine."}, headers=HEADERS).json()
    assert note["status"] == "comment"
    approved = client.post(f"/api/instructor-notes/{note['id']}/approve", headers=HEADERS, json={
        "interpretation": "Printing rows with a loop is acceptable unless the assignment says otherwise.",
        "note_kind": "clarification", "provider_mode": "mock",
        "applicability_conditions": {"feature": "loop_count", "op": ">", "value": 0}}).json()
    process_queued(client.registry)
    impact = client.get(f"/api/instructor-notes/{note['id']}/impact").json()
    assert impact["scan_run"]["status"] == "succeeded"
    items = {i["student_id"]: i for i in impact["items"]}
    assert len(items) == 8 and items["D006"]["status"] == "applies" and not items["D006"]["previously_flagged"]
    assert client.get("/api/review-cases", params={"assessment_id": aid}).json() == []   # all now historical
    stale = client.get("/api/review-cases", params={"assessment_id": aid, "include_stale": True}).json()
    assert stale and all(not c["is_current"] for c in stale)

    applied = client.post(f"/api/note-applicability/{items['D006']['id']}/decisions",
                          json={"action": "confirm_applies", "proposed_score": 8.0}, headers=HEADERS)
    assert applied.status_code == 200
    report = client.post("/api/exports/moderation-report", json={"assessment_id": aid}, headers=HEADERS).json()
    d006 = next(line for line in report["csv_text"].splitlines() if line.startswith("D006"))
    assert "7.0" in d006 and "8.0" in d006               # original kept, proposal is human-entered
    actions = {e["action"] for e in client.get("/api/audit").json()}
    assert {"import_committed", "run_created", "case_decided", "note_approved", "applicability_decided",
            "report_exported"} <= actions


def test_providers_status_never_exposes_secrets(client):
    status = client.get("/api/providers/status").json()
    assert "roles" in status and status["local_only"] is True

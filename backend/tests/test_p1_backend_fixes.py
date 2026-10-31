"""Regression tests: commit-before-response, SA4 rule scoping (scenario 10) and
candidate refresh after a retrieval channel recovers on resume (scenario 23)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.config import PROJECT_ROOT
from app.db.models import AnalysisRun, CandidatePair, InstructorNote, Submission
from app.db.models_p1 import DraftFeedback, FeedbackCard
from app.db.session import init_engine, session_scope
from app.grading.drafts import save_draft
from app.jobs.worker import process_queued
from app.main import create_app
from app.providers.ollama import OllamaClient
from app.providers.registry import ProviderRegistry
from tests.conftest import PRINT3, import_dataset, make_settings, run_pipeline_sync, write_dataset

H = {"X-GradeLens-Client": "1"}


# ---------------------------------------------------------------- commit before response


def test_every_route_commits_before_the_response_is_sent():
    for name in ("routes.py", "routes_p1.py"):
        source = (PROJECT_ROOT / "backend" / "app" / "api" / name).read_text(encoding="utf-8")
        assert "Depends(get_session)" not in source
        assert len(re.findall(r'Depends\(get_session, scope="function"\)', source)) > 10


def test_failed_commit_is_an_error_response_not_a_silent_lost_write(tmp_path: Path, monkeypatch):
    settings = make_settings(tmp_path)
    app = create_app(settings, ProviderRegistry(settings), start_worker=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        assessment_id = client.post("/api/imports/demo", headers=H).json()["assessment_id"]
        real_commit = OrmSession.commit

        def failing_commit(self):  # the request below performs exactly one commit
            raise RuntimeError("simulated disk failure during commit")

        monkeypatch.setattr(OrmSession, "commit", failing_commit)
        response = client.post("/api/instructor-notes", headers=H,
                               json={"assessment_id": assessment_id, "original_text": "must not be lost silently"})
        monkeypatch.setattr(OrmSession, "commit", real_commit)
        assert response.status_code == 500           # with request scope this was already a 200
        with session_scope() as s:
            assert s.scalar(select(func.count(InstructorNote.id))) == 0


def test_saved_draft_is_readable_by_the_very_next_request(tmp_path: Path):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings, ProviderRegistry(settings), start_worker=False)) as client:
        sa3 = client.post("/api/demo/p1", headers=H).json()["assessments"]["SA3"]["assessment_id"]
        sub = client.get(f"/api/assessments/{sa3}/grading-queue").json()[0]["submission_id"]
        for i in range(5):
            current = client.get(f"/api/submissions/{sub}/workspace").json()["current_revision"]
            saved = client.post(f"/api/submissions/{sub}/draft-grades", headers=H, json={
                "total": f"{5 + i}", "grader_label": "G", "expected_revision_id": current["id"] if current else None})
            assert saved.status_code == 200
            after = client.get(f"/api/submissions/{sub}/workspace").json()["current_revision"]
            assert after["id"] == saved.json()["revision"]["id"]


# ---------------------------------------------------------------- scenario 10: SA4 scoping


def test_10_sa4_while_policy_and_warning_only_exception_are_scoped_to_sa4(tmp_path: Path):
    from app.importers.demo_p1 import import_synthetic_p1
    from app.rubrics.service import link_assessment

    settings = make_settings(tmp_path)
    init_engine(settings.effective_database_url)
    with session_scope() as s:
        rubric_id = import_synthetic_p1(s, "tester")["rubric_version_id"]
    code = ('# Author: [NAME]\nfrom cs1lib import *\nN = 5\n\ndef draw():\n    i = 0\n    while i < N:\n'
            '        draw_line(0, i, 100, i)\n        i = i + 1\n\nstart_graphics(draw)\n')
    csv_path, src = write_dataset(tmp_path / "sa4", [("S001", "G", 9), ("S002", "G", 9)],
                                  {"S001_art.py": code,
                                   "S002_art.py": code.replace("    i = 0\n    while i < N:\n", "    for i in range(N):\n")
                                   .replace("        i = i + 1\n", "")},
                                  header=("student_id", "grader", "SA4"))
    from app.importers.service import build_preview, commit_import

    with session_scope() as s:
        preview = build_preview(csv_path, src, r"^(?P<student_id>S[0-9]+)(?:_.*)?\.py$", "syn-sa4", "SA4")
        _, assessment, _ = commit_import(s, preview, "tester", is_synthetic=True)
        link_assessment(s, assessment.id, rubric_id, "SA4", "tester")
        aid = assessment.id
        from app.api.routes_p1 import _criteria

        criteria = _criteria(s, assessment)
        window = next(k for k in criteria if "window_size" in k)
        subs = {s.get(Submission, x).artifacts[0].source_text.count("while"): x
                for x in s.scalars(select(Submission.id).where(Submission.assessment_id == aid))}
        save_draft(s, subs[1], total="8", grader_label="G", actor="t", expected_revision_id=None,
                   criterion_points={window: "1"}, criteria=criteria)
        save_draft(s, subs[0], total="9", grader_label="G", actor="t", expected_revision_id=None, criteria=criteria)
    process_queued(ProviderRegistry(settings))
    with session_scope() as s:
        def types(sid):
            fb = s.scalar(select(DraftFeedback).where(DraftFeedback.submission_id == sid))
            return {c.card_type: c for c in s.scalars(select(FeedbackCard).where(FeedbackCard.feedback_id == fb.id))}
        with_while, without_while = types(subs[1]), types(subs[0])
        assert "exceptional_policy" in with_while and "SA4" in with_while["exceptional_policy"].body
        assert "explicit_allowance" in with_while and "warning" in with_while["explicit_allowance"].title.lower()
        assert "exceptional_policy" not in without_while          # a for loop never triggers the while rule


# ---------------------------------------------------------------- scenario 23: channel recovery


def _flaky_ollama(state: dict) -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3-embedding:0.6b", "digest": "d1"}]})
        if request.url.path == "/api/embed":
            if state["down"]:
                raise httpx.ConnectError("embedding server down")
            body = json.loads(request.content)
            return httpx.Response(200, json={"embeddings": [[float(len(t) % 7 + 1), 1.0, 0.5, float(i)]
                                                            for i, t in enumerate(body["input"])]})
        return httpx.Response(404)
    return OllamaClient("http://ollama.test", transport=httpx.MockTransport(handler))


def test_23_resume_refreshes_candidates_when_a_failed_channel_recovers(tmp_path: Path):
    settings = make_settings(tmp_path, embedding_provider="ollama", embedding_model="qwen3-embedding:0.6b")
    init_engine(settings.effective_database_url)
    state = {"down": True}
    registry = ProviderRegistry(settings, _flaky_ollama(state))
    files = {f"S00{i}_a.py": PRINT3.format(a=f"r{i}", b="mid", c="end" if i % 2 else "fin") for i in range(1, 6)}
    csv_path, src = write_dataset(tmp_path / "d", [(f"S00{i}", "G", 9) for i in range(1, 6)], files)
    _, aid, _ = import_dataset(csv_path, src)
    run_id = run_pipeline_sync(aid, registry, budget=4)
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.status == "partial"
        assert run.progress["retrieval"]["channel_availability"]["source_vector"]["available_artifacts"] == 0
        before = {(p.submission_a_id, p.submission_b_id): p.channels["source_vector"]["status"]
                  for p in s.scalars(select(CandidatePair).where(CandidatePair.run_id == run_id,
                                                                 CandidatePair.is_random_control.is_(False)))}
        assert set(before.values()) == {"unavailable"}
        run.status = "queued"                                    # resume with unchanged settings
    state["down"] = False
    process_queued(registry)
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.progress["retrieval_rebuild"]["mode"] == "refresh_in_place"   # cases existed: kept
        after = [p.channels["source_vector"]["status"] for p in s.scalars(
            select(CandidatePair).where(CandidatePair.run_id == run_id, CandidatePair.is_random_control.is_(False)))]
        assert "unavailable" not in after and len(after) >= len(before)


def test_deterministic_feedback_for_new_saves_runs_before_any_model_job(tmp_path: Path):
    """A slow local model must not delay instant deterministic cards for later saves."""
    from app.importers.demo_p1 import import_synthetic_p1
    from app.jobs.worker import claim_next, execute

    settings = make_settings(tmp_path)
    init_engine(settings.effective_database_url)
    with session_scope() as s:
        sa3 = import_synthetic_p1(s, "tester")["assessments"]["SA3"]["assessment_id"]
        subs = list(s.scalars(select(Submission.id).where(Submission.assessment_id == sa3).order_by(Submission.id)))[:3]
        for sid in subs:
            save_draft(s, sid, total="9", grader_label="G", actor="t", expected_revision_id=None)
    registry = ProviderRegistry(settings)
    order = []
    while (run_id := claim_next()) is not None:
        with session_scope() as s:
            order.append(s.get(AnalysisRun, run_id).kind)
        execute(run_id, registry)
    checks = [i for i, k in enumerate(order) if k == "draft_check"]
    models = [i for i, k in enumerate(order) if k == "draft_model"]
    assert len(checks) == 3 and len(models) >= 3 and max(checks) < min(models)
    with session_scope() as s:
        feedback = list(s.scalars(select(DraftFeedback).where(DraftFeedback.submission_id.in_(subs))))
        assert all(f.status in ("completed", "partial") for f in feedback)
        assert all(f.stages["model_source_analysis"]["status"] == "completed" for f in feedback)
        assert all(f.stages.get("deterministic_ready_ms") is not None for f in feedback)

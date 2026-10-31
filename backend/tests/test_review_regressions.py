"""Regression cases discovered during the independent P0 review."""
from pathlib import Path

import pytest
from sqlalchemy import select

from app.analysis.context import current_guidance, guidance_block
from app.analysis.reviewer import run_reviewer
from app.analysis.analyst import run_analyst
from app.db.models import (AnalysisRun, AssessmentVersion, InstructorNoteVersion,
                           NoteApplicability, PairComparison, ReviewCase, Submission)
from app.db.session import session_scope
from app.guidance import notes
from app.jobs.worker import process_queued
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry
from tests.conftest import ScriptedProvider, import_dataset, run_pipeline_sync, write_dataset
from tests.test_api import client, HEADERS


@pytest.fixture
def sample(tmp_path, db):
    csv, src = write_dataset(tmp_path / "input", [("S001", "G", 10), ("S002", "G", 8)], {
        "S001_a.py": 'for x in ["a", "b"]:\n    print(x)\n',
        "S002_a.py": 'print("a")\nprint("b")\n',
    })
    return import_dataset(csv, src)[1]


def comparison_response(relation="apparently_equivalent"):
    return {"relation": relation, "scope": "component", "requirement_basis": "unavailable",
            "shared_observations": [], "material_differences": [], "contradicted_analyst_claims": [],
            "alternative_explanations": [], "missing_context": [], "clarification_question": "",
            "suggested_category": "alternate_method", "coverage_status": "full_source_seen"}


def test_unsubstantiated_equivalence_is_downgraded(sample, registry):
    registry._generation["reviewer"] = ScriptedProvider(lambda *args: comparison_response())
    with session_scope() as s:
        subs = list(s.scalars(select(Submission).order_by(Submission.id)))
        result = run_reviewer(s, registry, *subs, s.get(AssessmentVersion, sample))
        assert result.result["relation"] == "insufficient_evidence"
        assert result.result["equivalence_level"] is None


def test_context_edit_retires_old_cases_and_blocks_old_run_resume(client):
    aid = client.post("/api/imports/demo", headers=HEADERS).json()["assessment_id"]
    run = client.post(f"/api/assessments/{aid}/analysis-runs", headers=HEADERS,
                      json={"provider_mode": "mock", "pilot_size": 3, "review_budget": 2}).json()
    process_queued(client.registry)
    assert client.get(f"/api/review-cases?assessment_id={aid}").json()
    with session_scope() as s:
        s.get(AnalysisRun, run["id"]).status = "partial"
    response = client.patch(f"/api/assessments/{aid}/context", headers=HEADERS,
                            json={"instructions_text": "A newly clarified synthetic assignment."})
    assert response.status_code == 200
    assert client.get(f"/api/review-cases?assessment_id={aid}").json() == []
    assert client.post(f"/api/analysis-runs/{run['id']}/resume", headers=HEADERS).status_code == 409


def test_zero_pair_budget_means_no_reviewer_calls(sample, registry):
    rid = run_pipeline_sync(sample, registry, budget=0)
    with session_scope() as s:
        run = s.get(AnalysisRun, rid)
        assert run.coverage["pairs_selected_for_review"] == 0
        assert run.coverage["pairs_reviewed"] == 0


def test_prospective_note_is_not_a_historical_grading_rule(sample):
    with session_scope() as s:
        v = notes.create_note(s, sample, "For future work", "head-ta", "Require loops next time.")
        notes.approve(s, v.id, {"note_kind": "prospective_policy"}, "head-ta")
        assert current_guidance(s, sample) == []


def test_guidance_prompt_preserves_scope_and_conditions(sample):
    with session_scope() as s:
        v = notes.create_note(s, sample, "One function", "head-ta", "Accept this approach.")
        v, _ = notes.approve(s, v.id, {"note_kind": "clarification", "scope_component": "draw_border",
            "scope_requirement": "edge shape", "exceptions": "Not filled shapes",
            "applicability_conditions": {"feature": "loop_count", "op": ">", "value": 0}}, "head-ta")
        text = guidance_block([v])
        assert "draw_border" in text and "edge shape" in text and "loop_count" in text


@pytest.mark.parametrize("restriction", [{"exceptions": "Except loops over supplied starter data"},
                                        {"scope_component": "draw_border"}])
def test_condition_does_not_bypass_scope_or_exceptions(sample, registry, restriction):
    with session_scope() as s:
        v = notes.create_note(s, sample, "Rule with restrictions", "head-ta", "Loops may be accepted.")
        v, _ = notes.approve(s, v.id, {"note_kind": "clarification", **restriction,
            "applicability_conditions": {"feature": "loop_count", "op": ">", "value": 0}}, "head-ta")
        vid = v.id
    process_queued(registry)
    with session_scope() as s:
        rows = list(s.scalars(select(NoteApplicability).where(NoteApplicability.note_version_id == vid)))
        assert rows and all(row.status == "uncertain" for row in rows)


def test_impact_scan_timeout_is_retryable(sample, registry):
    registry._generation["reviewer"] = ScriptedProvider(
        lambda *args: ProviderError("timeout", "temporary failure"))
    with session_scope() as s:
        v = notes.create_note(s, sample, "Rule", "head-ta", "Inspect the supplied output.")
        v, run = notes.approve(s, v.id, {"note_kind": "clarification"}, "head-ta", use_model_scan=True)
        rid, vid = run.id, v.id
    process_queued(registry)
    with session_scope() as s:
        assert s.get(AnalysisRun, rid).status == "partial"
    provider = ScriptedProvider(lambda *args: {"status": "uncertain", "rationale": "Needs human review", "evidence": []})
    registry._generation["reviewer"] = provider
    with session_scope() as s:
        s.get(AnalysisRun, rid).status = "queued"
    process_queued(registry)
    assert len(provider.calls) == 2
    with session_scope() as s:
        assert s.get(AnalysisRun, rid).status == "succeeded"


def test_generation_settings_invalidate_analyst_cache(sample, registry):
    with session_scope() as s:
        sub = s.scalar(select(Submission))
        a = s.get(AssessmentVersion, sample)
        first = run_analyst(s, registry, sub.artifacts[0], a)
        changed = ProviderRegistry(registry.settings.model_copy(update={"analyst_max_output_tokens": 4096}))
        second = run_analyst(s, changed, sub.artifacts[0], a)
        assert first.id != second.id


def test_pipeline_resume_after_settings_change_requires_new_run(client):
    aid = client.post("/api/imports/demo", headers=HEADERS).json()["assessment_id"]
    run = client.post(f"/api/assessments/{aid}/analysis-runs", headers=HEADERS,
                      json={"pilot_size": 2, "review_budget": 1}).json()
    process_queued(client.registry)
    with session_scope() as s:
        s.get(AnalysisRun, run["id"]).status = "partial"
    client.registry.settings.reviewer_max_output_tokens += 10
    assert client.post(f"/api/analysis-runs/{run['id']}/resume", headers=HEADERS).status_code == 409


def test_invalid_condition_type_is_rejected_on_approval(sample):
    with session_scope() as s:
        v = notes.create_note(s, sample, "Rule", "head-ta", "Check loops.")
        with pytest.raises(notes.NoteError, match="condition"):
            notes.approve(s, v.id, {"note_kind": "clarification",
                "applicability_conditions": {"feature": "loop_count", "op": ">", "value": "many"}}, "head-ta")


def test_embedding_calls_do_not_hold_a_database_write_lock(sample, registry, settings):
    import sqlite3
    from app.providers.mock import MockEmbeddingProvider
    class ProbeEmbedding(MockEmbeddingProvider):
        def embed(self, texts, representation_version):
            with sqlite3.connect(settings.effective_database_url.removeprefix("sqlite:///"), timeout=0.2) as conn:
                conn.execute("insert into audit_events (at, actor, action, entity_type, detail) "
                             "values (datetime('now'), 'probe', 'probe', 'probe', '{}')")
            return super().embed(texts, representation_version)
    registry._embedding = ProbeEmbedding()
    rid = run_pipeline_sync(sample, registry, budget=1)
    with session_scope() as s:
        run = s.get(AnalysisRun, rid)
        assert run.status == "succeeded", run.error


def test_stale_grade_proposals_are_not_exported_as_current(sample, registry, settings):
    import csv
    import io
    from app.db.models import ReviewDecision
    from app.exports.report import build_report
    run_pipeline_sync(sample, registry, budget=1)
    with session_scope() as s:
        case = s.scalar(select(ReviewCase))
        case.is_current = False
        s.add(ReviewDecision(case_id=case.id, action="possible_recording_error", comment="Old guidance",
                             proposed_score_a=9.5, actor="head-ta"))
    with session_scope() as s:
        report = build_report(s, sample, settings, "head-ta")
        rows = list(csv.DictReader(io.StringIO(report["csv_text"])))
        assert all(not row["human_proposed_score"] for row in rows)


def test_run_filter_does_not_mix_runs(client):
    aid = client.post("/api/imports/demo", headers=HEADERS).json()["assessment_id"]
    ids = []
    for _ in range(2):
        run = client.post(f"/api/assessments/{aid}/analysis-runs", headers=HEADERS,
                          json={"provider_mode": "mock", "pilot_size": 3, "review_budget": 2}).json()
        ids.append(run["id"])
        process_queued(client.registry)
    rows = client.get(f"/api/review-cases?assessment_id={aid}&include_stale=true&run_id={ids[0]}").json()
    assert rows and all(row["run_id"] == ids[0] for row in rows)

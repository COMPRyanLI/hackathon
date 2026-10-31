"""Acceptance tests 15 and 16: resumable jobs, honest coverage, untouched gradebook."""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import AnalysisRun, ReviewCase, ReviewDecision, SubmissionEvidence
from app.db.session import session_scope
from app.exports.report import build_report, escape_cell
from app.jobs.worker import mark_interrupted, process_queued
from tests.conftest import PRINT3, import_dataset, run_pipeline_sync, write_dataset


def _dataset(tmp_path: Path):
    files = {f"S00{i}_logo.py": PRINT3.format(a=f"r{i}", b="mid", c="end" if i % 2 else "fin") for i in range(1, 7)}
    rows = [(f"S00{i}", "G", 10 if i % 2 else 8) for i in range(1, 7)]
    return write_dataset(tmp_path / "d", rows, files)


def test_interrupted_run_resumes_and_skips_cached_items(tmp_path: Path, db, registry):
    csv_path, src = _dataset(tmp_path)
    _, assessment_id, _ = import_dataset(csv_path, src)
    with session_scope() as s:
        run = AnalysisRun(assessment_id=assessment_id, kind="pipeline", status="running", actor="t",
                          params={"submission_ids": None, "review_budget": 4})
        s.add(run)
        s.flush()
        run_id = run.id
    assert mark_interrupted() == 1                      # what startup does after a crash
    with session_scope() as s:
        assert s.get(AnalysisRun, run_id).status == "interrupted"
        s.get(AnalysisRun, run_id).status = "queued"    # what POST /resume does
    process_queued(registry)
    analyst = registry.generation("analyst")
    first_calls = len([c for c in analyst.calls if c["role"] == "analyst"])
    assert first_calls == 6
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.status == "succeeded" and run.attempts == 1
        assert run.coverage["exhaustive"] is False
        assert run.coverage["pairs_selected_for_review"] <= 4
        assert any("Budget-capped" in n for n in run.coverage["notes"])
        assert any("not exhaustive" in n for n in run.coverage["notes"])
        s.get(AnalysisRun, run_id).status = "queued"    # resume again: cache-valid items are reused
    process_queued(registry)
    assert len([c for c in analyst.calls if c["role"] == "analyst"]) == first_calls
    with session_scope() as s:
        assert s.scalar(select(func.count(SubmissionEvidence.id))) == 6
        assert s.scalar(select(func.count(ReviewCase.id)).where(ReviewCase.run_id == run_id)) == \
            s.get(AnalysisRun, run_id).coverage["cases_created"]


def test_cancel_request_stops_between_items(tmp_path: Path, db, registry):
    csv_path, src = _dataset(tmp_path)
    _, assessment_id, _ = import_dataset(csv_path, src)
    with session_scope() as s:
        s.add(AnalysisRun(assessment_id=assessment_id, kind="pipeline", status="queued", actor="t",
                          cancel_requested=True, params={}))
    process_queued(registry)
    with session_scope() as s:
        run = s.scalar(select(AnalysisRun))
        assert run.status == "cancelled" and run.finished_at is not None


def test_original_gradebook_untouched_and_proposed_scores_only_from_humans(tmp_path: Path, db, registry, settings):
    csv_path, src = _dataset(tmp_path)
    before = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    src_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir()}
    _, assessment_id, _ = import_dataset(csv_path, src)
    run_pipeline_sync(assessment_id, registry, budget=6)
    with session_scope() as s:
        report = build_report(s, assessment_id, settings, "tester")
        assert "human_proposed_score" in report["csv_text"]
        rows = [line.split(",") for line in report["csv_text"].strip().splitlines()[1:]]
        assert all(row[10] == "" for row in rows)          # no decisions yet -> no proposals
        case = s.scalar(select(ReviewCase))
        s.add(ReviewDecision(case_id=case.id, action="possible_recording_error", comment="=HYPERLINK(1)",
                             proposed_score_a=9.5, actor="instructor"))
    with session_scope() as s:
        report = build_report(s, assessment_id, settings, "tester")
        assert "9.5" in report["csv_text"] and "case" in report["csv_text"]
        assert Path(report["csv_path"]).is_file() and Path(report["json_path"]).is_file()
        assert str(settings.private_dir) in report["csv_path"]
    assert hashlib.sha256(csv_path.read_bytes()).hexdigest() == before
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir()} == src_before


def test_formula_leading_cells_are_escaped():
    assert escape_cell("=1+1") == "'=1+1"
    assert escape_cell("@SUM(A1)") == "'@SUM(A1)"
    assert escape_cell("+cmd") == "'+cmd"
    assert escape_cell("Grader K") == "Grader K"
    assert escape_cell(None) == ""


def test_retrieval_and_reviewer_ablation_run_on_synthetic_labels(settings):
    import json as _json

    from app.config import PROJECT_ROOT
    from app.db.session import init_engine
    from app.evaluation.ablation import _units, retrieval_ablation, reviewer_ablation
    from app.importers.demo import import_synthetic_demo
    from app.providers.registry import ProviderRegistry

    init_engine(settings.effective_database_url)
    labels = _json.loads((PROJECT_ROOT / "fixtures/synthetic/demo/eval_labels.json").read_text(encoding="utf-8"))
    registry = ProviderRegistry(settings)
    with session_scope() as s:
        _, assessment, _ = import_synthetic_demo(s, "tester")
        units, codes = _units(s, registry, assessment, prepare=True)
        result = retrieval_ablation(units, codes, labels["useful_pairs"], budget=10, top_k=5)
        assert set(result) == {"lexical_ast_only", "source_vectors_only", "hybrid_with_summary_vectors"}
        assert all(0.0 <= r["recall_at_budget"] <= 1.0 for r in result.values())
        reviewer = reviewer_ablation(s, registry, assessment, codes, labels["useful_pairs"][:1])
        assert reviewer["pairs"][0]["summaries_plus_source"]["relation"] == "apparently_equivalent"
        assert reviewer["pairs"][0]["source_only"]["is_mock"] is True


def test_newer_run_supersedes_older_current_case_for_same_pair(tmp_path: Path, db, registry):
    csv_path, src = _dataset(tmp_path)
    _, assessment_id, _ = import_dataset(csv_path, src)
    first = run_pipeline_sync(assessment_id, registry, budget=30)
    second = run_pipeline_sync(assessment_id, registry, budget=30)
    with session_scope() as s:
        current = list(s.scalars(select(ReviewCase).where(ReviewCase.is_current.is_(True))))
        pairs = [(c.submission_a_id, c.submission_b_id, c.origin) for c in current]
        assert len(pairs) == len(set(pairs)) and all(c.run_id == second for c in current)
        old = list(s.scalars(select(ReviewCase).where(ReviewCase.run_id == first)))
        assert old and all(not c.is_current and f"run {second}" in c.stale_reason for c in old)


def test_no_database_write_lock_is_held_during_model_calls(tmp_path: Path, db, registry, settings):
    import sqlite3

    from app.providers.mock import _FALLBACKS
    from tests.conftest import ScriptedProvider

    db_path = settings.effective_database_url.removeprefix("sqlite:///")

    def respond(role, messages, s_):
        conn = sqlite3.connect(db_path, timeout=0.2)   # fails fast if a writer holds the lock
        conn.execute("insert into audit_events (at, actor, action, entity_type, detail) "
                     "values (datetime('now'), 'probe', 'probe', 'probe', '{}')")
        conn.commit()
        conn.close()
        return _FALLBACKS[role](s_.mock_context or {})

    probe = ScriptedProvider(respond)
    registry._generation["analyst"] = probe
    registry._generation["reviewer"] = probe
    csv_path, src = _dataset(tmp_path)
    _, assessment_id, _ = import_dataset(csv_path, src)
    run_id = run_pipeline_sync(assessment_id, registry, budget=6)
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.status == "succeeded", run.error
        assert run.progress["analyst"]["done"] == 6 and run.progress["review"]["done"] >= 1

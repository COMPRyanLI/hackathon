"""Acceptance tests 12 and 13: notes have no effect until adopted; adoption scans
every in-scope submission; superseding/conflicts invalidate judgments, keep history."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import (
    InstructorNoteVersion,
    NoteApplicability,
    PairComparison,
    ReviewCase,
    ReviewDecision,
    Student,
    Submission,
)
from app.db.session import init_engine, session_scope
from app.guidance import notes
from app.importers.demo import demo_manifest, import_synthetic_demo
from app.jobs.worker import process_queued
from app.providers.registry import ProviderRegistry
from tests.conftest import run_pipeline_sync, submission_ids_for

LOOP_NOTE = ("A solution is not required to avoid loops: printing the rows with a loop is acceptable unless the "
             "assignment says otherwise. Evaluate the specified output requirements separately. This note does not "
             "establish a numeric deduction for other issues.")


@pytest.fixture
def demo(settings):
    init_engine(settings.effective_database_url)
    with session_scope() as s:
        _, assessment, _ = import_synthetic_demo(s, "tester")
        assessment_id = assessment.id
    registry = ProviderRegistry(settings)
    pilot = submission_ids_for(assessment_id, demo_manifest()["suggested_pilot"])
    run_pipeline_sync(assessment_id, registry, submission_ids=pilot, budget=8)
    return assessment_id, registry


def _statuses(version_id: int) -> dict[str, NoteApplicability]:
    with session_scope() as s:
        rows = s.execute(select(NoteApplicability, Student.code)
                         .join(Submission, Submission.id == NoteApplicability.submission_id)
                         .join(Student, Student.id == Submission.student_id)
                         .where(NoteApplicability.note_version_id == version_id)).all()
        return {code: row for row, code in rows}


def test_comments_and_drafts_have_no_grading_effect(demo):
    assessment_id, _ = demo
    with session_scope() as s:
        current_before = s.scalar(select(func.count(ReviewCase.id)).where(ReviewCase.is_current.is_(True)))
        version = notes.create_note(s, assessment_id, "Loops seem fine?", "instructor")
        notes.update_draft(s, version.id, {"interpretation": LOOP_NOTE, "note_kind": "clarification"}, "instructor")
    with session_scope() as s:
        assert s.scalar(select(func.count(NoteApplicability.id))) == 0
        assert s.scalar(select(func.count(ReviewCase.id)).where(ReviewCase.is_current.is_(True))) == current_before
        assert current_before > 0
        from app.analysis.context import current_guidance
        assert current_guidance(s, assessment_id) == []


def test_adopted_note_scans_every_submission_including_unflagged(demo):
    assessment_id, registry = demo
    from app.db.models import ScoreRecord
    with session_scope() as s:
        scores_before = sorted((r.id, r.raw_cell, r.score) for r in s.scalars(select(ScoreRecord)))
    with session_scope() as s:
        version = notes.create_note(s, assessment_id, "Is a loop OK?", "instructor", interpretation=LOOP_NOTE)
        approved, run = notes.approve(s, version.id, {
            "note_kind": "clarification",
            "applicability_conditions": {"feature": "loop_count", "op": ">", "value": 0}}, "instructor")
        version_id = approved.id
        stale = s.scalar(select(func.count(ReviewCase.id)).where(ReviewCase.is_current.is_(False)))
        assert stale > 0                                   # dependent judgments no longer current
    process_queued(registry)
    rows = _statuses(version_id)
    with session_scope() as s:
        total = s.scalar(select(func.count(Submission.id)).where(Submission.assessment_id == assessment_id))
    assert len(rows) == total == 8                         # scope-wide, not only flagged work
    assert rows["D003"].status == "applies"
    assert rows["D006"].status == "applies" and rows["D006"].previously_flagged is False
    assert rows["D001"].status == "does_not_apply"
    assert rows["D005"].status == "uncertain"              # AST unavailable after anonymization damage
    assert all(r.method == "deterministic_condition" for r in rows.values())
    with session_scope() as s:
        # No score was changed anywhere.
        assert sorted((r.id, r.raw_cell, r.score) for r in s.scalars(select(ScoreRecord))) == scores_before


def test_overlapping_approved_notes_conflict_and_superseding_keeps_history(demo):
    assessment_id, registry = demo
    with session_scope() as s:
        case = s.scalar(select(ReviewCase).where(ReviewCase.is_current.is_(True)))
        s.add(ReviewDecision(case_id=case.id, action="needs_clarification", comment="ask", actor="instructor"))
        first = notes.create_note(s, assessment_id, "loops", "instructor", interpretation=LOOP_NOTE)
        first, _ = notes.approve(s, first.id, {"note_kind": "clarification"}, "instructor")
        first_id, case_id = first.id, case.id
    process_queued(registry)

    with session_scope() as s:
        second = notes.create_note(s, assessment_id, "letters", "instructor",
                                   interpretation="Exact letter shapes are not assessed.")
        with pytest.raises(notes.NoteConflict) as conflict:
            notes.approve(s, second.id, {"note_kind": "prospective_policy"}, "instructor")
        assert conflict.value.conflicts[0]["note_version_id"] == first_id
        # Explicit resolution: the instructor confirms both notes apply.
        notes.approve(s, second.id, {"note_kind": "prospective_policy", "coexists_with": [first_id]}, "instructor")

    with session_scope() as s:
        successor = notes.supersede(s, first_id, {"interpretation": LOOP_NOTE + " (revised)"}, "instructor")
        assert successor.status == "draft" and successor.version == 2
        assert s.get(InstructorNoteVersion, first_id).status == "approved"   # unchanged until approval
        successor, _ = notes.approve(s, successor.id, {}, "instructor")
        successor_id = successor.id
    with session_scope() as s:
        old = s.get(InstructorNoteVersion, first_id)
        assert old.status == "superseded" and old.superseded_at is not None
        assert all(not r.is_current for r in s.scalars(
            select(NoteApplicability).where(NoteApplicability.note_version_id == first_id)))
        assert s.get(ReviewCase, case_id).is_current is False
        assert "Guidance changed" in s.get(ReviewCase, case_id).stale_reason
        assert s.scalar(select(func.count(ReviewDecision.id)).where(ReviewDecision.case_id == case_id)) == 1
        assert s.scalar(select(func.count(PairComparison.id)).where(PairComparison.is_current.is_(True))) == 0
        assert s.get(InstructorNoteVersion, successor_id).supersedes_version_id == first_id


def test_approved_guidance_is_immutable_and_requires_kind(demo):
    assessment_id, _ = demo
    with session_scope() as s:
        version = notes.create_note(s, assessment_id, "x", "instructor", interpretation="Some guidance.")
        with pytest.raises(notes.NoteError, match="clarification"):
            notes.approve(s, version.id, {}, "instructor")
        notes.approve(s, version.id, {"note_kind": "retrospective_review"}, "instructor")
        with pytest.raises(notes.NoteError, match="immutable"):
            notes.update_draft(s, version.id, {"interpretation": "changed"}, "instructor")


def test_rerun_after_guidance_change_produces_new_current_comparisons(demo):
    assessment_id, registry = demo
    with session_scope() as s:
        version = notes.create_note(s, assessment_id, "x", "instructor", interpretation=LOOP_NOTE)
        notes.approve(s, version.id, {"note_kind": "clarification"}, "instructor")
    process_queued(registry)
    pilot = submission_ids_for(assessment_id, ["D001", "D003"])
    run_pipeline_sync(assessment_id, registry, submission_ids=pilot)
    with session_scope() as s:
        current = list(s.scalars(select(PairComparison).where(PairComparison.is_current.is_(True))))
        assert current and all(c.guidance_versions for c in current)

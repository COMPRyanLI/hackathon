"""Draft grade revisions: immutable, idempotent, optimistic-concurrency saves.

A save persists the revision and enqueues its feedback check in the same
transaction (a persistent outbox), then returns. Model availability never
affects whether a draft is saved. Imported totals stay in score_records;
creating a draft from one is an explicit, recorded action.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit
from app.db.models import AnalysisRun, AssessmentVersion, ScoreRecord, Submission
from app.db.models_p1 import DraftFeedback, DraftGradeRevision, GradingEvent
from app.rubrics.engine import PointError, fmt, to_decimal

MAX_TOTAL = Decimal("100")


class DraftError(ValueError):
    pass


class DraftConflict(DraftError):
    def __init__(self, message: str, current: DraftGradeRevision | None):
        super().__init__(message)
        self.current = current


def current_revision(session: Session, submission_id: int) -> DraftGradeRevision | None:
    return session.scalar(select(DraftGradeRevision).where(DraftGradeRevision.submission_id == submission_id)
                          .order_by(DraftGradeRevision.revision_number.desc()).limit(1))


def _normalise_payload(total: Any, criterion_points: dict[str, Any] | None, reasons: list[Any] | None,
                       note: str, criteria: dict[str, Any] | None) -> tuple[str, dict[str, str], list[Any]]:
    try:
        value = to_decimal(total)
    except PointError as exc:
        raise DraftError(f"Total: {exc}") from exc
    if value < 0 or value > MAX_TOTAL:
        raise DraftError("Total must be between 0 and 100")
    points: dict[str, str] = {}
    for key, raw in (criterion_points or {}).items():
        if raw in (None, ""):
            continue
        if criteria is not None and key not in criteria:
            raise DraftError(f"Unknown criterion {key!r} for this assignment's rubric")
        try:
            p = to_decimal(raw)
        except PointError as exc:
            raise DraftError(f"{key}: {exc}") from exc
        maximum = criteria[key].max_points if criteria is not None else None
        if p < 0 or (maximum is not None and p > to_decimal(maximum)):
            raise DraftError(f"{key}: {fmt(p)} is outside 0-{maximum}")
        points[key] = fmt(p)
    return fmt(value), points, [str(r).strip() for r in (reasons or []) if str(r).strip()]


def save_draft(session: Session, submission_id: int, *, total: Any, grader_label: str, actor: str,
               expected_revision_id: int | None, criterion_points: dict[str, Any] | None = None,
               deduction_reasons: list[Any] | None = None, note: str = "", idempotency_key: str | None = None,
               origin: str = "app_save", historical_score_record_id: int | None = None,
               criteria: dict[str, Any] | None = None) -> tuple[DraftGradeRevision, bool, DraftFeedback | None]:
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise DraftError("Submission not found")
    total_s, points, reasons = _normalise_payload(total, criterion_points, deduction_reasons, note, criteria)
    payload_hash = audit.stable_hash([submission_id, total_s, points, reasons, note.strip(), grader_label, origin,
                                      historical_score_record_id])
    if idempotency_key:
        existing = session.scalar(select(DraftGradeRevision).where(DraftGradeRevision.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.payload_hash != payload_hash or existing.submission_id != submission_id:
                raise DraftConflict("Idempotency key reused with a different draft", existing)
            feedback = session.scalar(select(DraftFeedback).where(DraftFeedback.revision_id == existing.id)
                                      .order_by(DraftFeedback.id.desc()).limit(1))
            return existing, False, feedback
    current = current_revision(session, submission_id)
    current_id = current.id if current else None
    if expected_revision_id != current_id:
        session.add(GradingEvent(actor=actor, event_type="draft_save_conflict", assessment_id=submission.assessment_id,
                                 submission_id=submission_id, revision_id=current_id,
                                 detail={"expected": expected_revision_id, "current": current_id}))
        raise DraftConflict("This draft changed since you opened it. Review the current revision, then save again.",
                            current)
    revision = DraftGradeRevision(
        assessment_id=submission.assessment_id, submission_id=submission_id,
        revision_number=(current.revision_number + 1) if current else 1, parent_revision_id=current_id,
        total=total_s, criterion_points=points, deduction_reasons=reasons, note=note.strip(),
        grader_label=grader_label.strip() or actor, actor=actor, origin=origin,
        historical_score_record_id=historical_score_record_id, idempotency_key=idempotency_key or None,
        payload_hash=payload_hash)
    session.add(revision)
    session.flush()
    feedback = enqueue_check(session, revision, actor, trigger="draft_save")
    session.add(GradingEvent(actor=actor, event_type="draft_saved", assessment_id=submission.assessment_id,
                             submission_id=submission_id, revision_id=revision.id,
                             detail={"total": total_s, "criterion_points": bool(points), "origin": origin,
                                     "feedback_id": feedback.id}))
    audit.record(session, actor, "draft_saved", "draft_grade_revision", revision.id, submission_id=submission_id,
                 revision_number=revision.revision_number, origin=origin)
    return revision, True, feedback


def enqueue_check(session: Session, revision: DraftGradeRevision, actor: str, trigger: str,
                  provider_mode: str | None = None) -> DraftFeedback:
    """Queue a bounded check; superseded queued checks for older revisions are coalesced away."""
    older = list(session.scalars(select(DraftFeedback).where(
        DraftFeedback.submission_id == revision.submission_id, DraftFeedback.status == "queued",
        DraftFeedback.revision_id != revision.id)))
    for feedback in older:
        feedback.status = "superseded"
        feedback.error = f"Superseded by revision {revision.revision_number} before it ran."
        if feedback.run_id:
            session.execute(update(AnalysisRun).where(AnalysisRun.id == feedback.run_id,
                                                      AnalysisRun.status == "queued")
                            .values(status="cancelled", cancel_requested=True, error=feedback.error))
    assessment = session.get(AssessmentVersion, revision.assessment_id)
    mode = provider_mode or ("mock" if assessment.is_synthetic else "configured")
    feedback = DraftFeedback(revision_id=revision.id, submission_id=revision.submission_id,
                             assessment_id=revision.assessment_id, trigger=trigger, status="queued")
    session.add(feedback)
    session.flush()
    run = AnalysisRun(assessment_id=revision.assessment_id, kind="draft_check", status="queued", actor=actor,
                      params={"revision_id": revision.id, "feedback_id": feedback.id, "provider_mode": mode,
                              "trigger": trigger})
    session.add(run)
    session.flush()
    feedback.run_id = run.id
    return feedback


def draft_from_historical(session: Session, submission_id: int, actor: str, expected_revision_id: int | None,
                          grader_label: str = "") -> tuple[DraftGradeRevision, bool, DraftFeedback | None]:
    """Explicitly start a working draft from the imported total. The original record is untouched."""
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise DraftError("Submission not found")
    assessment = session.get(AssessmentVersion, submission.assessment_id)
    record = session.scalar(select(ScoreRecord).where(
        ScoreRecord.batch_id == assessment.batch_id, ScoreRecord.student_id == submission.student_id,
        ScoreRecord.assessment_column == assessment.name))
    if record is None or record.score is None:
        raise DraftError("No recorded total exists for this submission; enter a draft total instead.")
    revision, created, feedback = save_draft(
        session, submission_id, total=record.raw_cell, grader_label=grader_label or (record.grader_label or actor),
        actor=actor, expected_revision_id=expected_revision_id, origin="from_historical_record",
        historical_score_record_id=record.id,
        note="Working draft created explicitly from the imported historical total (no event time is implied).")
    if created:
        session.add(GradingEvent(actor=actor, event_type="draft_from_historical", assessment_id=assessment.id,
                                 submission_id=submission_id, revision_id=revision.id,
                                 detail={"score_record_id": record.id, "raw_cell": record.raw_cell}))
    return revision, created, feedback


def revision_view(revision: DraftGradeRevision | None) -> dict[str, Any] | None:
    if revision is None:
        return None
    return {"id": revision.id, "submission_id": revision.submission_id, "revision_number": revision.revision_number,
            "parent_revision_id": revision.parent_revision_id, "total": revision.total,
            "criterion_points": revision.criterion_points, "deduction_reasons": revision.deduction_reasons,
            "note": revision.note, "grader_label": revision.grader_label, "actor": revision.actor,
            "origin": revision.origin, "historical_score_record_id": revision.historical_score_record_id,
            "created_at": revision.created_at.isoformat() if revision.created_at else None}

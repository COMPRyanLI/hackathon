"""Instructor notes: comment -> draft interpretation -> explicit approval ->
guidance version -> scope-wide applicability scan -> human decisions.

Comments and drafts have no effect on judgments. Approval is a human action
exposed only through the API; model code paths never call it. Approving or
superseding guidance marks dependent comparisons and cases as not current
while preserving them (and their decisions) as history.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit
from app.analysis.context import current_guidance
from app.db.models import (
    AnalysisRun,
    InstructorNote,
    InstructorNoteVersion,
    NoteApplicability,
    PairComparison,
    ReviewCase,
    Submission,
)
from app.guidance.conditions import validate_condition

NOTE_KINDS = ("clarification", "prospective_policy", "retrospective_review")
EDITABLE_FIELDS = ("original_text", "interpretation", "note_kind", "scope_component", "scope_requirement",
                   "examples", "applicability_conditions", "exceptions", "rationale", "scoring_implication")


class NoteError(ValueError):
    pass


class NoteConflict(NoteError):
    def __init__(self, message: str, conflicts: list[dict[str, Any]]):
        super().__init__(message)
        self.conflicts = conflicts


def create_note(session: Session, assessment_id: int, original_text: str, actor: str,
                interpretation: str = "", source_case_id: int | None = None) -> InstructorNoteVersion:
    if not original_text.strip():
        raise NoteError("Note text is required")
    note = InstructorNote(assessment_id=assessment_id)
    session.add(note)
    session.flush()
    version = InstructorNoteVersion(note_id=note.id, version=1,
                                    status="draft" if interpretation.strip() else "comment",
                                    original_text=original_text, interpretation=interpretation,
                                    source_case_id=source_case_id, actor=actor)
    session.add(version)
    session.flush()
    audit.record(session, actor, "note_created", "instructor_note_version", version.id,
                 note_id=note.id, status=version.status)
    return version


def _apply_fields(version: InstructorNoteVersion, fields: dict[str, Any]) -> None:
    for name in EDITABLE_FIELDS:
        if name in fields and fields[name] is not None:
            setattr(version, name, fields[name])
    if version.note_kind is not None and version.note_kind not in NOTE_KINDS:
        raise NoteError(f"note_kind must be one of {', '.join(NOTE_KINDS)}")
    if version.applicability_conditions:
        errors = validate_condition(version.applicability_conditions)
        if errors:
            raise NoteError("Invalid applicability conditions: " + "; ".join(errors))


def update_draft(session: Session, version_id: int, fields: dict[str, Any], actor: str) -> InstructorNoteVersion:
    version = session.get(InstructorNoteVersion, version_id)
    if version is None:
        raise NoteError("Note version not found")
    if version.status not in ("comment", "draft"):
        raise NoteError("Approved or superseded guidance is immutable; supersede it instead")
    _apply_fields(version, fields)
    if version.interpretation.strip():
        version.status = "draft"
    audit.record(session, actor, "note_draft_updated", "instructor_note_version", version.id,
                 fields=sorted(k for k in fields if k in EDITABLE_FIELDS))
    return version


def _assessment_id(session: Session, version: InstructorNoteVersion) -> int:
    return session.get(InstructorNote, version.note_id).assessment_id


def _overlaps(a: InstructorNoteVersion, b: InstructorNoteVersion) -> bool:
    def within(x: str, y: str) -> bool:
        return not x or not y or x.strip().lower() == y.strip().lower()
    return within(a.scope_component, b.scope_component) and within(a.scope_requirement, b.scope_requirement)


def invalidate_current_judgments(session: Session, assessment_id: int, reason: str) -> dict[str, int]:
    sub_ids = select(Submission.id).where(Submission.assessment_id == assessment_id)
    comparisons = session.execute(
        update(PairComparison)
        .where(PairComparison.submission_a_id.in_(sub_ids), PairComparison.is_current.is_(True))
        .values(is_current=False, stale_reason=reason))
    cases = session.execute(
        update(ReviewCase)
        .where(ReviewCase.assessment_id == assessment_id, ReviewCase.is_current.is_(True))
        .values(is_current=False, stale_reason=reason))
    return {"comparisons": comparisons.rowcount or 0, "cases": cases.rowcount or 0}


def approve(session: Session, version_id: int, fields: dict[str, Any], actor: str,
            use_model_scan: bool = False) -> tuple[InstructorNoteVersion, AnalysisRun]:
    version = session.get(InstructorNoteVersion, version_id)
    if version is None:
        raise NoteError("Note version not found")
    if version.status not in ("comment", "draft"):
        raise NoteError(f"Only comments or drafts can be approved (status is {version.status})")
    _apply_fields(version, fields)
    if not version.interpretation.strip():
        raise NoteError("An approved interpretation is required before adoption")
    if version.note_kind not in NOTE_KINDS:
        raise NoteError("Choose whether this note is a clarification of existing expectations, a prospective "
                        "policy change, or a requested retrospective review")
    if not (version.scoring_implication or "").strip():
        version.scoring_implication = "no_numeric_rule"
    coexists = set(fields.get("coexists_with") or [])
    assessment_id = _assessment_id(session, version)
    inherited = []
    conflicts = []
    active_pipeline = session.scalar(select(AnalysisRun.id).where(
        AnalysisRun.assessment_id == assessment_id, AnalysisRun.kind == "pipeline",
        AnalysisRun.status == "running"))
    if active_pipeline:
        raise NoteError("Wait for the active analysis run to finish or cancel it before adopting guidance.")
    for g in current_guidance(session, assessment_id, include_prospective=True):
        if g.id == version.supersedes_version_id or g.id in coexists or not _overlaps(g, version):
            continue
        if version.supersedes_version_id and version.supersedes_version_id in (g.coexists_with or []):
            inherited.append(g.id)  # the instructor already confirmed this note family coexists with g
            continue
        conflicts.append(g)
    coexists.update(inherited)
    if conflicts:
        raise NoteConflict(
            "Overlapping approved guidance exists. Supersede it, or explicitly confirm that both notes apply.",
            [{"note_version_id": g.id, "note_id": g.note_id, "version": g.version,
              "interpretation": g.interpretation} for g in conflicts])

    now = datetime.now(timezone.utc)
    version.status = "approved"
    version.approved_by = actor
    version.approved_at = now
    version.coexists_with = sorted(coexists)
    if version.supersedes_version_id:
        previous = session.get(InstructorNoteVersion, version.supersedes_version_id)
        previous.status = "superseded"
        previous.superseded_at = now
        session.execute(update(NoteApplicability)
                        .where(NoteApplicability.note_version_id == previous.id)
                        .values(is_current=False, stale_reason=f"Superseded by note version {version.id}"))
        session.execute(update(AnalysisRun).where(
            AnalysisRun.kind == "note_scan", AnalysisRun.status == "queued",
            AnalysisRun.params["note_version_id"].as_integer() == previous.id)
            .values(status="cancelled", cancel_requested=True,
                    error="Guidance superseded before this scan ran."))
    reason = (f"Guidance changed: note {version.note_id} v{version.version} approved by {actor} at "
              f"{now.isoformat(timespec='seconds')}")
    invalidated = invalidate_current_judgments(session, assessment_id, reason)

    flagged = set()
    for case in session.scalars(select(ReviewCase).where(ReviewCase.assessment_id == assessment_id)):
        flagged.update((case.submission_a_id, case.submission_b_id))
    submissions = list(session.scalars(select(Submission).where(Submission.assessment_id == assessment_id)
                                       .order_by(Submission.id)))
    for sub in submissions:
        session.add(NoteApplicability(note_version_id=version.id, submission_id=sub.id, status="pending",
                                      previously_flagged=sub.id in flagged))
    from app.db.models import AssessmentVersion
    assessment = session.get(AssessmentVersion, assessment_id)
    run = AnalysisRun(assessment_id=assessment_id, kind="note_scan", status="queued", actor=actor,
                      params={"note_version_id": version.id, "use_model": bool(use_model_scan),
                              "context_hash": assessment.context_hash})
    session.add(run)
    session.flush()
    audit.record(session, actor, "note_approved", "instructor_note_version", version.id,
                 note_kind=version.note_kind, scan_run_id=run.id, in_scope=len(submissions),
                 invalidated=invalidated, superseded=version.supersedes_version_id,
                 coexists_with=sorted(coexists), coexistence_inherited=inherited)
    return version, run


def supersede(session: Session, version_id: int, fields: dict[str, Any], actor: str) -> InstructorNoteVersion:
    """Create a successor draft. The old version stays approved until the successor is approved."""
    old = session.get(InstructorNoteVersion, version_id)
    if old is None or old.status != "approved":
        raise NoteError("Only approved guidance can be superseded")
    pending = session.scalar(select(InstructorNoteVersion).where(
        InstructorNoteVersion.supersedes_version_id == old.id,
        InstructorNoteVersion.status.in_(("comment", "draft"))))
    if pending is not None:
        raise NoteError(f"A successor draft already exists (version id {pending.id})")
    latest = max(v.version for v in session.scalars(
        select(InstructorNoteVersion).where(InstructorNoteVersion.note_id == old.note_id)))
    new = InstructorNoteVersion(
        note_id=old.note_id, version=latest + 1, status="draft", original_text=old.original_text,
        interpretation=old.interpretation, note_kind=old.note_kind, scope_component=old.scope_component,
        scope_requirement=old.scope_requirement, examples=list(old.examples or []),
        applicability_conditions=old.applicability_conditions, exceptions=old.exceptions,
        rationale=old.rationale, scoring_implication=old.scoring_implication,
        source_case_id=old.source_case_id, supersedes_version_id=old.id, actor=actor)
    _apply_fields(new, fields)
    session.add(new)
    session.flush()
    audit.record(session, actor, "note_successor_drafted", "instructor_note_version", new.id, supersedes=old.id)
    return new

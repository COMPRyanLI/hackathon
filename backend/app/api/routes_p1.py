"""P1 HTTP API: rubric, multi-assignment import, flags, grading workspace,
draft feedback, shared calibration issues, pre-release review and snapshots.
Mutations go through the same local-origin/CSRF guard as P0."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes import actor_dep, registry_dep, settings_dep
from app.api import serializers as S
from app.analysis.cases import score_map
from app.config import PROJECT_ROOT, Settings
from app.db.models import Artifact, AssessmentVersion, Student, Submission
from app.db.models_p1 import (
    CalibrationIssue,
    ComponentMapping,
    DraftFeedback,
    DraftGradeRevision,
    EvidenceManifestVersion,
    ExternalFlag,
    ExternalFlagLink,
    FeedbackCard,
    GradingEvent,
    ModerationSnapshot,
    RubricCriterion,
    RubricDocument,
    RubricIssue,
    RubricVersion,
)
from app.db.session import get_session
from app.extraction.facts import SCALAR_FACTS, scalar_fact
from app.calibration import issues as issue_service
from app.grading import dispositions as disposition_service
from app.grading.drafts import DraftConflict, DraftError, current_revision, draft_from_historical, revision_view, \
    save_draft
from app.grading.feedback import current_feedback, feedback_view
from app.grading.prerelease import create_snapshot, prerelease_summary, start_prerelease_review
from app.importers.flags import import_flags
from app.importers.multi import batch_preview, confirm_mapping, current_roles, discover_assignments, facts_for, \
    prepare_assessment
from app.importers.service import ImportInputError, build_preview, commit_import
from app.rubrics import service as rubric_service
from app.rubrics.engine import information_bound

router = APIRouter(prefix="/api")
DEFAULT_GUIDE = "./data/private/phase2-review/grading-guide.pdf"
DEFAULT_FLAGS = "./data/private/phase2-review/flags.csv"


def _get(session: Session, model: Any, ident: int, name: str) -> Any:
    obj = session.get(model, ident)
    if obj is None:
        raise HTTPException(404, f"{name} {ident} not found")
    return obj


def _conflict(exc: DraftConflict) -> HTTPException:
    return HTTPException(409, {"message": str(exc), "current_revision": revision_view(exc.current)})


def _criteria(session: Session, assessment: AssessmentVersion) -> dict[str, Any] | None:
    rubric = rubric_service.assessment_rubric(session, assessment)
    if rubric is None:
        return None
    # Award criteria record points given; deduction criteria record points deducted.
    return {k: c for k, c in rubric["by_key"].items() if not c.is_container
            and c.rule_kind in ("award", "deduction") and c.assignment == rubric["code"]}


# ------------------------------------------------------------------ demo and import


@router.post("/demo/p1")
def demo_p1(session: Session = Depends(get_session, scope="function"), actor: str = Depends(actor_dep)) -> dict[str, Any]:
    from app.importers.demo_p1 import import_synthetic_p1

    return import_synthetic_p1(session, actor)


class BatchRequest(BaseModel):
    csv_path: Optional[str] = None
    root: Optional[str] = None
    assignments: Optional[list[str]] = None
    filename_regex: Optional[str] = None
    dataset_key: Optional[str] = None


@router.post("/imports/batch-preview")
def batch_preview_route(body: BatchRequest, settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    root = settings.resolve(body.root or ".")
    codes = body.assignments or discover_assignments(root)
    return batch_preview(settings.resolve(body.csv_path or settings.csv_path), root, codes,
                         body.filename_regex or settings.filename_id_regex, body.dataset_key or settings.dataset_key)


@router.post("/imports/batch-commit")
def batch_commit(body: BatchRequest, session: Session = Depends(get_session, scope="function"), settings: Settings = Depends(settings_dep),
                 actor: str = Depends(actor_dep)) -> dict[str, Any]:
    root = settings.resolve(body.root or ".")
    codes = body.assignments or discover_assignments(root)
    results = {}
    for code in codes:
        try:
            preview = build_preview(settings.resolve(body.csv_path or settings.csv_path), root / code,
                                    body.filename_regex or settings.filename_id_regex,
                                    body.dataset_key or settings.dataset_key, code)
        except ImportInputError as exc:
            results[code] = {"error": str(exc)}
            continue
        batch, assessment, created = commit_import(session, preview, actor)
        results[code] = {"assessment_id": assessment.id, "batch_id": batch.id, "created": created,
                         **prepare_assessment(session, assessment, actor)}
    return {"results": results}


@router.post("/assessments/{assessment_id}/prepare")
def prepare(assessment_id: int, session: Session = Depends(get_session, scope="function"), actor: str = Depends(actor_dep)) -> dict:
    return prepare_assessment(session, _get(session, AssessmentVersion, assessment_id, "Assessment"), actor)


@router.get("/assessments/{assessment_id}/manifest")
def manifest(assessment_id: int, session: Session = Depends(get_session, scope="function")) -> dict[str, Any]:
    row = session.scalar(select(EvidenceManifestVersion).where(EvidenceManifestVersion.assessment_id == assessment_id)
                         .order_by(EvidenceManifestVersion.version.desc()).limit(1))
    if row is None:
        raise HTTPException(404, "No evidence manifest yet; run prepare")
    return {"version": row.version, "summary": row.summary, "manifest": row.manifest,
            "states": ["available", "not_in_current_export", "confirmed_not_submitted", "unreadable",
                       "anonymization_affected", "not_applicable", "unknown"]}


class MappingRequest(BaseModel):
    role: str


@router.post("/artifacts/{artifact_id}/component-mapping")
def set_mapping(artifact_id: int, body: MappingRequest, session: Session = Depends(get_session, scope="function"),
                actor: str = Depends(actor_dep)) -> dict[str, Any]:
    try:
        row = confirm_mapping(session, artifact_id, body.role, actor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"artifact_id": artifact_id, "role": row.role, "status": row.status}


# ------------------------------------------------------------------ rubric


class RubricImport(BaseModel):
    path: Optional[str] = None


@router.post("/rubrics/import")
def rubric_import(body: RubricImport, session: Session = Depends(get_session, scope="function"), settings: Settings = Depends(settings_dep),
                  actor: str = Depends(actor_dep)) -> dict[str, Any]:
    path = settings.resolve(body.path or DEFAULT_GUIDE)
    try:
        version, created = (rubric_service.import_pdf(session, path, actor) if path.suffix.lower() == ".pdf"
                            else rubric_service.import_text(session, path, actor, is_synthetic=False))
    except rubric_service.RubricError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"rubric_version_id": version.id, "created": created}


def _criterion_view(c: RubricCriterion) -> dict[str, Any]:
    return {"criterion_key": c.criterion_key, "parent_key": c.parent_key, "assignment": c.assignment,
            "component_role": c.component_role, "title": c.title, "page": c.page, "source_quote": c.source_quote,
            "max_points": c.max_points, "rule_kind": c.rule_kind, "classification": c.classification,
            "authority": c.authority, "applies_to": c.applies_to, "exceptions": c.exceptions,
            "fact_binding": c.fact_binding, "is_container": c.is_container}


def _issue_view(i: RubricIssue) -> dict[str, Any]:
    return {"id": i.id, "assignment": i.assignment, "criterion_key": i.criterion_key, "kind": i.kind,
            "message": i.message, "page": i.page, "source_quote": i.source_quote,
            "blocks_total_proposals": i.blocks_total_proposals, "status": i.status}


@router.get("/rubrics")
def rubrics(session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    out = []
    for v in session.scalars(select(RubricVersion).order_by(RubricVersion.id)):
        d = session.get(RubricDocument, v.document_id)
        out.append({"id": v.id, "version": v.version, "status": v.status, "document": d.title,
                    "document_sha256": d.sha256, "is_synthetic": d.is_synthetic, "pages": d.page_count})
    return out


@router.get("/rubrics/{version_id}")
def rubric_detail(version_id: int, session: Session = Depends(get_session, scope="function")) -> dict[str, Any]:
    v = _get(session, RubricVersion, version_id, "Rubric version")
    d = session.get(RubricDocument, v.document_id)
    return {"id": v.id, "status": v.status, "parser_version": v.parser_version, "is_synthetic": d.is_synthetic,
            "document": {"title": d.title, "sha256": d.sha256, "pages": [{"page": p["page"], "text": p["text"]}
                                                                         for p in d.pages]},
            "criteria": [_criterion_view(c) for c in session.scalars(select(RubricCriterion).where(
                RubricCriterion.rubric_version_id == v.id).order_by(RubricCriterion.sort_order))],
            "issues": [_issue_view(i) for i in session.scalars(select(RubricIssue).where(
                RubricIssue.rubric_version_id == v.id))]}


@router.post("/rubrics/{version_id}/reparse")
def rubric_reparse(version_id: int, session: Session = Depends(get_session, scope="function"),
                   actor: str = Depends(actor_dep)) -> dict[str, Any]:
    try:
        version, created = rubric_service.reparse(session, version_id, actor)
    except rubric_service.RubricError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"rubric_version_id": version.id, "created": created, "status": version.status}


@router.post("/rubrics/{version_id}/activate")
def rubric_activate(version_id: int, session: Session = Depends(get_session, scope="function"), actor: str = Depends(actor_dep)) -> dict:
    try:
        v = rubric_service.activate(session, version_id, actor)
    except rubric_service.RubricError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": v.id, "status": v.status}


class LinkRequest(BaseModel):
    rubric_version_id: int


@router.post("/assessments/{assessment_id}/rubric-link")
def rubric_link(assessment_id: int, body: LinkRequest, session: Session = Depends(get_session, scope="function"),
                actor: str = Depends(actor_dep)) -> dict[str, Any]:
    a = _get(session, AssessmentVersion, assessment_id, "Assessment")
    try:
        rubric_service.link_assessment(session, a.id, body.rubric_version_id, a.name, actor)
    except rubric_service.RubricError as exc:
        raise HTTPException(400, str(exc)) from exc
    return prepare_assessment(session, a, actor)


@router.get("/assessments/{assessment_id}/rubric")
def assessment_rubric_route(assessment_id: int, session: Session = Depends(get_session, scope="function")) -> dict[str, Any]:
    a = _get(session, AssessmentVersion, assessment_id, "Assessment")
    rubric = rubric_service.assessment_rubric(session, a)
    if rubric is None:
        return {"linked": False, "mode": "content_only"}
    bound = information_bound(rubric["criteria"], rubric["code"], {}, rubric["blocking"])
    return {"linked": True, "rubric_version_id": rubric["version"].id, "code": rubric["code"],
            "criteria": [_criterion_view(c) for c in rubric["criteria"]],
            "issues": [_issue_view(i) for i in rubric["issues"]], "blocking": rubric["blocking"],
            "empty_draft_bound": bound}


# ------------------------------------------------------------------ external flags


class FlagsImport(BaseModel):
    path: Optional[str] = None
    dataset_key: Optional[str] = None


@router.post("/flags/import")
def flags_import(body: FlagsImport, session: Session = Depends(get_session, scope="function"), settings: Settings = Depends(settings_dep),
                 actor: str = Depends(actor_dep)) -> dict[str, Any]:
    try:
        return import_flags(session, settings.resolve(body.path or DEFAULT_FLAGS),
                            body.dataset_key or settings.dataset_key, actor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/flags")
def flags(session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    out = []
    for f in session.scalars(select(ExternalFlag).order_by(ExternalFlag.id)):
        links = list(session.scalars(select(ExternalFlagLink).where(ExternalFlagLink.flag_id == f.id)))
        out.append({"id": f.id, "original_flag_id": f.original_flag_id, "row_number": f.row_number,
                    "trust": f.trust, "similarity": f.similarity, "similarity_raw": f.similarity_raw,
                    "assignment_hint": f.assignment_hint, "assignment_hint_status": "unconfirmed",
                    "rubric_note": f.rubric_note, "explanation": f.explanation, "checks": f.checks,
                    "fields": {k: v for k, v in f.fields.items() if k in ("grader_a", "grader_b", "score_a", "score_b")},
                    "links": [{"side": l.side, "status": l.status, "candidates": l.candidates,
                               "metadata_check": l.metadata_check} for l in links]})
    return out


# ------------------------------------------------------------------ grading workspace


@router.get("/assessments/{assessment_id}/grading-queue")
def grading_queue(assessment_id: int, session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    a = _get(session, AssessmentVersion, assessment_id, "Assessment")
    scores = score_map(session, a)
    rows = session.execute(select(Submission, Student).join(Student, Student.id == Submission.student_id)
                           .where(Submission.assessment_id == a.id).order_by(Student.code)).all()
    out = []
    for sub, student in rows:
        revision = current_revision(session, sub.id)
        fb = current_feedback(session, sub.id) if revision else None
        open_cards = session.scalar(select(func.count(FeedbackCard.id)).where(
            FeedbackCard.feedback_id == fb.id, FeedbackCard.status == "open")) if fb else 0
        out.append({"submission_id": sub.id, "student_id": student.code, "files": len(sub.artifacts),
                    "recorded": scores.get(sub.id), "draft": revision_view(revision),
                    "feedback_status": fb.status if fb else None, "open_cards": open_cards})
    return out


@router.get("/submissions/{submission_id}/workspace")
def workspace(submission_id: int, session: Session = Depends(get_session, scope="function"),
              registry: Any = Depends(registry_dep)) -> dict[str, Any]:
    sub = _get(session, Submission, submission_id, "Submission")
    a = session.get(AssessmentVersion, sub.assessment_id)
    student = session.get(Student, sub.student_id)
    roles = current_roles(session, sub)
    rubric = rubric_service.assessment_rubric(session, a)
    revision = current_revision(session, sub.id)
    history = [revision_view(r) for r in session.scalars(select(DraftGradeRevision).where(
        DraftGradeRevision.submission_id == sub.id).order_by(DraftGradeRevision.revision_number))]
    artifacts = []
    for art in sub.artifacts:
        facts = facts_for(art)
        view = S.artifact_view(session, registry, art, a)
        view["role"] = {"role": roles[art.id].role, "status": roles[art.id].status,
                        "evidence": roles[art.id].evidence} if art.id in roles else None
        view["typed_facts"] = {name: scalar_fact(facts, name) for name in SCALAR_FACTS}
        view["loops"] = facts.get("loops", [])
        artifacts.append(view)
    criteria = []
    if rubric:
        criteria = [_criterion_view(c) for c in rubric["criteria"] if c.assignment == rubric["code"]]
    return {"submission_id": sub.id, "student_id": student.code, "assessment_id": a.id, "assignment": a.name,
            "is_synthetic": a.is_synthetic, "recorded": score_map(session, a).get(sub.id),
            "recorded_note": "Imported historical total: a prior observation, not a draft save and not ground truth.",
            "artifacts": artifacts, "criteria": criteria,
            "rubric_issues": [_issue_view(i) for i in rubric["issues"]] if rubric else [],
            "current_revision": revision_view(revision), "history": history,
            "feedback": feedback_view(session, current_feedback(session, sub.id)) if revision else None}


class DraftRequest(BaseModel):
    total: str
    expected_revision_id: Optional[int] = Field(..., description="Current revision id you edited, or null for a first draft")
    grader_label: str = ""
    criterion_points: Optional[dict[str, str]] = None
    deduction_reasons: Optional[list[str]] = None
    note: str = ""
    idempotency_key: Optional[str] = None


@router.post("/submissions/{submission_id}/draft-grades")
def save_draft_route(submission_id: int, body: DraftRequest, session: Session = Depends(get_session, scope="function"),
                     actor: str = Depends(actor_dep)) -> dict[str, Any]:
    sub = _get(session, Submission, submission_id, "Submission")
    try:
        revision, created, feedback = save_draft(
            session, submission_id, total=body.total, grader_label=body.grader_label or actor, actor=actor,
            expected_revision_id=body.expected_revision_id, criterion_points=body.criterion_points,
            deduction_reasons=body.deduction_reasons, note=body.note, idempotency_key=body.idempotency_key,
            criteria=_criteria(session, session.get(AssessmentVersion, sub.assessment_id)))
    except DraftConflict as exc:
        raise _conflict(exc) from exc
    except DraftError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"revision": revision_view(revision), "created": created,
            "feedback": {"id": feedback.id, "status": feedback.status} if feedback else None,
            "message": "Draft saved. Feedback is being prepared; you can continue to the next submission."
            if created else "Duplicate request: the existing revision was returned (no new revision)."}


class HistoricalRequest(BaseModel):
    expected_revision_id: Optional[int] = Field(...)
    grader_label: str = ""


@router.post("/submissions/{submission_id}/draft-from-historical")
def draft_from_historical_route(submission_id: int, body: HistoricalRequest, session: Session = Depends(get_session, scope="function"),
                                actor: str = Depends(actor_dep)) -> dict[str, Any]:
    try:
        revision, created, feedback = draft_from_historical(session, submission_id, actor, body.expected_revision_id,
                                                            body.grader_label)
    except DraftConflict as exc:
        raise _conflict(exc) from exc
    except DraftError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"revision": revision_view(revision), "created": created,
            "feedback": {"id": feedback.id, "status": feedback.status} if feedback else None}


@router.get("/draft-grades/{revision_id}/feedback")
def revision_feedback(revision_id: int, session: Session = Depends(get_session, scope="function")) -> dict[str, Any]:
    revision = _get(session, DraftGradeRevision, revision_id, "Revision")
    fb = session.scalar(select(DraftFeedback).where(DraftFeedback.revision_id == revision.id)
                        .order_by(DraftFeedback.id.desc()).limit(1))
    current = current_revision(session, revision.submission_id)
    view = feedback_view(session, fb)
    return {"revision": revision_view(revision), "is_current_revision": current.id == revision.id,
            "current_revision_id": current.id, "feedback": view}


class DispositionRequest(BaseModel):
    action: Literal[disposition_service.ACTIONS]  # type: ignore[valid-type]
    revision_id: int
    explanation: str = ""
    issue_id: Optional[int] = None
    new_total: Optional[str] = None
    criterion_points: Optional[dict[str, str]] = None
    grader_label: str = ""
    idempotency_key: Optional[str] = None


@router.post("/feedback-cards/{card_id}/dispositions")
def disposition(card_id: int, body: DispositionRequest, session: Session = Depends(get_session, scope="function"),
                actor: str = Depends(actor_dep)) -> dict[str, Any]:
    card = _get(session, FeedbackCard, card_id, "Card")
    sub = session.get(Submission, card.submission_id)
    try:
        return disposition_service.respond(
            session, card_id, body.action, actor, revision_id=body.revision_id, explanation=body.explanation,
            issue_id=body.issue_id, new_total=body.new_total, criterion_points=body.criterion_points,
            grader_label=body.grader_label, idempotency_key=body.idempotency_key,
            criteria=_criteria(session, session.get(AssessmentVersion, sub.assessment_id)))
    except DraftConflict as exc:
        raise _conflict(exc) from exc
    except (DraftError, issue_service.IssueError) as exc:
        raise HTTPException(400, str(exc)) from exc


# ------------------------------------------------------------------ calibration issues


@router.get("/calibration-issues")
def list_issues(assessment_id: Optional[int] = None, state: Optional[str] = None,
                session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    query = select(CalibrationIssue).order_by(CalibrationIssue.id)
    if assessment_id:
        query = query.where(CalibrationIssue.assessment_id == assessment_id)
    if state:
        query = query.where(CalibrationIssue.state == state)
    return [issue_service.issue_view(session, i) for i in session.scalars(query)]


@router.get("/calibration-issues/{issue_id}")
def issue_detail(issue_id: int, session: Session = Depends(get_session, scope="function")) -> dict[str, Any]:
    issue = _get(session, CalibrationIssue, issue_id, "Issue")
    view = issue_service.issue_view(session, issue, detail=True)
    a = session.get(AssessmentVersion, issue.assessment_id)
    scores = score_map(session, a)
    examples = []
    for link in view["links"]:
        if not link["active"]:
            continue
        sub = session.get(Submission, link["submission_id"])
        revision = current_revision(session, sub.id)
        card = session.get(FeedbackCard, link["card_id"]) if link["card_id"] else None
        examples.append({"submission_id": sub.id, "student_id": session.get(Student, sub.student_id).code,
                         "recorded": scores.get(sub.id), "draft": revision_view(revision),
                         "evidence": card.evidence if card else [], "method": link["method"],
                         "local_disposition": link["local_disposition"]})
    view["examples"] = examples
    view["is_synthetic"] = a.is_synthetic
    return view


class IssueAction(BaseModel):
    expected_version: Optional[int] = None


class ResolveRequest(IssueAction):
    resolution_type: Literal["interpretation", "record_correction", "case_specific"]
    text: str
    note_kind: Optional[Literal["clarification", "prospective_policy", "retrospective_review"]] = None
    condition: Optional[dict[str, Any]] = None
    exception_conditions: Optional[list[dict[str, Any]]] = None
    point_treatment: Optional[dict[str, Any]] = None
    criterion_key: Optional[str] = None
    coexists_with: Optional[list[int]] = None
    is_anchor: bool = False
    anchor_examples: Optional[list[dict[str, Any]]] = None


def _issue_error(exc: Exception) -> HTTPException:
    from app.guidance.notes import NoteConflict

    if isinstance(exc, issue_service.IssueConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, NoteConflict):
        return HTTPException(409, {"message": str(exc), "conflicts": exc.conflicts})
    return HTTPException(400, str(exc))


@router.post("/calibration-issues/{issue_id}/resolve")
def resolve_issue(issue_id: int, body: ResolveRequest, session: Session = Depends(get_session, scope="function"),
                  actor: str = Depends(actor_dep)) -> dict[str, Any]:
    from app.guidance.notes import NoteError

    issue = _get(session, CalibrationIssue, issue_id, "Issue")
    a = session.get(AssessmentVersion, issue.assessment_id)
    try:
        issue, run = issue_service.resolve(
            session, issue_id, actor=actor, expected_version=body.expected_version,
            resolution_type=body.resolution_type, text=body.text, note_kind=body.note_kind, condition=body.condition,
            exception_conditions=body.exception_conditions, point_treatment=body.point_treatment,
            criterion_key=body.criterion_key, coexists_with=body.coexists_with,
            provider_mode="mock" if a.is_synthetic else None, is_anchor=body.is_anchor,
            anchor_examples=body.anchor_examples)
    except (issue_service.IssueError, NoteError) as exc:
        raise _issue_error(exc) from exc
    return {"issue": issue_service.issue_view(session, issue), "scan_run_id": run.id if run else None,
            "statement": "Approval changes no original or draft grade. Linked and future matching drafts reuse this "
                         "answer; exceptions stay reviewable."}


class MergeRequest(IssueAction):
    into_issue_id: int
    expected_into_version: Optional[int] = None


@router.post("/calibration-issues/{issue_id}/merge")
def merge_issue(issue_id: int, body: MergeRequest, session: Session = Depends(get_session, scope="function"),
                actor: str = Depends(actor_dep)) -> dict[str, Any]:
    try:
        target = issue_service.merge(session, issue_id, body.into_issue_id, actor, body.expected_version,
                                     body.expected_into_version)
    except issue_service.IssueError as exc:
        raise _issue_error(exc) from exc
    return issue_service.issue_view(session, target)


class SplitRequest(IssueAction):
    submission_ids: list[int]
    question: str


@router.post("/calibration-issues/{issue_id}/split")
def split_issue(issue_id: int, body: SplitRequest, session: Session = Depends(get_session, scope="function"),
                actor: str = Depends(actor_dep)) -> dict[str, Any]:
    try:
        new = issue_service.split(session, issue_id, body.submission_ids, body.question, actor, body.expected_version)
    except issue_service.IssueError as exc:
        raise _issue_error(exc) from exc
    return issue_service.issue_view(session, new)


class DeferRequest(IssueAction):
    reason: str = ""


@router.post("/calibration-issues/{issue_id}/defer")
def defer_issue(issue_id: int, body: DeferRequest, session: Session = Depends(get_session, scope="function"),
                actor: str = Depends(actor_dep)) -> dict[str, Any]:
    try:
        issue = issue_service.defer(session, issue_id, actor, body.expected_version, body.reason)
    except issue_service.IssueError as exc:
        raise _issue_error(exc) from exc
    return issue_service.issue_view(session, issue)


# ------------------------------------------------------------------ pre-release, snapshots, metrics


@router.get("/assessments/{assessment_id}/prerelease")
def prerelease(assessment_id: int, session: Session = Depends(get_session, scope="function")) -> dict[str, Any]:
    return prerelease_summary(session, _get(session, AssessmentVersion, assessment_id, "Assessment"))


class PrereleaseRequest(BaseModel):
    review_budget: int = Field(default=10, ge=0, le=500)


@router.post("/assessments/{assessment_id}/prerelease-review")
def prerelease_review(assessment_id: int, body: PrereleaseRequest, session: Session = Depends(get_session, scope="function"),
                      actor: str = Depends(actor_dep)) -> dict[str, Any]:
    a = _get(session, AssessmentVersion, assessment_id, "Assessment")
    run = start_prerelease_review(session, a, body.review_budget, actor, "mock" if a.is_synthetic else None)
    return S.run_view(run)


@router.post("/assessments/{assessment_id}/moderation-snapshots")
def snapshot(assessment_id: int, session: Session = Depends(get_session, scope="function"), settings: Settings = Depends(settings_dep),
             actor: str = Depends(actor_dep)) -> dict[str, Any]:
    snap = create_snapshot(session, _get(session, AssessmentVersion, assessment_id, "Assessment"), actor, settings)
    return {"id": snap.id, "version": snap.version, "csv_path": snap.csv_path, "json_path": snap.json_path,
            "summary": snap.summary, "unresolved": snap.unresolved, "revision_ids": snap.revision_ids,
            "csv_text": getattr(snap, "csv_text", "")}


@router.get("/assessments/{assessment_id}/moderation-snapshots")
def snapshots(assessment_id: int, session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    return [{"id": s.id, "version": s.version, "created_at": S.iso(s.created_at), "summary": s.summary,
             "csv_path": s.csv_path, "json_path": s.json_path}
            for s in session.scalars(select(ModerationSnapshot).where(ModerationSnapshot.assessment_id == assessment_id)
                                     .order_by(ModerationSnapshot.version))]


@router.get("/assessments/{assessment_id}/grading-metrics")
def grading_metrics(assessment_id: int, session: Session = Depends(get_session, scope="function")) -> dict[str, Any]:
    """Workload measures (not grading accuracy): latency, questions and duplicate prompts."""
    events = list(session.scalars(select(GradingEvent).where(GradingEvent.assessment_id == assessment_id)))
    latencies = sorted(e.detail.get("latency_ms") for e in events
                       if e.event_type == "feedback_completed" and e.detail.get("latency_ms") is not None)
    issues = list(session.scalars(select(CalibrationIssue).where(CalibrationIssue.assessment_id == assessment_id)))
    cards = list(session.scalars(select(FeedbackCard).join(DraftFeedback).where(
        DraftFeedback.assessment_id == assessment_id)))
    question_cards = [c for c in cards if (c.comparison or {}).get("issue_spec")]
    resolved = [i for i in issues if i.state == "resolved"]

    def pct(values: list, q: float):
        return values[min(len(values) - 1, int(q * len(values)))] if values else None

    return {"saves": sum(1 for e in events if e.event_type == "draft_saved"),
            "save_conflicts": sum(1 for e in events if e.event_type == "draft_save_conflict"),
            "feedback_latency_ms": {"n": len(latencies), "median": pct(latencies, 0.5), "p90": pct(latencies, 0.9),
                                    "max": latencies[-1] if latencies else None},
            "shared_questions": len([i for i in issues if i.state != "superseded"]),
            "question_cards": len(question_cards),
            "question_cards_linked_to_existing_issue": sum(1 for c in question_cards if c.issue_id),
            "cases_per_resolved_issue": {i.id: issue_service.affected_count(session, i.id) for i in resolved},
            "suppressed_repeat_cards": sum(1 for c in cards if c.status == "suppressed"),
            "note": "Functional workload measures only; not grading accuracy."}

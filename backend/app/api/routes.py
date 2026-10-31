"""HTTP API (prefix /api). Mutations are protected by the CSRF middleware in
main.py. There is no endpoint through which model output can approve notes or
change scores; approvals and decisions require explicit requests from the UI."""
from __future__ import annotations

import hashlib
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import audit
from app.analysis.cases import score_map
from app.analysis.context import INSTRUCTION_STATUSES, context_mode, current_guidance, refresh_context_hash
from app.api import serializers as S
from app.config import Settings
from app.db.models import (
    AnalysisRun,
    ApplicabilityDecision,
    Artifact,
    AssessmentVersion,
    AuditEvent,
    CandidatePair,
    EmbeddingRecord,
    ImportBatch,
    InstructorNote,
    InstructorNoteVersion,
    NoteApplicability,
    PairComparison,
    ReviewCase,
    ReviewDecision,
    Student,
    Submission,
    SubmissionEvidence,
    utcnow,
)
from app.db.session import get_session
from app.exports.report import build_report
from app.guidance import notes as note_service
from app.importers.service import ImportInputError, build_preview, commit_import, preview_payload, source_assignment_warning
from app.jobs.worker import RESUMABLE
from app.providers.registry import ProviderRegistry

router = APIRouter(prefix="/api")

CASE_ACTIONS = ("difference_justified", "needs_clarification", "context_incomplete", "possible_recording_error",
                "dismiss_model_analysis")
APPLICABILITY_ACTIONS = ("confirm_applies", "confirm_not_applicable", "needs_review", "dismiss_model_analysis")


def settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def registry_dep(request: Request) -> ProviderRegistry:
    return request.app.state.registry


def actor_dep(request: Request) -> str:
    # Local prototype actor; not institutional authentication.
    return request.app.state.settings.actor_name


def _codes(session: Session, assessment_id: int) -> dict[int, str]:
    return dict(session.execute(select(Submission.id, Student.code).join(Student, Student.id == Submission.student_id)
                                .where(Submission.assessment_id == assessment_id)).all())


def _get(session: Session, model: Any, ident: int, name: str) -> Any:
    obj = session.get(model, ident)
    if obj is None:
        raise HTTPException(404, f"{name} {ident} not found")
    return obj


# ------------------------------------------------------------------ meta


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/providers/status")
def providers_status(registry: ProviderRegistry = Depends(registry_dep)) -> dict[str, Any]:
    return registry.status()


@router.get("/settings/public")
def public_settings(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    return {"csv_path": settings.csv_path, "sa1_path": settings.sa1_path, "dataset_key": settings.dataset_key,
            "assessment_name": settings.assessment_name, "filename_id_regex": settings.filename_id_regex,
            "retrieval_top_k_per_channel": settings.retrieval_top_k_per_channel,
            "initial_review_pair_budget": settings.initial_review_pair_budget,
            "allow_cloud_inference": settings.allow_cloud_inference, "actor": settings.actor_name}


# ------------------------------------------------------------------ imports


class ImportRequest(BaseModel):
    csv_path: Optional[str] = None
    source_root: Optional[str] = None
    filename_regex: Optional[str] = None
    dataset_key: Optional[str] = None
    assessment_name: Optional[str] = None


def _preview(body: ImportRequest, settings: Settings):
    try:
        return build_preview(settings.resolve(body.csv_path or settings.csv_path),
                             settings.resolve(body.source_root or settings.sa1_path),
                             body.filename_regex or settings.filename_id_regex,
                             body.dataset_key or settings.dataset_key,
                             body.assessment_name or settings.assessment_name)
    except ImportInputError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/imports/preview")
def import_preview(body: ImportRequest, settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    return preview_payload(_preview(body, settings))


@router.post("/imports/commit")
def import_commit(body: ImportRequest, session: Session = Depends(get_session, scope="function"),
                  settings: Settings = Depends(settings_dep), actor: str = Depends(actor_dep)) -> dict[str, Any]:
    preview = _preview(body, settings)
    batch, assessment, created = commit_import(session, preview, actor)
    return {"batch_id": batch.id, "assessment_id": assessment.id, "created": created,
            "message": "Imported." if created else "Identical inputs were already imported; nothing changed."}


@router.post("/imports/demo")
def import_demo(session: Session = Depends(get_session, scope="function"), settings: Settings = Depends(settings_dep),
                actor: str = Depends(actor_dep)) -> dict[str, Any]:
    from app.importers.demo import import_synthetic_demo

    batch, assessment, created = import_synthetic_demo(session, actor)
    return {"batch_id": batch.id, "assessment_id": assessment.id, "created": created}


@router.get("/imports")
def list_imports(session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    return [{"id": b.id, "dataset_key": b.dataset_key, "assessment_name": b.assessment_name,
             "csv_sha256": b.csv_sha256, "is_synthetic": b.is_synthetic, "created_at": S.iso(b.created_at),
             "actor": b.actor, "assumptions": b.assumptions}
            for b in session.scalars(select(ImportBatch).order_by(ImportBatch.id))]


# ------------------------------------------------------------------ assessments


def _assessment_view(session: Session, a: AssessmentVersion) -> dict[str, Any]:
    guidance = current_guidance(session, a.id)
    batch = session.get(ImportBatch, a.batch_id)
    return {"id": a.id, "dataset_key": a.dataset_key, "name": a.name, "version": a.version,
            "batch_id": a.batch_id, "is_synthetic": a.is_synthetic, "instructions_status": a.instructions_status,
            "import_warning": source_assignment_warning(batch.source_root, a.name),
            "instructions_text": a.instructions_text, "reference_solution_text": a.reference_solution_text,
            "reference_solution_status": a.reference_solution_status,
            "max_points_confirmed": a.max_points_confirmed, "multi_file_scope_note": a.multi_file_scope_note,
            "context_version": a.context_version, "context_mode": context_mode(a, guidance),
            "current_guidance": [S.note_version_view(g) for g in guidance],
            "submission_count": session.scalar(select(func.count(Submission.id))
                                               .where(Submission.assessment_id == a.id))}


@router.get("/assessments")
def list_assessments(session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    return [_assessment_view(session, a) for a in session.scalars(select(AssessmentVersion).order_by(AssessmentVersion.id))]


@router.get("/assessments/{assessment_id}")
def get_assessment(assessment_id: int, session: Session = Depends(get_session, scope="function")) -> dict[str, Any]:
    return _assessment_view(session, _get(session, AssessmentVersion, assessment_id, "Assessment"))


class ContextPatch(BaseModel):
    instructions_status: Optional[Literal["pending", "synthetic_demo", "confirmed_real"]] = None
    instructions_text: Optional[str] = None
    reference_solution_text: Optional[str] = None
    reference_solution_status: Optional[str] = None
    max_points_confirmed: Optional[float] = None
    multi_file_scope_note: Optional[str] = None


@router.patch("/assessments/{assessment_id}/context")
def patch_context(assessment_id: int, body: ContextPatch, session: Session = Depends(get_session, scope="function"),
                  actor: str = Depends(actor_dep)) -> dict[str, Any]:
    a = _get(session, AssessmentVersion, assessment_id, "Assessment")
    fields = body.model_dump(exclude_unset=True)
    status = fields.get("instructions_status")
    if status and status not in INSTRUCTION_STATUSES:
        raise HTTPException(400, "Unknown instructions status")
    if status == "synthetic_demo" and not a.is_synthetic:
        raise HTTPException(400, "Synthetic demo context cannot be attached to real class data.")
    if status == "confirmed_real" and a.is_synthetic:
        raise HTTPException(400, "A synthetic assessment cannot carry confirmed real instructions.")
    before = a.context_hash
    for key, value in fields.items():
        setattr(a, key, value)
    refresh_context_hash(a)
    if a.context_hash != before:
        if session.scalar(select(AnalysisRun.id).where(
                AnalysisRun.assessment_id == a.id, AnalysisRun.status == "running")):
            raise HTTPException(409, "Wait for the running job to finish or cancel it before changing context.")
        a.context_version += 1
        a.updated_at = utcnow()
        reason = f"Assignment context changed to version {a.context_version}; start a new review."
        note_service.invalidate_current_judgments(session, a.id, reason)
        versions = select(InstructorNoteVersion.id).join(InstructorNote).where(InstructorNote.assessment_id == a.id)
        session.execute(update(NoteApplicability).where(NoteApplicability.note_version_id.in_(versions))
                        .values(is_current=False, stale_reason=reason))
    audit.record(session, actor, "context_updated", "assessment_version", a.id, fields=sorted(fields),
                 context_version=a.context_version)
    return _assessment_view(session, a)


@router.get("/assessments/{assessment_id}/submissions")
def list_submissions(assessment_id: int, session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    a = _get(session, AssessmentVersion, assessment_id, "Assessment")
    scores = score_map(session, a)
    subs = session.execute(select(Submission, Student).join(Student, Student.id == Submission.student_id)
                           .where(Submission.assessment_id == assessment_id).order_by(Student.code)).all()
    return [S.submission_summary(sub, student, scores.get(sub.id)) for sub, student in subs]


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: int, session: Session = Depends(get_session, scope="function"),
                   registry: ProviderRegistry = Depends(registry_dep)) -> dict[str, Any]:
    sub = _get(session, Submission, submission_id, "Submission")
    a = session.get(AssessmentVersion, sub.assessment_id)
    student = session.get(Student, sub.student_id)
    return {**S.submission_summary(sub, student, score_map(session, a).get(sub.id)),
            "assessment_id": a.id, "is_synthetic": a.is_synthetic,
            "artifacts": [S.artifact_view(session, registry, art, a) for art in sub.artifacts]}


@router.get("/assessments/{assessment_id}/data-quality")
def data_quality(assessment_id: int, session: Session = Depends(get_session, scope="function")) -> dict[str, Any]:
    a = _get(session, AssessmentVersion, assessment_id, "Assessment")
    batch = session.get(ImportBatch, a.batch_id)
    return {"report": batch.summary, "assumptions": batch.assumptions, "is_synthetic": batch.is_synthetic}


@router.get("/assessments/{assessment_id}/coverage")
def coverage(assessment_id: int, session: Session = Depends(get_session, scope="function"),
             registry: ProviderRegistry = Depends(registry_dep)) -> dict[str, Any]:
    a = _get(session, AssessmentVersion, assessment_id, "Assessment")
    hashes = set(session.scalars(select(Artifact.content_sha256).join(Submission, Submission.id == Artifact.submission_id)
                                 .where(Submission.assessment_id == a.id)))
    evidence = session.execute(select(SubmissionEvidence.status, SubmissionEvidence.is_mock, func.count())
                               .where(SubmissionEvidence.content_sha256.in_(hashes),
                                      SubmissionEvidence.context_hash == a.context_hash)
                               .group_by(SubmissionEvidence.status, SubmissionEvidence.is_mock)).all()
    embeddings = session.execute(select(EmbeddingRecord.representation, EmbeddingRecord.status, func.count())
                                 .group_by(EmbeddingRecord.representation, EmbeddingRecord.status)).all()
    runs = list(session.scalars(select(AnalysisRun).where(AnalysisRun.assessment_id == a.id)
                                .order_by(AnalysisRun.id.desc()).limit(20)))
    cases = session.execute(select(ReviewCase.is_current, func.count()).where(ReviewCase.assessment_id == a.id)
                            .group_by(ReviewCase.is_current)).all()
    return {"distinct_artifact_contents": len(hashes),
            "analyst_evidence": [{"status": s, "is_mock": m, "count": n} for s, m, n in evidence],
            "embeddings": [{"representation": r, "status": s, "count": n} for r, s, n in embeddings],
            "cases": {("current" if cur else "historical"): n for cur, n in cases},
            "runs": [S.run_view(r) for r in runs],
            "providers": registry.snapshot()}


# ------------------------------------------------------------------ runs


class RunRequest(BaseModel):
    submission_ids: Optional[list[int]] = None
    pilot_size: Optional[int] = Field(default=12, ge=2, le=500)
    review_budget: Optional[int] = Field(default=None, ge=0, le=1000)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    provider_mode: Literal["configured", "mock"] = "configured"


def _check_mode(assessment: AssessmentVersion, mode: str) -> None:
    if mode == "mock" and not assessment.is_synthetic:
        raise HTTPException(400, "Mock providers are only selectable per run for synthetic data. To use mocks on "
                                 "real class data, set the *_PROVIDER variables to mock in .env explicitly.")


def pilot_sample(session: Session, assessment_id: int, size: int, seed: str = "gradelens-pilot") -> list[int]:
    """Seeded hash order over exact student IDs: not row order and not filename-dependent."""
    rows = session.execute(select(Submission.id, Student.code).join(Student, Student.id == Submission.student_id)
                           .where(Submission.assessment_id == assessment_id)).all()
    ranked = sorted(rows, key=lambda r: hashlib.sha256(f"{seed}:{r[1]}".encode()).hexdigest())
    return sorted(r[0] for r in ranked[:size])


@router.post("/assessments/{assessment_id}/analysis-runs")
def create_run(assessment_id: int, body: RunRequest, session: Session = Depends(get_session, scope="function"),
               registry: ProviderRegistry = Depends(registry_dep), actor: str = Depends(actor_dep)) -> dict[str, Any]:
    assessment = _get(session, AssessmentVersion, assessment_id, "Assessment")
    _check_mode(assessment, body.provider_mode)
    ids = body.submission_ids or pilot_sample(session, assessment_id, body.pilot_size or 12)
    valid = set(session.scalars(select(Submission.id).where(Submission.assessment_id == assessment_id)))
    if not set(ids) <= valid:
        raise HTTPException(400, "Some submission ids are not part of this assessment")
    run = AnalysisRun(assessment_id=assessment_id, kind="pipeline", status="queued", actor=actor,
                      params={"submission_ids": sorted(ids), "review_budget": body.review_budget,
                              "top_k": body.top_k, "provider_mode": body.provider_mode},
                      provider_snapshot=registry.for_mode(body.provider_mode).snapshot())
    session.add(run)
    session.flush()
    audit.record(session, actor, "run_created", "analysis_run", run.id, submissions=len(ids),
                 providers=registry.snapshot())
    return S.run_view(run)


@router.get("/analysis-runs")
def list_runs(assessment_id: Optional[int] = None, session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    query = select(AnalysisRun).order_by(AnalysisRun.id.desc())
    if assessment_id:
        query = query.where(AnalysisRun.assessment_id == assessment_id)
    return [S.run_view(r) for r in session.scalars(query.limit(100))]


@router.get("/analysis-runs/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session, scope="function")) -> dict[str, Any]:
    return S.run_view(_get(session, AnalysisRun, run_id, "Run"))


@router.post("/analysis-runs/{run_id}/cancel")
def cancel_run(run_id: int, session: Session = Depends(get_session, scope="function"), actor: str = Depends(actor_dep)) -> dict[str, Any]:
    run = _get(session, AnalysisRun, run_id, "Run")
    if run.status == "queued":
        run.status = "cancelled"
    elif run.status == "running":
        run.cancel_requested = True
    audit.record(session, actor, "run_cancel_requested", "analysis_run", run.id)
    return S.run_view(run)


@router.post("/analysis-runs/{run_id}/resume")
def resume_run(run_id: int, session: Session = Depends(get_session, scope="function"), actor: str = Depends(actor_dep),
               registry: ProviderRegistry = Depends(registry_dep)) -> dict[str, Any]:
    run = _get(session, AnalysisRun, run_id, "Run")
    if run.status not in RESUMABLE:
        raise HTTPException(409, f"Run is {run.status}; only {', '.join(RESUMABLE)} runs can be resumed")
    if run.kind == "pipeline" and (run.params or {}).get("input_signature"):
        from app.jobs.pipeline import input_signature
        actual = input_signature(session, run.assessment_id, registry.for_mode(run.params.get("provider_mode")))
        if actual != run.params["input_signature"]:
            raise HTTPException(409, "Context, guidance, model or settings changed. Start a new analysis run.")
    if run.kind == "note_scan":
        version = session.get(InstructorNoteVersion, run.params["note_version_id"])
        assessment = session.get(AssessmentVersion, run.assessment_id)
        if version.status != "approved" or run.params.get("context_hash") != assessment.context_hash:
            raise HTTPException(409, "Guidance or context changed. Adopt a successor note and scan again.")
    run.status, run.cancel_requested, run.finished_at = "queued", False, None
    audit.record(session, actor, "run_resumed", "analysis_run", run.id)
    return S.run_view(run)


@router.get("/analysis-runs/{run_id}/candidates")
def run_candidates(run_id: int, session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    run = _get(session, AnalysisRun, run_id, "Run")
    codes = _codes(session, run.assessment_id)
    pairs = session.scalars(select(CandidatePair).where(CandidatePair.run_id == run_id)
                            .order_by(CandidatePair.fused_score.desc()))
    return [S.pair_view(p, codes) for p in pairs]


# ------------------------------------------------------------------ review cases


@router.get("/review-cases")
def list_cases(assessment_id: int, include_stale: bool = False, run_id: Optional[int] = None,
               session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    codes = _codes(session, assessment_id)
    query = select(ReviewCase).where(ReviewCase.assessment_id == assessment_id)
    if run_id is not None:
        query = query.where(ReviewCase.run_id == run_id)
    if not include_stale:
        query = query.where(ReviewCase.is_current.is_(True))
    counts = dict(session.execute(select(ReviewDecision.case_id, func.count()).group_by(ReviewDecision.case_id)).all())
    cases = sorted(session.scalars(query), key=lambda c: (not c.is_current, -c.priority, c.id))
    return [S.case_summary(c, codes, counts.get(c.id, 0)) for c in cases]


@router.get("/review-cases/{case_id}")
def get_case(case_id: int, session: Session = Depends(get_session, scope="function"),
             registry: ProviderRegistry = Depends(registry_dep)) -> dict[str, Any]:
    case = _get(session, ReviewCase, case_id, "Case")
    a = session.get(AssessmentVersion, case.assessment_id)
    codes = _codes(session, a.id)
    comparison = session.get(PairComparison, case.comparison_id) if case.comparison_id else None
    labels = {v: k for k, v in ((comparison.sent_content or {}).get("labels", {}) if comparison else {}).items()}
    sides = []
    for sid in (case.submission_a_id, case.submission_b_id):
        sub = session.get(Submission, sid)
        sides.append({"submission_id": sid, "student_id": codes.get(sid),
                      "component_scope_status": sub.component_scope_status,
                      "artifacts": [S.artifact_view(session, registry, art, a, labels.get(art.id)) for art in sub.artifacts]})
    decisions = session.scalars(select(ReviewDecision).where(ReviewDecision.case_id == case_id).order_by(ReviewDecision.id))
    return {**S.case_summary(case, codes, 0), "is_synthetic": a.is_synthetic,
            "candidate": S.pair_view(session.get(CandidatePair, case.candidate_pair_id), codes),
            "comparison": S.comparison_view(comparison), "sides": sides,
            "decisions": [S.decision_view(d) for d in decisions],
            "context_mode": context_mode(a, current_guidance(session, a.id))}


class DecisionRequest(BaseModel):
    action: Literal[CASE_ACTIONS]  # type: ignore[valid-type]
    comment: str = ""
    proposed_score_a: Optional[float] = None
    proposed_score_b: Optional[float] = None


@router.post("/review-cases/{case_id}/decisions")
def decide_case(case_id: int, body: DecisionRequest, session: Session = Depends(get_session, scope="function"),
                actor: str = Depends(actor_dep)) -> dict[str, Any]:
    case = _get(session, ReviewCase, case_id, "Case")
    guidance = [{"note_version_id": g.id, "version": g.version} for g in current_guidance(session, case.assessment_id)]
    decision = ReviewDecision(case_id=case.id, action=body.action, comment=body.comment,
                              proposed_score_a=body.proposed_score_a, proposed_score_b=body.proposed_score_b,
                              guidance_versions=guidance, actor=actor)
    session.add(decision)
    case.status = "decided"
    session.flush()
    audit.record(session, actor, "case_decided", "review_case", case.id, decision_id=decision.id,
                 decision_action=body.action, case_current=case.is_current,
                 proposed_scores=[body.proposed_score_a, body.proposed_score_b])
    return S.decision_view(decision)


# ------------------------------------------------------------------ instructor notes


class NoteCreate(BaseModel):
    assessment_id: int
    original_text: str
    interpretation: str = ""
    source_case_id: Optional[int] = None


class NoteFields(BaseModel):
    original_text: Optional[str] = None
    interpretation: Optional[str] = None
    note_kind: Optional[Literal["clarification", "prospective_policy", "retrospective_review"]] = None
    scope_component: Optional[str] = None
    scope_requirement: Optional[str] = None
    examples: Optional[list[dict[str, Any]]] = None
    applicability_conditions: Optional[dict[str, Any]] = None
    exceptions: Optional[str] = None
    rationale: Optional[str] = None
    scoring_implication: Optional[str] = None


class NoteApprove(NoteFields):
    coexists_with: Optional[list[int]] = None
    use_model_scan: bool = False
    provider_mode: Literal["configured", "mock"] = "configured"


def _note_error(exc: note_service.NoteError) -> HTTPException:
    if isinstance(exc, note_service.NoteConflict):
        return HTTPException(409, {"message": str(exc), "conflicts": exc.conflicts})
    return HTTPException(400, str(exc))


@router.get("/instructor-notes")
def list_notes(assessment_id: int, session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    versions = session.scalars(select(InstructorNoteVersion).join(InstructorNote)
                               .where(InstructorNote.assessment_id == assessment_id)
                               .order_by(InstructorNoteVersion.note_id, InstructorNoteVersion.version))
    return [S.note_version_view(v) for v in versions]


@router.post("/instructor-notes")
def create_note(body: NoteCreate, session: Session = Depends(get_session, scope="function"), actor: str = Depends(actor_dep)) -> dict[str, Any]:
    _get(session, AssessmentVersion, body.assessment_id, "Assessment")
    try:
        version = note_service.create_note(session, body.assessment_id, body.original_text, actor,
                                           body.interpretation, body.source_case_id)
    except note_service.NoteError as exc:
        raise _note_error(exc) from exc
    return S.note_version_view(version)


@router.patch("/instructor-notes/{version_id}")
def edit_note(version_id: int, body: NoteFields, session: Session = Depends(get_session, scope="function"),
              actor: str = Depends(actor_dep)) -> dict[str, Any]:
    try:
        return S.note_version_view(note_service.update_draft(session, version_id, body.model_dump(exclude_unset=True), actor))
    except note_service.NoteError as exc:
        raise _note_error(exc) from exc


@router.post("/instructor-notes/{version_id}/approve")
def approve_note(version_id: int, body: NoteApprove, session: Session = Depends(get_session, scope="function"),
                 actor: str = Depends(actor_dep)) -> dict[str, Any]:
    existing = _get(session, InstructorNoteVersion, version_id, "Note version")
    _check_mode(session.get(AssessmentVersion, session.get(InstructorNote, existing.note_id).assessment_id),
                body.provider_mode)
    try:
        version, run = note_service.approve(session, version_id, body.model_dump(exclude_unset=True), actor,
                                            body.use_model_scan)
    except note_service.NoteError as exc:
        raise _note_error(exc) from exc
    run.params = {**run.params, "provider_mode": body.provider_mode}
    return {"note": S.note_version_view(version), "scan_run": S.run_view(run)}


@router.post("/instructor-notes/{version_id}/supersede")
def supersede_note(version_id: int, body: NoteFields, session: Session = Depends(get_session, scope="function"),
                   actor: str = Depends(actor_dep)) -> dict[str, Any]:
    try:
        return S.note_version_view(note_service.supersede(session, version_id, body.model_dump(exclude_unset=True), actor))
    except note_service.NoteError as exc:
        raise _note_error(exc) from exc


@router.get("/instructor-notes/{version_id}/impact")
def note_impact(version_id: int, session: Session = Depends(get_session, scope="function")) -> dict[str, Any]:
    version = _get(session, InstructorNoteVersion, version_id, "Note version")
    note = session.get(InstructorNote, version.note_id)
    a = session.get(AssessmentVersion, note.assessment_id)
    scores = score_map(session, a)
    codes = _codes(session, a.id)
    rows = list(session.scalars(select(NoteApplicability).where(NoteApplicability.note_version_id == version_id)
                                .order_by(NoteApplicability.priority_rank, NoteApplicability.submission_id)))
    decisions: dict[int, list[ApplicabilityDecision]] = {}
    for d in session.scalars(select(ApplicabilityDecision).where(
            ApplicabilityDecision.applicability_id.in_([r.id for r in rows])).order_by(ApplicabilityDecision.id)):
        decisions.setdefault(d.applicability_id, []).append(d)
    scan = session.scalar(select(AnalysisRun).where(AnalysisRun.kind == "note_scan")
                          .where(AnalysisRun.params["note_version_id"].as_integer() == version_id)
                          .order_by(AnalysisRun.id.desc()).limit(1))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {
        "note": S.note_version_view(version), "is_synthetic": a.is_synthetic,
        "scan_run": S.run_view(scan) if scan else None, "status_counts": counts,
        "items": [{"id": r.id, "submission_id": r.submission_id, "student_id": codes.get(r.submission_id),
                   "status": r.status, "method": r.method, "rationale": r.rationale, "evidence": r.evidence,
                   "priority_rank": r.priority_rank, "previously_flagged": r.previously_flagged,
                   "is_mock": r.is_mock, "is_current": r.is_current, "stale_reason": r.stale_reason,
                   "score": scores.get(r.submission_id),
                   "decisions": [{"id": d.id, "action": d.action, "comment": d.comment,
                                  "proposed_score": d.proposed_score, "actor": d.actor,
                                  "created_at": S.iso(d.created_at)} for d in decisions.get(r.id, [])]}
                  for r in rows],
        "statement": "Potential impact only. No grades were changed. Proposed scores require a human decision.",
    }


class ApplicabilityDecisionRequest(BaseModel):
    action: Literal[APPLICABILITY_ACTIONS]  # type: ignore[valid-type]
    comment: str = ""
    proposed_score: Optional[float] = None


@router.post("/note-applicability/{applicability_id}/decisions")
def decide_applicability(applicability_id: int, body: ApplicabilityDecisionRequest,
                         session: Session = Depends(get_session, scope="function"), actor: str = Depends(actor_dep)) -> dict[str, Any]:
    row = _get(session, NoteApplicability, applicability_id, "Applicability item")
    decision = ApplicabilityDecision(applicability_id=row.id, action=body.action, comment=body.comment,
                                     proposed_score=body.proposed_score, actor=actor)
    session.add(decision)
    session.flush()
    audit.record(session, actor, "applicability_decided", "note_applicability", row.id, decision_id=decision.id,
                 decision_action=body.action, proposed_score=body.proposed_score)
    return {"id": decision.id, "action": decision.action}


# ------------------------------------------------------------------ export and audit


class ExportRequest(BaseModel):
    assessment_id: int


@router.post("/exports/moderation-report")
def export_report(body: ExportRequest, session: Session = Depends(get_session, scope="function"),
                  settings: Settings = Depends(settings_dep), actor: str = Depends(actor_dep)) -> dict[str, Any]:
    try:
        return build_report(session, body.assessment_id, settings, actor)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/audit")
def audit_log(limit: int = 200, session: Session = Depends(get_session, scope="function")) -> list[dict[str, Any]]:
    events = session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 1000)))
    return [{"id": e.id, "at": S.iso(e.at), "actor": e.actor, "action": e.action, "entity_type": e.entity_type,
             "entity_id": e.entity_id, "detail": e.detail} for e in events]

"""Multi-assignment import (SA1-SA6), evidence manifests and component mappings.

Each assignment is previewed together and committed separately through the
existing idempotent P0 importer, so each keeps its own manifest, score column,
context and rubric link. Evidence states are explicit; only verified original
submission records could establish ``confirmed_not_submitted`` (never inferred).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.db.models import Artifact, AssessmentVersion, ScoreRecord, Student, Submission
from app.db.models_p1 import ComponentMapping, EvidenceManifestVersion
from app.extraction.facts import MAPPER_VERSION, artifact_facts, infer_roles
from app.importers.service import ImportInputError, build_preview

ASSIGNMENT_DIR_RE = re.compile(r"^SA\d+$")
EVIDENCE_STATES = ("available", "not_in_current_export", "confirmed_not_submitted", "unreadable",
                   "anonymization_affected", "not_applicable", "unknown")


def discover_assignments(root: Path) -> list[str]:
    return sorted((p.name for p in root.iterdir() if p.is_dir() and ASSIGNMENT_DIR_RE.match(p.name)),
                  key=lambda n: int(n[2:]))


def batch_preview(csv_path: Path, root: Path, codes: list[str], regex: str, dataset_key: str) -> dict[str, Any]:
    rows, errors = [], {}
    for code in codes:
        try:
            preview = build_preview(csv_path, root / code, regex, dataset_key, code)
        except ImportInputError as exc:
            errors[code] = str(exc)
            continue
        report = preview.report
        rows.append({
            "assignment": code,
            "source_root": str(root / code),
            "files": report["files"]["matched_files"],
            "student_bundles": report["matching"]["students_with_files"],
            "multi_file_bundles": len(report["matching"]["multi_file_students"]),
            "bundles_without_score": len(report["matching"]["matched_without_score"]),
            "students_without_files": len(report["matching"]["students_without_files"]),
            "parse_failures": len(report["quality"]["parse_failures"]),
            "anonymization_flags": len(report["quality"]["possible_anonymization_artifacts"]),
            "rejected_files": report["files"]["rejected_files"],
            "manifest_sha256": preview.manifest_sha256,
            "issues": [i for i in preview.issues if i.get("severity") == "error"][:20],
        })
    return {"assignments": rows, "errors": errors,
            "note": "Each assignment is committed separately. All files here are code; screenshots and output "
                    "attachments are not part of this export (not evidence that students failed to submit them)."}


@lru_cache(maxsize=4096)
def _facts_cached(content_sha256: str, source: str) -> dict[str, Any]:
    return artifact_facts(source)


def facts_for(artifact: Artifact) -> dict[str, Any]:
    return _facts_cached(artifact.content_sha256, artifact.source_text)


def ensure_component_mappings(session: Session, assessment: AssessmentVersion, actor: str) -> int:
    """Propose roles from code evidence for artifacts without a current mapping."""
    created = 0
    code = assessment.name
    subs = list(session.scalars(select(Submission).where(Submission.assessment_id == assessment.id)))
    mapped = set(session.scalars(select(ComponentMapping.artifact_id).where(
        ComponentMapping.assessment_id == assessment.id, ComponentMapping.is_current.is_(True))))
    for sub in subs:
        if all(a.id in mapped for a in sub.artifacts):
            continue
        for row in infer_roles(code, [(a.id, facts_for(a)) for a in sub.artifacts]):
            if row["artifact_id"] in mapped:
                continue  # Never replace an existing confirmed mapping when adding another file.
            session.add(ComponentMapping(artifact_id=row["artifact_id"], assessment_id=assessment.id, role=row["role"],
                                         status=row["status"], evidence=row["evidence"],
                                         mapper_version=MAPPER_VERSION, actor=actor))
            created += 1
    session.flush()
    return created


def current_roles(session: Session, submission: Submission) -> dict[int, ComponentMapping]:
    rows = session.scalars(select(ComponentMapping).where(
        ComponentMapping.artifact_id.in_([a.id for a in submission.artifacts]),
        ComponentMapping.is_current.is_(True)).order_by(ComponentMapping.id))
    return {m.artifact_id: m for m in rows}


def confirm_mapping(session: Session, artifact_id: int, role: str, actor: str) -> ComponentMapping:
    from app.db.models import AnalysisRun, NoteApplicability, InstructorNoteVersion
    from app.db.models_p1 import CriterionAssessment, DraftFeedback, FeedbackCard, GuidanceRuleSpec, IssueCaseLink
    from app.grading.feedback import enqueue_refresh

    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        raise ValueError("Artifact not found")
    sub = session.get(Submission, artifact.submission_id)
    assessment = session.get(AssessmentVersion, sub.assessment_id)
    allowed = {"ambiguous", "program_1", "program_2"} if assessment.name.upper() == "SA3" else {"ambiguous", "whole_assignment"}
    if role not in allowed:
        raise ValueError(f"Role must be one of {', '.join(sorted(allowed))}")
    if session.scalar(select(AnalysisRun.id).where(AnalysisRun.assessment_id == assessment.id,
                      AnalysisRun.kind == "note_scan", AnalysisRun.status == "running")):
        raise ValueError("Wait for the active guidance scan before changing component scope")
    old = session.scalars(select(ComponentMapping).where(ComponentMapping.artifact_id == artifact_id,
                                                         ComponentMapping.is_current.is_(True))).all()
    if not old:
        raise ValueError("No mapping exists for this artifact")
    if len(old) == 1 and old[0].role == role and old[0].authority == "operator_confirmed":
        return old[0]
    for row in old:
        row.is_current = False
    new = ComponentMapping(artifact_id=artifact_id, assessment_id=old[0].assessment_id, role=role,
                           status="ambiguous" if role == "ambiguous" else "confirmed",
                           authority="operator_confirmed", evidence=["confirmed by operator"],
                           mapper_version=MAPPER_VERSION, actor=actor)
    session.add(new)
    session.flush()
    audit.record(session, actor, "component_mapping_confirmed", "artifact", artifact_id, role=role)
    # Comparisons with peers can depend on this role; conservatively refresh
    # current draft feedback across the assignment without changing grades.
    for fb in session.scalars(select(DraftFeedback).where(DraftFeedback.assessment_id == assessment.id)):
        fb.status, fb.error = "stale", "Component mapping changed; feedback is being refreshed."
        for card in session.scalars(select(FeedbackCard).where(FeedbackCard.feedback_id == fb.id)):
            if card.status == "open":
                card.status, card.status_reason = "superseded", "Component mapping changed."
    for row in session.scalars(select(CriterionAssessment).where(CriterionAssessment.submission_id == sub.id)):
        row.is_current, row.stale_reason = False, "Component mapping changed."
    for link in session.scalars(select(IssueCaseLink).where(IssueCaseLink.submission_id == sub.id)):
        link.active = False
    specs = list(session.scalars(select(GuidanceRuleSpec).join(InstructorNoteVersion,
                 InstructorNoteVersion.id == GuidanceRuleSpec.note_version_id).where(
                 GuidanceRuleSpec.assessment_id == assessment.id, InstructorNoteVersion.status == "approved")))
    for spec in specs:
        for row in session.scalars(select(NoteApplicability).where(NoteApplicability.submission_id == sub.id,
                                  NoteApplicability.note_version_id == spec.note_version_id)):
            row.is_current, row.stale_reason, row.status = False, "Component mapping changed.", "pending"
        session.add(AnalysisRun(assessment_id=assessment.id, kind="note_scan", status="queued", actor=actor,
                    params={"note_version_id": spec.note_version_id, "context_hash": assessment.context_hash,
                            "use_model": False, "provider_mode": "mock" if assessment.is_synthetic else None}))
    enqueue_refresh(session, assessment.id, None, actor, "component mapping changed", all_drafted=True)
    from app.rubrics.service import assessment_rubric
    rubric = assessment_rubric(session, assessment)
    build_manifest(session, assessment, rubric["criteria"] if rubric else None)
    return new


def build_manifest(session: Session, assessment: AssessmentVersion, criteria: list[Any] | None) -> EvidenceManifestVersion:
    """Snapshot evidence availability per submission and per criterion (versioned when it changes)."""
    students = session.execute(select(Student.code, ScoreRecord.status).join(
        ScoreRecord, ScoreRecord.student_id == Student.id).where(
        ScoreRecord.batch_id == assessment.batch_id, ScoreRecord.assessment_column == assessment.name)).all()
    subs = {sub.student_id: sub for sub in session.scalars(select(Submission).where(
        Submission.assessment_id == assessment.id))}
    code_by_student = dict(session.execute(select(Student.id, Student.code).where(
        Student.dataset_key == assessment.dataset_key)).all())
    by_code = {code_by_student[sid]: sub for sid, sub in subs.items()}
    per_submission: dict[str, Any] = {}
    for code, score_status in sorted(students):
        sub = by_code.get(code)
        if sub is None:
            per_submission[code] = {"code": "not_in_current_export", "files": 0, "screenshot": "not_in_current_export",
                                    "output": "not_in_current_export", "score_status": score_status}
            continue
        parse = "available"
        for artifact in sub.artifacts:
            if artifact.parse_status != "ok":
                kinds = {i.kind for i in artifact.issues}
                parse = "anonymization_affected" if "possible_anonymization_artifact" in kinds else "unreadable"
        roles = current_roles(session, sub)
        per_submission[code] = {
            "code": "available", "files": len(sub.artifacts), "parse": parse,
            "screenshot": "not_in_current_export", "output": "not_in_current_export",
            "components": {str(a.id): (roles[a.id].role if a.id in roles else "unknown") for a in sub.artifacts},
            "coverage_note": "Code files only; complete assignment coverage is not established by file count.",
            "score_status": score_status,
        }
    criteria_status = {}
    for c in criteria or []:
        if c.is_container:
            continue
        criteria_status[c.criterion_key] = {
            "classification": c.classification,
            "default_evidence": ("not_in_current_export" if c.classification == "external_evidence_required"
                                 else "available" if c.classification == "explicit" else "unknown"),
        }
    manifest = {"assessment": assessment.name, "export": {"file_types": [".py"], "screenshots_present": False,
                                                          "outputs_present": False, "starter_code_present": False},
                "submissions": per_submission, "criteria": criteria_status}
    content_hash = audit.stable_hash(manifest)
    latest = session.scalar(select(EvidenceManifestVersion).where(EvidenceManifestVersion.assessment_id == assessment.id)
                            .order_by(EvidenceManifestVersion.version.desc()).limit(1))
    if latest is not None and latest.content_hash == content_hash:
        return latest
    counts: dict[str, int] = {}
    for row in per_submission.values():
        counts[row["code"]] = counts.get(row["code"], 0) + 1
    version = EvidenceManifestVersion(
        assessment_id=assessment.id, version=(latest.version + 1) if latest else 1, content_hash=content_hash,
        manifest=manifest, summary={"code_states": counts, "submissions": len(per_submission),
                                    "external_criteria": sum(1 for v in criteria_status.values()
                                                             if v["default_evidence"] == "not_in_current_export")})
    session.add(version)
    session.flush()
    return version


def prepare_assessment(session: Session, assessment: AssessmentVersion, actor: str) -> dict[str, Any]:
    from app.rubrics.service import assessment_rubric

    created = ensure_component_mappings(session, assessment, actor)
    rubric = assessment_rubric(session, assessment)
    manifest = build_manifest(session, assessment, rubric["criteria"] if rubric else None)
    roles = session.execute(select(ComponentMapping.role, func.count()).where(
        ComponentMapping.assessment_id == assessment.id, ComponentMapping.is_current.is_(True))
        .group_by(ComponentMapping.role)).all()
    return {"mappings_created": created, "roles": dict(roles), "manifest_version": manifest.version,
            "manifest_summary": manifest.summary}

"""JSON views of domain objects for the local UI."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AnalysisRun,
    Artifact,
    AssessmentVersion,
    CandidatePair,
    InstructorNoteVersion,
    PairComparison,
    ReviewCase,
    ReviewDecision,
    Student,
    Submission,
    SubmissionEvidence,
)
from app.providers.registry import ProviderRegistry


def iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def issue_view(issue: Any) -> dict[str, Any]:
    return {"kind": issue.kind, "severity": issue.severity, "line": issue.line, "message": issue.message,
            "detail": issue.detail}


def evidence_view(ev: Any) -> dict[str, Any] | None:
    if ev is None:
        return None
    return {"id": ev.id, "status": ev.status, "provider": ev.provider, "model": ev.model, "is_mock": ev.is_mock,
            "prompt_version": ev.prompt_version, "evidence": ev.evidence, "rejected_claims": ev.rejected_claims,
            "error_kind": ev.error_kind, "error_message": ev.error_message, "duration_ms": ev.duration_ms,
            "usage": ev.usage, "created_at": iso(ev.created_at)}


def artifact_view(session: Session, registry: ProviderRegistry | None, artifact: Artifact,
                  assessment: AssessmentVersion, label: str | None = None) -> dict[str, Any]:
    features = dict(artifact.features or {})
    literals = features.pop("string_literals", [])
    # Latest analyst evidence for this exact content and context, from any provider (labeled).
    latest = session.scalar(select(SubmissionEvidence)
                            .where(SubmissionEvidence.content_sha256 == artifact.content_sha256,
                                   SubmissionEvidence.context_hash == assessment.context_hash)
                            .order_by(SubmissionEvidence.created_at.desc()).limit(1))
    evidence = evidence_view(latest)
    return {
        "id": artifact.id, "label": label, "position": artifact.position,
        "content_sha256": artifact.content_sha256, "size_bytes": artifact.size_bytes,
        "line_count": artifact.line_count, "encoding": artifact.encoding, "source_text": artifact.source_text,
        "parse_status": artifact.parse_status, "parse_error": artifact.parse_error,
        "features": features, "string_literal_count": len(literals),
        "cross_student_copies": artifact.cross_student_copies,
        "aliases": [{"relative_path": a.relative_path, "size_bytes": a.size_bytes} for a in artifact.aliases],
        "chunks": [{"key": c.chunk_key, "start_line": c.start_line, "end_line": c.end_line} for c in artifact.chunks],
        "issues": [issue_view(i) for i in artifact.issues],
        "evidence": evidence,
    }


def submission_summary(sub: Submission, student: Student, score: dict[str, Any] | None) -> dict[str, Any]:
    issues = [i for a in sub.artifacts for i in a.issues]
    return {
        "id": sub.id, "student_id": student.code, "artifact_count": len(sub.artifacts),
        "alias_count": sum(len(a.aliases) for a in sub.artifacts),
        "files": [al.relative_path for a in sub.artifacts for al in a.aliases],
        "component_scope_status": sub.component_scope_status,
        "parse_failures": sum(1 for a in sub.artifacts if a.parse_status != "ok"),
        "issue_kinds": sorted({i.kind for i in issues}),
        "score": score,
    }


def run_view(run: AnalysisRun) -> dict[str, Any]:
    return {"id": run.id, "assessment_id": run.assessment_id, "kind": run.kind, "status": run.status,
            "stage": run.stage, "params": run.params, "progress": run.progress, "coverage": run.coverage,
            "provider_snapshot": run.provider_snapshot, "error": run.error, "attempts": run.attempts,
            "cancel_requested": run.cancel_requested, "created_at": iso(run.created_at),
            "started_at": iso(run.started_at), "finished_at": iso(run.finished_at)}


def pair_view(pair: CandidatePair, codes: dict[int, str]) -> dict[str, Any]:
    return {"id": pair.id, "submission_a_id": pair.submission_a_id, "submission_b_id": pair.submission_b_id,
            "student_a": codes.get(pair.submission_a_id), "student_b": codes.get(pair.submission_b_id),
            "fused_score": round(pair.fused_score, 6), "channels": pair.channels, "reasons": pair.reasons,
            "contributing": pair.contributing, "scope": pair.scope, "selected": pair.selected,
            "selection_reason": pair.selection_reason, "is_random_control": pair.is_random_control}


def comparison_view(c: PairComparison | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {"id": c.id, "status": c.status, "provider": c.provider, "model": c.model, "is_mock": c.is_mock,
            "prompt_version": c.prompt_version, "result": c.result, "rejected_claims": c.rejected_claims,
            "sent_content": c.sent_content, "error_kind": c.error_kind, "error_message": c.error_message,
            "guidance_versions": c.guidance_versions, "is_current": c.is_current, "stale_reason": c.stale_reason,
            "usage": c.usage, "duration_ms": c.duration_ms, "created_at": iso(c.created_at)}


def decision_view(d: ReviewDecision) -> dict[str, Any]:
    return {"id": d.id, "action": d.action, "comment": d.comment, "proposed_score_a": d.proposed_score_a,
            "proposed_score_b": d.proposed_score_b, "guidance_versions": d.guidance_versions, "actor": d.actor,
            "created_at": iso(d.created_at)}


def case_summary(case: ReviewCase, codes: dict[int, str], decision_count: int) -> dict[str, Any]:
    return {"id": case.id, "run_id": case.run_id, "origin": case.origin, "category": case.category,
            "student_a": codes.get(case.submission_a_id), "student_b": codes.get(case.submission_b_id),
            "submission_a_id": case.submission_a_id, "submission_b_id": case.submission_b_id,
            "score_a": case.score_a, "score_b": case.score_b, "score_gap": case.score_gap,
            "grader_a": case.grader_a, "grader_b": case.grader_b, "headline": case.headline,
            "question": case.question, "priority": round(case.priority, 4), "is_mock": case.is_mock,
            "status": case.status, "is_current": case.is_current, "stale_reason": case.stale_reason,
            "guidance_versions": case.guidance_versions, "decision_count": decision_count,
            "created_at": iso(case.created_at)}


def note_version_view(v: InstructorNoteVersion) -> dict[str, Any]:
    return {"id": v.id, "note_id": v.note_id, "version": v.version, "status": v.status,
            "original_text": v.original_text, "interpretation": v.interpretation, "note_kind": v.note_kind,
            "scope_component": v.scope_component, "scope_requirement": v.scope_requirement,
            "examples": v.examples, "applicability_conditions": v.applicability_conditions,
            "exceptions": v.exceptions, "rationale": v.rationale, "scoring_implication": v.scoring_implication,
            "source_case_id": v.source_case_id, "supersedes_version_id": v.supersedes_version_id,
            "coexists_with": v.coexists_with, "actor": v.actor, "created_at": iso(v.created_at),
            "approved_by": v.approved_by, "approved_at": iso(v.approved_at), "superseded_at": iso(v.superseded_at)}

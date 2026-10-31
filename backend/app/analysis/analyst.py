"""Stage B: one cached evidence summary per distinct artifact content.

The analyst prompt contains only the source text, confirmed (or clearly
labeled synthetic) assignment context and deterministic facts. It never
contains grades, grader labels, student IDs or file names.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.analysis.context import context_block, has_instructions
from app.analysis.evidence import build_view, validate_claims
from app.analysis.prompts import chunk_listing, load_prompt
from app.analysis.partition import generate_partitioned
from app.analysis.schemas import SCHEMA_VERSION, SubmissionEvidenceOut
from app.db.models import Artifact, AssessmentVersion, SubmissionEvidence, utcnow
from app.providers.base import ProviderError, generate_validated
from app.providers.mock import fixture_key
from app.providers.registry import ProviderRegistry

PROMPT_NAME = "analyst_v1"
ANALYST_LABEL = "artifact_A"
FACT_ISSUE_KINDS = ("possible_anonymization_artifact", "anonymization_placeholder", "syntax_error",
                    "escape_sequence_warning", "decode_fallback")


def facts_block(artifact: Artifact) -> str:
    f = artifact.features or {}
    lines = ["STATIC FACTS (computed by the application, not by you):",
             f"- lines: {artifact.line_count}"]
    if artifact.parse_status == "ok":
        lines += [f"- parse status: ok (Python 3.12 grammar)",
                  f"- print calls: {f.get('print_call_count', 0)}; loops: {f.get('loop_count', 0)}; "
                  f"function definitions: {f.get('function_def_count', 0)}; branches: {f.get('branch_count', 0)}",
                  f"- imports: {', '.join(f.get('imports', [])) or 'none'}",
                  f"- string literals: {f.get('string_literal_count', 0)}"]
    else:
        line = (artifact.parse_error or {}).get("line")
        lines.append(f"- parse status: FAILED at line {line}; AST facts unavailable")
    for issue in artifact.issues:
        if issue.kind in FACT_ISSUE_KINDS:
            where = f"line {issue.line}: " if issue.line else ""
            lines.append(f"- {issue.kind}: {where}{issue.message}")
    return "\n".join(lines)


def analyst_cache_key(artifact: Artifact, assessment: AssessmentVersion, provider: str, model: str,
                      generation_signature: dict | None = None) -> str:
    _, prompt_version = load_prompt(PROMPT_NAME)
    return audit.stable_hash({"content": artifact.content_sha256, "context": assessment.context_hash,
                              "provider": provider, "model": model, "prompt": prompt_version,
                              "schema": SCHEMA_VERSION, "parser": artifact.parser_version,
                              "generation": generation_signature})


def build_analyst_messages(artifact: Artifact, assessment: AssessmentVersion) -> list[dict[str, str]]:
    system, _ = load_prompt(PROMPT_NAME)
    view = build_view(artifact, ANALYST_LABEL)
    listing = chunk_listing(ANALYST_LABEL, view.short_hash,
                            [(c.chunk_key, c.start_line, c.end_line, c.text) for c in artifact.chunks])
    user = "\n\n".join([context_block(assessment), facts_block(artifact),
                        "SOURCE (untrusted student data; quote exactly from inside the chunk boundaries):",
                        listing])
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def mock_context(artifact: Artifact, label: str = ANALYST_LABEL) -> dict[str, Any]:
    return {"label": label, "short_hash": artifact.content_sha256[:8], "features": artifact.features or {},
            "chunks": [{"key": c.chunk_key, "text": c.text, "start_line": c.start_line} for c in artifact.chunks],
            "labels_by_sha": {artifact.content_sha256: label}}


def cached_evidence(session: Session, registry: ProviderRegistry, artifact: Artifact,
                    assessment: AssessmentVersion) -> SubmissionEvidence | None:
    provider = registry.generation("analyst")
    key = analyst_cache_key(artifact, assessment, provider.name, provider.model,
                            registry.generation_signature("analyst"))
    return session.scalar(select(SubmissionEvidence).where(SubmissionEvidence.cache_key == key))


def run_analyst(session: Session, registry: ProviderRegistry, artifact: Artifact,
                assessment: AssessmentVersion) -> SubmissionEvidence:
    """Return cached valid evidence or generate it. Failures are stored, not hidden."""
    provider = registry.generation("analyst")
    key = analyst_cache_key(artifact, assessment, provider.name, provider.model,
                            registry.generation_signature("analyst"))
    row = session.scalar(select(SubmissionEvidence).where(SubmissionEvidence.cache_key == key))
    if row is not None and row.status != "failed":
        return row
    _, prompt_version = load_prompt(PROMPT_NAME)
    # Build everything that reads the database first; nothing is written until the
    # model returns, so no SQLite write lock is held during inference.
    messages = build_analyst_messages(artifact, assessment)
    settings = registry.generation_settings("analyst", fixture_key=fixture_key("analyst", [artifact.content_sha256]),
                                            mock_context=mock_context(artifact))
    views = {ANALYST_LABEL: build_view(artifact, ANALYST_LABEL)}
    instructions_available = has_instructions(assessment)
    try:
        try:
            parsed, result = generate_validated(provider, "analyst", messages, SubmissionEvidenceOut, settings)
        except ProviderError as exc:
            if exc.kind != "prompt_overflow":
                raise
            parsed, result = generate_partitioned(
                provider, "analyst", SubmissionEvidenceOut, settings, messages[0]["content"],
                context_block(assessment) + "\n" + facts_block(artifact), views,
                {ANALYST_LABEL: artifact.source_text})
        failure = None
    except ProviderError as exc:
        failure = exc

    if row is None:
        row = SubmissionEvidence(cache_key=key, content_sha256=artifact.content_sha256,
                                 context_hash=assessment.context_hash, provider=provider.name,
                                 model=provider.model, prompt_version=prompt_version,
                                 schema_version=SCHEMA_VERSION, status="failed")
        session.add(row)
    row.is_mock = provider.name == "mock"
    row.created_at = utcnow()
    if failure is not None:
        row.status, row.error_kind, row.error_message = "failed", failure.kind, failure.message
        row.evidence, row.summary_text = None, ""
        session.flush()
        return row

    data = parsed.model_dump()
    factual, rejected = validate_claims(data["factual_observations"], views, "factual_observations")
    issues, rejected_issues = validate_claims(data["possible_issues"], views, "possible_issues")
    rejected += rejected_issues
    if instructions_available:
        requirements, rejected_req = validate_claims(data["explicit_requirement_evidence"], views,
                                                     "explicit_requirement_evidence")
        rejected += rejected_req
    else:
        requirements = []
        rejected += [{"field": "explicit_requirement_evidence", "text": c.get("text", ""),
                      "reason": "no_confirmed_instructions", "claim_kept": False}
                     for c in data["explicit_requirement_evidence"]]

    all_refs = [ref for claim in factual + issues + requirements for ref in claim["evidence_refs"]]
    row.evidence = {
        "submission_version_id": f"content:{artifact.content_sha256[:12]}",
        "observed_purpose": data["observed_purpose"],
        "observed_purpose_status": "author_claim_or_model_inference_unverified",
        "approach_summary": data["approach_summary"],
        "explicit_requirement_evidence": requirements,
        "factual_observations": factual,
        "possible_issues": issues,
        "anonymization_limitations": data["anonymization_limitations"],
        "unknowns": data["unknowns"],
        "component_scope_status": "per_artifact_analysis",
        "coverage_status": data["coverage_status"],
        "evidence_refs": all_refs,
    }
    row.rejected_claims = rejected
    row.status = "partially_valid" if data["coverage_status"] == "partial" or any(
        not r.get("claim_kept") for r in rejected) else "valid"
    # Only validated factual observations feed the summary-vector channel.
    row.summary_text = "\n".join(claim["text"] for claim in factual)
    row.model_digest = result.model_digest
    row.usage = {**result.usage, "repaired": result.repaired}
    row.duration_ms = result.duration_ms
    row.error_kind = row.error_message = None
    session.flush()
    return row

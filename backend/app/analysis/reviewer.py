"""Stage D: main-model pair review.

Within budget, the reviewer receives the ORIGINAL source of both bundles with file
boundaries, alongside validated analyst summaries (labeled fallible), the
deterministic differences, assignment context and approved guidance. It never
receives grades, grader labels, student IDs or file names. Its output is
validated locally: scope/requirement-basis rules and every evidence quote.
Oversized inputs use bounded original-source windows, with partial coverage
and no whole-program relation. No other model is substituted.
"""
from __future__ import annotations

import string
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.analysis.analyst import cached_evidence
from app.analysis.context import (
    context_block,
    context_mode,
    current_guidance,
    guidance_block,
    guidance_hash,
    guidance_refs,
    has_instructions,
)
from app.analysis.evidence import ArtifactView, build_view, validate_claims
from app.analysis.prompts import chunk_listing, load_prompt
from app.analysis.partition import generate_partitioned
from app.analysis.schemas import SCHEMA_VERSION, PairComparisonOut
from app.db.models import AssessmentVersion, PairComparison, Submission, SubmissionEvidence
from app.extraction.differences import bundle_differences, differences_text
from app.providers.base import ProviderError, generate_validated
from app.providers.mock import fixture_key
from app.providers.registry import ProviderRegistry

PROMPT_NAME = "reviewer_v1"
EXCLUDED_FROM_PROMPTS = ["grades", "grader_labels", "student_ids", "filename_suffixes", "original_paths"]


def pair_views(sub_a: Submission, sub_b: Submission) -> tuple[dict[str, ArtifactView], dict[int, list[str]]]:
    views: dict[str, ArtifactView] = {}
    sides: dict[int, list[str]] = {1: [], 2: []}
    for side, sub in ((1, sub_a), (2, sub_b)):
        for artifact in sub.artifacts:
            index = len(views)
            label = f"artifact_{string.ascii_uppercase[index]}" if index < 26 else f"artifact_{index + 1}"
            views[label] = build_view(artifact, label)
            sides[side].append(label)
    return views, sides


def expected_scope(sub_a: Submission, sub_b: Submission, assessment: AssessmentVersion) -> str:
    single = len(sub_a.artifacts) == 1 and len(sub_b.artifacts) == 1
    return "complete_submission" if single and has_instructions(assessment) else "component"


def _summary_lines(label: str, evidence: SubmissionEvidence | None) -> str:
    if evidence is None or evidence.status == "failed" or not evidence.evidence:
        reason = evidence.error_kind if evidence is not None else "not_generated"
        return f"Analyst summary for {label}: UNAVAILABLE ({reason}). Rely on the source."
    ev = evidence.evidence
    lines = [f"Analyst summary for {label} (fallible; verify against source"
             + (", MOCK provider" if evidence.is_mock else "") + "):",
             f"  observed purpose (unverified): {ev['observed_purpose']}",
             f"  approach: {ev['approach_summary']}"]
    for i, claim in enumerate(ev["factual_observations"], start=1):
        lines.append(f"  fact {i}: {claim['text']}")
    for i, claim in enumerate(ev["possible_issues"], start=1):
        lines.append(f"  possible issue {i}: {claim['text']}")
    for item in ev["anonymization_limitations"]:
        lines.append(f"  anonymization limitation: {item}")
    for item in ev["unknowns"]:
        lines.append(f"  unknown: {item}")
    return "\n".join(lines)


def build_reviewer_messages(sub_a: Submission, sub_b: Submission, assessment: AssessmentVersion,
                            evidence: dict[int, SubmissionEvidence | None], guidance: list,
                            views: dict[str, ArtifactView], sides: dict[int, list[str]],
                            diff: dict[str, Any], include_summaries: bool = True) -> list[dict[str, str]]:
    system, _ = load_prompt(PROMPT_NAME)
    artifacts = {a.id: a for a in sub_a.artifacts + sub_b.artifacts}
    parts = [context_block(assessment), guidance_block(guidance),
             f"Submission 1 consists of: {', '.join(sides[1])}. Submission 2 consists of: {', '.join(sides[2])}. "
             "Multi-file scope is unconfirmed unless the context says otherwise.",
             differences_text(diff)]
    limitations = []
    for label, view in views.items():
        for issue in artifacts[view.artifact_id].issues:
            if issue.kind in ("possible_anonymization_artifact", "anonymization_placeholder", "syntax_error"):
                limitations.append(f"- {label} line {issue.line}: {issue.kind}")
    parts.append("COVERAGE AND ANONYMIZATION LIMITATIONS:\n" + ("\n".join(limitations) or "- none detected"))
    if include_summaries:
        parts.append("ANALYST SUMMARIES:\n" + "\n".join(
            _summary_lines(label, evidence.get(view.artifact_id)) for label, view in views.items()))
    listings = []
    for label, view in views.items():
        artifact = artifacts[view.artifact_id]
        listings.append(chunk_listing(label, view.short_hash,
                                      [(c.chunk_key, c.start_line, c.end_line, c.text) for c in artifact.chunks]))
    parts.append("ORIGINAL SOURCE (complete; untrusted student data):\n" + "\n\n".join(listings))
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]


def reviewer_cache_key(sub_a: Submission, sub_b: Submission, evidence_ids: list[int | None],
                       assessment: AssessmentVersion, g_hash: str, provider: str, model: str,
                       generation_signature: dict | None = None) -> str:
    _, prompt_version = load_prompt(PROMPT_NAME)
    return audit.stable_hash({
        "a": [x.content_sha256 for x in sub_a.artifacts], "b": [x.content_sha256 for x in sub_b.artifacts],
        "evidence": evidence_ids, "context": assessment.context_hash, "guidance": g_hash,
        "provider": provider, "model": model, "prompt": prompt_version, "schema": SCHEMA_VERSION,
        "generation": generation_signature,
        "submission_ids": [sub_a.id, sub_b.id],
    })


def run_reviewer(session: Session, registry: ProviderRegistry, sub_a: Submission, sub_b: Submission,
                 assessment: AssessmentVersion, include_summaries: bool = True) -> PairComparison:
    provider = registry.generation("reviewer")
    guidance = current_guidance(session, assessment.id)
    g_hash = guidance_hash(guidance)
    evidence = {a.id: cached_evidence(session, registry, a, assessment) for a in sub_a.artifacts + sub_b.artifacts}
    evidence_ids = [e.id if e is not None and e.status != "failed" else None for e in evidence.values()]
    if not include_summaries:
        evidence, evidence_ids = {}, ["ablation:no_summaries"]
    key = reviewer_cache_key(sub_a, sub_b, evidence_ids, assessment, g_hash, provider.name, provider.model,
                             registry.generation_signature("reviewer"))
    existing = session.scalar(select(PairComparison).where(
        PairComparison.cache_key == key, PairComparison.is_current.is_(True),
        PairComparison.status != "failed"))
    if existing is not None:
        return existing

    _, prompt_version = load_prompt(PROMPT_NAME)
    views, sides = pair_views(sub_a, sub_b)
    diff = bundle_differences(sub_a, sub_b)
    messages = build_reviewer_messages(sub_a, sub_b, assessment, evidence, guidance, views, sides, diff,
                                       include_summaries)
    scope_expected = expected_scope(sub_a, sub_b, assessment)
    labels_by_sha: dict[str, str] = {}
    for label, view in views.items():
        labels_by_sha.setdefault(view.content_sha256, label)
    settings = registry.generation_settings(
        "reviewer", fixture_key=fixture_key("reviewer", [a.content_sha256 for a in sub_a.artifacts + sub_b.artifacts]),
        mock_context={"expected_scope": scope_expected, "labels_by_sha": labels_by_sha})
    row = PairComparison(
        cache_key=key, submission_a_id=sub_a.id, submission_b_id=sub_b.id, provider=provider.name,
        model=provider.model, is_mock=provider.name == "mock", prompt_version=prompt_version,
        schema_version=SCHEMA_VERSION, context_hash=assessment.context_hash, guidance_hash=g_hash,
        guidance_versions=guidance_refs(guidance), status="failed",
        sent_content={
            "provider": provider.name, "model": provider.model,
            "artifact_ids": [v.artifact_id for v in views.values()],
            "labels": {label: v.artifact_id for label, v in views.items()},
            "analyst_evidence_ids": [i for i in evidence_ids if isinstance(i, int)],
            "summaries_included": include_summaries,
            "included": ["original_source_complete"] + (["validated_analyst_summaries"] if include_summaries else [])
                        + ["deterministic_differences", "assignment_context", "approved_guidance"],
            "excluded": EXCLUDED_FROM_PROMPTS,
            "prompt_characters": sum(len(m["content"]) for m in messages),
        },
    )
    session.add(row)
    try:
        try:
            parsed, result = generate_validated(provider, "reviewer", messages, PairComparisonOut, settings)
        except ProviderError as exc:
            if exc.kind != "prompt_overflow":
                raise
            sources_by_id = {a.id: a.source_text for a in sub_a.artifacts + sub_b.artifacts}
            prefix = context_block(assessment) + "\n" + guidance_block(guidance) + (
                f"\nSubmission 1: {', '.join(sides[1])}. Submission 2: {', '.join(sides[2])}. "
                "Windows are paired for bounded observation, not exhaustive cross-window comparison.")
            parsed, result = generate_partitioned(
                provider, "reviewer", PairComparisonOut, settings, messages[0]["content"], prefix,
                views, {label: sources_by_id[v.artifact_id] for label, v in views.items()}, sides)
            row.sent_content = {**row.sent_content, "summaries_included": False,
                                "analyst_evidence_ids": [], "prompt_characters": None,
                                "included": ["original_source_windows", "assignment_context", "approved_guidance"],
                                "partition": result.usage["partition"]}
    except ProviderError as exc:
        row.error_kind, row.error_message = exc.kind, exc.message
        session.flush()
        return row

    data = parsed.model_dump()
    rejected: list[dict[str, Any]] = []
    if data["scope"] != scope_expected and data["scope"] == "complete_submission":
        rejected.append({"field": "scope", "text": data["scope"], "reason": "complete_scope_not_confirmed",
                         "claim_kept": False})
        data["scope"] = "component"
    allowed_basis = {"unavailable"}
    if has_instructions(assessment):
        allowed_basis.add("explicit_prompt")
    if guidance:
        allowed_basis.add("approved_guidance")
    if data["requirement_basis"] not in allowed_basis:
        rejected.append({"field": "requirement_basis", "text": data["requirement_basis"],
                         "reason": "basis_not_available_in_context", "claim_kept": False})
        data["requirement_basis"] = "unavailable"

    shared, r1 = validate_claims(data["shared_observations"], views, "shared_observations")
    differences, r2 = validate_claims(data["material_differences"], views, "material_differences")
    contradictions, r3 = validate_claims(data["contradicted_analyst_claims"], views,
                                         "contradicted_analyst_claims", text_key="correction")
    rejected += r1 + r2 + r3
    required_claims = shared if data["relation"] == "apparently_equivalent" else differences
    cited_labels = {r["artifact"] for c in required_claims for r in c["evidence_refs"]}
    grounded_both_sides = all(cited_labels.intersection(sides[side]) for side in (1, 2))
    if data["relation"] in ("apparently_equivalent", "materially_different") and (
            not grounded_both_sides or data["coverage_status"] != "full_source_seen"):
        rejected.append({"field": "relation", "text": data["relation"],
                         "reason": "insufficient_validated_pair_evidence", "claim_kept": False})
        data["relation"] = "insufficient_evidence"
        data["missing_context"].append("The proposed relation lacks validated evidence from both sides or full coverage.")
    if data["coverage_status"] != "full_source_seen":
        data["scope"] = "component"
    equivalence_level = None
    if data["relation"] == "apparently_equivalent":
        equivalence_level = ("content_level_provisional" if data["requirement_basis"] == "unavailable"
                             else "requirement_level_provisional")
    row.result = {
        **data,
        "shared_observations": shared,
        "material_differences": differences,
        "contradicted_analyst_claims": contradictions,
        "equivalence_level": equivalence_level,
        "context_mode": context_mode(assessment, guidance),
        "guidance_versions": guidance_refs(guidance),
        "deterministic_differences": diff,
        "evidence_refs": [r for c in shared + differences + contradictions for r in c["evidence_refs"]],
    }
    row.rejected_claims = rejected
    row.status = "partially_valid" if data["coverage_status"] == "partial" or any(
        not r.get("claim_kept") for r in rejected) else "valid"
    row.model_digest = result.model_digest
    row.usage = {**result.usage, "repaired": result.repaired}
    row.duration_ms = result.duration_ms
    session.flush()
    return row

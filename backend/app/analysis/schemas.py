"""Model-facing output schemas. Kept small so a 4B local model can follow
them. Application metadata (IDs, hashes, guidance versions, scope status) is
attached by the backend, never trusted from model output."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "v3-source-windows"

CASE_CATEGORIES = (
    "exact_content_discrepancy",
    "alternate_method",
    "local_error_partial_credit",
    "similar_omission",
    "unspecified_style_format_expectation",
    "incomplete_or_anonymized_evidence",
    "conflicting_guidance",
    "recording_administrative_context",
)


class EvidenceRef(BaseModel):
    artifact: str = Field(description="Opaque artifact label exactly as given, e.g. artifact_A")
    source_hash: str = Field(description="The 8-character source hash shown for that artifact")
    chunk_id: str = Field(description="Chunk id exactly as given, e.g. c0")
    quote: str = Field(description="Exact text copied from that chunk (one line or less is best)")


class Claim(BaseModel):
    text: str
    evidence_refs: list[EvidenceRef]


class SubmissionEvidenceOut(BaseModel):
    observed_purpose: str
    approach_summary: str
    explicit_requirement_evidence: list[Claim]
    factual_observations: list[Claim]
    possible_issues: list[Claim]
    anonymization_limitations: list[str]
    unknowns: list[str]
    coverage_status: Literal["full_source_seen", "partial"]


class Contradiction(BaseModel):
    analyst_claim: str = Field(description="The analyst statement being contradicted, quoted or paraphrased")
    correction: str
    evidence_refs: list[EvidenceRef]


class PairComparisonOut(BaseModel):
    relation: Literal["apparently_equivalent", "materially_different", "not_comparable", "insufficient_evidence"]
    scope: Literal["component", "complete_submission"]
    requirement_basis: Literal["explicit_prompt", "approved_guidance", "unavailable"]
    shared_observations: list[Claim]
    material_differences: list[Claim]
    contradicted_analyst_claims: list[Contradiction]
    alternative_explanations: list[str]
    missing_context: list[str]
    clarification_question: str
    suggested_category: Literal[CASE_CATEGORIES]  # type: ignore[valid-type]
    coverage_status: Literal["full_source_seen", "partial"]


class NoteApplicabilityOut(BaseModel):
    status: Literal["applies", "does_not_apply", "uncertain"]
    rationale: str
    evidence: list[Claim]

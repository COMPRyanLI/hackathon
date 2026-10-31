"""P1 tables: rubric provenance, evidence manifests, component mappings,
external leads, draft grading, feedback, shared calibration issues and
moderation snapshots.

All tables are additive. No P0 table is altered, so existing databases keep
their data; ``app.db.migrate`` backs up a database before creating these.
Point values are stored as decimal strings and computed with ``Decimal``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, utcnow

# ---------------------------------------------------------------- rubric


class RubricDocument(Base):
    __tablename__ = "rubric_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    source_path: Mapped[str] = mapped_column(Text)  # private provenance
    title: Mapped[str] = mapped_column(Text, default="")
    page_count: Mapped[int] = mapped_column(Integer)
    pages: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{page, raw_text, text}]
    extractor: Mapped[str] = mapped_column(String(64))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RubricVersion(Base):
    __tablename__ = "rubric_versions"
    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_rubric_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("rubric_documents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | active | superseded
    parser_version: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activated_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class RubricCriterion(Base):
    __tablename__ = "rubric_criteria"
    __table_args__ = (UniqueConstraint("rubric_version_id", "criterion_key", name="uq_criterion_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rubric_version_id: Mapped[int] = mapped_column(ForeignKey("rubric_versions.id"), index=True)
    criterion_key: Mapped[str] = mapped_column(String(160))   # stable, e.g. SA3.program_1.final_balance
    parent_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    assignment: Mapped[str] = mapped_column(String(16))       # SA3 or "shared"
    component_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    page: Mapped[int] = mapped_column(Integer)
    source_quote: Mapped[str] = mapped_column(Text)
    max_points: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # award | deduction | warning | resubmit | hold | allowance | info
    rule_kind: Mapped[str] = mapped_column(String(16))
    # explicit | interpretation_required | conflicting | external_evidence_required
    classification: Mapped[str] = mapped_column(String(32))
    authority: Mapped[str] = mapped_column(String(32), default="explicit_source")
    applies_to: Mapped[list[Any]] = mapped_column(JSON, default=list)
    exceptions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    dependencies: Mapped[list[Any]] = mapped_column(JSON, default=list)
    fact_binding: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_container: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class RubricIssue(Base):
    __tablename__ = "rubric_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    rubric_version_id: Mapped[int] = mapped_column(ForeignKey("rubric_versions.id"), index=True)
    assignment: Mapped[str] = mapped_column(String(16))
    criterion_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    # arithmetic_conflict | exemption_unresolved | partial_credit_undefined | external_evidence |
    # precedence_unresolved | escalation_indicated | scaffold_unknown
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_quote: Mapped[str] = mapped_column(Text, default="")
    blocks_total_proposals: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="open")
    resolution: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class AssessmentRubricLink(Base):
    __tablename__ = "assessment_rubric_links"
    __table_args__ = (UniqueConstraint("assessment_id", "rubric_version_id", name="uq_assessment_rubric"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    rubric_version_id: Mapped[int] = mapped_column(ForeignKey("rubric_versions.id"))
    assignment_code: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- evidence and components


class EvidenceManifestVersion(Base):
    __tablename__ = "evidence_manifests"
    __table_args__ = (UniqueConstraint("assessment_id", "version", name="uq_manifest_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ComponentMapping(Base):
    __tablename__ = "component_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"), index=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    role: Mapped[str] = mapped_column(String(64))           # program_1 | program_2 | ambiguous | whole_assignment
    status: Mapped[str] = mapped_column(String(16))         # proposed | confirmed | ambiguous
    authority: Mapped[str] = mapped_column(String(32), default="inferred_from_code")
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)
    mapper_version: Mapped[str] = mapped_column(String(32))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CriterionAssessment(Base):
    """Evidence status and judgment per submission/criterion (two independent axes)."""

    __tablename__ = "criterion_assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    criterion_key: Mapped[str] = mapped_column(String(160))
    rubric_version_id: Mapped[int] = mapped_column(ForeignKey("rubric_versions.id"))
    # available | incomplete | damaged | externally_required | confirmed_absent | not_applicable | unknown
    evidence_status: Mapped[str] = mapped_column(String(24))
    # met | not_met | partially_met | uncertain | needs_interpretation
    judgment: Mapped[str] = mapped_column(String(24))
    method: Mapped[str] = mapped_column(String(32))          # deterministic_fact | model | policy
    refs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_draft_points: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    policy_proposal_points: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    signature: Mapped[str] = mapped_column(String(64))
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    stale_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ObservedCriterionScore(Base):
    """Future imported historical criterion scores. Never populated from model output."""

    __tablename__ = "observed_criterion_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    criterion_key: Mapped[str] = mapped_column(String(160))
    points: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- external leads


class ExternalFlag(Base):
    __tablename__ = "external_flags"
    __table_args__ = (UniqueConstraint("import_sha256", "row_number", name="uq_flag_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_path: Mapped[str] = mapped_column(Text)
    row_number: Mapped[int] = mapped_column(Integer)
    row_sha256: Mapped[str] = mapped_column(String(64))
    original_flag_id: Mapped[str] = mapped_column(String(64), default="")
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)   # non-source columns, verbatim
    text_a: Mapped[str] = mapped_column(Text, default="")
    text_b: Mapped[str] = mapped_column(Text, default="")
    similarity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    similarity_raw: Mapped[str] = mapped_column(String(64), default="")
    rubric_note: Mapped[str] = mapped_column(Text, default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    assignment_hint: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    trust: Mapped[str] = mapped_column(String(32), default="unverified_imported_lead")
    checks: Mapped[list[Any]] = mapped_column(JSON, default=list)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExternalFlagLink(Base):
    __tablename__ = "external_flag_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    flag_id: Mapped[int] = mapped_column(ForeignKey("external_flags.id"), index=True)
    side: Mapped[str] = mapped_column(String(1))
    # exact_bundle | exact_file_in_larger_bundle | ambiguous | unresolved | confirmed
    status: Mapped[str] = mapped_column(String(32))
    candidates: Mapped[list[Any]] = mapped_column(JSON, default=list)
    metadata_check: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- draft grading


class DraftGradeRevision(Base):
    """Immutable working-grade revision. Imported totals stay in score_records."""

    __tablename__ = "draft_grade_revisions"
    __table_args__ = (UniqueConstraint("submission_id", "revision_number", name="uq_draft_revision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    parent_revision_id: Mapped[Optional[int]] = mapped_column(ForeignKey("draft_grade_revisions.id"), nullable=True)
    total: Mapped[str] = mapped_column(String(16))
    criterion_points: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    deduction_reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    grader_label: Mapped[str] = mapped_column(String(128))
    actor: Mapped[str] = mapped_column(String(128))
    # app_save | from_historical_record | simulated_replay
    origin: Mapped[str] = mapped_column(String(32), default="app_save")
    historical_score_record_id: Mapped[Optional[int]] = mapped_column(ForeignKey("score_records.id"), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GradingEvent(Base):
    """Actual application events with real times. CSV order is never turned into events."""

    __tablename__ = "grading_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(48))
    assessment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    submission_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    revision_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DraftFeedback(Base):
    __tablename__ = "draft_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("draft_grade_revisions.id"), index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("analysis_runs.id"), nullable=True)
    trigger: Mapped[str] = mapped_column(String(24), default="draft_save")  # draft_save | peer_refresh | guidance
    # queued | running | completed | partial | failed | stale | superseded
    status: Mapped[str] = mapped_column(String(16), default="queued")
    signature: Mapped[str] = mapped_column(String(64), default="")
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stages: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class FeedbackCard(Base):
    __tablename__ = "feedback_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    feedback_id: Mapped[int] = mapped_column(ForeignKey("draft_feedback.id"), index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("draft_grade_revisions.id"))
    card_type: Mapped[str] = mapped_column(String(40))
    criterion_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    component_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)
    rubric_refs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    comparison: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    question: Mapped[str] = mapped_column(Text, default="")
    issue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("calibration_issues.id"), nullable=True)
    guidance_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instructor_note_versions.id"), nullable=True)
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    signature: Mapped[str] = mapped_column(String(64))
    # open | kept | revised | deferred | linked | covered | suppressed | superseded | resolved
    status: Mapped[str] = mapped_column(String(16), default="open")
    status_reason: Mapped[str] = mapped_column(Text, default="")
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FeedbackDisposition(Base):
    """Immutable grader response to a card."""

    __tablename__ = "feedback_dispositions"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("feedback_cards.id"), index=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("draft_grade_revisions.id"))
    # keep_with_explanation | revise | defer | link_issue | request_calibration
    action: Mapped[str] = mapped_column(String(32))
    explanation: Mapped[str] = mapped_column(Text, default="")
    new_revision_id: Mapped[Optional[int]] = mapped_column(ForeignKey("draft_grade_revisions.id"), nullable=True)
    issue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("calibration_issues.id"), nullable=True)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- shared calibration issues


class CalibrationIssue(Base):
    __tablename__ = "calibration_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    assignment_code: Mapped[str] = mapped_column(String(16))
    criterion_key: Mapped[str] = mapped_column(String(160))
    component_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    rubric_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rubric_versions.id"), nullable=True)
    rule_hash: Mapped[str] = mapped_column(String(64), default="")
    issue_type: Mapped[str] = mapped_column(String(64))
    match_key: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    context_note: Mapped[str] = mapped_column(Text, default="")
    # open | awaiting_head_ta | resolved | deferred | superseded
    state: Mapped[str] = mapped_column(String(24), default="open")
    authority_needed: Mapped[str] = mapped_column(String(16), default="head_ta")
    merged_into_id: Mapped[Optional[int]] = mapped_column(ForeignKey("calibration_issues.id"), nullable=True)
    # interpretation | record_correction | case_specific | deferred
    resolution_type: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    resolution_note_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("instructor_note_versions.id"), nullable=True)
    resolution_text: Mapped[str] = mapped_column(Text, default="")
    resolved_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IssueCaseLink(Base):
    """Audited membership of a submission/case in a shared issue."""

    __tablename__ = "issue_case_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("calibration_issues.id"), index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    revision_id: Mapped[Optional[int]] = mapped_column(ForeignKey("draft_grade_revisions.id"), nullable=True)
    card_id: Mapped[Optional[int]] = mapped_column(ForeignKey("feedback_cards.id"), nullable=True)
    # auto_signature | grader_request | head_ta_merge | head_ta_split | covered_by_resolution
    link_method: Mapped[str] = mapped_column(String(32))
    evidence_signature: Mapped[str] = mapped_column(String(64), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    local_disposition: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class IssueEvent(Base):
    __tablename__ = "issue_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("calibration_issues.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class GuidanceRuleSpec(Base):
    """Typed, criterion-scoped rule attached 1:1 to an approved guidance version."""

    __tablename__ = "guidance_rule_specs"

    id: Mapped[int] = mapped_column(primary_key=True)
    note_version_id: Mapped[int] = mapped_column(ForeignKey("instructor_note_versions.id"), unique=True)
    issue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("calibration_issues.id"), nullable=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    criterion_key: Mapped[str] = mapped_column(String(160))
    component_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    condition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    exception_conditions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    point_treatment: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rubric_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rubric_versions.id"), nullable=True)
    is_anchor: Mapped[bool] = mapped_column(Boolean, default=False)
    anchor_examples: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModerationSnapshot(Base):
    __tablename__ = "moderation_snapshots"
    __table_args__ = (UniqueConstraint("assessment_id", "version", name="uq_snapshot_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    prerelease_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("analysis_runs.id"), nullable=True)
    revision_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    guidance_versions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    unresolved: Mapped[list[Any]] = mapped_column(JSON, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    csv_path: Mapped[str] = mapped_column(Text, default="")
    json_path: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

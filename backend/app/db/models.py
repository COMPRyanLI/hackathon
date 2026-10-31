"""SQLAlchemy models.

Immutable records (score records, source snapshots, decisions, audit events)
have no update paths in the service layer. Judgments that depend on guidance or
model settings carry ``is_current`` / ``stale_reason`` so history is kept when
they stop being current.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# ---------------------------------------------------------------- imports


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint(
            "dataset_key", "assessment_name", "csv_sha256", "source_manifest_sha256", "filename_regex",
            name="uq_import_inputs",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_key: Mapped[str] = mapped_column(String(64))
    assessment_name: Mapped[str] = mapped_column(String(64))
    csv_path: Mapped[str] = mapped_column(Text)
    csv_sha256: Mapped[str] = mapped_column(String(64))
    source_root: Mapped[str] = mapped_column(Text)
    source_manifest_sha256: Mapped[str] = mapped_column(String(64))
    filename_regex: Mapped[str] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    assumptions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("dataset_key", "code", name="uq_student_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_key: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(64))  # exact CSV string, leading zeros kept
    first_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"))


class ScoreRecord(Base):
    """One raw gradebook cell. Never updated after import."""

    __tablename__ = "score_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    assessment_column: Mapped[str] = mapped_column(String(64))
    raw_cell: Mapped[str] = mapped_column(Text)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # scored | blank | unparseable
    grader_label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    grader_raw_cell: Mapped[str] = mapped_column(Text, default="")
    csv_row_number: Mapped[int] = mapped_column(Integer)


class AssessmentVersion(Base):
    __tablename__ = "assessment_versions"
    __table_args__ = (UniqueConstraint("dataset_key", "name", "version", name="uq_assessment_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    # pending | synthetic_demo | confirmed_real
    instructions_status: Mapped[str] = mapped_column(String(32), default="pending")
    instructions_text: Mapped[str] = mapped_column(Text, default="")
    reference_solution_text: Mapped[str] = mapped_column(Text, default="")
    reference_solution_status: Mapped[str] = mapped_column(String(32), default="absent")
    max_points_confirmed: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    multi_file_scope_note: Mapped[str] = mapped_column(Text, default="")
    context_version: Mapped[int] = mapped_column(Integer, default=1)
    context_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Submission(Base):
    """All files for one student within one assessment version (a bundle)."""

    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("assessment_id", "student_id", name="uq_submission"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"))
    bundle_hash: Mapped[str] = mapped_column(String(64))
    # single_artifact | component_scope_unconfirmed | confirmed
    component_scope_status: Mapped[str] = mapped_column(String(40))

    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="submission", order_by="Artifact.position"
    )


class Artifact(Base):
    """One distinct file content inside a bundle (duplicates collapse to aliases)."""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # ordered by content hash, never by filename
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    line_count: Mapped[int] = mapped_column(Integer)
    encoding: Mapped[str] = mapped_column(String(32))
    source_text: Mapped[str] = mapped_column(Text)
    parse_status: Mapped[str] = mapped_column(String(24))  # ok | syntax_error
    parse_error: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    parser_version: Mapped[str] = mapped_column(String(32))
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cross_student_copies: Mapped[int] = mapped_column(Integer, default=0)

    submission: Mapped[Submission] = relationship(back_populates="artifacts")
    aliases: Mapped[list["ArtifactAlias"]] = relationship(back_populates="artifact")
    chunks: Mapped[list["Chunk"]] = relationship(order_by="Chunk.start_line")
    issues: Mapped[list["ExtractionIssue"]] = relationship()


class ArtifactAlias(Base):
    """Private provenance: original path of each file that had this content."""

    __tablename__ = "artifact_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"), index=True)
    relative_path: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)

    artifact: Mapped[Artifact] = relationship(back_populates="aliases")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"), index=True)
    chunk_key: Mapped[str] = mapped_column(String(16))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))


class ExtractionIssue(Base):
    __tablename__ = "extraction_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(16))  # info | warning | error
    line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------- analysis


class SubmissionEvidence(Base):
    """Validated analyst output, cached by content/context/model/prompt/schema."""

    __tablename__ = "submission_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    context_hash: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    model_digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))  # valid | partially_valid | failed
    evidence: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    rejected_claims: Mapped[list[Any]] = mapped_column(JSON, default=list)
    summary_text: Mapped[str] = mapped_column(Text, default="")
    error_kind: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmbeddingRecord(Base):
    __tablename__ = "embedding_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True)
    representation: Mapped[str] = mapped_column(String(32))  # source_v1 | summary_v1
    input_sha256: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    model_digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    preprocessing: Mapped[str] = mapped_column(String(64))
    dims: Mapped[int] = mapped_column(Integer, default=0)
    vector: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16))  # ok | failed
    error_kind: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisRun(Base):
    """Persistent job record. One worker processes queued runs in order."""

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # pipeline | note_scan
    # queued | running | succeeded | partial | failed | cancelled | interrupted
    status: Mapped[str] = mapped_column(String(16), default="queued")
    stage: Mapped[str] = mapped_column(String(32), default="")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CandidatePair(Base):
    __tablename__ = "candidate_pairs"
    __table_args__ = (UniqueConstraint("run_id", "submission_a_id", "submission_b_id", name="uq_run_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    submission_a_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    submission_b_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    fused_score: Mapped[float] = mapped_column(Float, default=0.0)
    channels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    contributing: Mapped[list[Any]] = mapped_column(JSON, default=list)
    scope: Mapped[str] = mapped_column(String(40))
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    selection_reason: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    is_random_control: Mapped[bool] = mapped_column(Boolean, default=False)


class PairComparison(Base):
    __tablename__ = "pair_comparisons"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    submission_a_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    submission_b_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    model_digest: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    context_hash: Mapped[str] = mapped_column(String(64))
    guidance_hash: Mapped[str] = mapped_column(String(64))
    guidance_versions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24))  # valid | partially_valid | failed
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    rejected_claims: Mapped[list[Any]] = mapped_column(JSON, default=list)
    sent_content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_kind: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    stale_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewCase(Base):
    __tablename__ = "review_cases"
    __table_args__ = (UniqueConstraint("run_id", "candidate_pair_id", "origin", name="uq_case"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    candidate_pair_id: Mapped[int] = mapped_column(ForeignKey("candidate_pairs.id"))
    comparison_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pair_comparisons.id"), nullable=True)
    origin: Mapped[str] = mapped_column(String(32))  # model_review | deterministic_exact
    category: Mapped[str] = mapped_column(String(48))
    submission_a_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    submission_b_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    score_a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_b: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_gap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    grader_a: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    grader_b: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    headline: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | decided
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    stale_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    guidance_versions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewDecision(Base):
    """Immutable human decision on a review case."""

    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("review_cases.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    comment: Mapped[str] = mapped_column(Text, default="")
    proposed_score_a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    proposed_score_b: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    guidance_versions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- guidance


class InstructorNote(Base):
    __tablename__ = "instructor_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment_versions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InstructorNoteVersion(Base):
    __tablename__ = "instructor_note_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("instructor_notes.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    # comment | draft | approved | superseded
    status: Mapped[str] = mapped_column(String(16))
    original_text: Mapped[str] = mapped_column(Text)
    interpretation: Mapped[str] = mapped_column(Text, default="")
    # clarification | prospective_policy | retrospective_review
    note_kind: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    scope_component: Mapped[str] = mapped_column(String(128), default="")
    scope_requirement: Mapped[str] = mapped_column(String(256), default="")
    examples: Mapped[list[Any]] = mapped_column(JSON, default=list)
    applicability_conditions: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    exceptions: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    scoring_implication: Mapped[str] = mapped_column(Text, default="no_numeric_rule")
    source_case_id: Mapped[Optional[int]] = mapped_column(ForeignKey("review_cases.id"), nullable=True)
    supersedes_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("instructor_note_versions.id"), nullable=True
    )
    coexists_with: Mapped[list[Any]] = mapped_column(JSON, default=list)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class NoteApplicability(Base):
    __tablename__ = "note_applicability"
    __table_args__ = (UniqueConstraint("note_version_id", "submission_id", name="uq_applicability"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    note_version_id: Mapped[int] = mapped_column(ForeignKey("instructor_note_versions.id"), index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("analysis_runs.id"), nullable=True)
    # pending | applies | does_not_apply | uncertain
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # deterministic_condition | model | not_evaluated
    method: Mapped[str] = mapped_column(String(32), default="not_evaluated")
    rationale: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)
    priority_rank: Mapped[int] = mapped_column(Integer, default=0)
    previously_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    stale_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApplicabilityDecision(Base):
    __tablename__ = "applicability_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    applicability_id: Mapped[int] = mapped_column(ForeignKey("note_applicability.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    comment: Mapped[str] = mapped_column(Text, default="")
    proposed_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(48))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

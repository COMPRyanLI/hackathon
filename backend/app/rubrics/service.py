"""Rubric import, storage and lookup."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.db.models import AssessmentVersion
from app.db.models_p1 import AssessmentRubricLink, RubricCriterion, RubricDocument, RubricIssue, RubricVersion
from app.rubrics.parse import PARSER_VERSION, criteria_as_dicts, normalize, parse_guide
from app.rubrics.validate import validate


class RubricError(ValueError):
    pass


def extract_pdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader

    return [page.extract_text(extraction_mode="layout") or "" for page in PdfReader(str(path)).pages]


def import_pages(session: Session, pages: list[str], source_path: str, sha256: str, actor: str,
                 extractor: str, is_synthetic: bool = False, title: str = "") -> tuple[RubricVersion, bool]:
    """Store a draft parse; idempotent per document hash and parser version."""
    document = session.scalar(select(RubricDocument).where(RubricDocument.sha256 == sha256))
    if document is not None:
        version = session.scalar(select(RubricVersion).where(RubricVersion.document_id == document.id)
                                 .order_by(RubricVersion.version.desc()).limit(1))
        if version is not None and version.parser_version == PARSER_VERSION:
            return version, False
        next_version = (version.version + 1) if version else 1
    else:
        next_version = 1
        document = RubricDocument(sha256=sha256, source_path=source_path, title=title, page_count=len(pages),
                              pages=[{"page": i + 1, "raw_text": p, "text": normalize(p)} for i, p in enumerate(pages)],
                              extractor=extractor, is_synthetic=is_synthetic, actor=actor)
        session.add(document)
    session.flush()
    parsed = parse_guide(pages)
    rows = criteria_as_dicts(parsed)
    version = RubricVersion(document_id=document.id, version=next_version, status="draft", parser_version=PARSER_VERSION,
                            content_hash=audit.stable_hash(rows), actor=actor)
    session.add(version)
    session.flush()
    for order, c in enumerate(parsed.criteria):
        session.add(RubricCriterion(
            rubric_version_id=version.id, criterion_key=c.key, parent_key=c.parent_key, assignment=c.assignment,
            component_role=c.component_role, title=c.title, page=c.page, source_quote=c.source_quote,
            max_points=None if c.max_points is None else str(c.max_points), rule_kind=c.rule_kind,
            classification=c.classification, applies_to=c.applies_to, exceptions=c.exceptions,
            dependencies=c.dependencies, fact_binding=c.fact_binding, is_container=c.is_container,
            sort_order=order))
    for issue in validate(parsed):
        session.add(RubricIssue(rubric_version_id=version.id, assignment=issue.assignment,
                                criterion_key=issue.criterion_key, kind=issue.kind, message=issue.message,
                                page=issue.page, source_quote=issue.source_quote,
                                blocks_total_proposals=issue.blocks_total_proposals))
    session.flush()
    audit.record(session, actor, "rubric_imported", "rubric_version", version.id, document_sha256=sha256,
                 criteria=len(parsed.criteria), synthetic=is_synthetic)
    return version, True


def reparse(session: Session, version_id: int, actor: str) -> tuple[RubricVersion, bool]:
    previous = session.get(RubricVersion, version_id)
    if previous is None:
        raise RubricError("Rubric version not found")
    document = session.get(RubricDocument, previous.document_id)
    pages = [p.get("raw_text", p["text"]) for p in document.pages]
    path = Path(document.source_path)
    # Only re-read an unchanged local original; retain old document/version rows.
    if path.suffix.lower() == ".pdf" and path.is_file() and audit.sha256_bytes(path.read_bytes()) == document.sha256:
        pages = extract_pdf_pages(path)
    return import_pages(session, pages, document.source_path, document.sha256, actor,
                        document.extractor, document.is_synthetic, document.title)


def import_pdf(session: Session, path: Path, actor: str) -> tuple[RubricVersion, bool]:
    if not path.is_file():
        raise RubricError(f"Rubric PDF not found: {path}")
    return import_pages(session, extract_pdf_pages(path), str(path), audit.sha256_bytes(path.read_bytes()), actor,
                        "pypdf", title=path.name)


def import_text(session: Session, path: Path, actor: str, is_synthetic: bool) -> tuple[RubricVersion, bool]:
    """Text guide with '--- PAGE n ---' separators (used for the synthetic fixture)."""
    text = path.read_text(encoding="utf-8")
    pages = [p for p in text.split("--- PAGE") if p.strip()]
    pages = [p.split("---", 1)[1] if p.lstrip()[:1].isdigit() and "---" in p else p for p in pages]
    return import_pages(session, pages, str(path), audit.sha256_text(text), actor, "text", is_synthetic, path.name)


def activate(session: Session, version_id: int, actor: str) -> RubricVersion:
    """Mark a parsed rubric as the version graders use. Open issues stay open and visible."""
    from app.db.models import utcnow

    version = session.get(RubricVersion, version_id)
    if version is None:
        raise RubricError("Rubric version not found")
    for other in session.scalars(select(RubricVersion).where(RubricVersion.document_id == version.document_id,
                                                             RubricVersion.status == "active")):
        other.status = "superseded"
    version.status, version.activated_by, version.activated_at = "active", actor, utcnow()
    audit.record(session, actor, "rubric_activated", "rubric_version", version.id)
    return version


def link_assessment(session: Session, assessment_id: int, version_id: int, code: str, actor: str) -> AssessmentRubricLink:
    from app.db.models import AnalysisRun, ImportBatch, Submission
    from app.importers.service import source_assignment_warning
    assessment = session.get(AssessmentVersion, assessment_id)
    version = session.get(RubricVersion, version_id)
    if assessment is None or version is None:
        raise RubricError("Assessment or rubric version not found")
    mismatch = source_assignment_warning(session.get(ImportBatch, assessment.batch_id).source_root, assessment.name)
    if mismatch:
        raise RubricError(mismatch)
    if code != assessment.name:
        raise RubricError("Rubric assignment must match the assessment")
    document = session.get(RubricDocument, version.document_id)
    if assessment.is_synthetic != document.is_synthetic:
        raise RubricError("Synthetic and real data cannot share a rubric. Select the matching guide.")
    existing = session.scalar(select(AssessmentRubricLink).where(
        AssessmentRubricLink.assessment_id == assessment_id, AssessmentRubricLink.rubric_version_id == version_id))
    if existing:
        latest = session.scalar(select(AssessmentRubricLink.id).where(
            AssessmentRubricLink.assessment_id == assessment_id).order_by(AssessmentRubricLink.id.desc()).limit(1))
        if latest != existing.id:
            raise RubricError("This historical link is no longer current; link a new rubric version instead.")
        return existing
    if session.scalar(select(AnalysisRun.id).where(AnalysisRun.assessment_id == assessment_id,
                                                   AnalysisRun.status == "running")):
        raise RubricError("Wait for the active job to finish before changing the linked rubric")
    if not session.scalar(select(func.count(RubricCriterion.id)).where(
            RubricCriterion.rubric_version_id == version_id, RubricCriterion.assignment == code)):
        raise RubricError(f"Rubric version {version_id} has no criteria for {code}")
    link = AssessmentRubricLink(assessment_id=assessment_id, rubric_version_id=version_id, assignment_code=code,
                                actor=actor)
    session.add(link)
    session.flush()
    audit.record(session, actor, "rubric_linked", "assessment_version", assessment_id, rubric_version_id=version_id,
                 assignment=code)
    from app.db.models_p1 import CriterionAssessment, DraftFeedback, FeedbackCard
    from app.grading.feedback import enqueue_refresh
    reason = "Linked rubric changed; feedback is being refreshed."
    for fb in session.scalars(select(DraftFeedback).where(DraftFeedback.assessment_id == assessment_id)):
        fb.status, fb.error = "stale", reason
        for card in session.scalars(select(FeedbackCard).where(FeedbackCard.feedback_id == fb.id,
                                                               FeedbackCard.status == "open")):
            card.status, card.status_reason = "superseded", reason
    for row in session.scalars(select(CriterionAssessment).join(Submission,
            Submission.id == CriterionAssessment.submission_id).where(Submission.assessment_id == assessment_id)):
        row.is_current, row.stale_reason = False, reason
    # Refresh only when there are existing drafts; a rubric link is not a draft save.
    from app.db.models_p1 import DraftGradeRevision
    if session.scalar(select(DraftGradeRevision.id).where(DraftGradeRevision.assessment_id == assessment_id).limit(1)):
        enqueue_refresh(session, assessment_id, None, actor, reason, all_drafted=True)
    return link


def assessment_rubric(session: Session, assessment: AssessmentVersion) -> dict[str, Any] | None:
    link = session.scalar(select(AssessmentRubricLink).where(AssessmentRubricLink.assessment_id == assessment.id)
                          .order_by(AssessmentRubricLink.id.desc()).limit(1))
    if link is None:
        return None
    version = session.get(RubricVersion, link.rubric_version_id)
    code = link.assignment_code
    criteria = list(session.scalars(select(RubricCriterion).where(
        RubricCriterion.rubric_version_id == version.id,
        RubricCriterion.assignment.in_((code, "shared"))).order_by(RubricCriterion.sort_order)))
    issues = list(session.scalars(select(RubricIssue).where(
        RubricIssue.rubric_version_id == version.id, RubricIssue.assignment.in_((code, "shared")))))
    return {"version": version, "code": code, "criteria": criteria, "issues": issues,
            "by_key": {c.criterion_key: c for c in criteria},
            "blocking": [i.message for i in issues if i.blocks_total_proposals and i.status == "open"
                         and i.assignment == code]}


def criterion_hash(criterion: RubricCriterion) -> str:
    return audit.stable_hash([criterion.criterion_key, criterion.title, criterion.max_points, criterion.rule_kind,
                              criterion.classification, criterion.exceptions])

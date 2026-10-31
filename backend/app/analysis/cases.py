"""Review cases: validated comparisons joined with ORIGINAL recorded scores in
application code, after semantic review. Wording stays provisional; a score
gap on a whole assignment never implies a question-level deduction."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.analysis.context import has_instructions
from app.analysis.schemas import CASE_CATEGORIES
from app.db.models import (
    AnalysisRun,
    AssessmentVersion,
    CandidatePair,
    PairComparison,
    ReviewCase,
    ScoreRecord,
    Student,
    Submission,
)

RELATION_WEIGHT = {"apparently_equivalent": 3.0, "materially_different": 1.5, "insufficient_evidence": 1.0,
                   "not_comparable": 0.5}


def score_map(session: Session, assessment: AssessmentVersion) -> dict[int, dict[str, Any]]:
    """submission_id -> original score record for this assessment column."""
    rows = session.execute(
        select(Submission.id, Student.code, ScoreRecord)
        .join(Student, Student.id == Submission.student_id)
        .join(ScoreRecord, (ScoreRecord.student_id == Submission.student_id)
              & (ScoreRecord.batch_id == assessment.batch_id)
              & (ScoreRecord.assessment_column == assessment.name))
        .where(Submission.assessment_id == assessment.id)
    ).all()
    return {sid: {"student_id": code, "score": rec.score, "status": rec.status, "raw": rec.raw_cell,
                  "grader": rec.grader_label} for sid, code, rec in rows}


def score_gap(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float | None:
    if not a or not b or a["score"] is None or b["score"] is None:
        return None
    return round(abs(a["score"] - b["score"]), 4)


def default_question(assessment: AssessmentVersion) -> str:
    if has_instructions(assessment):
        return "Which stated requirement, if any, explains the difference in recorded scores?"
    return ("What does this assignment assess (specified content, formatting, comments, successful execution, "
            "or effort), and does that explain the difference in recorded scores?")


def _headline(relation: str | None, gap: float | None, a: dict | None, b: dict | None, question: str,
              is_mock: bool, exact: bool) -> str:
    prefix = "[MOCK - not a real finding] " if is_mock else ""
    if gap is None:
        return prefix + "A recorded score is blank for one or both submissions; comparison kept for context only."
    if gap == 0:
        return prefix + "Same recorded score; comparison retained as a consistency reference."
    scores = f"({a['raw']} vs {b['raw']})"
    if exact:
        lead = "A byte-identical file appears in both submissions, and the recorded scores differ"
    elif relation == "apparently_equivalent":
        lead = "Potentially comparable work received different recorded scores"
    elif relation == "materially_different":
        lead = "Retrieved as similar; the reviewer reports material differences. Recorded scores differ"
    else:
        lead = "Retrieved as similar; the relation could not be determined. Recorded scores differ"
    return f"{prefix}{lead} {scores}; clarify: {question}"


UNDECIDED_CATEGORIES = ("unspecified_style_format_expectation", "incomplete_or_anonymized_evidence",
                        "conflicting_guidance", "recording_administrative_context")


def _category(result: dict[str, Any] | None, pair: CandidatePair) -> str:
    if not result:
        return "incomplete_or_anonymized_evidence"
    suggested = result.get("suggested_category")
    if result.get("relation") in ("insufficient_evidence", "not_comparable"):
        # Undecided comparisons keep the reviewer's category only when it names missing context.
        return suggested if suggested in UNDECIDED_CATEGORIES else "incomplete_or_anonymized_evidence"
    has_identical = any(c.get("identical_content") for c in pair.contributing)
    if suggested == "exact_content_discrepancy" and not has_identical:
        return "unspecified_style_format_expectation"
    return suggested if suggested in CASE_CATEGORIES else "incomplete_or_anonymized_evidence"


def _retire_older(session: Session, run: AnalysisRun, assessment: AssessmentVersion, pair: CandidatePair,
                  origin: str) -> None:
    """A newer comparison of the same pair replaces older current cases; history is kept."""
    session.execute(
        update(ReviewCase)
        .where(ReviewCase.assessment_id == assessment.id, ReviewCase.origin == origin,
               ReviewCase.submission_a_id == pair.submission_a_id, ReviewCase.submission_b_id == pair.submission_b_id,
               ReviewCase.run_id != run.id, ReviewCase.is_current.is_(True))
        .values(is_current=False, stale_reason=f"Superseded by a newer comparison in run {run.id}"))


def make_case(session: Session, run: AnalysisRun, assessment: AssessmentVersion, pair: CandidatePair,
              comparison: PairComparison, scores: dict[int, dict[str, Any]]) -> ReviewCase:
    existing = session.scalar(select(ReviewCase).where(
        ReviewCase.run_id == run.id, ReviewCase.candidate_pair_id == pair.id, ReviewCase.origin == "model_review"))
    if existing is not None:
        return existing
    _retire_older(session, run, assessment, pair, "model_review")
    a, b = scores.get(pair.submission_a_id), scores.get(pair.submission_b_id)
    gap = score_gap(a, b)
    result = comparison.result or {}
    question = (result.get("clarification_question") or "").strip() or default_question(assessment)
    relation = result.get("relation")
    case = ReviewCase(
        run_id=run.id, assessment_id=assessment.id, candidate_pair_id=pair.id, comparison_id=comparison.id,
        origin="model_review", category=_category(result, pair),
        submission_a_id=pair.submission_a_id, submission_b_id=pair.submission_b_id,
        score_a=a["score"] if a else None, score_b=b["score"] if b else None, score_gap=gap,
        grader_a=a["grader"] if a else None, grader_b=b["grader"] if b else None,
        headline=_headline(relation, gap, a, b, question, comparison.is_mock, False),
        question=question, is_mock=comparison.is_mock,
        priority=(gap or 0) * 10 + RELATION_WEIGHT.get(relation or "", 0.5) + pair.fused_score,
        guidance_versions=comparison.guidance_versions,
    )
    session.add(case)
    session.flush()
    return case


def make_exact_case(session: Session, run: AnalysisRun, assessment: AssessmentVersion, pair: CandidatePair,
                    scores: dict[int, dict[str, Any]], guidance_versions: list) -> ReviewCase | None:
    """Deterministic case: byte-identical content across bundles AND differing recorded scores."""
    a, b = scores.get(pair.submission_a_id), scores.get(pair.submission_b_id)
    gap = score_gap(a, b)
    if not gap:
        return None
    existing = session.scalar(select(ReviewCase).where(
        ReviewCase.run_id == run.id, ReviewCase.candidate_pair_id == pair.id,
        ReviewCase.origin == "deterministic_exact"))
    if existing is not None:
        return existing
    _retire_older(session, run, assessment, pair, "deterministic_exact")
    whole = pair.channels.get("exact", {}).get("score") == 1.0 and all(
        c.get("identical_content") for c in pair.contributing)
    question = ("Is the identical file shared course material or a required component, and does it bear on the "
                "recorded scores? Remaining files were not compared by this deterministic check.")
    case = ReviewCase(
        run_id=run.id, assessment_id=assessment.id, candidate_pair_id=pair.id, comparison_id=None,
        origin="deterministic_exact", category="exact_content_discrepancy",
        submission_a_id=pair.submission_a_id, submission_b_id=pair.submission_b_id,
        score_a=a["score"], score_b=b["score"], score_gap=gap, grader_a=a["grader"], grader_b=b["grader"],
        headline=_headline(None, gap, a, b, question, False, True)
        + (" Component comparison only; not evidence of misconduct." if not whole else
           " Not evidence of misconduct."),
        question=question, is_mock=False, priority=gap * 10 + 2.0 + pair.fused_score,
        guidance_versions=guidance_versions,
    )
    session.add(case)
    session.flush()
    return case

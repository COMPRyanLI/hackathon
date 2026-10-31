"""Grader responses to feedback cards (immutable, audited).

keep_with_explanation resolves only this case: it never approves a standard
or closes anyone else's case. revise is the only path that changes a draft,
and it is always an explicit new revision. request_calibration creates (or
reuses) one shared issue and links every open matching card to it.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db.models_p1 import CalibrationIssue, DraftFeedback, FeedbackCard, FeedbackDisposition, GradingEvent, IssueCaseLink
from app.calibration import issues as issue_service
from app.grading.drafts import DraftConflict, DraftError, current_revision, save_draft

ACTIONS = ("keep_with_explanation", "revise", "defer", "link_issue", "request_calibration")


def respond(session: Session, card_id: int, action: str, actor: str, *, revision_id: int, explanation: str = "",
            issue_id: int | None = None, new_total: Any = None, criterion_points: dict | None = None,
            grader_label: str = "", idempotency_key: str | None = None,
            criteria: dict | None = None) -> dict[str, Any]:
    card = session.get(FeedbackCard, card_id)
    if card is None:
        raise DraftError("Feedback card not found")
    if action not in ACTIONS:
        raise DraftError(f"action must be one of {', '.join(ACTIONS)}")
    current = current_revision(session, card.submission_id)
    feedback = session.get(DraftFeedback, card.feedback_id)
    if feedback.status in ("stale", "superseded"):
        raise DraftConflict("Feedback inputs changed; refresh before acting on this card.", current)
    if current is None or current.id != revision_id or card.revision_id != revision_id:
        raise DraftConflict("This card belongs to an older revision; open the current feedback.", current)
    result: dict[str, Any] = {"card_id": card.id, "action": action}
    new_revision = None
    if action == "keep_with_explanation":
        if not explanation.strip():
            raise DraftError("Explain why the draft stays as it is")
        card.status = "kept"
        if card.issue_id:
            link = session.scalar(select(IssueCaseLink).where(IssueCaseLink.issue_id == card.issue_id,
                                                              IssueCaseLink.submission_id == card.submission_id,
                                                              IssueCaseLink.active.is_(True)))
            if link is not None:
                link.local_disposition = "kept_with_explanation"  # local only; issue state unchanged
    elif action == "defer":
        card.status = "deferred"
    elif action == "revise":
        if new_total is None:
            raise DraftError("A revision needs the new total")
        new_revision, _, feedback = save_draft(
            session, card.submission_id, total=new_total, criterion_points=criterion_points,
            grader_label=grader_label or current.grader_label, actor=actor, expected_revision_id=revision_id,
            note=explanation, idempotency_key=idempotency_key, criteria=criteria,
            deduction_reasons=current.deduction_reasons)
        card.status = "revised"
        result["new_revision_id"] = new_revision.id
        result["feedback_id"] = feedback.id if feedback else None
    elif action == "link_issue":
        issue = session.get(CalibrationIssue, issue_id) if issue_id else None
        if issue is None or issue.assessment_id != current.assessment_id:
            raise DraftError("Choose an existing shared issue in this assignment")
        issue_service.link_case(session, issue, card.submission_id, actor, "grader_request", revision_id, card.id,
                                card.signature)
        card.issue_id, card.status = issue.id, "linked"
        result["issue_id"] = issue.id
    elif action == "request_calibration":
        spec = (card.comparison or {}).get("issue_spec")
        if not spec:
            raise DraftError("This card has no policy question to calibrate")
        issue, created = issue_service.find_or_create(
            session, assessment_id=current.assessment_id, assignment_code=_code(session, current.assessment_id),
            criterion_key=spec["criterion_key"], component_role=spec["component_role"],
            rubric_version_id=spec["rubric_version_id"], rule_hash=spec["rule_hash"], issue_type=spec["issue_type"],
            question_key=spec["question_key"], question=spec["question"], context_note=spec.get("context_note", ""),
            actor=actor)
        issue_service.link_case(session, issue, card.submission_id, actor, "grader_request", revision_id, card.id,
                                card.signature)
        issue_service.request_head_ta(session, issue.id, actor, card.submission_id)
        card.issue_id, card.status = issue.id, "linked"
        linked = _link_matching_open_cards(session, issue, spec, actor)
        result.update(issue_id=issue.id, issue_created=created, also_linked=linked)
    disposition = FeedbackDisposition(card_id=card.id, revision_id=revision_id, action=action,
                                      explanation=explanation.strip(), new_revision_id=new_revision.id if new_revision else None,
                                      issue_id=card.issue_id, actor=actor)
    session.add(disposition)
    session.add(GradingEvent(actor=actor, event_type="feedback_disposition", assessment_id=current.assessment_id,
                             submission_id=card.submission_id, revision_id=revision_id,
                             detail={"card_id": card.id, "action": action, "issue_id": card.issue_id}))
    session.flush()
    audit.record(session, actor, "feedback_disposition", "feedback_card", card.id, disposition_action=action,
                 issue_id=card.issue_id, new_revision_id=result.get("new_revision_id"))
    result["disposition_id"] = disposition.id
    return result


def _code(session: Session, assessment_id: int) -> str:
    from app.db.models import AssessmentVersion

    return session.get(AssessmentVersion, assessment_id).name


def _link_matching_open_cards(session: Session, issue: CalibrationIssue, spec: dict, actor: str) -> int:
    """Batch: other submissions' current open cards with the same question join the same issue."""
    linked = 0
    for card in session.scalars(select(FeedbackCard).where(FeedbackCard.status == "open",
                                                           FeedbackCard.issue_id.is_(None))):
        other = (card.comparison or {}).get("issue_spec")
        if not other or other.get("question_key") != spec["question_key"]:
            continue
        key = issue_service.match_key(issue.assessment_id, other["criterion_key"], other["component_role"],
                                      other["rubric_version_id"], other["rule_hash"], other["issue_type"],
                                      other["question_key"])
        if key != issue.match_key:
            continue
        current = current_revision(session, card.submission_id)
        if current is None or current.id != card.revision_id:
            continue
        _, created = issue_service.link_case(session, issue, card.submission_id, actor, "auto_signature",
                                             card.revision_id, card.id, card.signature)
        card.issue_id = issue.id
        linked += int(created)
    return linked

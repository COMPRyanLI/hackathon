"""Shared calibration issues: ask the head TA once, apply consistently.

Matching is deterministic: assignment, criterion, component, rubric version,
material rule hash, issue type and policy-question key form the match key.
Similar wording never merges issues; only an audited head-TA merge does.
Affected counts are distinct linked submissions, so repeated or idempotent
saves cannot inflate them. Interpretation resolutions reuse the versioned
guidance service (notes.approve), which invalidates dependent judgments and
scans every in-scope submission. No resolution changes any grade.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.db.models import AnalysisRun, InstructorNoteVersion, utcnow
from app.db.models_p1 import CalibrationIssue, FeedbackCard, GuidanceRuleSpec, IssueCaseLink, IssueEvent
from app.calibration.rules import validate_scoped, validate_treatment
from app.guidance import notes as note_service

OPEN_STATES = ("open", "awaiting_head_ta", "deferred")
RESOLUTION_TYPES = ("interpretation", "record_correction", "case_specific")


class IssueError(ValueError):
    pass


class IssueConflict(IssueError):
    pass


def match_key(assessment_id: int, criterion_key: str, component_role: str | None, rubric_version_id: int | None,
              rule_hash: str, issue_type: str, question_key: str) -> str:
    # A rubric-free issue (no rubric version) never matches a confirmed-rubric issue.
    return audit.stable_hash([assessment_id, criterion_key, component_role or "", rubric_version_id or "no_rubric",
                              rule_hash, issue_type, question_key])


def affected_count(session: Session, issue_id: int) -> int:
    return session.scalar(select(func.count(func.distinct(IssueCaseLink.submission_id))).where(
        IssueCaseLink.issue_id == issue_id, IssueCaseLink.active.is_(True))) or 0


def _event(session: Session, issue: CalibrationIssue, actor: str, action: str, **detail: Any) -> None:
    session.add(IssueEvent(issue_id=issue.id, actor=actor, action=action, detail=detail))
    audit.record(session, actor, f"issue_{action}", "calibration_issue", issue.id, **detail)


def link_case(session: Session, issue: CalibrationIssue, submission_id: int, actor: str, method: str,
              revision_id: int | None = None, card_id: int | None = None,
              evidence_signature: str = "") -> tuple[IssueCaseLink, bool]:
    """Idempotent: one active link per (issue, submission)."""
    link = session.scalar(select(IssueCaseLink).where(IssueCaseLink.issue_id == issue.id,
                                                      IssueCaseLink.submission_id == submission_id,
                                                      IssueCaseLink.active.is_(True)))
    if link is not None:
        link.revision_id = revision_id or link.revision_id
        link.card_id = card_id or link.card_id
        if evidence_signature and evidence_signature != link.evidence_signature:
            link.evidence_signature = evidence_signature
        return link, False
    link = IssueCaseLink(issue_id=issue.id, submission_id=submission_id, revision_id=revision_id, card_id=card_id,
                         link_method=method, evidence_signature=evidence_signature, actor=actor)
    session.add(link)
    session.flush()
    _event(session, issue, actor, "case_linked", submission_id=submission_id, method=method)
    return link, True


def find_or_create(session: Session, *, assessment_id: int, assignment_code: str, criterion_key: str,
                   component_role: str | None, rubric_version_id: int | None, rule_hash: str, issue_type: str,
                   question_key: str, question: str, context_note: str, actor: str) -> tuple[CalibrationIssue, bool]:
    key = match_key(assessment_id, criterion_key, component_role, rubric_version_id, rule_hash, issue_type, question_key)
    issue = session.scalar(select(CalibrationIssue).where(CalibrationIssue.match_key == key,
                                                          CalibrationIssue.state != "superseded")
                           .order_by(CalibrationIssue.id.desc()).limit(1))
    if issue is not None:
        return issue, False
    issue = CalibrationIssue(assessment_id=assessment_id, assignment_code=assignment_code, criterion_key=criterion_key,
                             component_role=component_role, rubric_version_id=rubric_version_id, rule_hash=rule_hash,
                             issue_type=issue_type, match_key=key, question=question, context_note=context_note,
                             state="open", created_by=actor)
    session.add(issue)
    session.flush()
    _event(session, issue, actor, "created", issue_type=issue_type, criterion_key=criterion_key)
    return issue, True


def _check_version(issue: CalibrationIssue, expected_version: int | None) -> None:
    if expected_version is not None and expected_version != issue.version:
        raise IssueConflict(f"Issue changed (version {issue.version}); reload before acting.")


def _bump(issue: CalibrationIssue) -> None:
    issue.version += 1
    issue.updated_at = utcnow()


def request_head_ta(session: Session, issue_id: int, actor: str, submission_id: int | None = None) -> CalibrationIssue:
    issue = _get(session, issue_id)
    if issue.state == "open":
        issue.state = "awaiting_head_ta"
        _bump(issue)
        _event(session, issue, actor, "requested", submission_id=submission_id)
    return issue


def defer(session: Session, issue_id: int, actor: str, expected_version: int | None, reason: str) -> CalibrationIssue:
    issue = _get(session, issue_id)
    _check_version(issue, expected_version)
    if issue.state not in ("open", "awaiting_head_ta"):
        raise IssueError(f"Cannot defer an issue in state {issue.state}")
    issue.state = "deferred"
    _bump(issue)
    _event(session, issue, actor, "deferred", reason=reason)
    return issue


def _get(session: Session, issue_id: int) -> CalibrationIssue:
    issue = session.get(CalibrationIssue, issue_id)
    if issue is None:
        raise IssueError("Issue not found")
    return issue


def resolve(session: Session, issue_id: int, *, actor: str, expected_version: int | None, resolution_type: str,
            text: str, note_kind: str | None = None, condition: dict | None = None,
            exception_conditions: list[dict] | None = None, point_treatment: dict | None = None,
            criterion_key: str | None = None, coexists_with: list[int] | None = None,
            provider_mode: str | None = None, is_anchor: bool = False,
            anchor_examples: list[dict] | None = None) -> tuple[CalibrationIssue, AnalysisRun | None]:
    issue = _get(session, issue_id)
    _check_version(issue, expected_version)
    if issue.state not in OPEN_STATES:
        raise IssueError(f"Issue is {issue.state}; supersede its guidance instead of resolving again.")
    if resolution_type not in RESOLUTION_TYPES:
        raise IssueError(f"resolution_type must be one of {', '.join(RESOLUTION_TYPES)}")
    if not text.strip():
        raise IssueError("A resolution needs an explanation")
    run = None
    if resolution_type == "interpretation":
        errors = validate_scoped(condition) if condition else ["An interpretation needs a typed condition"]
        for i, ex in enumerate(exception_conditions or []):
            if not isinstance(ex, dict) or "condition" not in ex:
                errors.append(f"exception {i}: needs a condition")
            else:
                errors += validate_scoped(ex["condition"], f"exception[{i}]")
        errors += validate_treatment(point_treatment or {"action": "no_numeric_rule"})
        if errors:
            raise IssueError("; ".join(errors))
        version = note_service.create_note(session, issue.assessment_id, issue.question, actor, interpretation=text)
        descriptions = "; ".join(ex.get("description", "") for ex in exception_conditions or [] if ex.get("description"))
        version, run = note_service.approve(session, version.id, {
            "note_kind": note_kind or "clarification",
            "scope_component": issue.component_role or "",
            "scope_requirement": criterion_key or issue.criterion_key,
            "scoring_implication": (point_treatment or {}).get("action", "no_numeric_rule"),
            "rationale": f"Resolution of shared calibration issue #{issue.id}. Typed exceptions: {descriptions or 'none'}.",
            "coexists_with": coexists_with or [],
        }, actor)
        session.add(GuidanceRuleSpec(
            note_version_id=version.id, issue_id=issue.id, assessment_id=issue.assessment_id,
            criterion_key=criterion_key or issue.criterion_key, component_role=issue.component_role,
            condition=condition, exception_conditions=exception_conditions or [],
            point_treatment=point_treatment or {"action": "no_numeric_rule"},
            rubric_version_id=issue.rubric_version_id, is_anchor=is_anchor, anchor_examples=anchor_examples or []))
        if provider_mode:
            run.params = {**run.params, "provider_mode": provider_mode}
        issue.resolution_note_version_id = version.id
    issue.state, issue.resolution_type, issue.resolution_text = "resolved", resolution_type, text.strip()
    issue.resolved_by, issue.resolved_at = actor, utcnow()
    _bump(issue)
    session.flush()
    _event(session, issue, actor, "resolved", resolution_type=resolution_type,
           note_version_id=issue.resolution_note_version_id, affected=affected_count(session, issue.id))
    from app.grading.feedback import enqueue_refresh

    linked = list(session.scalars(select(IssueCaseLink.submission_id).where(
        IssueCaseLink.issue_id == issue.id, IssueCaseLink.active.is_(True))))
    enqueue_refresh(session, issue.assessment_id, None, actor, reason=f"issue #{issue.id} resolved",
                    provider_mode=provider_mode, all_drafted=True, priority_ids=linked)
    return issue, run


def merge(session: Session, issue_id: int, into_id: int, actor: str, expected_version: int | None,
          expected_into_version: int | None) -> CalibrationIssue:
    source, target = _get(session, issue_id), _get(session, into_id)
    _check_version(source, expected_version)
    _check_version(target, expected_into_version)
    if source.id == target.id or source.assessment_id != target.assessment_id:
        raise IssueError("Issues must differ and belong to the same assessment")
    if source.state not in OPEN_STATES or target.state not in OPEN_STATES:
        raise IssueError("Only unresolved issues can be merged")
    moved = 0
    for link in list(session.scalars(select(IssueCaseLink).where(IssueCaseLink.issue_id == source.id,
                                                                 IssueCaseLink.active.is_(True)))):
        link.active, link.removed_at, link.removed_reason = False, utcnow(), f"merged into issue #{target.id}"
        _, created = link_case(session, target, link.submission_id, actor, "head_ta_merge", link.revision_id,
                               link.card_id, link.evidence_signature)
        moved += int(created)
    for card in session.scalars(select(FeedbackCard).where(FeedbackCard.issue_id == source.id)):
        card.issue_id = target.id
    source.state, source.merged_into_id = "superseded", target.id
    _bump(source)
    _bump(target)
    _event(session, source, actor, "merged_away", into_issue_id=target.id, moved=moved)
    _event(session, target, actor, "merged_in", from_issue_id=source.id, moved=moved)
    return target


def split(session: Session, issue_id: int, submission_ids: list[int], question: str, actor: str,
          expected_version: int | None) -> CalibrationIssue:
    source = _get(session, issue_id)
    _check_version(source, expected_version)
    if source.state not in OPEN_STATES:
        raise IssueError("Only unresolved issues can be split")
    if not question.strip():
        raise IssueError("The new issue needs its own question")
    new = CalibrationIssue(
        assessment_id=source.assessment_id, assignment_code=source.assignment_code, criterion_key=source.criterion_key,
        component_role=source.component_role, rubric_version_id=source.rubric_version_id, rule_hash=source.rule_hash,
        issue_type=source.issue_type, match_key=audit.stable_hash([source.match_key, "split", source.id, question]),
        question=question.strip(), context_note=f"Split from issue #{source.id}", state="open", created_by=actor)
    session.add(new)
    session.flush()
    moved = 0
    for link in list(session.scalars(select(IssueCaseLink).where(
            IssueCaseLink.issue_id == source.id, IssueCaseLink.active.is_(True),
            IssueCaseLink.submission_id.in_(submission_ids)))):
        link.active, link.removed_at, link.removed_reason = False, utcnow(), f"split into issue #{new.id}"
        link_case(session, new, link.submission_id, actor, "head_ta_split", link.revision_id, link.card_id,
                  link.evidence_signature)
        moved += 1
    _bump(source)
    _event(session, source, actor, "split", new_issue_id=new.id, moved=moved)
    _event(session, new, actor, "created_by_split", from_issue_id=source.id, moved=moved)
    return new


def issue_view(session: Session, issue: CalibrationIssue, detail: bool = False) -> dict[str, Any]:
    links = list(session.scalars(select(IssueCaseLink).where(IssueCaseLink.issue_id == issue.id)
                                 .order_by(IssueCaseLink.id)))
    view = {"id": issue.id, "assessment_id": issue.assessment_id, "assignment": issue.assignment_code,
            "criterion_key": issue.criterion_key, "component_role": issue.component_role,
            "rubric_version_id": issue.rubric_version_id, "issue_type": issue.issue_type, "question": issue.question,
            "context_note": issue.context_note, "state": issue.state, "authority_needed": issue.authority_needed,
            "affected": len({l.submission_id for l in links if l.active}),
            "covered_after_resolution": len({l.submission_id for l in links
                                             if l.active and l.link_method == "covered_by_resolution"}),
            "merged_into_id": issue.merged_into_id, "resolution_type": issue.resolution_type,
            "resolution_text": issue.resolution_text, "resolution_note_version_id": issue.resolution_note_version_id,
            "resolved_by": issue.resolved_by, "version": issue.version,
            "created_at": issue.created_at.isoformat() if issue.created_at else None}
    if detail:
        view["links"] = [{"id": l.id, "submission_id": l.submission_id, "revision_id": l.revision_id,
                          "card_id": l.card_id, "method": l.link_method, "active": l.active,
                          "local_disposition": l.local_disposition, "removed_reason": l.removed_reason,
                          "created_at": l.created_at.isoformat() if l.created_at else None} for l in links]
        view["events"] = [{"at": e.at.isoformat(), "actor": e.actor, "action": e.action, "detail": e.detail}
                          for e in session.scalars(select(IssueEvent).where(IssueEvent.issue_id == issue.id)
                                                   .order_by(IssueEvent.id))]
        spec = session.scalar(select(GuidanceRuleSpec).where(GuidanceRuleSpec.issue_id == issue.id))
        if spec is not None:
            note = session.get(InstructorNoteVersion, spec.note_version_id)
            view["guidance"] = {"note_version_id": spec.note_version_id, "version": note.version,
                                "status": note.status, "condition": spec.condition,
                                "exception_conditions": spec.exception_conditions,
                                "point_treatment": spec.point_treatment, "criterion_key": spec.criterion_key,
                                "is_anchor": spec.is_anchor}
    return view

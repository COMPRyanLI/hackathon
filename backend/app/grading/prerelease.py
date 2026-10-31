"""Pre-release review and versioned moderation snapshots.

The pre-release summary keeps unresolved policy, unavailable evidence, failed
checks and unanswered feedback separate, and states how many pairs were never
model-reviewed. A snapshot pins the exact draft revisions, guidance versions
and unresolved items a human looked at. Nothing is written to the gradebook
CSV or an LMS; original totals, drafts, proposals and decisions stay in
separate columns.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings
from app.db.models import AnalysisRun, AssessmentVersion, CandidatePair, InstructorNoteVersion, NoteApplicability, \
    ScoreRecord, Student, Submission
from app.db.models_p1 import (
    CalibrationIssue,
    DraftFeedback,
    DraftGradeRevision,
    EvidenceManifestVersion,
    FeedbackCard,
    FeedbackDisposition,
    GuidanceRuleSpec,
    ModerationSnapshot,
)
from app.exports.report import escape_cell
from app.grading.drafts import current_revision
from app.calibration.issues import OPEN_STATES, affected_count
from app.calibration.rules import evaluate_spec
from app.rubrics.service import assessment_rubric

COLUMNS = ["student_id", "grader_original", "recorded_total_original", "recorded_status", "draft_revision_id",
           "draft_revision_number", "draft_total", "draft_origin", "draft_grader_label", "draft_criterion_points",
           "observed_criterion_deductions", "rule_derived_proposals", "human_dispositions", "open_issue_ids",
           "feedback_status", "unresolved", "snapshot_version"]


def _latest_feedback(session: Session, revision: DraftGradeRevision) -> DraftFeedback | None:
    return session.scalar(select(DraftFeedback).where(DraftFeedback.revision_id == revision.id)
                          .order_by(DraftFeedback.id.desc()).limit(1))


def prerelease_summary(session: Session, assessment: AssessmentVersion) -> dict[str, Any]:
    subs = list(session.scalars(select(Submission).where(Submission.assessment_id == assessment.id)))
    feedback_states: dict[str, int] = {}
    unanswered, drafted, from_historical = [], 0, 0
    for sub in subs:
        revision = current_revision(session, sub.id)
        if revision is None:
            feedback_states["no_draft"] = feedback_states.get("no_draft", 0) + 1
            continue
        drafted += 1
        from_historical += revision.origin == "from_historical_record"
        fb = _latest_feedback(session, revision)
        state = fb.status if fb else "not_queued"
        feedback_states[state] = feedback_states.get(state, 0) + 1
        if fb:
            for card in session.scalars(select(FeedbackCard).where(FeedbackCard.feedback_id == fb.id,
                                                                   FeedbackCard.status == "open")):
                unanswered.append({"submission_id": sub.id, "card_id": card.id, "type": card.card_type,
                                   "title": card.title})
    issues = [{"id": i.id, "state": i.state, "question": i.question, "criterion_key": i.criterion_key,
               "affected": affected_count(session, i.id)}
              for i in session.scalars(select(CalibrationIssue).where(CalibrationIssue.assessment_id == assessment.id,
                                                                      CalibrationIssue.state.in_(OPEN_STATES)))]
    guidance = []
    for spec in session.scalars(select(GuidanceRuleSpec).where(GuidanceRuleSpec.assessment_id == assessment.id)):
        note = session.get(InstructorNoteVersion, spec.note_version_id)
        counts = dict(session.execute(select(NoteApplicability.status, func.count()).where(
            NoteApplicability.note_version_id == note.id, NoteApplicability.is_current.is_(True))
            .group_by(NoteApplicability.status)).all())
        guidance.append({"note_version_id": note.id, "version": note.version, "status": note.status,
                         "criterion_key": spec.criterion_key, "applicability": counts})
    rubric = assessment_rubric(session, assessment)
    manifest = session.scalar(select(EvidenceManifestVersion).where(EvidenceManifestVersion.assessment_id == assessment.id)
                              .order_by(EvidenceManifestVersion.version.desc()).limit(1))
    run = session.scalar(select(AnalysisRun).where(AnalysisRun.assessment_id == assessment.id,
                                                   AnalysisRun.kind == "pipeline")
                         .order_by(AnalysisRun.id.desc()).limit(1))
    n = len(subs)
    all_pairs = n * (n - 1) // 2
    reviewed = (run.coverage or {}).get("pairs_reviewed", 0) if run else 0
    retrieved = session.scalar(select(func.count(CandidatePair.id)).where(CandidatePair.run_id == run.id)) if run else 0
    return {
        "assessment_id": assessment.id, "submissions": n, "drafted": drafted, "drafts_from_historical": from_historical,
        "unresolved_policy": issues,
        "unavailable_evidence": {
            "external_criteria": [c.criterion_key for c in (rubric["criteria"] if rubric else [])
                                  if c.classification == "external_evidence_required" and c.assignment == assessment.name],
            "manifest_summary": manifest.summary if manifest else None},
        "failed_checks": {"feedback": {k: v for k, v in feedback_states.items() if k in ("failed", "partial", "stale")},
                          "latest_pipeline": {"id": run.id, "status": run.status,
                                              "notes": (run.coverage or {}).get("notes", [])} if run else None},
        "unanswered_feedback": unanswered,
        "feedback_states": feedback_states,
        "approved_guidance": guidance,
        "rubric_blocking": rubric["blocking"] if rubric else ["No rubric linked: content-only mode."],
        "coverage": {"all_pairs": all_pairs, "retrieved_candidates": retrieved, "model_reviewed_pairs": reviewed,
                     "unchecked_pairs": max(0, all_pairs - reviewed),
                     "statement": "Cheap similarity over all pairs is not exhaustive model review; unchecked pairs are "
                                  "disclosed."},
    }


def start_prerelease_review(session: Session, assessment: AssessmentVersion, budget: int, actor: str,
                            provider_mode: str | None) -> AnalysisRun:
    ids = sorted(session.scalars(select(Submission.id).where(Submission.assessment_id == assessment.id)))
    run = AnalysisRun(assessment_id=assessment.id, kind="pipeline", status="queued", actor=actor,
                      params={"submission_ids": ids, "review_budget": budget, "top_k": None,
                              "provider_mode": provider_mode, "purpose": "prerelease"})
    session.add(run)
    session.flush()
    audit.record(session, actor, "prerelease_review_started", "analysis_run", run.id, submissions=len(ids),
                 budget=budget)
    return run


def create_snapshot(session: Session, assessment: AssessmentVersion, actor: str, settings: Settings) -> ModerationSnapshot:
    summary = prerelease_summary(session, assessment)
    rubric = assessment_rubric(session, assessment)
    specs = [s for s in session.scalars(select(GuidanceRuleSpec).where(GuidanceRuleSpec.assessment_id == assessment.id))
             if session.get(InstructorNoteVersion, s.note_version_id).status == "approved"]
    version = (session.scalar(select(func.max(ModerationSnapshot.version)).where(
        ModerationSnapshot.assessment_id == assessment.id)) or 0) + 1
    records = session.execute(select(Student, ScoreRecord).join(ScoreRecord, ScoreRecord.student_id == Student.id)
                              .where(ScoreRecord.batch_id == assessment.batch_id,
                                     ScoreRecord.assessment_column == assessment.name).order_by(Student.code)).all()
    subs = {s.student_id: s for s in session.scalars(select(Submission).where(Submission.assessment_id == assessment.id))}
    open_issue_by_sub: dict[int, list[int]] = {}
    for issue in summary["unresolved_policy"]:
        from app.db.models_p1 import IssueCaseLink

        for sid in session.scalars(select(IssueCaseLink.submission_id).where(IssueCaseLink.issue_id == issue["id"],
                                                                             IssueCaseLink.active.is_(True))):
            open_issue_by_sub.setdefault(sid, []).append(issue["id"])
    rows, revision_ids, unresolved_all = [], [], []
    for student, record in records:
        sub = subs.get(student.id)
        revision = current_revision(session, sub.id) if sub else None
        fb = _latest_feedback(session, revision) if revision else None
        proposals, dispositions, unresolved = [], [], []
        if sub is None:
            unresolved.append("no code in current export")
        if sub is not None:
            for spec in specs:
                criterion = rubric["by_key"].get(spec.criterion_key) if rubric else None
                out = evaluate_spec(session, spec, sub, criterion)
                if out["status"] == "applies" and out.get("proposal"):
                    prop = out["proposal"]
                    value = (prop.get("points") if prop.get("kind") == "points"
                             else f"{prop['min']}-{prop['max']}" if prop.get("kind") == "range" else prop.get("kind"))
                    proposals.append(f"{spec.criterion_key}={value}"
                                     f" (guidance note v{session.get(InstructorNoteVersion, spec.note_version_id).version})")
                elif out["status"] == "uncertain":
                    unresolved.append(f"guidance {spec.note_version_id} uncertain: {out['rationale'][:80]}")
        if fb is not None:
            for card in session.scalars(select(FeedbackCard).where(FeedbackCard.feedback_id == fb.id)):
                for d in session.scalars(select(FeedbackDisposition).where(FeedbackDisposition.card_id == card.id)):
                    dispositions.append(f"{card.card_type}:{d.action}")
                if card.status == "open":
                    unresolved.append(f"unanswered feedback: {card.title[:60]}")
            if fb.status in ("failed", "partial", "stale", "queued", "running"):
                unresolved.append(f"feedback {fb.status}")
        if sub is not None and sub.id in open_issue_by_sub:
            unresolved.append("open shared issue(s): " + ",".join(map(str, open_issue_by_sub[sub.id])))
        if revision is not None:
            revision_ids.append(revision.id)
            # A revision's own dispositions include the revise action that produced it.
            for d in session.scalars(select(FeedbackDisposition).where(FeedbackDisposition.new_revision_id == revision.id)):
                dispositions.append(f"revised_from_revision:{d.revision_id}")
        if unresolved:
            unresolved_all.append({"student_id": student.code, "reasons": unresolved})
        rows.append({
            "student_id": student.code, "grader_original": record.grader_label or "",
            "recorded_total_original": record.raw_cell, "recorded_status": record.status,
            "draft_revision_id": revision.id if revision else "", "draft_revision_number":
                revision.revision_number if revision else "",
            "draft_total": revision.total if revision else "", "draft_origin": revision.origin if revision else "",
            "draft_grader_label": revision.grader_label if revision else "",
            "draft_criterion_points": json.dumps(revision.criterion_points, sort_keys=True) if revision and
            revision.criterion_points else "",
            "observed_criterion_deductions": "none available in imported data",
            "rule_derived_proposals": "; ".join(proposals), "human_dispositions": "; ".join(dispositions),
            "open_issue_ids": ",".join(map(str, open_issue_by_sub.get(sub.id, []))) if sub else "",
            "feedback_status": fb.status if fb else ("no_draft" if revision is None else "not_queued"),
            "unresolved": " | ".join(unresolved), "snapshot_version": version})
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([escape_cell(row[c]) for c in COLUMNS])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = settings.private_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"moderation-snapshot-{assessment.dataset_key}-{assessment.name}-a{assessment.id}-v{version}-{stamp}"
    csv_path, json_path = out_dir / f"{base}.csv", out_dir / f"{base}.json"
    csv_path.write_text(buffer.getvalue(), encoding="utf-8")
    guidance_versions = [{"note_version_id": s.note_version_id, "criterion_key": s.criterion_key,
                          "condition": s.condition, "point_treatment": s.point_treatment} for s in specs]
    json_path.write_text(json.dumps({
        "snapshot_version": version, "assessment": {"id": assessment.id, "name": assessment.name,
                                                    "synthetic": assessment.is_synthetic},
        "generated_at": stamp, "actor": actor, "revision_ids": revision_ids, "guidance_versions": guidance_versions,
        "unresolved": unresolved_all, "prerelease_summary": summary, "rows": rows,
        "statement": "Snapshot for human moderation. Original recorded totals are unchanged; draft totals are "
                     "the graders' own revisions; proposals come only from approved typed guidance; nothing was "
                     "written back to the gradebook or an LMS."}, indent=2, default=str), encoding="utf-8")
    snapshot = ModerationSnapshot(assessment_id=assessment.id, version=version, revision_ids=revision_ids,
                                  guidance_versions=guidance_versions, unresolved=unresolved_all,
                                  summary={"rows": len(rows), "drafted": summary["drafted"],
                                           "unresolved_rows": len(unresolved_all),
                                           "unchecked_pairs": summary["coverage"]["unchecked_pairs"]},
                                  csv_path=str(csv_path), json_path=str(json_path), actor=actor)
    session.add(snapshot)
    session.flush()
    audit.record(session, actor, "moderation_snapshot_created", "moderation_snapshot", snapshot.id, version=version,
                 revisions=len(revision_ids), unresolved=len(unresolved_all))
    snapshot.csv_text = buffer.getvalue()  # transient attribute for the API response
    return snapshot

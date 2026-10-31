"""Moderation report export.

Original scores are copied from immutable score records; the original CSV is
never written. Proposed scores appear only where a human entered them in a
decision. Formula-leading cells are escaped for spreadsheet safety.
"""
from __future__ import annotations

import csv
import io
import json
import re
from uuid import uuid4
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.analysis.context import context_mode, current_guidance, guidance_refs
from app.config import Settings
from app.db.models import (
    AnalysisRun,
    ApplicabilityDecision,
    AssessmentVersion,
    InstructorNoteVersion,
    NoteApplicability,
    ReviewCase,
    ReviewDecision,
    ScoreRecord,
    Student,
    Submission,
)

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
COLUMNS = ["student_id", "grader_original", "original_score_raw", "original_score_status", "files_in_upload",
           "current_cases", "historical_cases", "latest_case_decisions", "current_note_applicability",
           "applicability_decisions", "human_proposed_score", "proposed_score_source"]


def escape_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def build_report(session: Session, assessment_id: int, settings: Settings, actor: str) -> dict[str, Any]:
    assessment = session.get(AssessmentVersion, assessment_id)
    if assessment is None:
        raise ValueError("Assessment not found")
    guidance = current_guidance(session, assessment_id)
    records = session.execute(
        select(Student, ScoreRecord).join(ScoreRecord, ScoreRecord.student_id == Student.id)
        .where(ScoreRecord.batch_id == assessment.batch_id, ScoreRecord.assessment_column == assessment.name)
        .order_by(Student.code)).all()
    submissions = {s.student_id: s for s in session.scalars(
        select(Submission).where(Submission.assessment_id == assessment_id))}
    cases = list(session.scalars(select(ReviewCase).where(ReviewCase.assessment_id == assessment_id)))
    decisions: dict[int, list[ReviewDecision]] = {}
    for d in session.scalars(select(ReviewDecision).join(ReviewCase).where(ReviewCase.assessment_id == assessment_id)
                             .order_by(ReviewDecision.id)):
        decisions.setdefault(d.case_id, []).append(d)
    applicability = list(session.scalars(select(NoteApplicability).join(Submission).where(
        Submission.assessment_id == assessment_id)))
    app_decisions: dict[int, list[ApplicabilityDecision]] = {}
    for d in session.scalars(select(ApplicabilityDecision).order_by(ApplicabilityDecision.id)):
        app_decisions.setdefault(d.applicability_id, []).append(d)
    versions = {v.id: v for v in session.scalars(select(InstructorNoteVersion))}

    rows = []
    for student, record in records:
        sub = submissions.get(student.id)
        sid = sub.id if sub else None
        mine = [c for c in cases if sid in (c.submission_a_id, c.submission_b_id)]
        latest_actions, proposed, source = [], None, ""
        proposals: list[tuple[float, str]] = []
        for case in sorted(mine, key=lambda c: c.id):
            for d in decisions.get(case.id, [])[-1:]:
                latest_actions.append(f"case{case.id}:{d.action}")
                value = d.proposed_score_a if case.submission_a_id == sid else d.proposed_score_b
                if value is not None and case.is_current:
                    proposals.append((value, f"case {case.id} decision {d.id} by {d.actor}"))
        notes, note_decisions = [], []
        for row in applicability:
            if row.submission_id != sid:
                continue
            v = versions[row.note_version_id]
            if row.is_current:
                notes.append(f"note{v.note_id}v{v.version}:{row.status}")
            for d in app_decisions.get(row.id, [])[-1:]:
                note_decisions.append(f"note{v.note_id}v{v.version}:{d.action}")
                if d.proposed_score is not None and row.is_current and v.status == "approved" and v.note_kind != "prospective_policy":
                    proposals.append((d.proposed_score, f"note {v.note_id} v{v.version} decision {d.id} by {d.actor}"))
        if proposals:
            values = {value for value, _ in proposals}
            source = "; ".join(f"{origin}: {value}" for value, origin in proposals)
            if len(values) == 1:
                proposed = next(iter(values))
            else:
                source = "CONFLICT - reconcile current human proposals before changing a grade: " + source
        rows.append({
            "student_id": student.code, "grader_original": record.grader_label or "",
            "original_score_raw": record.raw_cell, "original_score_status": record.status,
            "files_in_upload": len(sub.artifacts) if sub else 0,
            "current_cases": ";".join(str(c.id) for c in mine if c.is_current),
            "historical_cases": ";".join(str(c.id) for c in mine if not c.is_current),
            "latest_case_decisions": ";".join(latest_actions),
            "current_note_applicability": ";".join(notes),
            "applicability_decisions": ";".join(note_decisions),
            "human_proposed_score": "" if proposed is None else proposed,
            "proposed_score_source": source,
        })

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([escape_cell(row[c]) for c in COLUMNS])
    csv_text = buffer.getvalue()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = settings.private_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = lambda value: re.sub(r"[^\w.-]+", "_", str(value))[:80]
    base = f"moderation-report-{safe(assessment.dataset_key)}-{safe(assessment.name)}-v{assessment.version}-{stamp}-{uuid4().hex[:8]}"
    csv_path = out_dir / f"{base}.csv"
    json_path = out_dir / f"{base}.json"
    csv_path.write_text(csv_text, encoding="utf-8")
    runs = list(session.scalars(select(AnalysisRun).where(AnalysisRun.assessment_id == assessment_id)))
    detail = {
        "generated_at": stamp, "actor": actor, "synthetic": assessment.is_synthetic,
        "assessment": {"id": assessment.id, "name": assessment.name, "version": assessment.version,
                       "context_mode": context_mode(assessment, guidance),
                       "instructions_status": assessment.instructions_status},
        "current_guidance": guidance_refs(guidance),
        "runs": [{"id": r.id, "kind": r.kind, "status": r.status, "coverage": r.coverage,
                  "providers": r.provider_snapshot} for r in runs],
        "cases": [{"id": c.id, "origin": c.origin, "category": c.category, "headline": c.headline,
                   "is_current": c.is_current, "stale_reason": c.stale_reason, "is_mock": c.is_mock,
                   "decisions": [{"action": d.action, "comment": d.comment, "actor": d.actor,
                                  "proposed_score_a": d.proposed_score_a, "proposed_score_b": d.proposed_score_b,
                                  "at": d.created_at.isoformat()} for d in decisions.get(c.id, [])]}
                  for c in cases],
        "rows": rows,
        "statement": "Provisional review support. Original scores are unchanged; proposed scores, where present, "
                     "were entered by a human. Similarity values are retrieval diagnostics, not fairness or "
                     "correctness measures.",
    }
    json_path.write_text(json.dumps(detail, indent=2, default=str), encoding="utf-8")
    audit.record(session, actor, "report_exported", "assessment_version", assessment_id,
                 csv=csv_path.name, json=json_path.name, rows=len(rows))
    return {"csv_path": str(csv_path), "json_path": str(json_path), "csv_text": csv_text, "rows": len(rows)}

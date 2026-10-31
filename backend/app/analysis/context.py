"""Assignment context and approved guidance as seen by model prompts."""
from __future__ import annotations

import json
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db.models import AssessmentVersion, InstructorNote, InstructorNoteVersion

INSTRUCTION_STATUSES = ("pending", "synthetic_demo", "confirmed_real")


def refresh_context_hash(assessment: AssessmentVersion) -> None:
    assessment.context_hash = audit.stable_hash({
        "instructions_status": assessment.instructions_status or "pending",
        "instructions_text": assessment.instructions_text or "",
        "reference_solution_text": assessment.reference_solution_text or "",
        "reference_solution_status": assessment.reference_solution_status or "absent",
        "max_points_confirmed": assessment.max_points_confirmed,
        "multi_file_scope_note": assessment.multi_file_scope_note or "",
    })


def has_instructions(assessment: AssessmentVersion) -> bool:
    return assessment.instructions_status in ("synthetic_demo", "confirmed_real") and bool(
        (assessment.instructions_text or "").strip())


def current_guidance(session: Session, assessment_id: int, *, include_prospective: bool = False) -> list[InstructorNoteVersion]:
    """Historical review excludes rules explicitly intended only for future work."""
    query = (
        select(InstructorNoteVersion)
        .join(InstructorNote, InstructorNote.id == InstructorNoteVersion.note_id)
        .where(InstructorNote.assessment_id == assessment_id, InstructorNoteVersion.status == "approved")
        .order_by(InstructorNoteVersion.id)
    )
    if not include_prospective:
        query = query.where(InstructorNoteVersion.note_kind != "prospective_policy")
    return list(session.scalars(query))


def guidance_hash(guidance: list[InstructorNoteVersion]) -> str:
    return audit.stable_hash([[g.id, g.version, g.interpretation, g.applicability_conditions,
                               g.exceptions, g.scoring_implication, g.note_kind,
                               g.scope_component, g.scope_requirement] for g in guidance])


def guidance_refs(guidance: list[InstructorNoteVersion]) -> list[dict]:
    return [{"note_version_id": g.id, "note_id": g.note_id, "version": g.version} for g in guidance]


def context_mode(assessment: AssessmentVersion, guidance: list[InstructorNoteVersion]) -> str:
    if guidance:
        return "approved_guidance"
    if has_instructions(assessment):
        return "no_rubric"
    return "content_only"


def context_block(assessment: AssessmentVersion) -> str:
    status = assessment.instructions_status
    parts = []
    if has_instructions(assessment):
        if status == "synthetic_demo":
            parts.append("ASSIGNMENT CONTEXT - SYNTHETIC DEMO, NOT THE REAL COURSE ASSIGNMENT:\n"
                         + assessment.instructions_text.strip())
        else:
            parts.append("ASSIGNMENT INSTRUCTIONS (confirmed by the instructor):\n"
                         + assessment.instructions_text.strip())
    else:
        parts.append(
            "ASSIGNMENT CONTEXT: none confirmed (content-only mode). The real assignment instructions and "
            "rubric have not been provided. Do not state or imply that any requirement is satisfied or "
            "violated, and do not infer requirements from comments, file names or typical assignments. "
            "Describe only what the source observably contains.")
    if (assessment.reference_solution_text or "").strip():
        parts.append(
            f"EXAMPLE SOLUTION (status: {assessment.reference_solution_status}). An example shows one "
            "acceptable approach; it is not a list of requirements unless the instructions say so:\n"
            + assessment.reference_solution_text.strip())
    if assessment.max_points_confirmed is not None:
        parts.append(f"Maximum points (confirmed): {assessment.max_points_confirmed}")
    if (assessment.multi_file_scope_note or "").strip():
        parts.append("Multi-file scope note from the instructor: " + assessment.multi_file_scope_note.strip())
    return "\n\n".join(parts)


def guidance_block(guidance: list[InstructorNoteVersion]) -> str:
    if not guidance:
        return "APPROVED INSTRUCTOR GUIDANCE: none."
    lines = ["APPROVED INSTRUCTOR GUIDANCE (adopted by the instructor; cite by note id):"]
    for g in guidance:
        lines.append(f"- note {g.note_id} v{g.version} [{g.note_kind}]: {g.interpretation.strip()}"
                     + f" Component scope: {g.scope_component or 'assignment-wide'}."
                     + f" Requirement scope: {g.scope_requirement or 'unspecified'}."
                     + f" Applicability conditions: {json.dumps(g.applicability_conditions or {})}."
                     + (f" Exceptions: {g.exceptions.strip()}" if g.exceptions.strip() else "")
                     + f" Scoring implication: {g.scoring_implication}.")
    return "\n".join(lines)

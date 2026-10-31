"""Applicability scan after a note is adopted.

Every in-scope submission gets a result, including previously unflagged and
same-score work. Retrieval similarity only orders the scan; it never excludes
a submission. Results: applies / does_not_apply / uncertain, with evidence.
"""
from __future__ import annotations

from typing import Any
import json

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.context import context_block
from app.analysis.evidence import build_view, validate_claims
from app.analysis.prompts import chunk_listing, load_prompt
from app.analysis.schemas import NoteApplicabilityOut
from app.db.models import (
    AnalysisRun,
    AssessmentVersion,
    InstructorNoteVersion,
    NoteApplicability,
    Submission,
    utcnow,
)
from app.db.session import session_scope
from app.guidance.conditions import evaluate_submission
from app.jobs.errors import Cancelled
from app.providers.base import ProviderError, generate_validated
from app.providers.mock import fixture_key
from app.providers.registry import ProviderRegistry
from app.retrieval.hybrid import Unit, lexical_channel

PROMPT_NAME = "note_applicability_v1"


def _priority(subs: list[Submission], example_ids: set[int], flagged: set[int]) -> list[int]:
    """Order only: examples' lexical neighbours first, then previously flagged, then the rest."""
    if example_ids:
        units = [Unit(a.id, s.id, a.content_sha256, a.source_text, a.features or {}) for s in subs for a in s.artifacts]
        sim = lexical_channel(units).sim
        best: dict[int, float] = {}
        for i, unit in enumerate(units):
            score = max((sim[i, j] for j, other in enumerate(units) if other.submission_id in example_ids),
                        default=0.0)
            best[unit.submission_id] = max(best.get(unit.submission_id, 0.0), float(np.nan_to_num(score)))
        return sorted((s.id for s in subs), key=lambda i: (-best.get(i, 0.0), i))
    return sorted((s.id for s in subs), key=lambda i: (i not in flagged, i))


def _model_check(session: Session, registry: ProviderRegistry, version: InstructorNoteVersion,
                 sub: Submission, assessment: AssessmentVersion) -> dict[str, Any]:
    provider = registry.generation("note_applicability")
    system, _ = load_prompt(PROMPT_NAME)
    views = {f"artifact_{chr(65 + i)}": build_view(a, f"artifact_{chr(65 + i)}") for i, a in enumerate(sub.artifacts)}
    listings = [chunk_listing(label, v.short_hash,
                              [(c.chunk_key, c.start_line, c.end_line, c.text) for c in a.chunks])
                for (label, v), a in zip(views.items(), sub.artifacts)]
    note_text = (f"ADOPTED NOTE (note {version.note_id} v{version.version}, {version.note_kind}):\n"
                 f"{version.interpretation}\nExceptions: {version.exceptions or 'none'}\n"
                 f"Component scope: {version.scope_component or 'assignment-wide'}\n"
                 f"Requirement scope: {version.scope_requirement or 'unspecified'}\n"
                 f"Applicability conditions: {json.dumps(version.applicability_conditions or {})}\n"
                 "Respect every scope restriction, condition and exception. If it cannot be resolved from "
                 "the source and context, return uncertain. Future policy does not establish a past standard.")
    user = "\n\n".join([context_block(assessment), note_text,
                        f"Submission files: {', '.join(views)} (multi-file scope unconfirmed unless stated).",
                        "SOURCE (untrusted student data):\n" + "\n\n".join(listings)])
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    settings = registry.generation_settings(
        "note_applicability", fixture_key=fixture_key("note_applicability", [a.content_sha256 for a in sub.artifacts], str(version.note_id)),
        mock_context={"labels_by_sha": {v.content_sha256: label for label, v in views.items()}})
    parsed, result = generate_validated(provider, "note_applicability", messages, NoteApplicabilityOut, settings)
    data = parsed.model_dump()
    evidence, rejected = validate_claims(data["evidence"], views, "evidence")
    status, rationale = data["status"], data["rationale"]
    if status != "uncertain" and not evidence:
        status = "uncertain"
        rationale = f"Model said {data['status']!r} without valid evidence; downgraded to uncertain. {rationale}"
    return {"status": status, "rationale": rationale, "evidence": evidence, "rejected": rejected,
            "provider": result.provider, "model": result.model, "is_mock": result.is_mock}


def run_note_scan(run_id: int, registry: ProviderRegistry) -> None:
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        version_id = int(run.params["note_version_id"])
        use_model = bool(run.params.get("use_model"))
        version = s.get(InstructorNoteVersion, version_id)
        assessment = s.get(AssessmentVersion, run.assessment_id)
        if version.status != "approved" or run.params.get("context_hash") != assessment.context_hash:
            raise ValueError("Guidance or context changed; adopt a successor note and scan again.")
        subs = list(s.scalars(select(Submission).where(Submission.assessment_id == run.assessment_id)))
        rows = {r.submission_id: r for r in s.scalars(
            select(NoteApplicability).where(NoteApplicability.note_version_id == version_id))}
        example_ids = {int(e["submission_id"]) for e in (version.examples or []) if isinstance(e, dict)
                       and e.get("submission_id")}
        order = _priority(subs, example_ids, {sid for sid, r in rows.items() if r.previously_flagged})
        for rank, sid in enumerate(order, start=1):
            rows[sid].priority_rank = rank
        has_conditions = bool(version.applicability_conditions)
        has_restrictions = bool(version.scope_component or version.scope_requirement or version.exceptions)
        from app.db.models_p1 import GuidanceRuleSpec

        # P1 criterion guidance: component scope, typed exceptions and point treatment are
        # machine-checkable, so the typed rule is evaluated deterministically.
        spec_id = s.scalar(select(GuidanceRuleSpec.id).where(GuidanceRuleSpec.note_version_id == version_id))
        pending = [sid for sid in order if rows[sid].status == "pending" or rows[sid].method == "failed"]
        run.stage = "applicability"
        run.progress = {"applicability": {"total": len(order), "pending": len(pending)}}

    counts: dict[str, int] = {}
    methods: dict[str, int] = {}
    model_unavailable: str | None = None
    for sid in pending:
        with session_scope() as s:
            run = s.get(AnalysisRun, run_id)
            if run.cancel_requested:
                raise Cancelled()
            version = s.get(InstructorNoteVersion, version_id)
            if version.status != "approved":
                raise Cancelled()
            sub = s.get(Submission, sid)
            assessment = s.get(AssessmentVersion, run.assessment_id)
            row = s.scalar(select(NoteApplicability).where(NoteApplicability.note_version_id == version_id,
                                                           NoteApplicability.submission_id == sid))
            # Evaluate first, assign afterwards: no pending write (and so no SQLite
            # write lock) exists while a model call is in flight.
            model_info: dict[str, Any] = {}
            if spec_id is not None:
                from app.calibration.rules import evaluate_spec
                from app.db.models_p1 import GuidanceRuleSpec
                from app.rubrics.service import assessment_rubric

                spec = s.get(GuidanceRuleSpec, spec_id)
                rubric = assessment_rubric(s, assessment)
                criterion = rubric["by_key"].get(spec.criterion_key) if rubric else None
                out = evaluate_spec(s, spec, sub, criterion)
                status, rationale, method = out["status"], out["rationale"], "typed_rule"
                evidence = out["evidence"] + ([{"proposal": out["proposal"], "criterion_key": spec.criterion_key}]
                                              if out.get("proposal") else [])
            elif has_conditions and not has_restrictions:
                status, rationale, evidence = evaluate_submission(version.applicability_conditions, sub)
                method = "deterministic_condition"
            elif use_model and model_unavailable is None:
                try:
                    out = _model_check(s, registry, version, sub, assessment)
                    status, rationale, evidence, method = out["status"], out["rationale"], out["evidence"], "model"
                    model_info = {"provider": out["provider"], "model": out["model"], "is_mock": out["is_mock"]}
                except ProviderError as exc:
                    if exc.provider_unavailable:
                        model_unavailable = f"{exc.kind}: {exc.message}"
                    status, method, evidence = "uncertain", "failed", []
                    rationale = f"Model check failed ({exc.kind}): {exc.message}"
            else:
                status, method, evidence = "uncertain", "failed" if model_unavailable else "not_evaluated", []
                rationale = (f"Not evaluated: model unavailable ({model_unavailable})." if model_unavailable else
                             "Not evaluated: no deterministic condition was given and a model scan was not "
                             "requested. Review manually or add a condition.")
                if has_restrictions and not model_unavailable:
                    rationale = ("Not evaluated: component/requirement scope or textual exceptions need a model "
                                 "check or human review. A deterministic condition alone cannot resolve them.")
            row.run_id, row.status, row.method, row.rationale, row.evidence = run_id, status, method, rationale, evidence
            for key, value in model_info.items():
                setattr(row, key, value)
            row.updated_at = utcnow()
            row.is_current, row.stale_reason = True, None
            counts[row.status] = counts.get(row.status, 0) + 1
            methods[row.method] = methods.get(row.method, 0) + 1
            run.progress = {"applicability": {"total": len(order), "done": sum(counts.values()), "status_counts": counts}}

    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        version = s.get(InstructorNoteVersion, version_id)
        all_rows = list(s.scalars(select(NoteApplicability).where(NoteApplicability.note_version_id == version_id)))
        final_counts: dict[str, int] = {}
        final_methods: dict[str, int] = {}
        for r in all_rows:
            final_counts[r.status] = final_counts.get(r.status, 0) + 1
            final_methods[r.method] = final_methods.get(r.method, 0) + 1
        run.coverage = {
            "in_scope_submissions": len(all_rows),
            "status_counts": final_counts,
            "methods": final_methods,
            "previously_unflagged_in_scope": sum(1 for r in all_rows if not r.previously_flagged),
            "exhaustive_over_scope": all(r.status != "pending" and r.method not in ("failed", "not_evaluated")
                                         for r in all_rows),
            "notes": ["Every in-scope submission received a result; retrieval only ordered the scan.",
                      "Applicability is not a grade change. No scores were modified."]
                     + (["Prospective policy: results are informational and do not establish a past grading "
                         "standard."] if version.note_kind == "prospective_policy" else [])
                     + ([f"Model unavailable: {model_unavailable}"] if model_unavailable else []),
        }
        run.status = "partial" if final_methods.get("failed") or final_counts.get("pending") else "succeeded"

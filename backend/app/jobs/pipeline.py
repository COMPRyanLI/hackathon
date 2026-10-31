"""Pipeline job: analyst summaries -> source embeddings -> summary embeddings ->
hybrid retrieval and selection -> reviewer pairs -> review cases.

Stages run in batches (one model type at a time). Each item uses its own short
transaction; cache-valid items are skipped on resume. Coverage is never
reported as exhaustive.
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.analysis.analyst import cached_evidence, run_analyst
from app.analysis.cases import make_case, make_exact_case, score_gap, score_map
from app.analysis.context import current_guidance, guidance_refs, guidance_hash
from app.analysis.prompts import load_prompt
from app.analysis.schemas import SCHEMA_VERSION
from app.analysis.reviewer import run_reviewer
from app.db.models import (
    AnalysisRun,
    Artifact,
    AssessmentVersion,
    CandidatePair,
    PairComparison,
    ReviewCase,
    Submission,
)
from app.db.session import session_scope
from app.jobs.errors import Cancelled
from app.providers.base import UNAVAILABLE_KINDS
from app.providers.registry import ProviderRegistry
from app.retrieval.hybrid import Unit, retrieve, select_for_review
from app.retrieval.vectors import (
    SOURCE_PREPROCESSING,
    SOURCE_REP,
    SUMMARY_PREPROCESSING,
    SUMMARY_REP,
    ensure_embeddings,
    load_vector,
    source_items,
    summary_items,
)


def _set_progress(run_id: int, stage: str, **values: Any) -> None:
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        progress = dict(run.progress or {})
        progress[stage] = {**progress.get(stage, {}), **values}
        run.progress = progress
        run.stage = stage


def _cancel_check(run_id: int) -> Callable[[], bool]:
    def check() -> bool:
        with session_scope() as s:
            return bool(s.get(AnalysisRun, run_id).cancel_requested)
    return check


def _submissions(s: Session, assessment_id: int, ids: list[int] | None) -> list[Submission]:
    query = select(Submission).where(Submission.assessment_id == assessment_id).order_by(Submission.id)
    subs = list(s.scalars(query))
    return [x for x in subs if x.id in set(ids)] if ids else subs


def input_signature(session: Session, assessment_id: int, registry: ProviderRegistry) -> str:
    assessment = session.get(AssessmentVersion, assessment_id)
    return audit.stable_hash({
        "context": assessment.context_hash,
        "guidance": guidance_hash(current_guidance(session, assessment_id)),
        "providers": registry.snapshot(), "schema": SCHEMA_VERSION,
        "prompts": [load_prompt(name)[1] for name in ("analyst_v1", "reviewer_v1")],
        "rubric_versions": _rubric_versions(session, assessment_id),
    })


def _rubric_versions(session: Session, assessment_id: int) -> list[int]:
    from app.db.models_p1 import AssessmentRubricLink

    return sorted(session.scalars(select(AssessmentRubricLink.rubric_version_id).where(
        AssessmentRubricLink.assessment_id == assessment_id)))


def _embed_stage(run_id: int, registry: ProviderRegistry, items: list, representation: str,
                 preprocessing: str, cancelled: Callable[[], bool], stage: str) -> dict:
    """Commit each vector before the next model call; keep UI writes responsive."""
    total = {"total": len(items), "cached": 0, "embedded": 0, "failed": 0, "error": None}
    for index, item in enumerate(items):
        if cancelled():
            raise Cancelled()
        with session_scope() as session:
            current = ensure_embeddings(session, registry, [item], representation, preprocessing)
        for key in ("cached", "embedded", "failed"):
            total[key] += current[key]
        if current["error"]:
            total["error"] = current["error"]
        if current["error"] and current["error"]["kind"] in UNAVAILABLE_KINDS:
            total["failed"] += len(items) - index - 1
            break
        _set_progress(run_id, stage, **total)
    return total


def run_pipeline(run_id: int, registry: ProviderRegistry) -> None:
    cancelled = _cancel_check(run_id)
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        params = dict(run.params or {})
        assessment_id = run.assessment_id
        signature = input_signature(s, assessment_id, registry)
        if params.get("input_signature") and params["input_signature"] != signature:
            raise ValueError("Analysis inputs changed. Start a new run to rebuild candidates and cases.")
        run.params = {**params, "input_signature": signature}
        submission_ids = [x.id for x in _submissions(s, assessment_id, params.get("submission_ids"))]
        artifact_ids, seen = [], set()
        for sub in _submissions(s, assessment_id, submission_ids):
            for artifact in sub.artifacts:
                if artifact.content_sha256 not in seen:
                    seen.add(artifact.content_sha256)
                    artifact_ids.append(artifact.id)
    budget = int(params["review_budget"] if params.get("review_budget") is not None
                 else registry.settings.initial_review_pair_budget)
    top_k = int(params.get("top_k") or registry.settings.retrieval_top_k_per_channel)
    failures: list[str] = []

    # ---- Stage B: analyst summaries (once per distinct content)
    done = failed = cached = 0
    stage_error = None
    _set_progress(run_id, "analyst", total=len(artifact_ids), done=0, failed=0, cached=0)
    for artifact_id in artifact_ids:
        if cancelled():
            raise Cancelled()
        with session_scope() as s:
            artifact = s.get(Artifact, artifact_id)
            assessment = s.get(AssessmentVersion, assessment_id)
            before = cached_evidence(s, registry, artifact, assessment)
            if before is not None and before.status != "failed":
                cached += 1
                ev_status, ev_error = before.status, None
            else:
                ev = run_analyst(s, registry, artifact, assessment)
                ev_status, ev_error = ev.status, (ev.error_kind, ev.error_message)
        if ev_status == "failed":
            failed += 1
            stage_error = {"kind": ev_error[0], "message": ev_error[1]}
            if ev_error[0] in UNAVAILABLE_KINDS:
                failed += len(artifact_ids) - done - failed
                _set_progress(run_id, "analyst", done=done, failed=failed, cached=cached, error=stage_error,
                              skipped_reason="provider unavailable; remaining items skipped")
                break
        else:
            done += 1
        _set_progress(run_id, "analyst", done=done, failed=failed, cached=cached, error=stage_error)
    if failed:
        failures.append("analyst")

    # ---- Embeddings: source then validated summaries (separate channels)
    with session_scope() as s:
        artifacts = [s.get(Artifact, i) for i in artifact_ids]
        src_items = source_items(artifacts)
    _set_progress(run_id, "embed_source", total=len(src_items))
    stats = _embed_stage(run_id, registry, src_items, SOURCE_REP, SOURCE_PREPROCESSING, cancelled, "embed_source")
    _set_progress(run_id, "embed_source", **stats)
    if stats["failed"]:
        failures.append("embed_source")
    if cancelled():
        raise Cancelled()

    with session_scope() as s:
        assessment = s.get(AssessmentVersion, assessment_id)
        evidences = [e for e in (cached_evidence(s, registry, s.get(Artifact, i), assessment) for i in artifact_ids)
                     if e is not None]
        sum_items = summary_items(evidences)
    _set_progress(run_id, "embed_summary", total=len(sum_items),
                  note="Only artifacts with validated factual observations are embedded.")
    stats = _embed_stage(run_id, registry, sum_items, SUMMARY_REP, SUMMARY_PREPROCESSING, cancelled, "embed_summary")
    _set_progress(run_id, "embed_summary", **stats)
    if stats["failed"]:
        failures.append("embed_summary")
    if cancelled():
        raise Cancelled()

    # ---- Stage C: hybrid retrieval and budgeted selection
    refresh_in_place = False
    with session_scope() as s:
        existing_pairs = s.scalar(select(CandidatePair.id).where(CandidatePair.run_id == run_id).limit(1))
        if existing_pairs is not None:
            # A resumed run whose earlier attempt lacked vector channels must not keep the
            # incomplete candidate list once those channels are available again.
            previous = ((s.get(AnalysisRun, run_id).progress or {}).get("retrieval") or {}).get(
                "channel_availability", {})
            was_missing = any(previous.get(ch, {}).get("available_artifacts", 0)
                              < previous.get(ch, {}).get("total_artifacts", 0)
                              for ch in ("source_vector", "summary_vector"))
            recovered = "embed_source" not in failures and "embed_summary" not in failures
            has_cases = s.scalar(select(ReviewCase.id).where(ReviewCase.run_id == run_id).limit(1))
            if was_missing and recovered:
                if has_cases is None:
                    s.query(CandidatePair).filter(CandidatePair.run_id == run_id).delete()
                # With cases already recorded, refresh in place: keep existing pairs (and any
                # decisions on their cases), update diagnostics, add newly retrieved pairs.
                refresh_in_place = has_cases is not None
                existing_pairs = None
                _set_progress(run_id, "retrieval_rebuild",
                              reason="previously unavailable retrieval channels are now available",
                              mode="refresh_in_place" if refresh_in_place else "rebuilt")
        if existing_pairs is None:
            assessment = s.get(AssessmentVersion, assessment_id)
            units = []
            for sub in _submissions(s, assessment_id, submission_ids):
                for artifact in sub.artifacts:
                    unit = Unit(artifact.id, sub.id, artifact.content_sha256, artifact.source_text,
                                artifact.features or {})
                    unit.source_vector, unit.source_vector_reason = load_vector(
                        s, registry, SOURCE_REP, artifact.content_sha256, SOURCE_PREPROCESSING)
                    ev = cached_evidence(s, registry, artifact, assessment)
                    if ev is None or ev.status == "failed":
                        unit.summary_vector_reason = f"analyst summary unavailable ({ev.error_kind if ev else 'not generated'})"
                    elif not ev.summary_text.strip():
                        unit.summary_vector_reason = "no validated factual observations"
                    else:
                        unit.summary_vector, unit.summary_vector_reason = load_vector(
                            s, registry, SUMMARY_REP, audit.sha256_text(ev.summary_text), SUMMARY_PREPROCESSING)
                    units.append(unit)
            result = retrieve(units, top_k, registry.settings.rrf_k) if len(units) > 1 else {"bundles": {}, "availability": {}}
            scores = score_map(s, assessment)
            gaps = {k: score_gap(scores.get(k[0]), scores.get(k[1])) for k in result["bundles"]}
            selection = select_for_review(result["bundles"], budget, gaps, submission_ids, seed=run_id)
            artifact_counts = {sub.id: len(sub.artifacts) for sub in _submissions(s, assessment_id, submission_ids)}
            prior = {(p.submission_a_id, p.submission_b_id): p for p in s.scalars(
                select(CandidatePair).where(CandidatePair.run_id == run_id))} if refresh_in_place else {}
            selected_now = sum(1 for p in prior.values() if p.selected)
            for key, bundle in result["bundles"].items():
                multi = artifact_counts[key[0]] > 1 or artifact_counts[key[1]] > 1
                if key in prior:
                    row = prior[key]
                    row.fused_score, row.channels = bundle["fused"], bundle["channels"]
                    row.reasons, row.contributing = bundle["reasons"], bundle["contributing"]
                    continue
                take = key in selection and (not refresh_in_place or selected_now < budget)
                selected_now += int(take and refresh_in_place)
                s.add(CandidatePair(
                    run_id=run_id, submission_a_id=key[0], submission_b_id=key[1], fused_score=bundle["fused"],
                    channels=bundle["channels"], reasons=bundle["reasons"], contributing=bundle["contributing"],
                    scope="component_scope_unconfirmed" if multi else "single_file_bundles",
                    selected=take, selection_reason=selection.get(key) if take else None))
            for key, reason in selection.items():
                if reason == "random_control" and key not in prior:
                    s.add(CandidatePair(run_id=run_id, submission_a_id=key[0], submission_b_id=key[1],
                                        fused_score=0.0, channels={}, reasons=["random control (not retrieved)"],
                                        contributing=[], scope="random_control", selected=True,
                                        selection_reason=reason, is_random_control=True))
            _set_progress(run_id, "retrieval", candidate_pairs=len(result["bundles"]), selected=len(selection),
                          channel_availability=result["availability"], top_k=top_k, budget=budget)

    # ---- Stage D: reviewer on the selected pairs only
    with session_scope() as s:
        pair_ids = list(s.scalars(select(CandidatePair.id).where(
            CandidatePair.run_id == run_id, CandidatePair.selected.is_(True)).order_by(CandidatePair.id)))
    done = failed = 0
    stage_error = None
    _set_progress(run_id, "review", total=len(pair_ids), done=0, failed=0)
    reviewed: dict[int, int] = {}
    for pair_id in pair_ids:
        if cancelled():
            raise Cancelled()
        with session_scope() as s:
            pair = s.get(CandidatePair, pair_id)
            assessment = s.get(AssessmentVersion, assessment_id)
            comparison = run_reviewer(s, registry, s.get(Submission, pair.submission_a_id),
                                      s.get(Submission, pair.submission_b_id), assessment)
            status, error = comparison.status, (comparison.error_kind, comparison.error_message)
            comparison_id = comparison.id
        if status == "failed":
            failed += 1
            stage_error = {"kind": error[0], "message": error[1]}
            if error[0] in UNAVAILABLE_KINDS:
                failed += len(pair_ids) - done - failed
                _set_progress(run_id, "review", done=done, failed=failed, error=stage_error,
                              skipped_reason="provider unavailable; remaining pairs skipped")
                break
        else:
            done += 1
            reviewed[pair_id] = comparison_id
        _set_progress(run_id, "review", done=done, failed=failed, error=stage_error)
    if failed:
        failures.append("review")

    # ---- Stage E: cases (scores joined only now)
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        assessment = s.get(AssessmentVersion, assessment_id)
        scores = score_map(s, assessment)
        guidance = guidance_refs(current_guidance(s, assessment_id))
        created = 0
        for pair in s.scalars(select(CandidatePair).where(CandidatePair.run_id == run_id)):
            if pair.id in reviewed:
                make_case(s, run, assessment, pair, s.get(PairComparison, reviewed[pair.id]), scores)
                created += 1
            if pair.channels.get("exact", {}).get("status") == "ranked":
                if make_exact_case(s, run, assessment, pair, scores, guidance):
                    created += 1
        total_pairs = s.query(CandidatePair).filter(CandidatePair.run_id == run_id,
                                                    CandidatePair.is_random_control.is_(False)).count()
        run.coverage = {
            "submissions_in_scope": len(submission_ids),
            "distinct_artifacts": len(artifact_ids),
            "candidate_pairs_retrieved": total_pairs,
            "pairs_selected_for_review": len(pair_ids),
            "pairs_reviewed": len(reviewed),
            "review_budget": budget,
            "cases_created": s.query(ReviewCase).filter(ReviewCase.run_id == run_id).count(),
            "exhaustive": False,
            "notes": [
                f"Top-{top_k} retrieval per channel within the {len(submission_ids)} selected submissions; "
                "this is not exhaustive inconsistency detection.",
                f"Budget-capped review: {len(reviewed)} of {total_pairs} retrieved candidate pairs were reviewed.",
            ] + ([f"Stages with failures: {', '.join(failures)}."] if failures else []),
        }
        run.status = "partial" if failures else "succeeded"

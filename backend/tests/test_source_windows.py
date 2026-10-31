"""Long sources remain usable without claiming whole-program comparison."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.analysis.analyst import run_analyst
from app.analysis.evidence import ArtifactView
from app.analysis.partition import generate_partitioned, plan_windows
from app.analysis.reviewer import run_reviewer
from app.analysis.schemas import PairComparisonOut, SubmissionEvidenceOut
from app.db.models import AssessmentVersion, Submission
from app.db.session import session_scope
from app.providers.base import GenerationSettings, ProviderError, check_prompt_budget, generate_validated
from app.providers.registry import ProviderRegistry
from tests.conftest import ScriptedProvider, import_dataset, make_settings, write_dataset
from tests.test_providers import EVIDENCE


def long_view(label="artifact_A", n=200):
    text = "".join(f"value_{i:04d} = {i:04d} # synthetic source line\n" for i in range(n))
    return ArtifactView(label, 1, "12345678abcdef", {"c0":(text,1)}), text


def test_windows_cover_every_line_without_exceeding_budget():
    provider = ScriptedProvider(lambda *args: EVIDENCE)
    view, source = long_view()
    settings = GenerationSettings(num_ctx=1800, max_output_tokens=300)
    windows = plan_windows(provider, {view.label:view}, "system", "context", settings)
    assert len(windows) > 1
    spans = [next(iter(w.values())).chunks["c0"] for w in windows]
    assert "".join(t for t,_ in spans) == source
    assert spans[1][1] == 1 + len(spans[0][0].splitlines())


def test_partition_rejects_quote_outside_current_window_and_records_failed_pass():
    view, source = long_view()
    count = 0
    def respond(role, messages, settings):
        nonlocal count
        count += 1
        check_prompt_budget("mock", messages, settings)
        if count == 2:
            raise ProviderError("timeout", "synthetic timeout")
        return {**EVIDENCE, "factual_observations":[{"text":"claim about absent text", "evidence_refs":[
            {"artifact":view.label,"source_hash":view.short_hash,"chunk_id":"c0","quote":"print('not in source')"}]}]}
    provider = ScriptedProvider(respond)
    parsed, result = generate_partitioned(provider,"analyst",SubmissionEvidenceOut,
        GenerationSettings(num_ctx=1800,max_output_tokens=300),"system","context",
        {view.label:view},{view.label:source})
    assert parsed.coverage_status == "partial" and not parsed.factual_observations
    assert result.usage["partition"]["errors"][0]["kind"] == "timeout"
    assert result.usage["partition"]["rejected_claims"]
    assert result.usage["partition"]["global_reasoning_verified"] is False


def test_partition_does_not_promote_per_window_equivalence():
    a, source = long_view()
    b, _ = long_view("artifact_B")
    def respond(role, messages, settings):
        check_prompt_budget("mock",messages,settings)
        return dict(relation="apparently_equivalent",scope="complete_submission",requirement_basis="explicit_prompt",
                    shared_observations=[], material_differences=[],contradicted_analyst_claims=[],
                    alternative_explanations=[],missing_context=[],clarification_question="",suggested_category="alternate_method",
                    coverage_status="full_source_seen")
    provider = ScriptedProvider(respond)
    parsed, result = generate_partitioned(provider,"reviewer",PairComparisonOut,
        GenerationSettings(num_ctx=2200,max_output_tokens=300),"system","context",
        {a.label:a,b.label:b},{a.label:source,b.label:source},{1:[a.label],2:[b.label]})
    assert parsed.relation == "insufficient_evidence" and parsed.scope == "component"
    assert parsed.coverage_status == "partial"
    assert len(provider.calls) > 1
    assert result.usage["partition"]["all_planned_windows_completed"]


def test_repair_prompt_is_budget_checked_before_calling_provider():
    def invalid(*args):
        raise ProviderError("invalid_json", "invalid", raw_text="x"*6000)
    provider = ScriptedProvider(invalid)
    with pytest.raises(ProviderError, match="exceed") as exc:
        generate_validated(provider,"analyst",[{"role":"user","content":"small"}],SubmissionEvidenceOut,
                           GenerationSettings(num_ctx=1200,max_output_tokens=300))
    assert exc.value.kind == "prompt_overflow" and len(provider.calls) == 1


def test_partition_pass_limit_fails_visibly_without_model_calls():
    view, source = long_view(n=2500)
    provider = ScriptedProvider(lambda *args: EVIDENCE)
    with pytest.raises(ProviderError, match="limit is 16"):
        generate_partitioned(provider,"analyst",SubmissionEvidenceOut,
            GenerationSettings(num_ctx=1000,max_output_tokens=300),"system","context",
            {view.label:view},{view.label:source})
    assert provider.calls == []


def test_full_pipeline_falls_back_to_windows_and_keeps_provenance(tmp_path):
    from app.db.session import init_engine
    settings = make_settings(tmp_path, ollama_num_ctx=8192)
    init_engine(settings.effective_database_url)
    # Both the analyst and pair reviewer exceed the configured context.
    source = "".join(f"print('synthetic row {i:04d} with a reasonably long literal')\n" for i in range(600))
    csv, folder = write_dataset(tmp_path/"input",[("S001","A",8),("S002","B",9)],
                                {"S001.py":source,"S002.py":source+"# alternative export\n"})
    _, aid, _ = import_dataset(csv,folder)
    registry = ProviderRegistry(settings)
    with session_scope() as session:
        assessment = session.get(AssessmentVersion,aid)
        subs = list(session.scalars(select(Submission).where(Submission.assessment_id==aid)))
        evidence = run_analyst(session,registry,subs[0].artifacts[0],assessment)
        assert evidence.status == "partially_valid"
        assert evidence.usage["partition"]["all_planned_windows_completed"]
        comparison = run_reviewer(session,registry,subs[0],subs[1],assessment)
        assert comparison.status == "partially_valid"
        assert comparison.result["relation"] == "insufficient_evidence"
        assert comparison.sent_content["included"][0] == "original_source_windows"
        assert comparison.sent_content["summaries_included"] is False
        assert comparison.usage["partition"]["windows"]

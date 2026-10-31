"""Acceptance test 14 plus Ollama/Anthropic adapter contracts (no network)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.analysis.analyst import analyst_cache_key
from app.analysis.reviewer import run_reviewer
from app.analysis.schemas import PairComparisonOut, SubmissionEvidenceOut
from app.db.models import Artifact, AssessmentVersion, Submission
from app.db.session import init_engine, session_scope
from app.providers.anthropic_provider import AnthropicGenerationProvider
from app.providers.base import GenerationSettings, ProviderError, generate_validated, inline_schema
from app.providers.ollama import OllamaClient, OllamaEmbeddingProvider, OllamaGenerationProvider
from app.providers.registry import ProviderRegistry
from tests.conftest import PRINT3, import_dataset, make_settings, write_dataset

MODEL = "qwen3.5:4b-q4_K_M"
EVIDENCE = {"observed_purpose": "p", "approach_summary": "a", "explicit_requirement_evidence": [],
            "factual_observations": [], "possible_issues": [], "anonymization_limitations": [], "unknowns": [],
            "coverage_status": "full_source_seen"}


def ollama(handler) -> OllamaClient:
    return OllamaClient("http://ollama.test", transport=httpx.MockTransport(handler))


def tags_response(models=(MODEL, "qwen3-embedding:0.6b")):
    return httpx.Response(200, json={"models": [{"name": m, "model": m, "digest": f"digest-{m}"} for m in models]})


def test_ollama_chat_sends_schema_no_stream_bounded_context_and_disables_thinking():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return tags_response()
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "thinking"]})
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": json.dumps(EVIDENCE), "thinking": "hidden"},
                                         "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 5})

    provider = OllamaGenerationProvider(ollama(handler), MODEL)
    parsed, result = generate_validated(provider, "analyst", [{"role": "user", "content": "x"}],
                                        SubmissionEvidenceOut, GenerationSettings(num_ctx=8192, max_output_tokens=900))
    body = seen["body"]
    assert body["stream"] is False and body["think"] is False
    from app.providers.ollama import cap_arrays
    assert body["format"] == cap_arrays(inline_schema(SubmissionEvidenceOut), 6)
    assert "$defs" not in json.dumps(body["format"])
    assert body["format"]["properties"]["factual_observations"]["maxItems"] == 6
    assert body["options"]["num_ctx"] == 8192 and body["options"]["num_predict"] == 900
    assert result.model_digest == f"digest-{MODEL}" and result.usage["output_tokens"] == 5
    assert "hidden" not in result.raw_text                 # hidden reasoning is never kept
    assert parsed.coverage_status == "full_source_seen"


def test_ollama_missing_model_reports_exact_pull_command_without_switching():
    def handler(request):
        return tags_response(models=("gemma4:e4b",))
    provider = OllamaGenerationProvider(ollama(handler), MODEL)
    with pytest.raises(ProviderError) as err:
        provider.generate_structured("analyst", [], {}, GenerationSettings())
    assert err.value.kind == "model_missing"
    assert f"ollama pull {MODEL}" in err.value.message


@pytest.mark.parametrize("response,kind", [
    (httpx.Response(200, json={"message": {"content": '{"partial": '}, "done_reason": "length"}), "truncated"),
    (httpx.Response(500, text="CUDA error: out of memory"), "oom"),
    (httpx.Response(404, text="model not found"), "model_missing"),
])
def test_ollama_failure_kinds_are_distinct(response, kind):
    def handler(request):
        if request.url.path == "/api/tags":
            return tags_response()
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": []})
        return response
    with pytest.raises(ProviderError) as err:
        OllamaGenerationProvider(ollama(handler), MODEL).generate_structured("analyst", [], {}, GenerationSettings())
    assert err.value.kind == kind


def test_invalid_json_gets_exactly_one_repair_then_fails_visibly():
    calls = []

    def handler(request):
        if request.url.path == "/api/tags":
            return tags_response()
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": []})
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "not json"}, "done_reason": "stop"})

    with pytest.raises(ProviderError) as err:
        generate_validated(OllamaGenerationProvider(ollama(handler), MODEL), "analyst",
                           [{"role": "user", "content": "x"}], SubmissionEvidenceOut, GenerationSettings())
    assert len(calls) == 2 and err.value.kind in ("invalid_json", "schema_invalid")
    assert "did not match" in calls[1]["messages"][-1]["content"]


def test_ollama_embed_batches_with_truncate_false():
    bodies = []

    def handler(request):
        if request.url.path == "/api/tags":
            return tags_response()
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]] * len(body["input"])})

    result = OllamaEmbeddingProvider(ollama(handler), "qwen3-embedding:0.6b", batch_size=2).embed(["a", "b", "c"], "source_v1")
    assert [len(b["input"]) for b in bodies] == [2, 1]
    assert all(b["truncate"] is False for b in bodies)
    assert result.dims == 2 and len(result.vectors) == 3 and not result.is_mock


class ExplodingClient:
    class messages:  # noqa: N801 - mimics SDK attribute
        @staticmethod
        def create(**kwargs):
            raise AssertionError("network call attempted")


def test_anthropic_reviewer_never_sends_when_cloud_disabled_or_key_missing():
    disabled = AnthropicGenerationProvider("claude-opus-5-5", "sk-test", allow_cloud=False, client=ExplodingClient())
    with pytest.raises(ProviderError) as err:
        disabled.generate_structured("reviewer", [{"role": "user", "content": "x"}], {}, GenerationSettings())
    assert err.value.kind == "cloud_disabled"
    no_key = AnthropicGenerationProvider("claude-opus-5-5", "", allow_cloud=True, client=ExplodingClient())
    with pytest.raises(ProviderError) as err:
        no_key.generate_structured("reviewer", [{"role": "user", "content": "x"}], {}, GenerationSettings())
    assert err.value.kind == "missing_credentials"


class FakeResponse:
    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.content = [type("Block", (), {"type": "text", "text": text})()]
        self.stop_reason = stop_reason
        self.usage = type("Usage", (), {"input_tokens": 12, "output_tokens": 34})()
        self.stop_details = None


class RecordingClient:
    def __init__(self, response):
        self.response = response
        self.kwargs = None
        outer = self

        class Messages:
            def create(self, **kwargs):
                outer.kwargs = kwargs
                return outer.response
        self.messages = Messages()


def test_anthropic_adapter_uses_structured_outputs_and_maps_refusal():
    payload = {"relation": "insufficient_evidence", "scope": "component", "requirement_basis": "unavailable",
               "shared_observations": [], "material_differences": [], "contradicted_analyst_claims": [],
               "alternative_explanations": [], "missing_context": [], "clarification_question": "",
               "suggested_category": "incomplete_or_anonymized_evidence", "coverage_status": "full_source_seen"}
    client = RecordingClient(FakeResponse(json.dumps(payload)))
    provider = AnthropicGenerationProvider("claude-opus-5-5", "sk-test", allow_cloud=True, client=client)
    parsed, result = generate_validated(provider, "reviewer", [{"role": "system", "content": "sys"},
                                                               {"role": "user", "content": "u"}],
                                        PairComparisonOut, GenerationSettings())
    assert client.kwargs["output_config"]["format"]["type"] == "json_schema"
    assert client.kwargs["system"] == "sys" and client.kwargs["messages"] == [{"role": "user", "content": "u"}]
    assert "temperature" not in client.kwargs
    assert parsed.relation == "insufficient_evidence" and result.provider == "anthropic"
    refusing = AnthropicGenerationProvider("claude-opus-5-5", "sk-test", allow_cloud=True,
                                           client=RecordingClient(FakeResponse("", "refusal")))
    with pytest.raises(ProviderError) as err:
        refusing.generate_structured("reviewer", [{"role": "user", "content": "u"}], {}, GenerationSettings())
    assert err.value.kind == "refusal"


def test_provider_switch_changes_cache_keys_and_never_falls_back(tmp_path: Path):
    settings = make_settings(tmp_path, reviewer_provider="anthropic", reviewer_model="claude-opus-5-5",
                             allow_cloud_inference=False)
    init_engine(settings.effective_database_url)
    csv_path, src = write_dataset(tmp_path / "d", [("S001", "G", 10), ("S002", "G", 9)],
                                  {"S001_a.py": PRINT3.format(a="1", b="2", c="3"),
                                   "S002_a.py": PRINT3.format(a="4", b="5", c="6")})
    import_dataset(csv_path, src)
    registry = ProviderRegistry(settings)
    with session_scope() as s:
        artifact = s.scalar(select(Artifact))
        assessment = s.scalar(select(AssessmentVersion))
        assert analyst_cache_key(artifact, assessment, "ollama", MODEL) != analyst_cache_key(
            artifact, assessment, "ollama", "gemma4:e4b")
        assert analyst_cache_key(artifact, assessment, "ollama", MODEL) != analyst_cache_key(
            artifact, assessment, "anthropic", MODEL)
        subs = list(s.scalars(select(Submission).order_by(Submission.id)))
        comparison = run_reviewer(s, registry, subs[0], subs[1], assessment)
        assert comparison.status == "failed" and comparison.error_kind == "cloud_disabled"
        assert comparison.provider == "anthropic" and comparison.result is None   # no silent local fallback
    status = registry.status()
    assert status["local_only"] is True
    assert status["roles"]["reviewer"]["ready"] is False
    assert "ANTHROPIC" not in json.dumps(status)            # never echoes the key


def test_registry_reports_missing_models_with_pull_commands(tmp_path: Path):
    settings = make_settings(tmp_path, analyst_provider="ollama", analyst_model=MODEL)
    client = ollama(lambda request: httpx.Response(200, json={"version": "0.40.13"}) if request.url.path == "/api/version"
                    else tags_response(models=("gemma4:e4b",)))
    status = ProviderRegistry(settings, client).status()
    analyst = status["roles"]["analyst"]
    assert analyst["installed"] is False and analyst["pull_command"] == f"ollama pull {MODEL}"
    assert status["roles"]["embedding"]["is_mock"] is True

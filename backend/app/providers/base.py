"""Provider contracts shared by Ollama, Anthropic and the labeled mock provider.

Failure kinds are distinct so the UI can say exactly what went wrong:
model_missing, unreachable, timeout, invalid_json, schema_invalid, truncated,
refusal, oom, overlength, cloud_disabled, missing_credentials, provider_error.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

UNAVAILABLE_KINDS = {"model_missing", "unreachable", "cloud_disabled", "missing_credentials"}


class ProviderError(Exception):
    def __init__(self, kind: str, message: str, raw_text: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.raw_text = raw_text

    @property
    def provider_unavailable(self) -> bool:
        return self.kind in UNAVAILABLE_KINDS


@dataclass
class GenerationSettings:
    num_ctx: int = 8192
    max_output_tokens: int = 2048
    temperature: float = 0.0
    # Only the mock provider reads these two: a fixture key for scripted
    # synthetic responses and deterministic context for conservative fallbacks.
    fixture_key: str | None = None
    mock_context: dict[str, Any] | None = None


@dataclass
class StructuredResult:
    data: dict[str, Any]
    raw_text: str
    provider: str
    model: str
    model_digest: str | None
    is_mock: bool
    usage: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    repaired: bool = False


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    provider: str
    model: str
    model_digest: str | None
    dims: int
    is_mock: bool
    duration_ms: int = 0


class GenerationProvider(Protocol):
    name: str
    model: str

    def generate_structured(self, role: str, messages: list[dict[str, str]], schema: dict[str, Any],
                            settings: GenerationSettings) -> StructuredResult: ...


class EmbeddingProvider(Protocol):
    name: str
    model: str

    def embed(self, texts: list[str], representation_version: str) -> EmbeddingResult: ...


def inline_schema(model_cls: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema with $defs inlined and closed objects.

    Closed objects with every property required suit both Ollama's grammar
    constraint and Anthropic structured outputs.
    """
    schema = model_cls.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            out = {k: resolve(v) for k, v in node.items() if k not in ("title", "default")}
            if out.get("type") == "object" and "properties" in out:
                out["required"] = list(out["properties"].keys())
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


M = TypeVar("M", bound=BaseModel)


def parse_json_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("invalid_json", f"Output is not valid JSON: {exc}", raw_text=text) from exc
    if not isinstance(data, dict):
        raise ProviderError("schema_invalid", "Output JSON is not an object", raw_text=text)
    return data


PROMPT_MARGIN_TOKENS = 256


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    """Conservative estimate (about 3 characters per token, plus per-message overhead).

    This is a budget guard, not exact accounting; actual usage is re-checked from
    the provider's reported prompt token count where available."""
    return sum(len(m.get("content", "")) // 3 + 8 for m in messages)


def check_prompt_budget(provider_name: str, messages: list[dict[str, str]], settings: GenerationSettings) -> None:
    if provider_name == "anthropic" or not settings.num_ctx:
        return
    budget = settings.num_ctx - settings.max_output_tokens - PROMPT_MARGIN_TOKENS
    estimate = estimate_tokens(messages)
    if estimate > budget:
        raise ProviderError("prompt_overflow",
                            f"Estimated {estimate} prompt tokens exceed the {budget}-token budget "
                            f"(num_ctx {settings.num_ctx} minus output allowance). Nothing was truncated; "
                            "coverage for this item is incomplete.")


def generate_validated(provider: GenerationProvider, role: str, messages: list[dict[str, str]],
                       model_cls: type[M], settings: GenerationSettings) -> tuple[M, StructuredResult]:
    """Call the provider, validate against the Pydantic model, allow ONE repair.

    Domain rules and quote validation happen afterwards, in application code.
    """
    schema = inline_schema(model_cls)
    check_prompt_budget(provider.name, messages, settings)
    started = time.monotonic()
    try:
        result = provider.generate_structured(role, messages, schema, settings)
        return model_cls.model_validate(result.data), result
    except ValidationError as exc:
        first_error, first_raw = str(exc)[:1500], result.raw_text
    except ProviderError as exc:
        if exc.kind not in ("invalid_json", "schema_invalid"):
            raise
        first_error, first_raw = exc.message[:1500], exc.raw_text or ""
    repair_messages = messages + [
        {"role": "assistant", "content": first_raw[:6000]},
        {"role": "user", "content": "Your previous output did not match the required JSON schema. "
                                    f"Validation errors:\n{first_error}\n"
                                    "Return only corrected JSON that matches the schema."},
    ]
    check_prompt_budget(provider.name, repair_messages, settings)
    repaired = provider.generate_structured(role, repair_messages, schema, settings)
    try:
        parsed = model_cls.model_validate(repaired.data)
    except ValidationError as exc:
        raise ProviderError("schema_invalid", f"Output failed schema validation after one repair: {str(exc)[:800]}",
                            raw_text=repaired.raw_text) from exc
    repaired.repaired = True
    repaired.duration_ms = int((time.monotonic() - started) * 1000)
    return parsed, repaired

"""Claude reviewer adapter (official Anthropic SDK, Messages API structured outputs).

Disabled unless ALLOW_CLOUD_INFERENCE=true and ANTHROPIC_API_KEY is set on the
backend. No automatic fallback to another model or provider: refusals,
truncation and API errors surface as distinct failures.
"""
from __future__ import annotations

import time
from typing import Any

from app.providers.base import GenerationSettings, ProviderError, StructuredResult, parse_json_text


class AnthropicGenerationProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str, allow_cloud: bool, max_tokens: int = 16000,
                 effort: str | None = "high", client: Any | None = None):
        self.model = model
        self.api_key = api_key
        self.allow_cloud = allow_cloud
        self.max_tokens = max_tokens
        self.effort = effort
        self._client = client

    def _get_client(self) -> Any:
        if not self.allow_cloud:
            raise ProviderError("cloud_disabled",
                                "Cloud inference is disabled (ALLOW_CLOUD_INFERENCE=false). Nothing was sent.")
        if not self.api_key:
            raise ProviderError("missing_credentials",
                                "ANTHROPIC_API_KEY is not set on the backend. Nothing was sent.")
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def generate_structured(self, role: str, messages: list[dict[str, str]], schema: dict[str, Any],
                            settings: GenerationSettings) -> StructuredResult:
        client = self._get_client()
        import anthropic

        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        started = time.monotonic()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=turns,
                output_config=output_config,
            )
        except anthropic.NotFoundError as exc:
            raise ProviderError("model_missing", f"Claude model {self.model!r} not found: {exc}") from exc
        except anthropic.AuthenticationError as exc:
            raise ProviderError("missing_credentials", f"Anthropic authentication failed: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("provider_error", f"Anthropic rate limit: {exc}") from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderError("timeout", f"Anthropic request timed out: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("unreachable", f"Anthropic API not reachable: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError("provider_error", f"Anthropic HTTP {exc.status_code}: {exc}") from exc
        duration_ms = int((time.monotonic() - started) * 1000)

        text = "".join(getattr(block, "text", "") for block in response.content if block.type == "text")
        if response.stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            raise ProviderError("refusal", f"Claude declined the request (category: {category}).", raw_text=text)
        if response.stop_reason == "max_tokens":
            raise ProviderError("truncated", "Claude output hit max_tokens before completing the JSON.",
                                raw_text=text)
        usage = {
            "prompt_tokens": getattr(response.usage, "input_tokens", None),
            "output_tokens": getattr(response.usage, "output_tokens", None),
        }
        return StructuredResult(parse_json_text(text), text, self.name, self.model, None, False, usage,
                                duration_ms)

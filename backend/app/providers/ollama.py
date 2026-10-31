"""Ollama HTTP adapters: /api/chat with a JSON schema in ``format`` and
/api/embed with ``truncate=false``. Models are never pulled or switched
automatically; a missing model is reported with its exact pull command.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from app.providers.base import (
    EmbeddingResult,
    GenerationSettings,
    ProviderError,
    StructuredResult,
    parse_json_text,
)

_generation_lock = threading.Semaphore(1)


def pull_command(model: str) -> str:
    return f"ollama pull {model}"


def cap_arrays(schema: Any, max_items: int) -> Any:
    """Add maxItems to every array so grammar-constrained decoding cannot run on
    indefinitely (observed with greedy decoding on a 4B model)."""
    if isinstance(schema, dict):
        out = {k: cap_arrays(v, max_items) for k, v in schema.items()}
        if out.get("type") == "array" and max_items > 0:
            out["maxItems"] = min(int(out.get("maxItems", max_items)), max_items)
        return out
    if isinstance(schema, list):
        return [cap_arrays(v, max_items) for v in schema]
    return schema


class OllamaClient:
    def __init__(self, base_url: str, timeout_s: float = 600.0, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout_s, connect=5.0),
                                    transport=transport)
        self._tags_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._show_cache: dict[str, dict[str, Any]] = {}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", f"Ollama request timed out: {path}") from exc
        except httpx.TransportError as exc:
            raise ProviderError("unreachable", f"Ollama is not reachable at {self.base_url}: {exc}") from exc
        if response.status_code == 404:
            raise ProviderError("model_missing", response.text[:300])
        if response.status_code >= 400:
            text = response.text[:500]
            lowered = text.lower()
            if "out of memory" in lowered or ("cuda" in lowered and "memory" in lowered):
                raise ProviderError("oom", f"Ollama ran out of memory: {text}")
            if "context length" in lowered or "input length" in lowered or "exceeds" in lowered:
                raise ProviderError("overlength", f"Input exceeds model context: {text}")
            raise ProviderError("provider_error", f"Ollama HTTP {response.status_code}: {text}")
        return response.json()

    def close(self) -> None:
        self._client.close()

    def version(self) -> str:
        return self._request("GET", "/api/version").get("version", "unknown")

    def tags(self, max_age_s: float = 15.0) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._tags_cache and now - self._tags_cache[0] < max_age_s:
            return self._tags_cache[1]
        models = self._request("GET", "/api/tags").get("models", [])
        self._tags_cache = (now, models)
        return models

    def model_info(self, model: str) -> dict[str, Any] | None:
        for entry in self.tags():
            if entry.get("name") == model or entry.get("model") == model:
                return entry
        return None

    def require_model(self, model: str) -> dict[str, Any]:
        info = self.model_info(model)
        if info is None:
            raise ProviderError("model_missing",
                                f"Model {model} is not installed in Ollama. Run: {pull_command(model)}")
        return info

    def show(self, model: str) -> dict[str, Any]:
        if model not in self._show_cache:
            self._show_cache[model] = self._request("POST", "/api/show", json={"model": model})
        return self._show_cache[model]


class OllamaGenerationProvider:
    name = "ollama"

    def __init__(self, client: OllamaClient, model: str, think_mode: str = "auto", max_list_items: int = 6):
        self.client = client
        self.model = model
        self.think_mode = think_mode
        self.max_list_items = max_list_items

    def _think_param(self) -> bool | None:
        if self.think_mode == "true":
            return True
        if self.think_mode == "false":
            return False
        try:
            capabilities = self.client.show(self.model).get("capabilities", [])
        except ProviderError:
            return None
        # Disable hidden reasoning for bounded structured extraction when supported.
        return False if "thinking" in capabilities else None

    def generate_structured(self, role: str, messages: list[dict[str, str]], schema: dict[str, Any],
                            settings: GenerationSettings) -> StructuredResult:
        info = self.client.require_model(self.model)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "format": cap_arrays(schema, self.max_list_items),
            "stream": False,
            "options": {
                "num_ctx": settings.num_ctx,
                "num_predict": settings.max_output_tokens,
                "temperature": settings.temperature,
            },
        }
        think = self._think_param()
        if think is not None:
            body["think"] = think
        started = time.monotonic()
        with _generation_lock:
            data = self.client._request("POST", "/api/chat", json=body)
        duration_ms = int((time.monotonic() - started) * 1000)
        message = data.get("message") or {}
        # message["thinking"] (hidden reasoning) is intentionally discarded, never stored as evidence.
        content = message.get("content", "")
        prompt_tokens = data.get("prompt_eval_count") or 0
        if prompt_tokens and prompt_tokens >= settings.num_ctx - 16:
            # Ollama keeps only the tail of an over-long prompt; never accept that as complete coverage.
            raise ProviderError("prompt_overflow", f"Prompt used {prompt_tokens} of {settings.num_ctx} context tokens; "
                                                   "the runtime may have truncated it.", raw_text=None)
        if data.get("done_reason") == "length":
            raise ProviderError("truncated", "Output hit the token limit before completing the JSON.",
                                raw_text=content)
        parsed = parse_json_text(content)
        usage = {
            "prompt_tokens": data.get("prompt_eval_count"),
            "output_tokens": data.get("eval_count"),
            "total_duration_ns": data.get("total_duration"),
            "load_duration_ns": data.get("load_duration"),
        }
        return StructuredResult(parsed, content, self.name, self.model, info.get("digest"), False,
                                usage, duration_ms)


class OllamaEmbeddingProvider:
    name = "ollama"

    def __init__(self, client: OllamaClient, model: str, batch_size: int = 16):
        self.client = client
        self.model = model
        self.batch_size = batch_size

    def embed(self, texts: list[str], representation_version: str) -> EmbeddingResult:
        info = self.client.require_model(self.model)
        vectors: list[list[float]] = []
        started = time.monotonic()
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            data = self.client._request("POST", "/api/embed",
                                        json={"model": self.model, "input": batch, "truncate": False})
            embeddings = data.get("embeddings") or []
            if len(embeddings) != len(batch):
                raise ProviderError("provider_error",
                                    f"Expected {len(batch)} embeddings, received {len(embeddings)}")
            vectors.extend(embeddings)
        dims = len(vectors[0]) if vectors else 0
        return EmbeddingResult(vectors, self.name, self.model, info.get("digest"), dims, False,
                               int((time.monotonic() - started) * 1000))

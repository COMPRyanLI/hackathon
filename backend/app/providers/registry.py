"""Builds per-role providers from settings. Roles are independently
configurable; nothing switches provider or model implicitly."""
from __future__ import annotations

from typing import Any

from app.config import Settings
from app.providers.anthropic_provider import AnthropicGenerationProvider
from app.providers.base import EmbeddingProvider, GenerationProvider, GenerationSettings, ProviderError
from app.providers.mock import MockEmbeddingProvider, MockGenerationProvider
from app.providers.ollama import OllamaClient, OllamaEmbeddingProvider, OllamaGenerationProvider, pull_command

GENERATION_ROLES = ("analyst", "reviewer")


class ProviderRegistry:
    def __init__(self, settings: Settings, ollama_client: OllamaClient | None = None):
        self.settings = settings
        self.ollama = ollama_client or OllamaClient(settings.ollama_base_url, settings.ollama_timeout_s)
        self._generation: dict[str, GenerationProvider] = {}
        self._embedding: EmbeddingProvider | None = None
        self._mock_variant: ProviderRegistry | None = None

    def for_mode(self, mode: str | None) -> "ProviderRegistry":
        """'mock' returns an explicitly labeled all-mock registry; anything else is the configured one."""
        if mode != "mock":
            return self
        if self._mock_variant is None:
            settings = self.settings.model_copy(update={
                "analyst_provider": "mock", "analyst_model": "mock-scripted-v1",
                "reviewer_provider": "mock", "reviewer_model": "mock-scripted-v1",
                "embedding_provider": "mock", "embedding_model": "mock-hash-embedding-v1"})
            self._mock_variant = ProviderRegistry(settings, self.ollama)
        return self._mock_variant

    def generation(self, role: str) -> GenerationProvider:
        # The note-applicability scan uses the reviewer role's configuration.
        config_role = "reviewer" if role == "note_applicability" else role
        if config_role not in self._generation:
            provider, model = self.settings.role_config(config_role)
            if provider == "ollama":
                self._generation[config_role] = OllamaGenerationProvider(
                    self.ollama, model, self.settings.ollama_think, self.settings.ollama_max_list_items)
            elif provider == "anthropic":
                self._generation[config_role] = AnthropicGenerationProvider(
                    model, self.settings.anthropic_api_key.get_secret_value(),
                    self.settings.allow_cloud_inference, self.settings.anthropic_max_tokens,
                    self.settings.anthropic_effort)
            else:
                self._generation[config_role] = MockGenerationProvider(
                    self.settings.resolve(self.settings.mock_fixture_dir), model or "mock-scripted-v1")
        return self._generation[config_role]

    def embedding(self) -> EmbeddingProvider:
        if self._embedding is None:
            provider, model = self.settings.role_config("embedding")
            if provider == "ollama":
                self._embedding = OllamaEmbeddingProvider(self.ollama, model, self.settings.embedding_batch_size)
            elif provider == "mock":
                self._embedding = MockEmbeddingProvider()
            else:
                raise ProviderError("provider_error", f"Embedding provider {provider!r} is not supported")
        return self._embedding

    def generation_settings(self, role: str, **overrides: Any) -> GenerationSettings:
        limit = (self.settings.analyst_max_output_tokens if role == "analyst"
                 else self.settings.reviewer_max_output_tokens)
        return GenerationSettings(num_ctx=self.settings.ollama_num_ctx, max_output_tokens=limit, **overrides)

    def snapshot(self) -> dict[str, Any]:
        s = self.settings
        return {
            "analyst": {"provider": s.analyst_provider, "model": s.analyst_model},
            "reviewer": {"provider": s.reviewer_provider, "model": s.reviewer_model},
            "embedding": {"provider": s.embedding_provider, "model": s.embedding_model},
            "allow_cloud_inference": s.allow_cloud_inference,
            "num_ctx": s.ollama_num_ctx,
            "generation": {role: self.generation_signature(role) for role in GENERATION_ROLES},
            "embedding_digest": self._model_digest(s.embedding_provider, s.embedding_model),
        }

    def _model_digest(self, provider: str, model: str) -> str:
        if provider != "ollama":
            return model
        try:
            info = self.ollama.model_info(model)
            return (info or {}).get("digest") or "unavailable"
        except ProviderError:
            return "unavailable"

    def generation_signature(self, role: str) -> dict[str, Any]:
        provider = self.generation(role)
        s = self.settings
        limits = self.generation_settings(role)
        return {"provider": provider.name, "model": provider.model,
                "digest": self._model_digest(provider.name, provider.model),
                "num_ctx": limits.num_ctx, "max_output_tokens": limits.max_output_tokens,
                "temperature": limits.temperature, "think": s.ollama_think,
                "max_list_items": s.ollama_max_list_items,
                "anthropic_max_tokens": s.anthropic_max_tokens, "anthropic_effort": s.anthropic_effort}

    def status(self) -> dict[str, Any]:
        s = self.settings
        ollama: dict[str, Any] = {"base_url": s.ollama_base_url}
        installed: list[str] = []
        try:
            ollama["version"] = self.ollama.version()
            installed = [m.get("name") for m in self.ollama.tags(max_age_s=0)]
            ollama["reachable"] = True
            ollama["installed_models"] = installed
        except ProviderError as exc:
            ollama["reachable"] = False
            ollama["error"] = exc.message
        roles = {}
        for role in ("analyst", "reviewer", "embedding"):
            provider, model = s.role_config(role)
            entry: dict[str, Any] = {"provider": provider, "model": model}
            if provider == "ollama":
                entry["installed"] = model in installed
                entry["ready"] = bool(ollama.get("reachable")) and entry["installed"]
                if not entry["installed"]:
                    entry["pull_command"] = pull_command(model)
                else:
                    try:
                        entry["capabilities"] = self.ollama.show(model).get("capabilities", [])
                    except ProviderError:
                        entry["capabilities"] = []
            elif provider == "anthropic":
                entry["cloud_allowed"] = s.allow_cloud_inference
                entry["api_key_configured"] = bool(s.anthropic_api_key.get_secret_value())
                entry["ready"] = entry["cloud_allowed"] and entry["api_key_configured"]
                entry["sends"] = ("Original source of the selected pair, validated analyst summaries, deterministic "
                                  "differences, assignment context and approved guidance. Never grades, grader "
                                  "labels, student IDs or filename suffixes.")
            else:
                entry["ready"] = True
                entry["is_mock"] = True
                entry["warning"] = "MOCK provider selected: outputs are labeled and are not real findings."
            roles[role] = entry
        return {"ollama": ollama, "roles": roles, "allow_cloud_inference": s.allow_cloud_inference,
                "local_only": not s.allow_cloud_inference}

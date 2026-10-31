"""Application settings.

All paths in settings are resolved against the project root (the folder that
contains ``backend/``), so ``./cs1_scores_anon.csv`` works no matter which
directory the server is started from.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PROVIDER_NAMES = ("ollama", "anthropic", "mock")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    csv_path: str = "./cs1_scores_anon.csv"
    sa1_path: str = "./SA1"
    private_data_dir: str = "./data/private"
    database_url: str = ""
    dataset_key: str = "cs1"
    assessment_name: str = "SA1"
    filename_id_regex: str = r"^(?P<student_id>S[0-9]+)(?:_.*)?\.py$"

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_timeout_s: float = 600.0
    # auto: send think=false when /api/show reports a "thinking" capability.
    ollama_think: str = "auto"
    # Grammar-level cap on every JSON array in Ollama structured output (0 disables).
    ollama_max_list_items: int = 6

    analyst_provider: str = "ollama"
    analyst_model: str = "qwen3.5:4b-q4_K_M"
    reviewer_provider: str = "ollama"
    reviewer_model: str = "qwen3.5:4b-q4_K_M"
    embedding_provider: str = "ollama"
    embedding_model: str = "qwen3-embedding:0.6b"

    ollama_num_ctx: int = 8192
    analyst_max_output_tokens: int = 2048
    reviewer_max_output_tokens: int = 3072
    embedding_batch_size: int = 16
    max_concurrent_generations: int = 1
    retrieval_top_k_per_channel: int = 5
    initial_review_pair_budget: int = 30
    rrf_k: int = 60

    allow_cloud_inference: bool = False
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_max_tokens: int = 16000
    anthropic_effort: str = "high"

    host: str = "127.0.0.1"
    port: int = 8000
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    actor_name: str = "local-instructor"
    start_worker: bool = True
    # Synthetic, labeled scripted responses used only by the mock provider.
    mock_fixture_dir: str = "./fixtures/synthetic/demo/scripted"

    @field_validator("analyst_provider", "reviewer_provider", "embedding_provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in PROVIDER_NAMES:
            raise ValueError(f"unknown provider {value!r}; expected one of {PROVIDER_NAMES}")
        return value

    @property
    def effective_origins(self) -> list[str]:
        """Configured origins plus the UI served by this server on its own port."""
        own = [f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"]
        return list(dict.fromkeys(self.allowed_origins + own))

    def resolve(self, path_value: str) -> Path:
        path = Path(path_value)
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    @property
    def private_dir(self) -> Path:
        return self.resolve(self.private_data_dir)

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.private_dir / 'gradelens.db').as_posix()}"

    def role_config(self, role: str) -> tuple[str, str]:
        provider = getattr(self, f"{role}_provider")
        model = getattr(self, f"{role}_model")
        return provider, model


@lru_cache
def get_settings() -> Settings:
    return Settings()

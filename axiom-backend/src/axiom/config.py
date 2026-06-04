"""Application configuration.

Single source of truth for runtime config. All values are loaded from
environment variables (or `.env` in development) and validated at startup,
so misconfiguration fails fast rather than at the first request.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_env: Literal["development", "staging", "production"] = "development"
    app_name: str = "axiom"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    secret_key: str = Field(min_length=16)

    # --- Database ---
    database_url: PostgresDsn

    # --- Redis ---
    redis_url: RedisDsn

    # --- LLM ---
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    default_llm_provider: Literal["anthropic", "openai"] = "anthropic"
    default_model: str = "claude-sonnet-4-6"
    default_embedding_model: str = "text-embedding-3-small"

    # --- RAG ---
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 100
    rag_top_k: int = 6
    rag_rerank_top_k: int = 3

    # --- Agent runtime ---
    agent_max_iterations: int = 10
    agent_timeout_seconds: int = 120

    # --- Safety ---
    safety_enable_prompt_injection_guard: bool = True
    safety_enable_pii_redaction: bool = True
    safety_max_input_tokens: int = 20_000

    # --- Observability ---
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "axiom-api"
    enable_prometheus: bool = True

    # --- Rate limiting ---
    rate_limit_per_minute: int = 60

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Memoized settings accessor — safe to call from anywhere."""
    return Settings()  # type: ignore[call-arg]

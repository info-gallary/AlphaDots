"""Environment-driven settings. Every value has a local default that needs no credentials."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALPHADOTS_", env_file=".env", extra="ignore")

    service_name: str = "alphadots"
    log_level: str = "INFO"
    log_json: bool = True
    metrics_port: int = Field(default=0, ge=0, le=65535, description="0 disables the endpoint.")
    # Where run-once jobs push their metrics on exit; empty disables the push.
    pushgateway_url: str = ""

    bus_backend: Literal["memory", "kafka"] = "kafka"
    kafka_bootstrap: str = "localhost:19092"

    database_url: str = "sqlite+aiosqlite:///var/alphadots.sqlite3"
    fixtures_dir: Path = Path("data/fixtures")

    retry_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_delay_seconds: float = Field(default=0.2, ge=0)

    # Replay-mode modelling assumption: each stage adds this to `available_at` so that
    # point-in-time filters never assume zero processing time. Not a measurement.
    stage_latency_allowance_seconds: float = Field(default=1.0, ge=0)

    # Heuristic signal priors. These are configuration, NOT estimates: tau must be fitted
    # on training folds before the signal can claim anything (M3).
    signal_tau_seconds: float = Field(default=86_400.0, gt=0)
    signal_lookback_seconds: float = Field(default=432_000.0, gt=0)
    signal_stance_threshold: float = Field(default=0.15, ge=0, le=1)
    signal_min_effective_weight: float = Field(default=0.2, ge=0)

    api_token: SecretStr | None = None
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=18000, ge=1, le=65535)
    # Shown on every response and in the UI. Only fixture replay exists today.
    feed_mode: Literal["simulated", "delayed", "live"] = "simulated"

    # Where any service reaches the read-only AlphaDots API (currently only research_agent).
    api_url: str = "http://localhost:18000"

    # --- research_agent: where the API reaches it, and where it listens --------------
    research_agent_url: str = "http://localhost:18100"
    research_agent_timeout_seconds: float = Field(default=30.0, gt=0)
    research_agent_host: str = "127.0.0.1"
    research_agent_port: int = Field(default=18100, ge=1, le=65535)
    research_agent_concurrency: int = Field(
        default=2, ge=1, le=16, description="Bounded-parallel agent runs per process."
    )

    # --- LLM adapter: "auto" means Groq if a key is set, else the deterministic mock --
    llm_provider: Literal["auto", "mock", "groq"] = "auto"
    groq_api_key: SecretStr | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Left unset by default: Groq's published rates change, and a stale hard-coded figure
    # would be worse than an honest "not estimated". Set both to get a cost estimate.
    groq_price_input_per_m_usd: float | None = Field(default=None, ge=0)
    groq_price_output_per_m_usd: float | None = Field(default=None, ge=0)

    # --- bounds applied to every agent run (research brief or monitor summary) -------
    agent_max_tool_calls: int = Field(default=8, ge=1, le=50)
    agent_max_tokens: int = Field(default=900, ge=64, le=8000)
    agent_timeout_seconds: float = Field(default=25.0, gt=0, le=120)
    agent_max_correction_attempts: int = Field(
        default=1, ge=0, le=3, description="Retries allowed when the model's JSON fails validation."
    )
    agent_max_cost_usd: float = Field(
        default=0.02,
        ge=0,
        description="A run whose estimated cost would exceed this abstains before calling.",
    )
    monitor_cache_seconds: float = Field(default=30.0, ge=0)

    @field_validator(
        "api_token",
        "groq_api_key",
        "groq_price_input_per_m_usd",
        "groq_price_output_per_m_usd",
        mode="before",
    )
    @classmethod
    def _empty_env_value_means_unset(cls, value: object) -> object:
        """An env var left as `${VAR:-}` in compose is an empty string, not absent -
        pydantic-settings only skips a field for a genuinely *unset* variable, so an
        empty string would otherwise fail float parsing instead of falling back to the
        field's default."""
        return None if value == "" else value

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

    @field_validator("api_token", mode="before")
    @classmethod
    def _empty_token_means_disabled(cls, value: object) -> object:
        return None if value == "" else value

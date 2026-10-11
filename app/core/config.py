"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic import ValidationError as PydanticValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.exceptions import ConfigurationError

_SUPPORTED_DB_SCHEMES = ("postgresql+psycopg://", "postgresql://")
_SUPPORTED_BROKER_SCHEMES = ("redis://", "rediss://")


class Settings(BaseSettings):
    """Runtime configuration.

    Values come from the process environment, falling back to a local ``.env``
    file. Defaults are only ever suitable for local development.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(
        default="postgresql+psycopg://football:football@localhost:5432/football_data",
    )
    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    statsbomb_source_dir: Path = Path("./data/raw")
    statsbomb_base_url: str | None = None
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)

    broker_url: str = "redis://localhost:6379/0"
    job_queue_name: str = Field(default="football-jobs", min_length=1, max_length=64)
    worker_concurrency: int = Field(default=2, ge=1, le=64)
    # 0 disables the worker's metrics/health endpoint.
    worker_metrics_port: int = Field(default=9100, ge=0, le=65535)
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    # Seconds to wait before attempt 2, 3, ...; the last value repeats if attempts outrun the list.
    job_retry_delays_seconds: str = "5,30,120"
    stale_job_minutes: int = Field(default=15, ge=1)

    @property
    def retry_delays(self) -> tuple[int, ...]:
        return tuple(int(part) for part in self.job_retry_delays_seconds.split(","))

    @field_validator("broker_url")
    @classmethod
    def _validate_broker_url(cls, value: str) -> str:
        if not value.startswith(_SUPPORTED_BROKER_SCHEMES):
            raise ValueError(
                f"BROKER_URL must be a Redis URL starting with one of "
                f"{', '.join(_SUPPORTED_BROKER_SCHEMES)}"
            )
        return value

    @field_validator("job_retry_delays_seconds")
    @classmethod
    def _validate_retry_delays(cls, value: str) -> str:
        try:
            delays = [int(part) for part in value.split(",")]
        except ValueError as exc:
            raise ValueError("JOB_RETRY_DELAYS_SECONDS must be comma-separated integers") from exc
        if not delays or any(delay < 0 for delay in delays):
            raise ValueError("JOB_RETRY_DELAYS_SECONDS must hold non-negative integers")
        return value

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value.startswith(_SUPPORTED_DB_SCHEMES):
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL URL "
                f"starting with one of {', '.join(_SUPPORTED_DB_SCHEMES)}"
            )
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


def load_settings(**overrides: object) -> Settings:
    """Build :class:`Settings`, raising :class:`ConfigurationError` on bad input."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except PydanticValidationError as exc:
        raise ConfigurationError(f"Invalid application configuration: {exc}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return load_settings()

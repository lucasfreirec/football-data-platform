"""Request and response models for the job endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.schemas.common import ORM_CONFIG
from app.db.models import JobStatus, JobType
from app.jobs.paths import validate_source_reference


class JobCreate(BaseModel):
    """A request to process one match."""

    model_config = ConfigDict(extra="forbid")

    job_type: JobType = Field(description="`ingest_match` or `compute_match_statistics`.")
    match_id: int = Field(gt=0, description="StatsBomb match identifier.")
    source_path: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Ingest only. A directory relative to the configured source directory. "
            "Defaults to the source directory itself."
        ),
    )
    competition: str | None = Field(
        default=None,
        max_length=128,
        description="Ingest only. Competition name; defaults to the configured source scope.",
    )
    season: str | None = Field(
        default=None,
        max_length=32,
        description="Ingest only. Season name; defaults to the configured source scope.",
    )

    @field_validator("source_path")
    @classmethod
    def _relative_path_only(cls, value: str | None) -> str | None:
        return None if value is None else validate_source_reference(value)

    @model_validator(mode="after")
    def _ingest_only_fields(self) -> JobCreate:
        if self.job_type is JobType.compute_match_statistics and (
            self.source_path or self.competition or self.season
        ):
            raise ValueError("source_path, competition and season apply only to ingest_match jobs")
        return self


class JobRead(BaseModel):
    """A processing job. Broker and filesystem details are never included."""

    model_config = ORM_CONFIG

    id: uuid.UUID = Field(description="Public job identifier.")
    job_type: JobType
    status: JobStatus
    match_id: int | None = Field(
        default=None,
        validation_alias="statsbomb_match_id",
        description="StatsBomb match identifier.",
    )
    source_path: str | None = None
    payload: dict[str, Any]
    result: dict[str, Any] | None = Field(
        default=None, description="Present once the job has completed."
    )
    error_message: str | None = Field(
        default=None, description="A short, safe summary of the most recent failure."
    )
    attempt_count: int
    max_attempts: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime

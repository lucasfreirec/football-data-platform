"""Shared response building blocks.

Every public identifier is the stable StatsBomb external ID, never the internal
primary key, so ``id`` is read from ``statsbomb_id`` on the ORM object.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

ORM_CONFIG = ConfigDict(from_attributes=True, populate_by_name=True)


class ExternalIdModel(BaseModel):
    """Base for resources keyed by a StatsBomb identifier."""

    model_config = ORM_CONFIG

    id: int = Field(validation_alias="statsbomb_id", description="StatsBomb external identifier.")


class Page[ItemT](BaseModel):
    """Offset-paginated collection envelope."""

    items: list[ItemT]
    limit: int = Field(description="Maximum number of items returned.")
    offset: int = Field(description="Number of items skipped.")
    total: int = Field(description="Total number of items matching the query.")


class ErrorResponse(BaseModel):
    """Error body returned for every handled failure."""

    detail: str


class HealthResponse(BaseModel):
    """Liveness and database reachability."""

    status: str
    database: str

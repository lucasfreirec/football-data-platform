"""Shared API dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


@dataclass(frozen=True, slots=True)
class Pagination:
    limit: int
    offset: int


def get_pagination(
    limit: Annotated[
        int, Query(ge=1, le=MAX_LIMIT, description="Maximum number of items to return.")
    ] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0, description="Number of items to skip.")] = 0,
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


SessionDep = Annotated[Session, Depends(get_db)]
PaginationDep = Annotated[Pagination, Depends(get_pagination)]

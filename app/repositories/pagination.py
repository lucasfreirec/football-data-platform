"""Offset pagination shared by the read-only query modules."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


def _count(session: Session, statement: Select[Any]) -> int:
    return session.scalar(select(func.count()).select_from(statement.subquery())) or 0


def paginate(
    session: Session, statement: Select[Any], *, limit: int, offset: int
) -> tuple[list[Any], int]:
    """Return one page of ORM entities plus the total number of matching rows."""
    total = _count(session, statement)
    items = list(session.scalars(statement.limit(limit).offset(offset)).unique())
    return items, total


def paginate_rows(
    session: Session, statement: Select[Any], *, limit: int, offset: int
) -> tuple[list[Any], int]:
    """Like :func:`paginate`, for statements that select columns instead of entities."""
    total = _count(session, statement)
    rows = list(session.execute(statement.limit(limit).offset(offset)).all())
    return rows, total

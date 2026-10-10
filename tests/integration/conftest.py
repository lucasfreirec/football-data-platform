"""Shared fixtures for the PostgreSQL integration tests.

All of them are skipped unless ``TEST_DATABASE_URL`` points at a reachable
database. The schema is dropped and recreated, so point it at a scratch
database, never at one holding data you care about.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.db.models import Base
from app.db.session import get_db
from app.main import create_app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _connect() -> Engine:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - surfaced as a skip, not a failure
        engine.dispose()
        pytest.skip(f"PostgreSQL is not reachable: {exc}")
    return engine


def reset_schema(engine: Engine) -> None:
    """Remove every table, including the one alembic keeps, so each module starts clean."""
    Base.metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[Engine]:
    engine = _connect()
    reset_schema(engine)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        reset_schema(engine)
        engine.dispose()


@pytest.fixture
def pg_session(pg_engine: Engine) -> Iterator[Session]:
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with pg_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with Session(pg_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def pg_client(pg_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from app.api.routes import health as health_route

    monkeypatch.setattr(health_route, "check_database_connection", lambda: True)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: pg_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()

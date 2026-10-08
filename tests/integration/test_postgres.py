"""Integration tests that require a live PostgreSQL database.

These are skipped unless ``TEST_DATABASE_URL`` is set, for example::

    docker compose up -d db
    TEST_DATABASE_URL=postgresql+psycopg://football:football@localhost:5432/football_test \
        uv run pytest -m integration

The target database is created and dropped around the test run, so point it at
a scratch database, never at one holding data you care about.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session

from app.db.models import Base, Competition, Event, Lineup, Match, Player, Season, Team
from app.ingestion.loader import SourceLoader
from app.ingestion.service import IngestionService

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[Engine]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - surfaced as a skip, not a failure
        engine.dispose()
        pytest.skip(f"PostgreSQL is not reachable: {exc}")

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def pg_session(pg_engine: Engine) -> Iterator[Session]:
    with Session(pg_engine, expire_on_commit=False) as session:
        for model in (Event, Lineup, Match, Season, Competition, Player, Team):
            session.query(model).delete()
        session.commit()
        yield session


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_ingestion_round_trip_on_postgres(pg_session: Session, raw_source_dir: Path) -> None:
    summary = IngestionService(SourceLoader(raw_source_dir), pg_session).run()
    pg_session.commit()

    assert summary.matches_processed == 2
    assert _count(pg_session, Match) == 2
    assert _count(pg_session, Event) == 5
    assert _count(pg_session, Lineup) == 6


def test_jsonb_round_trips_on_postgres(pg_session: Session, raw_source_dir: Path) -> None:
    IngestionService(SourceLoader(raw_source_dir), pg_session).run()
    pg_session.commit()

    event = pg_session.scalar(select(Event).where(Event.type_name == "Shot"))
    assert event is not None
    assert event.raw_data is not None
    assert event.raw_data["detail"]["statsbomb_xg"] == 0.42


def test_reimport_is_idempotent_on_postgres(pg_session: Session, raw_source_dir: Path) -> None:
    for _ in range(2):
        IngestionService(SourceLoader(raw_source_dir), pg_session).run()
        pg_session.commit()

    assert _count(pg_session, Match) == 2
    assert _count(pg_session, Event) == 5
    assert _count(pg_session, Lineup) == 6
    assert _count(pg_session, Competition) == 1

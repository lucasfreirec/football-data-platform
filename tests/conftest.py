import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Base

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RAW_SOURCE_DIR = FIXTURES_DIR / "raw"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Keep the settings singleton from leaking between tests."""
    get_settings.cache_clear()


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory database with the full schema, so unit tests need no Docker."""
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def raw_source_dir() -> Path:
    return RAW_SOURCE_DIR


@pytest.fixture
def raw_competitions() -> list[dict[str, Any]]:
    return _load(RAW_SOURCE_DIR / "competitions.json")


@pytest.fixture
def raw_matches() -> list[dict[str, Any]]:
    return _load(RAW_SOURCE_DIR / "matches" / "16" / "4.json")


@pytest.fixture
def raw_events() -> list[dict[str, Any]]:
    return _load(RAW_SOURCE_DIR / "events" / "7298.json")


@pytest.fixture
def raw_lineups() -> list[dict[str, Any]]:
    return _load(RAW_SOURCE_DIR / "lineups" / "7298.json")


def _load(path: Path) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return data

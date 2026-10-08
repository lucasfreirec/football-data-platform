from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_pagination  # noqa: F401  (imported for clarity)
from app.db.session import get_db
from app.ingestion.loader import SourceLoader
from app.ingestion.service import IngestionService
from app.main import create_app


@pytest.fixture
def api(session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """App wired to the in-memory database, with health reporting it as reachable."""
    from app.api.routes import health as health_route

    monkeypatch.setattr(health_route, "check_database_connection", lambda: True)

    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(api: FastAPI) -> Iterator[TestClient]:
    with TestClient(api) as client:
        yield client


@pytest.fixture
def seeded_client(client: TestClient, session: Session, raw_source_dir) -> TestClient:  # type: ignore[no-untyped-def]
    IngestionService(SourceLoader(raw_source_dir), session).run()
    session.commit()
    return client

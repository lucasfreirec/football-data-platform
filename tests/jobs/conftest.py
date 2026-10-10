import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.jobs import get_job_queue
from app.db.session import get_db
from app.main import create_app


class FakeQueue:
    """Records what would have been queued; can simulate an unavailable broker."""

    def __init__(self) -> None:
        self.sent: list[tuple[uuid.UUID, int]] = []
        self.fail_with: Exception | None = None

    def enqueue(self, job_id: uuid.UUID, *, countdown: int = 0) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((job_id, countdown))
        return f"task-{len(self.sent)}"

    @property
    def job_ids(self) -> list[uuid.UUID]:
        return [job_id for job_id, _ in self.sent]


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def jobs_api(
    session: Session, queue: FakeQueue, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FastAPI]:
    from app.api.routes import health as health_route

    monkeypatch.setattr(health_route, "check_database_connection", lambda: True)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_job_queue] = lambda: queue
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def jobs_client(jobs_api: FastAPI) -> Iterator[TestClient]:
    with TestClient(jobs_api, raise_server_exceptions=False) as client:
        yield client

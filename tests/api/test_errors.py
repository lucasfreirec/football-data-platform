import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.routes import competitions as competitions_route
from app.repositories import queries


def test_unexpected_error_returns_a_generic_500(
    api: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("SELECT secret_column FROM competitions", {}, Exception("pg: boom"))

    monkeypatch.setattr(competitions_route.queries, "list_competitions", _boom)

    with TestClient(api, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/competitions")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}


def test_database_details_are_not_leaked_to_clients(
    api: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("password=hunter2 authentication failed"))

    monkeypatch.setattr(competitions_route.queries, "list_competitions", _boom)

    with TestClient(api, raise_server_exceptions=False) as client:
        body = client.get("/api/v1/competitions").text

    assert "hunter2" not in body
    assert "authentication failed" not in body


def test_queries_module_is_shared_by_routes() -> None:
    assert competitions_route.queries is queries

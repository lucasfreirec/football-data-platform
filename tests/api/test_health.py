import pytest
from fastapi.testclient import TestClient

from app.api.routes import health as health_route
from app.main import create_app


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_unreachable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_route, "check_database_connection", lambda: False)

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unreachable"}


def test_openapi_documents_the_api(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == "Football Data Platform"
    assert "/api/v1/competitions" in schema["paths"]
    assert "/api/v1/matches/{match_id}/events" in schema["paths"]
    assert schema["paths"]["/api/v1/teams/{team_id}"]["get"]["summary"] == "Get a team"


def test_docs_are_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200

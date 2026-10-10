import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from app import __version__


def test_the_api_reports_the_package_version(client: TestClient) -> None:
    assert client.get("/openapi.json").json()["info"]["version"] == __version__


def test_the_package_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["version"]

    assert declared == __version__

"""Liveness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.schemas.common import HealthResponse
from app.db.session import check_database_connection

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description="Reports whether the application is running and the database is reachable.",
    responses={503: {"description": "The database is not reachable."}},
)
def health(response: Response) -> HealthResponse:
    database_ok = check_database_connection()
    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if database_ok else "degraded",
        database="ok" if database_ok else "unreachable",
    )

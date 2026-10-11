"""FastAPI application factory and entry point."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import (
    analytics,
    competitions,
    event_details,
    health,
    jobs,
    matches,
    players,
    teams,
)
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.observability import api as observability

API_PREFIX = "/api/v1"

logger = get_logger(__name__)

DESCRIPTION = """
Read access to StatsBomb Open Data imported for the UEFA Champions League
2017/18 season, plus `/jobs` endpoints to submit and track background processing.

Resources are addressed by their stable StatsBomb identifiers. Collection
endpoints are offset-paginated and return an `items` / `limit` / `offset` /
`total` envelope. Football data is read-only; only jobs can be created.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Football Data Platform",
        description=DESCRIPTION,
        version=__version__,
    )

    observability.install(app, API_PREFIX)
    app.include_router(health.router)
    for router in (
        competitions.router,
        teams.router,
        players.router,
        matches.router,
        event_details.router,
        analytics.router,
        jobs.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        """Keep database and stack details server-side."""
        logger.exception("Unhandled API error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()

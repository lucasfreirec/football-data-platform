"""Request metrics and the ``/metrics`` endpoint for the API."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, FastAPI, Request, Response

from app.core.config import get_settings
from app.observability.metrics import (
    HTTP_DURATION,
    HTTP_REQUESTS,
    METRICS_CONTENT_TYPE,
    render_metrics,
)

METRICS_PATH = "/metrics"
UNMATCHED_ROUTE = "unmatched"

router = APIRouter()


@router.get(METRICS_PATH, include_in_schema=False)
def metrics() -> Response:
    return Response(render_metrics(get_settings()), media_type=METRICS_CONTENT_TYPE)


def route_label(request: Request, api_prefix: str = "") -> str:
    """The route template (``/api/v1/jobs/{job_id}``), so ids and 404 probes cannot add series.

    The router reports a template without its include prefix, so it is added back for
    requests that arrived under it.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not template:
        return UNMATCHED_ROUTE
    arrived_under_prefix = api_prefix and request.url.path.startswith(f"{api_prefix}/")
    if arrived_under_prefix and not template.startswith(api_prefix):
        return f"{api_prefix}{template}"
    return str(template)


def install(app: FastAPI, api_prefix: str = "") -> None:
    @app.middleware("http")
    async def record_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route = route_label(request, api_prefix)
            if route != METRICS_PATH:
                HTTP_REQUESTS.labels(request.method, route, str(status_code)).inc()
                HTTP_DURATION.labels(request.method, route).observe(time.perf_counter() - started)

    app.include_router(router)

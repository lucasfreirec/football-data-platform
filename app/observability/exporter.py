"""A small HTTP server in the worker's main process: ``/metrics`` and ``/healthz``.

Celery workers have no HTTP interface of their own. This serves Prometheus scrapes and
gives an orchestrator a liveness endpoint that does not depend on the broker or database
(a broker outage must not make the platform restart every worker).
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from app.core.config import Settings
from app.core.logging import get_logger
from app.observability.metrics import METRICS_CONTENT_TYPE, render_metrics

logger = get_logger(__name__)


class ExporterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], settings: Settings) -> None:
        super().__init__(address, _Handler)
        self.settings = settings


class _Handler(BaseHTTPRequestHandler):
    server: ExporterServer

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._reply(200, b"ok\n", "text/plain; charset=utf-8")
        elif path == "/metrics":
            try:
                body = render_metrics(self.server.settings)
            except Exception as exc:
                logger.error("metrics_render_failed error=%r", exc)
                self._reply(500, b"metrics unavailable\n", "text/plain; charset=utf-8")
            else:
                self._reply(200, body, METRICS_CONTENT_TYPE)
        else:
            self._reply(404, b"not found\n", "text/plain; charset=utf-8")

    def _reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Scrapes every few seconds would drown the job logs."""


def start_exporter(settings: Settings, host: str = "0.0.0.0") -> ExporterServer | None:
    """Serve in a background thread; ``None`` if disabled (port 0) or the port is taken."""
    port = settings.worker_metrics_port
    if port == 0:
        logger.info("Worker metrics exporter disabled (WORKER_METRICS_PORT=0)")
        return None
    try:
        server = ExporterServer((host, port), settings)
    except OSError as exc:
        # Several workers on one host share a port; losing metrics must not stop the work.
        logger.warning("Worker metrics exporter not started on port %d: %s", port, exc)
        return None
    Thread(target=server.serve_forever, name="metrics-exporter", daemon=True).start()
    logger.info("Worker metrics exporter listening on %s:%d", host, port)
    return server

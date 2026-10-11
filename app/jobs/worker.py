"""Worker entry point: ``python -m app.jobs.worker``.

Waits (bounded) for the database and broker, then runs a Celery worker whose slot
count comes from ``WORKER_CONCURRENCY``. If the dependencies never appear it exits
non-zero, so the orchestrator restarts it with backoff instead of the process
spinning forever.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import check_database_connection
from app.jobs.celery_app import celery_app

logger = get_logger(__name__)

WAIT_INTERVAL_SECONDS = 2.0


def broker_available() -> bool:
    try:
        with celery_app.connection_for_read() as connection:
            connection.ensure_connection(max_retries=1, timeout=3)
        return True
    except Exception:
        return False


def wait_for_dependencies(timeout_seconds: float) -> bool:
    """Poll the database and broker until both answer or the timeout passes."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        database, broker = check_database_connection(), broker_available()
        if database and broker:
            return True
        if time.monotonic() >= deadline:
            logger.error(
                "Dependencies unavailable after %.0fs (database=%s broker=%s)",
                timeout_seconds,
                database,
                broker,
            )
            return False
        logger.warning("Waiting for dependencies (database=%s broker=%s)", database, broker)
        time.sleep(WAIT_INTERVAL_SECONDS)


def build_arguments(concurrency: int, queue: str, loglevel: str, hostname: str | None) -> list[str]:
    arguments = [
        "worker",
        f"--concurrency={concurrency}",
        "--pool=prefork",
        f"--queues={queue}",
        f"--loglevel={loglevel}",
        "-Ofair",
        "--without-gossip",
        "--without-mingle",
    ]
    if hostname:
        arguments.append(f"--hostname={hostname}")
    return arguments


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "darwin":
        # macOS starts pool processes by spawning, which leaves Celery's per-child task
        # state uninitialised and fails the first task. Linux, where the containers run,
        # forks and does not need this.
        os.environ.setdefault("FORKED_BY_MULTIPROCESSING", "1")

    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run a football job worker.")
    parser.add_argument("--concurrency", type=int, default=settings.worker_concurrency)
    parser.add_argument("--wait-seconds", type=float, default=60.0)
    parser.add_argument("--hostname", default=None)
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    configure_logging(settings.log_level)
    if not wait_for_dependencies(args.wait_seconds):
        return 1

    logger.info(
        "Starting worker with %d slot(s) on queue %s", args.concurrency, settings.job_queue_name
    )
    celery_app.worker_main(
        build_arguments(
            args.concurrency, settings.job_queue_name, settings.log_level.lower(), args.hostname
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

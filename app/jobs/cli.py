"""Operations commands for jobs.

python -m app.jobs.cli reconcile-stale --older-than-minutes 15
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, NoReturn

import typer

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError
from app.core.logging import configure_logging, get_logger
from app.db.session import check_database_connection, get_session_factory
from app.jobs.queue import CeleryJobQueue
from app.jobs.reconcile import reconcile_stale

EXIT_CONFIGURATION = 3
EXIT_DATABASE = 5
EXIT_REQUEUE_FAILED = 6

logger = get_logger(__name__)
app = typer.Typer(help="Operate on background processing jobs.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Operate on background processing jobs."""


@app.command("reconcile-stale")
def reconcile_stale_command(
    older_than_minutes: Annotated[
        int | None,
        typer.Option(
            "--older-than-minutes",
            min=1,
            help="Age that makes a job stale. Defaults to STALE_JOB_MINUTES.",
        ),
    ] = None,
    skip_queued: Annotated[
        bool,
        typer.Option(
            "--skip-queued", help="Only recover running jobs; leave pending/retrying ones alone."
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would change without changing it.")
    ] = False,
) -> None:
    """Re-queue or fail jobs that a lost worker or a lost queue message left behind."""
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        _fail(EXIT_CONFIGURATION, str(exc))
    configure_logging(settings.log_level)

    if not check_database_connection():
        _fail(EXIT_DATABASE, "Cannot reach the database.")

    minutes = older_than_minutes or settings.stale_job_minutes
    report = reconcile_stale(
        get_session_factory(),
        CeleryJobQueue(),
        older_than=timedelta(minutes=minutes),
        include_queued=not skip_queued,
        dry_run=dry_run,
    )

    typer.echo(
        f"Jobs not progressing for over {minutes} minute(s){' (dry run)' if dry_run else ''}"
    )
    for line in report.lines(dry_run=dry_run):
        typer.echo(f"  {line}")
    if report.enqueue_failures:
        _fail(EXIT_REQUEUE_FAILED, f"{report.enqueue_failures} job(s) could not be queued.")


def _fail(code: int, message: str) -> NoReturn:
    logger.error(message)
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code)


if __name__ == "__main__":
    app()

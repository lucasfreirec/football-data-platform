"""Command-line entry point for StatsBomb ingestion.

Usage::

    python -m app.ingestion.cli ingest --source-dir ./data/raw
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from app.core.config import Settings, get_settings
from app.core.exceptions import ConfigurationError, PersistenceError, SourceError
from app.core.logging import configure_logging, get_logger
from app.db.session import check_database_connection, session_scope
from app.ingestion.loader import SourceLoader
from app.ingestion.service import (
    DEFAULT_COMPETITION_NAME,
    DEFAULT_SEASON_NAME,
    IngestionService,
    IngestionSummary,
)

EXIT_CONFIGURATION = 3
EXIT_SOURCE = 4
EXIT_DATABASE = 5

logger = get_logger(__name__)
app = typer.Typer(help="Import StatsBomb Open Data into PostgreSQL.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Import StatsBomb Open Data into PostgreSQL."""


@app.command()
def ingest(
    source_dir: Annotated[
        Path | None,
        typer.Option(
            "--source-dir",
            help="Directory holding StatsBomb JSON files. Defaults to STATSBOMB_SOURCE_DIR.",
        ),
    ] = None,
    competition: Annotated[
        str, typer.Option("--competition", help="Competition name to import.")
    ] = DEFAULT_COMPETITION_NAME,
    season: Annotated[
        str, typer.Option("--season", help="Season name to import.")
    ] = DEFAULT_SEASON_NAME,
) -> None:
    """Import one competition season and print a summary of what was written."""
    settings = _load_settings()
    configure_logging(settings.log_level)

    if not check_database_connection():
        _fail(
            EXIT_DATABASE,
            f"Cannot reach the database at {_safe_url(settings.database_url)}. "
            "Is PostgreSQL running and migrated?",
        )

    loader = SourceLoader(source_dir or settings.statsbomb_source_dir)
    try:
        with session_scope() as session:
            service = IngestionService(
                loader, session, competition_name=competition, season_name=season
            )
            summary = service.run()
    except SourceError as exc:
        _fail(EXIT_SOURCE, str(exc))
    except PersistenceError as exc:
        _fail(EXIT_DATABASE, str(exc))

    _print_summary(summary)


def _load_settings() -> Settings:
    try:
        return get_settings()
    except ConfigurationError as exc:
        _fail(EXIT_CONFIGURATION, str(exc))


def _print_summary(summary: IngestionSummary) -> None:
    for line in summary.as_lines():
        typer.echo(line)


def _fail(code: int, message: str) -> NoReturn:
    logger.error(message)
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code)


def _safe_url(database_url: str) -> str:
    """Strip credentials before showing a database URL to the user."""
    scheme, _, remainder = database_url.partition("://")
    _, _, host = remainder.rpartition("@")
    return f"{scheme}://{host}" if scheme else database_url


if __name__ == "__main__":
    app()

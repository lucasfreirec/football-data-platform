"""Ingestion orchestration: source -> normalize -> validate -> persist.

Invalid individual events or lineup entries are skipped and reported; a file or
command only fails when its structure or required context is invalid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import SourceError, ValidationError
from app.core.logging import get_logger
from app.db.models import Match
from app.ingestion.loader import SourceLoader
from app.ingestion.normalizers import (
    NormalizedCompetitionSeason,
    NormalizedEvent,
    NormalizedLineupEntry,
    NormalizedMatch,
    normalize_competition_season,
    normalize_event,
    normalize_lineup_block,
    normalize_lineup_entry,
    normalize_match,
)
from app.ingestion.persistence import IngestionRepository

DEFAULT_COMPETITION_NAME = "Champions League"
DEFAULT_SEASON_NAME = "2017/2018"

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RejectedRecord:
    """A single source record that could not be imported."""

    source_file: str
    reason: str
    match_external_id: int | None = None
    record_external_id: str | None = None

    def __str__(self) -> str:
        location = self.source_file
        if self.match_external_id is not None:
            location = f"{location} (match {self.match_external_id})"
        if self.record_external_id is not None:
            location = f"{location} record {self.record_external_id}"
        return f"{location}: {self.reason}"


@dataclass
class IngestionSummary:
    """Counts reported at the end of an import."""

    matches_processed: int = 0
    matches_skipped: int = 0
    events_imported: int = 0
    events_skipped: int = 0
    lineups_imported: int = 0
    lineups_skipped: int = 0
    rejected: list[RejectedRecord] = field(default_factory=list)

    @property
    def validation_failures(self) -> int:
        return len(self.rejected)

    def reject(self, record: RejectedRecord) -> None:
        self.rejected.append(record)
        logger.warning("Rejected record | %s", record)

    def as_lines(self) -> list[str]:
        lines = [
            "Ingestion summary",
            f"  matches processed : {self.matches_processed}",
            f"  matches skipped   : {self.matches_skipped}",
            f"  events imported   : {self.events_imported}",
            f"  events skipped    : {self.events_skipped}",
            f"  lineups imported  : {self.lineups_imported}",
            f"  lineups skipped   : {self.lineups_skipped}",
            f"  validation failures: {self.validation_failures}",
        ]
        lines.extend(f"    - {record}" for record in self.rejected)
        return lines


class IngestionService:
    """Imports one competition season from a local StatsBomb source directory."""

    def __init__(
        self,
        loader: SourceLoader,
        session: Session,
        *,
        competition_name: str = DEFAULT_COMPETITION_NAME,
        season_name: str = DEFAULT_SEASON_NAME,
    ) -> None:
        self.loader = loader
        self.session = session
        self.repository = IngestionRepository(session)
        self.competition_name = competition_name
        self.season_name = season_name

    def run(self) -> IngestionSummary:
        summary = IngestionSummary()
        self.loader.ensure_available()

        target = self._find_target_season()
        competition_id = target.competition.statsbomb_id
        season_id = target.season.statsbomb_id
        logger.info(
            "Importing %s %s (competition_id=%s, season_id=%s)",
            target.competition.name,
            target.season.name,
            competition_id,
            season_id,
        )

        matches_file = str(self.loader.matches_path(competition_id, season_id))

        # competitions.json carries fields the match records omit, so persist it first.
        competition = self.repository.upsert_competition(target.competition)
        self.repository.upsert_season(competition, target.season)

        for raw_match in self.loader.load_matches(competition_id, season_id):
            match_record = self._normalize_match(raw_match, matches_file, summary)
            if match_record is None:
                continue

            match = self.repository.upsert_match(match_record)
            summary.matches_processed += 1
            self._ingest_lineups(match, summary)
            self._ingest_events(match, summary)
            self.session.flush()

        return summary

    def _find_target_season(self) -> NormalizedCompetitionSeason:
        """Resolve the configured competition/season to StatsBomb identifiers."""
        wanted_competition = self.competition_name.casefold()
        wanted_season = self.season_name.casefold()

        for raw in self.loader.load_competitions():
            try:
                record = normalize_competition_season(raw)
            except ValidationError as exc:
                logger.warning("Skipping malformed competitions.json entry: %s", exc)
                continue
            if (
                record.competition.name.casefold() == wanted_competition
                and record.season.name.casefold() == wanted_season
            ):
                return record

        raise SourceError(
            f"No {self.competition_name} {self.season_name} entry in "
            f"{self.loader.competitions_path}"
        )

    def _normalize_match(
        self, raw: Any, source_file: str, summary: IngestionSummary
    ) -> NormalizedMatch | None:
        try:
            return normalize_match(raw)
        except ValidationError as exc:
            summary.matches_skipped += 1
            summary.reject(
                RejectedRecord(
                    source_file=source_file,
                    reason=str(exc),
                    match_external_id=_maybe_int(raw, "match_id"),
                )
            )
            return None

    def _ingest_lineups(self, match: Match, summary: IngestionSummary) -> None:
        match_id = match.statsbomb_id
        source_file = str(self.loader.lineups_path(match_id))
        if not self.loader.has_lineups(match_id):
            summary.reject(
                RejectedRecord(
                    source_file=source_file,
                    reason="Lineup file not found",
                    match_external_id=match_id,
                )
            )
            return

        entries: list[NormalizedLineupEntry] = []
        seen: set[tuple[int, int]] = set()
        for raw_team in self.loader.load_lineups(match_id):
            try:
                block = normalize_lineup_block(raw_team)
            except ValidationError as exc:
                summary.lineups_skipped += 1
                summary.reject(
                    RejectedRecord(
                        source_file=source_file,
                        reason=str(exc),
                        match_external_id=match_id,
                        record_external_id=_maybe_str(raw_team, "team_id"),
                    )
                )
                continue

            for raw_entry in block.players:
                try:
                    entry = normalize_lineup_entry(block.team, raw_entry)
                except ValidationError as exc:
                    summary.lineups_skipped += 1
                    summary.reject(
                        RejectedRecord(
                            source_file=source_file,
                            reason=str(exc),
                            match_external_id=match_id,
                            record_external_id=_maybe_str(raw_entry, "player_id"),
                        )
                    )
                    continue

                key = (entry.team.statsbomb_id, entry.player.statsbomb_id)
                if key in seen:
                    summary.lineups_skipped += 1
                    summary.reject(
                        RejectedRecord(
                            source_file=source_file,
                            reason="Duplicate player in team squad",
                            match_external_id=match_id,
                            record_external_id=str(entry.player.statsbomb_id),
                        )
                    )
                    continue
                seen.add(key)
                entries.append(entry)

        summary.lineups_imported += self.repository.upsert_lineups(match, entries)

    def _ingest_events(self, match: Match, summary: IngestionSummary) -> None:
        match_id = match.statsbomb_id
        source_file = str(self.loader.events_path(match_id))
        if not self.loader.has_events(match_id):
            summary.reject(
                RejectedRecord(
                    source_file=source_file,
                    reason="Event file not found",
                    match_external_id=match_id,
                )
            )
            return

        records: list[NormalizedEvent] = []
        seen_ids: set[str] = set()
        seen_indexes: set[int] = set()
        for raw_event in self.loader.load_events(match_id):
            try:
                record = normalize_event(raw_event)
            except ValidationError as exc:
                summary.events_skipped += 1
                summary.reject(
                    RejectedRecord(
                        source_file=source_file,
                        reason=str(exc),
                        match_external_id=match_id,
                        record_external_id=_maybe_str(raw_event, "id"),
                    )
                )
                continue

            if record.statsbomb_id in seen_ids or record.index_in_match in seen_indexes:
                summary.events_skipped += 1
                summary.reject(
                    RejectedRecord(
                        source_file=source_file,
                        reason=f"Duplicate event id or index {record.index_in_match}",
                        match_external_id=match_id,
                        record_external_id=record.statsbomb_id,
                    )
                )
                continue

            seen_ids.add(record.statsbomb_id)
            seen_indexes.add(record.index_in_match)
            records.append(record)

        summary.events_imported += self.repository.upsert_events(match, records)


def _maybe_int(raw: Any, key: str) -> int | None:
    value = raw.get(key) if isinstance(raw, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _maybe_str(raw: Any, key: str) -> str | None:
    value = raw.get(key) if isinstance(raw, dict) else None
    return None if value is None else str(value)

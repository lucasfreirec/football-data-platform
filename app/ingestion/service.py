"""Ingestion orchestration: source -> normalize -> validate -> persist.

Invalid individual events or lineup entries are skipped and reported; a file or
command only fails when its structure or required context is invalid. Every run
that resolves its competition and season leaves a ``source_manifests`` row.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import PersistenceError, SourceError, ValidationError
from app.core.logging import get_logger
from app.db.models import Match, SourceManifest
from app.ingestion.loader import SourceLoader
from app.ingestion.normalizers import (
    PASS_TYPE,
    SHOT_TYPE,
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

STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

# Keeps the stored manifest summary small however many records were rejected.
MANIFEST_DETAIL_LIMIT = 50

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


@dataclass(frozen=True, slots=True)
class MissingFile:
    """A source file the match list implies but that is not present.

    Distinct from :class:`RejectedRecord`: the file is unavailable, not malformed.
    """

    source_file: str
    kind: str
    match_external_id: int

    def __str__(self) -> str:
        return f"{self.kind} file for match {self.match_external_id}: {self.source_file}"


@dataclass
class IngestionSummary:
    """Counts and findings reported at the end of an import."""

    matches_listed: int = 0
    matches_processed: int = 0
    matches_skipped: int = 0
    event_files_found: int = 0
    event_files_missing: int = 0
    lineup_files_found: int = 0
    lineup_files_missing: int = 0
    events_imported: int = 0
    events_skipped: int = 0
    passes_imported: int = 0
    passes_skipped: int = 0
    shots_imported: int = 0
    shots_skipped: int = 0
    lineups_imported: int = 0
    lineups_skipped: int = 0
    rejected: list[RejectedRecord] = field(default_factory=list)
    missing_files: list[MissingFile] = field(default_factory=list)
    import_status: str = STATUS_SUCCESS
    failure: str | None = None
    manifest_id: int | None = None

    @property
    def validation_failures(self) -> int:
        return len(self.rejected)

    def reject(self, record: RejectedRecord) -> None:
        self.rejected.append(record)
        logger.warning("Rejected record | %s", record)

    def report_missing(self, missing: MissingFile) -> None:
        self.missing_files.append(missing)
        if missing.kind == "events":
            self.event_files_missing += 1
        else:
            self.lineup_files_missing += 1
        logger.warning("Unavailable source file | %s", missing)

    def settle_status(self) -> str:
        """Success unless a file was unavailable or a record was rejected."""
        self.import_status = (
            STATUS_PARTIAL if self.missing_files or self.rejected else STATUS_SUCCESS
        )
        return self.import_status

    def mark_failed(self, reason: str) -> None:
        """Discard the counts of rolled-back work, keeping only what is still true."""
        listed = self.matches_listed
        fresh = IngestionSummary()
        for item in fields(fresh):
            setattr(self, item.name, getattr(fresh, item.name))
        self.matches_listed = listed
        self.import_status = STATUS_FAILED
        self.failure = reason

    def to_manifest_summary(self) -> dict[str, Any]:
        """Small JSON-safe digest; never contains raw match or event payloads."""
        return {
            "matches_listed": self.matches_listed,
            "matches_processed": self.matches_processed,
            "matches_skipped": self.matches_skipped,
            "event_files_found": self.event_files_found,
            "event_files_missing": self.event_files_missing,
            "lineup_files_found": self.lineup_files_found,
            "lineup_files_missing": self.lineup_files_missing,
            "events_imported": self.events_imported,
            "events_skipped": self.events_skipped,
            "passes_imported": self.passes_imported,
            "passes_skipped": self.passes_skipped,
            "shots_imported": self.shots_imported,
            "shots_skipped": self.shots_skipped,
            "lineups_imported": self.lineups_imported,
            "lineups_skipped": self.lineups_skipped,
            "validation_failures": self.validation_failures,
            "failure": self.failure,
            "missing_files": [str(item) for item in self.missing_files[:MANIFEST_DETAIL_LIMIT]],
            "rejected": [
                {
                    "source_file": record.source_file,
                    "match": record.match_external_id,
                    "record": record.record_external_id,
                    "reason": record.reason,
                }
                for record in self.rejected[:MANIFEST_DETAIL_LIMIT]
            ],
        }

    def as_lines(self) -> list[str]:
        lines = [
            "Ingestion summary",
            f"  matches listed    : {self.matches_listed}",
            f"  matches processed : {self.matches_processed}",
            f"  matches skipped   : {self.matches_skipped}",
            f"  event files       : {self.event_files_found} found / "
            f"{self.event_files_missing} missing",
            f"  lineup files      : {self.lineup_files_found} found / "
            f"{self.lineup_files_missing} missing",
            f"  events imported   : {self.events_imported}",
            f"  events skipped    : {self.events_skipped}",
            f"  passes imported   : {self.passes_imported}",
            f"  passes skipped    : {self.passes_skipped}",
            f"  shots imported    : {self.shots_imported}",
            f"  shots skipped     : {self.shots_skipped}",
            f"  lineups imported  : {self.lineups_imported}",
            f"  lineups skipped   : {self.lineups_skipped}",
            f"  validation failures: {self.validation_failures}",
            f"  import status     : {self.import_status}",
            f"  source manifest id: {self.manifest_id if self.manifest_id is not None else '-'}",
        ]
        if self.failure:
            lines.append(f"  failure           : {self.failure}")
        if self.missing_files:
            lines.append("  unavailable files:")
            lines.extend(f"    - {item}" for item in self.missing_files)
        if self.rejected:
            lines.append("  rejected records:")
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
        # Set once the target season resolves, so callers can report a failed run too.
        self.summary: IngestionSummary | None = None

    def run(self) -> IngestionSummary:
        self.loader.ensure_available()
        target = self._find_target_season()
        logger.info(
            "Importing %s %s (competition_id=%s, season_id=%s)",
            target.competition.name,
            target.season.name,
            target.competition.statsbomb_id,
            target.season.statsbomb_id,
        )

        summary = self.summary = IngestionSummary()
        try:
            self._import(target, summary)
            summary.settle_status()
            self._add_manifest(target, summary)
        except SourceError as exc:
            self._record_failure(target, summary, str(exc))
            raise
        except SQLAlchemyError as exc:
            reason = _first_line(exc)
            self._record_failure(target, summary, reason)
            raise PersistenceError(f"Database write failed: {reason}") from exc
        return summary

    def _import(self, target: NormalizedCompetitionSeason, summary: IngestionSummary) -> None:
        competition_id = target.competition.statsbomb_id
        season_id = target.season.statsbomb_id
        matches_file = str(self.loader.matches_path(competition_id, season_id))

        # competitions.json carries fields the match records omit, so persist it first.
        competition = self.repository.upsert_competition(target.competition)
        self.repository.upsert_season(competition, target.season)

        raw_matches = self.loader.load_matches(competition_id, season_id)
        summary.matches_listed = len(raw_matches)

        for raw_match in raw_matches:
            match_record = self._normalize_match(raw_match, matches_file, summary)
            if match_record is None:
                continue

            match = self.repository.upsert_match(match_record)
            summary.matches_processed += 1
            self._ingest_lineups(match, summary)
            self._ingest_events(match, summary)
            self.session.flush()

    def _add_manifest(
        self, target: NormalizedCompetitionSeason, summary: IngestionSummary
    ) -> SourceManifest:
        competition = self.repository.upsert_competition(target.competition)
        season = self.repository.upsert_season(competition, target.season)
        manifest = SourceManifest(
            competition_id=competition.id,
            season_id=season.id,
            source_path=str(Path(self.loader.source_dir).resolve()),
            source_version=self.loader.source_version(),
            match_count_expected=summary.matches_listed,
            event_file_count_found=summary.event_files_found,
            lineup_file_count_found=summary.lineup_files_found,
            import_status=summary.import_status,
            summary=summary.to_manifest_summary(),
        )
        self.session.add(manifest)
        self.session.flush()
        summary.manifest_id = manifest.id
        return manifest

    def _record_failure(
        self, target: NormalizedCompetitionSeason, summary: IngestionSummary, reason: str
    ) -> None:
        """Roll back the run's writes and keep a ``failed`` manifest for diagnosis.

        The manifest is committed here because the caller's own transaction is about
        to be rolled back by the exception this run re-raises.
        """
        self.session.rollback()
        # Cached ORM objects belong to the rolled-back transaction.
        self.repository = IngestionRepository(self.session)
        summary.mark_failed(reason)
        try:
            self._add_manifest(target, summary)
            self.session.commit()
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise PersistenceError(
                f"Import failed ({reason}) and the failure could not be recorded: "
                f"{_first_line(exc)}"
            ) from exc
        logger.error("Import failed: %s | recorded source manifest %s", reason, summary.manifest_id)

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
            summary.report_missing(MissingFile(source_file, "lineups", match_id))
            return
        summary.lineup_files_found += 1

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
            summary.report_missing(MissingFile(source_file, "events", match_id))
            return
        summary.event_files_found += 1

        records: list[NormalizedEvent] = []
        seen_ids: set[str] = set()
        seen_indexes: set[int] = set()
        for raw_event in self.loader.load_events(match_id):
            try:
                record = normalize_event(raw_event)
            except ValidationError as exc:
                self._count_skipped_event(summary, _raw_event_type(raw_event))
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
                self._count_skipped_event(summary, record.type_name)
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
        summary.passes_imported += sum(1 for record in records if record.pass_detail is not None)
        summary.shots_imported += sum(1 for record in records if record.shot_detail is not None)

    @staticmethod
    def _count_skipped_event(summary: IngestionSummary, type_name: str | None) -> None:
        summary.events_skipped += 1
        if type_name == PASS_TYPE:
            summary.passes_skipped += 1
        elif type_name == SHOT_TYPE:
            summary.shots_skipped += 1


def _raw_event_type(raw: Any) -> str | None:
    type_raw = raw.get("type") if isinstance(raw, dict) else None
    name = type_raw.get("name") if isinstance(type_raw, dict) else None
    return name if isinstance(name, str) else None


def _first_line(exc: Exception) -> str:
    """Short, single-line description that stays safe to store and print."""
    lines = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {lines[0][:300]}" if lines else type(exc).__name__


def _maybe_int(raw: Any, key: str) -> int | None:
    value = raw.get(key) if isinstance(raw, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _maybe_str(raw: Any, key: str) -> str | None:
    value = raw.get(key) if isinstance(raw, dict) else None
    return None if value is None else str(value)

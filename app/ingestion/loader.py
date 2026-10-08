"""Discovery and reading of StatsBomb Open Data JSON files.

Expected layout under the configured source directory::

    competitions.json
    matches/<competition_id>/<season_id>.json
    events/<match_id>.json
    lineups/<match_id>.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.exceptions import SourceError

COMPETITIONS_FILE = "competitions.json"
MATCHES_DIR = "matches"
EVENTS_DIR = "events"
LINEUPS_DIR = "lineups"


class SourceLoader:
    """Reads StatsBomb JSON files from a local directory."""

    def __init__(self, source_dir: Path | str) -> None:
        self.source_dir = Path(source_dir)

    def ensure_available(self) -> None:
        if not self.source_dir.is_dir():
            raise SourceError(f"Source directory does not exist: {self.source_dir}")
        if not self.competitions_path.is_file():
            raise SourceError(f"Missing {COMPETITIONS_FILE} in {self.source_dir}")

    @property
    def competitions_path(self) -> Path:
        return self.source_dir / COMPETITIONS_FILE

    def matches_path(self, competition_id: int, season_id: int) -> Path:
        return self.source_dir / MATCHES_DIR / str(competition_id) / f"{season_id}.json"

    def events_path(self, match_id: int) -> Path:
        return self.source_dir / EVENTS_DIR / f"{match_id}.json"

    def lineups_path(self, match_id: int) -> Path:
        return self.source_dir / LINEUPS_DIR / f"{match_id}.json"

    def load_competitions(self) -> list[dict[str, Any]]:
        return self._read_object_array(self.competitions_path)

    def load_matches(self, competition_id: int, season_id: int) -> list[dict[str, Any]]:
        return self._read_object_array(self.matches_path(competition_id, season_id))

    def load_events(self, match_id: int) -> list[dict[str, Any]]:
        return self._read_object_array(self.events_path(match_id))

    def load_lineups(self, match_id: int) -> list[dict[str, Any]]:
        return self._read_object_array(self.lineups_path(match_id))

    def has_events(self, match_id: int) -> bool:
        return self.events_path(match_id).is_file()

    def has_lineups(self, match_id: int) -> bool:
        return self.lineups_path(match_id).is_file()

    @staticmethod
    def _read_object_array(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            raise SourceError(f"Source file not found: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SourceError(f"Invalid JSON in {path}: {exc}") from exc
        except OSError as exc:
            raise SourceError(f"Could not read {path}: {exc}") from exc

        if not isinstance(payload, list):
            raise SourceError(f"Expected a JSON array in {path}, got {type(payload).__name__}")
        if any(not isinstance(item, dict) for item in payload):
            raise SourceError(f"Expected an array of JSON objects in {path}")
        return payload

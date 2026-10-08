import json
from pathlib import Path

import pytest

from app.core.exceptions import SourceError
from app.ingestion.loader import SourceLoader


def test_loads_every_fixture_file(raw_source_dir: Path) -> None:
    loader = SourceLoader(raw_source_dir)
    loader.ensure_available()

    assert len(loader.load_competitions()) == 2
    assert len(loader.load_matches(16, 4)) == 2
    assert len(loader.load_events(7298)) == 3
    assert len(loader.load_lineups(7298)) == 2


def test_paths_are_derived_not_hard_coded(raw_source_dir: Path) -> None:
    loader = SourceLoader(raw_source_dir)

    assert loader.matches_path(16, 4) == raw_source_dir / "matches" / "16" / "4.json"
    assert loader.events_path(7298) == raw_source_dir / "events" / "7298.json"
    assert loader.lineups_path(7298) == raw_source_dir / "lineups" / "7298.json"


def test_presence_checks(raw_source_dir: Path) -> None:
    loader = SourceLoader(raw_source_dir)

    assert loader.has_events(7298) is True
    assert loader.has_lineups(7298) is True
    assert loader.has_events(9999) is False
    assert loader.has_lineups(9999) is False


def test_missing_source_directory_raises(tmp_path: Path) -> None:
    loader = SourceLoader(tmp_path / "absent")

    with pytest.raises(SourceError, match="Source directory does not exist"):
        loader.ensure_available()


def test_missing_competitions_file_raises(tmp_path: Path) -> None:
    loader = SourceLoader(tmp_path)

    with pytest.raises(SourceError, match="Missing competitions.json"):
        loader.ensure_available()


def test_missing_match_file_raises(raw_source_dir: Path) -> None:
    loader = SourceLoader(raw_source_dir)

    with pytest.raises(SourceError, match="Source file not found"):
        loader.load_matches(999, 999)


def test_malformed_json_raises(tmp_path: Path) -> None:
    (tmp_path / "competitions.json").write_text("{not json", encoding="utf-8")
    loader = SourceLoader(tmp_path)

    with pytest.raises(SourceError, match="Invalid JSON"):
        loader.load_competitions()


def test_non_array_payload_raises(tmp_path: Path) -> None:
    (tmp_path / "competitions.json").write_text(
        json.dumps({"competition_id": 16}), encoding="utf-8"
    )
    loader = SourceLoader(tmp_path)

    with pytest.raises(SourceError, match="Expected a JSON array"):
        loader.load_competitions()


def test_array_of_non_objects_raises(tmp_path: Path) -> None:
    (tmp_path / "competitions.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    loader = SourceLoader(tmp_path)

    with pytest.raises(SourceError, match="array of JSON objects"):
        loader.load_competitions()

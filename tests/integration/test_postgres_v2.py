"""V2 behavior that only a real PostgreSQL can prove.

Skipped unless ``TEST_DATABASE_URL`` is set; see ``conftest.py``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import SourceError
from app.db.models import (
    Event,
    Match,
    PassDetail,
    ShotDetail,
    SourceManifest,
)
from app.ingestion.loader import SourceLoader
from app.ingestion.service import IngestionService

pytestmark = pytest.mark.integration

PLAYERS = "/api/v1/competitions/16/seasons/1/player-stats"
TEAMS = "/api/v1/competitions/16/seasons/1/team-stats"


def _import(source: Path, session: Session, *seasons: str) -> None:
    for season in seasons or ("2017/2018",):
        IngestionService(SourceLoader(source), session, season_name=season).run()
    session.commit()


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture
def seeded(pg_client: TestClient, pg_session: Session, v2_source_dir: Path) -> TestClient:
    _import(v2_source_dir, pg_session, "2017/2018", "2016/2017")
    return pg_client


class TestDetailPersistence:
    def test_details_and_manifest_round_trip(
        self, pg_session: Session, v2_source_dir: Path
    ) -> None:
        _import(v2_source_dir, pg_session)

        assert _count(pg_session, PassDetail) == 8
        assert _count(pg_session, ShotDetail) == 6
        manifest = pg_session.scalar(select(SourceManifest))
        assert manifest is not None
        assert manifest.import_status == "partial"
        assert manifest.imported_at is not None
        assert manifest.imported_at.tzinfo is not None  # timezone-aware, as specified

    def test_manifest_summary_is_stored_as_jsonb(
        self, pg_session: Session, v2_source_dir: Path
    ) -> None:
        _import(v2_source_dir, pg_session)

        column_type = pg_session.scalar(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'source_manifests' AND column_name = 'summary'"
            )
        )
        passes_skipped = pg_session.scalar(
            text("SELECT (summary ->> 'passes_skipped')::int FROM source_manifests")
        )
        assert column_type == "jsonb"
        assert passes_skipped == 2

    def test_reimport_is_idempotent(self, pg_session: Session, v2_source_dir: Path) -> None:
        for _ in range(3):
            _import(v2_source_dir, pg_session)

        assert _count(pg_session, Event) == 19
        assert _count(pg_session, PassDetail) == 8
        assert _count(pg_session, ShotDetail) == 6
        assert _count(pg_session, SourceManifest) == 3

    def test_key_pass_link_is_a_real_foreign_key(
        self, pg_session: Session, v2_source_dir: Path
    ) -> None:
        _import(v2_source_dir, pg_session)

        linked = pg_session.scalar(
            select(func.count())
            .select_from(ShotDetail)
            .join(Event, Event.id == ShotDetail.key_pass_event_id)
            .where(Event.type_name == "Pass")
        )
        assert linked == 2  # one key pass in each of the two matches

    def test_stale_detail_removal_on_postgres(
        self, pg_session: Session, v2_source_dir: Path, tmp_path: Path
    ) -> None:
        import json

        source = tmp_path / "raw"
        shutil.copytree(v2_source_dir, source)
        _import(source, pg_session)

        events_file = source / "events" / "9001.json"
        events = json.loads(events_file.read_text())
        pass_event = next(e for e in events if e["index"] == 3)
        pass_event["type"] = {"id": 43, "name": "Carry"}
        pass_event["carry"] = {"end_location": [60.0, 40.0]}
        del pass_event["pass"]
        events_file.write_text(json.dumps(events))
        _import(source, pg_session)

        assert _count(pg_session, PassDetail) == 7


class TestConstraintsOnPostgres:
    def test_a_second_detail_row_for_an_event_is_rejected(
        self, pg_session: Session, v2_source_dir: Path
    ) -> None:
        _import(v2_source_dir, pg_session)
        event_id = pg_session.scalar(select(PassDetail.event_id))

        pg_session.add(PassDetail(event_id=event_id))
        with pytest.raises(IntegrityError):
            pg_session.flush()
        pg_session.rollback()

    def test_the_status_check_constraint_is_enforced(
        self, pg_session: Session, v2_source_dir: Path
    ) -> None:
        _import(v2_source_dir, pg_session)

        with pytest.raises(IntegrityError):
            pg_session.execute(text("UPDATE source_manifests SET import_status = 'done'"))
        pg_session.rollback()

    def test_deleting_an_event_cascades_to_its_detail(
        self, pg_session: Session, v2_source_dir: Path
    ) -> None:
        _import(v2_source_dir, pg_session)
        event_id = pg_session.scalar(select(ShotDetail.event_id))

        pg_session.execute(text("DELETE FROM events WHERE id = :id"), {"id": event_id})

        assert pg_session.get(ShotDetail, event_id) is None

    def test_deleting_a_key_pass_nulls_the_reference(
        self, pg_session: Session, v2_source_dir: Path
    ) -> None:
        _import(v2_source_dir, pg_session)
        shot = pg_session.scalar(
            select(ShotDetail).where(ShotDetail.key_pass_event_id.is_not(None)).limit(1)
        )
        assert shot is not None
        key_pass_id = shot.key_pass_event_id

        pg_session.execute(text("DELETE FROM events WHERE id = :id"), {"id": key_pass_id})
        pg_session.expire_all()

        assert pg_session.get(ShotDetail, shot.event_id) is not None
        assert pg_session.get(ShotDetail, shot.event_id).key_pass_event_id is None  # type: ignore[union-attr]

    def test_oversized_text_is_rejected_by_the_column_width(self, pg_session: Session) -> None:
        with pytest.raises(DataError):
            pg_session.execute(
                text(
                    "INSERT INTO source_manifests (competition_id, season_id, source_path, "
                    "match_count_expected, event_file_count_found, lineup_file_count_found, "
                    "import_status, summary) VALUES (1, 1, 'p', 0, 0, 0, repeat('x', 40), '{}')"
                )
            )
        pg_session.rollback()


class TestFailedImportsOnPostgres:
    def test_a_failed_run_rolls_back_but_keeps_its_manifest(
        self, pg_session: Session, v2_source_dir: Path, tmp_path: Path
    ) -> None:
        source = tmp_path / "raw"
        shutil.copytree(v2_source_dir, source)
        (source / "events" / "9002.json").write_text("{broken json")

        with pytest.raises(SourceError, match="Invalid JSON"):
            IngestionService(SourceLoader(source), pg_session).run()
        pg_session.rollback()

        assert _count(pg_session, Match) == 0
        assert _count(pg_session, Event) == 0
        manifest = pg_session.scalar(select(SourceManifest))
        assert manifest is not None
        assert manifest.import_status == "failed"
        assert manifest.summary["failure"].startswith("Invalid JSON")

    def test_a_failed_manifest_is_visible_to_another_connection(
        self, pg_session: Session, pg_engine: Engine, v2_source_dir: Path, tmp_path: Path
    ) -> None:
        source = tmp_path / "raw"
        shutil.copytree(v2_source_dir, source)
        (source / "events" / "9002.json").write_text("{broken json")

        with pytest.raises(SourceError):
            IngestionService(SourceLoader(source), pg_session).run()

        with pg_engine.connect() as other:
            status = other.scalar(text("SELECT import_status FROM source_manifests"))
        assert status == "failed"


class TestSchemaOnPostgres:
    def test_the_required_indexes_exist(self, pg_engine: Engine) -> None:
        inspector = inspect(pg_engine)

        def indexed(table: str) -> set[tuple[str, ...]]:
            return {tuple(i["column_names"]) for i in inspector.get_indexes(table)}

        assert ("recipient_player_id",) in indexed("pass_details")
        assert ("outcome_name",) in indexed("pass_details")
        assert ("outcome_name",) in indexed("shot_details")
        assert ("statsbomb_xg",) in indexed("shot_details")

    def test_detail_tables_are_keyed_by_the_event(self, pg_engine: Engine) -> None:
        inspector = inspect(pg_engine)

        for table in ("pass_details", "shot_details"):
            assert inspector.get_pk_constraint(table)["constrained_columns"] == ["event_id"]

    def test_the_detail_event_foreign_key_cascades(self, pg_engine: Engine) -> None:
        inspector = inspect(pg_engine)

        for table in ("pass_details", "shot_details"):
            foreign_key = next(
                fk
                for fk in inspector.get_foreign_keys(table)
                if fk["constrained_columns"] == ["event_id"]
            )
            assert foreign_key["options"]["ondelete"] == "CASCADE"

    def test_filtering_by_xg_can_use_its_index(
        self, pg_session: Session, v2_source_dir: Path
    ) -> None:
        _import(v2_source_dir, pg_session)
        pg_session.execute(text("ANALYZE shot_details"))
        pg_session.execute(text("SET enable_seqscan = off"))

        plan = "\n".join(
            row[0]
            for row in pg_session.execute(
                text("EXPLAIN SELECT * FROM shot_details WHERE statsbomb_xg > 0.2")
            )
        )
        pg_session.rollback()

        assert "ix_shot_details_statsbomb_xg" in plan


class TestAggregatesOnPostgres:
    def test_player_stats_match_the_hand_computed_values(self, seeded: TestClient) -> None:
        dan = seeded.get(
            "/api/v1/players/2001/stats", params={"competition_id": 16, "season_id": 1}
        ).json()

        assert (dan["appearances"], dan["passes_attempted"], dan["passes_completed"]) == (2, 3, 3)
        assert dan["pass_completion_pct"] == pytest.approx(100.0)
        assert (dan["shots"], dan["goals"]) == (2, 1)
        assert dan["xg"] == pytest.approx(0.5)
        assert dan["xg_per_shot"] == pytest.approx(0.25)

    def test_ratios_are_floats_not_decimals_and_zero_division_is_null(
        self, seeded: TestClient
    ) -> None:
        gus = seeded.get(
            "/api/v1/players/2003/stats", params={"competition_id": 16, "season_id": 1}
        ).json()
        ana = seeded.get(
            "/api/v1/players/1001/stats", params={"competition_id": 16, "season_id": 1}
        ).json()

        assert gus["pass_completion_pct"] is None
        assert gus["xg_per_shot"] is None
        assert isinstance(ana["pass_completion_pct"], float)
        assert isinstance(ana["xg"], float)

    def test_season_scoping_holds(self, seeded: TestClient) -> None:
        season1 = seeded.get(
            "/api/v1/players/1001/stats", params={"competition_id": 16, "season_id": 1}
        ).json()
        season2 = seeded.get(
            "/api/v1/players/1001/stats", params={"competition_id": 16, "season_id": 2}
        ).json()

        assert season1["xg"] == pytest.approx(0.25)
        assert season2["xg"] == pytest.approx(0.9)

    @pytest.mark.parametrize("order", ["asc", "desc"])
    def test_null_percentages_sort_last_on_postgres(self, seeded: TestClient, order: str) -> None:
        items = seeded.get(
            PLAYERS, params={"sort_by": "pass_completion_pct", "order": order}
        ).json()["items"]

        assert items[-1]["player_id"] == 2003
        assert items[-1]["pass_completion_pct"] is None

    @pytest.mark.parametrize(
        ("sort_by", "order", "expected"),
        [
            ("xg", "desc", [2001, 2002, 1001, 1002, 1003, 2003]),
            ("goals", "desc", [1001, 2001, 1002, 1003, 2002, 2003]),
            ("shots", "asc", [2003, 1001, 1002, 1003, 2002, 2001]),
            ("pass_completion_pct", "desc", [1002, 2001, 1001, 1003, 2002, 2003]),
        ],
    )
    def test_orderings_agree_with_sqlite(
        self, seeded: TestClient, sort_by: str, order: str, expected: list[int]
    ) -> None:
        items = seeded.get(PLAYERS, params={"sort_by": sort_by, "order": order}).json()["items"]

        assert [item["player_id"] for item in items] == expected

    def test_pages_partition_the_leaderboard(self, seeded: TestClient) -> None:
        full = [
            i["player_id"] for i in seeded.get(PLAYERS, params={"sort_by": "goals"}).json()["items"]
        ]
        paged: list[int] = []
        for offset in (0, 2, 4):
            page = seeded.get(
                PLAYERS, params={"sort_by": "goals", "limit": 2, "offset": offset}
            ).json()
            paged += [i["player_id"] for i in page["items"]]

        assert paged == full
        assert len(set(paged)) == len(full)

    def test_team_leaderboard(self, seeded: TestClient) -> None:
        items = seeded.get(TEAMS, params={"sort_by": "xg"}).json()["items"]

        assert [i["team_id"] for i in items] == [102, 101]
        assert items[0]["xg"] == pytest.approx(1.0)
        assert items[0]["pass_completion_pct"] == pytest.approx(75.0)
        assert items[1]["xg_per_shot"] == pytest.approx(0.4 / 3)

    def test_leaderboard_rows_equal_the_single_resource_rows(self, seeded: TestClient) -> None:
        for item in seeded.get(PLAYERS).json()["items"]:
            single = seeded.get(
                f"/api/v1/players/{item['player_id']}/stats",
                params={"competition_id": 16, "season_id": 1},
            ).json()
            assert item == single

    def test_event_detail_endpoints_work_on_postgres(self, seeded: TestClient) -> None:
        passes = seeded.get("/api/v1/matches/9001/passes").json()
        shots = seeded.get("/api/v1/matches/9001/shots", params={"outcome_name": "Goal"}).json()
        detail = seeded.get(
            "/api/v1/matches/9001/events/90010000-0000-4000-8000-000000000006"
        ).json()

        assert passes["total"] == 4
        assert shots["total"] == 1
        assert detail["shot"]["key_pass_id"] == "90010000-0000-4000-8000-000000000005"
        assert detail["pass"] is None
        assert "raw_data" not in detail

    def test_the_statement_count_is_independent_of_page_size(
        self, seeded: TestClient, pg_engine: Engine
    ) -> None:
        from sqlalchemy import event

        def count(limit: int) -> int:
            seen: list[str] = []

            def record(_c: Any, _cur: Any, statement: str, *_r: Any) -> None:
                seen.append(statement)

            event.listen(pg_engine, "before_cursor_execute", record)
            try:
                assert seeded.get(PLAYERS, params={"limit": limit}).status_code == 200
            finally:
                event.remove(pg_engine, "before_cursor_execute", record)
            return len(seen)

        assert count(1) == count(50)

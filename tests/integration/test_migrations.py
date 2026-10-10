"""The Alembic migration chain, run for real on a blank PostgreSQL database.

Skipped unless ``TEST_DATABASE_URL`` is set; see ``conftest.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from app.db.models import Base
from tests.integration.conftest import _connect, reset_schema

pytestmark = pytest.mark.integration

V1_REVISION = "09462d11fffe"
V2_REVISION = "799bbae899b8"


def _config(connection: Any) -> Config:
    # No ini file: env.py would otherwise call fileConfig() and disable every
    # existing logger for the rest of the test session.
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.attributes["connection"] = connection
    return config


@pytest.fixture
def blank_engine() -> Iterator[Engine]:
    engine = _connect()
    reset_schema(engine)
    try:
        yield engine
    finally:
        reset_schema(engine)
        engine.dispose()


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def _upgrade(engine: Engine, revision: str) -> None:
    with engine.begin() as connection:
        command.upgrade(_config(connection), revision)


def _downgrade(engine: Engine, revision: str) -> None:
    with engine.begin() as connection:
        command.downgrade(_config(connection), revision)


def _current(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _schema(engine: Engine) -> dict[str, Any]:
    """A comparable description of tables, columns, keys, and indexes."""
    inspector = inspect(engine)
    described: dict[str, Any] = {}
    for table in sorted(_tables(engine)):
        described[table] = {
            "columns": {
                column["name"]: (str(column["type"]).split("(")[0], column["nullable"])
                for column in inspector.get_columns(table)
            },
            "pk": inspector.get_pk_constraint(table)["constrained_columns"],
            "fks": sorted(
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    fk["options"].get("ondelete"),
                )
                for fk in inspector.get_foreign_keys(table)
            ),
            "uniques": sorted(
                tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)
            ),
            "indexes": sorted(
                (tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes(table)
            ),
        }
    return described


class TestMigrationChain:
    def test_there_is_a_single_head(self) -> None:
        script = ScriptDirectory("alembic")

        assert len(script.get_heads()) == 1

    def test_upgrade_creates_every_table(self, blank_engine: Engine) -> None:
        _upgrade(blank_engine, "head")

        assert _tables(blank_engine) == {t.name for t in Base.metadata.sorted_tables}
        assert {"source_manifests", "pass_details", "shot_details"} <= _tables(blank_engine)

    def test_the_v2_migration_follows_v1_and_adds_only_the_new_tables(
        self, blank_engine: Engine
    ) -> None:
        _upgrade(blank_engine, V1_REVISION)
        v1_tables = _tables(blank_engine)
        _upgrade(blank_engine, V2_REVISION)

        assert "pass_details" not in v1_tables
        assert _tables(blank_engine) - v1_tables == {
            "source_manifests",
            "pass_details",
            "shot_details",
        }

    def test_the_jobs_migration_adds_only_the_jobs_table(self, blank_engine: Engine) -> None:
        _upgrade(blank_engine, V2_REVISION)
        before = _tables(blank_engine)
        _upgrade(blank_engine, "head")

        assert _tables(blank_engine) - before == {"processing_jobs"}

    def test_downgrading_the_jobs_migration_leaves_v2_intact(self, blank_engine: Engine) -> None:
        _upgrade(blank_engine, "head")
        _downgrade(blank_engine, V2_REVISION)

        assert _current(blank_engine) == V2_REVISION
        assert "processing_jobs" not in _tables(blank_engine)
        assert {"source_manifests", "pass_details", "shot_details"} <= _tables(blank_engine)

    def test_downgrade_removes_only_the_v2_tables(self, blank_engine: Engine) -> None:
        _upgrade(blank_engine, "head")
        _downgrade(blank_engine, V1_REVISION)

        assert _current(blank_engine) == V1_REVISION
        assert _tables(blank_engine).isdisjoint(
            {"source_manifests", "pass_details", "shot_details"}
        )
        assert {"events", "matches", "lineups"} <= _tables(blank_engine)

    def test_downgrade_to_base_then_upgrade_again(self, blank_engine: Engine) -> None:
        _upgrade(blank_engine, "head")
        _downgrade(blank_engine, "base")

        assert _tables(blank_engine) == set()

        _upgrade(blank_engine, "head")

        assert _tables(blank_engine) == {t.name for t in Base.metadata.sorted_tables}

    def test_upgrading_twice_is_a_no_op(self, blank_engine: Engine) -> None:
        _upgrade(blank_engine, "head")
        before = _schema(blank_engine)
        _upgrade(blank_engine, "head")

        assert _schema(blank_engine) == before

    def test_the_v2_migration_preserves_existing_v1_data(self, blank_engine: Engine) -> None:
        _upgrade(blank_engine, V1_REVISION)
        with blank_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO competitions (id, statsbomb_id, name) VALUES (1, 16, 'UCL')")
            )
        _upgrade(blank_engine, "head")

        with blank_engine.connect() as connection:
            assert connection.scalar(text("SELECT name FROM competitions WHERE id = 1")) == "UCL"


class TestMigrationsMatchModels:
    def test_migrated_schema_equals_the_schema_the_models_create(
        self, blank_engine: Engine
    ) -> None:
        _upgrade(blank_engine, "head")
        migrated = _schema(blank_engine)

        reset_schema(blank_engine)
        Base.metadata.create_all(blank_engine)
        from_models = _schema(blank_engine)

        assert migrated == from_models

    def test_alembic_autogenerate_sees_no_difference(self, blank_engine: Engine) -> None:
        from alembic.autogenerate import compare_metadata

        _upgrade(blank_engine, "head")
        with blank_engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            differences = compare_metadata(context, Base.metadata)

        assert differences == []

    def test_summary_columns_are_jsonb(self, blank_engine: Engine) -> None:
        _upgrade(blank_engine, "head")
        inspector = inspect(blank_engine)

        for table, column in (
            ("source_manifests", "summary"),
            ("events", "raw_data"),
            ("lineups", "raw_data"),
        ):
            kind = next(c["type"] for c in inspector.get_columns(table) if c["name"] == column)
            assert type(kind).__name__ == "JSONB", f"{table}.{column} is {kind}"

    def test_the_import_status_check_constraint_exists(self, blank_engine: Engine) -> None:
        _upgrade(blank_engine, "head")

        checks = inspect(blank_engine).get_check_constraints("source_manifests")

        assert [c["name"] for c in checks] == ["ck_source_manifests_import_status"]
        for status in ("success", "partial", "failed"):
            assert status in checks[0]["sqltext"]

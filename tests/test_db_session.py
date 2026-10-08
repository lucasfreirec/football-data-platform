import pytest
from sqlalchemy import create_engine

from app.core.exceptions import PersistenceError
from app.db.base import Base
from app.db.session import check_database_connection, create_db_engine


def test_create_db_engine_uses_the_given_url() -> None:
    engine = create_db_engine("postgresql+psycopg://u:p@localhost:5432/db")

    assert engine.url.drivername == "postgresql+psycopg"
    assert engine.url.database == "db"
    engine.dispose()


def test_check_database_connection_true_for_reachable_database() -> None:
    engine = create_engine("sqlite://")

    assert check_database_connection(engine) is True
    engine.dispose()


def test_check_database_connection_false_for_unreachable_database() -> None:
    engine = create_db_engine("postgresql+psycopg://u:p@127.0.0.1:1/absent")

    assert check_database_connection(engine) is False
    engine.dispose()


def test_metadata_uses_naming_convention() -> None:
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"
    assert "uq" in Base.metadata.naming_convention
    assert "fk" in Base.metadata.naming_convention


def test_persistence_error_is_an_application_error() -> None:
    with pytest.raises(PersistenceError):
        raise PersistenceError("boom")

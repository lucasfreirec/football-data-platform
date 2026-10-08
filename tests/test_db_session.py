import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import PersistenceError
from app.db import session as session_module
from app.db.base import Base
from app.db.models import Competition, Team
from app.db.session import check_database_connection, create_db_engine, get_db, session_scope


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


class TestSessionScope:
    @pytest.fixture(autouse=True)
    def _bind_to_test_engine(self, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        monkeypatch.setattr(session_module, "get_session_factory", lambda: factory)

    def test_commits_on_success(self, session: Session) -> None:
        with session_scope() as scoped:
            scoped.add(Team(statsbomb_id=220, name="Real Madrid"))

        assert session.scalar(select(Team).where(Team.statsbomb_id == 220)) is not None

    def test_rolls_back_on_application_error(self, session: Session) -> None:
        with pytest.raises(RuntimeError), session_scope() as scoped:
            scoped.add(Team(statsbomb_id=220, name="Real Madrid"))
            raise RuntimeError("boom")

        assert session.scalar(select(Team).where(Team.statsbomb_id == 220)) is None

    def test_database_errors_become_persistence_errors(self) -> None:
        with (
            pytest.raises(PersistenceError, match="Database transaction failed"),
            session_scope() as scoped,
        ):
            scoped.add(Team(statsbomb_id=220, name="First"))
            scoped.flush()
            scoped.add(Team(statsbomb_id=220, name="Duplicate"))

    def test_get_db_yields_a_usable_session(self) -> None:
        generator = get_db()
        db = next(generator)

        assert db.execute(text("SELECT 1")).scalar() == 1

        with pytest.raises(StopIteration):
            next(generator)


class TestConstraints:
    def test_duplicate_external_id_is_rejected(self, session: Session) -> None:
        session.add_all([Team(statsbomb_id=220, name="A"), Team(statsbomb_id=220, name="B")])

        with pytest.raises(IntegrityError):
            session.flush()

    def test_season_requires_an_existing_competition(self, session: Session) -> None:
        from app.db.models import Season

        session.add(Season(competition_id=999, statsbomb_id=1, name="2017/2018"))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_event_requires_an_existing_match(self, session: Session) -> None:
        from app.db.models import Event

        session.add(Event(statsbomb_id="x", match_id=999, index_in_match=1, type_name="Pass"))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_competition_name_is_required(self, session: Session) -> None:
        session.add(Competition(statsbomb_id=16))

        with pytest.raises(IntegrityError):
            session.flush()

"""Match model."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.models.competition import Competition, Season
from app.db.models.team import Team

if TYPE_CHECKING:
    from app.db.models.event import Event
    from app.db.models.lineup import Lineup


class Match(TimestampMixin, Base):
    __tablename__ = "matches"
    __table_args__ = (Index("ix_matches_competition_id_season_id", "competition_id", "season_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statsbomb_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), nullable=False)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    match_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kick_off: Mapped[time | None] = mapped_column(Time)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    stage_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(64))
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    competition: Mapped[Competition] = relationship(back_populates="matches")
    season: Mapped[Season] = relationship(back_populates="matches")
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])
    events: Mapped[list[Event]] = relationship(back_populates="match", cascade="all, delete-orphan")
    lineups: Mapped[list[Lineup]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )

"""Lineup model: one row per player in a match squad."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JsonDict, TimestampMixin
from app.db.models.player import Player
from app.db.models.team import Team

if TYPE_CHECKING:
    from app.db.models.match import Match


class Lineup(TimestampMixin, Base):
    __tablename__ = "lineups"
    __table_args__ = (UniqueConstraint("match_id", "team_id", "player_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    jersey_number: Mapped[int | None] = mapped_column(Integer)
    position_name: Mapped[str | None] = mapped_column(String(64))
    is_starter: Mapped[bool | None] = mapped_column(Boolean)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JsonDict)

    match: Mapped[Match] = relationship(back_populates="lineups")
    team: Mapped[Team] = relationship()
    player: Mapped[Player] = relationship()

"""Event model.

V1 keeps a single flat events table; StatsBomb event subtypes are not modelled
separately. Unmodelled attributes of interest live in ``raw_data``.
"""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Integer, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JsonDict, TimestampMixin
from app.db.models.player import Player
from app.db.models.team import Team

if TYPE_CHECKING:
    from app.db.models.match import Match


class Event(TimestampMixin, Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("match_id", "index_in_match"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statsbomb_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    index_in_match: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[int | None] = mapped_column(Integer)
    timestamp: Mapped[time | None] = mapped_column(Time)
    minute: Mapped[int | None] = mapped_column(Integer)
    second: Mapped[int | None] = mapped_column(Integer)
    type_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    possession: Mapped[int | None] = mapped_column(Integer)
    possession_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), index=True)
    location_x: Mapped[float | None] = mapped_column(Float)
    location_y: Mapped[float | None] = mapped_column(Float)
    outcome_name: Mapped[str | None] = mapped_column(String(64))
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JsonDict)

    match: Mapped[Match] = relationship(back_populates="events")
    team: Mapped[Team | None] = relationship(foreign_keys=[team_id])
    possession_team: Mapped[Team | None] = relationship(foreign_keys=[possession_team_id])
    player: Mapped[Player | None] = relationship(foreign_keys=[player_id])

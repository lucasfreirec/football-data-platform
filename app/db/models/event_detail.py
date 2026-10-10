"""Pass and shot detail tables.

Each row shares its primary key with the parent ``events`` row, so an event has
at most one detail row. The pairing of detail type to event type is enforced by
the ingestion code rather than a database constraint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.player import Player

if TYPE_CHECKING:
    from app.db.models.event import Event


class PassDetail(Base):
    __tablename__ = "pass_details"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    recipient_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), index=True)
    length: Mapped[float | None] = mapped_column(Float)
    angle: Mapped[float | None] = mapped_column(Float)
    height_name: Mapped[str | None] = mapped_column(String(64))
    end_location_x: Mapped[float | None] = mapped_column(Float)
    end_location_y: Mapped[float | None] = mapped_column(Float)
    outcome_name: Mapped[str | None] = mapped_column(String(64), index=True)
    body_part_name: Mapped[str | None] = mapped_column(String(64))
    technique_name: Mapped[str | None] = mapped_column(String(64))
    type_name: Mapped[str | None] = mapped_column(String(64))
    is_cross: Mapped[bool | None] = mapped_column(Boolean)
    is_cut_back: Mapped[bool | None] = mapped_column(Boolean)
    is_switch: Mapped[bool | None] = mapped_column(Boolean)
    is_assisted_shot: Mapped[bool | None] = mapped_column(Boolean)
    is_goal_assist: Mapped[bool | None] = mapped_column(Boolean)

    event: Mapped[Event] = relationship(back_populates="pass_detail")
    recipient: Mapped[Player | None] = relationship(foreign_keys=[recipient_player_id])


class ShotDetail(Base):
    __tablename__ = "shot_details"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    key_pass_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL")
    )
    end_location_x: Mapped[float | None] = mapped_column(Float)
    end_location_y: Mapped[float | None] = mapped_column(Float)
    statsbomb_xg: Mapped[float | None] = mapped_column(Float, index=True)
    outcome_name: Mapped[str | None] = mapped_column(String(64), index=True)
    body_part_name: Mapped[str | None] = mapped_column(String(64))
    technique_name: Mapped[str | None] = mapped_column(String(64))
    type_name: Mapped[str | None] = mapped_column(String(64))
    first_time: Mapped[bool | None] = mapped_column(Boolean)
    one_on_one: Mapped[bool | None] = mapped_column(Boolean)
    open_goal: Mapped[bool | None] = mapped_column(Boolean)

    event: Mapped[Event] = relationship(back_populates="shot_detail", foreign_keys=[event_id])
    key_pass_event: Mapped[Event | None] = relationship(foreign_keys=[key_pass_event_id])

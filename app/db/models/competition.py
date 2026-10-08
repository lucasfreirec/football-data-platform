"""Competition and season models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.match import Match


class Competition(TimestampMixin, Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statsbomb_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_name: Mapped[str | None] = mapped_column(String(255))
    gender: Mapped[str | None] = mapped_column(String(32))
    format: Mapped[str | None] = mapped_column(String(64))

    seasons: Mapped[list[Season]] = relationship(
        back_populates="competition", cascade="all, delete-orphan"
    )
    matches: Mapped[list[Match]] = relationship(back_populates="competition")


class Season(TimestampMixin, Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("competition_id", "statsbomb_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    statsbomb_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    competition: Mapped[Competition] = relationship(back_populates="seasons")
    matches: Mapped[list[Match]] = relationship(back_populates="season")

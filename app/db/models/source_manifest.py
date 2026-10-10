"""Record of the source input behind each ingestion run."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JsonDict
from app.db.models.competition import Competition, Season

IMPORT_STATUSES = ("success", "partial", "failed")


class SourceManifest(Base):
    __tablename__ = "source_manifests"
    __table_args__ = (
        CheckConstraint("import_status IN ('success', 'partial', 'failed')", name="import_status"),
        Index("ix_source_manifests_competition_id_season_id", "competition_id", "season_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False
    )
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False
    )
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(128))
    match_count_expected: Mapped[int] = mapped_column(Integer, nullable=False)
    event_file_count_found: Mapped[int] = mapped_column(Integer, nullable=False)
    lineup_file_count_found: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    import_status: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JsonDict, nullable=False, default=dict)

    competition: Mapped[Competition] = relationship()
    season: Mapped[Season] = relationship()

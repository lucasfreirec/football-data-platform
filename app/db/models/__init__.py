"""ORM models.

Importing this package registers every model on ``Base.metadata`` so Alembic
autogenerate sees the full schema.
"""

from app.db.base import Base
from app.db.models.competition import Competition, Season
from app.db.models.event import Event
from app.db.models.event_detail import PassDetail, ShotDetail
from app.db.models.lineup import Lineup
from app.db.models.match import Match
from app.db.models.player import Player
from app.db.models.processing_job import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    JobStatus,
    JobType,
    ProcessingJob,
)
from app.db.models.source_manifest import SourceManifest
from app.db.models.team import Team

__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "Base",
    "Competition",
    "Event",
    "JobStatus",
    "JobType",
    "Lineup",
    "Match",
    "PassDetail",
    "Player",
    "ProcessingJob",
    "Season",
    "ShotDetail",
    "SourceManifest",
    "Team",
]

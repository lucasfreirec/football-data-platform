"""Response models for pass and shot details."""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field

from app.api.schemas.common import ORM_CONFIG
from app.api.schemas.resources import EventBase, PlayerRef


class PassDetailRead(BaseModel):
    """Attributes specific to a pass. Absent source values are ``null``."""

    model_config = ORM_CONFIG

    recipient: PlayerRef | None = None
    length: float | None = None
    angle: float | None = None
    height_name: str | None = None
    end_location_x: float | None = None
    end_location_y: float | None = None
    outcome_name: str | None = Field(
        default=None, description="`null` means the pass was completed."
    )
    body_part_name: str | None = None
    technique_name: str | None = None
    type_name: str | None = Field(default=None, description="StatsBomb pass subtype.")
    is_cross: bool | None = None
    is_cut_back: bool | None = None
    is_switch: bool | None = None
    is_assisted_shot: bool | None = None
    is_goal_assist: bool | None = None


class ShotDetailRead(BaseModel):
    """Attributes specific to a shot. Absent source values are ``null``."""

    model_config = ORM_CONFIG

    key_pass_id: str | None = Field(
        default=None,
        validation_alias="key_pass_statsbomb_id",
        description="StatsBomb UUID of the pass event that created the chance.",
    )
    end_location_x: float | None = None
    end_location_y: float | None = None
    statsbomb_xg: float | None = None
    outcome_name: str | None = None
    body_part_name: str | None = None
    technique_name: str | None = None
    type_name: str | None = Field(
        default=None, description="Shot subtype, such as open play or penalty."
    )
    first_time: bool | None = None
    one_on_one: bool | None = None
    open_goal: bool | None = None


class EventDetailRead(EventBase):
    """A base event plus the one detail object that applies to its type."""

    pass_: PassDetailRead | None = Field(
        default=None,
        validation_alias=AliasChoices("pass_detail", "pass_"),
        serialization_alias="pass",
        description="Present only for `Pass` events.",
    )
    shot: ShotDetailRead | None = Field(
        default=None,
        validation_alias=AliasChoices("shot_detail", "shot"),
        description="Present only for `Shot` events.",
    )


class PassEventRead(EventBase):
    """A pass event with its pass details."""

    pass_: PassDetailRead = Field(
        validation_alias=AliasChoices("pass_detail", "pass_"), serialization_alias="pass"
    )


class ShotEventRead(EventBase):
    """A shot event with its shot details."""

    shot: ShotDetailRead = Field(validation_alias=AliasChoices("shot_detail", "shot"))

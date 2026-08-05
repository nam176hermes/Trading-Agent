"""Engine-neutral event classification and payload metadata."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)


class EventFamily(str, Enum):
    """Closed protocol-v1 classification for engine output events."""

    ENGINE_LIFECYCLE = "ENGINE_LIFECYCLE"
    MARKET_DATA_CONTINUITY = "MARKET_DATA_CONTINUITY"
    STRATEGY_LIFECYCLE = "STRATEGY_LIFECYCLE"
    ORDER_LIFECYCLE = "ORDER_LIFECYCLE"
    FILLS = "FILLS"
    POSITIONS = "POSITIONS"
    ACCOUNT_STATE = "ACCOUNT_STATE"
    RUNTIME_RISK = "RUNTIME_RISK"
    RECONCILIATION = "RECONCILIATION"
    HEALTH = "HEALTH"
    HALT = "HALT"


EventValue: TypeAlias = (
    Annotated[StrictStr, Field(max_length=4_096)] | StrictBool | StrictInt
)


class EventModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class EventAttribute(EventModel):
    """One bounded scalar event fact with no implementation-specific object."""

    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            pattern=r"^[a-z][a-z0-9_]{0,63}$",
        ),
    ]
    value: EventValue


class EngineEvent(EventModel):
    """Classified, implementation-neutral engine event payload."""

    event_type: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$",
        ),
    ]
    family: EventFamily
    attributes: tuple[EventAttribute, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def _unique_attributes(self) -> "EngineEvent":
        names: set[str] = set()
        for attribute in self.attributes:
            if attribute.name in names:
                raise ValueError(f"duplicate event attribute: {attribute.name}")
            names.add(attribute.name)
        return self

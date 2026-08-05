"""Versioned engine capability declarations."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .commands import CommandName
from .events import EventFamily
from .versions import SchemaVersion


class EngineMode(str, Enum):
    """The only execution modes authorized by contract version 1."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"


class EngineCapabilities(BaseModel):
    """Closed, immutable declaration returned by an engine implementation."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    schema_version: SchemaVersion
    engine_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
        ),
    ]
    engine_version: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$",
        ),
    ]
    supported_commands: tuple[CommandName, ...] = Field(min_length=1)
    supported_event_families: tuple[EventFamily, ...] = Field(min_length=1)
    supported_modes: tuple[EngineMode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _reject_duplicate_capabilities(self) -> "EngineCapabilities":
        for field_name in (
            "supported_commands",
            "supported_event_families",
            "supported_modes",
        ):
            items = getattr(self, field_name)
            if len(set(items)) != len(items):
                raise ValueError(f"duplicate {field_name} capability")
        return self

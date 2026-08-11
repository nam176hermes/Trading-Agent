"""Registered content-addressed recovery checkpoint record."""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    ValidationError,
    field_validator,
    model_validator,
)

from .runtime_risk import Sha256


NonEmptyCheckpointJson = Annotated[str, Field(min_length=1)]


class SandboxRecoveryCheckpointRecorded(BaseModel):
    """Canonical checkpoint bytes and identity, without execution authority."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )

    recovery_session_id: UUID
    checkpoint_id: UUID
    checkpoint_digest: Sha256
    checkpoint_json: NonEmptyCheckpointJson
    checkpoint_schema_version: Literal["sandbox-recovery-checkpoint-v1"]
    schema_version: Literal["sandbox-recovery-checkpoint-recorded-v1"]

    @field_validator("recovery_session_id", "checkpoint_id", mode="before")
    @classmethod
    def _concrete_uuid(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "json":
            return value
        if type(value) is not UUID:
            raise ValueError("recovery record identities must be concrete UUID values")
        return value

    @field_validator(
        "checkpoint_digest",
        "checkpoint_json",
        "checkpoint_schema_version",
        "schema_version",
        mode="before",
    )
    @classmethod
    def _concrete_text(cls, value: object) -> str:
        if type(value) is not str:
            raise ValueError("recovery record text must use concrete string values")
        return value

    @model_validator(mode="after")
    def _stored_bytes_match_digest(self) -> "SandboxRecoveryCheckpointRecorded":
        try:
            checkpoint_bytes = str.encode(self.checkpoint_json, "utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("checkpoint_json must be valid UTF-8 text") from exc
        if sha256(checkpoint_bytes).hexdigest() != self.checkpoint_digest:
            raise ValueError("checkpoint_digest must match stored checkpoint_json bytes")
        return self

    @classmethod
    def model_validate(
        cls,
        obj: object,
        *,
        strict: bool | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Rebuild copied or constructed records before trusting their fields."""

        if isinstance(obj, cls):
            try:
                obj = {
                    name: object.__getattribute__(obj, name)
                    for name in cls.model_fields
                }
            except AttributeError as exc:
                raise ValidationError.from_exception_data(
                    cls.__name__,
                    [{"type": "missing", "loc": ("recovery_record",), "input": obj}],
                ) from exc
        return super().model_validate(
            obj,
            strict=strict,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )


__all__ = ["SandboxRecoveryCheckpointRecorded"]

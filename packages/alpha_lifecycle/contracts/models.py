"""Shared validation and parsing for untrusted P3 artifact bytes."""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from packages.engine_contracts.serialization import canonical_json_bytes


def json_array(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("value must be a JSON array")
    return tuple(value)


class ContractError(ValueError):
    """Untrusted bytes do not identify one valid P3 contract."""


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    @model_validator(mode="after")
    def _artifact_references_are_bound(self) -> "StrictModel":
        from packages.data_contracts import ArtifactRefV1

        def walk(value: object) -> None:
            if isinstance(value, ArtifactRefV1):
                if value.locator != f"{value.content_sha256}.blob":
                    raise ValueError("artifact locator does not match content digest")
            elif isinstance(value, BaseModel):
                for field in type(value).model_fields:
                    walk(getattr(value, field))
            elif isinstance(value, (tuple, list)):
                for item in value:
                    walk(item)

        for field in type(self).model_fields:
            walk(getattr(self, field))
        return self


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DigestModel(StrictModel):
    digest: "Sha256"

    @model_validator(mode="after")
    def _digest_matches(self) -> "DigestModel":
        calculated = hashlib.sha256(
            canonical_json_bytes(self.model_dump(mode="json", exclude={"digest"}))
        ).hexdigest()
        if self.digest != calculated:
            raise ValueError("artifact digest is invalid")
        return self


class SafeAuthority(StrictModel):
    broker: Literal[False]
    live: Literal[False]
    network: Literal[False]
    production: Literal[False]


GitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
AlphaId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")]
Token = Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")]
SemVer = Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
Text = Annotated[str, Field(min_length=1, max_length=512)]


class SourceIdentity(StrictModel):
    commit_sha: GitSha
    tree_sha: GitSha
    closure_schema_version: Text
    closure_policy_sha256: Sha256
    closure_sha256: Sha256


def _decimal_text(value: object) -> str:
    if not isinstance(value, str) or "e" in value.lower() or value.startswith("+"):
        raise ValueError("decimal text must be a finite non-exponential string")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("decimal text is invalid") from error
    if not number.is_finite():
        raise ValueError("decimal text must be finite")
    canonical = format(number, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if number == 0:
        canonical = "0"
    if canonical != value:
        raise ValueError("decimal text is not canonical")
    return value


DecimalText = Annotated[
    str,
    BeforeValidator(_decimal_text),
    Field(pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$"),
]

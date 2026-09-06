"""Shared validation and parsing for untrusted P3 artifact bytes."""

from __future__ import annotations

import json
import hashlib
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from packages.engine_contracts.serialization import canonical_json_bytes


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


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
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


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _contract_types() -> dict[str, type[BaseModel]]:
    from packages.alpha_lifecycle.baselines import BaselineResultV1
    from packages.alpha_lifecycle.metrics import CostModelV1
    from packages.alpha_lifecycle.protocol import PerformanceMetricsV1, QualificationCriterionV1
    from packages.data_contracts import ArtifactRefV1
    from . import authority, data, execution, lifecycle, policy, results

    models: dict[str, type[BaseModel]] = {
        "ArtifactRef": ArtifactRefV1,
        "CostModel": CostModelV1,
        "Criterion": QualificationCriterionV1,
        "LegacyBaselineResult": BaselineResultV1,
        "PerformanceMetrics": PerformanceMetricsV1,
        "SafeAuthority": SafeAuthority,
        "SourceIdentity": SourceIdentity,
    }
    for module in (authority, data, execution, lifecycle, policy, results):
        for name in module.__all__:
            value = getattr(module, name)
            if isinstance(value, type) and issubclass(value, BaseModel):
                models[name] = value
    return models


def parse_contract(type_name: str, raw: bytes) -> BaseModel:
    if not isinstance(raw, bytes):
        raise ContractError("contract input must be bytes")
    try:
        payload = json.loads(raw, object_pairs_hook=_object)
    except ContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("contract input must be one valid UTF-8 JSON value") from error
    if not isinstance(payload, dict):
        raise ContractError("contract root must be an object")
    try:
        model = _contract_types()[type_name]
    except KeyError as error:
        raise ContractError(f"unknown contract type: {type_name}") from error
    value = model.model_validate(payload)
    from packages.data_contracts import ArtifactRefV1

    if isinstance(value, ArtifactRefV1) and value.locator != f"{value.content_sha256}.blob":
        raise ContractError("artifact locator does not match content digest")
    return value


__all__ = [
    "AlphaId",
    "ContractError",
    "DigestModel",
    "DecimalText",
    "GitSha",
    "SafeAuthority",
    "SemVer",
    "Sha256",
    "SourceIdentity",
    "StrictModel",
    "Token",
    "Text",
    "parse_contract",
]

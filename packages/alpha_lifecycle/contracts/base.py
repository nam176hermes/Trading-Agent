"""Public P3 contract parsing and compatibility exports."""

from __future__ import annotations

import json
from typing import Any
from pydantic import BaseModel

from .models import (
    AlphaId as AlphaId,
    ContractError as ContractError,
    DigestModel as DigestModel,
    DecimalText as DecimalText,
    GitSha as GitSha,
    SafeAuthority as SafeAuthority,
    SemVer as SemVer,
    Sha256 as Sha256,
    SourceIdentity as SourceIdentity,
    StrictModel as StrictModel,
    Token as Token,
    Text as Text,
)


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
    import packages.alpha_lifecycle.contracts.authority as authority
    import packages.alpha_lifecycle.contracts.data as data
    import packages.alpha_lifecycle.contracts.execution as execution
    import packages.alpha_lifecycle.contracts.lifecycle as lifecycle
    import packages.alpha_lifecycle.contracts.policy as policy
    import packages.alpha_lifecycle.contracts.results as results

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
    value = model.model_validate_json(raw)
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

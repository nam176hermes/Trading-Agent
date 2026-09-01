"""Pure, source-owned LTS policy and change classifier for the P1 Nautilus lane."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac
import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from packages.nautilus_runtime_contracts.paper import PAPER_PROTOCOL_SCHEMA
from packages.nautilus_upgrade_authority.lifecycle import (
    CheckpointCompatibility,
    EngineLifecycle,
    EngineRegistryEntry,
    EventApiEpoch,
    classify_checkpoint_compatibility,
    golden_registry_sha256,
    validate_engine_registry,
)


_EXPECTED_POLICY_SHA256 = "3f5055e2db482da951b3d27a7a489623e8f8ec9b0a7bd2848e220ffcd30363e0"
_SHA256 = r"^[0-9a-f]{64}$"
_COMMIT = r"^[0-9a-f]{40}$"


class P1LtsPolicyError(ValueError):
    """The P1 LTS policy or its selected runtime identity is invalid."""


class P1ChangeClass(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class P1ImpactDisposition(str, Enum):
    QUALIFIABLE = "QUALIFIABLE"
    HELD = "HELD"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class P1AuthorityLimitsV1(_FrozenModel):
    network_authorized: bool
    live_authorized: bool
    production_authorized: bool
    broker_access_authorized: bool
    database_runtime_authorized: bool


class P1EvidenceBindingV1(_FrozenModel):
    path: str
    sha256: Annotated[str, Field(pattern=_SHA256)]

    @model_validator(mode="after")
    def _safe_path(self) -> "P1EvidenceBindingV1":
        parsed = PurePosixPath(self.path)
        if not self.path or parsed.is_absolute() or ".." in parsed.parts or "\\" in self.path:
            raise ValueError("binding path is invalid")
        return self


class P1EngineRegistryEntryV2(_FrozenModel):
    runtime_family: str
    engine_version: str
    engine_upstream_commit: Annotated[str, Field(pattern=_COMMIT)]
    python_abi: str
    closure_schema_version: Annotated[int, Field(ge=1)]
    closure_sha256: Annotated[str, Field(pattern=_SHA256)]
    semantic_profile: str
    lifecycle: EngineLifecycle

    @field_validator("lifecycle", mode="before")
    @classmethod
    def _json_lifecycle(cls, value: object) -> object:
        return EngineLifecycle(value) if type(value) is str else value


class P1EventApiEpochV1(_FrozenModel):
    request_protocol: str
    event_schema: str
    paper_schema: str
    result_validator: str
    manifest_schema: Annotated[int, Field(ge=1)]


class P1CheckpointPolicyV1(_FrozenModel):
    active_schema: str
    replay_required_from: tuple[str, ...]
    incompatible_by_default: bool

    @field_validator("replay_required_from", mode="before")
    @classmethod
    def _json_tuple(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value


class P1GoldenScenarioV1(_FrozenModel):
    result_sha256: Annotated[str, Field(pattern=_SHA256)]
    event_sha256: Annotated[str, Field(pattern=_SHA256)]
    oracle_sha256: Annotated[str, Field(pattern=_SHA256)]
    candidate_semantic_sha256: Annotated[str, Field(pattern=_SHA256)]
    rollback_semantic_sha256: Annotated[str, Field(pattern=_SHA256)]


class P1GoldenRegistryV1(_FrozenModel):
    module: str
    object_name: str
    source_path: str
    source_sha256: Annotated[str, Field(pattern=_SHA256)]
    scenarios: dict[str, P1GoldenScenarioV1]


class P1QualificationNodeV1(_FrozenModel):
    node_id: str
    dependencies: tuple[str, ...]

    @field_validator("dependencies", mode="before")
    @classmethod
    def _json_tuple(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value


class P1BindingsV2(_FrozenModel):
    baseline_receipt: P1EvidenceBindingV1
    candidate_generation: P1EvidenceBindingV1
    p1_complete_receipt: P1EvidenceBindingV1
    release_regression_matrix: P1EvidenceBindingV1
    rollback_evidence: P1EvidenceBindingV1
    u06_regression: P1EvidenceBindingV1
    u07_dual_runtime: P1EvidenceBindingV1


class P1LtsPolicyV2(_FrozenModel):
    document_schema: Literal["trading-agent-p1-engine-lts-policy/v2"] = Field(alias="schema")
    execution_scope: Literal["PAPER_LOCAL_ONLY"]
    authority_limits: P1AuthorityLimitsV1
    engine_registry: tuple[P1EngineRegistryEntryV2, ...]
    event_api_epoch: P1EventApiEpochV1
    checkpoint_policy: P1CheckpointPolicyV1
    golden_registry: P1GoldenRegistryV1
    qualification_dag: tuple[P1QualificationNodeV1, ...]
    bindings: P1BindingsV2
    _record_sha256: str = PrivateAttr()

    @property
    def record_sha256(self) -> str:
        return self._record_sha256

    @field_validator("engine_registry", "qualification_dag", mode="before")
    @classmethod
    def _json_tuple(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @model_validator(mode="after")
    def _closed_policy(self) -> "P1LtsPolicyV2":
        if any(self.authority_limits.model_dump().values()):
            raise ValueError("P1 LTS policy grants invalid authority")
        registry = validate_engine_registry(
            EngineRegistryEntry(item.runtime_family, item.engine_version, item.lifecycle)
            for item in self.engine_registry
        )
        if len(registry) != 2 or {item.lifecycle for item in registry} != {
            EngineLifecycle.ACTIVE,
            EngineLifecycle.ROLLBACK,
        }:
            raise ValueError("P1 LTS registry requires active and rollback engines")
        if not self.checkpoint_policy.incompatible_by_default or len(self.golden_registry.scenarios) != 8:
            raise ValueError("P1 LTS compatibility policy is incomplete")
        seen: set[str] = set()
        for node in self.qualification_dag:
            if node.node_id in seen or not set(node.dependencies) <= seen:
                raise ValueError("P1 LTS qualification nodes are not a static DAG")
            seen.add(node.node_id)
        return self


@dataclass(frozen=True, slots=True)
class P1ImpactDecisionV1:
    change_class: P1ChangeClass
    disposition: P1ImpactDisposition
    required_node_ids: tuple[str, ...]
    reasons: tuple[str, ...]


_CLASS_NODES = {
    P1ChangeClass.A: ("P1S_SOURCE", "P1S_RECOVERY", "P1S_GOLDEN", "P1H_FOUNDATION"),
    P1ChangeClass.B: (
        "P1S_SOURCE", "P1S_RECOVERY", "P1S_GOLDEN", "P1N_G1",
        "P1N_E2E", "P1N_PAPER", "P1H_FOUNDATION", "P1O_ACCEPT",
    ),
    P1ChangeClass.C: (
        "P1S_SOURCE", "P1S_RECOVERY", "P1S_GOLDEN", "P1N_G1",
        "P1N_E2E", "P1N_PAPER", "P1H_FOUNDATION", "P1O_ACCEPT",
    ),
}
_CLASS_ORDER = {P1ChangeClass.A: 0, P1ChangeClass.B: 1, P1ChangeClass.C: 2, P1ChangeClass.D: 3}
_CLASS_D_PREFIXES = ("packages/engine_contracts/", "packages/nautilus_runtime_contracts/")
_CLASS_D_PATHS = {
    "docs/implementation/p1-real-nautilus/lts/p1-engine-lts-policy-v2.json",
    "packages/nautilus_upgrade_authority/lts.py",
}
_CLASS_C_PATHS = {"packages/domain/recovery.py", "pyproject.toml", "uv.lock"}
_CLASS_C_PREFIXES = (
    "engines/nautilus/candidates/", "engines/nautilus/native_entry_guard/",
    "engines/nautilus/sealed_uv_exec/", "scripts/build_nautilus",
    "scripts/materialize_nautilus_runtime_closure.py", "scripts/verify_nautilus_runtime_closure.py",
)
_CLASS_B_PREFIXES = (
    "engines/nautilus/", "packages/nautilus_backtest/", "services/job_worker/", "services/paper_runtime/",
)
_CLASS_A_PATHS = {"Makefile", "scripts/qualify_p1_engine_lts.py"}
_CLASS_A_PREFIXES = ("docs/", "tests/", "packages/nautilus_upgrade_authority/")


def _canonical(value: object) -> bytes:
    return (json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _loads_exact(raw: bytes) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise P1LtsPolicyError("P1 LTS policy contains a duplicate key")
            result[key] = value
        return result

    def reject_float(_value: str) -> object:
        raise P1LtsPolicyError("P1 LTS policy float input is forbidden")

    try:
        value = json.loads(raw, object_pairs_hook=no_duplicates, parse_float=reject_float, parse_constant=reject_float)
    except P1LtsPolicyError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P1LtsPolicyError("P1 LTS policy bytes are invalid JSON") from exc
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise P1LtsPolicyError("P1 LTS policy bytes are not canonical")
    return cast(dict[str, object], value)


def load_p1_lts_policy(path: Path) -> P1LtsPolicyV2:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise P1LtsPolicyError("P1 LTS policy is unavailable") from exc
    value = _loads_exact(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(digest, _EXPECTED_POLICY_SHA256):
        raise P1LtsPolicyError("P1 LTS policy SHA-256 is not accepted")
    try:
        policy = P1LtsPolicyV2.model_validate(value)
    except ValidationError as exc:
        raise P1LtsPolicyError("P1 LTS policy shape is invalid") from exc
    object.__setattr__(policy, "_record_sha256", digest)
    return policy


def validate_p1_lts_identity(policy: P1LtsPolicyV2, engine_policy: object) -> None:
    active = next(item for item in policy.engine_registry if item.lifecycle is EngineLifecycle.ACTIVE)
    expected: Mapping[str, object] = {
        "runtime_family": active.runtime_family,
        "engine_version": active.engine_version,
        "engine_upstream_commit": active.engine_upstream_commit,
        "manifest_schema_version": active.closure_schema_version,
        "closure_sha256": active.closure_sha256,
        "request_protocol_version": policy.event_api_epoch.request_protocol,
        "event_schema": policy.event_api_epoch.event_schema,
        "semantic_profile": active.semantic_profile,
        "p1_baseline_receipt_sha256": policy.bindings.baseline_receipt.sha256,
    }
    argv_prefix = getattr(engine_policy, "argv_prefix", ())
    expected_python = {"cp312": "/usr/bin/python3.12"}.get(active.python_abi)
    if (
        any(getattr(engine_policy, key, None) != value for key, value in expected.items())
        or policy.event_api_epoch.paper_schema != PAPER_PROTOCOL_SCHEMA
        or policy.event_api_epoch.result_validator != getattr(engine_policy, "result_validator_id", None)
        or expected_python is None
        or not isinstance(argv_prefix, tuple)
        or not argv_prefix
        or argv_prefix[0] != expected_python
    ):
        raise P1LtsPolicyError("selected P1 engine policy is incompatible with the LTS tuple")


def _path_class(path: str) -> P1ChangeClass | None:
    parsed = PurePosixPath(path)
    if not path or "\\" in path or parsed.is_absolute() or ".." in parsed.parts:
        return None
    if path in _CLASS_D_PATHS or path.startswith(_CLASS_D_PREFIXES):
        return P1ChangeClass.D
    if path.startswith("engines/nautilus/") and path.endswith(".json"):
        return P1ChangeClass.C
    if path in _CLASS_C_PATHS or path.startswith(_CLASS_C_PREFIXES):
        return P1ChangeClass.C
    if path.startswith(_CLASS_B_PREFIXES):
        return P1ChangeClass.B
    if path in _CLASS_A_PATHS or path.startswith(_CLASS_A_PREFIXES):
        return P1ChangeClass.A
    return None


def classify_p1_change(changed_paths: Iterable[str], declared_class: P1ChangeClass | str, compatibility_changed: bool) -> P1ImpactDecisionV1:
    declared = P1ChangeClass(declared_class)
    paths = tuple(changed_paths)
    reasons: list[str] = []
    if compatibility_changed:
        reasons.append("compatibility_tuple_changed")
    if not paths:
        reasons.append("no_changed_paths")
    classes: list[P1ChangeClass] = []
    for path in paths:
        inferred = _path_class(path)
        if inferred is None:
            reasons.append("invalid_or_unclassified_path")
            inferred = P1ChangeClass.D
        classes.append(inferred)
    inferred_class = max(classes, key=_CLASS_ORDER.__getitem__) if classes else P1ChangeClass.D
    change_class = max((declared, inferred_class), key=_CLASS_ORDER.__getitem__)
    if compatibility_changed:
        change_class = P1ChangeClass.D
    if change_class is P1ChangeClass.D:
        return P1ImpactDecisionV1(change_class=change_class, disposition=P1ImpactDisposition.HELD, required_node_ids=(), reasons=tuple(reasons or ("class_d_requires_operator_policy",)))
    if _CLASS_ORDER[change_class] > _CLASS_ORDER[declared]:
        reasons.append("source_path_escalated_declared_class")
    if change_class is P1ChangeClass.C:
        reasons.append("closure_rebuild_and_verify_required")
    return P1ImpactDecisionV1(change_class=change_class, disposition=P1ImpactDisposition.QUALIFIABLE, required_node_ids=_CLASS_NODES[change_class], reasons=tuple(reasons))


def classify_changed_paths(changed_paths: Iterable[str]) -> P1ImpactDecisionV1:
    return classify_p1_change(changed_paths, P1ChangeClass.A, False)


__all__ = [
    "CheckpointCompatibility", "EngineLifecycle", "EngineRegistryEntry", "EventApiEpoch",
    "P1ChangeClass", "P1ImpactDecisionV1", "P1ImpactDisposition", "P1LtsPolicyError",
    "P1LtsPolicyV2", "classify_changed_paths", "classify_checkpoint_compatibility",
    "classify_p1_change", "golden_registry_sha256", "load_p1_lts_policy",
    "validate_engine_registry", "validate_p1_lts_identity",
]

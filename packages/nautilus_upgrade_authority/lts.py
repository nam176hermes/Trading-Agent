"""Pure, source-owned LTS policy for the accepted P1 Nautilus lane."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac
import json
from pathlib import Path, PurePosixPath
from typing import cast

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


_EXPECTED_POLICY_SHA256 = "c851425432b3a6a5d14e56bf8810687b39f8ff5e8946df888284224fba4e305c"


class P1LtsPolicyError(ValueError):
    """The P1 LTS policy or its selected runtime identity is invalid."""


class LineageRole(str, Enum):
    BASELINE = "BASELINE"
    CHALLENGER = "CHALLENGER"
    ROLLBACK = "ROLLBACK"


class SourceQualification(str, Enum):
    UNASSESSED = "UNASSESSED"
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"
    STALE = "STALE"


class P1ChangeClass(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class P1ImpactDisposition(str, Enum):
    QUALIFIABLE = "QUALIFIABLE"
    HELD = "HELD"


@dataclass(frozen=True, slots=True)
class P1CompatibilityTupleV1:
    runtime_family: str
    engine_version: str
    engine_upstream_commit: str
    python_abi: str
    candidate_closure_schema_version: int
    candidate_closure_sha256: str
    product_closure_schema_version: int
    product_closure_sha256: str
    request_protocol_version: str
    event_schema: str
    paper_schema: str
    semantic_profile: str
    rollback_version: str
    rollback_upstream_commit: str
    rollback_closure_schema_version: int
    rollback_closure_sha256: str


@dataclass(frozen=True, slots=True)
class P1AuthorityLimitsV1:
    network_authorized: bool
    live_authorized: bool
    production_authorized: bool
    broker_access_authorized: bool
    database_runtime_authorized: bool


@dataclass(frozen=True, slots=True)
class P1EvidenceBindingV1:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class P1ScenarioBindingV1:
    module: str
    object_name: str
    sha256: str


@dataclass(frozen=True, slots=True)
class P1QualificationNodeV1:
    node_id: str
    dependencies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class P1LtsPolicyV1:
    schema: str
    lineage_role: LineageRole
    source_qualification: SourceQualification
    execution_scope: str
    compatibility: P1CompatibilityTupleV1
    authority_limits: P1AuthorityLimitsV1
    candidate_generation: P1EvidenceBindingV1
    baseline_receipt: P1EvidenceBindingV1
    scenarios: P1ScenarioBindingV1
    evidence: tuple[P1EvidenceBindingV1, ...]
    qualification_nodes: tuple[P1QualificationNodeV1, ...]
    record_sha256: str


@dataclass(frozen=True, slots=True)
class P1ImpactDecisionV1:
    change_class: P1ChangeClass
    disposition: P1ImpactDisposition
    required_node_ids: tuple[str, ...]
    reasons: tuple[str, ...]


_POLICY_KEYS = {
    "schema",
    "lineage_role",
    "source_qualification",
    "execution_scope",
    "compatibility",
    "authority_limits",
    "candidate_generation",
    "baseline_receipt",
    "scenarios",
    "evidence",
    "qualification_nodes",
}
_COMPATIBILITY_KEYS = set(P1CompatibilityTupleV1.__dataclass_fields__)
_AUTHORITY_KEYS = set(P1AuthorityLimitsV1.__dataclass_fields__)
_BINDING_KEYS = {"path", "sha256"}
_SCENARIO_KEYS = {"module", "object_name", "sha256"}
_NODE_KEYS = {"node_id", "dependencies"}

_CLASS_NODES = {
    P1ChangeClass.A: (
        "P1S_SOURCE",
        "P1S_RECOVERY",
        "P1S_GOLDEN",
        "P1H_FOUNDATION",
    ),
    P1ChangeClass.B: (
        "P1S_SOURCE",
        "P1S_RECOVERY",
        "P1S_GOLDEN",
        "P1N_G1",
        "P1N_E2E",
        "P1N_PAPER",
        "P1H_FOUNDATION",
        "P1O_ACCEPT",
    ),
    P1ChangeClass.C: (
        "P1S_SOURCE",
        "P1S_RECOVERY",
        "P1S_GOLDEN",
        "P1N_G1",
        "P1N_E2E",
        "P1N_PAPER",
        "P1H_FOUNDATION",
        "P1O_ACCEPT",
    ),
}
_CLASS_ORDER = {
    P1ChangeClass.A: 0,
    P1ChangeClass.B: 1,
    P1ChangeClass.C: 2,
    P1ChangeClass.D: 3,
}
_CLASS_D_PREFIXES = (
    "packages/engine_contracts/",
    "packages/nautilus_runtime_contracts/",
)
_CLASS_D_PATHS = {
    "docs/implementation/p1-real-nautilus/lts/p1-engine-lts-policy-v1.json",
    "packages/nautilus_upgrade_authority/lts.py",
}
_CLASS_C_PATHS = {"packages/domain/recovery.py", "pyproject.toml", "uv.lock"}
_CLASS_C_PREFIXES = (
    "engines/nautilus/candidates/",
    "engines/nautilus/native_entry_guard/",
    "engines/nautilus/sealed_uv_exec/",
    "scripts/build_nautilus",
    "scripts/materialize_nautilus_runtime_closure.py",
    "scripts/verify_nautilus_runtime_closure.py",
)
_CLASS_B_PREFIXES = (
    "engines/nautilus/",
    "packages/nautilus_backtest/",
    "services/job_worker/",
    "services/paper_runtime/",
)
_CLASS_A_PATHS = {"Makefile", "scripts/qualify_p1_engine_lts.py"}
_CLASS_A_PREFIXES = (
    "docs/",
    "tests/",
    "packages/nautilus_upgrade_authority/",
)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


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
        value = json.loads(
            raw,
            object_pairs_hook=no_duplicates,
            parse_float=reject_float,
            parse_constant=reject_float,
        )
    except P1LtsPolicyError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P1LtsPolicyError("P1 LTS policy bytes are invalid JSON") from exc
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise P1LtsPolicyError("P1 LTS policy bytes are not canonical")
    return cast(dict[str, object], value)


def _exact_object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise P1LtsPolicyError("P1 LTS policy shape is invalid")
    return cast(dict[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise P1LtsPolicyError("P1 LTS policy shape is invalid")
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise P1LtsPolicyError("P1 LTS policy shape is invalid")
    return cast(int, value)


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise P1LtsPolicyError("P1 LTS policy shape is invalid")
    return cast(bool, value)


def _binding(value: object) -> P1EvidenceBindingV1:
    item = _exact_object(value, _BINDING_KEYS)
    return P1EvidenceBindingV1(
        path=_string(item["path"]),
        sha256=_string(item["sha256"]),
    )


def load_p1_lts_policy(path: Path) -> P1LtsPolicyV1:
    """Load the one accepted P1 LTS policy from exact canonical bytes."""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise P1LtsPolicyError("P1 LTS policy is unavailable") from exc
    value = _loads_exact(raw)
    record_sha256 = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(record_sha256, _EXPECTED_POLICY_SHA256):
        raise P1LtsPolicyError("P1 LTS policy SHA-256 is not accepted")
    value = _exact_object(value, _POLICY_KEYS)
    compatibility = _exact_object(value["compatibility"], _COMPATIBILITY_KEYS)
    limits = _exact_object(value["authority_limits"], _AUTHORITY_KEYS)
    scenarios = _exact_object(value["scenarios"], _SCENARIO_KEYS)
    evidence_value = value["evidence"]
    nodes_value = value["qualification_nodes"]
    if not isinstance(evidence_value, list) or not isinstance(nodes_value, list):
        raise P1LtsPolicyError("P1 LTS policy shape is invalid")
    nodes: list[P1QualificationNodeV1] = []
    seen: set[str] = set()
    for raw_node in nodes_value:
        node = _exact_object(raw_node, _NODE_KEYS)
        node_id = _string(node["node_id"])
        dependencies_value = node["dependencies"]
        if not isinstance(dependencies_value, list):
            raise P1LtsPolicyError("P1 LTS policy shape is invalid")
        dependencies = tuple(_string(item) for item in dependencies_value)
        if node_id in seen or not set(dependencies) <= seen:
            raise P1LtsPolicyError("P1 LTS qualification nodes are not a static DAG")
        nodes.append(P1QualificationNodeV1(node_id, dependencies))
        seen.add(node_id)
    try:
        result = P1LtsPolicyV1(
            schema=_string(value["schema"]),
            lineage_role=LineageRole(_string(value["lineage_role"])),
            source_qualification=SourceQualification(_string(value["source_qualification"])),
            execution_scope=_string(value["execution_scope"]),
            compatibility=P1CompatibilityTupleV1(
                runtime_family=_string(compatibility["runtime_family"]),
                engine_version=_string(compatibility["engine_version"]),
                engine_upstream_commit=_string(compatibility["engine_upstream_commit"]),
                python_abi=_string(compatibility["python_abi"]),
                candidate_closure_schema_version=_integer(
                    compatibility["candidate_closure_schema_version"]
                ),
                candidate_closure_sha256=_string(compatibility["candidate_closure_sha256"]),
                product_closure_schema_version=_integer(
                    compatibility["product_closure_schema_version"]
                ),
                product_closure_sha256=_string(compatibility["product_closure_sha256"]),
                request_protocol_version=_string(compatibility["request_protocol_version"]),
                event_schema=_string(compatibility["event_schema"]),
                paper_schema=_string(compatibility["paper_schema"]),
                semantic_profile=_string(compatibility["semantic_profile"]),
                rollback_version=_string(compatibility["rollback_version"]),
                rollback_upstream_commit=_string(compatibility["rollback_upstream_commit"]),
                rollback_closure_schema_version=_integer(
                    compatibility["rollback_closure_schema_version"]
                ),
                rollback_closure_sha256=_string(compatibility["rollback_closure_sha256"]),
            ),
            authority_limits=P1AuthorityLimitsV1(
                network_authorized=_boolean(limits["network_authorized"]),
                live_authorized=_boolean(limits["live_authorized"]),
                production_authorized=_boolean(limits["production_authorized"]),
                broker_access_authorized=_boolean(limits["broker_access_authorized"]),
                database_runtime_authorized=_boolean(limits["database_runtime_authorized"]),
            ),
            candidate_generation=_binding(value["candidate_generation"]),
            baseline_receipt=_binding(value["baseline_receipt"]),
            scenarios=P1ScenarioBindingV1(
                module=_string(scenarios["module"]),
                object_name=_string(scenarios["object_name"]),
                sha256=_string(scenarios["sha256"]),
            ),
            evidence=tuple(_binding(item) for item in evidence_value),
            qualification_nodes=tuple(nodes),
            record_sha256=record_sha256,
        )
    except ValueError as exc:
        raise P1LtsPolicyError("P1 LTS policy enum value is invalid") from exc
    if (
        result.schema != "trading-agent-p1-engine-lts-policy/v1"
        or result.execution_scope != "PAPER_LOCAL_ONLY"
        or any(
            (
                result.authority_limits.network_authorized,
                result.authority_limits.live_authorized,
                result.authority_limits.production_authorized,
                result.authority_limits.broker_access_authorized,
                result.authority_limits.database_runtime_authorized,
            )
        )
    ):
        raise P1LtsPolicyError("P1 LTS policy grants invalid authority")
    return result


def validate_p1_lts_identity(policy: P1LtsPolicyV1, engine_policy: object) -> None:
    """Fail closed unless the selected worker policy matches the accepted tuple."""

    expected: Mapping[str, object] = {
        "runtime_family": policy.compatibility.runtime_family,
        "engine_version": policy.compatibility.engine_version,
        "engine_upstream_commit": policy.compatibility.engine_upstream_commit,
        "manifest_schema_version": policy.compatibility.product_closure_schema_version,
        "closure_sha256": policy.compatibility.product_closure_sha256,
        "request_protocol_version": policy.compatibility.request_protocol_version,
        "event_schema": policy.compatibility.event_schema,
        "semantic_profile": policy.compatibility.semantic_profile,
        "p1_baseline_receipt_sha256": policy.baseline_receipt.sha256,
    }
    argv_prefix = getattr(engine_policy, "argv_prefix", ())
    expected_python = {"cp312": "/usr/bin/python3.12"}.get(policy.compatibility.python_abi)
    if (
        any(getattr(engine_policy, key, None) != value for key, value in expected.items())
        or policy.compatibility.paper_schema != PAPER_PROTOCOL_SCHEMA
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


def classify_p1_change(
    changed_paths: Iterable[str],
    declared_class: P1ChangeClass | str,
    compatibility_changed: bool,
) -> P1ImpactDecisionV1:
    """Classify source impact with the declaration as a floor and unknown as held."""

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
        return P1ImpactDecisionV1(
            change_class=change_class,
            disposition=P1ImpactDisposition.HELD,
            required_node_ids=(),
            reasons=tuple(reasons or ("class_d_requires_operator_policy",)),
        )
    if _CLASS_ORDER[change_class] > _CLASS_ORDER[declared]:
        reasons.append("source_path_escalated_declared_class")
    if change_class is P1ChangeClass.C:
        reasons.append("closure_rebuild_and_verify_required")
    return P1ImpactDecisionV1(
        change_class=change_class,
        disposition=P1ImpactDisposition.QUALIFIABLE,
        required_node_ids=_CLASS_NODES[change_class],
        reasons=tuple(reasons),
    )


def classify_changed_paths(changed_paths: Iterable[str]) -> P1ImpactDecisionV1:
    """Fail closed for an undeclared source change."""

    return classify_p1_change(changed_paths, P1ChangeClass.A, False)


__all__ = [
    "CheckpointCompatibility",
    "EngineLifecycle",
    "EngineRegistryEntry",
    "EventApiEpoch",
    "LineageRole",
    "P1ChangeClass",
    "P1CompatibilityTupleV1",
    "P1ImpactDecisionV1",
    "P1ImpactDisposition",
    "P1LtsPolicyError",
    "P1LtsPolicyV1",
    "SourceQualification",
    "classify_changed_paths",
    "classify_checkpoint_compatibility",
    "classify_p1_change",
    "load_p1_lts_policy",
    "golden_registry_sha256",
    "validate_p1_lts_identity",
    "validate_engine_registry",
]

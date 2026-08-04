#!/usr/bin/env python3
"""Validate an exact predeclared layout for disposable PostgreSQL fixtures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Mapping, NamedTuple

from scripts.validate_disposable_postgres_approval import (
    BIND_HOST,
    CLUSTER_NAME,
    DISPOSABLE_DATABASE_NAME,
    FORBIDDEN_PORTS,
    canonical_record_sha256 as approval_record_sha256,
)


_TOP_FIELDS = frozenset(
    {
        "record_kind",
        "schema_version",
        "source",
        "approval_record_sha256",
        "validity",
        "greenlight",
        "constraints",
        "slots",
        "canonical_record_sha256",
    }
)
_SOURCE_FIELDS = frozenset({"commit", "tree"})
_VALIDITY_FIELDS = frozenset({"approved_at_utc", "expires_at_utc"})
_GREENLIGHT_FIELDS = frozenset(
    {"decision", "operator_identity", "approved_at_utc", "operation_lifecycles"}
)
_OPERATION_LIFECYCLE_FIELDS = frozenset(
    {"test_path", "operation_id", "lifecycle_actions"}
)
_CONSTRAINT_FIELDS = frozenset(
    {
        "bind_host",
        "cluster_name",
        "database_name",
        "forbidden_ports",
        "port_allocation",
    }
)
_SLOT_FIELDS = frozenset(
    {"test_path", "operation_id", "ordinal", "root", "pgdata", "port"}
)
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{2,127}$")
_ROOT = re.compile(r"^/tmp/phase4-postgres-[A-Za-z0-9._-]+$")
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
_LIFECYCLE_ACTIONS_BY_KIND = {
    "MIGRATE": ("INITDB", "START", "MIGRATE", "STOP", "DELETE"),
    "RESTORE": ("INITDB", "START", "RESTORE", "STOP", "DELETE"),
}
_VALID_LIFECYCLE_ACTIONS = frozenset(_LIFECYCLE_ACTIONS_BY_KIND.values())


class DisposablePostgresFixturePlanRejected(ValueError):
    pass


class DisposablePostgresFixtureSlot(NamedTuple):
    test_path: str
    operation_id: str
    ordinal: int
    root: str
    pgdata: str
    port: int
    lifecycle_actions: tuple[str, ...]


def lifecycle_actions_for(kind: str) -> tuple[str, ...]:
    try:
        return _LIFECYCLE_ACTIONS_BY_KIND[kind]
    except KeyError as error:
        raise ValueError("fixture lifecycle kind is not supported") from error


def _reject(message: str) -> None:
    raise DisposablePostgresFixturePlanRejected(message)


def _exact_mapping(
    value: object,
    fields: frozenset[str],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        _reject(f"{label} fields are missing or unknown")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        _reject(f"{label} is not an exact UTC timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _reject(f"{label} is not a real UTC timestamp")


def _valid_test_path(value: object) -> bool:
    if not isinstance(value, str) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        value == path.as_posix()
        and not path.is_absolute()
        and len(path.parts) >= 2
        and path.parts[0] == "tests"
        and ".." not in path.parts
        and path.suffix == ".py"
    )


def canonical_record_sha256(record: Mapping[str, object]) -> str:
    unsigned = {
        key: value for key, value in record.items() if key != "canonical_record_sha256"
    }
    encoded = json.dumps(
        unsigned,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_disposable_postgres_fixture_plan(
    record: Mapping[str, object],
    approval_record: Mapping[str, object],
    *,
    source_commit: str,
    source_tree: str,
    now: datetime,
) -> tuple[DisposablePostgresFixtureSlot, ...]:
    document = _exact_mapping(record, _TOP_FIELDS, "fixture plan")
    if document["record_kind"] != "DISPOSABLE_POSTGRES_FIXTURE_PLAN":
        _reject("fixture plan kind is invalid")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        _reject("fixture plan schema version is invalid")

    source = _exact_mapping(document["source"], _SOURCE_FIELDS, "fixture plan source")
    if (
        not isinstance(source_commit, str)
        or _GIT_OBJECT.fullmatch(source_commit) is None
        or source["commit"] != source_commit
        or not isinstance(source_tree, str)
        or _GIT_OBJECT.fullmatch(source_tree) is None
        or source["tree"] != source_tree
    ):
        _reject("fixture plan source identity does not match")

    expected_approval_digest = approval_record_sha256(approval_record)
    if document["approval_record_sha256"] != expected_approval_digest:
        _reject("fixture plan approval digest does not match")

    validity = _exact_mapping(document["validity"], _VALIDITY_FIELDS, "fixture plan validity")
    approved_at = _timestamp(validity["approved_at_utc"], "fixture plan approval time")
    expires_at = _timestamp(validity["expires_at_utc"], "fixture plan expiry time")
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
        or expires_at <= approved_at
        or expires_at - approved_at > timedelta(hours=24)
        or not approved_at <= now.astimezone(UTC) < expires_at
    ):
        _reject("fixture plan validity window is invalid")
    approval_validity = approval_record.get("validity")
    if validity != approval_validity:
        _reject("fixture plan does not share the approval validity window")

    greenlight = _exact_mapping(
        document["greenlight"], _GREENLIGHT_FIELDS, "fixture plan greenlight"
    )
    if greenlight["decision"] != "APPROVED":
        _reject("fixture plan Greenlight is not approved")
    identity = greenlight["operator_identity"]
    if not isinstance(identity, str) or _IDENTITY.fullmatch(identity) is None:
        _reject("fixture plan Greenlight operator is invalid")
    approval_review = approval_record.get("review")
    if (
        not isinstance(approval_review, dict)
        or identity != approval_review.get("operator_identity")
    ):
        _reject("fixture plan Greenlight operator does not match approval")
    greenlight_at = _timestamp(
        greenlight["approved_at_utc"], "fixture plan Greenlight time"
    )
    if greenlight_at < approved_at or greenlight_at > now.astimezone(UTC):
        _reject("fixture plan Greenlight time is invalid")
    constraints = _exact_mapping(
        document["constraints"], _CONSTRAINT_FIELDS, "fixture plan constraints"
    )
    if constraints != {
        "bind_host": BIND_HOST,
        "cluster_name": CLUSTER_NAME,
        "database_name": DISPOSABLE_DATABASE_NAME,
        "forbidden_ports": list(FORBIDDEN_PORTS),
        "port_allocation": "EXPLICITLY_APPROVED",
    }:
        _reject("fixture plan constraints are not exact")

    approved_operations = approval_record.get("approved_operations")
    if not isinstance(approved_operations, list):
        _reject("fixture plan approval operations are unavailable")
    approved_pairs = {
        (item.get("test_path"), item.get("operation_id"))
        for item in approved_operations
        if isinstance(item, dict)
    }
    raw_slots = document["slots"]
    if not isinstance(raw_slots, list) or not 1 <= len(raw_slots) <= 256:
        _reject("fixture plan slots are missing or excessive")
    slot_data: list[tuple[str, str, int, str, str, int]] = []
    roots: set[str] = set()
    ports: set[int] = set()
    for index, raw_slot in enumerate(raw_slots):
        slot = _exact_mapping(raw_slot, _SLOT_FIELDS, f"fixture plan slot {index}")
        test_path = slot["test_path"]
        operation_id = slot["operation_id"]
        ordinal = slot["ordinal"]
        root = slot["root"]
        pgdata = slot["pgdata"]
        port = slot["port"]
        if (
            not _valid_test_path(test_path)
            or not isinstance(operation_id, str)
            or _OPERATION_ID.fullmatch(operation_id) is None
            or (test_path, operation_id) not in approved_pairs
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal < 1
            or not isinstance(root, str)
            or _ROOT.fullmatch(root) is None
            or PurePosixPath(root).parent != PurePosixPath("/tmp")
            or pgdata != f"{root}/data"
            or isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
            or port in FORBIDDEN_PORTS
        ):
            _reject("fixture plan slot is invalid")
        if root in roots or port in ports:
            _reject("fixture plan roots and ports must be unique")
        roots.add(root)
        ports.add(port)
        slot_data.append((test_path, operation_id, ordinal, root, pgdata, port))
    canonical_slot_data = sorted(slot_data, key=lambda item: item[:3])
    if slot_data != canonical_slot_data:
        _reject("fixture plan slots are not in canonical order")
    by_pair: dict[tuple[str, str], list[int]] = {}
    for test_path, operation_id, ordinal, _root, _pgdata, _port in slot_data:
        by_pair.setdefault((test_path, operation_id), []).append(ordinal)
    if any(ordinals != list(range(1, len(ordinals) + 1)) for ordinals in by_pair.values()):
        _reject("fixture plan slot ordinals are not contiguous")

    slot_pairs = tuple(sorted(by_pair))
    raw_lifecycles = greenlight["operation_lifecycles"]
    if not isinstance(raw_lifecycles, list) or len(raw_lifecycles) != len(slot_pairs):
        _reject("fixture plan operation lifecycles are incomplete")
    lifecycle_by_pair: dict[tuple[str, str], tuple[str, ...]] = {}
    lifecycle_pairs: list[tuple[str, str]] = []
    for index, raw_lifecycle in enumerate(raw_lifecycles):
        lifecycle = _exact_mapping(
            raw_lifecycle,
            _OPERATION_LIFECYCLE_FIELDS,
            f"fixture plan operation lifecycle {index}",
        )
        test_path = lifecycle["test_path"]
        operation_id = lifecycle["operation_id"]
        actions = lifecycle["lifecycle_actions"]
        pair = (test_path, operation_id)
        if (
            not _valid_test_path(test_path)
            or not isinstance(operation_id, str)
            or _OPERATION_ID.fullmatch(operation_id) is None
            or pair not in approved_pairs
            or not isinstance(actions, list)
            or not all(isinstance(action, str) for action in actions)
            or tuple(actions) not in _VALID_LIFECYCLE_ACTIONS
            or pair in lifecycle_by_pair
        ):
            _reject("fixture plan operation lifecycle is invalid")
        lifecycle_pairs.append(pair)
        lifecycle_by_pair[pair] = tuple(actions)
    if tuple(lifecycle_pairs) != slot_pairs:
        _reject("fixture plan operation lifecycles are not canonical or exact")

    slots = tuple(
        DisposablePostgresFixtureSlot(
            test_path,
            operation_id,
            ordinal,
            root,
            pgdata,
            port,
            lifecycle_by_pair[(test_path, operation_id)],
        )
        for test_path, operation_id, ordinal, root, pgdata, port in slot_data
    )

    digest = document["canonical_record_sha256"]
    if (
        not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
        or digest != canonical_record_sha256(document)
    ):
        _reject("fixture plan canonical digest does not match")
    return slots


def load_protected_fixture_plan(path: Path) -> dict[str, object]:
    if not path.is_absolute():
        _reject("fixture plan path must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _reject("fixture plan cannot be opened as a protected file")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or not 1 <= metadata.st_size <= 65536
        ):
            _reject("fixture plan protected-file invariant failed")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
    finally:
        os.close(descriptor)

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                _reject("fixture plan contains a duplicate member")
            result[key] = value
        return result

    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        _reject("fixture plan is not strict UTF-8 JSON")
    if not isinstance(value, dict):
        _reject("fixture plan top level is not an object")
    return value

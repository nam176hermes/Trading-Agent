from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest

from scripts.validate_disposable_postgres_fixture_plan import (
    DisposablePostgresFixturePlanRejected,
    canonical_record_sha256,
    load_protected_fixture_plan,
    validate_disposable_postgres_fixture_plan,
)
from tests.jobs.test_disposable_postgres_approval import (
    COMMIT,
    NOW,
    OPERATION_ID,
    TEST_PATH,
    TREE,
    build_record,
)
from scripts.validate_disposable_postgres_approval import (
    canonical_record_sha256 as approval_record_sha256,
)


def _plan() -> dict[str, object]:
    approval = build_record()
    document: dict[str, object] = {
        "record_kind": "DISPOSABLE_POSTGRES_FIXTURE_PLAN",
        "schema_version": 1,
        "source": {"commit": COMMIT, "tree": TREE},
        "approval_record_sha256": approval_record_sha256(approval),
        "validity": {
            "approved_at_utc": "2026-07-16T17:00:00Z",
            "expires_at_utc": "2026-07-16T19:00:00Z",
        },
        "greenlight": {
            "decision": "APPROVED",
            "operator_identity": "operator.example",
            "approved_at_utc": "2026-07-16T17:30:00Z",
            "lifecycle_actions": [
                "INITDB",
                "START",
                "RESTORE",
                "STOP",
                "DELETE",
            ],
        },
        "constraints": {
            "bind_host": "127.0.0.1",
            "cluster_name": "trading-agent-disposable-tests",
            "database_name": "trading_agent_disposable_test",
            "forbidden_ports": [3002, 8401, 55432],
            "port_allocation": "EXPLICITLY_APPROVED",
        },
        "slots": [
            {
                "test_path": TEST_PATH,
                "operation_id": OPERATION_ID,
                "ordinal": 1,
                "root": "/tmp/phase4-postgres-fixture-plan-01",
                "pgdata": "/tmp/phase4-postgres-fixture-plan-01/data",
                "port": 49152,
            },
            {
                "test_path": TEST_PATH,
                "operation_id": OPERATION_ID,
                "ordinal": 2,
                "root": "/tmp/phase4-postgres-fixture-plan-02",
                "pgdata": "/tmp/phase4-postgres-fixture-plan-02/data",
                "port": 49153,
            },
        ],
        "canonical_record_sha256": "0" * 64,
    }
    document["canonical_record_sha256"] = canonical_record_sha256(document)
    return document


def _validate(document: dict[str, object]):
    return validate_disposable_postgres_fixture_plan(
        document,
        build_record(),
        source_commit=COMMIT,
        source_tree=TREE,
        now=NOW,
    )


def _refresh(document: dict[str, object]) -> None:
    document["canonical_record_sha256"] = canonical_record_sha256(document)


def test_exact_fixture_plan_binds_two_predeclared_slots() -> None:
    slots = _validate(_plan())
    assert [slot.ordinal for slot in slots] == [1, 2]
    assert [slot.port for slot in slots] == [49152, 49153]
    assert all(slot.root.startswith("/tmp/phase4-postgres-") for slot in slots)


@pytest.mark.parametrize(
    "case",
    (
        "approval_digest",
        "source",
        "lifecycle",
        "runtime_port",
        "runtime_root",
        "duplicate_port",
        "ordinal",
        "order",
        "digest",
    ),
)
def test_fixture_plan_rejects_identity_lifecycle_and_slot_drift(case: str) -> None:
    document = _plan()
    if case == "approval_digest":
        document["approval_record_sha256"] = "f" * 64
    elif case == "source":
        document["source"]["tree"] = "f" * 40  # type: ignore[index]
    elif case == "lifecycle":
        document["greenlight"]["lifecycle_actions"] = ["START"]  # type: ignore[index]
    elif case == "runtime_port":
        document["slots"][0]["port"] = 55432  # type: ignore[index]
    elif case == "runtime_root":
        document["slots"][0]["root"] = "/var/lib/postgresql/runtime"  # type: ignore[index]
    elif case == "duplicate_port":
        document["slots"][1]["port"] = 49152  # type: ignore[index]
    elif case == "ordinal":
        document["slots"][1]["ordinal"] = 3  # type: ignore[index]
    elif case == "order":
        document["slots"].reverse()  # type: ignore[union-attr]
    elif case == "digest":
        document["canonical_record_sha256"] = "f" * 64
    if case != "digest":
        _refresh(document)
    with pytest.raises(DisposablePostgresFixturePlanRejected):
        _validate(document)


def test_fixture_plan_loader_requires_private_regular_file() -> None:
    with tempfile.TemporaryDirectory(
        prefix="fixture-plan-loader-",
        dir="/tmp",
    ) as raw:
        path = Path(raw) / "fixture-plan.json"
        path.write_text(json.dumps(_plan()), encoding="utf-8")
        path.chmod(0o600)
        assert load_protected_fixture_plan(path) == _plan()
        path.chmod(0o644)
        with pytest.raises(DisposablePostgresFixturePlanRejected):
            load_protected_fixture_plan(path)

from __future__ import annotations

import ast
import os
import re
import socket
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain import EventEnvelope, FillEvent, FillReportStatus, OrderEvent, OrderQuantity, OrderStatus
from packages.event_ledger import InMemoryEventLedger, OutboxIntent, serialize_event
from packages.execution_sandbox import (
    SandboxCommandKind,
    SandboxCommandPlan,
    SandboxConnectionState,
    SandboxExecutionClient,
    SandboxExecutionError,
    SandboxLostResponse,
    SandboxReportPlan,
    SandboxResponseDisposition,
    SandboxScenario,
    SandboxSubmitRequest,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})
_SANDBOX_REFERENCE = re.compile(r"execution(?:_|-)sandbox", flags=re.IGNORECASE)
_NONPRODUCTION_SOURCE_PARTS = frozenset(
    {"tests", "test", "generated", "__generated__", ".next", "node_modules", "__pycache__"}
)
_FORBIDDEN_IMPORTS = (
    "socket", "ssl", "http", "urllib", "requests", "websockets", "subprocess",
    "asyncio", "threading", "multiprocessing", "sqlite3", "sqlalchemy", "psycopg",
    "psycopg2", "asyncpg", "packages.runtime_release", "packages.nautilus_",
    "packages.provider_", "packages.execution_provider", "packages.paper_",
    "packages.live_", "services.paper_runtime", "services.provider_",
    "services.execution_provider", "services.live_",
)
_PROCESS_ENTRY_POINTS = (
    "system", "posix_spawn", "posix_spawnp", "spawnl", "spawnle", "spawnlp",
    "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
)


class ExactSafetyVerifier:
    def verify(self, *, observation: object) -> object:
        return observation


def uid(value: int) -> UUID:
    return UUID(int=value)


def _activation_source_roots(root: Path) -> tuple[Path, ...]:
    return tuple(root / name for name in ("apps", "services", "packages"))


def _is_production_source(source: Path) -> bool:
    return (
        source.is_file()
        and source.suffix in _SOURCE_SUFFIXES
        and not any(part in _NONPRODUCTION_SOURCE_PARTS for part in source.parts)
        and "execution_sandbox" not in source.parts
    )


def _registration_references(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    sources = (
        source
        for root in roots
        for source in root.rglob("*")
        if _is_production_source(source)
    )
    return tuple(
        source
        for source in sorted(sources)
        if _SANDBOX_REFERENCE.search(source.read_text(encoding="utf-8"))
    )


def _import_candidates(tree: ast.AST) -> tuple[str, ...]:
    candidates: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            candidates.append(node.module)
            candidates.extend(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if alias.name != "*"
            )
    return tuple(candidates)


def _import_bindings(tree: ast.AST) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bindings[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bindings


def _call_candidate(call: ast.Call, bindings: dict[str, str]) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        return bindings.get(function.id)
    if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
        return f"{bindings.get(function.value.id, function.value.id)}.{function.attr}"
    return None


def _is_forbidden_import(candidate: str) -> bool:
    return any(
        candidate == blocked
        or candidate.startswith(f"{blocked}.")
        or (blocked.endswith("_") and candidate.startswith(blocked))
        for blocked in _FORBIDDEN_IMPORTS
    )


def _is_forbidden_process_entry(candidate: str) -> bool:
    return candidate in {"os.system", "os.posix_spawn", "os.posix_spawnp"} or candidate.startswith("os.spawn")


def _forbidden_source_references(source: str) -> tuple[str, ...]:
    tree = ast.parse(source)
    bindings = _import_bindings(tree)
    references = [
        candidate for candidate in _import_candidates(tree) if _is_forbidden_import(candidate)
    ]
    references.extend(
        candidate
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for candidate in (_call_candidate(node, bindings),)
        if candidate is not None and _is_forbidden_process_entry(candidate)
    )
    return tuple(dict.fromkeys(references))


def order_report(
    source: EventEnvelope[OrderEvent], *, report_id: int, event_id: int, sequence: int, status: OrderStatus
) -> SandboxReportPlan:
    event = EventEnvelope[OrderEvent](
        **{
            **source.model_dump(mode="python"),
            "event_id": uid(event_id),
            "sequence": sequence,
            "payload": OrderEvent.create(
                event_id=uid(10_000 + event_id),
                order_id=uid(1),
                sequence=sequence,
                target_status=status,
                occurred_at=NOW,
            ),
        }
    )
    return SandboxReportPlan(report_id=uid(report_id), deliver_at=NOW, event=event)


def partial_fill_report(source: EventEnvelope[FillEvent]) -> SandboxReportPlan:
    event = EventEnvelope[FillEvent](
        **{
            **source.model_dump(mode="python"),
            "event_id": uid(102),
            "sequence": 3,
            "payload": FillEvent(
                **{
                    **{name: getattr(source.payload, name) for name in FillEvent.model_fields},
                    "execution_id": uid(20_102),
                    "report_sequence": 1,
                    "venue_trade_id": "partial-fill",
                    "status": FillReportStatus.PARTIALLY_FILLED,
                    "quantity": OrderQuantity(Decimal("0.500"), 3),
                    "cumulative_fill_quantity": OrderQuantity(Decimal("0.500"), 3),
                    "leaves_quantity": OrderQuantity(Decimal("0.500"), 3),
                }
            ),
        }
    )
    return SandboxReportPlan(report_id=uid(22), deliver_at=NOW, event=event)


def complete_scenario(
    submitted_envelope: EventEnvelope[OrderEvent], fill_envelope: EventEnvelope[FillEvent]
) -> SandboxScenario:
    return SandboxScenario(
        command_plans=(
            SandboxCommandPlan(
                command_id=uid(100),
                kind=SandboxCommandKind.SUBMIT,
                response_disposition=SandboxResponseDisposition.LOST_RESPONSE,
                order_id=uid(1),
                report_ids=(uid(20), uid(21), uid(22), uid(23)),
            ),
        ),
        report_plans=(
            order_report(submitted_envelope, report_id=20, event_id=100, sequence=1, status=OrderStatus.SUBMITTED),
            order_report(submitted_envelope, report_id=21, event_id=101, sequence=2, status=OrderStatus.PARTIALLY_FILLED),
            partial_fill_report(fill_envelope),
            SandboxReportPlan(report_id=uid(23), deliver_at=NOW, duplicate_of_report_id=uid(22)),
        ),
    )


def submit_request(prepared_case: Any) -> SandboxSubmitRequest:
    return SandboxSubmitRequest(
        command_id=uid(100),
        order_id=uid(1),
        order_intent=prepared_case.intent,
        permit=prepared_case.permit,
        current_observation=prepared_case.observation,
        current_policy=prepared_case.policy,
        current_safety=prepared_case.safety,
        consumed_event_id=uid(9_001),
        submitted_at=NOW + timedelta(seconds=1),
    )


def fresh_prepared_case(prepared_case: Any) -> Any:
    ledger = InMemoryEventLedger()
    outbox_by_event_id = {
        outbox.event_id: outbox for outbox in prepared_case.ledger.load_outbox()
    }
    for event in prepared_case.ledger.load_events():
        outbox = outbox_by_event_id[event.event_id]
        ledger.append(event, OutboxIntent(**outbox.model_dump(mode="python")))
    return replace(prepared_case, ledger=ledger)


def run_complete_script(
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
    fill_envelope: EventEnvelope[FillEvent],
) -> tuple[object, tuple[EventEnvelope[object], ...], tuple[EventEnvelope[object], ...]]:
    client = SandboxExecutionClient(
        repository=prepared_case.ledger,
        safety_verifier=ExactSafetyVerifier(),
        scenario=complete_scenario(submitted_envelope, fill_envelope),
        initial_time=NOW,
    )
    with pytest.raises(SandboxLostResponse):
        client.submit(submit_request(prepared_case))
    delivered = client.drain_reports()
    return client.snapshot(), delivered, client.drain_reports()


def test_execution_sandbox_ast_has_no_transport_process_or_runtime_imports() -> None:
    violations: list[str] = []
    for source in sorted((PACKAGE_ROOT / "packages" / "execution_sandbox").rglob("*.py")):
        violations.extend(
            f"{source.relative_to(PACKAGE_ROOT)}: {reference}"
            for reference in _forbidden_source_references(source.read_text(encoding="utf-8"))
        )
    assert violations == []


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("import sqlite3\n", ("sqlite3",)),
        ("from packages import runtime_release\n", ("packages.runtime_release",)),
        ("from packages import nautilus_backtest\n", ("packages.nautilus_backtest",)),
        ("from packages import execution_provider\n", ("packages.execution_provider",)),
        ("from services import paper_runtime\n", ("services.paper_runtime",)),
        ("from services import live_runtime\n", ("services.live_runtime",)),
        ("import os\nos.system('blocked')\n", ("os.system",)),
        ("from os import system\nsystem('blocked')\n", ("os.system",)),
        ("import os\nos.posix_spawn('x', (), {})\n", ("os.posix_spawn",)),
        ("from os import posix_spawn\nposix_spawn('x', (), {})\n", ("os.posix_spawn",)),
    ),
)
def test_forbidden_detector_rejects_direct_from_import_and_process_entries(
    source: str, expected: tuple[str, ...]
) -> None:
    assert _forbidden_source_references(source) == expected


@pytest.mark.parametrize("reference", ("execution_sandbox", "execution-sandbox"))
def test_registration_proof_catches_nested_dashboard_references(
    tmp_path: Path, reference: str
) -> None:
    dashboard_route = tmp_path / "apps" / "dashboard" / "src" / "app" / "api" / "route.ts"
    dashboard_route.parent.mkdir(parents=True)
    dashboard_route.write_text(
        f'registerAdapter("{reference}")\n', encoding="utf-8"
    )

    assert _registration_references((tmp_path / "apps" / "dashboard",)) == (
        dashboard_route,
    )


def test_registration_proof_catches_multiple_activation_surfaces(tmp_path: Path) -> None:
    dashboard_route = tmp_path / "apps" / "dashboard" / "src" / "app" / "route.ts"
    provider_module = tmp_path / "services" / "paper_runtime" / "provider.py"
    dashboard_route.parent.mkdir(parents=True)
    provider_module.parent.mkdir(parents=True)
    dashboard_route.write_text('register("execution-sandbox")\n', encoding="utf-8")
    provider_module.write_text('register("execution_sandbox")\n', encoding="utf-8")

    assert _registration_references((tmp_path / "apps", tmp_path / "services")) == (
        dashboard_route,
        provider_module,
    )


def test_registration_source_roots_exclude_sandbox_tests_and_generated_files(tmp_path: Path) -> None:
    active = tmp_path / "packages" / "runtime_release" / "registration.py"
    sandbox = tmp_path / "packages" / "execution_sandbox" / "client.py"
    test_source = tmp_path / "apps" / "control_api" / "tests" / "test_sandbox.py"
    generated = tmp_path / "services" / "job_worker" / "generated" / "types.ts"
    for source in (active, sandbox, test_source, generated):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('register("execution_sandbox")\n', encoding="utf-8")

    assert _registration_references(_activation_source_roots(tmp_path)) == (active,)


def test_equivalent_runs_produce_identical_snapshots_and_event_bytes(
    monkeypatch: pytest.MonkeyPatch,
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
    fill_envelope: EventEnvelope[FillEvent],
) -> None:
    def forbidden_io(*_: object, **__: object) -> None:
        raise AssertionError("sandbox attempted external I/O")

    monkeypatch.setattr(socket, "socket", forbidden_io)
    monkeypatch.setattr(subprocess, "Popen", forbidden_io)
    monkeypatch.setattr(subprocess, "run", forbidden_io)
    monkeypatch.setattr(subprocess, "call", forbidden_io)
    monkeypatch.setattr(subprocess, "check_call", forbidden_io)
    monkeypatch.setattr(subprocess, "check_output", forbidden_io)
    for entry_point in _PROCESS_ENTRY_POINTS:
        if hasattr(os, entry_point):
            monkeypatch.setattr(os, entry_point, forbidden_io)

    left_snapshot, left_delivered, left_remaining = run_complete_script(
        fresh_prepared_case(prepared_case), submitted_envelope, fill_envelope
    )
    right_snapshot, right_delivered, right_remaining = run_complete_script(
        fresh_prepared_case(prepared_case), submitted_envelope, fill_envelope
    )

    assert left_snapshot == right_snapshot
    assert left_remaining == right_remaining == ()
    assert [serialize_event(event) for event in left_delivered] == [
        serialize_event(event) for event in right_delivered
    ]


def test_disconnected_client_rejects_planned_submit_without_consuming_command(
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    scenario = SandboxScenario(
        command_plans=(
            SandboxCommandPlan(
                command_id=uid(70), kind=SandboxCommandKind.DISCONNECT,
                response_disposition=SandboxResponseDisposition.ACKNOWLEDGED,
                order_id=uid(1), report_ids=(uid(20),),
            ),
            SandboxCommandPlan(
                command_id=uid(71), kind=SandboxCommandKind.RECONNECT,
                response_disposition=SandboxResponseDisposition.ACKNOWLEDGED,
                order_id=uid(1), report_ids=(uid(21),),
            ),
            SandboxCommandPlan(
                command_id=uid(100), kind=SandboxCommandKind.SUBMIT,
                response_disposition=SandboxResponseDisposition.ACKNOWLEDGED,
                order_id=uid(1), report_ids=(uid(22),),
            ),
        ),
        report_plans=(
            order_report(submitted_envelope, report_id=20, event_id=100, sequence=1, status=OrderStatus.SUBMITTED),
            order_report(submitted_envelope, report_id=21, event_id=101, sequence=2, status=OrderStatus.ACCEPTED),
            order_report(submitted_envelope, report_id=22, event_id=102, sequence=1, status=OrderStatus.SUBMITTED),
        ),
    )
    client = SandboxExecutionClient(
        repository=prepared_case.ledger,
        safety_verifier=ExactSafetyVerifier(),
        scenario=scenario,
        initial_time=NOW,
    )
    client.disconnect(command_id=uid(70), at=NOW)
    before = client.snapshot()
    request = submit_request(prepared_case)
    with pytest.raises(SandboxExecutionError, match="sandbox is disconnected"):
        client.submit(request)
    assert client.snapshot() == before

    client.reconnect(command_id=uid(71), at=NOW)
    client.submit(request)
    assert client.snapshot().orders[0].order_id == request.order_id
    assert client.snapshot().connection_state is SandboxConnectionState.CONNECTED


def test_client_rejects_backwards_connection_timestamp_without_mutation(
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    scenario = SandboxScenario(
        command_plans=(
            SandboxCommandPlan(
                command_id=uid(70), kind=SandboxCommandKind.DISCONNECT,
                response_disposition=SandboxResponseDisposition.ACKNOWLEDGED,
                order_id=uid(1), report_ids=(uid(20),),
            ),
        ),
        report_plans=(order_report(submitted_envelope, report_id=20, event_id=100, sequence=1, status=OrderStatus.SUBMITTED),),
    )
    client = SandboxExecutionClient(
        repository=prepared_case.ledger,
        safety_verifier=ExactSafetyVerifier(),
        scenario=scenario,
        initial_time=NOW,
    )
    client.advance_time(to=NOW + timedelta(seconds=1))
    before = client.snapshot()

    with pytest.raises(SandboxExecutionError, match="clock cannot move backwards"):
        client.disconnect(command_id=uid(70), at=NOW)

    assert client.snapshot() == before


def test_snapshot_and_reports_are_immutable_and_each_declared_report_is_consumed_once(
    prepared_case: Any,
    submitted_envelope: EventEnvelope[OrderEvent],
    fill_envelope: EventEnvelope[FillEvent],
) -> None:
    snapshot, delivered, remaining = run_complete_script(
        prepared_case, submitted_envelope, fill_envelope
    )
    with pytest.raises(ValidationError):
        snapshot.current_time = NOW  # type: ignore[misc]
    assert snapshot.queued_reports == ()
    assert [event.event_id for event in delivered] == [uid(100), uid(101), uid(102), uid(102)]
    assert remaining == ()


def test_no_engine_job_dashboard_or_provider_registers_the_sandbox() -> None:
    roots = _activation_source_roots(PACKAGE_ROOT)
    assert all(root.is_dir() for root in roots)
    assert _registration_references(roots) == ()

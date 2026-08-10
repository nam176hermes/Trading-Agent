from __future__ import annotations

import ast
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
    SandboxCancelRequest,
    SandboxCommandKind,
    SandboxCommandPlan,
    SandboxConnectionState,
    SandboxExecutionClient,
    SandboxExecutionError,
    SandboxLostResponse,
    SandboxModifyRequest,
    SandboxReportPlan,
    SandboxResponseDisposition,
    SandboxScenario,
    SandboxSubmitRequest,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]


class ExactSafetyVerifier:
    def verify(self, *, observation: object) -> object:
        return observation


def uid(value: int) -> UUID:
    return UUID(int=value)


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
) -> tuple[object, tuple[EventEnvelope[object], ...]]:
    client = SandboxExecutionClient(
        repository=prepared_case.ledger,
        safety_verifier=ExactSafetyVerifier(),
        scenario=complete_scenario(submitted_envelope, fill_envelope),
        initial_time=NOW,
    )
    with pytest.raises(SandboxLostResponse):
        client.submit(submit_request(prepared_case))
    delivered = client.drain_reports()
    return client.snapshot(), delivered


def test_execution_sandbox_ast_has_no_transport_process_or_runtime_imports() -> None:
    forbidden = (
        "socket", "ssl", "http", "urllib", "requests", "websockets", "subprocess", "asyncio",
        "threading", "sqlalchemy", "psycopg", "packages.runtime_release", "packages.nautilus_",
    )
    violations: list[str] = []
    for source in sorted((PACKAGE_ROOT / "packages" / "execution_sandbox").glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            names = (
                (alias.name for alias in node.names)
                if isinstance(node, ast.Import)
                else (node.module,) if isinstance(node, ast.ImportFrom) and node.module else ()
            )
            for name in names:
                if any(name == blocked or name.startswith(f"{blocked}.") or name.startswith(blocked) for blocked in forbidden):
                    violations.append(f"{source.relative_to(PACKAGE_ROOT)}: {name}")
    assert violations == []


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

    left_snapshot, left_delivered = run_complete_script(
        fresh_prepared_case(prepared_case), submitted_envelope, fill_envelope
    )
    right_snapshot, right_delivered = run_complete_script(
        fresh_prepared_case(prepared_case), submitted_envelope, fill_envelope
    )

    assert left_snapshot == right_snapshot
    assert [serialize_event(event) for event in left_delivered] == [
        serialize_event(event) for event in right_delivered
    ]


def test_disconnected_client_has_no_command_backdoor(
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
    client.disconnect(command_id=uid(70), at=NOW)
    before = client.snapshot()
    for operation, request in (
        (client.submit, submit_request(prepared_case)),
        (
            client.modify,
            SandboxModifyRequest(
                command_id=uid(71), order_id=uid(1),
                replacement_order_intent=prepared_case.intent, requested_at=NOW,
            ),
        ),
        (client.cancel, SandboxCancelRequest(command_id=uid(72), order_id=uid(1), requested_at=NOW)),
    ):
        with pytest.raises(SandboxExecutionError):
            operation(request)
        assert client.snapshot() == before
    assert client.snapshot().connection_state is SandboxConnectionState.DISCONNECTED


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
    snapshot, delivered = run_complete_script(prepared_case, submitted_envelope, fill_envelope)
    with pytest.raises(ValidationError):
        snapshot.current_time = NOW  # type: ignore[misc]
    assert len(delivered) == 4
    assert len({serialize_event(event) for event in delivered}) == 3


def test_no_engine_job_dashboard_or_provider_registers_the_sandbox() -> None:
    production_roots = (PACKAGE_ROOT / "packages", PACKAGE_ROOT / "apps", PACKAGE_ROOT / "services")
    references = [
        source.relative_to(PACKAGE_ROOT)
        for root in production_roots
        for source in root.rglob("*.py")
        if source.parent.name != "execution_sandbox"
        if "execution_sandbox" in source.read_text(encoding="utf-8")
    ]
    assert references == []

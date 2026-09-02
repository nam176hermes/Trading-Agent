from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from engines.nautilus.runtime_v1.control_channel import framed_document, iter_payloads
from packages.domain import (
    AccountBalanceSnapshot,
    AssetClass,
    Currency,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    LiquiditySide,
    Money,
    OrderQuantity,
    PortfolioOpeningEntry,
    Price,
    ProductType,
    ReconciliationSource,
)
from packages.engine_contracts import (
    ArtifactReference,
    EngineCommandEnvelope,
    EngineInstrumentId,
    EngineSessionIdentityV1,
    EngineTargetPortfolio,
    EngineTargetPosition,
    RunBacktest,
    StartPaperEngine,
    StopPaperEngine,
    SubmitTargetPortfolio,
    canonical_json_bytes,
    payload_digest,
)
from packages.engine_portfolio_projection.models import ProjectionAuthority
from packages.engine_portfolio_projection.validation import catalog_digest
from packages.nautilus_runtime_contracts.artifacts import P1InstrumentCatalogV1
from packages.nautilus_runtime_contracts.events import (
    P1AccountObserved,
    P1PositionObserved,
    P1RunCompleted,
    P1RunStarted,
    P1TargetAccepted,
    P1TargetQuantityPlanned,
)
from packages.nautilus_runtime_contracts.paper import (
    PAPER_PROTOCOL_SCHEMA,
    PaperCommandFrame,
    PaperSessionJournal,
    paper_request_id,
)
from packages.nautilus_runtime_contracts.semantic import semantic_digest
from packages.safety_evidence import CanonicalKillSwitchState
from services.job_store.engine_event_repository import InMemoryEngineEventLedger
from services.job_worker.safety import SafetyMode
from services.job_worker.safety_state import SafetyEvidence
from services.job_worker.p1_engine_spawn import (
    P1EngineClosureAttestation,
    P1_PAPER_SOURCE_SHA256,
)
from services.paper_runtime import controller as controller_module
from services.paper_runtime.controller import (
    Package6Controller,
    _custodian_authority_sha256,
    issue_engine_session_port,
)
from services.paper_runtime.custodian_client import (
    CustodianAttestation,
    CustodianClient,
)
from services.paper_runtime.nautilus_checkpoint import ZERO_CHECKPOINT_SHA256
from services.paper_runtime.nautilus_session import (
    EngineSessionPort,
    NautilusPaperSession,
    NautilusSessionRejected,
    _issue_engine_session_port,
)
from services.paper_runtime import nautilus_process as process_module
from tests.fixtures.paper_runtime import (
    _Child,
    _authority,
    _commands,
    _request,
    _safety,
)


NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
SESSION = UUID("10000000-0000-4000-8000-000000000001")
OWNER = UUID("20000000-0000-4000-8000-000000000001")
CORRELATION = UUID("30000000-0000-4000-8000-000000000001")
TARGET = UUID("40000000-0000-4000-8000-000000000001")
SIGNAL = UUID("50000000-0000-4000-8000-000000000001")
CLOSURE = "b" * 64
CHILD = "c" * 64


def test_process_binding_uses_the_executable_identity_observed_in_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_argv = (
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "/engine/runtime_v1/paper_main.py",
        "/inputs/request.json",
        "/inputs/request.sha256",
    )
    executable_sha256 = "e" * 64
    monkeypatch.setattr(process_module, "_descendants", lambda _pid: (101,))
    monkeypatch.setattr(
        process_module,
        "_process_facts",
        lambda _pid: (2, 1234, (83, 146)),
    )
    monkeypatch.setattr(
        process_module,
        "_process_executable_sha256",
        lambda _pid: executable_sha256,
    )
    monkeypatch.setattr(
        process_module,
        "_process_cmdline_sha256",
        lambda _pid: hashlib.sha256(canonical_json_bytes(child_argv)).hexdigest(),
    )
    process = process_module._bind_process(
        cast(
            subprocess.Popen[bytes],
            SimpleNamespace(pid=100, poll=lambda: None),
        ),
        cast(P1EngineClosureAttestation, SimpleNamespace(closure_sha256=CLOSURE)),
        _request(),
        "f" * 64,
        P1_PAPER_SOURCE_SHA256,
        executable_sha256,
        child_argv,
    )

    assert process.host_pid == 101
    assert process.executable_identity == (83, 146)
    assert process.executable_sha256 == executable_sha256


def test_live_process_with_unreadable_authority_is_not_reported_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = object.__new__(process_module.NautilusPaperProcess)
    object.__setattr__(process, "_process", SimpleNamespace(poll=lambda: None))
    object.__setattr__(process, "_closure", object())
    object.__setattr__(process, "_request", _request())
    monkeypatch.setattr(
        process_module.NautilusPaperProcess,
        "matches_authority",
        lambda *_args: False,
    )

    with pytest.raises(ValueError, match="liveness cannot be proven"):
        process.is_running()


def _controller_authority(
    monkeypatch: pytest.MonkeyPatch,
    *,
    approval_sha256: str = "3" * 64,
    closure_digest: str = CLOSURE,
) -> tuple[object, CustodianClient, object]:
    custodian = SimpleNamespace(
        helper_binary_sha256="4" * 64,
        native_source_set_sha256="5" * 64,
        protocol_version=1,
        protocol_features=(),
        endpoint_authority="PREOPENED_UNIX_SEQPACKET_DESCRIPTOR",
        operations=(
            "START",
            "STOP",
            "STATUS",
            "RECOVER",
            "RUN_ONCE",
            "READ_TRANSCRIPT",
            "PUBLISH_BUNDLE",
            "ACK",
        ),
        mode="PAPER",
        live_execution_approved=False,
        live_trading_approved=False,
    )
    capability = SimpleNamespace(
        approval_sha256=approval_sha256,
        source_commit="6" * 40,
        source_tree="7" * 40,
        fixture_sha256="8" * 64,
        authority_digests={"stage": "9" * 64},
        custodian=custodian,
    )
    attestation = CustodianAttestation(
        helper_binary_sha256=custodian.helper_binary_sha256,
        native_source_set_sha256=custodian.native_source_set_sha256,
        protocol_version=custodian.protocol_version,
        protocol_features=custodian.protocol_features,
        endpoint_authority=custodian.endpoint_authority,
        peer_pid=os.getpid(),
        peer_uid=os.geteuid(),
        peer_gid=os.getegid(),
        candidate_commit=capability.source_commit,
        candidate_tree=capability.source_tree,
        stage_sha256=capability.authority_digests["stage"],
        fixture_sha256=capability.fixture_sha256,
        mode="PAPER",
        live_execution_approved=False,
        live_trading_approved=False,
    )
    client = CustodianClient.__new__(CustodianClient)
    client._attestation = attestation
    closure = SimpleNamespace(
        closure_sha256=closure_digest,
        runtime_family="cython-v1",
        engine_version="1.231.0",
        engine_upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
    )
    monkeypatch.setattr(
        controller_module,
        "is_issued_capability",
        lambda value: value is capability,
    )
    monkeypatch.setattr(
        controller_module,
        "validate_p1_engine_closure_attestation",
        lambda value: value if value is closure else (_ for _ in ()).throw(ValueError),
    )
    return capability, client, closure


def _session(
    child: _Child,
    evidence: list[SafetyEvidence],
    monkeypatch: pytest.MonkeyPatch,
    *,
    authority_closure: str = CLOSURE,
    authority_identity: str = CHILD,
    recovery_recorder: object | None = None,
) -> tuple[NautilusPaperSession, object, CustodianClient]:
    capability, client, closure = _controller_authority(
        monkeypatch, closure_digest=authority_closure
    )
    handle = _issue_engine_session_port(
        identity=EngineSessionIdentityV1(
            runtime_family=closure.runtime_family,
            engine_version=closure.engine_version,
            engine_upstream_commit=closure.engine_upstream_commit,
            closure_digest=closure.closure_sha256,
            request_protocol="1.0.0",
            event_schema="nautilus-p1-event-stream-v1",
            paper_schema="nautilus-paper-session-v2",
        ),
        capability_sha256=capability.approval_sha256,
        custodian_authority_sha256=_custodian_authority_sha256(client.attestation),
        process_authority_sha256=authority_identity,
        paper_source_sha256=P1_PAPER_SOURCE_SHA256,
        session_id=SESSION,
        owner_id=OWNER,
        exchange=child.exchange,
        close_input=child.close,
        abort=child.abort,
        is_running=child.is_running,
    )
    session = NautilusPaperSession(
        request=_request(),
        job_id="job_" + "1" * 32,
        attempt_id="attempt_" + "2" * 32,
        child=handle,
        safety_preflight=lambda: evidence[-1],
        clock=lambda: NOW + timedelta(seconds=1),
        event_ledger=InMemoryEngineEventLedger(),
        projection_authority=_authority(),
        recovery_recorder=recovery_recorder,
    )
    return session, capability, client


def test_session_persists_exact_events_and_reducer_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child()
    session, _capability, _client = _session(child, [_safety()], monkeypatch)
    start, target, stop = _commands(_request())

    first = session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)
    second = session.execute(
        target, expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256
    )
    completed = session.execute(
        stop, expected_checkpoint_sha256=second.checkpoint.checkpoint_sha256
    )

    assert completed.event_receipt is not None
    assert completed.parity_receipt is not None
    assert completed.event_receipt.batch_sha256 == completed.parity_receipt.batch_sha256
    assert completed.parity_receipt.terminal_cash == Decimal(1000)
    assert completed.parity_receipt.terminal_position == Decimal(0)
    assert (
        completed.checkpoint.event_batch_sha256 == completed.event_receipt.batch_sha256
    )
    assert session.state == "STOPPED"
    assert child.calls == 3


def test_existing_controller_owns_the_single_nautilus_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child()
    session, capability, client = _session(child, [_safety()], monkeypatch)
    controller = Package6Controller(
        capability,
        custodian_client=client,
        nautilus_session=session,
    )
    start, _target, _stop = _commands(_request())

    result = controller.execute_nautilus(
        start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256
    )

    assert result.state == "RUNNING"
    assert child.calls == 1


def test_controller_rejects_session_issued_for_another_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child()
    session, capability, client = _session(child, [_safety()], monkeypatch)
    capability.approval_sha256 = "a" * 64

    with pytest.raises(TypeError, match="custody-bound"):
        Package6Controller(
            capability,
            custodian_client=client,
            nautilus_session=session,
        )

    assert child.calls == 0


def test_target_revalidates_safety_before_child_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child()
    evidence = [_safety()]
    session, _capability, _client = _session(child, evidence, monkeypatch)
    start, target, _stop = _commands(_request())
    first = session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)
    evidence.append(_safety(NOW - timedelta(minutes=1)))

    with pytest.raises(NautilusSessionRejected, match="safety"):
        session.execute(
            target, expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256
        )

    assert child.calls == 1
    assert child.aborted is False
    assert session.state == "EXIT_ONLY"


def test_kill_switch_engagement_blocks_target_before_child_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child()
    evidence = [_safety()]
    session, _capability, _client = _session(child, evidence, monkeypatch)
    start, target, stop = _commands(_request())
    first = session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)
    second = session.execute(
        target, expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256
    )
    evidence.append(_safety(kill_switch=CanonicalKillSwitchState.ACTIVE))
    next_target = __import__("json").loads(target)
    next_target["command_sequence"] = 3
    next_target["request_id"] = str(paper_request_id(SESSION, 3))

    with pytest.raises(NautilusSessionRejected, match="safety"):
        session.execute(
            canonical_json_bytes(next_target),
            expected_checkpoint_sha256=second.checkpoint.checkpoint_sha256,
        )

    assert child.calls == 2
    assert child.aborted is False
    assert session.state == "EXIT_ONLY"

    completed = session.execute(
        stop, expected_checkpoint_sha256=second.checkpoint.checkpoint_sha256
    )

    assert completed.state == "STOPPED"
    assert child.calls == 3


def test_target_rejects_checkpoint_drift_without_child_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child()
    session, _capability, _client = _session(child, [_safety()], monkeypatch)
    start, target, _stop = _commands(_request())
    session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)

    with pytest.raises(NautilusSessionRejected, match="checkpoint"):
        session.execute(target, expected_checkpoint_sha256="9" * 64)

    assert child.calls == 1
    assert session.state == "RECONCILIATION_REQUIRED"


def test_malformed_command_aborts_an_active_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child()
    session, _capability, _client = _session(child, [_safety()], monkeypatch)
    start, _target, _stop = _commands(_request())
    first = session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)

    with pytest.raises(NautilusSessionRejected, match="command"):
        session.execute(
            b"{}", expected_checkpoint_sha256=first.checkpoint.checkpoint_sha256
        )

    assert child.aborted is True
    assert session.state == "RECONCILIATION_REQUIRED"


def test_child_closure_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child(closure="9" * 64, event_closure=CLOSURE)
    session, _capability, _client = _session(child, [_safety()], monkeypatch)
    start, _target, _stop = _commands(_request())

    with pytest.raises(NautilusSessionRejected, match="closure"):
        session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)

    assert child.aborted is True
    assert session.state == "RECONCILIATION_REQUIRED"


def test_event_lineage_mismatch_fails_before_checkpoint_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child(closure="9" * 64, event_closure=CLOSURE)
    session, _capability, _client = _session(
        child,
        [_safety()],
        monkeypatch,
        authority_closure="9" * 64,
    )
    start, _target, _stop = _commands(_request())

    with pytest.raises(NautilusSessionRejected, match="closure"):
        session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)

    assert child.aborted is True
    assert session.state == "RECONCILIATION_REQUIRED"


def test_process_identity_mismatch_fails_before_checkpoint_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child(identity="d" * 64)
    session, _capability, _client = _session(child, [_safety()], monkeypatch)
    start, _target, _stop = _commands(_request())

    with pytest.raises(NautilusSessionRejected, match="closure"):
        session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)

    assert child.aborted is True
    assert session.state == "RECONCILIATION_REQUIRED"


def test_public_issuer_rejects_unattested_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child()
    capability, client, closure = _controller_authority(monkeypatch)

    with pytest.raises(TypeError, match="custody"):
        issue_engine_session_port(
            capability,
            custodian_client=client,
            closure_attestation=closure,  # type: ignore[arg-type]
            request=_request(),
            process=child,  # type: ignore[arg-type]
        )


def test_invalid_checkpoint_type_aborts_active_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child()
    session, _capability, _client = _session(child, [_safety()], monkeypatch)
    start, target, _stop = _commands(_request())
    session.execute(start, expected_checkpoint_sha256=ZERO_CHECKPOINT_SHA256)

    with pytest.raises(NautilusSessionRejected, match="checkpoint"):
        session.execute(target, expected_checkpoint_sha256=None)  # type: ignore[arg-type]

    assert child.aborted is True
    assert session.state == "RECONCILIATION_REQUIRED"


def test_freely_constructed_child_is_not_session_authority() -> None:
    with pytest.raises(TypeError):
        EngineSessionPort(  # type: ignore[call-arg]
            closure_digest=CLOSURE,
            authority_sha256="3" * 64,
            exchange=lambda _raw: b"",
            close_input=lambda: 0,
            abort=lambda: None,
        )


def test_source_does_not_add_network_or_live_authority() -> None:
    root = Path(__file__).parents[2]
    source = (root / "services/paper_runtime/nautilus_session.py").read_text(
        encoding="utf-8"
    )
    assert "subprocess" not in source
    assert "LIVE_TRADING_ENABLED=true" not in source
    assert "LIVE_EXECUTION_ENABLED=true" not in source
    for name in ("nautilus_process.py", "nautilus_session.py"):
        tree = ast.parse(
            (root / "services/paper_runtime" / name).read_text(encoding="utf-8")
        )
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert imports.isdisjoint({"aiohttp", "httpx", "requests", "socket", "urllib"})
    assert iter_payloads(b"") == ()

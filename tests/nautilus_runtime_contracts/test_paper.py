from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import pytest

from packages.engine_contracts import (
    CURRENT_SCHEMA_VERSION,
    ArtifactReference,
    EngineTargetPortfolio,
    EngineTargetPosition,
    EngineInstrumentId,
    StartPaperEngine,
    StopPaperEngine,
    SubmitTargetPortfolio,
    canonical_json_bytes,
    payload_digest,
)
from packages.domain import ProductType
from packages.nautilus_runtime_contracts.events import (
    P1AccountObserved,
    P1Event,
    P1Fill,
    P1PositionObserved,
    P1OrderSubmitted,
    P1RunCompleted,
    P1RunStarted,
    P1TargetAccepted,
    P1TargetQuantityPlanned,
)
from packages.nautilus_runtime_contracts.paper import (
    MAX_PAPER_FRAME_BYTES,
    PAPER_PROTOCOL_SCHEMA,
    PaperCommandFrame,
    PaperCommandAcknowledgement,
    PaperEventFrame,
    PaperSessionCheckpoint,
    PaperSessionJournal,
    PaperSessionState,
    paper_request_id,
)
from packages.nautilus_runtime_contracts.semantic import semantic_digest


SESSION = UUID("00000000-0000-0000-0000-000000000101")
OWNER = UUID("00000000-0000-0000-0000-000000000102")
DIGEST = "a" * 64
NOW = datetime(2026, 8, 30, tzinfo=UTC)


def _artifact(value: int) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=UUID(int=value),
        sha256=f"{value:064x}",
        media_type="application/json",
    )


def _start() -> StartPaperEngine:
    return StartPaperEngine(
        command_type="StartPaperEngine",
        engine_configuration=_artifact(1),
        instrument_catalog=_artifact(2),
        strategy_configuration=_artifact(3),
    )


def _target() -> SubmitTargetPortfolio:
    return SubmitTargetPortfolio(
        command_type="SubmitTargetPortfolio",
        target_portfolio=EngineTargetPortfolio(
            target_id=UUID(int=10),
            positions=(
                EngineTargetPosition(
                    instrument=EngineInstrumentId(
                        symbol="BTCUSDT",
                        product_type=ProductType.CRYPTO_SPOT,
                        venue="BINANCE",
                    ),
                    target_weight=Decimal("1"),
                ),
            ),
            source_signal_ids=(UUID(int=11),),
            effective_at=NOW,
            schema_version=CURRENT_SCHEMA_VERSION,
        ),
    )


def _command_bytes(command: object, sequence: int, request_id: UUID | None = None) -> bytes:
    request_id = request_id or paper_request_id(SESSION, sequence)
    return canonical_json_bytes(
        PaperCommandFrame(
            schema_version=PAPER_PROTOCOL_SCHEMA,
            frame_type="COMMAND",
            session_id=SESSION,
            owner_id=OWNER,
            request_id=request_id,
            command_sequence=sequence,
            command_digest=payload_digest(command),
            command=command,
        )
    )


def _start_event() -> P1RunStarted:
    return P1RunStarted(
        schema_version="nautilus-p1-event-stream-v1",
        event_type="RunStarted",
        origin="CONTROL_PLANE",
        native_type=None,
        sequence=2,
        simulation_time=NOW,
        runtime_family="cython-v1",
        engine_version="1.231.0",
        upstream_commit="b" * 40,
        closure_digest=DIGEST,
        config_digest=DIGEST,
        catalog_digest=DIGEST,
        data_digest=DIGEST,
    )


def _event_bytes(event: object, request_id: UUID | None = None) -> bytes:
    return canonical_json_bytes(
        PaperEventFrame(
            schema_version=PAPER_PROTOCOL_SCHEMA,
            frame_type="EVENT",
            session_id=SESSION,
            owner_id=OWNER,
            request_id=request_id or paper_request_id(SESSION, 1),
            event_sequence=event.sequence,
            event_digest=payload_digest(event),
            event=event,
        )
    )


def _ack_bytes(command: object, sequence: int, state: PaperSessionState) -> bytes:
    return canonical_json_bytes(
        PaperCommandAcknowledgement(
            schema_version=PAPER_PROTOCOL_SCHEMA,
            frame_type="ACK",
            session_id=SESSION,
            owner_id=OWNER,
            request_id=paper_request_id(SESSION, sequence),
            command_sequence=sequence,
            command_digest=payload_digest(command),
            state=state,
            accepted=True,
            reason_code="ACCEPTED",
        )
    )


def _zero_target_events() -> tuple[P1Event, ...]:
    events: tuple[P1Event, ...] = (
        _start_event(),
        P1TargetAccepted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="TargetAccepted",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=3,
            simulation_time=NOW,
            target_id="target-1",
            source_signal_ids=("signal-1",),
            target_weight=Decimal("0"),
        ),
        P1TargetQuantityPlanned(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="TargetQuantityPlanned",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=4,
            simulation_time=NOW,
            target_id="target-1",
            quantity=Decimal("0"),
        ),
        P1PositionObserved(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="PositionObserved",
            origin="NAUTILUS_CACHE_OBSERVATION",
            native_type="Position",
            sequence=5,
            simulation_time=NOW,
            quantity=Decimal("0"),
            average_entry_price=Decimal("0"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
        ),
        P1AccountObserved(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="AccountObserved",
            origin="NAUTILUS_CACHE_OBSERVATION",
            native_type="Account",
            sequence=6,
            simulation_time=NOW,
            cash_balance=Decimal("1000000"),
            fees=Decimal("0"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
        ),
        P1RunCompleted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="RunCompleted",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=7,
            simulation_time=NOW,
            runtime_family="cython-v1",
            engine_version="1.231.0",
            upstream_commit="b" * 40,
            closure_digest=DIGEST,
            target_count=1,
            order_count=0,
            fill_count=0,
            final_cash=Decimal("1000000"),
            final_position=Decimal("0"),
            fees=Decimal("0"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            semantic_digest="0" * 64,
        ),
    )
    return events[:-1] + (
        events[-1].model_copy(update={"semantic_digest": semantic_digest(events)}),
    )


def test_paper_journal_accepts_finite_session_and_builds_exact_checkpoint() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    start = _start()
    target = _target()
    stop = StopPaperEngine(command_type="StopPaperEngine", target_engine_run_id=SESSION)
    journal.accept_command(_command_bytes(start, 1))
    assert journal.state is PaperSessionState.STARTING
    journal.record_ack(_ack_bytes(start, 1, PaperSessionState.RUNNING))
    journal.record_event(_event_bytes(_start_event()))
    journal.accept_command(_command_bytes(target, 2))
    journal.record_ack(_ack_bytes(target, 2, PaperSessionState.RUNNING))
    journal.accept_command(_command_bytes(stop, 3))

    checkpoint = journal.checkpoint(
        semantic_state_hash="1" * 64,
        child_identity="2" * 64,
        closure_digest="3" * 64,
        portfolio_state_hash="4" * 64,
    )
    assert checkpoint.state is PaperSessionState.STOPPING
    assert checkpoint.last_accepted_command == 3
    assert checkpoint.last_emitted_event == 2
    restored = PaperSessionJournal.restore(
        checkpoint,
        session_id=SESSION,
        owner_id=OWNER,
        semantic_state_hash="1" * 64,
        child_identity="2" * 64,
        closure_digest="3" * 64,
        portfolio_state_hash="4" * 64,
        event_prefix=(_start_event(),),
    )
    assert restored.state is PaperSessionState.STOPPING


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate", "duplicate request"),
        ("gap", "command sequence gap"),
        ("wrong_session", "request identity"),
        ("changed_replay", "changed bytes"),
        ("oversized", "frame exceeds"),
        ("unknown", "unsupported paper command"),
    ],
)
def test_paper_protocol_rejects_untrusted_command_frames(
    mutation: str, message: str
) -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    first = _command_bytes(_start(), 1)
    if mutation == "oversized":
        with pytest.raises(ValueError, match=message):
            journal.accept_command(b"{" + b" " * MAX_PAPER_FRAME_BYTES + b"}")
        return
    if mutation == "unknown":
        raw = first.replace(b'"StartPaperEngine"', b'"RunBacktest"')
        with pytest.raises(ValueError, match=message):
            journal.accept_command(raw)
        return
    if mutation == "wrong_session":
        raw = first.replace(str(SESSION).encode(), str(UUID(int=999)).encode())
        with pytest.raises(ValueError, match=message):
            journal.accept_command(raw)
        return
    journal.accept_command(first)
    if mutation == "duplicate":
        raw = first
    elif mutation == "gap":
        journal.record_ack(_ack_bytes(_start(), 1, PaperSessionState.RUNNING))
        raw = _command_bytes(_target(), 3)
    else:
        raw = first.replace(b'"sha256":"' + b"0" * 63 + b'1"', b'"sha256":"' + b"f" * 64 + b'"', 1)
    with pytest.raises(ValueError, match=message):
        journal.accept_command(raw)


def test_paper_protocol_rejects_target_during_stopping_and_after_failure() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    start = _start()
    stop = StopPaperEngine(command_type="StopPaperEngine", target_engine_run_id=SESSION)
    journal.accept_command(_command_bytes(start, 1))
    journal.record_ack(_ack_bytes(start, 1, PaperSessionState.RUNNING))
    journal.accept_command(_command_bytes(stop, 2))
    journal.record_ack(_ack_bytes(stop, 2, PaperSessionState.STOPPING))
    with pytest.raises(ValueError, match="not accepted in STOPPING"):
        journal.accept_command(_command_bytes(_target(), 3))
    journal.transition(PaperSessionState.FAILED)
    with pytest.raises(ValueError, match="terminal session state"):
        journal.accept_command(_command_bytes(_target(), 3))


@pytest.mark.parametrize("side", ("BUY", "SELL"))
def test_stop_causality_rejects_an_opening_order_after_zero_target(
    side: Literal["BUY", "SELL"],
) -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    start = _start()
    stop = StopPaperEngine(command_type="StopPaperEngine", target_engine_run_id=SESSION)
    journal.accept_command(_command_bytes(start, 1))
    journal.record_ack(_ack_bytes(start, 1, PaperSessionState.RUNNING))
    journal.record_event(_event_bytes(_start_event()))
    journal.accept_command(_command_bytes(stop, 2))
    journal.record_ack(_ack_bytes(stop, 2, PaperSessionState.STOPPING))
    target = _zero_target_events()[1]
    journal.record_event(_event_bytes(target, paper_request_id(SESSION, 2)))
    plan = _zero_target_events()[2]
    journal.record_event(_event_bytes(plan, paper_request_id(SESSION, 2)))
    order = P1OrderSubmitted(
        schema_version="nautilus-p1-event-stream-v1",
        event_type="OrderSubmitted",
        origin="CONTROL_PLANE",
        native_type="Order",
        sequence=5,
        simulation_time=NOW,
        client_order_id="target-1",
        native_order_id="native-order-1",
        target_id="target-1",
        source_signal_ids=("signal-1",),
        side=side,
        quantity=Decimal("1"),
        order_type="MARKET",
    )
    with pytest.raises(ValueError, match="causality"):
        journal.record_event(_event_bytes(order, paper_request_id(SESSION, 2)))


def test_stop_causality_rejects_aggregate_exit_overcommit() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    start, target = _start(), _target()
    stop = StopPaperEngine(command_type="StopPaperEngine", target_engine_run_id=SESSION)
    journal.accept_command(_command_bytes(start, 1))
    journal.record_ack(_ack_bytes(start, 1, PaperSessionState.RUNNING))
    journal.record_event(_event_bytes(_start_event()))
    journal.accept_command(_command_bytes(target, 2))
    journal.record_ack(_ack_bytes(target, 2, PaperSessionState.RUNNING))
    long_target = _zero_target_events()[1].model_copy(update={"target_weight": Decimal("1")})
    long_plan = _zero_target_events()[2].model_copy(update={"quantity": Decimal("1")})
    buy = P1OrderSubmitted(
        schema_version="nautilus-p1-event-stream-v1", event_type="OrderSubmitted", origin="CONTROL_PLANE", native_type="Order", sequence=5, simulation_time=NOW, client_order_id="entry", native_order_id="native-entry", target_id="target-1", source_signal_ids=("signal-1",), side="BUY", quantity=Decimal("1"), order_type="MARKET"
    )
    fill = P1Fill(
        schema_version="nautilus-p1-event-stream-v1", event_type="Fill", origin="NAUTILUS_CALLBACK", native_type="OrderFilled", sequence=6, simulation_time=NOW, client_order_id="entry", native_fill_id="trade-entry", side="BUY", quantity=Decimal("1"), price=Decimal("100"), fee=Decimal("0"), fee_currency="USDT"
    )
    for event in (long_target, long_plan, buy, fill):
        journal.record_event(_event_bytes(event, paper_request_id(SESSION, 2)))
    journal.accept_command(_command_bytes(stop, 3))
    journal.record_ack(_ack_bytes(stop, 3, PaperSessionState.STOPPING))
    for index, target_id in enumerate(("exit-1", "exit-2")):
        exit_target = long_target.model_copy(update={"sequence": 7 + index * 3, "target_id": target_id, "target_weight": Decimal("0")})
        exit_plan = long_plan.model_copy(update={"sequence": 8 + index * 3, "target_id": target_id, "quantity": Decimal("0")})
        exit_order = buy.model_copy(update={"sequence": 9 + index * 3, "client_order_id": target_id, "native_order_id": f"native-{target_id}", "target_id": target_id, "side": "SELL"})
        journal.record_event(_event_bytes(exit_target, paper_request_id(SESSION, 3)))
        journal.record_event(_event_bytes(exit_plan, paper_request_id(SESSION, 3)))
        if index == 0:
            journal.record_event(_event_bytes(exit_order, paper_request_id(SESSION, 3)))
        else:
            with pytest.raises(ValueError, match="causality"):
                journal.record_event(_event_bytes(exit_order, paper_request_id(SESSION, 3)))


def test_stop_causality_rejects_nonflat_terminal_observation() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    start, target = _start(), _target()
    stop = StopPaperEngine(command_type="StopPaperEngine", target_engine_run_id=SESSION)
    journal.accept_command(_command_bytes(start, 1))
    journal.record_ack(_ack_bytes(start, 1, PaperSessionState.RUNNING))
    journal.record_event(_event_bytes(_start_event()))
    journal.accept_command(_command_bytes(target, 2))
    journal.record_ack(_ack_bytes(target, 2, PaperSessionState.RUNNING))
    long_target = _zero_target_events()[1].model_copy(update={"target_weight": Decimal("1")})
    long_plan = _zero_target_events()[2].model_copy(update={"quantity": Decimal("1")})
    order = P1OrderSubmitted(schema_version="nautilus-p1-event-stream-v1", event_type="OrderSubmitted", origin="CONTROL_PLANE", native_type="Order", sequence=5, simulation_time=NOW, client_order_id="entry", native_order_id="native-entry", target_id="target-1", source_signal_ids=("signal-1",), side="BUY", quantity=Decimal("1"), order_type="MARKET")
    fill = P1Fill(schema_version="nautilus-p1-event-stream-v1", event_type="Fill", origin="NAUTILUS_CALLBACK", native_type="OrderFilled", sequence=6, simulation_time=NOW, client_order_id="entry", native_fill_id="fill-entry", side="BUY", quantity=Decimal("1"), price=Decimal("100"), fee=Decimal("0"), fee_currency="USDT")
    for event in (long_target, long_plan, order, fill):
        journal.record_event(_event_bytes(event, paper_request_id(SESSION, 2)))
    journal.accept_command(_command_bytes(stop, 3))
    journal.record_ack(_ack_bytes(stop, 3, PaperSessionState.STOPPING))
    nonflat = _zero_target_events()[3].model_copy(update={"sequence": 7, "quantity": Decimal("1"), "average_entry_price": Decimal("100")})
    with pytest.raises(ValueError, match="causality"):
        journal.record_event(_event_bytes(nonflat, paper_request_id(SESSION, 3)))


def test_paper_checkpoint_restart_requires_exact_authority() -> None:
    checkpoint = PaperSessionCheckpoint(
        schema_version=PAPER_PROTOCOL_SCHEMA,
        session_id=SESSION,
        owner_id=OWNER,
        state=PaperSessionState.RUNNING,
        last_accepted_command=1,
        last_request_id=paper_request_id(SESSION, 1),
        last_command_type="StartPaperEngine",
        last_command_frame_sha256="0" * 64,
        last_command_digest="1" * 64,
        last_emitted_event=0,
        last_event_digest="0" * 64,
        event_prefix_sha256="0" * 64,
        last_acknowledged_command=1,
        last_acknowledgement_sha256="0" * 64,
        semantic_state_hash="3" * 64,
        child_identity="4" * 64,
        closure_digest="5" * 64,
        portfolio_state_hash="6" * 64,
    )
    for field, value in (
        ("session_id", UUID(int=999)),
        ("owner_id", UUID(int=998)),
        ("child_identity", "7" * 64),
        ("closure_digest", "8" * 64),
        ("portfolio_state_hash", "9" * 64),
        ("semantic_state_hash", "a" * 64),
    ):
        kwargs = {
            "session_id": SESSION,
            "owner_id": OWNER,
            "child_identity": "4" * 64,
            "closure_digest": "5" * 64,
            "portfolio_state_hash": "6" * 64,
            "semantic_state_hash": "3" * 64,
            "event_prefix": (),
        }
        kwargs[field] = value
        with pytest.raises(ValueError, match="checkpoint authority"):
            PaperSessionJournal.restore(checkpoint, **kwargs)

    restored = PaperSessionJournal.restore(
        checkpoint,
        session_id=SESSION,
        owner_id=OWNER,
        semantic_state_hash="3" * 64,
        child_identity="4" * 64,
        closure_digest="5" * 64,
        portfolio_state_hash="6" * 64,
        event_prefix=(),
    )
    replayed_ack = PaperCommandAcknowledgement(
        schema_version=PAPER_PROTOCOL_SCHEMA,
        frame_type="ACK",
        session_id=SESSION,
        owner_id=OWNER,
        request_id=checkpoint.last_request_id,
        command_sequence=1,
        command_digest="1" * 64,
        state=PaperSessionState.RUNNING,
        accepted=True,
        reason_code="ACCEPTED",
    )
    with pytest.raises(ValueError, match="duplicate paper acknowledgement"):
        restored.record_ack(canonical_json_bytes(replayed_ack))
    with pytest.raises(ValueError, match="event prefix"):
        PaperSessionJournal.restore(
            checkpoint,
            session_id=SESSION,
            owner_id=OWNER,
            semantic_state_hash="3" * 64,
            child_identity="4" * 64,
            closure_digest="5" * 64,
            portfolio_state_hash="6" * 64,
            event_prefix=(_start_event(),),
        )
    with pytest.raises(ValueError, match="event prefix"):
        PaperSessionJournal.restore(
            checkpoint.model_copy(update={"event_prefix_sha256": "f" * 64}),
            session_id=SESSION,
            owner_id=OWNER,
            semantic_state_hash="3" * 64,
            child_identity="4" * 64,
            closure_digest="5" * 64,
            portfolio_state_hash="6" * 64,
            event_prefix=(),
        )


def test_paper_event_frame_reuses_p1_a_event_contract() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    start = _start()
    target = _target()
    journal.accept_command(_command_bytes(start, 1))
    journal.record_event(_event_bytes(_start_event()))
    journal.record_ack(_ack_bytes(start, 1, PaperSessionState.RUNNING))
    journal.accept_command(_command_bytes(target, 2))
    changed = P1TargetAccepted(
        schema_version="nautilus-p1-event-stream-v1",
        event_type="TargetAccepted",
        origin="CONTROL_PLANE",
        native_type=None,
        sequence=3,
        simulation_time=NOW,
        target_id="target-1",
        source_signal_ids=("signal-1",),
        target_weight=Decimal("1"),
    )
    journal.record_event(_event_bytes(changed, paper_request_id(SESSION, 2)))
    with pytest.raises(ValueError, match="event sequence gap"):
        journal.record_event(
            _event_bytes(
                changed.model_copy(update={"sequence": 5}),
                paper_request_id(SESSION, 2),
            )
        )


def test_paper_events_require_started_nonterminal_session_and_known_request() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    with pytest.raises(ValueError, match="event requires a started session"):
        journal.record_event(_event_bytes(_start_event()))
    journal.accept_command(_command_bytes(_start(), 1))
    unknown = _event_bytes(_start_event()).replace(
        str(paper_request_id(SESSION, 1)).encode(), str(UUID(int=999)).encode()
    )
    with pytest.raises(ValueError, match="causal request"):
        journal.record_event(unknown)
    journal.record_event(_event_bytes(_start_event()))
    journal.transition(PaperSessionState.FAILED)
    with pytest.raises(ValueError, match="terminal session state"):
        journal.record_event(
            _event_bytes(_start_event().model_copy(update={"sequence": 3}))
        )

    duplicate = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    duplicate.accept_command(_command_bytes(_start(), 1))
    duplicate.record_event(_event_bytes(_start_event()))
    with pytest.raises(ValueError, match="duplicate RunStarted"):
        duplicate.record_event(
            _event_bytes(_start_event().model_copy(update={"sequence": 3}))
        )


def test_first_paper_event_must_be_p1_run_started() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    journal.accept_command(_command_bytes(_start(), 1))
    event = P1TargetAccepted(
        schema_version="nautilus-p1-event-stream-v1",
        event_type="TargetAccepted",
        origin="CONTROL_PLANE",
        native_type=None,
        sequence=2,
        simulation_time=NOW,
        target_id="target-1",
        source_signal_ids=("signal-1",),
        target_weight=Decimal("1"),
    )
    with pytest.raises(ValueError, match="RunStarted"):
        journal.record_event(_event_bytes(event))

    causal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    causal.accept_command(_command_bytes(_start(), 1))
    causal.record_event(_event_bytes(_start_event()))
    with pytest.raises(ValueError, match="command/event causality"):
        causal.record_event(_event_bytes(event.model_copy(update={"sequence": 3})))


def test_ack_is_canonical_correlated_and_eof_is_fail_closed() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    command = _start()
    journal.accept_command(_command_bytes(command, 1))
    ack = PaperCommandAcknowledgement(
        schema_version=PAPER_PROTOCOL_SCHEMA,
        frame_type="ACK",
        session_id=SESSION,
        owner_id=OWNER,
        request_id=paper_request_id(SESSION, 1),
        command_sequence=1,
        command_digest=payload_digest(command),
        state=PaperSessionState.RUNNING,
        accepted=True,
        reason_code="ACCEPTED",
    )
    with pytest.raises(ValueError, match="does not match an accepted command"):
        journal.record_ack(
            canonical_json_bytes(ack.model_copy(update={"command_digest": "f" * 64}))
        )
    journal.record_ack(canonical_json_bytes(ack))
    assert journal.state is PaperSessionState.RUNNING
    with pytest.raises(ValueError, match="canonical JSON"):
        journal.record_ack(b" " + canonical_json_bytes(ack))

    target = _target()
    journal.accept_command(_command_bytes(target, 2))
    with pytest.raises(ValueError, match="acknowledgement state"):
        journal.record_ack(_ack_bytes(target, 2, PaperSessionState.STOPPING))

    unexpected = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    unexpected.accept_command(_command_bytes(_start(), 1))
    with pytest.raises(ValueError, match="acknowledged stop"):
        unexpected.end_of_input()
    assert unexpected.state is PaperSessionState.RECONCILIATION_REQUIRED


def test_clean_eof_requires_acknowledged_stop_and_complete_p1_stream() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    start = _start()
    target = _target()
    stop = StopPaperEngine(command_type="StopPaperEngine", target_engine_run_id=SESSION)
    journal.accept_command(_command_bytes(start, 1))
    journal.record_ack(_ack_bytes(start, 1, PaperSessionState.RUNNING))
    journal.record_event(_event_bytes(_zero_target_events()[0]))
    journal.accept_command(_command_bytes(target, 2))
    journal.record_ack(_ack_bytes(target, 2, PaperSessionState.RUNNING))
    for event in _zero_target_events()[1:-1]:
        journal.record_event(_event_bytes(event, paper_request_id(SESSION, 2)))
    journal.accept_command(_command_bytes(stop, 3))
    with pytest.raises(ValueError, match="acknowledged stop"):
        journal.end_of_input()
    assert journal.state is PaperSessionState.RECONCILIATION_REQUIRED

    clean = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    clean.accept_command(_command_bytes(start, 1))
    clean.record_ack(_ack_bytes(start, 1, PaperSessionState.RUNNING))
    clean.record_event(_event_bytes(_zero_target_events()[0]))
    clean.accept_command(_command_bytes(target, 2))
    clean.record_ack(_ack_bytes(target, 2, PaperSessionState.RUNNING))
    for event in _zero_target_events()[1:-1]:
        clean.record_event(_event_bytes(event, paper_request_id(SESSION, 2)))
    checkpoint = clean.checkpoint(
        semantic_state_hash="1" * 64,
        child_identity="2" * 64,
        closure_digest="3" * 64,
        portfolio_state_hash="4" * 64,
    )
    clean = PaperSessionJournal.restore(
        checkpoint,
        session_id=SESSION,
        owner_id=OWNER,
        semantic_state_hash="1" * 64,
        child_identity="2" * 64,
        closure_digest="3" * 64,
        portfolio_state_hash="4" * 64,
        event_prefix=_zero_target_events()[:-1],
    )
    clean.accept_command(_command_bytes(stop, 3))
    clean.record_ack(_ack_bytes(stop, 3, PaperSessionState.STOPPING))
    clean.record_event(_event_bytes(_zero_target_events()[-1], paper_request_id(SESSION, 3)))
    clean.end_of_input()
    assert clean.state is PaperSessionState.STOPPED


def test_start_then_stop_cannot_bypass_complete_event_stream() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    start = _start()
    stop = StopPaperEngine(command_type="StopPaperEngine", target_engine_run_id=SESSION)
    journal.accept_command(_command_bytes(start, 1))
    journal.record_ack(_ack_bytes(start, 1, PaperSessionState.RUNNING))
    journal.record_event(_event_bytes(_start_event()))
    journal.accept_command(_command_bytes(stop, 2))
    journal.record_ack(_ack_bytes(stop, 2, PaperSessionState.STOPPING))
    with pytest.raises(ValueError, match="complete P1 event stream"):
        journal.end_of_input()
    assert journal.state is PaperSessionState.RECONCILIATION_REQUIRED


def test_restore_rejects_replayed_request_with_changed_sequence() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    start = _start()
    journal.accept_command(_command_bytes(start, 1))
    journal.record_ack(_ack_bytes(start, 1, PaperSessionState.RUNNING))
    checkpoint = journal.checkpoint(
        semantic_state_hash="1" * 64,
        child_identity="2" * 64,
        closure_digest="3" * 64,
        portfolio_state_hash="4" * 64,
    )
    restored = PaperSessionJournal.restore(
        checkpoint,
        session_id=SESSION,
        owner_id=OWNER,
        semantic_state_hash="1" * 64,
        child_identity="2" * 64,
        closure_digest="3" * 64,
        portfolio_state_hash="4" * 64,
        event_prefix=(),
    )
    with pytest.raises(ValueError, match="request identity"):
        restored.accept_command(_command_bytes(_target(), 2, checkpoint.last_request_id))


def test_restore_preserves_the_only_allowed_outstanding_command() -> None:
    journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
    start = _start()
    journal.accept_command(_command_bytes(start, 1))
    with pytest.raises(ValueError, match="previous paper command"):
        journal.accept_command(_command_bytes(_target(), 2))
    checkpoint = journal.checkpoint(
        semantic_state_hash="1" * 64,
        child_identity="2" * 64,
        closure_digest="3" * 64,
        portfolio_state_hash="4" * 64,
    )
    restored = PaperSessionJournal.restore(
        checkpoint,
        session_id=SESSION,
        owner_id=OWNER,
        semantic_state_hash="1" * 64,
        child_identity="2" * 64,
        closure_digest="3" * 64,
        portfolio_state_hash="4" * 64,
        event_prefix=(),
    )
    restored.record_ack(_ack_bytes(start, 1, PaperSessionState.RUNNING))
    assert restored.state is PaperSessionState.RUNNING

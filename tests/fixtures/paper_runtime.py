"""Deterministic provider-free fixture for the actual Nautilus paper session."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from uuid import UUID

from engines.nautilus.runtime_v1.control_channel import framed_document
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
from services.job_worker.p1_engine_spawn import P1_PAPER_SOURCE_SHA256
from services.job_worker.safety import SafetyMode
from services.job_worker.safety_state import SafetyEvidence
from services.paper_runtime.nautilus_checkpoint import ZERO_CHECKPOINT_SHA256
from services.paper_runtime.nautilus_child import issue_engine_session_port
from services.paper_runtime.nautilus_session import NautilusPaperSession


NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
SESSION = UUID("10000000-0000-4000-8000-000000000001")
OWNER = UUID("20000000-0000-4000-8000-000000000001")
CORRELATION = UUID("30000000-0000-4000-8000-000000000001")
TARGET = UUID("40000000-0000-4000-8000-000000000001")
SIGNAL = UUID("50000000-0000-4000-8000-000000000001")
CLOSURE = "b" * 64
CHILD = "c" * 64


def _money(value: str) -> Money:
    return Money(Decimal(value), Currency.USDT)


def _catalog() -> P1InstrumentCatalogV1:
    return P1InstrumentCatalogV1(
        schema_version="nautilus-p1-instrument-catalog-v1",
        instrument_id="BTCUSDT.BINANCE",
        product_type="crypto_spot",
        symbol="BTCUSDT",
        base_currency="BTC",
        quote_currency="USDT",
        venue="BINANCE",
        price_precision=2,
        size_precision=6,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.000001"),
        min_quantity=Decimal("0.000001"),
        min_notional=Decimal("0.01"),
        provenance_sha256="d" * 64,
    )


def _authority() -> ProjectionAuthority:
    catalog = _catalog()
    zero = _money("0")
    return ProjectionAuthority(
        request_message_id=OWNER,
        catalog=catalog,
        instrument=InstrumentDefinition(
            instrument_id=InstrumentId("BTCUSDT", ProductType.CRYPTO_SPOT, "BINANCE"),
            raw_symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            base_currency=Currency.BTC,
            quote_currency=Currency.USDT,
            settlement_currency=Currency.USDT,
            tick_size=Price(Decimal("0.01"), Currency.USDT),
            size_increment=OrderQuantity(Decimal("0.000001"), 6),
            minimum_quantity=OrderQuantity(Decimal("0.000001"), 6),
            maximum_quantity=OrderQuantity(Decimal("1000000"), 6),
            minimum_notional=_money("0.01"),
            maximum_notional=_money("100000000"),
            multiplier=Decimal(1),
            margin=None,
            session_calendar="24X7",
            provenance=InstrumentProvenance("P1CATALOG", "d" * 32, NOW),
        ),
        opening=PortfolioOpeningEntry(
            account_id="account-1",
            reporting_currency=Currency.USDT,
            balances=(
                AccountBalanceSnapshot(
                    account_id="account-1",
                    currency=Currency.USDT,
                    cash=_money("1000"),
                    locked_funds=zero,
                    margin_used=zero,
                    realized_pnl=zero,
                    unrealized_pnl=zero,
                    fees=zero,
                    funding=zero,
                    observed_at=NOW,
                    schema_version="balance-v1",
                ),
            ),
            source_id="p1-opening",
            source_revision="r1",
            effective_at=NOW,
            schema_version="portfolio-entry-v1",
        ),
        strategy_id="strategy-1",
        liquidity_side=LiquiditySide.TAKER,
        reconciliation_source=ReconciliationSource.VENUE,
    )


def _request() -> EngineCommandEnvelope:
    refs = {
        "engine_configuration": ArtifactReference(
            artifact_id=UUID("60000000-0000-4000-8000-000000000001"),
            sha256="f" * 64,
            media_type="application/json",
        ),
        "instrument_catalog": ArtifactReference(
            artifact_id=UUID("70000000-0000-4000-8000-000000000001"),
            sha256=catalog_digest(_catalog()),
            media_type="application/json",
        ),
        "strategy_configuration": ArtifactReference(
            artifact_id=UUID("80000000-0000-4000-8000-000000000001"),
            sha256="e" * 64,
            media_type="application/json",
        ),
        "market_data": ArtifactReference(
            artifact_id=UUID("90000000-0000-4000-8000-000000000001"),
            sha256="1" * 64,
            media_type="application/jsonl",
        ),
    }
    payload = RunBacktest(
        command_type="RunBacktest",
        **refs,
        start_time=NOW,
        end_time=NOW + timedelta(minutes=1),
    )
    return EngineCommandEnvelope(
        message_id=OWNER,
        correlation_id=CORRELATION,
        causation_id=OWNER,
        engine_run_id=SESSION,
        stream_sequence=1,
        event_time=NOW,
        initialization_time=NOW,
        schema_version="1.0.0",
        producer_identity="p1-paper-test",
        source_commit="0123456789abcdef0123456789abcdef01234567",
        config_digest="f" * 64,
        payload_digest=payload_digest(payload),
        payload=payload,
    )


def _events(closure: str = CLOSURE) -> tuple[object, ...]:
    values: tuple[object, ...] = (
        P1RunStarted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="RunStarted",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=2,
            simulation_time=NOW,
            runtime_family="cython-v1",
            engine_version="1.231.0",
            upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
            closure_digest=closure,
            config_digest="f" * 64,
            catalog_digest=catalog_digest(_catalog()),
            data_digest="1" * 64,
        ),
        P1TargetAccepted(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="TargetAccepted",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=3,
            simulation_time=NOW,
            target_id=str(TARGET),
            source_signal_ids=(str(SIGNAL),),
            target_weight=Decimal(0),
        ),
        P1TargetQuantityPlanned(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="TargetQuantityPlanned",
            origin="CONTROL_PLANE",
            native_type=None,
            sequence=4,
            simulation_time=NOW,
            target_id=str(TARGET),
            quantity=Decimal(0),
        ),
        P1PositionObserved(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="PositionObserved",
            origin="NAUTILUS_CACHE_OBSERVATION",
            native_type="Position",
            sequence=5,
            simulation_time=NOW,
            quantity=Decimal(0),
            average_entry_price=Decimal(0),
            realized_pnl=Decimal(0),
            unrealized_pnl=Decimal(0),
        ),
        P1AccountObserved(
            schema_version="nautilus-p1-event-stream-v1",
            event_type="AccountObserved",
            origin="NAUTILUS_CACHE_OBSERVATION",
            native_type="Account",
            sequence=6,
            simulation_time=NOW,
            cash_balance=Decimal(1000),
            fees=Decimal(0),
            realized_pnl=Decimal(0),
            unrealized_pnl=Decimal(0),
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
            upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
            closure_digest=closure,
            target_count=1,
            order_count=0,
            fill_count=0,
            final_cash=Decimal(1000),
            final_position=Decimal(0),
            fees=Decimal(0),
            realized_pnl=Decimal(0),
            unrealized_pnl=Decimal(0),
            semantic_digest="0" * 64,
        ),
    )
    return values[:-1] + (
        values[-1].model_copy(update={"semantic_digest": semantic_digest(values)}),
    )


def _commands(request: EngineCommandEnvelope) -> tuple[bytes, ...]:
    target = EngineTargetPortfolio(
        target_id=TARGET,
        positions=(
            EngineTargetPosition(
                instrument=EngineInstrumentId(
                    product_type=ProductType.CRYPTO_SPOT,
                    symbol="BTCUSDT",
                    venue="BINANCE",
                ),
                target_weight=Decimal(0),
            ),
        ),
        source_signal_ids=(SIGNAL,),
        effective_at=NOW,
        schema_version="1.0.0",
    )
    payloads = (
        StartPaperEngine(
            command_type="StartPaperEngine",
            engine_configuration=request.payload.engine_configuration,
            instrument_catalog=request.payload.instrument_catalog,
            strategy_configuration=request.payload.strategy_configuration,
        ),
        SubmitTargetPortfolio(
            command_type="SubmitTargetPortfolio", target_portfolio=target
        ),
        StopPaperEngine(command_type="StopPaperEngine", target_engine_run_id=SESSION),
    )
    return tuple(
        canonical_json_bytes(
            PaperCommandFrame(
                schema_version=PAPER_PROTOCOL_SCHEMA,
                frame_type="COMMAND",
                session_id=SESSION,
                owner_id=OWNER,
                request_id=paper_request_id(SESSION, sequence),
                command_sequence=sequence,
                command_digest=payload_digest(command),
                command=command,
            )
        )
        for sequence, command in enumerate(payloads, start=1)
    )


class _Child:
    def __init__(
        self,
        *,
        closure: str = CLOSURE,
        identity: str = CHILD,
        event_closure: str | None = None,
    ) -> None:
        self.journal = PaperSessionJournal(session_id=SESSION, owner_id=OWNER)
        self.events = _events(closure if event_closure is None else event_closure)
        self.closure = closure
        self.identity = identity
        self.calls = 0
        self.aborted = False

    def exchange(self, raw: bytes) -> bytes:
        command = self.journal.accept_command(raw)
        self.calls += 1
        state = "RUNNING" if command.command_sequence < 3 else "STOPPING"
        acknowledgement = canonical_json_bytes(
            {
                "accepted": True,
                "command_digest": command.command_digest,
                "command_sequence": command.command_sequence,
                "frame_type": "ACK",
                "owner_id": str(OWNER),
                "reason_code": "ACCEPTED",
                "request_id": str(command.request_id),
                "schema_version": PAPER_PROTOCOL_SCHEMA,
                "session_id": str(SESSION),
                "state": state,
            }
        )
        self.journal.record_ack(acknowledgement)
        selected = (
            self.events[:1]
            if command.command_sequence == 1
            else self.events[1:3]
            if command.command_sequence == 2
            else self.events[3:]
        )
        frames = [framed_document(__import__("json").loads(acknowledgement))]
        for event in selected:
            event_raw = canonical_json_bytes(event)
            raw_frame = canonical_json_bytes(
                {
                    "event": event.model_dump(mode="json"),
                    "event_digest": hashlib.sha256(event_raw).hexdigest(),
                    "event_sequence": event.sequence,
                    "frame_type": "EVENT",
                    "owner_id": str(OWNER),
                    "request_id": str(command.request_id),
                    "schema_version": PAPER_PROTOCOL_SCHEMA,
                    "session_id": str(SESSION),
                }
            )
            self.journal.record_event(raw_frame)
            frames.append(framed_document(__import__("json").loads(raw_frame)))
        checkpoint = self.journal.checkpoint(
            semantic_state_hash=f"{command.command_sequence:064x}",
            child_identity=self.identity,
            closure_digest=self.closure,
            portfolio_state_hash=f"{command.command_sequence + 10:064x}",
        )
        document = checkpoint.model_dump(mode="json")
        frames.append(
            framed_document(
                {
                    "checkpoint": document,
                    "checkpoint_sha256": hashlib.sha256(
                        canonical_json_bytes(document)
                    ).hexdigest(),
                    "frame_type": "CHECKPOINT",
                    "schema_version": PAPER_PROTOCOL_SCHEMA,
                }
            )
        )
        return b"".join(frames)

    def close(self) -> int:
        self.journal.end_of_input()
        return 0

    def abort(self) -> None:
        self.aborted = True

    def is_running(self) -> bool:
        return not self.aborted


def _safety(
    now: datetime = NOW,
    *,
    kill_switch: CanonicalKillSwitchState = CanonicalKillSwitchState.INACTIVE,
) -> SafetyEvidence:
    return SafetyEvidence(
        requested_mode=SafetyMode.PAPER,
        effective_mode=SafetyMode.PAPER,
        live_execution_enabled=False,
        live_trading_approved=False,
        kill_switch_state=kill_switch,
        snapshot_sha256="a" * 64,
        generated_at=now,
        expires_at=now + timedelta(seconds=6),
    )


class DeterministicPaperRuntime:
    """JSONL batch adapter over the repository's actual NautilusPaperSession."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, mode=0o700)
        (self.root / "results").mkdir(mode=0o700)
        self.request = _request()
        child = _Child()
        identity = EngineSessionIdentityV1(
            runtime_family="cython-v1",
            engine_version="1.231.0",
            engine_upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
            closure_digest=CLOSURE,
            request_protocol="1.0.0",
            event_schema="nautilus-p1-event-stream-v1",
            paper_schema="nautilus-paper-session-v2",
        )
        self.port = issue_engine_session_port(
            identity=identity,
            capability_sha256="3" * 64,
            custodian_authority_sha256="4" * 64,
            process_authority_sha256=CHILD,
            paper_source_sha256=P1_PAPER_SOURCE_SHA256,
            session_id=SESSION,
            owner_id=OWNER,
            exchange=child.exchange,
            close_input=child.close,
            abort=child.abort,
            is_running=child.is_running,
        )
        self.session = NautilusPaperSession(
            request=self.request,
            job_id="job_" + "1" * 32,
            attempt_id="attempt_" + "2" * 32,
            child=self.port,
            safety_preflight=lambda: _safety(),
            clock=lambda: NOW + timedelta(seconds=1),
            event_ledger=InMemoryEngineEventLedger(),
            projection_authority=_authority(),
        )
        self.commands = _commands(self.request)
        self.sequence = 0
        self.checkpoint_sha256 = ZERO_CHECKPOINT_SHA256

    def exchange(self, raw: bytes) -> bytes:
        try:
            request = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("paper batch is invalid") from exc
        expected = "A" if self.sequence == 0 else "B" if self.sequence == 1 else None
        if request != {"batch": expected, "schema_version": "hwc-paper-batch-v1"}:
            raise ValueError("paper batch is invalid")
        command_slice = self.commands[:1] if expected == "A" else self.commands[1:]
        result = None
        for command in command_slice:
            result = self.session.execute(
                command, expected_checkpoint_sha256=self.checkpoint_sha256
            )
            self.checkpoint_sha256 = result.checkpoint.checkpoint_sha256
        assert result is not None
        self.sequence += 1
        payload = {
            "schema_version": "hwc-paper-result-v2",
            "batch": expected,
            "sequence": self.sequence,
            "session_id": str(self.port.session_id),
            "state": result.state,
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "event_sha256": result.checkpoint.checkpoint.event_prefix_sha256,
            "checkpoint_sha256": result.checkpoint.checkpoint_sha256,
            "event_batch_sha256": (
                result.event_receipt.batch_sha256 if result.event_receipt else None
            ),
            "parity_receipt_sha256": result.checkpoint.parity_receipt_sha256,
        }
        payload["result_sha256"] = payload_digest(payload)
        self._write(
            self.root / "checkpoint.json",
            result.checkpoint.checkpoint.model_dump(mode="json"),
        )
        self._write(self.root / "results" / f"batch-{expected.lower()}.json", payload)
        return canonical_json_bytes(payload)

    @staticmethod
    def _write(path: Path, payload: object) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(canonical_json_bytes(payload) + b"\n")
        temporary.chmod(0o600)
        temporary.replace(path)


__all__ = ["DeterministicPaperRuntime"]

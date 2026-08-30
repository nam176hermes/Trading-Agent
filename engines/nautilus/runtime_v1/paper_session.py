"""Streaming reuse of the fixed P1 BacktestEngine session for local paper replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re

from .backtest_runner import BacktestRun, NativeFact, _native_facts, _snapshot
from .control_channel import request_id
from .event_collector import CollectedExecution, collect_executions
from .generated_protocol import canonical_json_bytes
from .input_loader import RuntimeInputs
from .instrument_factory import build_instrument
from .market_data_loader import load_market_data
from .session import BacktestEngineSession, create_session


_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class _NativeCheckpoint:
    command_sequence: int
    data_cursor: int
    processed_target_ids: tuple[str, ...]
    collector_sha256: str
    semantic_state_sha256: str
    portfolio_state_sha256: str


@dataclass(frozen=True, slots=True)
class _ExecutionPrefix:
    native_facts: tuple[NativeFact, ...]
    processed_target_ids: tuple[str, ...]
    native_order_ids: tuple[str, ...]
    native_fill_ids: tuple[str, ...]
    order_count: int
    fill_count: int
    strategy_state: str
    engine_version: str = "1.231.0"
    pending_order_ids: tuple[str, ...] = ()
    rejected_order_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PaperCheckpoint:
    schema_version: str
    session_id: str
    owner_id: str
    state: str
    last_accepted_command: int
    last_request_id: str
    last_command_type: str
    last_command_frame_sha256: str
    last_command_digest: str
    last_emitted_event: int
    last_event_digest: str
    event_prefix_sha256: str
    last_acknowledged_command: int
    last_acknowledgement_sha256: str
    semantic_state_hash: str
    child_identity: str
    closure_digest: str
    portfolio_state_hash: str


def _mapping(value: object) -> dict[str, object]:
    if type(value) is not tuple:
        raise ValueError("paper target authority is invalid")
    result = dict(value)
    if len(result) != len(value):
        raise ValueError("paper target authority is invalid")
    return result


def _target_authority(inputs: RuntimeInputs) -> tuple[dict[str, object], ...]:
    schedule = _mapping(inputs.target_schedule)
    targets = schedule.get("targets")
    if (
        schedule.get("schema_version") != "nautilus-p1-target-schedule-v1"
        or type(targets) is not tuple
    ):
        raise ValueError("paper target authority is invalid")
    return tuple(_mapping(target) for target in targets)


def _thaw(value: object) -> object:
    if type(value) is dict:
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        if all(
            type(item) is tuple and len(item) == 2 and type(item[0]) is str
            for item in value
        ):
            return {str(key): _thaw(item) for key, item in value}
        return [_thaw(item) for item in value]
    return value


def _portfolio_state(session: BacktestEngineSession) -> tuple[tuple[str, object], ...]:
    engine = session.engine
    positions = tuple(
        sorted(
            (str(position.id), str(position.side), str(position.quantity))
            for position in engine.cache.positions()
        )
    )
    orders = tuple(
        sorted(
            (
                str(order.client_order_id),
                order.status.name,
                str(order.quantity),
                str(order.filled_qty),
            )
            for order in engine.cache.orders()
        )
    )
    accounts = tuple(engine.cache.accounts())
    balances = tuple(
        sorted(
            (str(currency), str(money))
            for account in accounts
            for currency, money in account.balances_total().items()
        )
    )
    return (("balances", balances), ("orders", orders), ("positions", positions))


def _processed_target_ids(session: BacktestEngineSession) -> tuple[str, ...]:
    try:
        return session.strategy.processed_target_ids
    except AttributeError:
        return ()


def _collector_snapshot(
    session: BacktestEngineSession,
) -> tuple[tuple[str, object], ...]:
    try:
        return session.strategy.collector.snapshot()
    except AttributeError:
        return ()


class PaperEngineSession:
    """One long-lived BacktestEngine fed one deterministic event at a time."""

    def __init__(self, inputs: RuntimeInputs) -> None:
        if type(inputs) is not RuntimeInputs:
            raise ValueError("paper native authority is invalid")
        instrument = build_instrument(inputs.instrument_catalog)
        batch = load_market_data(inputs, instrument)
        session = create_session(inputs, instrument, batch)
        try:
            session.engine.clear_data()
            targets = _target_authority(inputs)
        except BaseException as primary:
            session.dispose(primary)
            raise AssertionError("unreachable")
        self._session = session
        self._targets = targets
        self._cursor = 0
        self._last_command_sequence = 0
        self._stopped = False

    @property
    def target_ids(self) -> tuple[str, ...]:
        return tuple(str(target["target_id"]) for target in self._targets)

    @property
    def processed_target_ids(self) -> tuple[str, ...]:
        return _processed_target_ids(self._session)

    def start(self, sequence: int) -> _NativeCheckpoint:
        if self._stopped or self._last_command_sequence != 0 or sequence != 1:
            raise ValueError("paper native start authority is invalid")
        self._last_command_sequence = sequence
        return self.checkpoint()

    def submit_target(
        self, command: dict[str, object], sequence: int
    ) -> _NativeCheckpoint:
        if self._stopped or sequence != self._last_command_sequence + 1:
            raise ValueError("paper native command sequence is invalid")
        portfolio = command.get("target_portfolio")
        expected_index = len(_processed_target_ids(self._session))
        if type(portfolio) is not dict or expected_index >= len(self._targets):
            raise ValueError("paper target command is invalid")
        expected = self._targets[expected_index]
        if portfolio != _thaw(expected):
            raise ValueError("paper target command does not match sealed schedule")
        target_id = str(expected["target_id"])
        while target_id not in _processed_target_ids(self._session):
            if self._cursor >= len(self._session.batch.data):
                raise ValueError("paper market replay ended before target completion")
            self._session.engine.add_data([self._session.batch.data[self._cursor]])
            self._session.engine.run(streaming=True)
            self._session.engine.clear_data()
            self._cursor += 1
        self._last_command_sequence = sequence
        return self.checkpoint()

    def inspect(self, sequence: int) -> _NativeCheckpoint:
        if self._stopped or sequence != self._last_command_sequence + 1:
            raise ValueError("paper native inspect sequence is invalid")
        self._last_command_sequence = sequence
        return self.checkpoint()

    def checkpoint(self) -> _NativeCheckpoint:
        facts = _native_facts(_collector_snapshot(self._session))
        collector = tuple((fact.kind, fact.attributes) for fact in facts)
        portfolio = _portfolio_state(self._session)
        collector_digest = hashlib.sha256(canonical_json_bytes(collector)).hexdigest()
        portfolio_digest = hashlib.sha256(canonical_json_bytes(portfolio)).hexdigest()
        semantic = hashlib.sha256(
            canonical_json_bytes(
                {
                    "collector_sha256": collector_digest,
                    "command_sequence": self._last_command_sequence,
                    "cursor": self._cursor,
                    "portfolio_state_sha256": portfolio_digest,
                    "processed_target_ids": _processed_target_ids(self._session),
                }
            )
        ).hexdigest()
        return _NativeCheckpoint(
            command_sequence=self._last_command_sequence,
            data_cursor=self._cursor,
            processed_target_ids=_processed_target_ids(self._session),
            collector_sha256=collector_digest,
            semantic_state_sha256=semantic,
            portfolio_state_sha256=portfolio_digest,
        )

    def executions(self) -> tuple[CollectedExecution, ...]:
        facts = _native_facts(_collector_snapshot(self._session))
        strategy_state = self._session.strategy.state
        if self._session.strategy.pending_order:
            raise ValueError("paper native execution prefix is pending")
        order_ids = tuple(
            str(dict(fact.attributes)["client_order_id"])
            for fact in facts
            if fact.kind == "order_submitted"
        )
        fill_ids = tuple(
            str(dict(fact.attributes)["trade_id"])
            for fact in facts
            if fact.kind == "order_filled"
        )
        prefix = _ExecutionPrefix(
            native_facts=facts,
            processed_target_ids=_processed_target_ids(self._session),
            native_order_ids=order_ids,
            native_fill_ids=fill_ids,
            order_count=len(order_ids),
            fill_count=len(fill_ids),
            strategy_state=strategy_state,
        )
        return collect_executions(prefix, allow_nonterminal=True)[1]

    def _settle_exit_targets(self) -> None:
        processed = len(_processed_target_ids(self._session))
        if processed == 0:
            raise ValueError("paper stop requires an accepted target")
        if self._session.strategy.state == "COMPLETED":
            return
        if not self._session.engine.cache.positions_open() and not self._session.engine.cache.orders_open():
            self._session.strategy.finish_exit_only()
            return
        remaining = self._targets[processed:]
        if not remaining:
            raise ValueError("paper stop cannot settle exposure")
        positions = remaining[0].get("positions")
        if type(positions) is not tuple or len(positions) != 1 or _mapping(positions[0]).get("target_weight") != "0":
            raise ValueError("paper stop cannot create exposure")
        target_id = str(remaining[0]["target_id"])
        while target_id not in _processed_target_ids(self._session):
            if self._cursor >= len(self._session.batch.data):
                raise ValueError("paper market replay ended before exit settlement")
            self._session.engine.add_data([self._session.batch.data[self._cursor]])
            self._session.engine.run(streaming=True)
            self._session.engine.clear_data()
            self._cursor += 1
        if self._session.engine.cache.positions_open() or self._session.engine.cache.orders_open():
            raise ValueError("paper stop did not settle exposure")
        self._session.strategy.finish_exit_only()

    def stop(self, sequence: int) -> tuple[BacktestRun, _NativeCheckpoint]:
        if self._stopped or sequence != self._last_command_sequence + 1:
            raise ValueError("paper native stop authority is invalid")
        self._settle_exit_targets()
        while self._cursor < len(self._session.batch.data):
            self._session.engine.add_data([self._session.batch.data[self._cursor]])
            self._session.engine.run(streaming=True)
            self._session.engine.clear_data()
            self._cursor += 1
        self._session.engine.end()
        run = _snapshot(
            self._session.engine,
            self._session.strategy,
            self._session.batch,
            _processed_target_ids(self._session),
        )
        self._last_command_sequence = sequence
        self._stopped = True
        return run, self.checkpoint()

    def dispose(self, primary: BaseException | None = None) -> None:
        self._session.dispose(primary)


def bind_checkpoint(
    native: _NativeCheckpoint,
    *,
    state: str,
    session_id: str,
    owner_id: str,
    last_request_id: str,
    last_command_type: str,
    last_command_frame_sha256: str,
    last_command_digest: str,
    last_emitted_event: int,
    last_event_digest: str,
    event_prefix_sha256: str,
    last_acknowledgement_sha256: str,
    child_identity: str,
    closure_digest: str,
) -> PaperCheckpoint:
    values = {
        "schema_version": "nautilus-paper-session-v2",
        "session_id": session_id,
        "owner_id": owner_id,
        "state": state,
        "last_accepted_command": native.command_sequence,
        "last_request_id": last_request_id,
        "last_command_type": last_command_type,
        "last_command_frame_sha256": last_command_frame_sha256,
        "last_command_digest": last_command_digest,
        "last_emitted_event": last_emitted_event,
        "last_event_digest": last_event_digest,
        "event_prefix_sha256": event_prefix_sha256,
        "last_acknowledged_command": native.command_sequence,
        "last_acknowledgement_sha256": last_acknowledgement_sha256,
        "semantic_state_hash": native.semantic_state_sha256,
        "child_identity": child_identity,
        "closure_digest": closure_digest,
        "portfolio_state_hash": native.portfolio_state_sha256,
    }
    if (
        state not in {"RUNNING", "STOPPING", "STOPPED", "RECONCILIATION_REQUIRED", "FAILED"}
        or last_request_id != request_id(session_id, native.command_sequence)
        or last_emitted_event < 0
        or any(
            _DIGEST.fullmatch(values[name]) is None
            for name in (
                "last_command_frame_sha256",
                "last_command_digest",
                "last_event_digest",
                "event_prefix_sha256",
                "last_acknowledgement_sha256",
                "child_identity",
                "closure_digest",
            )
        )
    ):
        raise ValueError("paper native checkpoint authority is invalid")
    return PaperCheckpoint(**values)


def verify_checkpoint(
    checkpoint: PaperCheckpoint,
    *,
    checkpoint_sha256: str,
    session_id: str,
    owner_id: str,
    child_identity: str,
    closure_digest: str,
) -> PaperCheckpoint:
    values = asdict(checkpoint)
    if (
        type(checkpoint) is not PaperCheckpoint
        or checkpoint.schema_version != "nautilus-paper-session-v2"
        or hashlib.sha256(canonical_json_bytes(values)).hexdigest()
        != checkpoint_sha256
        or checkpoint.session_id != session_id
        or checkpoint.owner_id != owner_id
        or checkpoint.child_identity != child_identity
        or checkpoint.closure_digest != closure_digest
    ):
        raise ValueError("paper native checkpoint requires reconciliation")
    return checkpoint


__all__ = [
    "PaperCheckpoint",
    "PaperEngineSession",
    "bind_checkpoint",
    "verify_checkpoint",
]

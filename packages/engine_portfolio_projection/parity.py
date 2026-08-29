"""Exact, side-effect-free P1 engine/reducer portfolio parity proof."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from packages.domain import Currency, EventEnvelope, FiniteDecimal
from packages.engine_event_ledger import EngineEventTypeCount, EngineRunProjection
from packages.nautilus_runtime_contracts.events import (
    P1AccountObserved,
    P1Event,
    P1PositionObserved,
    P1RunCompleted,
)
from packages.portfolio_reducer import (
    replay_portfolio,
    snapshot_authority_from_result,
    snapshot_from_portfolio_result,
)

from .models import PortfolioProjection, ProjectionAuthority
from .projector import project_portfolio


Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class P1PortfolioParityError(ValueError):
    """The durable P1 engine facts and independent reducer do not agree."""


class P1PortfolioParityReceipt(BaseModel):
    """Pass-only commitment to one exact P1 portfolio parity proof."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    schema_version: Literal["nautilus-p1-portfolio-parity-v1"]
    normalization_version: Literal["nautilus-p1-portfolio-normalization-v1"]
    engine_run_id: UUID
    batch_sha256: Sha256Hex
    semantic_digest: Sha256Hex
    request_message_id: UUID
    engine_event_count: Annotated[int, Field(gt=0)]
    engine_last_sequence: Annotated[int, Field(gt=0)]
    engine_last_digest: Sha256Hex
    projection_identity: Sha256Hex
    portfolio_stream_id: UUID
    portfolio_event_count: Annotated[int, Field(gt=1)]
    portfolio_last_sequence: Annotated[int, Field(gt=1)]
    restart_prefix_sequence: Literal[1]
    portfolio_state_hash: Sha256Hex
    portfolio_prefix_history_hash: Sha256Hex
    account_id: str
    account_currency: Currency
    terminal_position: FiniteDecimal
    terminal_average_entry_price: FiniteDecimal | None
    terminal_mark_price: FiniteDecimal | None
    terminal_cash: FiniteDecimal
    terminal_fees: FiniteDecimal
    terminal_realized_pnl: FiniteDecimal
    terminal_unrealized_pnl: FiniteDecimal
    observed_at: datetime


def _validated_run_projection(value: object) -> EngineRunProjection:
    if type(value) is not EngineRunProjection:
        raise P1PortfolioParityError("durable engine run projection is invalid")
    try:
        return EngineRunProjection.model_validate(
            {
                name: getattr(value, name)
                for name in EngineRunProjection.model_fields
            }
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise P1PortfolioParityError(
            "durable engine run projection is invalid"
        ) from exc


def _portfolio_envelopes(
    projection: PortfolioProjection,
    run: EngineRunProjection,
) -> tuple[EventEnvelope[object], ...]:
    request_message_id = run.request_message_id
    if request_message_id is None:
        raise P1PortfolioParityError("durable P1 authority is incomplete")
    stream_id = uuid5(run.engine_run_id, f"p1-portfolio:{projection.canonical_identity}")
    return tuple(
        EventEnvelope[object](
            event_id=item.event_id,
            event_type=type(item.entry).__name__,
            schema_version="event-envelope-v1",
            source="nautilus-p1-portfolio-parity",
            stream_id=stream_id,
            sequence=sequence,
            observed_at=item.entry.effective_at,
            ingested_at=item.entry.effective_at,
            produced_at=item.entry.effective_at,
            effective_at=item.entry.effective_at,
            expires_at=item.entry.effective_at + timedelta(days=1),
            correlation_id=run.engine_run_id,
            causation_id=item.source_message_id,
            trace_id=request_message_id,
            payload=item.entry,
        )
        for sequence, item in enumerate(projection.entries, start=1)
    )


def _verify_p1_portfolio_parity(
    events: tuple[P1Event, ...],
    authority: ProjectionAuthority,
    engine_run_projection: EngineRunProjection,
    *,
    batch_sha256: str,
) -> P1PortfolioParityReceipt:
    run = _validated_run_projection(engine_run_projection)
    projection = project_portfolio(events, authority)
    completion = events[-1]
    position_observation = events[-3]
    account_observation = events[-2]
    if not (
        isinstance(completion, P1RunCompleted)
        and isinstance(position_observation, P1PositionObserved)
        and isinstance(account_observation, P1AccountObserved)
    ):
        raise P1PortfolioParityError("terminal P1 observation trio is invalid")
    counts = Counter(event.event_type for event in events)
    expected_counts = tuple(
        EngineEventTypeCount(event_type=name, count=counts[name])
        for name in sorted(counts)
    )
    if (
        batch_sha256 != run.batch_sha256
        or completion.semantic_digest != run.semantic_digest
        or authority.request_message_id != run.request_message_id
        or len(events) != run.event_count
        or completion.sequence != run.last_sequence
        or expected_counts != run.event_type_counts
    ):
        raise P1PortfolioParityError("durable P1 authority does not match the event stream")

    projected = projection.accounting
    terminal_accounting = (
        completion.final_cash,
        completion.final_position,
        completion.fees,
        completion.realized_pnl,
        completion.unrealized_pnl,
    )
    if terminal_accounting != (
        projected.cash_balance,
        projected.position_quantity,
        projected.fees,
        projected.realized_pnl,
        projected.unrealized_pnl,
    ):
        raise P1PortfolioParityError("projected accounting does not match P1 completion")

    envelopes = _portfolio_envelopes(projection, run)
    if len(envelopes) < 2:
        raise P1PortfolioParityError("portfolio proof requires a non-empty restart tail")
    full = replay_portfolio(envelopes)
    prefix = replay_portfolio(envelopes[:1])
    restarted = replay_portfolio(
        envelopes[1:],
        snapshot=snapshot_from_portfolio_result(prefix),
        authority=snapshot_authority_from_result(prefix),
    )
    if (
        restarted != full
        or restarted.canonical_state_json != full.canonical_state_json
        or restarted.state_hash != full.state_hash
        or restarted.prefix_history_hash != full.prefix_history_hash
    ):
        raise P1PortfolioParityError("full and trusted-prefix replay do not match")

    snapshot = full.canonical_snapshot
    settlement = authority.instrument.settlement_currency
    balances = tuple(
        balance for balance in snapshot.balances if balance.currency is settlement
    )
    positions = tuple(
        position
        for position in snapshot.positions
        if position.strategy_id == authority.strategy_id
        and position.instrument == authority.instrument.instrument_id
    )
    if (
        snapshot.reporting_currency is not settlement
        or len(balances) != 1
        or len(positions) > 1
        or len(positions) != len(snapshot.positions)
        or snapshot.observed_at != account_observation.simulation_time
    ):
        raise P1PortfolioParityError("reduced terminal portfolio scope is invalid")
    balance = balances[0]
    if (
        balance.cash.amount,
        balance.fees.amount,
        balance.realized_pnl.amount,
        balance.unrealized_pnl.amount,
    ) != (
        account_observation.cash_balance,
        account_observation.fees,
        account_observation.realized_pnl,
        account_observation.unrealized_pnl,
    ):
        raise P1PortfolioParityError("reduced account does not match P1 observation")

    position = positions[0] if positions else None
    reduced_quantity = position.quantity.value if position else Decimal(0)
    reduced_average = (
        position.average_entry_price.amount
        if position and position.average_entry_price is not None
        else None
    )
    reduced_mark = (
        position.mark.price.amount if position and position.mark is not None else None
    )
    normalized_average = (
        None
        if position_observation.quantity == 0
        and position_observation.average_entry_price == 0
        else position_observation.average_entry_price
    )
    if (
        reduced_quantity != position_observation.quantity
        or reduced_average != normalized_average
        or (reduced_quantity == 0) != (reduced_mark is None)
        or (
            position is not None
            and (
                position.settlement_currency is not settlement
                or position.realized_pnl.amount != position_observation.realized_pnl
                or position.unrealized_pnl.amount != position_observation.unrealized_pnl
                or position.fees.amount != account_observation.fees
            )
        )
    ):
        raise P1PortfolioParityError("reduced position does not match P1 observation")

    return P1PortfolioParityReceipt(
        schema_version="nautilus-p1-portfolio-parity-v1",
        normalization_version="nautilus-p1-portfolio-normalization-v1",
        engine_run_id=run.engine_run_id,
        batch_sha256=batch_sha256,
        semantic_digest=completion.semantic_digest,
        request_message_id=authority.request_message_id,
        engine_event_count=run.event_count,
        engine_last_sequence=run.last_sequence,
        engine_last_digest=run.last_digest,
        projection_identity=projection.canonical_identity,
        portfolio_stream_id=envelopes[0].stream_id,
        portfolio_event_count=len(envelopes),
        portfolio_last_sequence=envelopes[-1].sequence,
        restart_prefix_sequence=1,
        portfolio_state_hash=full.state_hash,
        portfolio_prefix_history_hash=full.prefix_history_hash,
        account_id=snapshot.account_id,
        account_currency=settlement,
        terminal_position=reduced_quantity,
        terminal_average_entry_price=reduced_average,
        terminal_mark_price=reduced_mark,
        terminal_cash=balance.cash.amount,
        terminal_fees=balance.fees.amount,
        terminal_realized_pnl=balance.realized_pnl.amount,
        terminal_unrealized_pnl=balance.unrealized_pnl.amount,
        observed_at=snapshot.observed_at,
    )


def verify_p1_portfolio_parity(
    events: tuple[P1Event, ...],
    authority: ProjectionAuthority,
    engine_run_projection: EngineRunProjection,
    *,
    batch_sha256: str,
) -> P1PortfolioParityReceipt:
    """Return a receipt only when engine, projection, and reducer agree exactly."""

    try:
        return _verify_p1_portfolio_parity(
            events,
            authority,
            engine_run_projection,
            batch_sha256=batch_sha256,
        )
    except P1PortfolioParityError:
        raise
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise P1PortfolioParityError("P1 portfolio parity verification failed") from exc

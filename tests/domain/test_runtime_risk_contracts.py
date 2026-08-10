from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from packages.domain import (
    AccountBalanceSnapshot,
    AccountPortfolioSnapshot,
    AccountPositionSnapshot,
    Currency,
    ExposureSnapshot,
    InstrumentId,
    InstrumentExposureSnapshot,
    Money,
    OrderQuantity,
    PositionMark,
    Price,
    ProductType,
    Quantity,
    RuntimeRiskPolicy as PublicRuntimeRiskPolicy,
    StrategyExposureSnapshot,
    VenueExposureSnapshot,
)
from packages.domain.runtime_risk import (
    DurableOrderApprovalRef,
    PriorRuntimeCommandIdentity,
    RuntimeInstrumentRiskSpec,
    RuntimeOrderRiskDecision,
    RuntimeRiskConversionRate,
    RuntimeRiskMarketSnapshot,
    RuntimeRiskObservation,
    RuntimeRiskOutcome,
    RuntimeRiskPolicy,
    RuntimeRiskReasonCode,
    RuntimeVenueHealth,
    RuntimeVenueHealthRecord,
)
from packages.runtime_risk import canonical_model_digest, canonical_model_json


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "SIM")
DIGEST = "a" * 64
MAX_RUNTIME_RISK_DURATION_SECONDS = 86_399_999_999_999

EXPECTED_REASON_ORDER = (
    "POLICY_RISK_NOT_APPROVED",
    "ENGINE_NOT_READY",
    "INSTRUMENT_UNKNOWN",
    "MARKET_DATA_STALE",
    "VALUATION_AUTHORITY_MISSING",
    "PORTFOLIO_STATE_INVALID",
    "PRICE_PRECISION_INVALID",
    "QUANTITY_PRECISION_INVALID",
    "QUANTITY_OUT_OF_BOUNDS",
    "ORDER_NOTIONAL_LIMIT",
    "BALANCE_MARGIN_LIMIT",
    "PENDING_EXPOSURE_LIMIT",
    "GROSS_EXPOSURE_LIMIT",
    "NET_EXPOSURE_LIMIT",
    "STRATEGY_EXPOSURE_LIMIT",
    "VENUE_EXPOSURE_LIMIT",
    "DAILY_LOSS_LIMIT",
    "DRAWDOWN_LIMIT",
    "REDUCE_ONLY_VIOLATION",
    "COMMAND_RATE_LIMIT",
    "VENUE_UNHEALTHY",
    "DUPLICATE_COMMAND",
    "WITHIN_LIMITS",
)


class _CanonicalChild(BaseModel):
    model_config = ConfigDict(strict=True)

    amount: int

    @field_validator("amount")
    @classmethod
    def _positive_amount(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("amount must be positive")
        return value


class _CanonicalParent(BaseModel):
    model_config = ConfigDict(strict=True)

    child: _CanonicalChild


class _StrictUuidChild(BaseModel):
    model_config = ConfigDict(strict=True)

    identifier: UUID


class _StrictDecimalChild(BaseModel):
    model_config = ConfigDict(strict=True)

    amount: Decimal


class _StrictPrimitiveParent(BaseModel):
    model_config = ConfigDict(strict=True)

    child: _StrictUuidChild | _StrictDecimalChild


class _FrozenSetChild(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    identifier: UUID


class _SetParent(BaseModel):
    model_config = ConfigDict(strict=True)

    children: set[_FrozenSetChild]


class _FrozenSetParent(BaseModel):
    model_config = ConfigDict(strict=True)

    children: frozenset[_FrozenSetChild]


class _ArbitraryContainerParent(BaseModel):
    model_config = ConfigDict(strict=True)

    payload: Any


def uid(value: int) -> UUID:
    return UUID(int=value)


def money(value: str) -> Money:
    return Money(Decimal(value), Currency.USD)


def portfolio() -> AccountPortfolioSnapshot:
    balance = AccountBalanceSnapshot(
        account_id="account-1", currency=Currency.USD, cash=money("1000"),
        locked_funds=money("0"), margin_used=money("0"), realized_pnl=money("0"),
        unrealized_pnl=money("0"), fees=money("0"), funding=money("0"),
        observed_at=NOW, schema_version="account-balance-v1",
    )
    position = AccountPositionSnapshot(
        account_id="account-1", strategy_id="strategy-1", instrument=INSTRUMENT,
        settlement_currency=Currency.USD, quantity=Quantity(Decimal("1"), 0),
        mark=PositionMark(price=Price(Decimal("100"), Currency.USD), marked_at=NOW, provenance_id="sim-mark"),
        average_entry_price=Price(Decimal("100"), Currency.USD), realized_pnl=money("0"),
        unrealized_pnl=money("0"), fees=money("0"), funding=money("0"),
        observed_at=NOW, schema_version="account-position-v1",
    )
    exposure = ExposureSnapshot(currency=Currency.USD, gross=money("100"), net=money("100"), pending=money("0"))
    return AccountPortfolioSnapshot(
        snapshot_id=uid(1), account_id="account-1", reporting_currency=Currency.USD,
        balances=(balance,), positions=(position,), total_exposure=exposure,
        instrument_exposures=(InstrumentExposureSnapshot(instrument=INSTRUMENT, exposure=exposure),),
        strategy_exposures=(StrategyExposureSnapshot(strategy_id="strategy-1", exposure=exposure),),
        venue_exposures=(VenueExposureSnapshot(venue_id="SIM", exposure=exposure),),
        observed_at=NOW, schema_version="account-portfolio-v1",
    )


def instrument_spec(**changes: object) -> RuntimeInstrumentRiskSpec:
    values: dict[str, object] = {
        "instrument": INSTRUMENT, "venue_id": "SIM", "settlement_currency": Currency.USD,
        "price_increment": Price(Decimal("0.01"), Currency.USD),
        "quantity_increment": OrderQuantity(Decimal("0.001"), 3),
        "min_quantity": OrderQuantity(Decimal("0.001"), 3),
        "max_quantity": OrderQuantity(Decimal("10"), 3),
        "min_order_notional": money("1"), "max_order_notional": money("100000"),
        "initial_margin_rate": Decimal("0.1"),
    }
    values.update(changes)
    return RuntimeInstrumentRiskSpec(**values)


def policy(**changes: object) -> RuntimeRiskPolicy:
    values: dict[str, object] = {
        "policy_id": uid(2), "policy_version": "policy-v1", "account_id": "account-1",
        "market_data_max_age_seconds": 10, "portfolio_max_age_seconds": 10,
        "max_pending_exposure": money("100"), "max_gross_exposure": money("1000"),
        "max_abs_net_exposure": money("1000"), "max_strategy_exposure": money("500"),
        "max_venue_exposure": money("1000"), "min_available_funds": money("0"),
        "max_daily_loss": money("100"), "max_drawdown": money("200"),
        "command_window_seconds": 60, "max_commands_per_window": 5,
        "schema_version": "runtime-risk-policy-v1",
    }
    values.update(changes)
    return RuntimeRiskPolicy(**values)


def observation(**changes: object) -> RuntimeRiskObservation:
    values: dict[str, object] = {
        "observation_id": uid(3), "state_version": 1, "portfolio": portfolio(),
        "instrument_specs": (instrument_spec(),),
        "market_snapshots": (RuntimeRiskMarketSnapshot(instrument=INSTRUMENT, bid=Price(Decimal("99"), Currency.USD), ask=Price(Decimal("101"), Currency.USD), last=Price(Decimal("100"), Currency.USD), observed_at=NOW, provenance_id="sim-book"),),
        "conversion_rates": (RuntimeRiskConversionRate(source_currency=Currency.BTC, target_currency=Currency.USD, rate=Decimal("100"), observed_at=NOW, provenance_id="sim-fx"),),
        "venue_health": (RuntimeVenueHealthRecord(venue_id="SIM", health=RuntimeVenueHealth.HEALTHY, observed_at=NOW),),
        "engine_ready": True, "daily_pnl": money("0"), "current_equity": money("1000"),
        "peak_equity": money("1000"), "command_window_started_at": NOW,
        "commands_in_window": 0, "prior_commands": (), "observed_at": NOW,
        "schema_version": "runtime-risk-observation-v1",
    }
    values.update(changes)
    return RuntimeRiskObservation(**values)


def approved_decision(**changes: object) -> RuntimeOrderRiskDecision:
    values: dict[str, object] = {
        "decision_id": uid(4), "intent_id": uid(5), "risk_decision_id": uid(6),
        "intent_digest": DIGEST, "policy_risk_decision_digest": DIGEST,
        "portfolio_snapshot_id": uid(1), "portfolio_digest": DIGEST,
        "observation_id": uid(3), "observation_version": 1, "observation_digest": DIGEST,
        "policy_id": uid(2), "policy_version": "policy-v1", "policy_digest": DIGEST,
        "risk_price": Price(Decimal("100"), Currency.USD), "order_notional": money("100"),
        "projected_position_quantity": Quantity(Decimal("2"), 0), "projected_pending": money("0"),
        "projected_gross": money("200"), "projected_net": money("200"),
        "projected_strategy_gross": money("200"), "projected_venue_gross": money("200"),
        "projected_instrument_gross": money("200"), "projected_margin_used": money("20"),
        "projected_available_funds": money("980"), "outcome": RuntimeRiskOutcome.APPROVED,
        "reason_codes": (RuntimeRiskReasonCode.WITHIN_LIMITS,), "decided_at": NOW,
        "schema_version": "runtime-order-risk-decision-v1",
    }
    values.update(changes)
    return RuntimeOrderRiskDecision(**values)


def approval_ref(**changes: object) -> DurableOrderApprovalRef:
    values: dict[str, object] = {
        "decision_outcome": RuntimeRiskOutcome.APPROVED, "event_id": uid(7), "stream_id": uid(8),
        "sequence": 1, "event_digest": DIGEST, "decision_id": uid(4), "decision_digest": DIGEST,
        "intent_id": uid(5), "intent_digest": DIGEST, "risk_decision_id": uid(6),
        "policy_risk_decision_digest": DIGEST, "portfolio_snapshot_id": uid(1),
        "portfolio_digest": DIGEST, "observation_id": uid(3), "observation_version": 1,
        "observation_digest": DIGEST, "policy_id": uid(2), "policy_version": "policy-v1",
        "policy_digest": DIGEST, "schema_version": "durable-order-approval-v1",
    }
    values.update(changes)
    return DurableOrderApprovalRef(**values)


def test_runtime_risk_reason_codes_are_the_complete_stable_contract() -> None:
    assert tuple(code.value for code in RuntimeRiskReasonCode) == EXPECTED_REASON_ORDER


def test_runtime_contracts_are_strict_frozen_and_export_existing_domain_types() -> None:
    spec = instrument_spec()
    assert spec.instrument == INSTRUMENT
    assert PublicRuntimeRiskPolicy is RuntimeRiskPolicy
    with pytest.raises(ValidationError):
        instrument_spec(venue_id=1)
    with pytest.raises(ValidationError):
        spec.venue_id = "OTHER"  # type: ignore[misc]


def test_runtime_contracts_require_exact_decimals_strict_scalars_and_utc_datetimes() -> None:
    with pytest.raises(ValidationError, match="Decimal"):
        instrument_spec(initial_margin_rate=0.1)
    with pytest.raises(ValidationError):
        policy(max_commands_per_window=True)
    with pytest.raises(ValidationError):
        observation(engine_ready=1)
    with pytest.raises(ValidationError, match="UTC"):
        RuntimeVenueHealthRecord(
            venue_id="SIM",
            health=RuntimeVenueHealth.HEALTHY,
            observed_at=NOW.astimezone(timezone(timedelta(hours=1))),
        )


def test_instrument_spec_requires_positive_increments_and_ordered_shared_currency_bounds() -> None:
    with pytest.raises(ValidationError, match="price_increment"):
        instrument_spec(price_increment={"amount": Decimal("0"), "currency": Currency.USD})
    with pytest.raises(ValidationError, match="quantity_increment"):
        instrument_spec(quantity_increment=OrderQuantity(Decimal("0"), 3))
    with pytest.raises(ValidationError, match="max_quantity"):
        instrument_spec(max_quantity=OrderQuantity(Decimal("0.0001"), 4))
    with pytest.raises(ValidationError, match="notional"):
        instrument_spec(min_order_notional=money("100001"))
    with pytest.raises(ValidationError, match="notional bounds currency"):
        instrument_spec(max_order_notional=Money(Decimal("100000"), Currency.USDT))
    with pytest.raises(ValidationError, match="initial_margin_rate"):
        instrument_spec(initial_margin_rate=Decimal("1.01"))


def test_cross_currency_instrument_notional_bounds_use_reporting_currency() -> None:
    spec = instrument_spec(
        settlement_currency=Currency.USDT,
        price_increment=Price(Decimal("0.01"), Currency.USDT),
        min_order_notional=money("1"),
        max_order_notional=money("100000"),
    )

    assert spec.settlement_currency is Currency.USDT
    assert spec.min_order_notional.currency is Currency.USD
    assert spec.max_order_notional.currency is Currency.USD


def test_observation_requires_instrument_notional_bounds_in_reporting_currency() -> None:
    spec = instrument_spec(
        settlement_currency=Currency.USDT,
        price_increment=Price(Decimal("0.01"), Currency.USDT),
        min_order_notional=Money(Decimal("1"), Currency.USDT),
        max_order_notional=Money(Decimal("100000"), Currency.USDT),
    )

    with pytest.raises(ValidationError, match="reporting currency"):
        observation(instrument_specs=(spec,))


def test_market_and_conversion_contracts_require_ordered_currency_aligned_positive_quotes() -> None:
    with pytest.raises(ValidationError, match="bid"):
        RuntimeRiskMarketSnapshot(instrument=INSTRUMENT, bid=Price(Decimal("102"), Currency.USD), ask=Price(Decimal("101"), Currency.USD), last=Price(Decimal("100"), Currency.USD), observed_at=NOW, provenance_id="sim-book")
    with pytest.raises(ValidationError, match="currency"):
        RuntimeRiskMarketSnapshot(instrument=INSTRUMENT, bid=Price(Decimal("99"), Currency.USD), ask=Price(Decimal("101"), Currency.USDT), last=Price(Decimal("100"), Currency.USD), observed_at=NOW, provenance_id="sim-book")
    with pytest.raises(ValidationError, match="source_currency"):
        RuntimeRiskConversionRate(source_currency=Currency.USD, target_currency=Currency.USD, rate=Decimal("1"), observed_at=NOW, provenance_id="sim-fx")
    with pytest.raises(ValidationError, match="rate"):
        RuntimeRiskConversionRate(source_currency=Currency.BTC, target_currency=Currency.USD, rate=Decimal("0"), observed_at=NOW, provenance_id="sim-fx")


def test_policy_and_observation_require_nonnegative_counts_matching_currencies_and_unique_prior_commands() -> None:
    with pytest.raises(ValidationError, match="max_commands_per_window"):
        policy(max_commands_per_window=-1)
    with pytest.raises(ValidationError, match="market_data_max_age_seconds"):
        policy(market_data_max_age_seconds=0)
    with pytest.raises(ValidationError, match="policy money"):
        policy(max_daily_loss=Money(Decimal("100"), Currency.USDT))
    prior = PriorRuntimeCommandIdentity(intent_id=uid(9), client_order_id="client-1")
    with pytest.raises(ValidationError, match="prior_commands"):
        observation(prior_commands=(prior, prior))
    with pytest.raises(ValidationError, match="commands_in_window"):
        observation(commands_in_window=-1)
    with pytest.raises(ValidationError, match="reporting currency"):
        observation(daily_pnl=Money(Decimal("0"), Currency.USDT))


@pytest.mark.parametrize("authority", ("portfolio", "market", "conversion", "venue"))
def test_observation_rejects_child_source_facts_after_observation_time(
    authority: str,
) -> None:
    future = NOW + timedelta(seconds=1)
    changes: dict[str, object]
    if authority == "portfolio":
        changes = {"portfolio": portfolio().model_copy(update={"observed_at": future})}
    elif authority == "market":
        market = observation().market_snapshots[0].model_copy(
            update={"observed_at": future}
        )
        changes = {"market_snapshots": (market,)}
    elif authority == "conversion":
        conversion = observation().conversion_rates[0].model_copy(
            update={"observed_at": future}
        )
        changes = {"conversion_rates": (conversion,)}
    else:
        venue = observation().venue_health[0].model_copy(
            update={"observed_at": future}
        )
        changes = {"venue_health": (venue,)}

    with pytest.raises(ValidationError, match="after observation"):
        observation(**changes)


def test_observation_rejects_command_window_start_after_observation_time() -> None:
    with pytest.raises(ValidationError, match="command window"):
        observation(command_window_started_at=NOW + timedelta(seconds=1))


def test_observation_requires_canonical_order_for_each_identity_collection() -> None:
    second_spec = instrument_spec(venue_id="SIM-2")
    with pytest.raises(ValidationError, match="instrument_specs must be canonically ordered"):
        observation(instrument_specs=(second_spec, instrument_spec()))
    second_venue = RuntimeVenueHealthRecord(venue_id="SIM-2", health=RuntimeVenueHealth.HEALTHY, observed_at=NOW)
    with pytest.raises(ValidationError, match="venue_health must be canonically ordered"):
        observation(venue_health=(second_venue, RuntimeVenueHealthRecord(venue_id="SIM", health=RuntimeVenueHealth.HEALTHY, observed_at=NOW)))
    second_instrument = InstrumentId("ETH-USD", ProductType.CRYPTO_SPOT, "SIM")
    btc_market = RuntimeRiskMarketSnapshot(instrument=INSTRUMENT, bid=Price(Decimal("99"), Currency.USD), ask=Price(Decimal("101"), Currency.USD), last=Price(Decimal("100"), Currency.USD), observed_at=NOW, provenance_id="sim-btc-book")
    eth_market = RuntimeRiskMarketSnapshot(instrument=second_instrument, bid=Price(Decimal("49"), Currency.USD), ask=Price(Decimal("51"), Currency.USD), last=Price(Decimal("50"), Currency.USD), observed_at=NOW, provenance_id="sim-eth-book")
    with pytest.raises(ValidationError, match="market_snapshots must be canonically ordered"):
        observation(market_snapshots=(eth_market, btc_market))
    btc_conversion = RuntimeRiskConversionRate(source_currency=Currency.BTC, target_currency=Currency.USD, rate=Decimal("100"), observed_at=NOW, provenance_id="sim-btc-fx")
    eth_conversion = RuntimeRiskConversionRate(source_currency=Currency.ETH, target_currency=Currency.USD, rate=Decimal("50"), observed_at=NOW, provenance_id="sim-eth-fx")
    with pytest.raises(ValidationError, match="conversion_rates must be canonically ordered"):
        observation(conversion_rates=(eth_conversion, btc_conversion))
    first_prior = PriorRuntimeCommandIdentity(intent_id=uid(9), client_order_id="client-1")
    second_prior = PriorRuntimeCommandIdentity(intent_id=uid(10), client_order_id="client-2")
    with pytest.raises(ValidationError, match="prior_commands must be canonically ordered"):
        observation(prior_commands=(second_prior, first_prior))


def test_runtime_policy_limits_and_margin_rate_include_their_boundaries() -> None:
    assert instrument_spec(initial_margin_rate=Decimal("0")).initial_margin_rate == Decimal("0")
    assert instrument_spec(initial_margin_rate=Decimal("1")).initial_margin_rate == Decimal("1")
    assert policy(min_available_funds=money("0")).min_available_funds.amount == Decimal("0")


@pytest.mark.parametrize(
    "field",
    (
        "market_data_max_age_seconds",
        "portfolio_max_age_seconds",
        "command_window_seconds",
    ),
)
def test_runtime_policy_duration_fields_accept_exact_supported_maximum(
    field: str,
) -> None:
    assert getattr(
        policy(**{field: MAX_RUNTIME_RISK_DURATION_SECONDS}),
        field,
    ) == MAX_RUNTIME_RISK_DURATION_SECONDS


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (field, value)
        for field in (
            "market_data_max_age_seconds",
            "portfolio_max_age_seconds",
            "command_window_seconds",
        )
        for value in (MAX_RUNTIME_RISK_DURATION_SECONDS + 1, 10**30)
    ],
)
def test_runtime_policy_duration_fields_reject_values_above_supported_maximum(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError, match="less than or equal"):
        policy(**{field: value})


def test_runtime_order_decision_enforces_outcome_projection_and_reason_semantics() -> None:
    approved = approved_decision()
    rejected = approved_decision(
        outcome=RuntimeRiskOutcome.REJECTED,
        reason_codes=(RuntimeRiskReasonCode.ENGINE_NOT_READY,),
        risk_price=None, order_notional=None, projected_position_quantity=None,
        projected_pending=None, projected_gross=None, projected_net=None,
        projected_strategy_gross=None, projected_venue_gross=None,
        projected_instrument_gross=None, projected_margin_used=None,
        projected_available_funds=None,
    )
    assert rejected.outcome is RuntimeRiskOutcome.REJECTED
    with pytest.raises(ValueError):
        approved.model_copy(update={"reason_codes": (RuntimeRiskReasonCode.ENGINE_NOT_READY,)})
    with pytest.raises(ValueError):
        rejected.model_copy(update={"reason_codes": (RuntimeRiskReasonCode.WITHIN_LIMITS,)})
    with pytest.raises(ValueError):
        approved.model_copy(update={"risk_price": None})
    with pytest.raises(ValidationError, match="canonical risk-check order"):
        approved_decision(outcome=RuntimeRiskOutcome.REJECTED, reason_codes=(RuntimeRiskReasonCode.VENUE_UNHEALTHY, RuntimeRiskReasonCode.ENGINE_NOT_READY))


def test_approval_reference_is_approval_only_and_model_copy_revalidates() -> None:
    approved_ref = approval_ref()
    with pytest.raises(ValueError):
        approved_ref.model_copy(update={"decision_outcome": RuntimeRiskOutcome.REJECTED})


@pytest.mark.parametrize("contract_kind", ("instrument", "policy", "observation"))
def test_deep_model_copy_preserves_registered_currency_singleton_authority(
    contract_kind: str,
) -> None:
    if contract_kind == "instrument":
        original = instrument_spec()
        copied = original.model_copy(deep=True)
        assert copied.price_increment is not original.price_increment
        currency_pairs = (
            (copied.settlement_currency, original.settlement_currency),
            (copied.price_increment.currency, original.price_increment.currency),
            (copied.min_order_notional.currency, original.min_order_notional.currency),
        )
    elif contract_kind == "policy":
        original = policy()
        copied = original.model_copy(deep=True)
        assert copied.max_pending_exposure is not original.max_pending_exposure
        currency_pairs = (
            (
                copied.max_pending_exposure.currency,
                original.max_pending_exposure.currency,
            ),
            (copied.max_drawdown.currency, original.max_drawdown.currency),
        )
    else:
        original = observation()
        copied = original.model_copy(deep=True)
        assert copied.portfolio is not original.portfolio
        assert copied.instrument_specs[0] is not original.instrument_specs[0]
        currency_pairs = (
            (
                copied.portfolio.reporting_currency,
                original.portfolio.reporting_currency,
            ),
            (
                copied.portfolio.balances[0].currency,
                original.portfolio.balances[0].currency,
            ),
            (
                copied.instrument_specs[0].settlement_currency,
                original.instrument_specs[0].settlement_currency,
            ),
            (
                copied.market_snapshots[0].bid.currency,
                original.market_snapshots[0].bid.currency,
            ),
        )

    assert copied == original
    assert copied is not original
    assert all(actual is expected for actual, expected in currency_pairs)


def test_canonical_identity_has_stable_json_and_digest_vectors() -> None:
    value = PriorRuntimeCommandIdentity(intent_id=uid(9), client_order_id="client-1")
    assert canonical_model_json(value) == (
        '{"client_order_id":"client-1","intent_id":"00000000-0000-0000-0000-000000000009"}'
    )
    assert canonical_model_digest(value) == "5b886f1f3ba909ef35d322209d3797296d4663cd401e923b6d3406d1ac9763ec"


def test_canonical_identity_rejects_top_level_and_nested_forged_models() -> None:
    with pytest.raises(ValueError, match="Pydantic model"):
        canonical_model_json("not-a-model")  # type: ignore[arg-type]
    incomplete = DurableOrderApprovalRef.model_construct(decision_outcome=RuntimeRiskOutcome.APPROVED)
    with pytest.raises(ValueError, match="canonically represented"):
        canonical_model_digest(incomplete)
    forged_child = _CanonicalChild.model_construct(amount=-1)
    forged_parent = _CanonicalParent(child=forged_child)
    with pytest.raises(ValueError, match="canonically represented"):
        canonical_model_json(forged_parent)
    forged_nested_incomplete = _CanonicalParent.model_construct(
        child=_CanonicalChild.model_construct()
    )
    with pytest.raises(ValueError, match="canonically represented"):
        canonical_model_digest(forged_nested_incomplete)


def test_canonical_identity_preserves_strict_uuid_and_decimal_python_types() -> None:
    forged_approval = approval_ref()
    object.__setattr__(forged_approval, "decision_id", str(uid(4)))
    with pytest.raises(ValueError, match="canonically represented"):
        canonical_model_digest(forged_approval)
    forged_uuid_parent = _StrictPrimitiveParent(
        child=_StrictUuidChild.model_construct(identifier=str(uid(1)))
    )
    with pytest.raises(ValueError, match="canonically represented"):
        canonical_model_json(forged_uuid_parent)
    forged_decimal_parent = _StrictPrimitiveParent(
        child=_StrictDecimalChild.model_construct(amount="1.25")
    )
    with pytest.raises(ValueError, match="canonically represented"):
        canonical_model_json(forged_decimal_parent)


def test_canonical_identity_preserves_valid_set_and_frozenset_model_members() -> None:
    child = _FrozenSetChild(identifier=uid(1))
    assert canonical_model_json(_SetParent(children={child})) == (
        '{"children":[{"identifier":"00000000-0000-0000-0000-000000000001"}]}'
    )
    assert canonical_model_json(_FrozenSetParent(children=frozenset({child}))) == (
        '{"children":[{"identifier":"00000000-0000-0000-0000-000000000001"}]}'
    )


def test_canonical_identity_rejects_forged_set_and_frozenset_members() -> None:
    forged_child = _FrozenSetChild.model_construct(identifier=str(uid(1)))
    with pytest.raises(ValueError, match="canonically represented"):
        canonical_model_json(_SetParent(children={forged_child}))
    with pytest.raises(ValueError, match="canonically represented"):
        canonical_model_json(_FrozenSetParent(children=frozenset({forged_child})))


@pytest.mark.parametrize("container_kind", ("runtime-model", "mapping", "list"))
def test_canonical_identity_rejects_recursive_model_and_container_graphs(
    container_kind: str,
) -> None:
    if container_kind == "runtime-model":
        value: BaseModel = observation()
        object.__setattr__(value, "portfolio", value)
    elif container_kind == "mapping":
        recursive_mapping: dict[str, object] = {}
        recursive_mapping["self"] = recursive_mapping
        value = _ArbitraryContainerParent(payload=recursive_mapping)
    else:
        recursive_list: list[object] = []
        recursive_list.append(recursive_list)
        value = _ArbitraryContainerParent(payload=recursive_list)

    with pytest.raises(ValueError, match="canonically represented") as caught:
        canonical_model_json(value)

    assert type(caught.value.__cause__) is ValueError

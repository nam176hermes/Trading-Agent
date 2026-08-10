from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from packages.domain import (
    AccountBalanceSnapshot,
    AccountPortfolioSnapshot,
    AccountPositionSnapshot,
    Currency,
    ExposureSnapshot,
    InstrumentExposureSnapshot,
    InstrumentId,
    Money,
    OrderIntent,
    OrderQuantity,
    OrderSide,
    OrderType,
    PositionMark,
    Price,
    ProductType,
    Quantity,
    StrategyExposureSnapshot,
    TimeInForce,
    VenueExposureSnapshot,
)
from packages.domain.runtime_risk import (
    RuntimeInstrumentRiskSpec,
    RuntimeRiskConversionRate,
    RuntimeRiskMarketSnapshot,
    RuntimeRiskObservation,
    RuntimeRiskPolicy,
    RuntimeVenueHealth,
    RuntimeVenueHealthRecord,
)
from packages.runtime_risk import (
    ProjectionError,
    RuntimeRiskProjection,
    canonical_model_digest,
    project_runtime_order,
)


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "SIM")
SAME_VENUE_INSTRUMENT = InstrumentId(
    "ETH-USD", ProductType.CRYPTO_SPOT, "SIM"
)
OTHER_VENUE_INSTRUMENT = InstrumentId(
    "SOL-USD", ProductType.CRYPTO_SPOT, "ALT"
)


def uid(value: int) -> UUID:
    return UUID(int=value)


def money(amount: str | Decimal, currency: Currency = Currency.USD) -> Money:
    return Money(Decimal(amount), currency)


def exposure(
    gross: str | Decimal,
    net: str | Decimal,
    *,
    pending: str | Decimal = "5",
) -> ExposureSnapshot:
    return ExposureSnapshot(
        currency=Currency.USD,
        gross=money(gross),
        net=money(net),
        pending=money(pending),
    )


@dataclass(frozen=True)
class RuntimeCase:
    observation: RuntimeRiskObservation
    policy: RuntimeRiskPolicy
    decided_at: datetime = NOW

    def make_intent(
        self,
        *,
        side: OrderSide = OrderSide.BUY,
        order_type: OrderType = OrderType.MARKET,
        quantity_amount: Decimal = Decimal("1"),
        quantity_precision: int = 0,
        limit_amount: Decimal | None = None,
        trigger_amount: Decimal | None = None,
    ) -> OrderIntent:
        market = self.observation.market_snapshots[0]
        return OrderIntent(
            intent_id=uid(11),
            risk_decision_id=uid(12),
            client_order_id="client-1",
            strategy_id="strategy-1",
            trader_id="trader-1",
            account_id="account-1",
            execution_client_id="execution-1",
            instrument=market.instrument,
            side=side,
            order_type=order_type,
            time_in_force=TimeInForce.GTC,
            quantity=OrderQuantity(quantity_amount, quantity_precision),
            limit_price=(
                None
                if limit_amount is None
                else Price(limit_amount, market.bid.currency)
            ),
            trigger_price=(
                None
                if trigger_amount is None
                else Price(trigger_amount, market.bid.currency)
            ),
            requested_at=NOW,
            schema_version="order-intent-v1",
        )


def runtime_case(
    *,
    current_quantity: Decimal = Decimal("2"),
    quantity_precision: int = 0,
    settlement_currency: Currency = Currency.USD,
    reporting_rate: Decimal | None = None,
    initial_margin_rate: Decimal = Decimal("0.1"),
    include_position: bool = True,
    include_partitions: bool = True,
    pending: Decimal = Decimal("5"),
) -> RuntimeCase:
    rate = Decimal("1") if reporting_rate is None else reporting_rate
    current_notional = abs(current_quantity) * Decimal("100") * rate
    signed_notional = current_notional if current_quantity >= 0 else -current_notional
    market = RuntimeRiskMarketSnapshot(
        instrument=INSTRUMENT,
        bid=Price(Decimal("99"), settlement_currency),
        ask=Price(Decimal("101"), settlement_currency),
        last=Price(Decimal("100"), settlement_currency),
        observed_at=NOW,
        provenance_id="sim-book",
    )
    spec = RuntimeInstrumentRiskSpec(
        instrument=INSTRUMENT,
        venue_id="SIM",
        settlement_currency=settlement_currency,
        price_increment=Price(Decimal("0.01"), settlement_currency),
        quantity_increment=OrderQuantity(Decimal("0.001"), 3),
        min_quantity=OrderQuantity(Decimal("0.001"), 3),
        max_quantity=OrderQuantity(Decimal("1000000"), 3),
        min_order_notional=money("1", settlement_currency),
        max_order_notional=money("100000000", settlement_currency),
        initial_margin_rate=initial_margin_rate,
    )
    balance = AccountBalanceSnapshot(
        account_id="account-1",
        currency=Currency.USD,
        cash=money("1000"),
        locked_funds=money("50"),
        margin_used=money("20"),
        realized_pnl=money("0"),
        unrealized_pnl=money("0"),
        fees=money("0"),
        funding=money("0"),
        observed_at=NOW,
        schema_version="account-balance-v1",
    )
    positions: tuple[AccountPositionSnapshot, ...] = ()
    if include_position:
        mark = None
        average_entry_price = None
        if current_quantity != 0:
            mark = PositionMark(
                price=Price(Decimal("100"), settlement_currency),
                marked_at=NOW,
                provenance_id="sim-mark",
            )
            average_entry_price = Price(Decimal("100"), settlement_currency)
        positions = (
            AccountPositionSnapshot(
                account_id="account-1",
                strategy_id="strategy-1",
                instrument=INSTRUMENT,
                settlement_currency=settlement_currency,
                quantity=Quantity(current_quantity, quantity_precision),
                mark=mark,
                average_entry_price=average_entry_price,
                realized_pnl=money("0", settlement_currency),
                unrealized_pnl=money("0", settlement_currency),
                fees=money("0", settlement_currency),
                funding=money("0", settlement_currency),
                observed_at=NOW,
                schema_version="account-position-v1",
            ),
        )
    current_exposure = exposure(current_notional, signed_notional, pending=pending)
    instrument_exposures = ()
    strategy_exposures = ()
    venue_exposures = ()
    if include_partitions:
        instrument_exposures = (
            InstrumentExposureSnapshot(
                instrument=INSTRUMENT,
                exposure=current_exposure,
            ),
        )
        strategy_exposures = (
            StrategyExposureSnapshot(
                strategy_id="strategy-1",
                exposure=current_exposure,
            ),
        )
        venue_exposures = (
            VenueExposureSnapshot(venue_id="SIM", exposure=current_exposure),
        )
    portfolio = AccountPortfolioSnapshot(
        snapshot_id=uid(1),
        account_id="account-1",
        reporting_currency=Currency.USD,
        balances=(balance,),
        positions=positions,
        total_exposure=current_exposure,
        instrument_exposures=instrument_exposures,
        strategy_exposures=strategy_exposures,
        venue_exposures=venue_exposures,
        observed_at=NOW,
        schema_version="account-portfolio-v1",
    )
    conversions = ()
    if settlement_currency is not Currency.USD and reporting_rate is not None:
        conversions = (
            RuntimeRiskConversionRate(
                source_currency=settlement_currency,
                target_currency=Currency.USD,
                rate=reporting_rate,
                observed_at=NOW,
                provenance_id="sim-fx",
            ),
        )
    observation = RuntimeRiskObservation(
        observation_id=uid(2),
        state_version=1,
        portfolio=portfolio,
        instrument_specs=(spec,),
        market_snapshots=(market,),
        conversion_rates=conversions,
        venue_health=(
            RuntimeVenueHealthRecord(
                venue_id="SIM",
                health=RuntimeVenueHealth.HEALTHY,
                observed_at=NOW,
            ),
        ),
        engine_ready=True,
        daily_pnl=money("0"),
        current_equity=money("1000"),
        peak_equity=money("1000"),
        command_window_started_at=NOW,
        commands_in_window=0,
        prior_commands=(),
        observed_at=NOW,
        schema_version="runtime-risk-observation-v1",
    )
    policy = RuntimeRiskPolicy(
        policy_id=uid(3),
        policy_version="policy-v1",
        account_id="account-1",
        market_data_max_age_seconds=10,
        portfolio_max_age_seconds=10,
        max_pending_exposure=money("100000000"),
        max_gross_exposure=money("100000000"),
        max_abs_net_exposure=money("100000000"),
        max_strategy_exposure=money("100000000"),
        max_venue_exposure=money("100000000"),
        min_available_funds=money("0"),
        max_daily_loss=money("100000000"),
        max_drawdown=money("100000000"),
        command_window_seconds=60,
        max_commands_per_window=10,
        schema_version="runtime-risk-policy-v1",
    )
    return RuntimeCase(observation=observation, policy=policy)


@pytest.fixture(name="case")
def fixture_runtime_case() -> RuntimeCase:
    return runtime_case()


@pytest.mark.parametrize(
    ("side", "order_type", "limit", "trigger", "expected"),
    [
        (OrderSide.BUY, OrderType.MARKET, None, None, Decimal("101")),
        (OrderSide.SELL, OrderType.MARKET, None, None, Decimal("99")),
        (OrderSide.BUY, OrderType.LIMIT, Decimal("103"), None, Decimal("103")),
        (
            OrderSide.SELL,
            OrderType.STOP_LIMIT,
            Decimal("98"),
            Decimal("97"),
            Decimal("99"),
        ),
    ],
)
def test_conservative_risk_price(
    case: RuntimeCase,
    side: OrderSide,
    order_type: OrderType,
    limit: Decimal | None,
    trigger: Decimal | None,
    expected: Decimal,
) -> None:
    intent = case.make_intent(
        side=side,
        order_type=order_type,
        limit_amount=limit,
        trigger_amount=trigger,
    )

    projection = project_runtime_order(
        intent,
        case.observation,
        case.policy,
        decided_at=case.decided_at,
    )

    assert projection.risk_price.amount == expected


def test_same_currency_uses_exact_unit_rate(case: RuntimeCase) -> None:
    projection = project_runtime_order(
        case.make_intent(quantity_amount=Decimal("3")),
        case.observation,
        case.policy,
        decided_at=case.decided_at,
    )

    assert projection.order_notional == money("303")


def test_same_currency_rejects_redundant_conflicting_rate(case: RuntimeCase) -> None:
    forged_rate = RuntimeRiskConversionRate.model_construct()
    for field, value in (
        ("source_currency", Currency.USD),
        ("target_currency", Currency.USD),
        ("rate", Decimal("2")),
        ("observed_at", NOW),
        ("provenance_id", "forged-fx"),
    ):
        object.__setattr__(forged_rate, field, value)
    forged = case.observation.model_copy()
    object.__setattr__(forged, "conversion_rates", (forged_rate,))

    with pytest.raises(ProjectionError, match="observation"):
        project_runtime_order(
            case.make_intent(),
            forged,
            case.policy,
            decided_at=case.decided_at,
        )


def test_cross_currency_uses_exact_source_target_rate() -> None:
    case = runtime_case(
        settlement_currency=Currency.USDT,
        reporting_rate=Decimal("0.5"),
    )

    projection = project_runtime_order(
        case.make_intent(),
        case.observation,
        case.policy,
        decided_at=case.decided_at,
    )

    assert projection.risk_price == Price(Decimal("101"), Currency.USDT)
    assert projection.order_notional == money("50.50")
    assert projection.projected_position_quantity == Quantity(Decimal("3"), 0)
    assert projection.projected_gross == money("151.50")
    assert projection.projected_net == money("151.50")


@pytest.mark.parametrize("rate_kind", ["missing", "reversed", "stale"])
def test_cross_currency_rejects_missing_reversed_or_stale_rate(
    rate_kind: str,
) -> None:
    case = runtime_case(
        settlement_currency=Currency.USDT,
        reporting_rate=Decimal("0.5"),
    )
    rate = case.observation.conversion_rates[0]
    if rate_kind == "missing":
        rates: tuple[RuntimeRiskConversionRate, ...] = ()
    elif rate_kind == "reversed":
        rates = (
            RuntimeRiskConversionRate(
                source_currency=Currency.USD,
                target_currency=Currency.USDT,
                rate=Decimal("2"),
                observed_at=NOW,
                provenance_id="sim-fx-reversed",
            ),
        )
    else:
        rates = (
            rate.model_copy(update={"observed_at": NOW - timedelta(seconds=11)}),
        )
    observation = case.observation.model_copy(update={"conversion_rates": rates})

    with pytest.raises(ProjectionError, match="conversion rate"):
        project_runtime_order(
            case.make_intent(),
            observation,
            case.policy,
            decided_at=case.decided_at,
        )


@pytest.mark.parametrize("venue_kind", ["absent", "wrong"])
def test_projection_requires_exact_venue_health_authority(
    case: RuntimeCase,
    venue_kind: str,
) -> None:
    records = ()
    if venue_kind == "wrong":
        records = (
            RuntimeVenueHealthRecord(
                venue_id="OTHER",
                health=RuntimeVenueHealth.HEALTHY,
                observed_at=NOW,
            ),
        )
    observation = case.observation.model_copy(update={"venue_health": records})

    with pytest.raises(ProjectionError, match="venue"):
        project_runtime_order(
            case.make_intent(),
            observation,
            case.policy,
            decided_at=case.decided_at,
        )


def test_absent_new_partitions_start_from_canonical_zero() -> None:
    case = runtime_case(
        current_quantity=Decimal("0"),
        include_position=False,
        include_partitions=False,
        pending=Decimal("0"),
    )

    projection = project_runtime_order(
        case.make_intent(),
        case.observation,
        case.policy,
        decided_at=case.decided_at,
    )

    assert projection.projected_gross == money("101")
    assert projection.projected_net == money("101")
    assert projection.projected_strategy_gross == money("101")
    assert projection.projected_venue_gross == money("101")
    assert projection.projected_instrument_gross == money("101")


@pytest.mark.parametrize(
    "missing_partition",
    ["instrument_exposures", "strategy_exposures", "venue_exposures"],
)
def test_absent_partition_for_existing_nonzero_position_fails_closed(
    case: RuntimeCase,
    missing_partition: str,
) -> None:
    portfolio = case.observation.portfolio.model_copy(
        update={missing_partition: ()}
    )
    observation = case.observation.model_copy(update={"portfolio": portfolio})

    with pytest.raises(ProjectionError, match="partition"):
        project_runtime_order(
            case.make_intent(),
            observation,
            case.policy,
            decided_at=case.decided_at,
        )


@pytest.mark.parametrize(
    "partition_family",
    ["instrument_exposures", "strategy_exposures", "venue_exposures"],
)
def test_each_keyed_partition_family_pending_must_sum_to_total_pending(
    case: RuntimeCase,
    partition_family: str,
) -> None:
    wrong = exposure("200", "200", pending="999")
    if partition_family == "instrument_exposures":
        replacement: object = (
            InstrumentExposureSnapshot(
                instrument=INSTRUMENT,
                exposure=wrong,
            ),
        )
    elif partition_family == "strategy_exposures":
        replacement = (
            StrategyExposureSnapshot(
                strategy_id="strategy-1",
                exposure=wrong,
            ),
        )
    else:
        replacement = (
            VenueExposureSnapshot(venue_id="SIM", exposure=wrong),
        )
    portfolio = case.observation.portfolio.model_copy(
        update={partition_family: replacement}
    )
    observation = case.observation.model_copy(update={"portfolio": portfolio})

    with pytest.raises(ProjectionError, match="pending"):
        project_runtime_order(
            case.make_intent(),
            observation,
            case.policy,
            decided_at=case.decided_at,
        )


@pytest.mark.parametrize(
    ("current", "side", "quantity", "expected"),
    [
        (
            Decimal("2"),
            OrderSide.BUY,
            Decimal("1"),
            {
                "risk_price": "101",
                "order_notional": "101",
                "quantity": "3",
                "pending": "106",
                "gross": "303",
                "net": "303",
                "margin": "30.30",
                "available": "919.70",
            },
        ),
        (
            Decimal("-2"),
            OrderSide.SELL,
            Decimal("1"),
            {
                "risk_price": "99",
                "order_notional": "99",
                "quantity": "-3",
                "pending": "104",
                "gross": "297",
                "net": "-297",
                "margin": "29.70",
                "available": "920.30",
            },
        ),
        (
            Decimal("2"),
            OrderSide.SELL,
            Decimal("1"),
            {
                "risk_price": "99",
                "order_notional": "99",
                "quantity": "1",
                "pending": "104",
                "gross": "99",
                "net": "99",
                "margin": "20",
                "available": "930",
            },
        ),
        (
            Decimal("1"),
            OrderSide.SELL,
            Decimal("1"),
            {
                "risk_price": "99",
                "order_notional": "99",
                "quantity": "0",
                "pending": "104",
                "gross": "0",
                "net": "0",
                "margin": "20",
                "available": "930",
            },
        ),
        (
            Decimal("1"),
            OrderSide.SELL,
            Decimal("2"),
            {
                "risk_price": "99",
                "order_notional": "198",
                "quantity": "-1",
                "pending": "203",
                "gross": "99",
                "net": "-99",
                "margin": "20",
                "available": "930",
            },
        ),
    ],
    ids=["long-increase", "short-increase", "partial-close", "full-close", "reversal"],
)
def test_exact_replacement_projection_for_position_shapes(
    current: Decimal,
    side: OrderSide,
    quantity: Decimal,
    expected: dict[str, str],
) -> None:
    case = runtime_case(current_quantity=current)

    projection = project_runtime_order(
        case.make_intent(side=side, quantity_amount=quantity),
        case.observation,
        case.policy,
        decided_at=case.decided_at,
    )

    assert projection.risk_price == Price(
        Decimal(expected["risk_price"]), Currency.USD
    )
    assert projection.order_notional == money(expected["order_notional"])
    assert projection.projected_position_quantity == Quantity(
        Decimal(expected["quantity"]), 0
    )
    assert projection.projected_pending == money(expected["pending"])
    assert projection.projected_gross == money(expected["gross"])
    assert projection.projected_net == money(expected["net"])
    assert projection.projected_strategy_gross == money(expected["gross"])
    assert projection.projected_venue_gross == money(expected["gross"])
    assert projection.projected_instrument_gross == money(expected["gross"])
    assert projection.projected_margin_used == money(expected["margin"])
    assert projection.projected_available_funds == money(expected["available"])


def _position(
    *,
    strategy_id: str,
    instrument: InstrumentId,
    quantity: str,
    mark: str,
) -> AccountPositionSnapshot:
    return AccountPositionSnapshot(
        account_id="account-1",
        strategy_id=strategy_id,
        instrument=instrument,
        settlement_currency=Currency.USD,
        quantity=Quantity(Decimal(quantity), 0),
        mark=PositionMark(
            price=Price(Decimal(mark), Currency.USD),
            marked_at=NOW,
            provenance_id="sim-mark",
        ),
        average_entry_price=Price(Decimal(mark), Currency.USD),
        realized_pnl=money("0"),
        unrealized_pnl=money("0"),
        fees=money("0"),
        funding=money("0"),
        observed_at=NOW,
        schema_version="account-position-v1",
    )


def test_multi_position_replacement_preserves_unrelated_aggregate_contributions(
    case: RuntimeCase,
) -> None:
    positions = (
        _position(
            strategy_id="strategy-1",
            instrument=INSTRUMENT,
            quantity="2",
            mark="100",
        ),
        _position(
            strategy_id="strategy-1",
            instrument=SAME_VENUE_INSTRUMENT,
            quantity="2",
            mark="50",
        ),
        _position(
            strategy_id="strategy-2",
            instrument=INSTRUMENT,
            quantity="1",
            mark="100",
        ),
        _position(
            strategy_id="strategy-3",
            instrument=OTHER_VENUE_INSTRUMENT,
            quantity="1",
            mark="25",
        ),
    )
    portfolio = case.observation.portfolio.model_copy(
        update={
            "positions": positions,
            "total_exposure": exposure("425", "425", pending="5"),
            "instrument_exposures": (
                InstrumentExposureSnapshot(
                    instrument=OTHER_VENUE_INSTRUMENT,
                    exposure=exposure("25", "25", pending="5"),
                ),
                InstrumentExposureSnapshot(
                    instrument=INSTRUMENT,
                    exposure=exposure("300", "300", pending="0"),
                ),
                InstrumentExposureSnapshot(
                    instrument=SAME_VENUE_INSTRUMENT,
                    exposure=exposure("100", "100", pending="0"),
                ),
            ),
            "strategy_exposures": (
                StrategyExposureSnapshot(
                    strategy_id="strategy-1",
                    exposure=exposure("300", "300", pending="5"),
                ),
                StrategyExposureSnapshot(
                    strategy_id="strategy-2",
                    exposure=exposure("100", "100", pending="0"),
                ),
                StrategyExposureSnapshot(
                    strategy_id="strategy-3",
                    exposure=exposure("25", "25", pending="0"),
                ),
            ),
            "venue_exposures": (
                VenueExposureSnapshot(
                    venue_id="ALT",
                    exposure=exposure("25", "25", pending="5"),
                ),
                VenueExposureSnapshot(
                    venue_id="SIM",
                    exposure=exposure("400", "400", pending="0"),
                ),
            ),
        }
    )
    observation = case.observation.model_copy(update={"portfolio": portfolio})

    projection = project_runtime_order(
        case.make_intent(),
        observation,
        case.policy,
        decided_at=case.decided_at,
    )

    assert projection.projected_pending == money("106")
    assert projection.projected_gross == money("528")
    assert projection.projected_net == money("528")
    assert projection.projected_strategy_gross == money("403")
    assert projection.projected_venue_gross == money("503")
    assert projection.projected_instrument_gross == money("403")
    assert projection.projected_margin_used == money("30.30")
    assert projection.projected_available_funds == money("919.70")


@pytest.mark.parametrize(
    "inconsistent_partition",
    ["total_exposure", "instrument_exposures", "strategy_exposures", "venue_exposures"],
)
def test_existing_partition_must_match_recomputed_position_contributions(
    case: RuntimeCase,
    inconsistent_partition: str,
) -> None:
    portfolio = case.observation.portfolio
    wrong = exposure("201", "200")
    if inconsistent_partition == "total_exposure":
        changes: dict[str, object] = {"total_exposure": wrong}
    elif inconsistent_partition == "instrument_exposures":
        changes = {
            "instrument_exposures": (
                InstrumentExposureSnapshot(
                    instrument=INSTRUMENT,
                    exposure=wrong,
                ),
            )
        }
    elif inconsistent_partition == "strategy_exposures":
        changes = {
            "strategy_exposures": (
                StrategyExposureSnapshot(
                    strategy_id="strategy-1",
                    exposure=wrong,
                ),
            )
        }
    else:
        changes = {
            "venue_exposures": (
                VenueExposureSnapshot(venue_id="SIM", exposure=wrong),
            )
        }
    observation = case.observation.model_copy(
        update={"portfolio": portfolio.model_copy(update=changes)}
    )

    with pytest.raises(ProjectionError, match="inconsistent"):
        project_runtime_order(
            case.make_intent(),
            observation,
            case.policy,
            decided_at=case.decided_at,
        )


def test_projection_products_ignore_hostile_decimal_context() -> None:
    case = runtime_case(
        current_quantity=Decimal("1.00"),
        quantity_precision=2,
        initial_margin_rate=Decimal("0.25"),
    )
    intent = case.make_intent(
        quantity_amount=Decimal("1234.56"),
        quantity_precision=2,
    )
    expected = project_runtime_order(
        intent,
        case.observation,
        case.policy,
        decided_at=case.decided_at,
    )

    with localcontext() as context:
        context.prec = 3
        actual = project_runtime_order(
            intent,
            case.observation,
            case.policy,
            decided_at=case.decided_at,
        )

    assert actual == expected
    assert actual.order_notional == money("124690.56")
    assert actual.projected_gross == money("124791.56")
    assert actual.projected_margin_used == money("31192.89")
    assert actual.projected_available_funds == money("-30242.89")


def test_projection_never_mutates_order_market_quantity_or_conversion_inputs() -> None:
    case = runtime_case(
        settlement_currency=Currency.USDT,
        reporting_rate=Decimal("0.5"),
    )
    intent = case.make_intent(quantity_amount=Decimal("2"))
    intent_digest = canonical_model_digest(intent)
    observation_digest = canonical_model_digest(case.observation)
    policy_digest = canonical_model_digest(case.policy)
    market_before = case.observation.market_snapshots[0]
    quantity_before = intent.quantity
    conversion_before = case.observation.conversion_rates[0]

    result = project_runtime_order(
        intent,
        case.observation,
        case.policy,
        decided_at=case.decided_at,
    )

    assert isinstance(result, RuntimeRiskProjection)
    assert canonical_model_digest(intent) == intent_digest
    assert canonical_model_digest(case.observation) == observation_digest
    assert canonical_model_digest(case.policy) == policy_digest
    assert case.observation.market_snapshots[0] == market_before
    assert intent.quantity == quantity_before
    assert case.observation.conversion_rates[0] == conversion_before


@pytest.mark.parametrize("wrong_argument", ["intent", "observation", "policy"])
def test_wrong_top_level_contract_type_raises_bounded_projection_error(
    case: RuntimeCase,
    wrong_argument: str,
) -> None:
    arguments: dict[str, object] = {
        "intent": case.make_intent(),
        "observation": case.observation,
        "policy": case.policy,
    }
    replacements = {
        "intent": case.policy,
        "observation": case.policy,
        "policy": case.observation,
    }
    arguments[wrong_argument] = replacements[wrong_argument]

    with pytest.raises(ProjectionError, match=wrong_argument):
        project_runtime_order(
            arguments["intent"],  # type: ignore[arg-type]
            arguments["observation"],  # type: ignore[arg-type]
            arguments["policy"],  # type: ignore[arg-type]
            decided_at=case.decided_at,
        )

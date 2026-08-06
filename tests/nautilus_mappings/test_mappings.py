from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from packages.domain import (
    AssetClass,
    Currency,
    FillEvent,
    FillReportStatus,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    LiquiditySide,
    MarginRequirements,
    Money,
    OrderCancelResolution,
    OrderEvent,
    OrderIntent,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    OrderType,
    Price,
    ProductType,
    ReconciliationSource,
    TimeInForce,
)
from packages.nautilus_mappings import (
    NautilusFillEventV1,
    NautilusMappingError,
    NautilusOrderEventV1,
    NautilusOrderIntentV1,
    NautilusPriceV1,
    NautilusQuantityV1,
    canonical_to_nautilus_fill_event,
    canonical_to_nautilus_order_event,
    canonical_to_nautilus_order_intent,
    nautilus_to_canonical_fill_event,
    nautilus_to_canonical_order_event,
    nautilus_to_canonical_order_intent,
)


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def uid(value: int) -> UUID:
    return UUID(int=value)


def definition() -> InstrumentDefinition:
    return InstrumentDefinition(
        instrument_id=InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA"),
        raw_symbol="BTCUSD",
        asset_class=AssetClass.CRYPTO,
        base_currency=Currency.BTC,
        quote_currency=Currency.USD,
        settlement_currency=Currency.USD,
        tick_size=Price(Decimal("0.01"), Currency.USD),
        size_increment=OrderQuantity(Decimal("0.01"), 2),
        minimum_quantity=OrderQuantity(Decimal("0.01"), 2),
        maximum_quantity=OrderQuantity(Decimal("100.00"), 2),
        minimum_notional=Money(Decimal("1.00"), Currency.USD),
        maximum_notional=Money(Decimal("100000.00"), Currency.USD),
        multiplier=Decimal("1.00"),
        margin=MarginRequirements(Decimal("1.00"), Decimal("0.50")),
        session_calendar="24X7",
        provenance=InstrumentProvenance(
            source_id="catalog", source_revision="r1", observed_at=NOW
        ),
    )


def intent(
    order_type: OrderType = OrderType.LIMIT,
    time_in_force: TimeInForce = TimeInForce.GTC,
    **changes: object,
) -> OrderIntent:
    values: dict[str, object] = {
        "intent_id": uid(1),
        "risk_decision_id": uid(2),
        "client_order_id": "client-1",
        "venue_order_id": "venue-1",
        "strategy_id": "strategy-1",
        "trader_id": "trader-1",
        "account_id": "account-1",
        "execution_client_id": "execution-1",
        "order_list_id": "list-1",
        "instrument": InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA"),
        "side": OrderSide.BUY,
        "order_type": order_type,
        "time_in_force": time_in_force,
        "quantity": OrderQuantity(Decimal("1.20"), 2),
        "limit_price": (
            Price(Decimal("100.20"), Currency.USD)
            if order_type
            in {OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.LIMIT_IF_TOUCHED}
            else None
        ),
        "trigger_price": (
            Price(Decimal("99.80"), Currency.USD)
            if order_type
            in {
                OrderType.STOP_MARKET,
                OrderType.STOP_LIMIT,
                OrderType.MARKET_IF_TOUCHED,
                OrderType.LIMIT_IF_TOUCHED,
            }
            else None
        ),
        "trailing_offset": (
            Price(Decimal("2.50"), Currency.USD)
            if order_type is OrderType.TRAILING_STOP
            else None
        ),
        "gtd_expiry": NOW + timedelta(days=1)
        if time_in_force is TimeInForce.GTD
        else None,
        "post_only": False,
        "reduce_only": True,
        "requested_at": NOW,
        "schema_version": "2.0",
    }
    values.update(changes)
    return OrderIntent(**values)


def order_event(
    target_status: OrderStatus = OrderStatus.ACCEPTED, **changes: object
) -> OrderEvent:
    values: dict[str, object] = {
        "event_id": uid(11),
        "order_id": uid(12),
        "sequence": 3,
        "target_status": target_status,
        "occurred_at": NOW,
        "reason": (
            "VENUE_TERMINATED"
            if target_status
            in {
                OrderStatus.CANCELED,
                OrderStatus.EXPIRED,
                OrderStatus.REJECTED,
                OrderStatus.DENIED,
            }
            else None
        ),
        "cancel_resolution": None,
        "schema_version": "2.0",
    }
    values.update(changes)
    return OrderEvent.create(**values)  # type: ignore[arg-type]


def fill_event(
    status: FillReportStatus = FillReportStatus.PARTIALLY_FILLED,
    **changes: object,
) -> FillEvent:
    references: dict[str, object] = {
        "duplicate_of_execution_id": None,
        "correction_of_execution_id": None,
        "bust_of_execution_id": None,
    }
    if status is FillReportStatus.DUPLICATE:
        references["duplicate_of_execution_id"] = uid(31)
    elif status is FillReportStatus.CORRECTION:
        references["correction_of_execution_id"] = uid(31)
    elif status is FillReportStatus.BUST:
        references["bust_of_execution_id"] = uid(31)

    values: dict[str, object] = {
        "execution_id": uid(21),
        "order_id": uid(22),
        "report_sequence": 4,
        "venue_trade_id": "venue-trade-1",
        "instrument_definition": definition(),
        "side": OrderSide.SELL,
        "liquidity_side": LiquiditySide.TAKER,
        "status": status,
        "quantity": OrderQuantity(Decimal("1.20"), 2),
        "cumulative_fill_quantity": OrderQuantity(Decimal("1.20"), 2),
        "leaves_quantity": OrderQuantity(Decimal("1.30"), 2),
        "order_quantity": OrderQuantity(Decimal("2.50"), 2),
        "last_fill_price": Price(Decimal("100.20"), Currency.USD),
        "average_fill_price": Price(Decimal("100.10"), Currency.USD),
        "commission": Money(Decimal("0.20"), Currency.USD),
        "reconciliation_source": ReconciliationSource.DROP_COPY,
        "filled_at": NOW,
        "schema_version": "2.0",
        **references,
    }
    if status is FillReportStatus.FILLED:
        values.update(
            {
                "quantity": OrderQuantity(Decimal("2.50"), 2),
                "cumulative_fill_quantity": OrderQuantity(Decimal("2.50"), 2),
                "leaves_quantity": OrderQuantity(Decimal("0.00"), 2),
            }
        )
    values.update(changes)
    return FillEvent(**values)


def equity_definition() -> InstrumentDefinition:
    return InstrumentDefinition(
        instrument_id=InstrumentId("SPY", ProductType.EQUITY, "ALPACA"),
        raw_symbol="SPY",
        asset_class=AssetClass.EQUITY,
        base_currency=None,
        quote_currency=Currency.USD,
        settlement_currency=Currency.USD,
        tick_size=Price(Decimal("0.01"), Currency.USD),
        size_increment=OrderQuantity(Decimal("1"), 0),
        minimum_quantity=OrderQuantity(Decimal("1"), 0),
        maximum_quantity=OrderQuantity(Decimal("1000"), 0),
        minimum_notional=Money(Decimal("1.00"), Currency.USD),
        maximum_notional=Money(Decimal("100000.00"), Currency.USD),
        multiplier=Decimal("1"),
        margin=None,
        session_calendar="XNYS",
        provenance=InstrumentProvenance(
            source_id="catalog", source_revision="r1", observed_at=NOW
        ),
    )


def model_construct_with_unmodeled_field(value: object) -> object:
    model_type = type(value)
    fields = getattr(model_type, "model_fields")
    forged = model_type.model_construct(
        **{name: getattr(value, name) for name in fields}
    )
    forged.__dict__["unsupported_nautilus_concept"] = "drop-me"
    return forged


@pytest.mark.parametrize(
    ("order_type", "expected"),
    [
        (OrderType.MARKET, "MARKET"),
        (OrderType.LIMIT, "LIMIT"),
        (OrderType.STOP_MARKET, "STOP_MARKET"),
        (OrderType.STOP_LIMIT, "STOP_LIMIT"),
        (OrderType.MARKET_IF_TOUCHED, "MARKET_IF_TOUCHED"),
        (OrderType.LIMIT_IF_TOUCHED, "LIMIT_IF_TOUCHED"),
        (OrderType.TRAILING_STOP, "TRAILING_STOP_MARKET"),
    ],
)
@pytest.mark.parametrize(
    ("time_in_force", "expected_tif"),
    [
        (TimeInForce.GTC, "GTC"),
        (TimeInForce.IOC, "IOC"),
        (TimeInForce.FOK, "FOK"),
        (TimeInForce.DAY, "DAY"),
        (TimeInForce.GTD, "GTD"),
    ],
)
def test_order_intent_mapping_round_trips_all_supported_types_and_tif_exactly(
    order_type: OrderType,
    expected: str,
    time_in_force: TimeInForce,
    expected_tif: str,
) -> None:
    original = intent(order_type, time_in_force)

    mapped = canonical_to_nautilus_order_intent(original)
    restored = nautilus_to_canonical_order_intent(mapped)

    assert isinstance(mapped, NautilusOrderIntentV1)
    assert mapped.order_type.value == expected
    assert mapped.time_in_force.value == expected_tif
    assert mapped.intent_id == original.intent_id
    assert mapped.risk_decision_id == original.risk_decision_id
    assert mapped.client_order_id == "client-1"
    assert mapped.venue_order_id == "venue-1"
    assert mapped.strategy_id == "strategy-1"
    assert mapped.trader_id == "trader-1"
    assert mapped.account_id == "account-1"
    assert mapped.execution_client_id == "execution-1"
    assert mapped.order_list_id == "list-1"
    assert mapped.post_only is False
    assert mapped.reduce_only is True
    assert mapped.quantity.value.as_tuple() == Decimal("1.20").as_tuple()
    assert restored == original
    assert restored is not original
    assert restored.quantity is not original.quantity
    assert restored.requested_at == NOW
    assert restored.requested_at.tzinfo is UTC


def test_order_intent_mapping_preserves_resting_flags_and_scale() -> None:
    original = intent(
        OrderType.LIMIT,
        TimeInForce.GTC,
        post_only=True,
        reduce_only=False,
        quantity=OrderQuantity(Decimal("1.20"), 2),
        limit_price=Price(Decimal("100.20"), Currency.USD),
    )

    mapped = canonical_to_nautilus_order_intent(original)
    restored = nautilus_to_canonical_order_intent(mapped)

    assert mapped.post_only is True
    assert mapped.reduce_only is False
    assert mapped.limit_price is not None
    assert mapped.limit_price.amount.as_tuple() == Decimal("100.20").as_tuple()
    assert restored.quantity.value.as_tuple() == Decimal("1.20").as_tuple()
    assert restored.limit_price is not None
    assert restored.limit_price.amount.as_tuple() == Decimal("100.20").as_tuple()


@pytest.mark.parametrize("status", list(OrderStatus))
def test_order_event_mapping_round_trips_all_states_with_utc_and_fingerprint(
    status: OrderStatus,
) -> None:
    original = order_event(status)

    mapped = canonical_to_nautilus_order_event(original)
    restored = nautilus_to_canonical_order_event(mapped)

    assert isinstance(mapped, NautilusOrderEventV1)
    assert mapped.target_status.value == status.value
    assert mapped.occurred_at == NOW
    assert mapped.occurred_at.tzinfo is UTC
    assert mapped.event_fingerprint == original.event_fingerprint
    assert restored == original
    assert restored is not original


def test_order_event_mapping_preserves_explicit_cancel_resolution() -> None:
    original = order_event(
        OrderStatus.ACCEPTED,
        reason="CANCEL_REJECTED",
        cancel_resolution=OrderCancelResolution.REJECTED,
    )

    mapped = canonical_to_nautilus_order_event(original)
    restored = nautilus_to_canonical_order_event(mapped)

    assert mapped.cancel_resolution is not None
    assert mapped.cancel_resolution.value == "REJECTED"
    assert restored.cancel_resolution is OrderCancelResolution.REJECTED
    assert restored.reason == "CANCEL_REJECTED"


@pytest.mark.parametrize("status", list(FillReportStatus))
def test_fill_event_mapping_round_trips_full_v2_execution_report(
    status: FillReportStatus,
) -> None:
    original = fill_event(status)

    mapped = canonical_to_nautilus_fill_event(original)
    restored = nautilus_to_canonical_fill_event(mapped)

    assert isinstance(mapped, NautilusFillEventV1)
    assert mapped.execution_id == original.execution_id
    assert mapped.order_id == original.order_id
    assert mapped.report_sequence == 4
    assert mapped.venue_trade_id == "venue-trade-1"
    assert mapped.instrument_definition.instrument_id.symbol == "BTC-USD"
    assert mapped.instrument_definition.tick_size.amount.as_tuple() == Decimal(
        "0.01"
    ).as_tuple()
    assert mapped.instrument_definition.size_increment.value.as_tuple() == Decimal(
        "0.01"
    ).as_tuple()
    assert mapped.last_fill_price.amount.as_tuple() == Decimal("100.20").as_tuple()
    assert mapped.average_fill_price.amount.as_tuple() == Decimal("100.10").as_tuple()
    assert mapped.commission.amount.as_tuple() == Decimal("0.20").as_tuple()
    assert mapped.status.value == status.value
    assert mapped.filled_at == NOW
    assert restored == original
    assert restored is not original
    assert restored.instrument_definition is not original.instrument_definition


def test_fill_event_mapping_preserves_non_quote_commission_currency() -> None:
    original = fill_event(commission=Money(Decimal("0.00000001"), Currency.BTC))

    restored = nautilus_to_canonical_fill_event(
        canonical_to_nautilus_fill_event(original)
    )

    assert restored.commission.currency is Currency.BTC
    assert restored.commission.amount.as_tuple() == Decimal("0.00000001").as_tuple()


@pytest.mark.parametrize(
    "currency", [Currency.USD, Currency.USDT, Currency.BTC, Currency.ETH]
)
def test_fill_event_mapping_preserves_each_registered_commission_currency(
    currency: Currency,
) -> None:
    original = fill_event(commission=Money(Decimal("0"), currency))

    mapped = canonical_to_nautilus_fill_event(original)
    restored = nautilus_to_canonical_fill_event(mapped)

    assert mapped.commission.currency.code == currency.code
    assert restored.commission.currency is currency


@pytest.mark.parametrize("liquidity_side", list(LiquiditySide))
@pytest.mark.parametrize("reconciliation_source", list(ReconciliationSource))
def test_fill_event_mapping_round_trips_each_liquidity_and_reconciliation_variant(
    liquidity_side: LiquiditySide, reconciliation_source: ReconciliationSource
) -> None:
    original = fill_event(
        liquidity_side=liquidity_side,
        reconciliation_source=reconciliation_source,
    )

    mapped = canonical_to_nautilus_fill_event(original)
    restored = nautilus_to_canonical_fill_event(mapped)

    assert mapped.liquidity_side.value == liquidity_side.value.upper()
    assert mapped.reconciliation_source.value == reconciliation_source.value.upper()
    assert restored == original


def test_fill_event_mapping_preserves_an_equity_instrument_definition() -> None:
    original = fill_event(
        instrument_definition=equity_definition(),
        quantity=OrderQuantity(Decimal("1"), 0),
        cumulative_fill_quantity=OrderQuantity(Decimal("1"), 0),
        leaves_quantity=OrderQuantity(Decimal("1"), 0),
        order_quantity=OrderQuantity(Decimal("2"), 0),
        last_fill_price=Price(Decimal("100.20"), Currency.USD),
        average_fill_price=Price(Decimal("100.10"), Currency.USD),
    )

    mapped = canonical_to_nautilus_fill_event(original)
    restored = nautilus_to_canonical_fill_event(mapped)

    assert mapped.instrument_definition.instrument_id.product_type.value == "EQUITY"
    assert mapped.instrument_definition.asset_class.value == "EQUITY"
    assert mapped.instrument_definition.base_currency is None
    assert restored == original


@pytest.mark.parametrize(
    ("mapping", "forged"),
    [
        (
            canonical_to_nautilus_order_intent,
            intent().model_copy(update={"requested_at": datetime(2026, 8, 6, 12)}),
        ),
        (
            canonical_to_nautilus_order_intent,
            OrderIntent.model_construct(
                **{
                    **{
                        name: getattr(intent(), name)
                        for name in OrderIntent.model_fields
                    },
                    "requested_at": datetime(2026, 8, 6, 12),
                }
            ),
        ),
        (
            canonical_to_nautilus_order_event,
            order_event().model_copy(update={"event_fingerprint": "0" * 64}),
        ),
        (
            canonical_to_nautilus_order_event,
            OrderEvent.model_construct(
                **{
                    **{
                        name: getattr(order_event(), name)
                        for name in OrderEvent.model_fields
                    },
                    "event_fingerprint": "0" * 64,
                }
            ),
        ),
        (
            canonical_to_nautilus_fill_event,
            fill_event().model_copy(
                update={"commission": Money(Decimal("-0.20"), Currency.USD)}
            ),
        ),
        (
            canonical_to_nautilus_fill_event,
            FillEvent.model_construct(
                **{
                    **{
                        name: getattr(fill_event(), name)
                        for name in FillEvent.model_fields
                    },
                    "commission": Money(Decimal("-0.20"), Currency.USD),
                }
            ),
        ),
    ],
)
def test_canonical_mapping_rejects_forged_pydantic_models(
    mapping: object, forged: object
) -> None:
    with pytest.raises(NautilusMappingError):
        mapping(forged)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("mapping", "forged"),
    [
        (
            canonical_to_nautilus_order_intent,
            intent().model_copy(update={"schema_version": "1.0"}),
        ),
        (
            canonical_to_nautilus_order_intent,
            OrderIntent.model_construct(
                **{
                    **{
                        name: getattr(intent(), name)
                        for name in OrderIntent.model_fields
                    },
                    "schema_version": "1.0",
                }
            ),
        ),
        (
            canonical_to_nautilus_order_event,
            order_event().model_copy(update={"schema_version": "1.0"}),
        ),
        (
            canonical_to_nautilus_order_event,
            OrderEvent.model_construct(
                **{
                    **{
                        name: getattr(order_event(), name)
                        for name in OrderEvent.model_fields
                    },
                    "schema_version": "1.0",
                }
            ),
        ),
        (
            canonical_to_nautilus_fill_event,
            fill_event().model_copy(update={"schema_version": "1.0"}),
        ),
        (
            canonical_to_nautilus_fill_event,
            FillEvent.model_construct(
                **{
                    **{
                        name: getattr(fill_event(), name)
                        for name in FillEvent.model_fields
                    },
                    "schema_version": "1.0",
                }
            ),
        ),
    ],
)
def test_canonical_mapping_rejects_unsupported_source_versions(
    mapping: object, forged: object
) -> None:
    with pytest.raises(NautilusMappingError):
        mapping(forged)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("mapping", "forged"),
    [
        (
            canonical_to_nautilus_order_intent,
            intent().model_copy(update={"unsupported_nautilus_concept": "drop-me"}),
        ),
        (
            canonical_to_nautilus_order_intent,
            model_construct_with_unmodeled_field(intent()),
        ),
        (
            canonical_to_nautilus_order_event,
            order_event().model_copy(update={"unsupported_nautilus_concept": "drop-me"}),
        ),
        (
            canonical_to_nautilus_order_event,
            model_construct_with_unmodeled_field(order_event()),
        ),
        (
            canonical_to_nautilus_fill_event,
            fill_event().model_copy(update={"unsupported_nautilus_concept": "drop-me"}),
        ),
        (
            canonical_to_nautilus_fill_event,
            model_construct_with_unmodeled_field(fill_event()),
        ),
    ],
)
def test_canonical_mapping_rejects_unmodeled_pydantic_fields(
    mapping: object, forged: object
) -> None:
    with pytest.raises(NautilusMappingError):
        mapping(forged)  # type: ignore[operator]


def test_adapter_mapping_rejects_forged_unknown_legacy_and_inconsistent_values() -> None:
    invalid_version = canonical_to_nautilus_order_intent(intent()).model_copy(
        update={"adapter_version": "nautilus-adapter-v0"}
    )
    legacy_event = NautilusOrderEventV1.model_construct(
        **{
            **{
                name: getattr(canonical_to_nautilus_order_event(order_event()), name)
                for name in NautilusOrderEventV1.model_fields
            },
            "schema_version": "1.0",
        }
    )
    malformed_fill = canonical_to_nautilus_fill_event(fill_event()).model_copy(
        update={
            "quantity": NautilusQuantityV1.model_construct(
                value=Decimal("1.001"), precision=2
            )
        }
    )
    currency_mismatch = canonical_to_nautilus_fill_event(fill_event()).model_copy(
        update={
            "last_fill_price": NautilusPriceV1.model_construct(
                amount=Decimal("100.20"),
                currency=canonical_to_nautilus_fill_event(
                    fill_event()
                ).instrument_definition.base_currency,
            )
        }
    )
    unsupported_field = canonical_to_nautilus_order_intent(intent()).model_copy(
        update={"unsupported_nautilus_concept": "drop-me"}
    )

    with pytest.raises(ValueError):
        NautilusOrderIntentV1.model_validate(unsupported_field)

    for forged, mapping in (
        (invalid_version, nautilus_to_canonical_order_intent),
        (legacy_event, nautilus_to_canonical_order_event),
        (malformed_fill, nautilus_to_canonical_fill_event),
        (currency_mismatch, nautilus_to_canonical_fill_event),
        (unsupported_field, nautilus_to_canonical_order_intent),
    ):
        with pytest.raises(NautilusMappingError):
            mapping(forged)


_PRODUCTION_SOURCE_ROOTS = ("packages", "apps", "services", "scripts")
# The isolated wheel is never importable in the root graph, including build
# tooling. Tests and the external engine wheel are not production roots.
_DIRECT_NAUTILUS_IMPORT_ALLOWLIST: frozenset[Path] = frozenset()


def _mapping_boundary_violations(root: Path) -> list[str]:
    central_root = root / "packages" / "nautilus_mappings"
    violations: list[str] = []

    for source_root_name in _PRODUCTION_SOURCE_ROOTS:
        source_root = root / source_root_name
        if not source_root.is_dir():
            continue
        for source_path in source_root.rglob("*.py"):
            relative = source_path.relative_to(root)
            if relative in _DIRECT_NAUTILUS_IMPORT_ALLOWLIST:
                continue
            tree = ast.parse(
                source_path.read_text(encoding="utf-8"), filename=str(source_path)
            )
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    imported = (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                    )
                    if any(
                        name == "nautilus_trader"
                        or name.startswith("nautilus_trader.")
                        for name in imported
                    ):
                        violations.append(
                            f"{relative}: direct nautilus runtime import"
                        )
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    node.name.startswith("canonical_to_nautilus_")
                    or node.name.startswith("nautilus_to_canonical_")
                ):
                    if central_root not in source_path.parents:
                        violations.append(
                            f"{relative}: ad-hoc mapping function {node.name}"
                        )

    return violations


def test_mapping_boundary_is_the_only_canonical_nautilus_conversion_site() -> None:
    root = Path(__file__).resolve().parents[2]

    assert _mapping_boundary_violations(root) == []


def test_mapping_architecture_scan_detects_a_prohibited_outside_central_source(
    tmp_path: Path,
) -> None:
    unsafe_source = tmp_path / "services" / "forbidden_mapping.py"
    unsafe_source.parent.mkdir()
    unsafe_source.write_text(
        "import nautilus_trader\n"
        "def canonical_to_nautilus_leak():\n"
        "    return None\n",
        encoding="utf-8",
    )

    violations = _mapping_boundary_violations(tmp_path)

    assert "services/forbidden_mapping.py: direct nautilus runtime import" in violations
    assert (
        "services/forbidden_mapping.py: ad-hoc mapping function "
        "canonical_to_nautilus_leak"
    ) in violations

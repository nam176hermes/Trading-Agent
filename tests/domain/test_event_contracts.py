from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from packages.domain import (
    AssetClass,
    Currency,
    EvidenceLocator,
    EvidenceLocatorKind,
    EvidenceReference,
    EvidenceSource,
    EventEnvelope,
    FillEvent,
    FillReportStatus,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    LiquiditySide,
    Money,
    OrderEvent,
    OrderIntent,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    PositionSnapshot,
    Price,
    ProductType,
    Quantity,
    ResearchPacket,
    ReconciliationSource,
    RiskDecision,
    RiskOutcome,
    RiskReasonCode,
    RiskStateSnapshot,
    SignalDirection,
    SignalProposal,
    TargetPortfolio,
    TargetPosition,
    TimeInForce,
    validate_event_batch,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA")


def fill_definition() -> InstrumentDefinition:
    return InstrumentDefinition(
        instrument_id=INSTRUMENT,
        raw_symbol="BTCUSD",
        asset_class=AssetClass.CRYPTO,
        base_currency=Currency.BTC,
        quote_currency=Currency.USD,
        settlement_currency=Currency.USD,
        tick_size=Price(Decimal("0.01"), Currency.USD),
        size_increment=OrderQuantity(Decimal("0.01"), 2),
        minimum_quantity=OrderQuantity(Decimal("0.01"), 2),
        maximum_quantity=OrderQuantity(Decimal("100"), 2),
        minimum_notional=Money(Decimal("1"), Currency.USD),
        maximum_notional=Money(Decimal("100000"), Currency.USD),
        multiplier=Decimal("1"),
        margin=None,
        session_calendar="24X7",
        provenance=InstrumentProvenance(
            source_id="catalog", source_revision="r1", observed_at=NOW
        ),
    )


def evidence(*, observed_at: datetime = NOW) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=uuid4(),
        source=EvidenceSource.RESEARCH,
        locator=EvidenceLocator(
            kind=EvidenceLocatorKind.HTTPS,
            authority="example.invalid",
            path=("evidence", "1"),
        ),
        observed_at=observed_at,
        schema_version="1.0",
    )


def signal() -> SignalProposal:
    packet = research_packet()
    return SignalProposal(
        signal_id=uuid4(),
        research_packet_id=packet.packet_id,
        instrument=INSTRUMENT,
        direction=SignalDirection.LONG,
        score=Decimal("0.9"),
        confidence=Decimal("0.8"),
        research_packet_cutoff_at=packet.cutoff_at,
        cutoff_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        evidence=(evidence(),),
        model_version="model-1",
        strategy_version="strategy-1",
        schema_version="1.0",
    )


def research_packet() -> ResearchPacket:
    return ResearchPacket(
        packet_id=uuid4(),
        cutoff_at=NOW,
        evidence=(evidence(),),
        model_version="model-1",
        schema_version="1.0",
    )


def target(
    target_id: UUID | None = None, *, source_signal_ids: tuple[UUID, ...] | None = None
) -> TargetPortfolio:
    return TargetPortfolio(
        target_id=target_id or uuid4(),
        positions=(TargetPosition(instrument=INSTRUMENT, target_weight=Decimal("0.25")),),
        source_signal_ids=source_signal_ids if source_signal_ids is not None else (uuid4(),),
        effective_at=NOW,
        schema_version="1.0",
    )


def state() -> RiskStateSnapshot:
    portfolio = PortfolioSnapshot(
        snapshot_id=uuid4(),
        positions=(
            PositionSnapshot(
                instrument=INSTRUMENT,
                quantity=Quantity(Decimal("1"), 0),
                observed_at=NOW,
            ),
        ),
        observed_at=NOW,
        schema_version="1.0",
    )
    return RiskStateSnapshot(
        state_id=uuid4(),
        portfolio=portfolio,
        open_order_ids=(uuid4(),),
        kill_switch_engaged=False,
        observed_at=NOW,
        schema_version="1.0",
    )


def risk_decision() -> RiskDecision:
    original = target()
    return RiskDecision(
        decision_id=uuid4(),
        original_target=original,
        approved_target=original,
        outcome=RiskOutcome.APPROVED,
        reason_codes=(RiskReasonCode.WITHIN_LIMITS,),
        policy_version="risk-1",
        state_snapshot=state(),
        decided_at=NOW,
        schema_version="1.0",
    )


def order_intent(order_type: OrderType = OrderType.LIMIT) -> OrderIntent:
    return OrderIntent(
        intent_id=uuid4(),
        risk_decision_id=uuid4(),
        client_order_id="client-1",
        strategy_id="strategy-1",
        trader_id="trader-1",
        account_id="account-1",
        execution_client_id="execution-client-1",
        instrument=INSTRUMENT,
        side=OrderSide.BUY,
        order_type=order_type,
        time_in_force=TimeInForce.DAY,
        quantity=OrderQuantity(Decimal("1.25"), precision=2),
        limit_price=Price(Decimal("100"), Currency.USD) if order_type is OrderType.LIMIT else None,
        requested_at=NOW,
        schema_version="1.0",
    )


def order_event() -> OrderEvent:
    return OrderEvent.create(
        event_id=uuid4(),
        order_id=uuid4(),
        sequence=1,
        target_status=OrderStatus.SUBMITTED,
        occurred_at=NOW,
        schema_version="2.0",
    )


def fill_event() -> FillEvent:
    order = order_event()
    return FillEvent(
        execution_id=uuid4(),
        order_id=order.order_id,
        report_sequence=1,
        venue_trade_id="trade-1",
        instrument_definition=fill_definition(),
        side=OrderSide.BUY,
        liquidity_side=LiquiditySide.MAKER,
        status=FillReportStatus.FILLED,
        quantity=OrderQuantity(Decimal("1.25"), 2),
        cumulative_fill_quantity=OrderQuantity(Decimal("1.25"), 2),
        leaves_quantity=OrderQuantity(Decimal("0"), 2),
        order_quantity=OrderQuantity(Decimal("1.25"), 2),
        last_fill_price=Price(Decimal("100"), Currency.USD),
        average_fill_price=Price(Decimal("100"), Currency.USD),
        commission=Money(Decimal("0.01"), Currency.USD),
        reconciliation_source=ReconciliationSource.VENUE,
        filled_at=NOW,
        schema_version="2.0",
    )


def fill_values(fill: FillEvent) -> dict[str, object]:
    return {name: getattr(fill, name) for name in FillEvent.model_fields}


def envelope(payload: object, *, event_id: UUID | None = None, stream_id: UUID | None = None, sequence: int = 1, **changes: object) -> EventEnvelope[object]:
    values: dict[str, object] = {
        "event_id": event_id or uuid4(),
        "event_type": type(payload).__name__,
        "schema_version": "1.0",
        "source": "domain-test",
        "stream_id": stream_id or uuid4(),
        "sequence": sequence,
        "observed_at": NOW,
        "ingested_at": NOW + timedelta(seconds=1),
        "produced_at": NOW + timedelta(seconds=2),
        "effective_at": NOW + timedelta(seconds=2),
        "expires_at": NOW + timedelta(minutes=5),
        "correlation_id": uuid4(),
        "causation_id": uuid4(),
        "trace_id": uuid4(),
        "payload": payload,
    }
    values.update(changes)
    return EventEnvelope[object](**values)


@pytest.mark.parametrize(
    "factory",
    [
        evidence,
        lambda: ResearchPacket(packet_id=uuid4(), cutoff_at=NOW, evidence=(evidence(),), model_version="model-1", schema_version="1.0"),
        signal,
        lambda: TargetPosition(instrument=INSTRUMENT, target_weight=Decimal("0.25")),
        target,
        lambda: PositionSnapshot(instrument=INSTRUMENT, quantity=Quantity(Decimal("1"), 0), observed_at=NOW),
        lambda: PortfolioSnapshot(snapshot_id=uuid4(), positions=(PositionSnapshot(instrument=INSTRUMENT, quantity=Quantity(Decimal("1"), 0), observed_at=NOW),), observed_at=NOW, schema_version="1.0"),
        state,
        risk_decision,
        order_intent,
        order_event,
        fill_event,
    ],
)
def test_contract_families_forbid_unknown_fields(factory: object) -> None:
    contract = factory()  # type: ignore[operator]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        type(contract).model_validate({**contract.model_dump(), "unexpected": True})


def test_envelope_forbids_unknown_fields_is_frozen_and_has_all_authority_fields() -> None:
    event = envelope(signal())
    assert tuple(EventEnvelope[object].model_fields) == (
        "event_id", "event_type", "schema_version", "source", "stream_id", "sequence",
        "observed_at", "ingested_at", "produced_at", "effective_at", "expires_at",
        "correlation_id", "causation_id", "trace_id", "payload",
    )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EventEnvelope[SignalProposal].model_validate({**event.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError, match="frozen"):
        event.sequence = 2  # type: ignore[misc]


@pytest.mark.parametrize("field", ["observed_at", "ingested_at", "produced_at", "effective_at", "expires_at"])
def test_envelope_requires_utc_timestamps(field: str) -> None:
    with pytest.raises(ValidationError, match="UTC"):
        envelope(signal(), **{field: NOW.replace(tzinfo=None)})
    with pytest.raises(ValidationError, match="UTC"):
        envelope(signal(), **{field: NOW.astimezone(timezone(timedelta(hours=-4)))})


@pytest.mark.parametrize("sequence", [0, -1, True, 1.0, "1"])
def test_envelope_requires_positive_non_boolean_strict_sequence(sequence: object) -> None:
    with pytest.raises(ValidationError):
        envelope(signal(), sequence=sequence)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": NOW + timedelta(seconds=2)},
        {"observed_at": NOW + timedelta(seconds=2)},
        {"ingested_at": NOW + timedelta(seconds=3)},
    ],
)
def test_envelope_rejects_invalid_timeline(changes: dict[str, datetime]) -> None:
    with pytest.raises(ValidationError):
        envelope(signal(), **changes)


def test_batch_validation_rejects_duplicate_ids_and_non_increasing_per_stream_only() -> None:
    stream = uuid4()
    first = envelope(signal(), stream_id=stream, sequence=1)
    second = envelope(signal(), stream_id=stream, sequence=2)
    assert validate_event_batch((first, second)) == (first, second)
    independent = envelope(signal(), stream_id=uuid4(), sequence=1)
    assert validate_event_batch((first, independent)) == (first, independent)
    with pytest.raises(ValueError, match="duplicate event_id"):
        validate_event_batch((first, envelope(signal(), event_id=first.event_id, stream_id=uuid4(), sequence=1)))
    with pytest.raises(ValueError, match="non-increasing sequence"):
        validate_event_batch((second, first))


@pytest.mark.parametrize("field", ["credential", "api_key", "account_id", "routing", "order_type", "execution_instruction", "execution_text"])
def test_signal_rejects_credentials_routing_and_execution_fields(field: str) -> None:
    value: object = OrderType.MARKET if field == "order_type" else "forbidden"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SignalProposal.model_validate({**signal().model_dump(), field: value})


@pytest.mark.parametrize(
    "value",
    [
        "research",
        "credential",
        "api_key",
        "account-routing",
        "order_type",
        "execution-instruction",
    ],
)
def test_evidence_source_requires_closed_typed_enum(value: str) -> None:
    with pytest.raises(ValidationError):
        EvidenceReference(
            evidence_id=uuid4(),
            source=value,  # type: ignore[arg-type]
            locator=evidence().locator,
            observed_at=NOW,
            schema_version="1.0",
        )


@pytest.mark.parametrize(
    "value",
    [
        "https://example.invalid/evidence/1",
        "account/primary",
        "api_key/secret",
        "order_type/market",
        "execution/buy-now",
    ],
)
def test_evidence_locator_rejects_free_form_or_sensitive_text(value: str) -> None:
    with pytest.raises(ValidationError):
        EvidenceReference(
            evidence_id=uuid4(),
            source=EvidenceSource.RESEARCH,
            locator=value,  # type: ignore[arg-type]
            observed_at=NOW,
            schema_version="1.0",
        )


@pytest.mark.parametrize(
    "field",
    [
        "query",
        "userinfo",
        "fragment",
        "credential",
        "account_routing",
        "order_type",
        "execution_text",
    ],
)
def test_evidence_locator_json_rejects_unknown_sensitive_fields(field: str) -> None:
    payload = json.loads(evidence().model_dump_json())
    payload["locator"][field] = "forbidden"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EvidenceReference.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "segment",
    [
        "credential",
        "prod-api-key-v1",
        "primary-account-routing",
        "limit-order-type",
        "trade-execution-text",
        "prodtokenv1",
        "prodsecretv1",
        "primaryaccountv1",
        "myroutingvalue",
        "preexecutionpost",
    ],
)
def test_typed_evidence_locator_rejects_sensitive_path_segments(segment: str) -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        EvidenceLocator(
            kind=EvidenceLocatorKind.DATASET,
            authority="research-feed",
            path=(segment,),
        )


@pytest.mark.parametrize(
    "path",
    [
        ("cred", "ential"),
        ("api", "key"),
        ("se", "cret"),
        ("account", "routing"),
        ("order", "type"),
        ("exec", "ution"),
    ],
)
def test_typed_evidence_locator_rejects_sensitive_text_split_across_segments(
    path: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        EvidenceLocator(
            kind=EvidenceLocatorKind.DATASET,
            authority="research-feed",
            path=path,
        )


@pytest.mark.parametrize(
    "segment",
    [
        "tokenized-assets",
        "tokenization-model",
        "tokenizer-vocab",
        "corporate-secretary",
        "corporate-secretariat",
        "accounting-policy",
        "orderbook",
    ],
)
def test_typed_evidence_locator_preserves_legitimate_research_terms(segment: str) -> None:
    locator = EvidenceLocator(
        kind=EvidenceLocatorKind.DATASET,
        authority="research-feed",
        path=(segment,),
    )
    assert locator.path == (segment,)


def test_research_packet_accepts_evidence_at_cutoff_and_rejects_future_evidence() -> None:
    exact = ResearchPacket(
        packet_id=uuid4(),
        cutoff_at=NOW,
        evidence=(evidence(observed_at=NOW),),
        model_version="model-1",
        schema_version="1.0",
    )
    assert exact.evidence[0].observed_at == exact.cutoff_at

    prior = ResearchPacket(
        packet_id=uuid4(),
        cutoff_at=NOW,
        evidence=(evidence(observed_at=NOW - timedelta(microseconds=1)),),
        model_version="model-1",
        schema_version="1.0",
    )
    assert prior.evidence[0].observed_at < prior.cutoff_at

    with pytest.raises(ValidationError, match="observed_at must not be after cutoff_at"):
        ResearchPacket(
            packet_id=uuid4(),
            cutoff_at=NOW,
            evidence=(evidence(observed_at=NOW + timedelta(microseconds=1)),),
            model_version="model-1",
            schema_version="1.0",
        )


def test_signal_aligns_packet_cutoff_signal_cutoff_evidence_and_expiry() -> None:
    proposal = signal()
    assert proposal.research_packet_cutoff_at == proposal.cutoff_at
    assert proposal.expires_at > proposal.cutoff_at
    assert all(item.observed_at <= proposal.cutoff_at for item in proposal.evidence)

    values = {name: getattr(proposal, name) for name in SignalProposal.model_fields}
    with pytest.raises(ValidationError, match="research packet cutoff must equal signal cutoff"):
        SignalProposal.model_validate(
            {
                **values,
                "research_packet_cutoff_at": NOW - timedelta(microseconds=1),
            }
        )
    with pytest.raises(ValidationError, match="observed_at must not be after cutoff_at"):
        SignalProposal.model_validate(
            {
                **values,
                "evidence": (evidence(observed_at=NOW + timedelta(microseconds=1)),),
            }
        )
    with pytest.raises(ValidationError, match="expires_at must be after cutoff_at"):
        SignalProposal.model_validate({**values, "expires_at": NOW})


def test_signal_and_evidence_boundary_timestamps_require_utc() -> None:
    proposal = signal()
    values = {name: getattr(proposal, name) for name in SignalProposal.model_fields}
    for field in ("research_packet_cutoff_at", "cutoff_at", "expires_at"):
        with pytest.raises(ValidationError, match="UTC"):
            SignalProposal.model_validate({**values, field: NOW.replace(tzinfo=None)})
        with pytest.raises(ValidationError, match="UTC"):
            SignalProposal.model_validate(
                {
                    **values,
                    field: NOW.astimezone(timezone(timedelta(hours=-4))),
                }
            )

    with pytest.raises(ValidationError, match="UTC"):
        evidence(observed_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError, match="UTC"):
        evidence(observed_at=NOW.astimezone(timezone(timedelta(hours=-4))))

    for cutoff_at in (
        NOW.replace(tzinfo=None),
        NOW.astimezone(timezone(timedelta(hours=-4))),
    ):
        with pytest.raises(ValidationError, match="UTC"):
            ResearchPacket(
                packet_id=uuid4(),
                cutoff_at=cutoff_at,
                evidence=(evidence(),),
                model_version="model-1",
                schema_version="1.0",
            )


def test_signal_requires_non_empty_evidence_and_decimal_score_confidence() -> None:
    values = signal().model_dump()
    with pytest.raises(ValidationError):
        SignalProposal.model_validate({**values, "evidence": ()})
    for field in ("score", "confidence"):
        for invalid in (1, 1.0, True, "1", Decimal("NaN"), Decimal("Infinity")):
            with pytest.raises(ValidationError):
                SignalProposal.model_validate({**values, field: invalid})


@pytest.mark.parametrize(
    ("field", "accepted", "rejected"),
    [
        ("score", (Decimal("-1"), Decimal("1")), (Decimal("-1.01"), Decimal("1.01"))),
        ("confidence", (Decimal("0"), Decimal("1")), (Decimal("-0.01"), Decimal("1.01"))),
    ],
)
def test_signal_score_and_confidence_enforce_closed_decimal_bounds(
    field: str, accepted: tuple[Decimal, ...], rejected: tuple[Decimal, ...]
) -> None:
    proposal = signal()
    values = {name: getattr(proposal, name) for name in SignalProposal.model_fields}
    for value in accepted:
        assert SignalProposal.model_validate({**values, field: value}).model_dump()[field] == value
    for value in rejected:
        with pytest.raises(ValidationError):
            SignalProposal.model_validate({**values, field: value})


@pytest.mark.parametrize("factory", [signal, target])
def test_decimal_contracts_round_trip_canonical_json(factory: object) -> None:
    contract = factory()  # type: ignore[operator]
    restored = type(contract).model_validate_json(contract.model_dump_json())
    assert restored == contract


@pytest.mark.parametrize(
    "invalid",
    [1, 1.0, True, "1E-1", "+0.5", "01.0", "NaN", "Infinity", "-Infinity"],
)
def test_decimal_contracts_reject_noncanonical_json_values(invalid: object) -> None:
    proposal = signal()
    proposal_data = json.loads(proposal.model_dump_json())
    proposal_data["score"] = invalid
    with pytest.raises(ValidationError):
        SignalProposal.model_validate_json(json.dumps(proposal_data))

    portfolio = target()
    portfolio_data = json.loads(portfolio.model_dump_json())
    portfolio_data["positions"][0]["target_weight"] = invalid
    with pytest.raises(ValidationError):
        TargetPortfolio.model_validate_json(json.dumps(portfolio_data))


@pytest.mark.parametrize(
    "field",
    [
        ("quantity", "value"),
        ("last_fill_price", "amount"),
        ("commission", "amount"),
    ],
)
@pytest.mark.parametrize("invalid", [1, 1.0, True])
def test_nested_d01_primitives_reject_json_numeric_and_boolean_tokens(
    field: tuple[str, str], invalid: object
) -> None:
    payload = json.loads(fill_event().model_dump_json())
    payload[field[0]][field[1]] = invalid

    with pytest.raises(ValidationError):
        FillEvent.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "invalid",
    ["1e-1", "+1", "01", "1.", ".1", "", "NaN", "Infinity", "-Infinity"],
)
def test_nested_d01_primitives_reject_noncanonical_json_decimal_strings(invalid: str) -> None:
    payload = json.loads(fill_event().model_dump_json())
    payload["last_fill_price"]["amount"] = invalid

    with pytest.raises(ValidationError):
        FillEvent.model_validate_json(json.dumps(payload))


def test_nested_d01_primitives_round_trip_supported_precision_canonical_json_strings() -> None:
    original = fill_event()
    fill = FillEvent.model_validate(
        {
            **fill_values(original),
            "last_fill_price": Price(Decimal("100.01"), Currency.USD),
            "average_fill_price": Price(Decimal("100.01"), Currency.USD),
            "commission": Money(Decimal("0.01"), Currency.ETH),
        }
    )

    serialized = json.loads(fill.model_dump_json())
    assert serialized["quantity"]["value"] == "1.25"
    assert serialized["last_fill_price"]["amount"] == "100.01"
    assert serialized["commission"]["amount"] == "0.01"
    restored = FillEvent.model_validate_json(json.dumps(serialized))
    assert restored.last_fill_price.amount == Decimal("100.01")
    assert restored.commission.amount == Decimal("0.01")


@pytest.mark.parametrize(
    ("factory", "payload_type"),
    [(signal, SignalProposal), (target, TargetPortfolio)],
)
def test_typed_event_envelope_round_trips_canonical_json(
    factory: object, payload_type: type[object]
) -> None:
    payload = factory()  # type: ignore[operator]
    envelope_type = EventEnvelope[payload_type]  # type: ignore[valid-type]
    wrapped = envelope_type(
        **{
            name: value
            for name, value in envelope(payload).model_dump().items()
            if name != "payload"
        },
        payload=payload,
    )
    restored = envelope_type.model_validate_json(wrapped.model_dump_json())
    assert restored == wrapped
    assert isinstance(restored.payload, payload_type)


def test_typed_event_envelope_rejects_mismatched_event_type_in_python_and_json() -> None:
    event_type = EventEnvelope[SignalProposal]
    values = envelope(signal()).model_dump()
    values["payload"] = signal()

    with pytest.raises(ValidationError, match="event_type"):
        event_type.model_validate({**values, "event_type": "FillEvent"})

    serialized = json.loads(event_type.model_validate(values).model_dump_json())
    serialized["event_type"] = "FillEvent"
    with pytest.raises(ValidationError, match="event_type"):
        event_type.model_validate_json(json.dumps(serialized))


def test_contracts_revalidate_nested_d01_dataclass_instances() -> None:
    corrupted = object.__new__(InstrumentId)
    object.__setattr__(corrupted, "symbol", "BTC/USD")
    object.__setattr__(corrupted, "product_type", ProductType.CRYPTO_SPOT)
    object.__setattr__(corrupted, "venue", "BAD VENUE")

    proposal = signal()
    with pytest.raises(ValidationError):
        SignalProposal(
            signal_id=proposal.signal_id,
            research_packet_id=proposal.research_packet_id,
            instrument=corrupted,
            direction=proposal.direction,
            score=proposal.score,
            confidence=proposal.confidence,
            research_packet_cutoff_at=proposal.research_packet_cutoff_at,
            cutoff_at=proposal.cutoff_at,
            expires_at=proposal.expires_at,
            evidence=proposal.evidence,
            model_version=proposal.model_version,
            strategy_version=proposal.strategy_version,
            schema_version=proposal.schema_version,
        )


@pytest.mark.parametrize(
    ("model", "field", "forged"),
    [
        (
            PositionSnapshot,
            "quantity",
            lambda: _forged_instance(Quantity, value=Decimal("1"), precision=19),
        ),
        (
            FillEvent,
            "last_fill_price",
            lambda: _forged_instance(Price, amount=Decimal("0"), currency=Currency.USD),
        ),
        (
            FillEvent,
            "commission",
            lambda: _forged_instance(Money, amount=Decimal("NaN"), currency=Currency.USD),
        ),
    ],
)
def test_contracts_revalidate_all_nested_d01_dataclass_instances(
    model: type[object], field: str, forged: object
) -> None:
    if model is PositionSnapshot:
        values: dict[str, object] = PositionSnapshot(
            instrument=INSTRUMENT,
            quantity=Quantity(Decimal("1"), 0),
            observed_at=NOW,
        ).model_dump()
    else:
        values = fill_event().model_dump()
    with pytest.raises(ValidationError):
        model.model_validate({**values, field: forged()})  # type: ignore[attr-defined, operator]


def _forged_instance(type_: type[object], **fields: object) -> object:
    forged = object.__new__(type_)
    for name, value in fields.items():
        object.__setattr__(forged, name, value)
    return forged


def test_target_portfolio_validates_weights_and_duplicate_instruments() -> None:
    portfolio = target()
    duplicate = TargetPosition(instrument=INSTRUMENT, target_weight=Decimal("0.1"))
    with pytest.raises(ValidationError, match="duplicate"):
        TargetPortfolio(target_id=portfolio.target_id, positions=(*portfolio.positions, duplicate), source_signal_ids=portfolio.source_signal_ids, effective_at=portfolio.effective_at, schema_version=portfolio.schema_version)
    for invalid in (1, 1.0, True, "1", Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValidationError):
            TargetPosition.model_validate({"instrument": INSTRUMENT, "target_weight": invalid})
    with pytest.raises(ValidationError):
        TargetPortfolio(target_id=portfolio.target_id, positions=(TargetPosition(instrument=INSTRUMENT, target_weight=Decimal("1.01")),), source_signal_ids=portfolio.source_signal_ids, effective_at=portfolio.effective_at, schema_version=portfolio.schema_version)


def test_target_portfolio_requires_unique_signal_provenance() -> None:
    signal_id = uuid4()
    with pytest.raises(ValidationError):
        target(source_signal_ids=())
    with pytest.raises(ValidationError, match="duplicate"):
        target(source_signal_ids=(signal_id, signal_id))


def test_risk_decision_outcome_target_semantics_are_fail_closed() -> None:
    original = target()
    approved = RiskDecision(
        decision_id=uuid4(),
        original_target=original,
        approved_target=original,
        outcome=RiskOutcome.APPROVED,
        reason_codes=(RiskReasonCode.WITHIN_LIMITS,),
        policy_version="risk-1",
        state_snapshot=state(),
        decided_at=NOW,
        schema_version="1.0",
    )
    assert approved.approved_target == original

    modified_target = TargetPortfolio(
        target_id=uuid4(),
        positions=(TargetPosition(instrument=INSTRUMENT, target_weight=Decimal("0.10")),),
        source_signal_ids=original.source_signal_ids,
        effective_at=original.effective_at,
        schema_version=original.schema_version,
    )
    modified = RiskDecision(
        decision_id=uuid4(),
        original_target=original,
        approved_target=modified_target,
        outcome=RiskOutcome.MODIFIED,
        reason_codes=(RiskReasonCode.GROSS_EXPOSURE_LIMIT,),
        policy_version="risk-1",
        state_snapshot=state(),
        decided_at=NOW,
        schema_version="1.0",
    )
    assert modified.approved_target == modified_target

    modified_values = {
        name: getattr(modified_target, name) for name in TargetPortfolio.model_fields
    }
    provenance_drifts = (
        {"source_signal_ids": (uuid4(),)},
        {"effective_at": NOW - timedelta(microseconds=1)},
        {"schema_version": "2.0"},
    )
    for drift in provenance_drifts:
        drifted_target = TargetPortfolio.model_validate({**modified_values, **drift})
        with pytest.raises(ValidationError, match="preserve target provenance"):
            RiskDecision(
                decision_id=uuid4(),
                original_target=original,
                approved_target=drifted_target,
                outcome=RiskOutcome.MODIFIED,
                reason_codes=(RiskReasonCode.GROSS_EXPOSURE_LIMIT,),
                policy_version="risk-1",
                state_snapshot=state(),
                decided_at=NOW,
                schema_version="1.0",
            )

    second_instrument = InstrumentId("ETH-USD", ProductType.CRYPTO_SPOT, "ALPACA")
    multi_position_original = TargetPortfolio(
        target_id=uuid4(),
        positions=(
            TargetPosition(instrument=INSTRUMENT, target_weight=Decimal("0.25")),
            TargetPosition(instrument=second_instrument, target_weight=Decimal("0.10")),
        ),
        source_signal_ids=original.source_signal_ids,
        effective_at=original.effective_at,
        schema_version=original.schema_version,
    )
    reordered_only = TargetPortfolio(
        target_id=uuid4(),
        positions=tuple(reversed(multi_position_original.positions)),
        source_signal_ids=multi_position_original.source_signal_ids,
        effective_at=multi_position_original.effective_at,
        schema_version=multi_position_original.schema_version,
    )
    with pytest.raises(ValidationError, match="changed target positions"):
        RiskDecision(
            decision_id=uuid4(),
            original_target=multi_position_original,
            approved_target=reordered_only,
            outcome=RiskOutcome.MODIFIED,
            reason_codes=(RiskReasonCode.GROSS_EXPOSURE_LIMIT,),
            policy_version="risk-1",
            state_snapshot=state(),
            decided_at=NOW,
            schema_version="1.0",
        )

    rejected = RiskDecision(
        decision_id=uuid4(),
        original_target=original,
        approved_target=None,
        outcome=RiskOutcome.REJECTED,
        reason_codes=(RiskReasonCode.DATA_STALE,),
        policy_version="risk-1",
        state_snapshot=state(),
        decided_at=NOW,
        schema_version="1.0",
    )
    assert rejected.approved_target is None

    approved_values = {
        name: getattr(approved, name) for name in RiskDecision.model_fields
    }
    with pytest.raises(ValidationError, match="approved outcome"):
        RiskDecision.model_validate({**approved_values, "approved_target": modified_target})
    with pytest.raises(ValidationError, match="modified outcome"):
        RiskDecision.model_validate({**approved_values, "outcome": RiskOutcome.MODIFIED})
    with pytest.raises(ValidationError, match="rejected outcome"):
        RiskDecision.model_validate(
            {
                **approved_values,
                "outcome": RiskOutcome.REJECTED,
                "reason_codes": (RiskReasonCode.DATA_STALE,),
            }
        )


def test_risk_decision_reason_codes_are_typed_unique_and_canonical() -> None:
    original = target()
    values = {
        "decision_id": uuid4(),
        "original_target": original,
        "approved_target": None,
        "outcome": RiskOutcome.REJECTED,
        "reason_codes": (
            RiskReasonCode.DATA_STALE,
            RiskReasonCode.SIGNAL_EXPIRED,
        ),
        "policy_version": "risk-1",
        "state_snapshot": state(),
        "decided_at": NOW,
        "schema_version": "1.0",
    }
    RiskDecision(**values)
    with pytest.raises(ValidationError, match="duplicate"):
        RiskDecision(**{**values, "reason_codes": (RiskReasonCode.DATA_STALE,) * 2})
    with pytest.raises(ValidationError, match="canonical"):
        RiskDecision(**{**values, "reason_codes": tuple(reversed(values["reason_codes"]))})
    with pytest.raises(ValidationError):
        RiskDecision(**{**values, "reason_codes": ("DATA_STALE",)})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="WITHIN_LIMITS"):
        RiskDecision(**{**values, "reason_codes": (RiskReasonCode.WITHIN_LIMITS,)})


def test_risk_decision_binds_target_and_state_time_and_kill_switch() -> None:
    original = target()
    values = {
        "decision_id": uuid4(),
        "original_target": original,
        "approved_target": original,
        "outcome": RiskOutcome.APPROVED,
        "reason_codes": (RiskReasonCode.WITHIN_LIMITS,),
        "policy_version": "risk-1",
        "state_snapshot": state(),
        "decided_at": NOW,
        "schema_version": "1.0",
    }
    future_state_values = {
        name: getattr(values["state_snapshot"], name)
        for name in RiskStateSnapshot.model_fields
    }
    future_state = RiskStateSnapshot.model_validate(
        {**future_state_values, "observed_at": NOW + timedelta(microseconds=1)}
    )
    with pytest.raises(ValidationError, match="state snapshot"):
        RiskDecision(**{**values, "state_snapshot": future_state})

    future_target = TargetPortfolio(
        target_id=uuid4(),
        positions=original.positions,
        source_signal_ids=original.source_signal_ids,
        effective_at=NOW + timedelta(microseconds=1),
        schema_version=original.schema_version,
    )
    with pytest.raises(ValidationError, match="original target"):
        RiskDecision(
            **{**values, "original_target": future_target, "approved_target": future_target}
        )

    halted_values = {
        name: getattr(values["state_snapshot"], name)
        for name in RiskStateSnapshot.model_fields
    }
    halted = RiskStateSnapshot.model_validate(
        {**halted_values, "kill_switch_engaged": True}
    )
    with pytest.raises(ValidationError, match="kill switch"):
        RiskDecision(**{**values, "state_snapshot": halted})
    halted_rejection = RiskDecision(
        **{
            **values,
            "approved_target": None,
            "outcome": RiskOutcome.REJECTED,
            "reason_codes": (RiskReasonCode.GLOBAL_HALT,),
            "state_snapshot": halted,
        }
    )
    assert halted_rejection.outcome is RiskOutcome.REJECTED
    with pytest.raises(ValidationError, match="GLOBAL_HALT requires"):
        RiskDecision(
            **{
                **values,
                "approved_target": None,
                "outcome": RiskOutcome.REJECTED,
                "reason_codes": (RiskReasonCode.GLOBAL_HALT,),
            }
        )


def test_risk_state_snapshot_is_complete_utc_and_requires_unique_open_orders() -> None:
    snapshot = state()
    values = {name: getattr(snapshot, name) for name in RiskStateSnapshot.model_fields}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RiskStateSnapshot.model_validate({**values, "unexpected": True})
    with pytest.raises(ValidationError, match="UTC"):
        RiskStateSnapshot.model_validate({**values, "observed_at": NOW.replace(tzinfo=None)})
    idle = RiskStateSnapshot.model_validate({**values, "open_order_ids": ()})
    assert idle.open_order_ids == ()
    with pytest.raises(ValidationError, match="duplicate"):
        RiskStateSnapshot.model_validate({**values, "open_order_ids": (values["open_order_ids"][0],) * 2})

    portfolio_values = {
        name: getattr(snapshot.portfolio, name)
        for name in PortfolioSnapshot.model_fields
    }
    future_portfolio = PortfolioSnapshot.model_validate(
        {**portfolio_values, "observed_at": NOW + timedelta(microseconds=1)}
    )
    with pytest.raises(ValidationError, match="portfolio snapshot"):
        RiskStateSnapshot.model_validate({**values, "portfolio": future_portfolio})

    future_position = PositionSnapshot(
        instrument=INSTRUMENT,
        quantity=Quantity(Decimal("1"), 0),
        observed_at=NOW + timedelta(microseconds=1),
    )
    portfolio_with_future_position = PortfolioSnapshot.model_validate(
        {**portfolio_values, "positions": (future_position,)}
    )
    with pytest.raises(ValidationError, match="position snapshot"):
        RiskStateSnapshot.model_validate(
            {**values, "portfolio": portfolio_with_future_position}
        )


def test_typed_provenance_chain_links_exactly() -> None:
    packet = research_packet()
    proposal = SignalProposal(
        signal_id=uuid4(),
        research_packet_id=packet.packet_id,
        instrument=INSTRUMENT,
        direction=SignalDirection.LONG,
        score=Decimal("0.9"),
        confidence=Decimal("0.8"),
        research_packet_cutoff_at=packet.cutoff_at,
        cutoff_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        evidence=packet.evidence,
        model_version=packet.model_version,
        strategy_version="strategy-1",
        schema_version="1.0",
    )
    original = target(source_signal_ids=(proposal.signal_id,))
    decision = RiskDecision(
        decision_id=uuid4(),
        original_target=original,
        approved_target=original,
        outcome=RiskOutcome.APPROVED,
        reason_codes=(RiskReasonCode.WITHIN_LIMITS,),
        policy_version="risk-1",
        state_snapshot=state(),
        decided_at=NOW,
        schema_version="1.0",
    )
    intent = OrderIntent(
        intent_id=uuid4(),
        risk_decision_id=decision.decision_id,
        client_order_id="client-provenance",
        strategy_id="strategy-1",
        trader_id="trader-1",
        account_id="account-1",
        execution_client_id="execution-client-1",
        instrument=INSTRUMENT,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        quantity=OrderQuantity(Decimal("1"), 0),
        requested_at=NOW,
        schema_version="1.0",
    )
    order = OrderEvent.create(
        event_id=uuid4(),
        order_id=uuid4(),
        sequence=1,
        target_status=OrderStatus.SUBMITTED,
        occurred_at=NOW,
        schema_version="2.0",
    )
    fill = FillEvent.model_validate(
        {
            **fill_values(fill_event()),
            "order_id": order.order_id,
        }
    )
    assert proposal.research_packet_id == packet.packet_id
    assert decision.approved_target is not None
    assert original.source_signal_ids == decision.approved_target.source_signal_ids == (proposal.signal_id,)
    assert intent.risk_decision_id == decision.decision_id
    assert intent.client_order_id == "client-provenance"
    assert fill.order_id == order.order_id


def test_orders_and_fills_require_enums_positive_quantities_and_correct_limit_price() -> None:
    intent = order_intent()
    values = {name: getattr(intent, name) for name in type(intent).model_fields}
    with pytest.raises(ValidationError):
        OrderIntent.model_validate({**values, "side": "buy"})
    with pytest.raises(ValidationError, match="limit_price"):
        OrderIntent.model_validate({**values, "limit_price": None})
    market = order_intent(OrderType.MARKET)
    market_values = {name: getattr(market, name) for name in type(market).model_fields}
    with pytest.raises(ValidationError, match="limit_price"):
        OrderIntent.model_validate({**market_values, "limit_price": Price(Decimal("1"), Currency.USD)})
    zero_quantity = OrderQuantity(Decimal("0"), 0)
    with pytest.raises(ValidationError, match="quantity"):
        OrderIntent.model_validate({**values, "quantity": zero_quantity})
    with pytest.raises(ValidationError, match="quantity"):
        fill = fill_event()
        FillEvent.model_validate(
            {
                **fill_values(fill),
                "quantity": OrderQuantity(Decimal("0"), 2),
            }
        )
    with pytest.raises(ValidationError, match="commission"):
        fill = fill_event()
        FillEvent.model_validate(
            {
                **fill_values(fill),
                "commission": Money(Decimal("-0.01"), Currency.USD),
            }
        )

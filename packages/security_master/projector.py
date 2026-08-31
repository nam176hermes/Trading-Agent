"""Fail-closed P2 security-master projection into the fixed P1 paper profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
from typing import TYPE_CHECKING, Annotated, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.domain import (
    AssetClass,
    Currency,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    MarketSnapshot,
    MarketTimeframe,
    Money,
    OrderQuantity,
    Price,
    ProductType,
    decimal_to_scaled_integer,
)
from packages.engine_contracts import CanonicalUtcDateTime, Sha256Hex, canonical_json_bytes
from packages.nautilus_runtime_contracts import (
    P1EngineConfigurationV1,
    P1InstrumentCatalogV1,
    P1MarketDataManifestV1,
    P1TargetScheduleV1,
    parse_canonical_artifact,
)

from .models import (
    AssetKind,
    AssetPayloadV1,
    IssuerPayloadV1,
    ListingPayloadV1,
    PersistedSecurityMasterRevisionV1,
    SecurityMasterIdentityKind,
    SecurityPayloadV1,
    SymbolMappingPayloadV1,
    VenuePayloadV1,
)
from .resolver import SecurityMasterResolver


if TYPE_CHECKING:
    from packages.data_catalog import (
        MaterializedMarketDatasetV1,
        MaterializedSecurityMasterSnapshotV1,
    )


_Component = Literal[
    "mapping",
    "listing",
    "security",
    "venue",
    "base_asset",
    "base_issuer",
    "quote_asset",
    "quote_issuer",
]
_COMPONENT_ORDER: tuple[_Component, ...] = (
    "mapping",
    "listing",
    "security",
    "venue",
    "base_asset",
    "base_issuer",
    "quote_asset",
    "quote_issuer",
)
_PRICE_MAX = Decimal("17014118346046")
_QUANTITY_MAX = Decimal("34028236692093")
_PayloadT = TypeVar("_PayloadT")


class P1ProjectionError(ValueError):
    """Verified inputs cannot be represented by the fixed P1 paper profile."""


class _ReceiptModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class P1ProjectionSelectedRevisionV1(_ReceiptModel):
    component: _Component
    revision_id: UUID
    revision_sha256: Sha256Hex
    recorded_at: CanonicalUtcDateTime


class P1ProjectionArtifactDigestsV1(_ReceiptModel):
    engine_configuration_sha256: Sha256Hex
    instrument_catalog_sha256: Sha256Hex
    market_data_sha256: Sha256Hex
    market_data_manifest_sha256: Sha256Hex
    target_schedule_sha256: Sha256Hex


class P1PaperProjectionReceiptV1(_ReceiptModel):
    schema_version: Literal["security-master-p1-paper-projection-receipt-v1"]
    security_master_snapshot_digest: Sha256Hex
    security_master_manifest_sha256: Sha256Hex
    security_master_knowledge_cutoff: CanonicalUtcDateTime
    market_dataset_content_digest: Sha256Hex
    market_dataset_manifest_sha256: Sha256Hex
    market_known_at: CanonicalUtcDateTime
    first_market_close: CanonicalUtcDateTime
    last_market_close: CanonicalUtcDateTime
    selected_revisions: Annotated[
        tuple[P1ProjectionSelectedRevisionV1, ...], Field(min_length=8, max_length=8)
    ]
    artifacts: P1ProjectionArtifactDigestsV1
    paper_local_only: Literal[True]
    network_enabled: Literal[False]
    live_authorized: Literal[False]
    production_authorized: Literal[False]
    broker_access_authorized: Literal[False]
    database_runtime_authorized: Literal[False]

    @model_validator(mode="after")
    def _exact_selected_graph(self) -> "P1PaperProjectionReceiptV1":
        if tuple(item.component for item in self.selected_revisions) != _COMPONENT_ORDER:
            raise ValueError("selected revisions must bind the exact P1 graph")
        if self.last_market_close < self.first_market_close:
            raise ValueError("projection market window is invalid")
        if self.market_known_at > self.security_master_knowledge_cutoff:
            raise ValueError("market knowledge exceeds the security-master cutoff")
        return self


@dataclass(frozen=True, slots=True)
class P1PaperProjectionV1:
    instrument_definition: InstrumentDefinition
    engine_configuration: bytes
    instrument_catalog: bytes
    market_data: bytes
    market_data_manifest: bytes
    target_schedule: bytes
    receipt: P1PaperProjectionReceiptV1
    receipt_bytes: bytes


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _artifact_bytes(value: BaseModel) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _payload(
    persisted: PersistedSecurityMasterRevisionV1,
    expected: type[_PayloadT],
    component: str,
) -> _PayloadT:
    value = persisted.revision.payload
    if not isinstance(value, expected):
        raise P1ProjectionError(f"{component} payload is not the exact required type")
    return value


def _resolve_graph(
    resolver: SecurityMasterResolver,
    *,
    valid_at: datetime,
    known_at: datetime,
) -> dict[_Component, PersistedSecurityMasterRevisionV1]:
    mapping = resolver.resolve_symbol_mapping(
        provider="BINANCE",
        raw_symbol="BTCUSDT",
        valid_at=valid_at,
        known_at=known_at,
    )
    mapping_payload = _payload(mapping, SymbolMappingPayloadV1, "mapping")
    listing = resolver.require_one(
        kind=SecurityMasterIdentityKind.LISTING,
        subject_id=mapping_payload.listing_id,
        valid_at=valid_at,
        known_at=known_at,
    )
    listing_payload = _payload(listing, ListingPayloadV1, "listing")
    security = resolver.require_one(
        kind=SecurityMasterIdentityKind.SECURITY,
        subject_id=listing_payload.security_id,
        valid_at=valid_at,
        known_at=known_at,
    )
    security_payload = _payload(security, SecurityPayloadV1, "security")
    venue = resolver.require_one(
        kind=SecurityMasterIdentityKind.VENUE,
        subject_id=listing_payload.venue_id,
        valid_at=valid_at,
        known_at=known_at,
    )
    base_asset = resolver.require_one(
        kind=SecurityMasterIdentityKind.ASSET,
        subject_id=security_payload.primary_asset_id,
        valid_at=valid_at,
        known_at=known_at,
    )
    base_payload = _payload(base_asset, AssetPayloadV1, "base asset")
    base_issuer = resolver.require_one(
        kind=SecurityMasterIdentityKind.ISSUER,
        subject_id=base_payload.issuer_id,
        valid_at=valid_at,
        known_at=known_at,
    )
    quote_asset = resolver.require_one(
        kind=SecurityMasterIdentityKind.ASSET,
        subject_id=listing_payload.quote_asset_id,
        valid_at=valid_at,
        known_at=known_at,
    )
    quote_payload = _payload(quote_asset, AssetPayloadV1, "quote asset")
    quote_issuer = resolver.require_one(
        kind=SecurityMasterIdentityKind.ISSUER,
        subject_id=quote_payload.issuer_id,
        valid_at=valid_at,
        known_at=known_at,
    )
    _payload(venue, VenuePayloadV1, "venue")
    _payload(base_issuer, IssuerPayloadV1, "base issuer")
    _payload(quote_issuer, IssuerPayloadV1, "quote issuer")
    return {
        "mapping": mapping,
        "listing": listing,
        "security": security,
        "venue": venue,
        "base_asset": base_asset,
        "base_issuer": base_issuer,
        "quote_asset": quote_asset,
        "quote_issuer": quote_issuer,
    }


def _precision(value: Decimal) -> int:
    return max(0, -int(value.as_tuple().exponent))


def _definition(
    graph: dict[_Component, PersistedSecurityMasterRevisionV1],
    *,
    snapshot_digest: str,
    observed_at: datetime,
) -> InstrumentDefinition:
    mapping = _payload(graph["mapping"], SymbolMappingPayloadV1, "mapping")
    listing = _payload(graph["listing"], ListingPayloadV1, "listing")
    security = _payload(graph["security"], SecurityPayloadV1, "security")
    venue = _payload(graph["venue"], VenuePayloadV1, "venue")
    base = _payload(graph["base_asset"], AssetPayloadV1, "base asset")
    quote = _payload(graph["quote_asset"], AssetPayloadV1, "quote asset")
    if (
        mapping.provider != "BINANCE"
        or mapping.raw_symbol != "BTCUSDT"
        or mapping.canonical_symbol != "BTCUSDT"
        or security.product_type is not ProductType.CRYPTO_SPOT
        or venue.code != "BINANCE"
        or listing.session_calendar != "CONTINUOUS"
        or base.code != "BTC"
        or base.asset_kind is not AssetKind.CRYPTO
        or quote.code != "USDT"
        or quote.asset_kind is not AssetKind.CRYPTO
    ):
        raise P1ProjectionError("security-master graph is outside the fixed P1 profile")

    tick = Decimal(listing.tick_size)
    step = Decimal(listing.size_increment)
    minimum_quantity = Decimal(listing.minimum_quantity)
    maximum_quantity = Decimal(listing.maximum_quantity)
    minimum_notional = Decimal(listing.minimum_notional)
    maximum_notional = Decimal(listing.maximum_notional)
    price_precision = _precision(tick)
    size_precision = _precision(step)
    if (
        price_precision > 16
        or size_precision > 16
        or _precision(minimum_quantity) != size_precision
        or tick > _PRICE_MAX
        or min(step, minimum_quantity) > _QUANTITY_MAX
        or maximum_quantity > _QUANTITY_MAX
        or minimum_notional > _PRICE_MAX
    ):
        raise P1ProjectionError("listing cannot be represented by the P1 native profile")

    return InstrumentDefinition(
        instrument_id=InstrumentId("BTCUSDT", ProductType.CRYPTO_SPOT, "BINANCE"),
        raw_symbol=mapping.raw_symbol,
        asset_class=AssetClass.CRYPTO,
        base_currency=Currency.BTC,
        quote_currency=Currency.USDT,
        settlement_currency=Currency.USDT,
        tick_size=Price(tick, Currency.USDT),
        size_increment=OrderQuantity(step, size_precision),
        minimum_quantity=OrderQuantity(minimum_quantity, size_precision),
        maximum_quantity=OrderQuantity(maximum_quantity, size_precision),
        minimum_notional=Money(minimum_notional, Currency.USDT),
        maximum_notional=Money(maximum_notional, Currency.USDT),
        multiplier=Decimal("1"),
        margin=None,
        session_calendar=listing.session_calendar,
        provenance=InstrumentProvenance(
            source_id="SECURITY-MASTER",
            source_revision=snapshot_digest[:32],
            observed_at=observed_at,
        ),
    )


def _market_bytes(
    snapshot: MarketSnapshot,
    *,
    price_precision: int,
    size_precision: int,
    tick_size: Decimal,
    step_size: Decimal,
) -> tuple[bytes, tuple[datetime, ...]]:
    tick_units = decimal_to_scaled_integer(tick_size, price_precision)
    step_units = decimal_to_scaled_integer(step_size, size_precision)
    rows: list[bytes] = []
    closes: list[datetime] = []
    for sequence, candle in enumerate(snapshot.candles, start=1):
        values = candle.model_dump(mode="json")
        for name in ("open", "high", "low", "close"):
            value = getattr(candle, name)
            try:
                price_units = decimal_to_scaled_integer(value, price_precision)
            except ValueError as exc:
                raise P1ProjectionError(
                    "market price is outside the P1 catalog grid"
                ) from exc
            if value > _PRICE_MAX or price_units % tick_units:
                raise P1ProjectionError("market price is outside the P1 catalog grid")
        try:
            volume_units = decimal_to_scaled_integer(candle.volume, size_precision)
        except ValueError as exc:
            raise P1ProjectionError(
                "market volume is outside the P1 catalog grid"
            ) from exc
        if candle.volume > _QUANTITY_MAX or volume_units % step_units:
            raise P1ProjectionError("market volume is outside the P1 catalog grid")
        close_time = (candle.open_time + timedelta(minutes=1)).astimezone(UTC)
        close_text = close_time.isoformat().replace("+00:00", "Z")
        bid_name, ask_name = (
            ("open", "close") if candle.open <= candle.close else ("close", "open")
        )
        row = {
            "ask": values[ask_name],
            "bid": values[bid_name],
            "close": values["close"],
            "event_time": close_text,
            "high": values["high"],
            "low": values["low"],
            "open": values["open"],
            "quote_time": close_text,
            "sequence": sequence,
            "volume": values["volume"],
        }
        rows.append(canonical_json_bytes(row) + b"\n")
        closes.append(close_time)
    return b"".join(rows), tuple(closes)


def _reject_corporate_actions(
    revisions: tuple[PersistedSecurityMasterRevisionV1, ...],
    resolver: SecurityMasterResolver,
    *,
    security_id: UUID,
    first_close: datetime,
    last_close: datetime,
    known_at: datetime,
) -> None:
    fact_ids = {
        item.revision.fact_id
        for item in revisions
        if item.revision.subject_kind is SecurityMasterIdentityKind.CORPORATE_ACTION
    }
    for fact_id in sorted(fact_ids, key=lambda value: value.bytes):
        active = resolver.resolve_fact(
            fact_id, valid_at=last_close, known_at=known_at
        )
        if active is None:
            continue
        payload = active.revision.payload
        if (
            payload is not None
            and getattr(payload, "security_id", None) == security_id
            and first_close <= active.revision.effective_from <= last_close
        ):
            raise P1ProjectionError("corporate action falls inside the market window")


def project_p1_paper_inputs(
    security_master_snapshot: "MaterializedSecurityMasterSnapshotV1",
    market_dataset: "MaterializedMarketDatasetV1",
    target_schedule: P1TargetScheduleV1,
) -> P1PaperProjectionV1:
    """Verify immutable sources and project only BTCUSDT.BINANCE paper inputs."""

    from packages.data_catalog import (
        verify_materialized_catalog,
        verify_security_master_snapshot,
    )

    try:
        revisions = verify_security_master_snapshot(security_master_snapshot)
        market = verify_materialized_catalog(market_dataset)
        if type(target_schedule) is not P1TargetScheduleV1:
            raise P1ProjectionError("exact P1TargetScheduleV1 is required")
        target_bytes = _artifact_bytes(target_schedule)
        try:
            target = parse_canonical_artifact(P1TargetScheduleV1, target_bytes)
        except ValueError as exc:
            raise P1ProjectionError("target schedule is invalid") from exc
        if any(
            len(item.positions) != 1
            or item.positions[0].instrument.symbol != "BTCUSDT"
            or item.positions[0].instrument.venue != "BINANCE"
            or item.positions[0].instrument.product_type is not ProductType.CRYPTO_SPOT
            for item in target.targets
        ):
            raise P1ProjectionError("target schedule instrument is unsupported")
        cutoff = security_master_snapshot.manifest.knowledge_cutoff
        if (
            market.instrument
            != InstrumentId("BTCUSDT", ProductType.CRYPTO_SPOT, "BINANCE")
            or market.timeframe is not MarketTimeframe.ONE_MINUTE
            or not market.continuity.is_continuous
            or market.normalization_version != "market-normalization-v1"
            or market.known_at > cutoff
        ):
            raise P1ProjectionError("market dataset is outside the fixed P1 profile")

        resolver = SecurityMasterResolver(revisions)
        close_times = tuple(
            (candle.open_time + timedelta(minutes=1)).astimezone(UTC)
            for candle in market.candles
        )
        first_close = close_times[0]
        last_close = close_times[-1]
        graph = _resolve_graph(resolver, valid_at=first_close, known_at=cutoff)
        if any(
            graph != _resolve_graph(resolver, valid_at=close_time, known_at=cutoff)
            for close_time in close_times[1:]
        ):
            raise P1ProjectionError("security-master graph changes inside the market window")

        definition = _definition(
            graph,
            snapshot_digest=security_master_snapshot.manifest.snapshot_digest,
            observed_at=cutoff,
        )
        listing = _payload(graph["listing"], ListingPayloadV1, "listing")
        security = _payload(graph["security"], SecurityPayloadV1, "security")
        _reject_corporate_actions(
            revisions,
            resolver,
            security_id=security.security_id,
            first_close=first_close,
            last_close=last_close,
            known_at=cutoff,
        )
        market_bytes, encoded_close_times = _market_bytes(
            market,
            price_precision=_precision(Decimal(listing.tick_size)),
            size_precision=_precision(Decimal(listing.size_increment)),
            tick_size=Decimal(listing.tick_size),
            step_size=Decimal(listing.size_increment),
        )
        if encoded_close_times != close_times:
            raise P1ProjectionError("market close projection is inconsistent")
        if any(item.effective_at not in close_times for item in target.targets):
            raise P1ProjectionError("target schedule is outside the market close grid")

        configuration = P1EngineConfigurationV1(
            schema_version="nautilus-p1-engine-configuration-v1",
            venue="BINANCE",
            account_type="CASH",
            oms_type="NETTING",
            starting_currency="USDT",
            starting_balance=Decimal("1000000"),
            fill_model="deterministic",
            fee_model="fixed-rate",
            fee_rate=Decimal("0.001"),
            bar_execution=False,
            allow_leverage=False,
            allow_short=False,
            network_access=False,
            load_state=False,
            save_state=False,
            run_analysis=False,
            logging_bypass=True,
        )
        catalog = P1InstrumentCatalogV1(
            schema_version="nautilus-p1-instrument-catalog-v1",
            instrument_id="BTCUSDT.BINANCE",
            product_type="crypto_spot",
            symbol="BTCUSDT",
            base_currency="BTC",
            quote_currency="USDT",
            venue="BINANCE",
            price_precision=_precision(Decimal(listing.tick_size)),
            size_precision=_precision(Decimal(listing.size_increment)),
            tick_size=Decimal(listing.tick_size),
            step_size=Decimal(listing.size_increment),
            min_quantity=Decimal(listing.minimum_quantity),
            min_notional=Decimal(listing.minimum_notional),
            provenance_sha256=security_master_snapshot.manifest.snapshot_digest,
        )
        engine_bytes = _artifact_bytes(configuration)
        catalog_bytes = _artifact_bytes(catalog)
        manifest = P1MarketDataManifestV1(
            schema_version="nautilus-p1-market-data-manifest-v1",
            media_type="application/jsonl",
            row_count=len(market.candles),
            first_timestamp=first_close,
            last_timestamp=last_close,
            quote_bar_pair_policy="quote-then-bar",
            timeframe="1m",
            timestamp_policy="close",
            data_sha256=_digest(market_bytes),
            catalog_sha256=_digest(catalog_bytes),
            normalization_version="market-normalization-v1",
        )
        manifest_bytes = _artifact_bytes(manifest)
        for model, raw in (
            (P1EngineConfigurationV1, engine_bytes),
            (P1InstrumentCatalogV1, catalog_bytes),
            (P1MarketDataManifestV1, manifest_bytes),
            (P1TargetScheduleV1, target_bytes),
        ):
            parse_canonical_artifact(model, raw)

        selected = tuple(
            P1ProjectionSelectedRevisionV1(
                component=component,
                revision_id=graph[component].revision.revision_id,
                revision_sha256=graph[component].revision.digest,
                recorded_at=graph[component].recorded_at,
            )
            for component in _COMPONENT_ORDER
        )
        artifact_digests = P1ProjectionArtifactDigestsV1(
            engine_configuration_sha256=_digest(engine_bytes),
            instrument_catalog_sha256=_digest(catalog_bytes),
            market_data_sha256=_digest(market_bytes),
            market_data_manifest_sha256=_digest(manifest_bytes),
            target_schedule_sha256=_digest(target_bytes),
        )
        receipt = P1PaperProjectionReceiptV1(
            schema_version="security-master-p1-paper-projection-receipt-v1",
            security_master_snapshot_digest=security_master_snapshot.manifest.snapshot_digest,
            security_master_manifest_sha256=_digest(
                canonical_json_bytes(security_master_snapshot.manifest)
            ),
            security_master_knowledge_cutoff=cutoff,
            market_dataset_content_digest=market_dataset.manifest.content_digest,
            market_dataset_manifest_sha256=_digest(
                canonical_json_bytes(market_dataset.manifest)
            ),
            market_known_at=market.known_at.astimezone(UTC),
            first_market_close=first_close,
            last_market_close=last_close,
            selected_revisions=selected,
            artifacts=artifact_digests,
            paper_local_only=True,
            network_enabled=False,
            live_authorized=False,
            production_authorized=False,
            broker_access_authorized=False,
            database_runtime_authorized=False,
        )
        return P1PaperProjectionV1(
            instrument_definition=definition,
            engine_configuration=engine_bytes,
            instrument_catalog=catalog_bytes,
            market_data=market_bytes,
            market_data_manifest=manifest_bytes,
            target_schedule=target_bytes,
            receipt=receipt,
            receipt_bytes=_artifact_bytes(receipt),
        )
    except P1ProjectionError:
        raise
    except (LookupError, TypeError, ValueError) as exc:
        raise P1ProjectionError("P2 inputs cannot be projected into P1") from exc


__all__ = [
    "P1PaperProjectionReceiptV1",
    "P1PaperProjectionV1",
    "P1ProjectionArtifactDigestsV1",
    "P1ProjectionError",
    "P1ProjectionSelectedRevisionV1",
    "project_p1_paper_inputs",
]

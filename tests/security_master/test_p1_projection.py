from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
from pathlib import Path
from uuid import UUID

import pytest

from packages.data_catalog import (
    CatalogMaterializationError,
    CatalogWorkspaceV1,
    materialize_fixture_catalog,
    materialize_security_master_snapshot,
)
from packages.domain import (
    EvidenceLocator,
    EvidenceLocatorKind,
    EvidenceReference,
    EvidenceSource,
    InstrumentId,
    MarketCandle,
    MarketDataProvenance,
    MarketSnapshot,
    MarketTimeframe,
    ProductType,
)
from packages.engine_contracts import canonical_json_bytes
from packages.nautilus_runtime_contracts import (
    P1EngineConfigurationV1,
    P1InstrumentCatalogV1,
    P1MarketDataManifestV1,
    P1TargetScheduleV1,
    parse_canonical_artifact,
)
import packages.security_master as security_master
from packages.security_master import (
    AssetKind,
    AssetPayloadV1,
    CorporateActionType,
    IssuerPayloadV1,
    ListingPayloadV1,
    PersistedSecurityMasterRevisionV1,
    PostgresSecurityMasterRepository,
    SecurityMasterEvidenceV1,
    SecurityMasterIdentityKind,
    SecurityMasterOperation,
    SecurityMasterRevisionV1,
    SecurityPayloadV1,
    SplitPayloadV1,
    SymbolMappingPayloadV1,
    VenuePayloadV1,
)

from .test_postgres_repository import Connection, Pool


OPEN = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
CUTOFF = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
BTC_ISSUER_ID = UUID("10000000-0000-4000-8000-000000000001")
USDT_ISSUER_ID = UUID("10000000-0000-4000-8000-000000000002")
BTC_ASSET_ID = UUID("20000000-0000-4000-8000-000000000001")
USDT_ASSET_ID = UUID("20000000-0000-4000-8000-000000000002")
SECURITY_ID = UUID("30000000-0000-4000-8000-000000000001")
VENUE_ID = UUID("40000000-0000-4000-8000-000000000001")
LISTING_ID = UUID("50000000-0000-4000-8000-000000000001")
MAPPING_ID = UUID("60000000-0000-4000-8000-000000000001")
RAW_EVIDENCE = b"immutable-btcusdt-market-fixture"


def _evidence(index: int, known_at: datetime) -> SecurityMasterEvidenceV1:
    return SecurityMasterEvidenceV1(
        schema_version="security-master-evidence-v1",
        reference=EvidenceReference(
            evidence_id=UUID(f"a0000000-0000-4000-8000-{index:012d}"),
            source=EvidenceSource.FILING,
            locator=EvidenceLocator(
                kind=EvidenceLocatorKind.HTTPS,
                authority="example.invalid",
                path=("security-master", f"record-{index}"),
            ),
            observed_at=known_at - timedelta(minutes=2),
            schema_version="source-record-v1",
        ),
        fetched_at=known_at - timedelta(minutes=1),
        known_at=known_at,
        content_sha256=f"{index:x}" * 64,
        media_type="application/json",
        source_revision=f"r{index}",
        normalization_version="security-master-normalization-v1",
    )


def _persisted(
    index: int,
    kind: SecurityMasterIdentityKind,
    subject_id: UUID,
    payload: object,
    *,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
) -> PersistedSecurityMasterRevisionV1:
    known_at = CUTOFF - timedelta(hours=2, minutes=20 - index)
    revision = SecurityMasterRevisionV1(
        schema_version="security-master-revision-v1",
        revision_id=UUID(f"90000000-0000-4000-8000-{index:012d}"),
        fact_id=UUID(f"80000000-0000-4000-8000-{index:012d}"),
        subject_id=subject_id,
        subject_kind=kind,
        revision_ordinal=1,
        operation=SecurityMasterOperation.ASSERT,
        effective_from=effective_from or OPEN - timedelta(days=1),
        effective_to=effective_to,
        known_at=known_at,
        supersedes_revision_id=None,
        evidence=(_evidence(index, known_at),),
        payload=payload,
    )
    return PersistedSecurityMasterRevisionV1(
        revision=revision,
        recorded_at=known_at + timedelta(minutes=30),
    )


def _security_master() -> tuple[PersistedSecurityMasterRevisionV1, ...]:
    return (
        _persisted(
            1,
            SecurityMasterIdentityKind.ISSUER,
            BTC_ISSUER_ID,
            IssuerPayloadV1(
                issuer_id=BTC_ISSUER_ID,
                legal_name="Bitcoin Network",
                jurisdiction="GLOBAL",
            ),
        ),
        _persisted(
            2,
            SecurityMasterIdentityKind.ISSUER,
            USDT_ISSUER_ID,
            IssuerPayloadV1(
                issuer_id=USDT_ISSUER_ID,
                legal_name="Tether Holdings",
                jurisdiction="GLOBAL",
            ),
        ),
        _persisted(
            3,
            SecurityMasterIdentityKind.ASSET,
            BTC_ASSET_ID,
            AssetPayloadV1(
                asset_id=BTC_ASSET_ID,
                code="BTC",
                asset_kind=AssetKind.CRYPTO,
                issuer_id=BTC_ISSUER_ID,
            ),
        ),
        _persisted(
            4,
            SecurityMasterIdentityKind.ASSET,
            USDT_ASSET_ID,
            AssetPayloadV1(
                asset_id=USDT_ASSET_ID,
                code="USDT",
                asset_kind=AssetKind.CRYPTO,
                issuer_id=USDT_ISSUER_ID,
            ),
        ),
        _persisted(
            5,
            SecurityMasterIdentityKind.SECURITY,
            SECURITY_ID,
            SecurityPayloadV1(
                security_id=SECURITY_ID,
                product_type=ProductType.CRYPTO_SPOT,
                primary_asset_id=BTC_ASSET_ID,
            ),
        ),
        _persisted(
            6,
            SecurityMasterIdentityKind.VENUE,
            VENUE_ID,
            VenuePayloadV1(
                venue_id=VENUE_ID,
                code="BINANCE",
                mic="XBIN",
                timezone="UTC",
            ),
        ),
        _persisted(
            7,
            SecurityMasterIdentityKind.LISTING,
            LISTING_ID,
            ListingPayloadV1(
                listing_id=LISTING_ID,
                security_id=SECURITY_ID,
                venue_id=VENUE_ID,
                quote_asset_id=USDT_ASSET_ID,
                session_calendar="CONTINUOUS",
                tick_size="0.01",
                size_increment="0.000001",
                minimum_quantity="0.000001",
                maximum_quantity="1000",
                minimum_notional="10",
                maximum_notional="10000000",
            ),
        ),
        _persisted(
            8,
            SecurityMasterIdentityKind.SYMBOL_MAPPING,
            MAPPING_ID,
            SymbolMappingPayloadV1(
                mapping_id=MAPPING_ID,
                provider="BINANCE",
                raw_symbol="BTCUSDT",
                canonical_symbol="BTCUSDT",
                listing_id=LISTING_ID,
            ),
        ),
    )


def _repository(
    values: tuple[PersistedSecurityMasterRevisionV1, ...],
) -> PostgresSecurityMasterRepository:
    rows: list[dict[str, object]] = []
    for persisted in values:
        revision = persisted.revision
        payload = revision.payload
        mapping = payload if isinstance(payload, SymbolMappingPayloadV1) else None
        related_security_id = (
            payload.security_id if isinstance(payload, SplitPayloadV1) else None
        )
        rows.append(
            {
                "canonical_revision_text": revision.canonical_revision_bytes.decode(),
                "revision_digest": revision.digest,
                "revision_id": revision.revision_id,
                "fact_id": revision.fact_id,
                "subject_id": revision.subject_id,
                "subject_kind": revision.subject_kind.value,
                "revision_ordinal": revision.revision_ordinal,
                "operation": revision.operation.value,
                "effective_from": revision.effective_from,
                "effective_to": revision.effective_to,
                "known_at": revision.known_at,
                "recorded_at": persisted.recorded_at,
                "supersedes_revision_id": revision.supersedes_revision_id,
                "lookup_provider": mapping.provider if mapping is not None else None,
                "lookup_symbol": mapping.raw_symbol if mapping is not None else None,
                "related_security_id": related_security_id,
            }
        )
    rows.sort(key=lambda row: (row["recorded_at"], row["revision_id"]))
    return PostgresSecurityMasterRepository(
        Pool(Connection([{"transaction_isolation": "read committed"}, rows]))
    )


def _market_snapshot(
    *,
    first_close: Decimal = Decimal("101.00"),
    known_at: datetime = OPEN + timedelta(minutes=5),
    three_candles: bool = False,
) -> MarketSnapshot:
    instrument = InstrumentId("BTCUSDT", ProductType.CRYPTO_SPOT, "BINANCE")
    return MarketSnapshot(
        instrument=instrument,
        timeframe=MarketTimeframe.ONE_MINUTE,
        candles=(
            MarketCandle(
                instrument=instrument,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=OPEN,
                open=Decimal("100.00"),
                high=Decimal("102.00"),
                low=Decimal("99.00"),
                close=first_close,
                volume=Decimal("2.000000"),
            ),
            MarketCandle(
                instrument=instrument,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=OPEN + timedelta(minutes=1),
                open=Decimal("101.00"),
                high=Decimal("103.00"),
                low=Decimal("98.00"),
                close=Decimal("99.00"),
                volume=Decimal("3.000000"),
            ),
        )
        + (
            (
                MarketCandle(
                    instrument=instrument,
                    timeframe=MarketTimeframe.ONE_MINUTE,
                    open_time=OPEN + timedelta(minutes=2),
                    open=Decimal("99.00"),
                    high=Decimal("101.00"),
                    low=Decimal("98.00"),
                    close=Decimal("100.00"),
                    volume=Decimal("4.000000"),
                ),
            )
            if three_candles
            else ()
        ),
        provenance=MarketDataProvenance(
            provider="deterministic-fixture-v1",
            observed_at=OPEN + timedelta(minutes=3),
            fetched_at=OPEN + timedelta(minutes=4),
            raw_evidence_sha256=hashlib.sha256(RAW_EVIDENCE).hexdigest(),
            schema_version="market-data-v1",
            normalization_version="market-normalization-v1",
        ),
        known_at=known_at,
        schema_version="market-snapshot-v1",
        normalization_version="market-normalization-v1",
    )


def _schedule(
    *, second_effective_at: str = "2026-08-05T12:02:00Z"
) -> P1TargetScheduleV1:
    return P1TargetScheduleV1.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": "nautilus-p1-target-schedule-v1",
                "targets": [
                    {
                        "effective_at": "2026-08-05T12:01:00Z",
                        "positions": [
                            {
                                "instrument": {
                                    "product_type": "crypto_spot",
                                    "symbol": "BTCUSDT",
                                    "venue": "BINANCE",
                                },
                                "target_weight": "1",
                            }
                        ],
                        "schema_version": "1.0.0",
                        "source_signal_ids": [
                            "22222222-2222-4222-8222-222222222222"
                        ],
                        "target_id": "11111111-1111-4111-8111-111111111111",
                    },
                    {
                        "effective_at": second_effective_at,
                        "positions": [
                            {
                                "instrument": {
                                    "product_type": "crypto_spot",
                                    "symbol": "BTCUSDT",
                                    "venue": "BINANCE",
                                },
                                "target_weight": "0",
                            }
                        ],
                        "schema_version": "1.0.0",
                        "source_signal_ids": [
                            "33333333-3333-4333-8333-333333333333"
                        ],
                        "target_id": "44444444-4444-4444-8444-444444444444",
                    },
                ],
            }
        )
    )


def _workspace(parent: Path) -> CatalogWorkspaceV1:
    parent.chmod(0o700)
    return CatalogWorkspaceV1.create(parent)


def _materialized_inputs(
    tmp_path: Path,
    *,
    revisions: tuple[PersistedSecurityMasterRevisionV1, ...] | None = None,
    market: MarketSnapshot | None = None,
):
    return (
        materialize_security_master_snapshot(
            _repository(revisions or _security_master()), CUTOFF, _workspace(tmp_path)
        ),
        materialize_fixture_catalog(
            market or _market_snapshot(),
            RAW_EVIDENCE,
            workspace=_workspace(tmp_path),
            importer_version="fixture-catalog-v1",
        ),
    )


def test_projects_verified_pit_and_market_inputs_into_exact_p1_artifacts(
    tmp_path: Path,
) -> None:
    security_snapshot, market_dataset = _materialized_inputs(tmp_path)

    projected = security_master.project_p1_paper_inputs(
        security_snapshot, market_dataset, _schedule()
    )

    configuration = parse_canonical_artifact(
        P1EngineConfigurationV1, projected.engine_configuration
    )
    catalog = parse_canonical_artifact(
        P1InstrumentCatalogV1, projected.instrument_catalog
    )
    schedule = parse_canonical_artifact(
        P1TargetScheduleV1, projected.target_schedule
    )
    manifest = parse_canonical_artifact(
        P1MarketDataManifestV1, projected.market_data_manifest
    )
    assert configuration.network_access is False
    assert catalog.model_dump(mode="json") == {
        "base_currency": "BTC",
        "instrument_id": "BTCUSDT.BINANCE",
        "min_notional": "10",
        "min_quantity": "0.000001",
        "price_precision": 2,
        "product_type": "crypto_spot",
        "provenance_sha256": security_snapshot.manifest.snapshot_digest,
        "quote_currency": "USDT",
        "schema_version": "nautilus-p1-instrument-catalog-v1",
        "size_precision": 6,
        "step_size": "0.000001",
        "symbol": "BTCUSDT",
        "tick_size": "0.01",
        "venue": "BINANCE",
    }
    assert schedule == _schedule()
    assert projected.market_data == (
        b'{"ask":"101","bid":"100","close":"101","event_time":"2026-08-05T12:01:00Z",'
        b'"high":"102","low":"99","open":"100","quote_time":"2026-08-05T12:01:00Z",'
        b'"sequence":1,"volume":"2"}\n'
        b'{"ask":"101","bid":"99","close":"99","event_time":"2026-08-05T12:02:00Z",'
        b'"high":"103","low":"98","open":"101","quote_time":"2026-08-05T12:02:00Z",'
        b'"sequence":2,"volume":"3"}\n'
    )
    assert manifest.data_sha256 == hashlib.sha256(projected.market_data).hexdigest()
    assert manifest.catalog_sha256 == hashlib.sha256(
        projected.instrument_catalog
    ).hexdigest()
    assert manifest.first_timestamp == OPEN + timedelta(minutes=1)
    assert manifest.last_timestamp == OPEN + timedelta(minutes=2)
    assert projected.instrument_definition.instrument_id.canonical == (
        "crypto_spot:BINANCE:BTCUSDT"
    )
    assert projected.receipt.security_master_snapshot_digest == (
        security_snapshot.manifest.snapshot_digest
    )
    assert projected.receipt.market_dataset_content_digest == (
        market_dataset.manifest.content_digest
    )
    assert tuple(item.component for item in projected.receipt.selected_revisions) == (
        "mapping",
        "listing",
        "security",
        "venue",
        "base_asset",
        "base_issuer",
        "quote_asset",
        "quote_issuer",
    )
    assert all(item.recorded_at <= CUTOFF for item in projected.receipt.selected_revisions)
    assert projected.receipt.paper_local_only is True
    assert projected.receipt.network_enabled is False
    assert projected.receipt.live_authorized is False
    assert projected.receipt.production_authorized is False
    assert projected.receipt.broker_access_authorized is False
    assert projected.receipt.database_runtime_authorized is False
    assert projected.receipt_bytes == canonical_json_bytes(projected.receipt) + b"\n"
    assert projected.receipt.artifacts.market_data_sha256 == hashlib.sha256(
        projected.market_data
    ).hexdigest()


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("stale-market", "market dataset"),
        ("off-grid-market", "market price"),
        ("off-grid-target", "target schedule"),
        ("wrong-target-instrument", "target schedule"),
        ("ambiguous-mapping", "cannot be projected"),
        ("missing-issuer", "cannot be projected"),
        ("corporate-action", "corporate action"),
    ),
)
def test_rejects_stale_ambiguous_or_unrepresentable_inputs(
    tmp_path: Path, case: str, message: str
) -> None:
    revisions = _security_master()
    market = _market_snapshot()
    schedule = _schedule()
    if case == "stale-market":
        market = _market_snapshot(known_at=CUTOFF + timedelta(minutes=1))
    elif case == "off-grid-market":
        market = _market_snapshot(first_close=Decimal("101.005"))
    elif case == "off-grid-target":
        schedule = _schedule(second_effective_at="2026-08-05T12:01:30Z")
    elif case == "wrong-target-instrument":
        first = schedule.targets[0]
        position = first.positions[0]
        forged_position = position.model_copy(
            update={
                "instrument": position.instrument.model_copy(
                    update={"symbol": "ETHUSDT"}
                )
            }
        )
        schedule = schedule.model_copy(
            update={
                "targets": (
                    first.model_copy(update={"positions": (forged_position,)}),
                    schedule.targets[1],
                )
            }
        )
    elif case == "ambiguous-mapping":
        alternate_id = UUID("60000000-0000-4000-8000-000000000002")
        revisions += (
            _persisted(
                9,
                SecurityMasterIdentityKind.SYMBOL_MAPPING,
                alternate_id,
                SymbolMappingPayloadV1(
                    mapping_id=alternate_id,
                    provider="BINANCE",
                    raw_symbol="BTCUSDT",
                    canonical_symbol="BTCUSDT",
                    listing_id=LISTING_ID,
                ),
            ),
        )
    elif case == "missing-issuer":
        revisions = revisions[1:]
    elif case == "corporate-action":
        action_id = UUID("70000000-0000-4000-8000-000000000001")
        revisions += (
            _persisted(
                9,
                SecurityMasterIdentityKind.CORPORATE_ACTION,
                action_id,
                SplitPayloadV1(
                    action_id=action_id,
                    security_id=SECURITY_ID,
                    action_type=CorporateActionType.SPLIT,
                    new_units="2",
                    old_units="1",
                ),
                effective_from=OPEN + timedelta(minutes=1),
            ),
        )
    security_snapshot, market_dataset = _materialized_inputs(
        tmp_path, revisions=revisions, market=market
    )

    with pytest.raises(security_master.P1ProjectionError, match=message):
        security_master.project_p1_paper_inputs(
            security_snapshot, market_dataset, schedule
        )


def test_rejects_identity_ambiguity_active_only_at_an_interior_close(
    tmp_path: Path,
) -> None:
    revisions = _security_master()
    listing = revisions[6].revision.payload
    assert isinstance(listing, ListingPayloadV1)
    revisions += (
        _persisted(
            9,
            SecurityMasterIdentityKind.LISTING,
            LISTING_ID,
            listing,
            effective_from=OPEN + timedelta(minutes=2),
            effective_to=OPEN + timedelta(minutes=3),
        ),
    )
    security_snapshot, market_dataset = _materialized_inputs(
        tmp_path,
        revisions=revisions,
        market=_market_snapshot(three_candles=True),
    )

    with pytest.raises(security_master.P1ProjectionError):
        security_master.project_p1_paper_inputs(
            security_snapshot, market_dataset, _schedule()
        )


def test_rejects_a_different_listing_graph_at_the_last_close(tmp_path: Path) -> None:
    revisions = _security_master()
    listing = revisions[6].revision.payload
    assert isinstance(listing, ListingPayloadV1)
    old = _persisted(
        7,
        SecurityMasterIdentityKind.LISTING,
        LISTING_ID,
        listing,
        effective_to=OPEN + timedelta(minutes=2),
    )
    replacement = _persisted(
        9,
        SecurityMasterIdentityKind.LISTING,
        LISTING_ID,
        listing,
        effective_from=OPEN + timedelta(minutes=2),
    )
    revisions = (*revisions[:6], old, *revisions[7:], replacement)
    security_snapshot, market_dataset = _materialized_inputs(
        tmp_path, revisions=revisions
    )

    with pytest.raises(security_master.P1ProjectionError, match="graph changes"):
        security_master.project_p1_paper_inputs(
            security_snapshot, market_dataset, _schedule()
        )


def test_propagates_tamper_rejection_from_the_market_catalog_verifier(
    tmp_path: Path,
) -> None:
    security_snapshot, market_dataset = _materialized_inputs(tmp_path)
    from packages.data_catalog import parquet as catalog_parquet

    parquet = (
        catalog_parquet._workspace_state(market_dataset.workspace).path
        / market_dataset.parquet_name
    )
    parquet.write_bytes(parquet.read_bytes() + b"tamper")

    with pytest.raises(security_master.P1ProjectionError) as caught:
        security_master.project_p1_paper_inputs(
            security_snapshot, market_dataset, _schedule()
        )
    assert isinstance(caught.value.__cause__, CatalogMaterializationError)

"""Approval-bound PostgreSQL 16 proof for P2 security-master PIT semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from uuid import UUID

import psycopg
import pytest

from packages.domain import (
    EvidenceLocator,
    EvidenceLocatorKind,
    EvidenceReference,
    EvidenceSource,
    ProductType,
)
from packages.security_master import (
    AssetKind,
    AssetPayloadV1,
    IssuerPayloadV1,
    ListingPayloadV1,
    SecurityMasterEvidenceV1,
    SecurityMasterIdentityKind,
    SecurityMasterOperation,
    SecurityMasterRevisionV1,
    SecurityPayloadV1,
    SymbolMappingPayloadV1,
    VenuePayloadV1,
)
from tests.jobs._postgres import disposable_database, upgrade_to_head


pytestmark = pytest.mark.runtime_postgres

OPERATION_ID = "p2-security-master-runtime-green-v1"
HEAD = "0019_p2_security_master"
KNOWN = datetime(2026, 8, 30, 12, tzinfo=UTC)
FACT_ID = UUID("81000000-0000-4000-8000-000000000001")
ISSUER_ID = UUID("11000000-0000-4000-8000-000000000001")
BASE_ASSET_ID = UUID("21000000-0000-4000-8000-000000000001")
QUOTE_ASSET_ID = UUID("21000000-0000-4000-8000-000000000002")
SECURITY_ID = UUID("31000000-0000-4000-8000-000000000001")
VENUE_ID = UUID("41000000-0000-4000-8000-000000000001")
MAPPING_ID = UUID("61000000-0000-4000-8000-000000000001")
LISTING_ID = UUID("51000000-0000-4000-8000-000000000001")


def _evidence(index: int, known_at: datetime) -> SecurityMasterEvidenceV1:
    return SecurityMasterEvidenceV1(
        schema_version="security-master-evidence-v1",
        reference=EvidenceReference(
            evidence_id=UUID(f"a1000000-0000-4000-8000-{index:012d}"),
            source=EvidenceSource.FILING,
            locator=EvidenceLocator(
                kind=EvidenceLocatorKind.HTTPS,
                authority="example.invalid",
                path=("p2-runtime", f"record-{index}"),
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


def _revision(
    ordinal: int,
    *,
    revision_id: UUID,
    predecessor: SecurityMasterRevisionV1 | None = None,
) -> SecurityMasterRevisionV1:
    known_at = KNOWN + timedelta(minutes=ordinal - 1)
    return SecurityMasterRevisionV1(
        schema_version="security-master-revision-v1",
        revision_id=revision_id,
        fact_id=FACT_ID,
        subject_id=MAPPING_ID,
        subject_kind=SecurityMasterIdentityKind.SYMBOL_MAPPING,
        revision_ordinal=ordinal,
        operation=SecurityMasterOperation.ASSERT,
        effective_from=KNOWN - timedelta(days=1),
        effective_to=None,
        known_at=known_at,
        supersedes_revision_id=None if predecessor is None else predecessor.revision_id,
        evidence=(_evidence(ordinal + 6, known_at),),
        payload=SymbolMappingPayloadV1(
            mapping_id=MAPPING_ID,
            provider="BINANCE",
            raw_symbol="BTCUSDT",
            canonical_symbol="BTCUSDT" if ordinal == 1 else "XBTUSDT",
            listing_id=LISTING_ID,
        ),
    )


def _definition_revisions() -> tuple[SecurityMasterRevisionV1, ...]:
    definitions = (
        (
            SecurityMasterIdentityKind.ISSUER,
            ISSUER_ID,
            IssuerPayloadV1(
                issuer_id=ISSUER_ID,
                legal_name="Bitcoin Network",
                jurisdiction="GLOBAL",
            ),
        ),
        (
            SecurityMasterIdentityKind.ASSET,
            BASE_ASSET_ID,
            AssetPayloadV1(
                asset_id=BASE_ASSET_ID,
                code="BTC",
                asset_kind=AssetKind.CRYPTO,
                issuer_id=ISSUER_ID,
            ),
        ),
        (
            SecurityMasterIdentityKind.ASSET,
            QUOTE_ASSET_ID,
            AssetPayloadV1(
                asset_id=QUOTE_ASSET_ID,
                code="USDT",
                asset_kind=AssetKind.CRYPTO,
                issuer_id=ISSUER_ID,
            ),
        ),
        (
            SecurityMasterIdentityKind.SECURITY,
            SECURITY_ID,
            SecurityPayloadV1(
                security_id=SECURITY_ID,
                product_type=ProductType.CRYPTO_SPOT,
                primary_asset_id=BASE_ASSET_ID,
            ),
        ),
        (
            SecurityMasterIdentityKind.VENUE,
            VENUE_ID,
            VenuePayloadV1(
                venue_id=VENUE_ID,
                code="BINANCE",
                mic="XBIN",
                timezone="UTC",
            ),
        ),
        (
            SecurityMasterIdentityKind.LISTING,
            LISTING_ID,
            ListingPayloadV1(
                listing_id=LISTING_ID,
                security_id=SECURITY_ID,
                venue_id=VENUE_ID,
                quote_asset_id=QUOTE_ASSET_ID,
                session_calendar="CONTINUOUS",
                tick_size="0.01",
                size_increment="0.000001",
                minimum_quantity="0.000001",
                maximum_quantity="1000",
                minimum_notional="10",
                maximum_notional="10000000",
            ),
        ),
    )
    return tuple(
        SecurityMasterRevisionV1(
            schema_version="security-master-revision-v1",
            revision_id=UUID(f"92000000-0000-4000-8000-{index:012d}"),
            fact_id=UUID(f"81000000-0000-4000-8000-{index + 1:012d}"),
            subject_id=subject_id,
            subject_kind=kind,
            revision_ordinal=1,
            operation=SecurityMasterOperation.ASSERT,
            effective_from=KNOWN - timedelta(days=1),
            effective_to=None,
            known_at=(known_at := KNOWN - timedelta(minutes=7 - index)),
            supersedes_revision_id=None,
            evidence=(_evidence(index, known_at),),
            payload=payload,
        )
        for index, (kind, subject_id, payload) in enumerate(definitions, start=1)
    )


@pytest.mark.runtime_postgres
def test_runtime_migration_and_pit_correction_are_fail_closed() -> None:
    if (
        os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES"
        or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE")
        != "DISPOSABLE_PG_GREEN"
    ):
        pytest.skip("exact disposable PostgreSQL GREEN authority is not present")

    with disposable_database(operation_id=OPERATION_ID, planned=True) as settings:
        upgrade_to_head(settings)
        root = _revision(
            1, revision_id=UUID("91000000-0000-4000-8000-000000000001")
        )
        correction = _revision(
            2,
            revision_id=UUID("91000000-0000-4000-8000-000000000002"),
            predecessor=root,
        )
        assert root.revision_id not in {
            definition.revision_id for definition in _definition_revisions()
        }
        with psycopg.connect(settings.conninfo()) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD
            with pytest.raises(psycopg.ProgrammingError, match="SYMBOL_MAPPING relation is invalid"):
                connection.execute(
                    "SELECT inserted FROM public.append_security_master_revision(%s)",
                    (root.canonical_revision_bytes.decode("utf-8"),),
                )
            connection.rollback()
            for definition in _definition_revisions():
                assert connection.execute(
                    "SELECT inserted FROM public.append_security_master_revision(%s)",
                    (definition.canonical_revision_bytes.decode("utf-8"),),
                ).fetchone()[0] is True
            connection.commit()
            assert connection.execute(
                "SELECT inserted FROM public.append_security_master_revision(%s)",
                (root.canonical_revision_bytes.decode("utf-8"),),
            ).fetchone()[0] is True
            connection.commit()
            t2 = connection.execute(
                "SELECT recorded_at FROM public.security_master_revisions "
                "WHERE revision_id = %s",
                (root.revision_id,),
            ).fetchone()[0]
            assert connection.execute(
                "SELECT inserted FROM public.append_security_master_revision(%s)",
                (correction.canonical_revision_bytes.decode("utf-8"),),
            ).fetchone()[0] is True
            connection.commit()

            at_t2 = connection.execute(
                "SELECT revision_id FROM public.security_master_revisions "
                "WHERE fact_id = %s AND recorded_at <= %s ORDER BY revision_ordinal",
                (FACT_ID, t2),
            ).fetchall()
            current = connection.execute(
                "SELECT revision_id FROM public.security_master_revisions "
                "WHERE fact_id = %s ORDER BY revision_ordinal",
                (FACT_ID,),
            ).fetchall()

        assert at_t2 == [(root.revision_id,)]
        assert current == [(root.revision_id,), (correction.revision_id,)]

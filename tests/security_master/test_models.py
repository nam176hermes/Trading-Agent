from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import localcontext
import json
from uuid import UUID

import pytest
from pydantic import ValidationError

import packages.security_master as security_master

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
    CashDividendPayloadV1,
    CorporateActionType,
    DelistingPayloadV1,
    IssuerPayloadV1,
    ListingPayloadV1,
    SecurityMasterEvidenceV1,
    SecurityMasterIdentityKind,
    SecurityMasterOperation,
    SecurityMasterRevisionV1,
    SecurityPayloadV1,
    SplitPayloadV1,
    SymbolChangePayloadV1,
    SymbolMappingPayloadV1,
    VenuePayloadV1,
)


KNOWN = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
ISSUER_ID = UUID("10000000-0000-4000-8000-000000000001")
ASSET_ID = UUID("20000000-0000-4000-8000-000000000001")
QUOTE_ASSET_ID = UUID("20000000-0000-4000-8000-000000000002")
SECURITY_ID = UUID("30000000-0000-4000-8000-000000000001")
VENUE_ID = UUID("40000000-0000-4000-8000-000000000001")
LISTING_ID = UUID("50000000-0000-4000-8000-000000000001")
MAPPING_ID = UUID("60000000-0000-4000-8000-000000000001")
ACTION_ID = UUID("70000000-0000-4000-8000-000000000001")
FACT_ID = UUID("80000000-0000-4000-8000-000000000001")
REVISION_ID = UUID("90000000-0000-4000-8000-000000000001")


def evidence(
    *,
    evidence_id: str = "a0000000-0000-4000-8000-000000000001",
    known_at: datetime = KNOWN,
) -> SecurityMasterEvidenceV1:
    return SecurityMasterEvidenceV1(
        schema_version="security-master-evidence-v1",
        reference=EvidenceReference(
            evidence_id=UUID(evidence_id),
            source=EvidenceSource.FILING,
            locator=EvidenceLocator(
                kind=EvidenceLocatorKind.HTTPS,
                authority="example.invalid",
                path=("security-master", "record-1"),
            ),
            observed_at=known_at - timedelta(minutes=2),
            schema_version="source-record-v1",
        ),
        fetched_at=known_at - timedelta(minutes=1),
        known_at=known_at,
        content_sha256="a" * 64,
        media_type="application/json",
        source_revision="r1",
        normalization_version="security-master-normalization-v1",
    )


def mapping_payload() -> SymbolMappingPayloadV1:
    return SymbolMappingPayloadV1(
        mapping_id=MAPPING_ID,
        provider="BINANCE",
        raw_symbol="BTCUSDT",
        canonical_symbol="BTCUSDT",
        listing_id=LISTING_ID,
    )


def revision(**updates: object) -> SecurityMasterRevisionV1:
    values: dict[str, object] = {
        "schema_version": "security-master-revision-v1",
        "revision_id": REVISION_ID,
        "fact_id": FACT_ID,
        "subject_id": MAPPING_ID,
        "subject_kind": SecurityMasterIdentityKind.SYMBOL_MAPPING,
        "revision_ordinal": 1,
        "operation": SecurityMasterOperation.ASSERT,
        "effective_from": KNOWN - timedelta(days=1),
        "effective_to": None,
        "known_at": KNOWN,
        "supersedes_revision_id": None,
        "evidence": (evidence(),),
        "payload": mapping_payload(),
    }
    values.update(updates)
    return SecurityMasterRevisionV1(**values)


def test_revision_has_exact_canonical_envelope_and_digest() -> None:
    document = revision()
    decoded = json.loads(document.canonical_revision_bytes)

    assert tuple(sorted(decoded)) == tuple(
        sorted(
            (
                "schema_version",
                "revision_id",
                "fact_id",
                "subject_id",
                "subject_kind",
                "revision_ordinal",
                "operation",
                "effective_from",
                "effective_to",
                "known_at",
                "supersedes_revision_id",
                "evidence",
                "payload",
            )
        )
    )
    assert decoded["known_at"] == "2026-08-30T12:00:00Z"
    assert decoded["payload"]["provider"] == "BINANCE"
    assert len(document.digest) == 64
    assert SecurityMasterRevisionV1.model_validate_json(document.canonical_revision_bytes) == document


def test_evidence_is_sorted_unique_and_closes_known_at() -> None:
    first = evidence(
        evidence_id="b0000000-0000-4000-8000-000000000001",
        known_at=KNOWN - timedelta(minutes=1),
    )
    second = evidence(
        evidence_id="a0000000-0000-4000-8000-000000000001",
        known_at=KNOWN,
    )
    document = revision(evidence=(first, second))

    assert tuple(str(item.reference.evidence_id) for item in document.evidence) == (
        "a0000000-0000-4000-8000-000000000001",
        "b0000000-0000-4000-8000-000000000001",
    )
    with pytest.raises(ValidationError, match="maximum evidence known_at"):
        revision(known_at=KNOWN + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="unique"):
        revision(evidence=(second, second))


def test_revision_rejects_temporal_lineage_and_payload_mismatches() -> None:
    with pytest.raises(ValidationError, match="effective_to"):
        revision(effective_to=KNOWN - timedelta(days=1))
    with pytest.raises(ValidationError, match="root"):
        revision(revision_ordinal=2)
    with pytest.raises(ValidationError, match="subject_id"):
        revision(subject_id=SECURITY_ID)
    with pytest.raises(ValidationError, match="payload"):
        revision(payload=None)
    with pytest.raises(ValidationError):
        revision(known_at=datetime(2026, 8, 30, 12, 0))


def test_retraction_is_child_only_and_has_no_payload() -> None:
    retracted = revision(
        revision_id=UUID("90000000-0000-4000-8000-000000000002"),
        revision_ordinal=2,
        operation=SecurityMasterOperation.RETRACT,
        supersedes_revision_id=REVISION_ID,
        known_at=KNOWN + timedelta(minutes=1),
        evidence=(evidence(known_at=KNOWN + timedelta(minutes=1)),),
        payload=None,
    )

    assert retracted.payload is None
    with pytest.raises(ValidationError, match="root"):
        revision(operation=SecurityMasterOperation.RETRACT, payload=None)


def test_all_definition_payloads_are_closed_and_subject_bound() -> None:
    payloads = (
        (SecurityMasterIdentityKind.ISSUER, ISSUER_ID, IssuerPayloadV1(issuer_id=ISSUER_ID, legal_name="Bitcoin Network", jurisdiction="GLOBAL")),
        (SecurityMasterIdentityKind.ASSET, ASSET_ID, AssetPayloadV1(asset_id=ASSET_ID, code="BTC", asset_kind=AssetKind.CRYPTO, issuer_id=ISSUER_ID)),
        (SecurityMasterIdentityKind.SECURITY, SECURITY_ID, SecurityPayloadV1(security_id=SECURITY_ID, product_type=ProductType.CRYPTO_SPOT, primary_asset_id=ASSET_ID)),
        (SecurityMasterIdentityKind.VENUE, VENUE_ID, VenuePayloadV1(venue_id=VENUE_ID, code="BINANCE", mic="XBIN", timezone="UTC")),
        (
            SecurityMasterIdentityKind.LISTING,
            LISTING_ID,
            ListingPayloadV1(
                listing_id=LISTING_ID,
                security_id=SECURITY_ID,
                venue_id=VENUE_ID,
                quote_asset_id=QUOTE_ASSET_ID,
                session_calendar="24X7",
                tick_size="0.01",
                size_increment="0.00001",
                minimum_quantity="0.00001",
                maximum_quantity="1000",
                minimum_notional="1",
                maximum_notional="10000000",
            ),
        ),
    )
    for index, (kind, subject_id, payload) in enumerate(payloads, start=1):
        assert revision(
            revision_id=UUID(f"90000000-0000-4000-8000-{index:012d}"),
            subject_kind=kind,
            subject_id=subject_id,
            payload=payload,
        ).payload == payload

    with pytest.raises(ValidationError):
        ListingPayloadV1(
            listing_id=LISTING_ID,
            security_id=SECURITY_ID,
            venue_id=VENUE_ID,
            quote_asset_id=QUOTE_ASSET_ID,
            session_calendar="24X7",
            tick_size=0.01,
            size_increment="0.00001",
            minimum_quantity="0.00001",
            maximum_quantity="1000",
            minimum_notional="1",
            maximum_notional="10000000",
        )


def test_corporate_action_union_covers_raw_p2_facts_only() -> None:
    payloads = (
        SplitPayloadV1(
            action_id=ACTION_ID,
            security_id=SECURITY_ID,
            action_type=CorporateActionType.SPLIT,
            new_units="2",
            old_units="1",
        ),
        CashDividendPayloadV1(
            action_id=ACTION_ID,
            security_id=SECURITY_ID,
            action_type=CorporateActionType.CASH_DIVIDEND,
            amount="0.25",
            currency_asset_id=QUOTE_ASSET_ID,
        ),
        SymbolChangePayloadV1(
            action_id=ACTION_ID,
            security_id=SECURITY_ID,
            action_type=CorporateActionType.SYMBOL_CHANGE,
            old_mapping_id=MAPPING_ID,
            new_mapping_id=UUID("60000000-0000-4000-8000-000000000002"),
        ),
        DelistingPayloadV1(
            action_id=ACTION_ID,
            security_id=SECURITY_ID,
            action_type=CorporateActionType.DELISTING,
            listing_id=LISTING_ID,
        ),
    )
    for index, payload in enumerate(payloads, start=1):
        document = revision(
            revision_id=UUID(f"91000000-0000-4000-8000-{index:012d}"),
            subject_id=ACTION_ID,
            subject_kind=SecurityMasterIdentityKind.CORPORATE_ACTION,
            effective_to=None,
            payload=payload,
        )
        assert document.payload == payload

    with pytest.raises(ValidationError, match="effective_to"):
        revision(
            subject_id=ACTION_ID,
            subject_kind=SecurityMasterIdentityKind.CORPORATE_ACTION,
            effective_to=KNOWN + timedelta(days=1),
            payload=payloads[0],
        )


def test_positive_decimal_canonicality_does_not_depend_on_ambient_context() -> None:
    with localcontext() as context:
        context.prec = 2
        payload = SplitPayloadV1(
            action_id=ACTION_ID,
            security_id=SECURITY_ID,
            action_type=CorporateActionType.SPLIT,
            new_units="12345678901234567890.123456789",
            old_units="1",
        )

    assert payload.new_units == "12345678901234567890.123456789"


def test_evidence_reference_schema_version_is_bounded_and_canonical() -> None:
    invalid = evidence().model_dump()
    invalid["reference"] = evidence().reference.model_copy(
        update={"schema_version": "x" * 65}
    )

    with pytest.raises(ValidationError, match="schema_version"):
        SecurityMasterEvidenceV1.model_validate(invalid)


def test_persisted_revision_uses_a_db_owned_visibility_time() -> None:
    persisted_type = getattr(
        security_master, "PersistedSecurityMasterRevisionV1", None
    )
    assert persisted_type is not None, "persisted revision envelope is missing"
    document = revision()
    recorded_at = KNOWN + timedelta(hours=1)

    persisted = persisted_type(revision=document, recorded_at=recorded_at)

    assert persisted.revision == document
    assert persisted.recorded_at == recorded_at
    with pytest.raises(ValidationError, match="recorded_at"):
        persisted_type(
            revision=document,
            recorded_at=document.known_at - timedelta(microseconds=1),
        )
    with pytest.raises(ValidationError, match="UTC"):
        persisted_type(revision=document, recorded_at=recorded_at.replace(tzinfo=None))


@pytest.mark.parametrize("component", ("accounting", "secretary", "tokenization"))
def test_evidence_locator_safe_exception_words_remain_parseable(component: str) -> None:
    item = evidence().model_dump()
    item["reference"]["locator"]["path"] = (component,)

    assert SecurityMasterEvidenceV1.model_validate(item).reference.locator.path == (
        component,
    )

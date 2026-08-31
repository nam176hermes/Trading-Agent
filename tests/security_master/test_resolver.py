from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest

from packages.security_master import (
    CorporateActionType,
    IssuerPayloadV1,
    PersistedSecurityMasterRevisionV1,
    SecurityMasterAmbiguityError,
    SecurityMasterIdentityKind,
    SecurityMasterIntegrityError,
    SecurityMasterNotFoundError,
    SecurityMasterOperation,
    SecurityMasterResolver,
    SplitPayloadV1,
)

from .test_models import (
    ACTION_ID,
    FACT_ID,
    KNOWN,
    MAPPING_ID,
    REVISION_ID,
    SECURITY_ID,
    evidence,
    mapping_payload,
    revision,
)


def child(*, ordinal: int, minutes: int, operation: SecurityMasterOperation = SecurityMasterOperation.ASSERT):
    return revision(
        revision_id=UUID(f"90000000-0000-4000-8000-{ordinal:012d}"),
        revision_ordinal=ordinal,
        operation=operation,
        supersedes_revision_id=(
            REVISION_ID
            if ordinal == 2
            else UUID(f"90000000-0000-4000-8000-{ordinal - 1:012d}")
        ),
        known_at=KNOWN + timedelta(minutes=minutes),
        effective_to=KNOWN + timedelta(days=2),
        evidence=(evidence(known_at=KNOWN + timedelta(minutes=minutes)),),
        payload=None if operation is SecurityMasterOperation.RETRACT else mapping_payload(),
    )


def stored(document, *, recorded_at=None) -> PersistedSecurityMasterRevisionV1:
    return PersistedSecurityMasterRevisionV1(
        revision=document,
        recorded_at=recorded_at or document.known_at,
    )


def test_resolver_selects_head_at_knowledge_cutoff_before_validity_filter() -> None:
    root = revision(effective_to=KNOWN + timedelta(days=2))
    correction = child(ordinal=2, minutes=10)
    retraction = child(ordinal=3, minutes=20, operation=SecurityMasterOperation.RETRACT)
    persisted_root = stored(root)
    persisted_correction = stored(correction)
    persisted_retraction = stored(retraction)
    resolver = SecurityMasterResolver(
        (persisted_root, persisted_correction, persisted_retraction)
    )

    assert resolver.resolve_fact(FACT_ID, valid_at=KNOWN, known_at=KNOWN + timedelta(minutes=5)) == persisted_root
    assert resolver.resolve_fact(FACT_ID, valid_at=KNOWN, known_at=KNOWN + timedelta(minutes=15)) == persisted_correction
    assert resolver.resolve_fact(FACT_ID, valid_at=KNOWN, known_at=KNOWN + timedelta(minutes=25)) is None
    assert resolver.resolve_fact(
        FACT_ID,
        valid_at=KNOWN + timedelta(days=2),
        known_at=KNOWN + timedelta(minutes=15),
    ) is None


def test_resolver_validates_linear_terminal_history() -> None:
    with pytest.raises(SecurityMasterIntegrityError, match="ordinal"):
        SecurityMasterResolver((stored(revision()), stored(child(ordinal=3, minutes=20))))
    with pytest.raises(SecurityMasterIntegrityError, match="strictly"):
        SecurityMasterResolver((stored(revision()), stored(child(ordinal=2, minutes=0))))
    with pytest.raises(SecurityMasterIntegrityError, match="terminal"):
        SecurityMasterResolver(
            (
                stored(revision(effective_to=KNOWN + timedelta(days=2))),
                stored(child(ordinal=2, minutes=10, operation=SecurityMasterOperation.RETRACT)),
                stored(child(ordinal=3, minutes=20)),
            )
        )


def test_subject_resolution_fails_for_zero_or_ambiguous_active_facts() -> None:
    root = revision()
    persisted_root = stored(root)
    resolver = SecurityMasterResolver((persisted_root,))

    with pytest.raises(SecurityMasterNotFoundError):
        resolver.require_one(
            kind=SecurityMasterIdentityKind.SYMBOL_MAPPING,
            subject_id=UUID("60000000-0000-4000-8000-000000000099"),
            valid_at=KNOWN,
            known_at=KNOWN,
        )

    competing = revision(
        fact_id=UUID("80000000-0000-4000-8000-000000000002"),
        revision_id=UUID("90000000-0000-4000-8000-000000000099"),
    )
    with pytest.raises(SecurityMasterAmbiguityError):
        SecurityMasterResolver((persisted_root, stored(competing))).require_one(
            kind=SecurityMasterIdentityKind.SYMBOL_MAPPING,
            subject_id=root.subject_id,
            valid_at=KNOWN,
            known_at=KNOWN,
        )

    with pytest.raises(SecurityMasterIntegrityError, match="UTC"):
        resolver.require_one(
            kind=SecurityMasterIdentityKind.SYMBOL_MAPPING,
            subject_id=UUID("60000000-0000-4000-8000-000000000099"),
            valid_at=KNOWN.replace(tzinfo=None),
            known_at=KNOWN,
        )


def test_symbol_lookup_is_exact_and_returns_no_stale_fallback() -> None:
    root = revision(effective_to=KNOWN + timedelta(days=1))
    persisted_root = stored(root)
    resolver = SecurityMasterResolver((persisted_root,))

    assert resolver.resolve_symbol_mapping(
        provider="BINANCE",
        raw_symbol="BTCUSDT",
        valid_at=KNOWN,
        known_at=KNOWN,
    ) == persisted_root
    with pytest.raises(SecurityMasterNotFoundError):
        resolver.resolve_symbol_mapping(
            provider="BINANCE",
            raw_symbol="btcusdt",
            valid_at=KNOWN,
            known_at=KNOWN,
        )
    with pytest.raises(SecurityMasterNotFoundError):
        resolver.resolve_symbol_mapping(
            provider="BINANCE",
            raw_symbol="BTCUSDT",
            valid_at=KNOWN + timedelta(days=1),
            known_at=KNOWN,
        )


def test_resolver_uses_db_recorded_at_for_knowledge_visibility() -> None:
    document = revision()
    recorded_at = KNOWN + timedelta(hours=2)
    persisted = stored(document, recorded_at=recorded_at)
    try:
        resolver = SecurityMasterResolver((persisted,))
    except SecurityMasterIntegrityError as exc:
        pytest.fail(f"resolver rejected the persisted envelope: {exc}")

    assert resolver.resolve_fact(
        FACT_ID,
        valid_at=KNOWN,
        known_at=KNOWN + timedelta(hours=1),
    ) is None
    assert resolver.resolve_fact(
        FACT_ID,
        valid_at=KNOWN,
        known_at=recorded_at,
    ) == persisted


def test_resolver_lineage_uses_strict_recorded_at_not_evidence_known_at() -> None:
    root = revision(
        known_at=KNOWN + timedelta(minutes=10),
        evidence=(evidence(known_at=KNOWN + timedelta(minutes=10)),),
    )
    correction = revision(
        revision_id=UUID("90000000-0000-4000-8000-000000000002"),
        revision_ordinal=2,
        supersedes_revision_id=root.revision_id,
        known_at=KNOWN + timedelta(minutes=5),
        evidence=(evidence(known_at=KNOWN + timedelta(minutes=5)),),
    )
    persisted_root = stored(root, recorded_at=KNOWN + timedelta(minutes=20))
    persisted_correction = stored(
        correction, recorded_at=KNOWN + timedelta(minutes=30)
    )

    resolver = SecurityMasterResolver((persisted_root, persisted_correction))

    assert resolver.resolve_fact(
        FACT_ID,
        valid_at=KNOWN,
        known_at=KNOWN + timedelta(minutes=25),
    ) == persisted_root
    assert resolver.resolve_fact(
        FACT_ID,
        valid_at=KNOWN,
        known_at=KNOWN + timedelta(minutes=30),
    ) == persisted_correction

    with pytest.raises(SecurityMasterIntegrityError, match="recorded_at"):
        SecurityMasterResolver(
            (
                persisted_root,
                persisted_correction.model_copy(
                    update={"recorded_at": persisted_root.recorded_at}
                ),
            )
        )


def test_subject_uuid_cannot_be_reused_across_identity_kinds() -> None:
    mapping = revision()
    colliding_issuer = revision(
        revision_id=UUID("90000000-0000-4000-8000-000000000002"),
        fact_id=UUID("80000000-0000-4000-8000-000000000002"),
        subject_id=MAPPING_ID,
        subject_kind=SecurityMasterIdentityKind.ISSUER,
        payload=IssuerPayloadV1(
            issuer_id=MAPPING_ID,
            legal_name="Conflicting Identity",
            jurisdiction="GLOBAL",
        ),
    )

    with pytest.raises(SecurityMasterIntegrityError, match="identity kind"):
        SecurityMasterResolver((stored(mapping), stored(colliding_issuer)))


@pytest.mark.parametrize(
    "changed_payload",
    (
        mapping_payload().model_copy(update={"provider": "OTHER"}),
        mapping_payload().model_copy(update={"raw_symbol": "ETHUSDT"}),
    ),
    ids=("provider", "raw-symbol"),
)
def test_symbol_mapping_lookup_keys_cannot_change_within_fact(
    changed_payload,
) -> None:
    correction = revision(
        revision_id=UUID("90000000-0000-4000-8000-000000000002"),
        revision_ordinal=2,
        supersedes_revision_id=REVISION_ID,
        known_at=KNOWN + timedelta(minutes=1),
        evidence=(evidence(known_at=KNOWN + timedelta(minutes=1)),),
        payload=changed_payload,
    )

    with pytest.raises(SecurityMasterIntegrityError, match="lookup keys"):
        SecurityMasterResolver((stored(revision()), stored(correction)))


def test_corporate_action_security_cannot_change_within_fact() -> None:
    root_payload = SplitPayloadV1(
        action_id=ACTION_ID,
        security_id=SECURITY_ID,
        action_type=CorporateActionType.SPLIT,
        new_units="2",
        old_units="1",
    )
    root = revision(
        subject_id=ACTION_ID,
        subject_kind=SecurityMasterIdentityKind.CORPORATE_ACTION,
        payload=root_payload,
    )
    correction = revision(
        revision_id=UUID("90000000-0000-4000-8000-000000000002"),
        subject_id=ACTION_ID,
        subject_kind=SecurityMasterIdentityKind.CORPORATE_ACTION,
        revision_ordinal=2,
        supersedes_revision_id=REVISION_ID,
        known_at=KNOWN + timedelta(minutes=1),
        evidence=(evidence(known_at=KNOWN + timedelta(minutes=1)),),
        payload=root_payload.model_copy(
            update={
                "security_id": UUID("30000000-0000-4000-8000-000000000002")
            }
        ),
    )

    with pytest.raises(SecurityMasterIntegrityError, match="lookup keys"):
        SecurityMasterResolver((stored(root), stored(correction)))


def test_corporate_action_retraction_needs_no_canonical_payload() -> None:
    root_payload = SplitPayloadV1(
        action_id=ACTION_ID,
        security_id=SECURITY_ID,
        action_type=CorporateActionType.SPLIT,
        new_units="2",
        old_units="1",
    )
    root = revision(
        subject_id=ACTION_ID,
        subject_kind=SecurityMasterIdentityKind.CORPORATE_ACTION,
        payload=root_payload,
    )
    retraction = revision(
        revision_id=UUID("90000000-0000-4000-8000-000000000002"),
        subject_id=ACTION_ID,
        subject_kind=SecurityMasterIdentityKind.CORPORATE_ACTION,
        revision_ordinal=2,
        operation=SecurityMasterOperation.RETRACT,
        supersedes_revision_id=REVISION_ID,
        known_at=KNOWN + timedelta(minutes=1),
        evidence=(evidence(known_at=KNOWN + timedelta(minutes=1)),),
        payload=None,
    )
    persisted_retraction = stored(
        retraction,
        recorded_at=KNOWN + timedelta(minutes=2),
    )
    resolver = SecurityMasterResolver((stored(root), persisted_retraction))

    assert resolver.resolve_fact(
        FACT_ID,
        valid_at=KNOWN,
        known_at=persisted_retraction.recorded_at,
    ) is None


def test_later_head_that_moves_validity_forward_never_falls_back_to_root() -> None:
    root = revision()
    correction = revision(
        revision_id=UUID("90000000-0000-4000-8000-000000000002"),
        revision_ordinal=2,
        supersedes_revision_id=REVISION_ID,
        effective_from=KNOWN + timedelta(days=1),
        known_at=KNOWN + timedelta(minutes=1),
        evidence=(evidence(known_at=KNOWN + timedelta(minutes=1)),),
    )
    persisted_root = stored(root)
    persisted_correction = stored(
        correction,
        recorded_at=KNOWN + timedelta(minutes=2),
    )
    resolver = SecurityMasterResolver((persisted_root, persisted_correction))

    assert resolver.resolve_fact(
        FACT_ID,
        valid_at=KNOWN,
        known_at=KNOWN + timedelta(minutes=1),
    ) == persisted_root
    assert resolver.resolve_fact(
        FACT_ID,
        valid_at=KNOWN,
        known_at=persisted_correction.recorded_at,
    ) is None

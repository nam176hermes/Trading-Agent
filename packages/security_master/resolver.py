"""Pure head-before-filter resolver for security-master revision histories."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from packages.domain import require_utc

from .models import (
    CashDividendPayloadV1,
    DelistingPayloadV1,
    PersistedSecurityMasterRevisionV1,
    SecurityMasterIdentityKind,
    SecurityMasterOperation,
    SecurityMasterRevisionV1,
    SplitPayloadV1,
    SymbolChangePayloadV1,
    SymbolMappingPayloadV1,
)


_CORPORATE_ACTION_PAYLOADS = (
    SplitPayloadV1,
    CashDividendPayloadV1,
    SymbolChangePayloadV1,
    DelistingPayloadV1,
)


class SecurityMasterIntegrityError(ValueError):
    """A persisted revision history is not linear and canonical."""


class SecurityMasterNotFoundError(LookupError):
    """No exact point-in-time security-master fact is active."""


class SecurityMasterAmbiguityError(SecurityMasterIntegrityError):
    """More than one fact is active for an exact point-in-time lookup."""


def _utc(value: datetime, name: str) -> datetime:
    try:
        return require_utc(value)
    except ValueError as exc:
        raise SecurityMasterIntegrityError(f"{name} must be an explicit UTC datetime") from exc


def _lookup_key(revision: SecurityMasterRevisionV1) -> tuple[object, ...] | None:
    payload = revision.payload
    if isinstance(payload, SymbolMappingPayloadV1):
        return (payload.provider, payload.raw_symbol)
    if isinstance(payload, _CORPORATE_ACTION_PAYLOADS):
        return (payload.security_id,)
    return None


class SecurityMasterResolver:
    """Validate bounded histories once, then resolve explicit PIT queries."""

    def __init__(self, revisions: Iterable[PersistedSecurityMasterRevisionV1]) -> None:
        grouped: dict[UUID, list[PersistedSecurityMasterRevisionV1]] = defaultdict(list)
        revision_ids: set[UUID] = set()
        for value in revisions:
            try:
                persisted = PersistedSecurityMasterRevisionV1.model_validate(value)
            except Exception as exc:
                raise SecurityMasterIntegrityError("revision is not canonical") from exc
            revision = persisted.revision
            if revision.revision_id in revision_ids:
                raise SecurityMasterIntegrityError("revision_id must be globally unique")
            revision_ids.add(revision.revision_id)
            grouped[revision.fact_id].append(persisted)
        if any(len(history) > 4096 for history in grouped.values()):
            raise SecurityMasterIntegrityError("fact history exceeds the 4096 revision bound")
        self._histories: dict[UUID, tuple[PersistedSecurityMasterRevisionV1, ...]] = {}
        identity_kinds: dict[UUID, SecurityMasterIdentityKind] = {}
        for fact_id, values in grouped.items():
            history = tuple(
                sorted(values, key=lambda item: item.revision.revision_ordinal)
            )
            self._validate_history(history)
            root = history[0].revision
            existing_kind = identity_kinds.setdefault(
                root.subject_id, root.subject_kind
            )
            if existing_kind is not root.subject_kind:
                raise SecurityMasterIntegrityError(
                    "subject identity kind conflicts globally"
                )
            self._histories[fact_id] = history

    @staticmethod
    def _validate_history(
        history: tuple[PersistedSecurityMasterRevisionV1, ...],
    ) -> None:
        if not history or history[0].revision.revision_ordinal != 1:
            raise SecurityMasterIntegrityError("fact history must start at ordinal 1")
        first = history[0].revision
        first_lookup_key = _lookup_key(first)
        previous = first
        previous_recorded_at = history[0].recorded_at
        for expected_ordinal, persisted in enumerate(history[1:], start=2):
            revision = persisted.revision
            if revision.revision_ordinal != expected_ordinal:
                raise SecurityMasterIntegrityError("fact history ordinal is not contiguous")
            if revision.supersedes_revision_id != previous.revision_id:
                raise SecurityMasterIntegrityError("fact history predecessor is not exact")
            if revision.subject_id != first.subject_id or revision.subject_kind is not first.subject_kind:
                raise SecurityMasterIntegrityError("fact history changed subject identity")
            if persisted.recorded_at <= previous_recorded_at:
                raise SecurityMasterIntegrityError(
                    "fact history recorded_at must increase strictly"
                )
            if previous.operation is SecurityMasterOperation.RETRACT:
                raise SecurityMasterIntegrityError("RETRACT is terminal")
            if (
                revision.operation is SecurityMasterOperation.ASSERT
                and _lookup_key(revision) != first_lookup_key
            ):
                raise SecurityMasterIntegrityError(
                    "fact lookup keys changed within history"
                )
            if revision.operation is SecurityMasterOperation.RETRACT and (
                revision.effective_from != previous.effective_from
                or revision.effective_to != previous.effective_to
            ):
                raise SecurityMasterIntegrityError("RETRACT must repeat the parent valid interval")
            previous = revision
            previous_recorded_at = persisted.recorded_at

    @staticmethod
    def _head(
        history: tuple[PersistedSecurityMasterRevisionV1, ...], known_at: datetime
    ) -> PersistedSecurityMasterRevisionV1 | None:
        for persisted in reversed(history):
            if persisted.recorded_at <= known_at:
                return persisted
        return None

    @staticmethod
    def _active(
        persisted: PersistedSecurityMasterRevisionV1, valid_at: datetime
    ) -> bool:
        revision = persisted.revision
        return (
            revision.operation is SecurityMasterOperation.ASSERT
            and revision.effective_from <= valid_at
            and (revision.effective_to is None or valid_at < revision.effective_to)
        )

    def resolve_fact(
        self,
        fact_id: UUID,
        *,
        valid_at: datetime,
        known_at: datetime,
    ) -> PersistedSecurityMasterRevisionV1 | None:
        valid = _utc(valid_at, "valid_at")
        known = _utc(known_at, "known_at")
        history = self._histories.get(fact_id)
        if history is None:
            return None
        head = self._head(history, known)
        return head if head is not None and self._active(head, valid) else None

    def require_one(
        self,
        *,
        kind: SecurityMasterIdentityKind,
        subject_id: UUID,
        valid_at: datetime,
        known_at: datetime,
    ) -> PersistedSecurityMasterRevisionV1:
        valid = _utc(valid_at, "valid_at")
        known = _utc(known_at, "known_at")
        matches = tuple(
            resolved
            for fact_id, history in self._histories.items()
            if history[0].revision.subject_kind is kind
            and history[0].revision.subject_id == subject_id
            if (resolved := self.resolve_fact(fact_id, valid_at=valid, known_at=known))
            is not None
        )
        if not matches:
            raise SecurityMasterNotFoundError("no exact active security-master fact")
        if len(matches) != 1:
            raise SecurityMasterAmbiguityError("multiple active security-master facts")
        return matches[0]

    def resolve_symbol_mapping(
        self,
        *,
        provider: str,
        raw_symbol: str,
        valid_at: datetime,
        known_at: datetime,
    ) -> PersistedSecurityMasterRevisionV1:
        valid = _utc(valid_at, "valid_at")
        known = _utc(known_at, "known_at")
        matches: list[PersistedSecurityMasterRevisionV1] = []
        for history in self._histories.values():
            head = self._head(history, known)
            if head is None or not self._active(head, valid):
                continue
            payload = head.revision.payload
            if (
                isinstance(payload, SymbolMappingPayloadV1)
                and payload.provider == provider
                and payload.raw_symbol == raw_symbol
            ):
                matches.append(head)
        if not matches:
            raise SecurityMasterNotFoundError("no exact active symbol mapping")
        if len(matches) != 1:
            raise SecurityMasterAmbiguityError("multiple active symbol mappings")
        return matches[0]


__all__ = [
    "SecurityMasterAmbiguityError",
    "SecurityMasterIntegrityError",
    "SecurityMasterNotFoundError",
    "SecurityMasterResolver",
]

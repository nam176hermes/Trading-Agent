"""Deterministic fail-closed quality checks for canonical market bars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
from typing import Mapping

from packages.domain import require_utc
from packages.engine_contracts.serialization import canonical_json_bytes


class DataQualityError(ValueError):
    """Canonical rows violate ordering, numeric, or OHLC invariants."""


class DataConflictError(ValueError):
    """Provider candidates cannot be resolved by the explicit priority policy."""


@dataclass(frozen=True, slots=True)
class ProviderCandidateV1:
    provider: str
    value_sha256: str
    system_observed_at: datetime

    def __post_init__(self) -> None:
        if (
            not self.provider
            or len(self.value_sha256) != 64
            or any(value not in "0123456789abcdef" for value in self.value_sha256)
        ):
            raise DataConflictError("provider candidate identity is invalid")
        require_utc(self.system_observed_at)


@dataclass(frozen=True, slots=True)
class DataConflictReceiptV1:
    selected: ProviderCandidateV1
    rejected: tuple[ProviderCandidateV1, ...]


@dataclass(frozen=True, slots=True)
class DataQualityReceiptV1:
    dataset: str
    row_count: int
    issue_codes: tuple[str, ...]
    canonical_rows_sha256: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "canonical_rows_sha256": self.canonical_rows_sha256,
                    "dataset": self.dataset,
                    "issue_codes": self.issue_codes,
                    "row_count": self.row_count,
                }
            )
        ).hexdigest()


def _decimal(row: Mapping[str, object], field: str) -> Decimal:
    try:
        value = Decimal(str(row[field]))
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise DataQualityError(f"bar {field} is invalid") from exc
    if not value.is_finite():
        raise DataQualityError(f"bar {field} is invalid")
    return value


def validate_bar_rows(
    rows: tuple[Mapping[str, object], ...], *, dataset: str
) -> DataQualityReceiptV1:
    if not dataset or not rows:
        raise DataQualityError("dataset and non-empty rows are required")
    timestamps: list[datetime] = []
    canonical: list[dict[str, str]] = []
    for row in rows:
        timestamp = row.get("ts_event")
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise DataQualityError("bar ts_event must be timezone-aware")
        try:
            require_utc(timestamp)
        except ValueError as exc:
            raise DataQualityError("bar ts_event must use canonical UTC") from exc
        open_, high, low, close, volume = (
            _decimal(row, field)
            for field in ("open", "high", "low", "close", "volume")
        )
        if low > min(open_, close) or high < max(open_, close) or low > high:
            raise DataQualityError("bar OHLC values are inconsistent")
        if min(open_, high, low, close) <= 0 or volume < 0:
            raise DataQualityError("bar price or volume is outside canonical bounds")
        timestamps.append(timestamp)
        canonical.append(
            {
                "close": format(close, "f"),
                "high": format(high, "f"),
                "low": format(low, "f"),
                "open": format(open_, "f"),
                "ts_event": timestamp.isoformat(),
                "volume": format(volume, "f"),
            }
        )
    if timestamps != sorted(set(timestamps)):
        raise DataQualityError("bar timestamps must be strictly increasing")
    rows_digest = hashlib.sha256(canonical_json_bytes(canonical)).hexdigest()
    return DataQualityReceiptV1(dataset, len(rows), (), rows_digest)


def resolve_provider_conflict(
    candidates: tuple[ProviderCandidateV1, ...], *, provider_priority: tuple[str, ...]
) -> DataConflictReceiptV1:
    if (
        not candidates
        or not provider_priority
        or len(provider_priority) != len(set(provider_priority))
    ):
        raise DataConflictError("provider conflict policy is invalid")
    if any(not isinstance(candidate, ProviderCandidateV1) for candidate in candidates):
        raise DataConflictError("provider candidate has invalid type")
    canonical = tuple(candidates)
    if any(candidate.provider not in provider_priority for candidate in canonical):
        raise DataConflictError("candidate provider has no explicit priority")
    best_rank = min(provider_priority.index(candidate.provider) for candidate in canonical)
    selected_rank = tuple(
        candidate
        for candidate in canonical
        if provider_priority.index(candidate.provider) == best_rank
    )
    if len(selected_rank) != 1:
        raise DataConflictError("top-priority provider result is ambiguous")
    selected = selected_rank[0]
    rejected = tuple(
        sorted(
            (candidate for candidate in canonical if candidate is not selected),
            key=lambda candidate: (
                provider_priority.index(candidate.provider),
                candidate.system_observed_at,
                candidate.value_sha256,
            ),
        )
    )
    return DataConflictReceiptV1(selected, rejected)


__all__ = [
    "DataConflictError",
    "DataConflictReceiptV1",
    "DataQualityError",
    "DataQualityReceiptV1",
    "ProviderCandidateV1",
    "resolve_provider_conflict",
    "validate_bar_rows",
]

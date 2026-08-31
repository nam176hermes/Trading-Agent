"""Measured gate for keeping Apache Iceberg out of the local P2 baseline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IcebergScaleProfileV1:
    dataset_bytes: int
    concurrent_writers: int
    object_store_primary: bool
    partition_evolutions_per_quarter: int
    query_engines: int

    def __post_init__(self) -> None:
        if min(
            self.dataset_bytes,
            self.concurrent_writers,
            self.partition_evolutions_per_quarter,
            self.query_engines,
        ) < 0:
            raise ValueError("Iceberg scale measurements cannot be negative")


@dataclass(frozen=True, slots=True)
class IcebergGateDecisionV1:
    enabled: bool
    reasons: tuple[str, ...]


def evaluate_iceberg_gate(profile: IcebergScaleProfileV1) -> IcebergGateDecisionV1:
    reasons = tuple(
        reason
        for triggered, reason in (
            (profile.dataset_bytes > 100 * 1024**3, "dataset-over-100-gib"),
            (profile.concurrent_writers > 1, "multiple-writers"),
            (profile.object_store_primary, "object-store-primary"),
            (profile.partition_evolutions_per_quarter > 1, "frequent-partition-evolution"),
            (profile.query_engines >= 3, "three-or-more-query-engines"),
        )
        if triggered
    )
    return IcebergGateDecisionV1(bool(reasons), reasons)


__all__ = ["IcebergGateDecisionV1", "IcebergScaleProfileV1", "evaluate_iceberg_gate"]

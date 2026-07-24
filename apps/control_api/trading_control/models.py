from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PlannedAuditEvent:
    code: str
    details: dict[str, str]


@dataclass(frozen=True, slots=True)
class NormalizedDecision:
    record_key: str
    asset_id: str
    symbol: str
    action: str
    confidence: float
    as_of: datetime
    known_at: datetime | None
    provenance_quality: str
    source_hash: str
    source_record_index: int
    canonical_fingerprint: str
    audit_events: tuple[PlannedAuditEvent, ...] = field(default_factory=tuple)

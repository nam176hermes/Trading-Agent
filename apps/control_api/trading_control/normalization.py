from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Mapping, Any

from . import NORMALIZATION_VERSION
from .identity import record_key, sha256_bytes
from .models import NormalizedDecision, PlannedAuditEvent

ASSETS = {
    **{symbol: f"crypto:spot:{symbol}/USDT" for symbol in ("BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC")},
    **{symbol: f"equity:alpaca:{symbol}" for symbol in ("AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "SPY", "QQQ")},
}
ACTIONS = {"BUY", "SELL", "HOLD", "STRONG_BUY", "STRONG_SELL", "WAIT", "NO_SIGNAL", "WATCH_FOR_ENTRY"}


class MigrationValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _aware_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MigrationValidationError("MISSING_REQUIRED_FIELD", "decision timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise MigrationValidationError("SCHEMA_VALIDATION_FAILED", "decision timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise MigrationValidationError("SCHEMA_VALIDATION_FAILED", "ambiguous naive timestamp")
    return parsed.astimezone(UTC)


def normalize_decision(
    value: Mapping[str, Any], *, source_hash: str, record_index: int
) -> NormalizedDecision:
    symbol_value = value.get("ticker")
    if not isinstance(symbol_value, str) or not symbol_value.strip():
        raise MigrationValidationError("MISSING_REQUIRED_FIELD", "decision ticker is required")
    symbol = symbol_value.strip().upper()
    if symbol not in ASSETS:
        raise MigrationValidationError("UNKNOWN_ASSET", "decision asset is not registered")
    confidence_value = value.get("confidence")
    if isinstance(confidence_value, bool) or not isinstance(confidence_value, (int, float)):
        raise MigrationValidationError("INVALID_CONFIDENCE", "decision confidence must be numeric")
    confidence = float(confidence_value)
    if not 0.0 <= confidence <= 1.0:
        raise MigrationValidationError("INVALID_CONFIDENCE", "decision confidence is outside [0,1]")
    raw_action = value.get("suggestion")
    if not isinstance(raw_action, str):
        raise MigrationValidationError("MISSING_REQUIRED_FIELD", "decision action is required")
    canonical_action = raw_action.strip().upper().replace("-", "_").replace(" ", "_")
    if canonical_action not in ACTIONS:
        raise MigrationValidationError("INVALID_ENUM", "decision action is not canonical")
    audits: tuple[PlannedAuditEvent, ...] = ()
    if raw_action.strip().upper() != canonical_action:
        audits = (PlannedAuditEvent("NORMALIZED_ACTION_ALIAS", {"to": canonical_action}),)
    as_of = _aware_timestamp(value.get("stored_at") or value.get("date"))
    canonical = {
        "asset_id": ASSETS[symbol],
        "action": canonical_action,
        "confidence": confidence,
        "as_of": as_of.isoformat(),
        "known_at": None,
        "provenance_quality": "LEGACY_ESTIMATED",
    }
    fingerprint = sha256_bytes(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode())
    return NormalizedDecision(
        record_key=record_key("decisions", source_hash, record_index, NORMALIZATION_VERSION),
        asset_id=ASSETS[symbol], symbol=symbol, action=canonical_action,
        confidence=confidence, as_of=as_of, known_at=None,
        provenance_quality="LEGACY_ESTIMATED", source_hash=source_hash,
        source_record_index=record_index, canonical_fingerprint=fingerprint,
        audit_events=audits,
    )

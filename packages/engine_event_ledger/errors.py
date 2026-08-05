"""Typed engine-event ledger failures for worker lifecycle mapping."""

from __future__ import annotations

from enum import Enum
from uuid import UUID


class EngineEventLedgerError(RuntimeError):
    """Base failure at the validated engine-event ingestion boundary."""


class EngineEventConflictError(EngineEventLedgerError):
    """An existing identity was presented with different canonical content."""


class InvalidEngineEventBatchError(EngineEventLedgerError):
    """Input was not the exact sealed output of engine result validation."""


class EngineEventSequenceBlockReason(str, Enum):
    SEQUENCE_GAP = "SEQUENCE_GAP"
    SEQUENCE_REGRESSION = "SEQUENCE_REGRESSION"


class EngineEventSequenceBlockedError(EngineEventLedgerError):
    """A batch cannot follow the durable per-run sequence."""

    def __init__(
        self,
        *,
        engine_run_id: UUID,
        expected_sequence: int,
        actual_sequence: int,
    ) -> None:
        self.engine_run_id = engine_run_id
        self.expected_sequence = expected_sequence
        self.actual_sequence = actual_sequence
        self.reason = (
            EngineEventSequenceBlockReason.SEQUENCE_GAP
            if actual_sequence > expected_sequence
            else EngineEventSequenceBlockReason.SEQUENCE_REGRESSION
        )
        super().__init__(
            f"engine run {engine_run_id} expected sequence {expected_sequence}, "
            f"got {actual_sequence} ({self.reason.value})"
        )

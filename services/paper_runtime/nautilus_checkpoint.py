"""Exact in-process checkpoint custody for one P1 Nautilus paper session."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import re
from uuid import UUID

from packages.engine_contracts import canonical_json_bytes
from packages.engine_event_ledger import EngineEventBatchReceipt
from packages.engine_portfolio_projection.parity import P1PortfolioParityReceipt
from packages.nautilus_runtime_contracts.paper import PaperSessionCheckpoint


ZERO_CHECKPOINT_SHA256 = "0" * 64
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def checkpoint_sha256(checkpoint: PaperSessionCheckpoint) -> str:
    if type(checkpoint) is not PaperSessionCheckpoint:
        raise TypeError("exact paper checkpoint is required")
    return hashlib.sha256(canonical_json_bytes(checkpoint)).hexdigest()


@dataclass(frozen=True, slots=True)
class NautilusCheckpointRecord:
    checkpoint: PaperSessionCheckpoint
    checkpoint_sha256: str
    event_batch_sha256: str | None = None
    parity_receipt_sha256: str | None = None


class NautilusCheckpointStore:
    """Current-process view rebuilt from the durable P1-29 command chain."""

    def __init__(self) -> None:
        self._records: dict[UUID, NautilusCheckpointRecord] = {}

    def load(self, session_id: UUID) -> NautilusCheckpointRecord | None:
        if type(session_id) is not UUID:
            raise TypeError("paper session identity is invalid")
        return self._records.get(session_id)

    def advance(
        self,
        checkpoint: PaperSessionCheckpoint,
        *,
        expected_prior_sha256: str,
    ) -> NautilusCheckpointRecord:
        if _SHA256.fullmatch(expected_prior_sha256) is None:
            raise ValueError("expected checkpoint identity is invalid")
        prior = self.load(checkpoint.session_id)
        observed_prior = (
            ZERO_CHECKPOINT_SHA256 if prior is None else prior.checkpoint_sha256
        )
        if expected_prior_sha256 != observed_prior:
            raise ValueError("paper checkpoint authority changed")
        if prior is not None:
            before = prior.checkpoint
            if (
                checkpoint.session_id != before.session_id
                or checkpoint.owner_id != before.owner_id
                or checkpoint.child_identity != before.child_identity
                or checkpoint.closure_digest != before.closure_digest
                or checkpoint.last_accepted_command
                != before.last_accepted_command + 1
                or checkpoint.last_acknowledged_command
                != checkpoint.last_accepted_command
                or checkpoint.last_emitted_event < before.last_emitted_event
            ):
                raise ValueError("paper checkpoint continuity is invalid")
        digest = checkpoint_sha256(checkpoint)
        record = NautilusCheckpointRecord(checkpoint, digest)
        self._records[checkpoint.session_id] = record
        return record

    def bind_durable_result(
        self,
        checkpoint_sha: str,
        receipt: EngineEventBatchReceipt,
        parity: P1PortfolioParityReceipt,
    ) -> NautilusCheckpointRecord:
        if (
            _SHA256.fullmatch(checkpoint_sha) is None
            or type(receipt) is not EngineEventBatchReceipt
            or type(parity) is not P1PortfolioParityReceipt
        ):
            raise TypeError("paper durable result authority is invalid")
        record = self._records.get(receipt.engine_run_id)
        if (
            record is None
            or record.checkpoint_sha256 != checkpoint_sha
            or record.checkpoint.state != "STOPPING"
            or receipt.batch_sha256 != parity.batch_sha256
            or receipt.engine_run_id != parity.engine_run_id
        ):
            raise ValueError("paper durable result does not match checkpoint")
        updated = replace(
            record,
            event_batch_sha256=receipt.batch_sha256,
            parity_receipt_sha256=hashlib.sha256(
                canonical_json_bytes(parity)
            ).hexdigest(),
        )
        self._records[receipt.engine_run_id] = updated
        return updated


__all__ = [
    "NautilusCheckpointRecord",
    "NautilusCheckpointStore",
    "ZERO_CHECKPOINT_SHA256",
    "checkpoint_sha256",
]

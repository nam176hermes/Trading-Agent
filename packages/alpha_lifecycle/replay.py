"""Parent-observed deterministic P3 replay proof."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from packages.alpha_lifecycle.contracts.results import ReplayProof, ReplayReceipt
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


class SandboxExecutor(Protocol):
    def execute(
        self, manifest_ref: ArtifactRefV1, *, replicate: str,
        logical_trial_id: str, output_dir: Path,
    ) -> ReplayReceipt: ...
    def read_bytes(self, ref: ArtifactRefV1) -> bytes: ...
    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1: ...


class ReplayError(ValueError):
    """Independent child results do not prove deterministic replay."""


def run_replays(
    manifest_ref: ArtifactRefV1,
    executor: SandboxExecutor,
    *,
    logical_trial_id: str,
    output_root: Path,
) -> ReplayProof:
    receipts = []
    outputs = []
    for replicate in ("R1", "R2", "R3"):
        directory = output_root / replicate.lower()
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        receipt = ReplayReceipt.model_validate(executor.execute(
            manifest_ref, replicate=replicate,
            logical_trial_id=logical_trial_id, output_dir=directory,
        ))
        if receipt.replicate != replicate or receipt.manifest_digest != manifest_ref.content_sha256:
            raise ReplayError("execution receipt does not bind its replay request")
        value = executor.read_bytes(receipt.result_ref)
        if hashlib.sha256(value).hexdigest() != receipt.result_ref.content_sha256:
            raise ReplayError("parent read-back result digest is invalid")
        outputs.append(value)
        receipts.append(executor.put_bytes(canonical_json_bytes(receipt), media_type="application/json"))
    if len(set(outputs)) != 1:
        raise ReplayError("E_REPLAY: independent result bytes differ")
    payload = {
        "schema_version": "p3-replay-proof-v1",
        "manifest_digest": manifest_ref.content_sha256,
        "result_digest": hashlib.sha256(outputs[0]).hexdigest(),
        "receipt_refs": tuple(receipts),
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return ReplayProof.model_validate(payload)


__all__ = ["ReplayError", "SandboxExecutor", "run_replays"]

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from packages.alpha_lifecycle.contracts.results import ReplayReceipt
from packages.alpha_lifecycle.replay import ReplayError, run_replays
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def _ref(digest: str, size: int = 1) -> ArtifactRefV1:
    return ArtifactRefV1(content_sha256=digest, size_bytes=size, media_type="application/json", locator=f"{digest}.blob")


class Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []
        self.result = b'{"result":"same"}'
        self.result_ref = _ref(hashlib.sha256(self.result).hexdigest(), len(self.result))
        self.values = {self.result_ref.content_sha256: self.result}

    def execute(self, manifest_ref, *, replicate, logical_trial_id, output_dir):
        self.calls.append((replicate, output_dir))
        payload = {
            "schema_version": "p3-replay-receipt-v1", "logical_trial_id": logical_trial_id,
            "replicate": replicate, "manifest_digest": manifest_ref.content_sha256,
            "result_ref": self.result_ref,
            "source": {"commit_sha":"a"*40,"tree_sha":"b"*40,"closure_schema_version":"v1","closure_policy_sha256":"c"*64,"closure_sha256":"d"*64},
            "environment_ref": _ref("e"*64), "sandbox_policy_digest": "f"*64,
            "started_at": "2026-09-05T00:00:00Z",
            "completed_at": "2026-09-05T00:00:01Z",
            "process_exit": 0, "network_denied": True,
            "output_inventory_digest": hashlib.sha256(self.result).hexdigest(),
        }
        payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return ReplayReceipt.model_validate(payload)

    def read_bytes(self, ref):
        return self.values[ref.content_sha256]

    def put_bytes(self, value, *, media_type):
        ref = _ref(hashlib.sha256(value).hexdigest(), len(value))
        self.values[ref.content_sha256] = value
        return ref


def test_three_parent_observed_replays_are_byte_identical(tmp_path: Path) -> None:
    executor = Executor()
    manifest_ref = _ref("1" * 64)
    proof = run_replays(manifest_ref, executor, logical_trial_id="trial.same", output_root=tmp_path)
    assert len(proof.receipt_refs) == 3
    assert tuple(item[0] for item in executor.calls) == ("R1", "R2", "R3")
    assert len({item[1] for item in executor.calls}) == 3


@pytest.mark.parametrize("field", ("logical_trial_id", "source", "environment_ref", "sandbox_policy_digest", "output_inventory_digest", "result_ref"))
def test_replay_rejects_mismatched_receipt_binding(tmp_path: Path, field: str) -> None:
    class MismatchedExecutor(Executor):
        def execute(self, manifest_ref, **kwargs):
            receipt = super().execute(manifest_ref, **kwargs)
            if kwargs["replicate"] != "R2":
                return receipt
            payload = receipt.model_dump(mode="json", exclude={"digest"})
            if field == "logical_trial_id":
                payload[field] = "trial.other"
            elif field == "source":
                payload[field]["commit_sha"] = "9" * 40
            elif field == "environment_ref":
                payload[field] = _ref("9" * 64).model_dump(mode="json")
            elif field in {"sandbox_policy_digest", "output_inventory_digest"}:
                payload[field] = "9" * 64
            else:
                payload[field]["size_bytes"] += 1
            payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
            return ReplayReceipt.model_validate(payload)

    executor = MismatchedExecutor()
    with pytest.raises(ReplayError):
        run_replays(_ref("1" * 64), executor, logical_trial_id="trial.same", output_root=tmp_path)
    assert len(executor.calls) == 2

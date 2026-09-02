"""Deterministic provider-free paper session used by portable HWC proofs."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from uuid import UUID

from packages.engine_contracts import EngineSessionIdentityV1, canonical_json_bytes
from services.paper_runtime.nautilus_child import (
    EngineSessionPort,
    issue_engine_session_port,
)


_SESSION_ID = UUID("40000000-0000-4000-8000-000000000001")
_OWNER_ID = UUID("40000000-0000-4000-8000-000000000002")


def _sha(payload: object) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class DeterministicPaperRuntime:
    """A tiny JSONL adapter around the repository's issued session port."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, mode=0o700)
        (self.root / "results").mkdir(mode=0o700)
        self.sequence = 0
        self.event_sha256 = "0" * 64
        identity = EngineSessionIdentityV1(
            runtime_family="provider-free-fixture-v1",
            engine_version="1.231.0",
            engine_upstream_commit="2" * 40,
            closure_digest=_sha({"fixture": "hwc-paper-runtime-v1"}),
            request_protocol="hwc-paper-batch-v1",
            event_schema="hwc-paper-event-v1",
            paper_schema="paper-session-v1",
        )
        self.port: EngineSessionPort = issue_engine_session_port(
            identity=identity,
            capability_sha256="4" * 64,
            custodian_authority_sha256="5" * 64,
            process_authority_sha256="6" * 64,
            paper_source_sha256="7" * 64,
            session_id=_SESSION_ID,
            owner_id=_OWNER_ID,
            exchange=self.exchange,
            close_input=lambda: 0,
            abort=lambda: None,
            is_running=lambda: True,
        )

    def exchange(self, raw: bytes) -> bytes:
        try:
            request = json.loads(raw)
            expected_batch = "A" if self.sequence == 0 else "B" if self.sequence == 1 else None
            if (
                not isinstance(request, dict)
                or set(request) != {"schema_version", "batch", "price", "target_quantity"}
                or request.get("schema_version") != "hwc-paper-batch-v1"
                or request.get("batch") != expected_batch
                or any(not isinstance(request.get(key), str) for key in ("price", "target_quantity"))
                or Decimal(request["price"]) <= 0
                or Decimal(request["target_quantity"]) < 0
            ):
                raise ValueError
        except (InvalidOperation, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("paper batch is invalid") from exc

        input_sha256 = _sha(request)
        sequence = self.sequence + 1
        previous = self.event_sha256
        event = {
            "schema_version": "hwc-paper-event-v1",
            "batch": request["batch"],
            "input_sha256": input_sha256,
            "previous_event_sha256": previous,
            "sequence": sequence,
        }
        event_sha256 = _sha(event)
        result = {
            **event,
            "schema_version": "hwc-paper-result-v1",
            "event_sha256": event_sha256,
            "session_id": str(_SESSION_ID),
        }
        result["result_sha256"] = _sha(result)
        checkpoint = {
            "schema_version": "hwc-paper-checkpoint-v1",
            "event_sha256": event_sha256,
            "result_sha256": result["result_sha256"],
            "sequence": sequence,
            "session_id": str(_SESSION_ID),
        }
        checkpoint["checkpoint_sha256"] = _sha(checkpoint)
        result["checkpoint_sha256"] = checkpoint["checkpoint_sha256"]
        self._write(self.root / "checkpoint.json", checkpoint)
        self._write(self.root / "results" / f"batch-{request['batch'].lower()}.json", result)
        self.sequence = sequence
        self.event_sha256 = event_sha256
        return canonical_json_bytes(result)

    @staticmethod
    def _write(path: Path, payload: object) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(canonical_json_bytes(payload) + b"\n")
        temporary.chmod(0o600)
        temporary.replace(path)


__all__ = ["DeterministicPaperRuntime"]

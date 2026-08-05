from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid5

import pytest

from packages.engine_contracts import (
    EngineEvent,
    EngineEventEnvelope,
    EventFamily,
    canonical_json_bytes,
    payload_digest,
)
from packages.job_contracts import EngineBacktestPayload, JobType, parse_payload
from services.job_store.worker_repository import ClaimedJob
from services.job_worker.artifacts import ArtifactMetadata
from services.job_worker.engine_authority import BacktestEngineAuthorityFactory


NOW = datetime(2026, 8, 5, 12, 30, 15, 123456, tzinfo=UTC)
CODE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
JOB_ID = "job_0123456789abcdef0123456789abcdef"
ATTEMPT_ID = "attempt_fedcba9876543210fedcba9876543210"


def _claim() -> ClaimedJob:
    payload = parse_payload(
        JobType.BACKTEST,
        {
            "engine_backtest": {
                "engine_configuration": {
                    "artifact_id": "11111111-1111-4111-8111-111111111111",
                    "sha256": "1" * 64,
                    "media_type": "application/json",
                },
                "instrument_catalog": {
                    "artifact_id": "22222222-2222-4222-8222-222222222222",
                    "sha256": "2" * 64,
                    "media_type": "application/json",
                },
                "strategy_configuration": {
                    "artifact_id": "33333333-3333-4333-8333-333333333333",
                    "sha256": "3" * 64,
                    "media_type": "application/json",
                },
                "market_data": {
                    "artifact_id": "44444444-4444-4444-8444-444444444444",
                    "sha256": "4" * 64,
                    "media_type": "application/jsonl",
                },
                "start_time": "2026-07-01T00:00:00Z",
                "end_time": "2026-08-01T00:00:00Z",
            }
        },
    )
    assert isinstance(payload, EngineBacktestPayload)
    return ClaimedJob(
        job_id=JOB_ID,
        job_type=JobType.BACKTEST,
        payload=payload,
        attempt_id=ATTEMPT_ID,
        attempt_number=1,
        worker_id="worker-authority-1",
        lease_token="lease-token_0123456789abcdefghijklmnopqrstuvwxyz",
        lease_expires_at=NOW + timedelta(seconds=30),
        max_attempts=2,
    )


def _request():
    return BacktestEngineAuthorityFactory(
        code_commit=CODE_COMMIT, clock=lambda: NOW
    ).from_claim(_claim())


def _event(
    *,
    request=None,
    sequence: int = 2,
    event_type: str = "BacktestFixtureCompleted",
):
    request = request or _request()
    payload = EngineEvent(
        event_type=event_type,
        family=EventFamily.ENGINE_LIFECYCLE,
    )
    return EngineEventEnvelope(
        message_id=uuid5(request.message_id, event_type),
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
        engine_run_id=request.engine_run_id,
        stream_sequence=sequence,
        event_time=request.event_time,
        initialization_time=request.initialization_time,
        schema_version=request.schema_version,
        producer_identity=request.producer_identity,
        source_commit=request.source_commit,
        config_digest=request.config_digest,
        payload_digest=payload_digest(payload),
        payload=payload,
    )


def _stdout(root: Path, raw: bytes) -> ArtifactMetadata:
    path = root / JOB_ID / ATTEMPT_ID / "stdout.log"
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_bytes(raw)
    path.chmod(0o600)
    return ArtifactMetadata(
        "stdout",
        f"{JOB_ID}/{ATTEMPT_ID}/stdout.log",
        hashlib.sha256(raw).hexdigest(),
        len(raw),
        "application/octet-stream",
        False,
    )


def test_engine_stdout_is_canonical_authority_validated_and_sealed(
    tmp_path: Path,
) -> None:
    from services.job_worker.engine_results import EngineResultValidator

    event = _event()
    raw = canonical_json_bytes(event) + b"\n"
    progress: list[str] = []

    result = EngineResultValidator(tmp_path).validate(
        "engine-event-v1",
        _claim(),
        request=_request(),
        stdout=_stdout(tmp_path, raw),
        exit_code=0,
        progress=lambda: progress.append("checked"),
    )

    assert result.events == (event,)
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert result.relative_ref == (
        f"engine-results/{JOB_ID}/{ATTEMPT_ID}/{result.sha256}.jsonl"
    )
    assert (tmp_path / result.relative_ref).read_bytes() == raw
    assert result.validation_metadata == {
        "attempt_id": ATTEMPT_ID,
        "config_digest": _request().config_digest,
        "engine_run_id": str(_request().engine_run_id),
        "event_count": 1,
        "first_sequence": 2,
        "job_id": JOB_ID,
        "last_sequence": 2,
        "request_message_id": str(_request().message_id),
        "source_commit": CODE_COMMIT,
        "validator_id": "engine-event-v1",
    }
    assert progress


@pytest.mark.parametrize(
    "mutation",
    ("malformed", "noncanonical", "duplicate", "wrong_authority", "truncated"),
)
def test_engine_stdout_refuses_every_noncanonical_or_unattributed_result(
    tmp_path: Path, mutation: str,
) -> None:
    from services.job_worker.engine_results import (
        EngineResultValidationError,
        EngineResultValidator,
    )

    event = _event()
    raw = canonical_json_bytes(event) + b"\n"
    metadata = _stdout(tmp_path, raw)
    if mutation == "malformed":
        raw = b"{not-json}\n"
    elif mutation == "noncanonical":
        raw = b" " + canonical_json_bytes(event) + b"\n"
    elif mutation == "duplicate":
        raw = canonical_json_bytes(event) + b"\n" + canonical_json_bytes(event) + b"\n"
    elif mutation == "wrong_authority":
        wrong = event.model_copy(update={"correlation_id": event.message_id})
        raw = canonical_json_bytes(wrong) + b"\n"
    metadata = _stdout(tmp_path / mutation, raw)
    if mutation == "truncated":
        metadata = replace(metadata, truncated=True)

    with pytest.raises(EngineResultValidationError):
        EngineResultValidator(tmp_path / mutation).validate(
            "engine-event-v1",
            _claim(),
            request=_request(),
            stdout=metadata,
            exit_code=0,
        )


def test_engine_stdout_metadata_must_match_the_worker_captured_bytes(
    tmp_path: Path,
) -> None:
    from services.job_worker.engine_results import (
        EngineResultValidationError,
        EngineResultValidator,
    )

    metadata = _stdout(tmp_path, canonical_json_bytes(_event()) + b"\n")

    with pytest.raises(EngineResultValidationError, match="metadata"):
        EngineResultValidator(tmp_path).validate(
            "engine-event-v1",
            _claim(),
            request=_request(),
            stdout=replace(metadata, sha256="0" * 64),
            exit_code=0,
        )


class _UntrustedArtifactMetadata(ArtifactMetadata):
    pass


@pytest.mark.parametrize(
    "mutation",
    (
        "subclass",
        "artifact_type",
        "relative_ref",
        "media_type",
        "validator_id",
        "truncated",
        "boolean_size",
        "zero_size",
        "oversize",
        "malformed_digest",
    ),
)
def test_engine_stdout_requires_the_exact_bounded_capture_descriptor(
    tmp_path: Path, mutation: str,
) -> None:
    from services.job_worker.engine_results import (
        EngineResultValidationError,
        EngineResultValidator,
    )

    raw = canonical_json_bytes(_event()) + b"\n"
    metadata = _stdout(tmp_path, raw)
    if mutation == "subclass":
        metadata = _UntrustedArtifactMetadata(
            metadata.artifact_type,
            metadata.relative_ref,
            metadata.sha256,
            metadata.size_bytes,
            metadata.media_type,
            metadata.truncated,
            metadata.validator_id,
        )
    elif mutation == "artifact_type":
        metadata = replace(metadata, artifact_type="stderr")
    elif mutation == "relative_ref":
        metadata = replace(metadata, relative_ref="other/stdout.log")
    elif mutation == "media_type":
        metadata = replace(metadata, media_type="text/plain")
    elif mutation == "validator_id":
        metadata = replace(metadata, validator_id="untrusted-validator")
    elif mutation == "truncated":
        metadata = replace(metadata, truncated=True)
    elif mutation == "boolean_size":
        metadata = replace(metadata, size_bytes=True)
    elif mutation == "zero_size":
        metadata = replace(metadata, size_bytes=0)
    elif mutation == "oversize":
        metadata = replace(metadata, size_bytes=1024 * 1024 + 1)
    elif mutation == "malformed_digest":
        metadata = replace(metadata, sha256="not-a-sha256")
    # Every mutation must be rejected from descriptor metadata alone. Removing
    # the file proves validation did not reach the captured-byte read.
    (tmp_path / JOB_ID / ATTEMPT_ID / "stdout.log").unlink()

    with pytest.raises(EngineResultValidationError, match="capture metadata"):
        EngineResultValidator(tmp_path).validate(
            "engine-event-v1",
            _claim(),
            request=_request(),
            stdout=metadata,
            exit_code=0,
        )


def test_engine_result_refuses_a_request_not_derived_from_the_claim(
    tmp_path: Path,
) -> None:
    from services.job_worker.engine_results import (
        EngineResultValidationError,
        EngineResultValidator,
    )

    wrong_claim = replace(
        _claim(),
        attempt_id="attempt_00000000000000000000000000000002",
        attempt_number=2,
    )
    wrong_request = BacktestEngineAuthorityFactory(
        code_commit=CODE_COMMIT, clock=lambda: NOW
    ).from_claim(wrong_claim)
    raw = canonical_json_bytes(_event(request=wrong_request)) + b"\n"

    with pytest.raises(EngineResultValidationError, match="request authority"):
        EngineResultValidator(tmp_path).validate(
            "engine-event-v1",
            _claim(),
            request=wrong_request,
            stdout=_stdout(tmp_path, raw),
            exit_code=0,
        )

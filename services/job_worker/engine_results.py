"""Worker-owned validation and sealing of canonical engine event stdout."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from packages.engine_contracts import (
    EngineCommandEnvelope,
    EngineEventEnvelope,
    EventFamily,
    RunBacktestSimulation,
    canonical_json_bytes,
    validate_envelope_batch,
)
from packages.nautilus_runtime_contracts.result import (
    P1_EVENT_FAMILIES,
    P1_ENGINE_VERSION,
    P1_RESULT_VALIDATOR_ID,
    P1_RUNTIME_FAMILY,
    P1_UPSTREAM_COMMIT,
    P1ValidatedResult,
    validate_p1_result,
)
from packages.job_contracts import (
    EngineBacktestPayload,
    EngineBacktestSimulationPayload,
    JobType,
)
from services.job_store.worker_repository import ClaimedJob

from .artifacts import MAX_STREAM_BYTES, ArtifactMetadata
from .engine_authority import BacktestEngineAuthorityFactory
from .results import (
    ResultValidationError,
    _cleanup_temp,
    _close_descriptors,
    _directory_flags,
    _open_directory_chain,
)


MAX_ENGINE_EVENTS = 4_096
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_GENERIC_EVENT_VALIDATOR = "engine-event-v1"
_NAUTILUS_BACKTEST_VALIDATOR = "nautilus-backtest-result-v1"
_NAUTILUS_SIMULATION_VALIDATOR = "nautilus-backtest-simulation-result-v1"
_NAUTILUS_SIMULATION_EVENT = "NautilusBacktestSimulationCompleted"


class EngineResultValidationError(ResultValidationError):
    """Engine stdout could not be proven canonical and request-attributed."""


@dataclass(frozen=True, slots=True)
class ValidatedEngineEventBatch:
    """Sealed event bytes plus an in-memory handoff for WS-02D."""

    artifact_type: str
    relative_ref: str
    sha256: str
    size_bytes: int
    media_type: str
    truncated: bool
    validator_id: str
    validation_metadata: dict[str, object]
    events: tuple[EngineEventEnvelope, ...]
    profile_result: P1ValidatedResult | None = None


class EngineResultValidator:
    """Validate captured stdout and seal the exact accepted JSONL bytes."""

    def __init__(
        self,
        artifact_root: Path,
        *,
        simulation_expected: Callable[[EngineCommandEnvelope], object] | None = None,
        p1_product_closure_sha256: str | None = None,
    ) -> None:
        if p1_product_closure_sha256 is not None and (
            type(p1_product_closure_sha256) is not str
            or _SHA256.fullmatch(p1_product_closure_sha256) is None
        ):
            raise ValueError("P1 product closure authority is invalid")
        self._artifact_root = Path(artifact_root).absolute()
        self._results_root = self._artifact_root / "engine-results"
        self._simulation_expected = simulation_expected
        self._p1_product_closure_sha256 = p1_product_closure_sha256

    def validate(
        self,
        validator_id: str,
        job: object,
        *,
        request: EngineCommandEnvelope,
        stdout: ArtifactMetadata,
        exit_code: int,
        progress: Callable[[], None] | None = None,
    ) -> ValidatedEngineEventBatch:
        if validator_id not in {
            _GENERIC_EVENT_VALIDATOR,
            _NAUTILUS_BACKTEST_VALIDATOR,
            _NAUTILUS_SIMULATION_VALIDATOR,
            P1_RESULT_VALIDATOR_ID,
        }:
            raise EngineResultValidationError(
                "engine result validator is not allowlisted"
            )
        if exit_code != 0:
            raise EngineResultValidationError("engine child exit code was not zero")
        if (
            type(job) is not ClaimedJob
            or job.job_type is not JobType.BACKTEST
            or type(job.payload)
            not in {EngineBacktestPayload, EngineBacktestSimulationPayload}
            or type(request) is not EngineCommandEnvelope
        ):
            raise EngineResultValidationError("engine result authority is invalid")
        try:
            expected_request = BacktestEngineAuthorityFactory(
                code_commit=request.source_commit,
                clock=lambda: request.event_time,
            ).from_claim(job)
        except (TypeError, ValueError) as exc:
            raise EngineResultValidationError(
                "engine request authority is invalid"
            ) from exc
        if request != expected_request:
            raise EngineResultValidationError(
                "engine request authority was not derived from this claim"
            )
        if (
            type(stdout) is not ArtifactMetadata
            or stdout.artifact_type != "stdout"
            or stdout.relative_ref
            != f"{job.job_id}/{job.attempt_id}/stdout.log"
            or stdout.media_type != "application/octet-stream"
            or stdout.validator_id != "bounded-stream-v1"
            or stdout.truncated is not False
            or isinstance(stdout.size_bytes, bool)
            or not isinstance(stdout.size_bytes, int)
            or stdout.size_bytes <= 0
            or stdout.size_bytes > MAX_STREAM_BYTES
            or not isinstance(stdout.sha256, str)
            or _SHA256.fullmatch(stdout.sha256) is None
        ):
            raise EngineResultValidationError(
                "engine stdout capture metadata is invalid"
            )

        check = progress or (lambda: None)
        check()
        raw = self._read_captured_stdout(job, stdout, check)
        events = self._parse_canonical_batch(
            raw,
            request,
            check,
            p1_event_stream=validator_id == P1_RESULT_VALIDATOR_ID,
        )
        has_nautilus_completion = any(
            event.payload.event_type
            in {"NautilusBacktestCompleted", _NAUTILUS_SIMULATION_EVENT}
            for event in events
        )
        has_p1_event = any(
            event.payload.event_type in P1_EVENT_FAMILIES for event in events
        )
        if validator_id == _GENERIC_EVENT_VALIDATOR and (
            has_nautilus_completion or has_p1_event
        ):
            raise EngineResultValidationError(
                "Nautilus completion requires the dedicated validator"
            )
        profile_result = None
        if validator_id == P1_RESULT_VALIDATOR_ID:
            if self._p1_product_closure_sha256 is None:
                raise EngineResultValidationError(
                    "P1 product closure authority is required"
                )
            try:
                profile_result = validate_p1_result(
                    request,
                    events,
                    raw=raw,
                    expected_closure_digest=self._p1_product_closure_sha256,
                )
            except (TypeError, ValueError) as exc:
                raise EngineResultValidationError(
                    "P1 Nautilus event stream is invalid"
                ) from exc
        if validator_id == _NAUTILUS_BACKTEST_VALIDATOR:
            if len(events) != 1:
                raise EngineResultValidationError(
                    "Nautilus backtest must emit exactly one completion event"
                )
            from packages.nautilus_backtest import (
                NautilusBacktestError,
                validate_isolated_backtest_result,
            )

            try:
                validate_isolated_backtest_result(request, events[0])
            except NautilusBacktestError as exc:
                raise EngineResultValidationError(
                    "Nautilus backtest result is not hash-bound and zero-order"
                ) from exc
        if validator_id == _NAUTILUS_SIMULATION_VALIDATOR:
            if len(events) != 1:
                raise EngineResultValidationError(
                    "Nautilus simulation must emit exactly one completion event"
                )
            if self._simulation_expected is None:
                raise EngineResultValidationError(
                    "Nautilus simulation oracle is unavailable"
                )
            self._validate_simulation_completion(
                request, events[0], self._simulation_expected(request)
            )
        check()
        return self._seal(
            job,
            request,
            raw,
            events,
            validator_id,
            profile_result=profile_result,
        )

    @staticmethod
    def _validate_simulation_completion(
        request: EngineCommandEnvelope, event: EngineEventEnvelope, expected: object
    ) -> None:
        from packages.nautilus_backtest import (
            BacktestExpectedOutcomeV1,
            NautilusBacktestError,
            validate_isolated_simulation_result,
        )

        try:
            if type(expected) is not BacktestExpectedOutcomeV1:
                raise NautilusBacktestError("simulation oracle result is invalid")
            validate_isolated_simulation_result(request, event, expected)
        except NautilusBacktestError as exc:
            raise EngineResultValidationError(
                "Nautilus simulation result does not prove parity"
            ) from exc

    def _read_captured_stdout(
        self,
        job: ClaimedJob,
        stdout: ArtifactMetadata,
        progress: Callable[[], None],
    ) -> bytes:
        root_fd = job_fd = attempt_fd = file_fd = -1
        try:
            root_fd = _open_directory_chain(self._artifact_root)
            job_fd = os.open(job.job_id, _directory_flags(), dir_fd=root_fd)
            attempt_fd = os.open(job.attempt_id, _directory_flags(), dir_fd=job_fd)
            file_fd = os.open(
                "stdout.log",
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=attempt_fd,
            )
            observed = os.fstat(file_fd)
            if (
                not stat.S_ISREG(observed.st_mode)
                or observed.st_uid != os.geteuid()
                or observed.st_nlink != 1
                or observed.st_size != stdout.size_bytes
                or observed.st_size > MAX_STREAM_BYTES
            ):
                raise OSError("captured stdout identity is unsafe")
            chunks: list[bytes] = []
            remaining = MAX_STREAM_BYTES + 1
            while remaining:
                progress()
                chunk = os.read(file_fd, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        except (OSError, ValueError) as exc:
            raise EngineResultValidationError(
                "engine stdout capture could not be read safely"
            ) from exc
        finally:
            try:
                _close_descriptors(file_fd, attempt_fd, job_fd, root_fd)
            except OSError as exc:
                raise EngineResultValidationError(
                    "engine stdout descriptors could not be closed safely"
                ) from exc
        if (
            len(raw) != stdout.size_bytes
            or hashlib.sha256(raw).hexdigest() != stdout.sha256
        ):
            raise EngineResultValidationError(
                "engine stdout capture metadata does not match bytes"
            )
        return raw

    @staticmethod
    def _parse_canonical_batch(
        raw: bytes,
        request: EngineCommandEnvelope,
        progress: Callable[[], None],
        *,
        p1_event_stream: bool = False,
    ) -> tuple[EngineEventEnvelope, ...]:
        if not raw.endswith(b"\n"):
            raise EngineResultValidationError(
                "engine stdout must be canonical newline-delimited JSON"
            )
        lines = raw[:-1].split(b"\n")
        if not lines or any(not line for line in lines) or len(lines) > MAX_ENGINE_EVENTS:
            raise EngineResultValidationError("engine event batch size is invalid")
        events: list[EngineEventEnvelope] = []
        try:
            for line in lines:
                progress()
                event = EngineEventEnvelope.model_validate_json(line)
                if canonical_json_bytes(event) != line:
                    raise EngineResultValidationError(
                        "engine event bytes are not canonical"
                    )
                events.append(event)
            batch = validate_envelope_batch(events)
        except EngineResultValidationError:
            raise
        except (ValidationError, TypeError, ValueError) as exc:
            reconciliation = "duplicate" in str(exc)
            raise EngineResultValidationError(
                "engine event batch is malformed or ambiguous",
                reconciliation_required=reconciliation,
            ) from exc

        for offset, event in enumerate(batch, start=1):
            if (
                event.engine_run_id != request.engine_run_id
                or event.correlation_id != request.correlation_id
                or event.causation_id
                != (request.causation_id if p1_event_stream else request.message_id)
                or event.stream_sequence != request.stream_sequence + offset
                or event.event_time < request.event_time
                or event.initialization_time != request.initialization_time
                or event.schema_version != request.schema_version
                or event.producer_identity != request.producer_identity
                or event.source_commit != request.source_commit
                or event.config_digest != request.config_digest
            ):
                raise EngineResultValidationError(
                    "engine event authority does not match the derived request"
                )
        return batch

    def _seal(
        self,
        job: ClaimedJob,
        request: EngineCommandEnvelope,
        raw: bytes,
        events: tuple[EngineEventEnvelope, ...],
        validator_id: str,
        *,
        profile_result: P1ValidatedResult | None = None,
    ) -> ValidatedEngineEventBatch:
        digest = hashlib.sha256(raw).hexdigest()
        components = (job.job_id, job.attempt_id)
        root_fd = current = -1
        try:
            root_fd = _open_directory_chain(self._results_root, create=True)
            root_info = os.fstat(root_fd)
            if root_info.st_uid != os.geteuid():
                raise OSError("sealed engine result root owner is unsafe")
            os.fchmod(root_fd, 0o700)
            current = root_fd
            for component in components:
                try:
                    os.mkdir(component, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
                os.fsync(current)
                child = os.open(component, _directory_flags(), dir_fd=current)
                if current != root_fd:
                    os.close(current)
                current = child
                info = os.fstat(current)
                if info.st_uid != os.geteuid():
                    raise OSError("sealed engine result directory owner is unsafe")
                os.fchmod(current, 0o700)

            filename = f"{digest}.jsonl"
            tempname = f".{digest}.{secrets.token_hex(12)}.tmp"
            descriptor = -1
            try:
                try:
                    existing = os.open(
                        filename,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=current,
                    )
                except FileNotFoundError:
                    existing = -1
                if existing >= 0:
                    try:
                        observed = os.fstat(existing)
                        existing_raw = os.read(existing, MAX_STREAM_BYTES + 1)
                        if (
                            not stat.S_ISREG(observed.st_mode)
                            or observed.st_uid != os.geteuid()
                            or existing_raw != raw
                        ):
                            raise OSError("sealed engine result collision")
                    finally:
                        os.close(existing)
                else:
                    descriptor = os.open(
                        tempname,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=current,
                    )
                    view = memoryview(raw)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise OSError("sealed engine result write made no progress")
                        view = view[written:]
                    os.fsync(descriptor)
                    os.close(descriptor)
                    descriptor = -1
                    os.rename(
                        tempname,
                        filename,
                        src_dir_fd=current,
                        dst_dir_fd=current,
                    )
                    os.fsync(current)
            finally:
                _cleanup_temp(current, tempname, descriptor)
        except (OSError, ValueError) as exc:
            raise EngineResultValidationError(
                "validated engine event batch could not be sealed"
            ) from exc
        finally:
            try:
                _close_descriptors(
                    current if current != root_fd else -1,
                    root_fd,
                )
            except OSError as exc:
                raise EngineResultValidationError(
                    "sealed engine result descriptors could not be closed safely"
                ) from exc

        first = events[0]
        last = events[-1]
        relative = "/".join(
            ("engine-results", *components, f"{digest}.jsonl")
        )
        metadata: dict[str, object] = {
            "attempt_id": job.attempt_id,
            "config_digest": request.config_digest,
            "engine_run_id": str(request.engine_run_id),
            "event_count": len(events),
            "first_sequence": first.stream_sequence,
            "job_id": job.job_id,
            "last_sequence": last.stream_sequence,
            "request_message_id": str(request.message_id),
            "source_commit": request.source_commit,
            "validator_id": validator_id,
        }
        if profile_result is not None:
            if profile_result.batch_sha256 != digest:
                raise EngineResultValidationError(
                    "P1 result digest does not match the sealed batch"
                )
            metadata.update(
                {
                    "engine_upstream_commit": P1_UPSTREAM_COMMIT,
                    "engine_version": P1_ENGINE_VERSION,
                    "fees": str(profile_result.fees),
                    "fill_count": profile_result.fill_count,
                    "final_cash": str(profile_result.final_cash),
                    "final_position": str(profile_result.final_position),
                    "order_count": profile_result.order_count,
                    "p1_product_closure_sha256": (
                        profile_result.product_closure_sha256
                    ),
                    "realized_pnl": str(profile_result.realized_pnl),
                    "runtime_family": P1_RUNTIME_FAMILY,
                    "semantic_digest": profile_result.semantic_sha256,
                    "target_count": profile_result.target_count,
                    "unrealized_pnl": str(profile_result.unrealized_pnl),
                }
            )
        return ValidatedEngineEventBatch(
            "engine_event_batch",
            relative,
            digest,
            len(raw),
            "application/x-ndjson",
            False,
            validator_id,
            metadata,
            events,
            profile_result,
        )


__all__ = [
    "EngineResultValidationError",
    "EngineResultValidator",
    "MAX_ENGINE_EVENTS",
    "ValidatedEngineEventBatch",
]

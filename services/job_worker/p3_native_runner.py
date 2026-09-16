"""Pinned native command and bounded, parent-owned six-replica execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TYPE_CHECKING

from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes, payload_digest

if TYPE_CHECKING:
    from packages.alpha_lifecycle.holdout_view import HoldoutCalculationView
    from packages.alpha_lifecycle.native_request import NativeRole
    from packages.alpha_lifecycle.replica_store import ArtifactStore
    from .p3_native_spawn import P3NativeSpawnProvider
    from .process_runner import HeartbeatDecision, HeartbeatInstruction, ProcessOutcome
    from .recovery import ProcessIdentity
    from .safety_state import SafetyEvidence


PINNED_ENGINE_VERSION = "1.231.0"


def native_parity_command(python: Path, release: Path) -> tuple[str, ...]:
    if not python.is_absolute() or not release.is_absolute():
        raise ValueError("native adapter paths must be absolute protected paths")
    return (str(python), "-I", "-B", str(release / "scripts/run_p3_nautilus_parity.py"))


@dataclass(frozen=True, slots=True)
class NativeReplica:
    """Parent observations; not a public replay receipt or SQL success proof."""
    role: NativeRole
    replica: Literal['R1', 'R2', 'R3']
    request_sha256: str
    started_at: datetime
    completed_at: datetime
    outcome: ProcessOutcome
    result_ref: ArtifactRefV1


class NativeExecutionError(RuntimeError):
    """Preserve observed failure context for the owning worker's finalization."""
    def __init__(self, message: str, *, completed: tuple[NativeReplica, ...],
        outcome: ProcessOutcome | None, artifact_root: Path) -> None:
        super().__init__(message)
        self.completed = completed
        self.outcome = outcome
        self.artifact_root = artifact_root


def run_native_replicas(provider: P3NativeSpawnProvider, *, view: HoldoutCalculationView, output: ArtifactStore,
    artifact_root: Path, heartbeat: Callable[[ProcessIdentity], HeartbeatDecision | HeartbeatInstruction],
    preflight: Callable[[], SafetyEvidence],
) -> tuple[NativeReplica, ...]:
    """Run both roles once, retaining only validated results through the fenced owner.

    Each child reports its actual identity to the caller's SQL heartbeat. The
    caller must support official process replacement; fixture SQL is not used.
    """
    from packages.alpha_lifecycle.native_request import native_result_from_output
    from .artifacts import ArtifactWriter
    from .p3_native_spawn import P3NativeSpawnProvider
    from .process_runner import ProcessRunner
    from .results import ResultValidator
    if type(provider) is not P3NativeSpawnProvider:
        raise ValueError('exact protected native launch owner required')
    with provider._lock:
        if provider._execution_started or provider._next or provider._failed:
            raise ValueError('native execution is single-use and requires an unused launch owner')
        provider._execution_started = True
    runs: list[NativeReplica] = []
    succeeded = False
    outcome: ProcessOutcome | None = None
    root = artifact_root
    try:
        for request in provider._requests:
            for replica in ('R1', 'R2', 'R3'):
                outcome = None
                provider._fence()
                root = artifact_root / request.role / replica
                observed: list[ProcessIdentity] = []
                def observe(identity: ProcessIdentity) -> HeartbeatDecision | HeartbeatInstruction:
                    provider._fence()
                    if ((observed and identity != observed[0])
                        or any((r.outcome.identity.pid, r.outcome.identity.start_ticks)
                            == (identity.pid, identity.start_ticks) for r in runs)):
                        raise ValueError('native execution requires six fresh parent-observed processes')
                    if not observed:
                        observed.append(identity)
                    return heartbeat(identity)

                def safety() -> SafetyEvidence:
                    provider._fence()
                    return preflight()

                provider._last_launch = None
                started = datetime.now(UTC)
                outcome = ProcessRunner(ArtifactWriter(root)).run(
                    lambda: provider.prepare(request.role, replica), None, None, observe,
                    job_id=provider._claim.job_id, attempt_id=provider._claim.attempt_id, preflight=safety)
                completed = datetime.now(UTC)
                provider._fence()
                if (outcome.exit_code != 0 or outcome.termination_reason is not None
                    or observed != [outcome.identity] or completed < started
                    or outcome.result_validator_id != 'p3-native-output-v1'
                    or outcome.backend_revision != request.source.commit_sha
                    or provider._last_launch != (outcome.capability_fingerprint, outcome.lineage.command)):
                    raise ValueError('native child outcome differs from its consumed launch or failed')
                reader = ResultValidator(root, root, root)
                raw = reader._read_p3_stream(provider._claim, outcome.stdout)
                _ = reader._read_p3_stream(provider._claim, outcome.stderr, stream_name='stderr')
                provider._fence()
                result = native_result_from_output(request, raw, view, output)
                provider._fence()
                result_ref = output.put_bytes(canonical_json_bytes(result), media_type='application/json')
                runs.append(NativeReplica(request.role, replica, payload_digest(request),
                    started, completed, outcome, result_ref))
        provider._fence()
        succeeded = True
        provider._completed_runs = tuple(runs)
        return tuple(runs)
    except Exception as error:
        raise NativeExecutionError(str(error), completed=tuple(runs), outcome=outcome,
            artifact_root=root) from error
    finally:
        if not succeeded:
            with provider._lock:
                provider._failed = True


def retain_native_proof(provider: P3NativeSpawnProvider, runs: tuple[NativeReplica, ...],
    store: ArtifactStore,
) -> tuple[ArtifactRefV1, tuple[ArtifactRefV1, ...]]:
    """Issue receipts only for the exact completed execution owned by this provider."""
    from packages.alpha_lifecycle.contracts.results import ExecutableResult, ReplayReceipt
    from packages.alpha_lifecycle.parity import NativeObservation, NativeParentProof, validate_native_proof
    from packages.alpha_lifecycle.replica_store import _read
    from .p3_native_spawn import P3NativeSpawnProvider, _ORDER
    if type(provider) is not P3NativeSpawnProvider:
        raise ValueError('exact native launch owner required')
    with provider._lock:
        if (provider._failed or provider._proof_retained or len(runs) != 6
            or runs != provider._completed_runs
            or tuple((run.role, run.replica) for run in runs) != _ORDER):
            raise ValueError('native proof requires the exact single-use completed execution')
        provider._proof_retained = True
    records = []
    try:
        for run in runs:
            provider._fence()
            request = provider._requests[run.role != 'PRIMARY']
            outcome = run.outcome
            command = outcome.lineage.command
            result = _read(store, run.result_ref, ExecutableResult)
            value = dict(schema_version='p3-replay-receipt-v1', logical_trial_id='p3-native-parity-v1',
                replicate=run.replica, manifest_digest=request.manifest_ref.content_sha256,
                result_ref=run.result_ref, source=request.source, environment_ref=request.environment_ref,
                sandbox_policy_digest=command['os_sandbox_profile_sha256'],
                started_at=run.started_at.isoformat().replace('+00:00', 'Z'),
                completed_at=run.completed_at.isoformat().replace('+00:00', 'Z'), process_exit=0, network_denied=True,
                output_inventory_digest=payload_digest((run.result_ref, result.fill_trace_ref)))
            receipt = ReplayReceipt.model_validate({**value, 'digest': payload_digest(value)})
            provider._fence()
            ref = store.put_bytes(canonical_json_bytes(receipt), media_type='application/json')
            records.append(NativeObservation.model_validate(dict(role=run.role, replicate=run.replica,
                request_sha256=run.request_sha256, pid=outcome.identity.pid,
                process_group=outcome.identity.process_group, start_ticks=outcome.identity.start_ticks,
                command_fingerprint=outcome.identity.command_fingerprint,
                capability_fingerprint=outcome.capability_fingerprint,
                closure_sha256=command['engine_closure_sha256'],
                sandbox_policy_sha256=command['os_sandbox_profile_sha256'], receipt_ref=ref)))
        claim = provider._claim
        value = dict(schema_version='p3-native-parent-proof-v1', source=provider._profile.source,
            session_sha256=provider._session_digest, profile_sha256=provider._profile_digest,
            job_id=claim.job_id, attempt_id=claim.attempt_id, worker_id=claim.worker_id,
            lease_token_sha256=claim.lease_token_sha256,
            commitment_ref=provider._profile.native_commitment_ref, runs=tuple(records))
        proof = NativeParentProof.model_validate({**value, 'digest': payload_digest(value)})
        provider._fence()
        ref = store.put_bytes(canonical_json_bytes(proof), media_type='application/json')
        _ = validate_native_proof(ref, manifest_ref=provider._commitment.manifest_ref,
            instrument_spec_ref=provider._commitment.instrument_spec_ref, store=store)
        provider._fence()
        return ref, tuple(record.receipt_ref for record in records)
    except BaseException:
        with provider._lock:
            provider._failed = True
        raise


__all__ = ["PINNED_ENGINE_VERSION", "native_parity_command", "run_native_replicas",
    "NativeReplica", "NativeExecutionError"]

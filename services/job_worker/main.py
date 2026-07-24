"""Worker composition root. Importing this module has no runtime side effects."""

from __future__ import annotations

import os
import socket
import time
from typing import Mapping

from services.job_store.config import JobStoreSettings
from services.job_store.worker_repository import WorkerRepository
from .artifacts import ArtifactWriter
from .command_registry import attest_worker_runtime_authority
from .environment import ResearchEnvironmentSettings
from .process_runner import ProcessRunner
from .recovery import ProcProcessInspector
from .results import ResultValidator
from .safety_state import AuthorityBoundSafetyPreflight, SafetyStateClient
from .worker import JobWorker, WORKER_LEASE_SECONDS

EXPECTED_DATABASE_REVISION = "0006_job_transition_database_authority"
_FORBIDDEN_AUTHORITY_KEYS = frozenset({
    "TRADING_APP_MANIFEST_SHA256",
    "TRADING_BACKEND_MANIFEST_SHA256",
    "TRADING_COMMAND_MANIFEST_SHA256",
    "TRADING_SEMANTIC_MANIFEST_SHA256",
    "TRADING_SAFETY_EXPORTER_COMMIT",
    "TRADING_CODE_COMMIT",
})


def build_worker(repository: WorkerRepository, source: Mapping[str, str] | None = None) -> JobWorker:
    values = os.environ if source is None else source
    if _FORBIDDEN_AUTHORITY_KEYS.intersection(values):
        raise ValueError("runtime authority cannot be supplied through environment digests")
    if "TRADING_WORKER_LEASE_SECONDS" in values:
        raise ValueError("worker lease is code-owned and cannot be overridden")
    authority = attest_worker_runtime_authority()
    runtime_authority = authority.runtime_authority
    environment = ResearchEnvironmentSettings.from_authority(
        runtime_authority, values
    )
    runtime_paths = authority.runtime_paths
    safety = SafetyStateClient(
        authority.safety_snapshot_path,
        expected_exporter_commit=authority.safety_exporter_commit,
        expected_source_fingerprint=authority.safety_source_fingerprint,
        protected_root_owned=True,
    )
    safety_preflight = AuthorityBoundSafetyPreflight(authority, safety)
    # Fail before the worker can recover leases, claim, or construct a runner.
    safety_preflight()
    worker_id = values.get("TRADING_WORKER_ID") or f"worker-{socket.gethostname()}"
    code_commit = authority.application_revision
    return JobWorker(
        repository,
        ProcessRunner(ArtifactWriter(runtime_paths.artifact_root)),
        ResultValidator(
            runtime_paths.reports_root,
            runtime_paths.signals_root,
            runtime_paths.artifact_root,
        ),
        worker_id=worker_id,
        code_commit=code_commit,
        environment=environment,
        safety_preflight=safety_preflight,
        lease_seconds=WORKER_LEASE_SECONDS,
    )


def main() -> int:
    idle_seconds = float(os.environ.get("TRADING_WORKER_IDLE_SECONDS", "1"))
    settings = JobStoreSettings.from_env(expected_user="trading_job_worker")
    with WorkerRepository(settings) as repository:
        repository.assert_runtime_identity(
            expected_user="trading_job_worker",
            expected_revision=EXPECTED_DATABASE_REVISION,
        )
        serve(repository, idle_seconds=idle_seconds)


def serve(repository: WorkerRepository, *, idle_seconds: float) -> None:
    """Recover crash leftovers once, then enter the single-worker claim loop.

    Recovery exceptions intentionally escape and stop the service.  Claiming a
    new job without first resolving expired child identity could create a
    duplicate research process after a restart.
    """

    worker = build_worker(repository)
    repository.recover_expired_leases(
        ProcProcessInspector(), recovery_id="worker-startup-recovery"
    )
    while True:
        if not worker.run_once():
            time.sleep(idle_seconds)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

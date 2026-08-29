"""Worker composition root. Importing this module has no runtime side effects."""

from __future__ import annotations

import os
import socket
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Mapping, NoReturn

from services.job_store.config import CANONICAL_DATABASE_REVISION, JobStoreSettings
from services.job_store.worker_repository import WorkerRepository
from .artifacts import ArtifactWriter
from .command_registry import attest_worker_runtime_authority
from .environment import ResearchEnvironmentSettings
from .process_runner import ProcessRunner
from .recovery import ProcProcessInspector
from .results import ResultValidator
from .safety_state import AuthorityBoundSafetyPreflight, SafetyStateClient
from .worker import (
    JobWorker,
    P1PortfolioParityVerifier,
    P1ProjectionAuthorityFactory,
    WORKER_LEASE_SECONDS,
)

if TYPE_CHECKING:
    from pathlib import Path

    from .command_registry import P1StagingSafetyAuthorityRefresher
    from .command_registry import WorkerRuntimeAuthority
    from .engine_artifacts import EngineArtifactBinding
    from .nautilus_closure import NautilusClosureConfig

EXPECTED_DATABASE_REVISION = CANONICAL_DATABASE_REVISION
_FORBIDDEN_AUTHORITY_KEYS = frozenset({
    "TRADING_APP_MANIFEST_SHA256",
    "TRADING_BACKEND_MANIFEST_SHA256",
    "TRADING_COMMAND_MANIFEST_SHA256",
    "TRADING_SEMANTIC_MANIFEST_SHA256",
    "TRADING_SAFETY_EXPORTER_COMMIT",
    "TRADING_CODE_COMMIT",
})


def build_worker(
    repository: WorkerRepository,
    source: Mapping[str, str] | None = None,
    *,
    authority: WorkerRuntimeAuthority | None = None,
    engine_spawn_provider: object | None = None,
    engine_result_validator: object | None = None,
    engine_event_ingestor: object | None = None,
    p1_projection_authority_factory: P1ProjectionAuthorityFactory | None = None,
    p1_portfolio_parity_verifier: P1PortfolioParityVerifier | None = None,
    _p1_safety_authority_refresher: P1StagingSafetyAuthorityRefresher | None = None,
) -> JobWorker:
    values = os.environ if source is None else source
    if _FORBIDDEN_AUTHORITY_KEYS.intersection(values):
        raise ValueError("runtime authority cannot be supplied through environment digests")
    if "TRADING_WORKER_LEASE_SECONDS" in values:
        raise ValueError("worker lease is code-owned and cannot be overridden")
    if (engine_spawn_provider is None) != (engine_event_ingestor is None):
        raise ValueError(
            "engine spawn and durable-ingestion authority must be injected together"
        )
    if engine_result_validator is not None and engine_spawn_provider is None:
        raise ValueError("engine result validation requires complete engine authority")
    if (p1_projection_authority_factory is None) != (
        p1_portfolio_parity_verifier is None
    ):
        raise ValueError("complete P1 portfolio parity authority is required")
    if p1_projection_authority_factory is not None and (
        engine_spawn_provider is None
        or engine_result_validator is None
        or engine_event_ingestor is None
    ):
        raise ValueError("P1 portfolio parity requires complete engine authority")
    if _p1_safety_authority_refresher is not None:
        from .command_registry import P1StagingSafetyAuthorityRefresher

        if (
            type(_p1_safety_authority_refresher)
            is not P1StagingSafetyAuthorityRefresher
        ):
            raise TypeError("exact P1 staging safety refresher is required")
    if _p1_safety_authority_refresher is not None and (
        engine_spawn_provider is None
        or engine_result_validator is None
        or engine_event_ingestor is None
        or p1_projection_authority_factory is None
        or p1_portfolio_parity_verifier is None
    ):
        raise ValueError(
            "P1 safety refresh requires complete P1 engine authority"
        )
    selected_authority = authority or attest_worker_runtime_authority()
    runtime_authority = selected_authority.runtime_authority
    environment = ResearchEnvironmentSettings.from_authority(
        runtime_authority, values
    )
    runtime_paths = selected_authority.runtime_paths
    if _p1_safety_authority_refresher is None:
        safety_authority = getattr(runtime_authority, "safety", None)
        safety = SafetyStateClient(
            selected_authority.safety_snapshot_path,
            expected_exporter_commit=selected_authority.safety_exporter_commit,
            expected_source_fingerprint=selected_authority.safety_source_fingerprint,
            expected_owner_uid=getattr(safety_authority, "expected_owner_uid", 0),
            protected_root_owned=getattr(
                safety_authority, "protected_root_owned", True
            ),
        )
        safety_preflight = AuthorityBoundSafetyPreflight(selected_authority, safety)
    else:
        current_authority = selected_authority

        def safety_preflight():
            nonlocal current_authority
            current_authority = _p1_safety_authority_refresher.refresh(
                current_authority
            )
            current_safety_authority = getattr(
                current_authority.runtime_authority, "safety", None
            )
            current_client = SafetyStateClient(
                current_authority.safety_snapshot_path,
                expected_exporter_commit=current_authority.safety_exporter_commit,
                expected_source_fingerprint=current_authority.safety_source_fingerprint,
                expected_owner_uid=getattr(
                    current_safety_authority, "expected_owner_uid", 0
                ),
                protected_root_owned=getattr(
                    current_safety_authority, "protected_root_owned", True
                ),
            )
            return AuthorityBoundSafetyPreflight(
                current_authority, current_client
            )()

    # Fail before the worker can recover leases, claim, or construct a runner.
    safety_preflight()
    worker_id = values.get("TRADING_WORKER_ID") or f"worker-{socket.gethostname()}"
    code_commit = selected_authority.application_revision
    engine_authority_factory = None
    selected_engine_result_validator = None
    if engine_spawn_provider is not None:
        # Resolve engine-only types solely for an explicitly injected complete
        # engine composition. The paper projection never enters this branch.
        from .engine_authority import BacktestEngineAuthorityFactory
        from .engine_results import EngineResultValidator

        engine_authority_factory = BacktestEngineAuthorityFactory(
            code_commit=code_commit,
            clock=lambda: datetime.now(UTC),
        )
        selected_engine_result_validator = (
            engine_result_validator
            if engine_result_validator is not None
            else EngineResultValidator(runtime_paths.artifact_root)
        )
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
        engine_authority_factory=engine_authority_factory,
        engine_spawn_provider=engine_spawn_provider,
        engine_result_validator=selected_engine_result_validator,
        engine_event_ingestor=engine_event_ingestor,
        p1_projection_authority_factory=p1_projection_authority_factory,
        p1_portfolio_parity_verifier=p1_portfolio_parity_verifier,
        lease_seconds=WORKER_LEASE_SECONDS,
    )


def build_p1_worker(
    repository: WorkerRepository,
    source: Mapping[str, str] | None = None,
    *,
    closure_config: NautilusClosureConfig,
    transport_root: Path,
    artifact_bindings: tuple[EngineArtifactBinding, ...],
    p1_projection_authority_factory: P1ProjectionAuthorityFactory,
    safety_authority_refresher: P1StagingSafetyAuthorityRefresher | None = None,
    authority: WorkerRuntimeAuthority | None = None,
) -> JobWorker:
    """Compose the fixed P1 lane from deployment-owned authority inputs."""

    from .engine_artifacts import HashBoundArtifactResolver
    from .engine_profiles import P1_REAL_BACKTEST_POLICY
    from .engine_results import EngineResultValidator
    from .p1_engine_spawn import P1EngineSpawnProvider
    from .p1_nautilus_closure import attest_p1_nautilus_closure
    from packages.engine_portfolio_projection.parity import verify_p1_portfolio_parity

    if p1_projection_authority_factory is None:
        raise ValueError("P1 portfolio projection authority factory is required")

    selected_authority = authority or attest_worker_runtime_authority()
    profile = P1_REAL_BACKTEST_POLICY
    provider = P1EngineSpawnProvider(
        transport_root=transport_root,
        attest_closure=lambda: attest_p1_nautilus_closure(closure_config),
        expected_manifest_schema_version=profile.manifest_schema_version,
        profile_policy=profile,
        attest_inputs=HashBoundArtifactResolver(artifact_bindings),
        monotonic_ns=time.monotonic_ns,
    )
    return build_worker(
        repository,
        source,
        authority=selected_authority,
        engine_spawn_provider=provider,
        engine_result_validator=EngineResultValidator(
            selected_authority.runtime_paths.artifact_root,
            p1_product_closure_sha256=profile.closure_sha256,
        ),
        engine_event_ingestor=repository.engine_event_ingestor(),
        p1_projection_authority_factory=p1_projection_authority_factory,
        p1_portfolio_parity_verifier=verify_p1_portfolio_parity,
        _p1_safety_authority_refresher=safety_authority_refresher,
    )


def main() -> int:
    values = os.environ
    authority = attest_worker_runtime_authority()
    idle_seconds = float(values.get("TRADING_WORKER_IDLE_SECONDS", "1"))
    settings = (
        JobStoreSettings.from_systemd_credentials(
            expected_user="trading_job_worker"
        )
        if "CREDENTIALS_DIRECTORY" in values
        else JobStoreSettings.from_env(
            expected_user="trading_job_worker"
        )
    )
    with WorkerRepository(settings) as repository:
        repository.assert_runtime_identity(
            expected_user="trading_job_worker",
            expected_revision=EXPECTED_DATABASE_REVISION,
        )
        serve(repository, idle_seconds=idle_seconds, authority=authority)


def serve(
    repository: WorkerRepository,
    *,
    idle_seconds: float,
    authority: WorkerRuntimeAuthority | None = None,
    engine_spawn_provider: object | None = None,
) -> NoReturn:
    """Recover crash leftovers once, then enter the single-worker claim loop.

    Recovery exceptions intentionally escape and stop the service.  Claiming a
    new job without first resolving expired child identity could create a
    duplicate research process after a restart.
    """

    engine_event_ingestor = (
        repository.engine_event_ingestor()
        if engine_spawn_provider is not None
        else None
    )
    if authority is None and engine_spawn_provider is None:
        worker = build_worker(repository)
    else:
        worker = build_worker(
            repository,
            authority=authority,
            engine_spawn_provider=engine_spawn_provider,
            engine_event_ingestor=engine_event_ingestor,
        )
    repository.recover_expired_leases(
        ProcProcessInspector(), recovery_id="worker-startup-recovery"
    )
    while True:
        if not worker.run_once():
            time.sleep(idle_seconds)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

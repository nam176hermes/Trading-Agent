from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.job_contracts import JobState, JobType, SnapshotPayload
from packages.runtime_release import RuntimeAuthorityV2, RuntimePathsV2
from services.job_store.worker_repository import ClaimedJob, WorkerRepository
from services.job_worker.artifacts import ArtifactMetadata
from services.job_worker.process_runner import (
    HeartbeatDecision,
    HeartbeatInstruction,
    ProcessOutcome,
    ProcessLineage,
)
from services.job_worker.recovery import ProcessIdentity
from services.job_worker.results import ResultValidationError, ValidatedResult
from services.job_worker.errors import SafetyBlockedError
from services.job_worker.safety import KillSwitchState, SafetyMode
from services.job_worker.safety_state import SafetyEvidence
from services.job_worker.worker import (
    JobWorker,
    WORKER_LEASE_SECONDS,
    WorkerControl,
)
from tests.jobs.backend_contract_fixtures import BACKEND_COMMIT


def claim(*, attempt_number=1, max_attempts=2):
    return ClaimedJob(
        job_id="job-1", job_type=JobType.SNAPSHOT,
        payload=SnapshotPayload(scope="default", requested_as_of=None),
        attempt_id="attempt-1", attempt_number=attempt_number,
        worker_id="worker-1", lease_token="secret",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        max_attempts=max_attempts,
    )


def artifact(kind):
    return ArtifactMetadata(kind, f"job-1/attempt-1/{kind}.log", "a" * 64, 3, "application/octet-stream", False)


def safety_evidence(digest: str) -> SafetyEvidence:
    generated = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    return SafetyEvidence(
        requested_mode=SafetyMode.PAPER,
        effective_mode=SafetyMode.PAPER,
        live_execution_enabled=False,
        live_trading_approved=False,
        kill_switch_state=KillSwitchState.INACTIVE,
        snapshot_sha256=digest,
        generated_at=generated,
        expires_at=generated + timedelta(seconds=6),
    )


def runtime_paths() -> RuntimePathsV2:
    return RuntimePathsV2(
        safety_snapshot=Path("/run/trading-agent-v2/safety-state.json"),
        semantic_authority=Path(
            "/etc/trading-agent-v2/research-input-manifests/active.json"
        ),
        semantic_input_root=Path("/var/lib/trading-agent-v2/research-input"),
        reports_root=Path("/var/lib/trading-agent-v2/research-output/reports"),
        signals_root=Path("/var/lib/trading-agent-v2/research-output/signals"),
        scratch_root=Path("/var/lib/trading-agent-v2/research-home"),
        artifact_root=Path("/var/lib/trading-agent-v2/job-artifacts"),
    )


def runtime_authority(paths: RuntimePathsV2) -> RuntimeAuthorityV2:
    authority = object.__new__(RuntimeAuthorityV2)
    object.__setattr__(authority, "runtime_paths", paths)
    return authority


def outcome(reason=None, exit_code=0, *, safety_reason_code=None):
    return ProcessOutcome(
        exit_code, reason, ProcessIdentity(1, 1, 1, "b" * 64),
        artifact("stdout"), artifact("stderr"), "c" * 64, "legacy-report-v1",
        BACKEND_COMMIT,
        ProcessLineage(
            {
                "authority_document_sha256": "d" * 64,
                "backend_manifest_sha256": "e" * 64,
                "semantic_policy_sha256": "f" * 64,
                "semantic_active_authority_sha256": "1" * 64,
                "semantic_version_manifest_sha256": "2" * 64,
                "semantic_input_fingerprint": "3" * 64,
                "semantic_manifest_version": "semantic-v1",
                "semantic_generated_at": "2026-07-16T12:00:00+00:00",
                "semantic_expires_at": "2026-07-16T12:30:00+00:00",
            },
            {},
            {},
        ),
        safety_reason_code,
    )


class Repository:
    def __init__(self, claimed=None, controls=(WorkerControl.CONTINUE,), *, pre_spawn_controls=(WorkerControl.CONTINUE,), finalize_result=True, start_result=True):
        self.claimed = claimed
        self.controls = iter(controls)
        self.pre_spawn_controls = iter(pre_spawn_controls)
        self.finalize_result = finalize_result
        self.start_result = start_result
        self.calls = []

    def worker_heartbeat(self, *args, **kwargs): self.calls.append(("worker_heartbeat", args, kwargs))
    def claim_next(self, *args, **kwargs): self.calls.append(("claim", args, kwargs)); value, self.claimed = self.claimed, None; return value
    def start_attempt(self, *args): self.calls.append(("start", args)); return self.start_result
    def pre_spawn_control(self, *args): self.calls.append(("pre_spawn_control", args)); return next(self.pre_spawn_controls, WorkerControl.CONTINUE)
    def heartbeat_control(self, *args): self.calls.append(("heartbeat", args)); return next(self.controls, WorkerControl.CONTINUE)
    def finalize_execution(self, *args, **kwargs): self.calls.append(("finalize", args, kwargs)); return self.finalize_result
    def finalize_retry(self, *args, **kwargs): self.calls.append(("retry", args, kwargs)); return self.finalize_result


class Runner:
    def __init__(self, result): self.result = result; self.decisions = []
    def run(self, prepared, environment, timeout_seconds, heartbeat, **ids):
        self.decisions.append(heartbeat(self.result.identity))
        return self.result


class BlockingRunner:
    def run(self, *args, **kwargs):
        from services.job_worker.errors import SafetyBlockedError
        raise SafetyBlockedError("SAFETY_DRIFT_BEFORE_SPAWN", "drift")


class Validator:
    def __init__(self, value=None, error=None): self.value, self.error, self.calls = value, error, []
    def validate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error: raise self.error
        return self.value or ValidatedResult("result", "report.json", "d" * 64, 10, "application/json", False, "legacy-report-v1", {})


def worker(
    repository,
    runner,
    validator=Validator(),
    safety=lambda: safety_evidence("4" * 64),
):
    return JobWorker(
        repository, runner, validator, worker_id="worker-1", code_commit="e" * 40,
        environment=object(), safety_preflight=safety,
        prepare_spawn=lambda job: object(), lease_seconds=WORKER_LEASE_SECONDS,
        clock=lambda: datetime(2026, 7, 12, tzinfo=UTC),
    )


def test_run_once_claims_starts_heartbeats_validates_and_atomically_finalizes_success() -> None:
    repository = Repository(claim())
    process = Runner(outcome())
    validator = Validator()
    assert worker(repository, process, validator).run_once() is True
    assert process.decisions == [HeartbeatDecision.CONTINUE]
    assert validator.calls[0][1]["backend_commit"] == BACKEND_COMMIT
    assert validator.calls[0][1]["semantic_input_fingerprint"] == "3" * 64
    claim_call = [call for call in repository.calls if call[0] == "claim"][0]
    assert claim_call[2]["allowed_job_types"] == (JobType.SNAPSHOT,)
    final = [call for call in repository.calls if call[0] == "finalize"][0]
    assert final[2]["final_state"] is JobState.SUCCEEDED
    assert final[2]["result"].sha256 == "d" * 64
    assert len(final[2]["stream_artifacts"]) == 2


def test_worker_repository_claim_authority_defaults_to_snapshot_only() -> None:
    default = signature(WorkerRepository.claim_next).parameters[
        "allowed_job_types"
    ].default

    assert default == (JobType.SNAPSHOT,)


def test_current_authority_and_safety_preflight_precedes_any_claim_write() -> None:
    events: list[str] = []

    class OrderedRepository(Repository):
        def worker_heartbeat(self, *args, **kwargs):
            events.append("heartbeat")
            return super().worker_heartbeat(*args, **kwargs)

        def claim_next(self, *args, **kwargs):
            events.append("claim")
            return super().claim_next(*args, **kwargs)

    repository = OrderedRepository(None)

    assert worker(
        repository,
        Runner(outcome()),
        safety=lambda: events.append("authority+safety")
        or safety_evidence("4" * 64),
    ).run_once() is False

    assert events[0] == "authority+safety"
    assert events.index("authority+safety") < events.index("heartbeat")
    assert events.index("authority+safety") < events.index("claim")


def test_worker_lease_is_code_owned_and_exceeds_three_scan_rollout_budget() -> None:
    assert WORKER_LEASE_SECONDS == 600
    assert WORKER_LEASE_SECONDS > 3 * 120

    with pytest.raises(ValueError, match="code-owned"):
        JobWorker(
            Repository(),
            Runner(outcome()),
            Validator(),
            worker_id="worker-1",
            code_commit="e" * 40,
            environment=object(),
            safety_preflight=lambda: safety_evidence("4" * 64),
            lease_seconds=30,
        )


@pytest.mark.parametrize(
    "job_types",
    [
        (JobType.DEBATE,),
        (JobType.REPLAY,),
        (JobType.BACKTEST,),
        (JobType.SNAPSHOT, JobType.DEBATE),
        (),
    ],
)
def test_worker_repository_rejects_inactive_claim_authority_before_query(
    job_types,
) -> None:
    repository = object.__new__(WorkerRepository)
    repository._pool = SimpleNamespace(
        connection=lambda: pytest.fail("inactive job authority reached PostgreSQL")
    )

    with pytest.raises(ValueError, match="job-type authority"):
        repository.claim_next(
            "worker-1",
            30,
            "claim:inactive",
            allowed_job_types=job_types,
        )


def test_repository_result_metadata_persists_exact_sanitized_lineage() -> None:
    repository = object.__new__(WorkerRepository)
    captured = {}
    repository.finalize = lambda *args, **kwargs: captured.update(kwargs) or True
    process_outcome = outcome()
    result = ValidatedResult(
        "result", "results/job-1/attempt-1/report.json", "d" * 64, 10,
        "application/json", False, "legacy-report-v1",
        {"semantic_input_fingerprint": "3" * 64},
    )

    assert repository.finalize_execution(
        claim(),
        expected_state=JobState.RUNNING,
        expected_attempt_outcome="RUNNING",
        final_state=JobState.SUCCEEDED,
        reason_code="RESULT_VALIDATED",
        trace_id="lineage:test",
        outcome=process_outcome,
        result=result,
        stream_artifacts=(process_outcome.stdout, process_outcome.stderr),
    )

    assert captured["result_metadata"]["lineage"] == process_outcome.lineage.as_metadata()
    assert (
        captured["result_metadata"]["lineage"]["command"]
        ["semantic_input_fingerprint"]
        == "3" * 64
    )


def test_completion_event_persists_only_sanitized_lineage_and_digest() -> None:
    process_lineage = outcome().lineage.as_metadata()
    process_lineage["secret"] = "must-not-persist"
    process_lineage["command"]["credential"] = "must-not-persist"
    metadata = {
        "lineage": process_lineage,
        "artifacts": [{"ref": "results/report.json"}],
    }

    event = WorkerRepository._completion_event_metadata(metadata)

    assert len(event["result_metadata_sha256"]) == 64
    assert event["lineage"] == outcome().lineage.as_metadata()
    assert "secret" not in event["lineage"]
    assert "credential" not in event["lineage"]["command"]


def test_long_job_lineage_retains_initial_and_latest_safe_heartbeat_evidence() -> None:
    repository = Repository(claim())
    snapshots = iter(
        safety_evidence(digit * 64) for digit in ("3", "4", "5", "6", "7")
    )

    assert worker(
        repository,
        Runner(outcome()),
        Validator(),
        safety=lambda: next(snapshots),
    ).run_once()

    final = [call for call in repository.calls if call[0] == "finalize"][0]
    persisted_outcome = final[2]["outcome"]
    assert persisted_outcome.lineage.safety_final["snapshot_sha256"] == "7" * 64


@pytest.mark.parametrize(
    ("reason", "control", "state"),
    [
        ("CANCELLED", WorkerControl.CANCEL, JobState.CANCELLED),
        ("SAFETY_DRIFT", WorkerControl.CONTINUE, JobState.BLOCKED),
        ("TIMEOUT", WorkerControl.CONTINUE, JobState.TIMED_OUT),
    ],
)
def test_termination_outcomes_are_reasoned(reason, control, state) -> None:
    repository = Repository(claim(), controls=(control,))
    process = Runner(outcome(reason, -9))
    assert worker(repository, process).run_once()
    final = [call for call in repository.calls if call[0] == "finalize"][0]
    assert final[2]["final_state"] is state


def test_stale_lease_never_finalizes() -> None:
    repository = Repository(claim(), controls=(WorkerControl.STALE,))
    process = Runner(outcome("STALE_LEASE", -15))
    assert worker(repository, process).run_once()
    assert process.decisions == [HeartbeatDecision.STALE_LEASE]
    assert not any(call[0] in {"finalize", "retry"} for call in repository.calls)


def test_validation_failure_retries_only_when_fixed_attempt_budget_remains() -> None:
    repository = Repository(claim(attempt_number=1, max_attempts=2))
    validation = Validator(error=ResultValidationError("missing"))
    assert worker(repository, Runner(outcome()), validation).run_once()
    assert any(call[0] == "retry" for call in repository.calls)


def test_ambiguous_result_blocks_reconciliation_instead_of_retry() -> None:
    repository = Repository(claim())
    validation = Validator(error=ResultValidationError("ambiguous", reconciliation_required=True))
    assert worker(repository, Runner(outcome()), validation).run_once()
    final = [call for call in repository.calls if call[0] == "finalize"][0]
    assert final[2]["final_state"] is JobState.BLOCKED
    assert final[2]["reason_code"] == "RESULT_RECONCILIATION_REQUIRED"


def test_no_claim_is_completed_resume_noop_and_persists_idle_heartbeat() -> None:
    repository = Repository(None)
    assert worker(repository, Runner(outcome())).run_once() is False
    assert [call[0] for call in repository.calls] == ["worker_heartbeat", "claim", "worker_heartbeat"]


def test_last_moment_preflight_drift_blocks_claim_without_starting() -> None:
    repository = Repository(claim())
    assert worker(repository, BlockingRunner()).run_once()
    assert not any(call[0] == "start" for call in repository.calls)
    final = [call for call in repository.calls if call[0] == "finalize"][0]
    assert final[2]["expected_state"] is JobState.CLAIMED
    assert final[2]["final_state"] is JobState.BLOCKED


def test_cancel_after_claim_before_start_finalizes_claimed_attempt_without_spawn() -> None:
    repository = Repository(claim(), pre_spawn_controls=(WorkerControl.CANCEL,))

    assert worker(repository, Runner(outcome())).run_once()

    assert not any(call[0] == "start" for call in repository.calls)
    final = [call for call in repository.calls if call[0] == "finalize"][0]
    assert final[2]["expected_state"] is JobState.CANCEL_REQUESTED
    assert final[2]["expected_attempt_outcome"] == "CLAIMED"
    assert final[2]["final_state"] is JobState.CANCELLED


def test_cancel_race_after_popen_and_before_start_finalizes_claimed_attempt() -> None:
    repository = Repository(
        claim(), start_result=False,
        pre_spawn_controls=(WorkerControl.CONTINUE, WorkerControl.CANCEL),
    )
    process = Runner(outcome("CANCELLED", -9))

    assert worker(repository, process).run_once()

    final = [call for call in repository.calls if call[0] == "finalize"][0]
    assert final[2]["expected_state"] is JobState.CANCEL_REQUESTED
    assert final[2]["expected_attempt_outcome"] == "CLAIMED"
    assert final[2]["final_state"] is JobState.CANCELLED


def test_false_finalization_marks_worker_unhealthy_and_never_idle() -> None:
    repository = Repository(claim(), finalize_result=False)

    assert worker(repository, Runner(outcome())).run_once()

    statuses = [call[1][2] for call in repository.calls if call[0] == "worker_heartbeat"]
    assert statuses[-1] == "UNHEALTHY"
    assert statuses[-1] != "IDLE"


def test_preparation_occurs_inside_runner_at_last_moment() -> None:
    repository = Repository(claim())
    prepared = []

    class PreparingRunner(Runner):
        def run(self, prepare_spawn, *args, **kwargs):
            assert callable(prepare_spawn)
            token = prepare_spawn()
            prepared.append(token)
            return super().run(token, *args, **kwargs)

    instance = JobWorker(
        repository, PreparingRunner(outcome()), Validator(), worker_id="worker-1",
        code_commit="e" * 40, environment=object(),
        safety_preflight=lambda: safety_evidence("4" * 64),
        prepare_spawn=lambda job: ("prepared", job.attempt_id),
        lease_seconds=WORKER_LEASE_SECONDS,
        clock=lambda: datetime(2026, 7, 12, tzinfo=UTC),
    )

    assert instance.run_once()
    assert prepared == [("prepared", "attempt-1")]


def test_worker_composition_reloads_snapshot_for_each_preflight(monkeypatch) -> None:
    from services.job_worker import main

    source = {}
    observed = []

    class Client:
        def __init__(self, path, **kwargs):
            observed.append((path, kwargs))

        def snapshot(self):
            observed.append("snapshot")

    paths = runtime_paths()
    authority = SimpleNamespace(
        application_revision="b" * 40,
        backend_revision=BACKEND_COMMIT,
        safety_snapshot_path=Path("/run/user/1000/trading-agent/safety-state.json"),
        safety_exporter_commit="a" * 40,
        safety_source_fingerprint="c" * 64,
        semantic_evidence=SimpleNamespace(policy_sha256="d" * 64),
        runtime_paths=paths,
        runtime_authority=runtime_authority(paths),
    )

    monkeypatch.setattr(main, "attest_worker_runtime_authority", lambda: authority)
    monkeypatch.setattr(
        main.ResearchEnvironmentSettings,
        "from_authority",
        lambda selected, values: object(),
    )
    monkeypatch.setattr(main, "SafetyStateClient", Client)
    monkeypatch.setattr(main, "AuthorityBoundSafetyPreflight", lambda authority, client: client.snapshot)
    monkeypatch.setattr(main, "ArtifactWriter", lambda path: object())
    monkeypatch.setattr(main, "ProcessRunner", lambda artifacts: object())
    monkeypatch.setattr(main, "ResultValidator", lambda *roots: object())
    monkeypatch.setattr(main, "JobWorker", lambda *args, **kwargs: kwargs["safety_preflight"])

    preflight = main.build_worker(object(), source)
    preflight()

    assert observed[0][1]["protected_root_owned"] is True

    assert observed[1:] == ["snapshot", "snapshot"]


def test_worker_composition_requires_fresh_snapshot_at_construction(monkeypatch) -> None:
    from services.job_worker import main

    observed = []

    class Client:
        def __init__(self, path, **kwargs):
            observed.append((path, kwargs))

        def snapshot(self):
            observed.append("snapshot")
            raise SafetyBlockedError("SAFETY_STATE_STALE", "stale")

    paths = runtime_paths()
    authority = SimpleNamespace(
        application_revision="b" * 40,
        backend_revision=BACKEND_COMMIT,
        safety_snapshot_path=Path("/run/user/1000/trading-agent/safety-state.json"),
        safety_exporter_commit="a" * 40,
        safety_source_fingerprint="c" * 64,
        semantic_evidence=SimpleNamespace(policy_sha256="d" * 64),
        runtime_paths=paths,
        runtime_authority=runtime_authority(paths),
    )

    monkeypatch.setattr(main, "attest_worker_runtime_authority", lambda: authority)
    monkeypatch.setattr(
        main.ResearchEnvironmentSettings,
        "from_authority",
        lambda selected, values: object(),
    )
    monkeypatch.setattr(main, "SafetyStateClient", Client)
    monkeypatch.setattr(main, "AuthorityBoundSafetyPreflight", lambda authority, client: client.snapshot)

    with pytest.raises(SafetyBlockedError) as raised:
        main.build_worker(
            object(),
            {},
        )

    assert raised.value.reason_code == "SAFETY_STATE_STALE"
    assert observed[-1] == "snapshot"


def test_worker_authority_failure_precedes_environment_repository_and_spawn(monkeypatch) -> None:
    from services.job_worker import main
    from services.job_worker.command_registry import CommandRegistryError

    observed = []
    monkeypatch.setattr(
        main,
        "attest_worker_runtime_authority",
        lambda: (_ for _ in ()).throw(CommandRegistryError("RUNTIME_AUTHORITY_INVALID", "closed")),
    )
    monkeypatch.setattr(
        main.ResearchEnvironmentSettings,
        "from_source",
        lambda values: observed.append("environment"),
    )

    with pytest.raises(CommandRegistryError):
        main.build_worker(object(), {})
    assert observed == []


def test_worker_rejects_environment_digest_or_exporter_authority_before_attestation(monkeypatch) -> None:
    from services.job_worker import main

    observed = []
    monkeypatch.setattr(main, "attest_worker_runtime_authority", lambda: observed.append("attest"))
    for key in (
        "TRADING_COMMAND_MANIFEST_SHA256",
        "TRADING_SAFETY_EXPORTER_COMMIT",
        "TRADING_CODE_COMMIT",
    ):
        with pytest.raises(ValueError, match="environment digests"):
            main.build_worker(object(), {key: "a" * 64})
    assert observed == []


def test_worker_rejects_lease_environment_override_before_attestation(monkeypatch) -> None:
    from services.job_worker import main

    observed = []
    monkeypatch.setattr(
        main,
        "attest_worker_runtime_authority",
        lambda: observed.append("attest"),
    )

    with pytest.raises(ValueError, match="lease.*code-owned"):
        main.build_worker(object(), {"TRADING_WORKER_LEASE_SECONDS": "600"})

    assert observed == []


def test_worker_heartbeat_identity_and_result_root_come_only_from_attested_authority(
    monkeypatch,
) -> None:
    from services.job_worker import main

    captured = {}
    paths = runtime_paths()
    authority = SimpleNamespace(
        application_revision="a" * 40,
        backend_revision=BACKEND_COMMIT,
        safety_snapshot_path=Path("/run/user/1000/trading-agent/safety-state.json"),
        safety_exporter_commit="a" * 40,
        safety_source_fingerprint="c" * 64,
        semantic_evidence=SimpleNamespace(policy_sha256="d" * 64),
        runtime_paths=paths,
        runtime_authority=runtime_authority(paths),
    )

    monkeypatch.setattr(main, "attest_worker_runtime_authority", lambda: authority)
    monkeypatch.setattr(
        main.ResearchEnvironmentSettings,
        "from_authority",
        lambda selected, values: object(),
    )
    monkeypatch.setattr(main, "SafetyStateClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        main,
        "AuthorityBoundSafetyPreflight",
        lambda authority, client: lambda: safety_evidence("4" * 64),
    )
    monkeypatch.setattr(main, "ArtifactWriter", lambda root: ("artifacts", root))
    monkeypatch.setattr(main, "ProcessRunner", lambda artifacts: ("runner", artifacts))
    monkeypatch.setattr(
        main,
        "ResultValidator",
        lambda reports, signals, artifact_root: (
            "validator", reports, signals, artifact_root,
        ),
    )
    monkeypatch.setattr(
        main,
        "JobWorker",
        lambda *args, **kwargs: captured.update(kwargs) or (args, kwargs),
    )

    main.build_worker(object(), {"TRADING_WORKER_ID": "worker-reviewed"})

    assert captured["code_commit"] == authority.application_revision
    assert captured["code_commit"] != "unknown"


def test_worker_composition_uses_only_v2_authority_runtime_roots(monkeypatch) -> None:
    from services.job_worker import main

    captured: dict[str, object] = {}
    paths = RuntimePathsV2(
        safety_snapshot=Path("/run/trading-agent-v2/safety-state.json"),
        semantic_authority=Path(
            "/etc/trading-agent-v2/research-input-manifests/active.json"
        ),
        semantic_input_root=Path("/var/lib/trading-agent-v2/research-input"),
        reports_root=Path("/var/lib/trading-agent-v2/research-output/reports"),
        signals_root=Path("/var/lib/trading-agent-v2/research-output/signals"),
        scratch_root=Path("/var/lib/trading-agent-v2/research-home"),
        artifact_root=Path("/var/lib/trading-agent-v2/job-artifacts"),
    )
    authority = SimpleNamespace(
        application_revision="a" * 40,
        backend_revision=BACKEND_COMMIT,
        safety_snapshot_path=paths.safety_snapshot,
        safety_exporter_commit="a" * 40,
        safety_source_fingerprint="c" * 64,
        semantic_evidence=SimpleNamespace(policy_sha256="d" * 64),
        runtime_paths=paths,
        runtime_authority=runtime_authority(paths),
    )

    monkeypatch.setattr(main, "attest_worker_runtime_authority", lambda: authority)
    monkeypatch.setattr(
        main.ResearchEnvironmentSettings,
        "from_source",
        lambda values: pytest.fail("v2 worker used legacy fixed roots"),
    )
    monkeypatch.setattr(
        main.ResearchEnvironmentSettings,
        "from_authority",
        lambda selected, values: captured.update(
            authority=selected, environment_source=values
        )
        or object(),
    )
    monkeypatch.setattr(main, "SafetyStateClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        main,
        "AuthorityBoundSafetyPreflight",
        lambda authority, client: lambda: safety_evidence("4" * 64),
    )
    monkeypatch.setattr(
        main,
        "ArtifactWriter",
        lambda root: captured.update(artifact_root=root) or object(),
    )
    monkeypatch.setattr(main, "ProcessRunner", lambda artifacts: object())
    monkeypatch.setattr(
        main,
        "ResultValidator",
        lambda reports, signals, artifacts: captured.update(
            reports=reports, signals=signals, validator_artifacts=artifacts
        )
        or object(),
    )
    monkeypatch.setattr(main, "JobWorker", lambda *args, **kwargs: object())

    main.build_worker(object(), {})

    assert captured["authority"] is authority.runtime_authority
    assert captured["artifact_root"] == paths.artifact_root
    assert captured["reports"] == paths.reports_root
    assert captured["signals"] == paths.signals_root
    assert captured["validator_artifacts"] == paths.artifact_root


def test_safety_preflight_blocks_authority_rotation_before_reusing_pinned_client(monkeypatch) -> None:
    from services.job_worker.safety_state import AuthorityBoundSafetyPreflight

    calls = []
    safety_a = SimpleNamespace(
        snapshot_path=Path("/run/user/1000/trading-agent/safety-state.json"),
        exporter_commit="a" * 40,
        source_fingerprint="b" * 64,
    )
    safety_b = SimpleNamespace(
        snapshot_path=safety_a.snapshot_path,
        exporter_commit="c" * 40,
        source_fingerprint=safety_a.source_fingerprint,
    )
    authority_a = SimpleNamespace(
        _identity=(1, 2), _document_sha256="d" * 64, safety=safety_a,
        recheck=lambda: authority_a,
    )
    authority_b = SimpleNamespace(
        _identity=(3, 4), _document_sha256="e" * 64, safety=safety_b,
        recheck=lambda: authority_b,
    )
    current = {"value": authority_a}
    client = SimpleNamespace(snapshot=lambda: calls.append("snapshot"))
    pinned = SimpleNamespace(
        safety_snapshot_path=safety_a.snapshot_path,
        safety_exporter_commit=safety_a.exporter_commit,
        safety_source_fingerprint=safety_a.source_fingerprint,
        authority_identity=(1, 2),
        authority_document_sha256="d" * 64,
    )
    preflight = AuthorityBoundSafetyPreflight(
        pinned, client, authority_loader=lambda: current["value"]
    )

    preflight()
    current["value"] = authority_b
    with pytest.raises(SafetyBlockedError) as raised:
        preflight()

    assert raised.value.reason_code == "SAFETY_AUTHORITY_CHANGED"
    assert calls == ["snapshot"]


@pytest.mark.parametrize("reason_code", [
    "SAFETY_KILL_SWITCH_ACTIVE",
    "SAFETY_STATE_STALE",
    "SAFETY_STATE_INVALID",
    "SAFETY_STATE_MISSING",
])
def test_running_fixture_preserves_safety_reason_without_success_or_orphan(reason_code) -> None:
    repository = Repository(claim())
    checks = iter((
        safety_evidence("4" * 64),
        safety_evidence("5" * 64),
        SafetyBlockedError(reason_code, "unsafe"),
    ))

    def safety():
        result = next(checks, SafetyBlockedError(reason_code, "unsafe"))
        if isinstance(result, BaseException):
            raise result
        return result

    class RunningFixture(Runner):
        child_alive = True

        def run(self, prepared, environment, timeout_seconds, heartbeat, **ids):
            instruction = heartbeat(self.result.identity)
            self.decisions.append(instruction.decision)
            if instruction.decision is HeartbeatDecision.SAFETY_DRIFT:
                self.child_alive = False
                return outcome(
                    "SAFETY_DRIFT", -15,
                    safety_reason_code=instruction.reason_code,
                )
            return self.result

    process = RunningFixture(outcome())

    assert worker(repository, process, safety=safety).run_once()

    assert process.decisions == [HeartbeatDecision.SAFETY_DRIFT]
    assert process.child_alive is False
    assert not any(call[0] == "start" for call in repository.calls)
    finals = [call for call in repository.calls if call[0] == "finalize"]
    assert len(finals) == 1
    assert finals[0][2]["expected_state"] is JobState.CLAIMED
    assert finals[0][2]["expected_attempt_outcome"] == "CLAIMED"
    assert finals[0][2]["final_state"] is JobState.BLOCKED
    assert finals[0][2]["reason_code"] == reason_code
    assert not any(
        call[0] == "finalize" and call[2].get("final_state") is JobState.SUCCEEDED
        for call in repository.calls
    )


@pytest.mark.parametrize("reason_code", [
    "SAFETY_KILL_SWITCH_ACTIVE",
    "SAFETY_STATE_STALE",
    "SAFETY_STATE_INVALID",
    "SAFETY_STATE_MISSING",
])
def test_safety_drift_after_child_exit_blocks_before_result_validation(reason_code) -> None:
    repository = Repository(claim())
    checks = iter((
        safety_evidence("3" * 64),
        safety_evidence("4" * 64),
        safety_evidence("5" * 64),
        SafetyBlockedError(reason_code, "unsafe"),
    ))

    def safety():
        result = next(checks)
        if isinstance(result, BaseException):
            raise result
        return result

    validator = Validator()

    assert worker(repository, Runner(outcome()), validator, safety).run_once()

    assert validator.calls == []
    finals = [call for call in repository.calls if call[0] == "finalize"]
    assert len(finals) == 1
    assert finals[0][2]["expected_state"] is JobState.RUNNING
    assert finals[0][2]["final_state"] is JobState.BLOCKED
    assert finals[0][2]["reason_code"] == reason_code
    assert not any(call[0] == "retry" for call in repository.calls)


def test_safety_drift_during_result_validation_progress_blocks_exact_reason() -> None:
    repository = Repository(claim())
    reason_code = "SAFETY_STATE_STALE"
    checks = iter((
        safety_evidence("3" * 64),
        safety_evidence("4" * 64),
        safety_evidence("5" * 64),
        safety_evidence("6" * 64),
        SafetyBlockedError(reason_code, "stale"),
    ))

    def safety():
        result = next(checks)
        if isinstance(result, BaseException):
            raise result
        return result

    class ProgressValidator(Validator):
        def validate(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            kwargs["progress"]()
            return self.value or ValidatedResult(
                "result", "report.json", "d" * 64, 10,
                "application/json", False, "legacy-report-v1", {},
            )

    validator = ProgressValidator()

    assert worker(repository, Runner(outcome()), validator, safety).run_once()

    finals = [call for call in repository.calls if call[0] == "finalize"]
    assert len(finals) == 1
    assert finals[0][2]["final_state"] is JobState.BLOCKED
    assert finals[0][2]["reason_code"] == reason_code
    assert not any(
        call[0] == "finalize" and call[2]["final_state"] is JobState.SUCCEEDED
        for call in repository.calls
    )


def test_safety_is_rechecked_after_validation_before_success_finalization() -> None:
    repository = Repository(claim())
    reason_code = "SAFETY_STATE_MISSING"
    checks = iter((
        safety_evidence("3" * 64),
        safety_evidence("4" * 64),
        safety_evidence("5" * 64),
        safety_evidence("6" * 64),
        SafetyBlockedError(reason_code, "missing"),
    ))

    def safety():
        result = next(checks)
        if isinstance(result, BaseException):
            raise result
        return result

    validator = Validator()

    assert worker(repository, Runner(outcome()), validator, safety).run_once()

    assert len(validator.calls) == 1
    finals = [call for call in repository.calls if call[0] == "finalize"]
    assert len(finals) == 1
    assert finals[0][2]["final_state"] is JobState.BLOCKED
    assert finals[0][2]["reason_code"] == reason_code


def test_worker_startup_recovers_expired_leases_before_any_claim(monkeypatch) -> None:
    from services.job_worker import main

    calls = []

    class Repository:
        def recover_expired_leases(self, inspector, *, recovery_id):
            calls.append(("recover", type(inspector).__name__, recovery_id))
            return ()

    class Worker:
        def run_once(self):
            calls.append(("claim",))
            raise KeyboardInterrupt

    monkeypatch.setattr(main, "build_worker", lambda repository: Worker())
    monkeypatch.setattr(main, "ProcProcessInspector", type("Inspector", (), {}))

    with pytest.raises(KeyboardInterrupt):
        main.serve(Repository(), idle_seconds=0)

    assert calls == [
        ("recover", "Inspector", "worker-startup-recovery"),
        ("claim",),
    ]


def test_worker_main_requires_exact_worker_database_role_before_repository(
    monkeypatch,
) -> None:
    from services.job_worker import main

    calls = []
    monkeypatch.setattr(
        main,
        "attest_worker_runtime_authority",
        lambda: calls.append("authority") or object(),
    )

    def reject_mismatch(cls, *, expected_user):
        calls.append(("settings", expected_user))
        raise ValueError("configured database role does not match service identity")

    monkeypatch.setattr(
        main.JobStoreSettings,
        "from_env",
        classmethod(reject_mismatch),
    )
    monkeypatch.setattr(
        main,
        "WorkerRepository",
        lambda settings: calls.append(("repository", settings)),
    )

    with pytest.raises(ValueError, match="database role"):
        main.main()

    assert calls == ["authority", ("settings", "trading_job_worker")]


def test_worker_main_attests_authority_before_database_credentials(
    monkeypatch,
) -> None:
    from services.job_worker import main
    from services.job_worker.command_registry import CommandRegistryError

    calls: list[str] = []
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)

    def reject_authority():
        calls.append("authority")
        raise CommandRegistryError("RUNTIME_AUTHORITY_INVALID", "closed")

    monkeypatch.setattr(main, "attest_worker_runtime_authority", reject_authority)
    monkeypatch.setattr(
        main.JobStoreSettings,
        "from_env",
        classmethod(
            lambda cls, *, expected_user: calls.append("database-credentials")
        ),
    )
    monkeypatch.setattr(
        main,
        "WorkerRepository",
        lambda settings: calls.append("repository"),
    )

    with pytest.raises(CommandRegistryError):
        main.main()

    assert calls == ["authority"]


def test_worker_main_checks_server_role_and_exact_head_before_recovery(
    monkeypatch,
) -> None:
    from services.job_worker import main

    calls: list[object] = []
    authority = object()

    class Repository:
        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, *args):
            calls.append("exit")

        def assert_runtime_identity(self, *, expected_user, expected_revision):
            calls.append(("identity", expected_user, expected_revision))

    monkeypatch.setattr(
        main,
        "attest_worker_runtime_authority",
        lambda: calls.append("authority") or authority,
    )
    monkeypatch.setattr(
        main.JobStoreSettings,
        "from_env",
        classmethod(lambda cls, *, expected_user: object()),
    )
    monkeypatch.setattr(main, "WorkerRepository", lambda settings: Repository())
    monkeypatch.setattr(
        main,
        "serve",
        lambda repository, *, idle_seconds, authority: calls.append(
            ("serve", authority)
        ),
    )

    assert main.main() is None
    assert calls == [
        "authority",
        "enter",
        (
            "identity",
            "trading_job_worker",
            "0006_job_transition_database_authority",
        ),
        ("serve", authority),
        "exit",
    ]


def test_worker_startup_recovery_error_stops_before_claim(monkeypatch) -> None:
    from services.job_worker import main

    claimed = False

    class Repository:
        def recover_expired_leases(self, inspector, *, recovery_id):
            raise RuntimeError("database unavailable during recovery")

    class Worker:
        def run_once(self):
            nonlocal claimed
            claimed = True
            return False

    monkeypatch.setattr(main, "build_worker", lambda repository: Worker())
    monkeypatch.setattr(main, "ProcProcessInspector", type("Inspector", (), {}))

    with pytest.raises(RuntimeError, match="recovery"):
        main.serve(Repository(), idle_seconds=0)

    assert claimed is False

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.job_contracts import (
    BacktestPayload,
    EngineBacktestPayload,
    JobState,
    JobType,
    parse_payload,
)
from services.job_store.worker_repository import ClaimedJob
from services.job_worker.artifacts import ArtifactMetadata
from services.job_worker.engine_authority import BacktestEngineAuthorityFactory
from services.job_worker.engine_results import ValidatedEngineEventBatch
from services.job_worker.engine_spawn import EngineSpawnError
from services.job_worker.errors import SafetyBlockedError
from services.job_worker.process_runner import (
    HeartbeatDecision,
    HeartbeatInstruction,
    ProcessLineage,
    ProcessOutcome,
)
from services.job_worker.recovery import ProcessIdentity
from services.job_worker.safety import KillSwitchState, SafetyMode
from services.job_worker.safety_state import SafetyEvidence
from services.job_worker.worker import JobWorker, WORKER_LEASE_SECONDS, WorkerControl


NOW = datetime(2026, 8, 5, 12, 30, 15, 123456, tzinfo=UTC)
CODE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
JOB_ID = "job_0123456789abcdef0123456789abcdef"
ATTEMPT_ID = "attempt_fedcba9876543210fedcba9876543210"


def _engine_payload() -> EngineBacktestPayload:
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
    return payload


def _claim(*, legacy: bool = False) -> ClaimedJob:
    payload = (
        BacktestPayload(
            asset="BTC",
            strategy_id="legacy-binary-report-v1",
            date_from=None,
            date_to=None,
        )
        if legacy
        else _engine_payload()
    )
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


def _artifact(kind: str) -> ArtifactMetadata:
    return ArtifactMetadata(
        kind,
        f"{JOB_ID}/{ATTEMPT_ID}/{kind}.log",
        "a" * 64,
        3,
        "application/octet-stream",
        False,
    )


def _outcome(reason: str | None = None, exit_code: int = 0) -> ProcessOutcome:
    return ProcessOutcome(
        exit_code,
        reason,
        ProcessIdentity(1, 1, 1, "b" * 64),
        _artifact("stdout"),
        _artifact("stderr"),
        "c" * 64,
        "engine-event-v1",
        CODE_COMMIT,
        ProcessLineage(
            {
                "engine_closure_sha256": "d" * 64,
                "os_sandbox_profile_sha256": "e" * 64,
                "engine_request_sha256": "f" * 64,
            },
            {},
            {},
        ),
    )


def _safety(digest: str = "4" * 64) -> SafetyEvidence:
    return SafetyEvidence(
        requested_mode=SafetyMode.PAPER,
        effective_mode=SafetyMode.PAPER,
        live_execution_enabled=False,
        live_trading_approved=False,
        kill_switch_state=KillSwitchState.INACTIVE,
        snapshot_sha256=digest,
        generated_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
    )


class Repository:
    def __init__(
        self,
        claimed: ClaimedJob,
        *,
        pre_spawn=(WorkerControl.CONTINUE,),
        controls=(WorkerControl.CONTINUE,),
    ) -> None:
        self.claimed = claimed
        self.pre_spawn = iter(pre_spawn)
        self.controls = iter(controls)
        self.calls: list[tuple[str, object]] = []

    def worker_heartbeat(self, *args, **kwargs):
        self.calls.append(("worker_heartbeat", (args, kwargs)))

    def claim_next(self, *args, **kwargs):
        self.calls.append(("claim", (args, kwargs)))
        value, self.claimed = self.claimed, None
        return value

    def pre_spawn_control(self, *args):
        self.calls.append(("pre_spawn_control", args))
        return next(self.pre_spawn, WorkerControl.CONTINUE)

    def start_attempt(self, *args):
        self.calls.append(("start", args))
        return True

    def heartbeat_control(self, *args):
        self.calls.append(("heartbeat", args))
        return next(self.controls, WorkerControl.CONTINUE)

    def finalize_execution(self, *args, **kwargs):
        self.calls.append(("finalize", kwargs))
        return True

    def finalize_retry(self, *args, **kwargs):
        self.calls.append(("retry", kwargs))
        return True


class Provider:
    def __init__(self, error: EngineSpawnError | None = None) -> None:
        self.error = error
        self.requests = []

    def prepare(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return ("prepared-engine", request.engine_run_id)


class Runner:
    def __init__(self, result: ProcessOutcome) -> None:
        self.result = result
        self.calls = 0
        self.decisions = []

    def run(self, prepare, environment, timeout_seconds, heartbeat, **kwargs):
        self.calls += 1
        assert timeout_seconds is None
        prepare()
        instruction = heartbeat(self.result.identity)
        self.decisions.append(
            instruction.decision
            if isinstance(instruction, HeartbeatInstruction)
            else instruction
        )
        if (
            isinstance(instruction, HeartbeatInstruction)
            and instruction.decision is HeartbeatDecision.SAFETY_DRIFT
            and self.result.termination_reason == "SAFETY_DRIFT"
        ):
            return replace(
                self.result, safety_reason_code=instruction.reason_code
            )
        return self.result


class Validator:
    def __init__(self, *, call_progress: bool = False) -> None:
        self.call_progress = call_progress
        self.calls = []
        self.result = ValidatedEngineEventBatch(
            "engine_event_batch",
            f"engine-results/{JOB_ID}/{ATTEMPT_ID}/{'9' * 64}.jsonl",
            "9" * 64,
            10,
            "application/x-ndjson",
            False,
            "engine-event-v1",
            {"event_count": 1},
            (),
        )

    def validate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.call_progress:
            kwargs["progress"]()
        return self.result


def _worker(
    repository: Repository,
    runner: Runner,
    provider: Provider,
    validator: Validator,
    *,
    safety=lambda: _safety(),
) -> JobWorker:
    return JobWorker(
        repository,
        runner,
        object(),
        worker_id="worker-authority-1",
        code_commit=CODE_COMMIT,
        environment=object(),
        safety_preflight=safety,
        engine_authority_factory=BacktestEngineAuthorityFactory(
            code_commit=CODE_COMMIT, clock=lambda: NOW
        ),
        engine_spawn_provider=provider,
        engine_result_validator=validator,
        lease_seconds=WORKER_LEASE_SECONDS,
        clock=lambda: NOW,
    )


def _final(repository: Repository) -> dict[str, object]:
    return [value for name, value in repository.calls if name == "finalize"][0]


def test_authorized_backtest_uses_worker_owned_engine_path_and_validated_handoff() -> None:
    repository = Repository(_claim())
    provider = Provider()
    runner = Runner(_outcome())
    validator = Validator()

    assert _worker(repository, runner, provider, validator).run_once()

    claim_call = [value for name, value in repository.calls if name == "claim"][0]
    assert claim_call[1]["allowed_job_types"] == (JobType.SNAPSHOT, JobType.BACKTEST)
    assert provider.requests[0].producer_identity == "worker-authority-1"
    assert validator.calls[0][1]["request"] is provider.requests[0]
    assert validator.calls[0][1]["stdout"] == runner.result.stdout
    assert _final(repository)["final_state"] is JobState.SUCCEEDED
    assert _final(repository)["result"] is validator.result


def test_engine_backtest_cancel_before_spawn_never_prepares_or_runs_child() -> None:
    repository = Repository(_claim(), pre_spawn=(WorkerControl.CANCEL,))
    provider = Provider()
    runner = Runner(_outcome())

    assert _worker(repository, runner, provider, Validator()).run_once()

    assert provider.requests == []
    assert runner.calls == 0
    assert _final(repository)["final_state"] is JobState.CANCELLED


def test_engine_backtest_cancel_during_child_uses_existing_termination_policy() -> None:
    repository = Repository(_claim(), controls=(WorkerControl.CANCEL,))
    runner = Runner(_outcome("CANCELLED", -15))

    assert _worker(repository, runner, Provider(), Validator()).run_once()

    assert runner.decisions == [HeartbeatDecision.CANCEL]
    assert _final(repository)["final_state"] is JobState.CANCELLED


def test_engine_backtest_cancel_during_result_validation_cannot_succeed() -> None:
    repository = Repository(
        _claim(),
        controls=(
            WorkerControl.CONTINUE,
            WorkerControl.CONTINUE,
            WorkerControl.CANCEL,
        ),
    )
    validator = Validator(call_progress=True)

    assert _worker(repository, Runner(_outcome()), Provider(), validator).run_once()

    assert _final(repository)["final_state"] is JobState.CANCELLED
    assert _final(repository)["result"] is None


def test_engine_backtest_stale_lease_never_finalizes() -> None:
    repository = Repository(_claim(), controls=(WorkerControl.STALE,))
    runner = Runner(_outcome("STALE_LEASE", -15))

    assert _worker(repository, runner, Provider(), Validator()).run_once()

    assert runner.decisions == [HeartbeatDecision.STALE_LEASE]
    assert not any(name in {"finalize", "retry"} for name, _ in repository.calls)


def test_engine_backtest_safety_drift_blocks_without_success() -> None:
    checks = iter((_safety("1" * 64), _safety("2" * 64), SafetyBlockedError(
        "SAFETY_STATE_STALE", "stale"
    )))

    def safety():
        value = next(checks)
        if isinstance(value, BaseException):
            raise value
        return value

    repository = Repository(_claim())
    runner = Runner(_outcome("SAFETY_DRIFT", -15))

    assert _worker(repository, runner, Provider(), Validator(), safety=safety).run_once()

    assert runner.decisions == [HeartbeatDecision.SAFETY_DRIFT]
    assert _final(repository)["final_state"] is JobState.BLOCKED
    assert _final(repository)["reason_code"] == "SAFETY_STATE_STALE"


@pytest.mark.parametrize(
    "reason",
    ("ENGINE_CLOSURE_UNAVAILABLE", "ENGINE_TRANSPORT_UNSAFE"),
)
def test_engine_closure_or_protected_transport_failure_blocks_before_child(
    reason: str,
) -> None:
    repository = Repository(_claim())
    provider = Provider(EngineSpawnError(reason, "unavailable"))
    runner = Runner(_outcome())

    assert _worker(repository, runner, provider, Validator()).run_once()

    assert runner.decisions == []
    assert not any(name == "start" for name, _ in repository.calls)
    assert _final(repository)["expected_state"] is JobState.CLAIMED
    assert _final(repository)["final_state"] is JobState.BLOCKED
    assert _final(repository)["reason_code"] == reason


def test_legacy_backtest_claim_fails_closed_before_engine_preparation() -> None:
    repository = Repository(_claim(legacy=True))
    provider = Provider()
    runner = Runner(_outcome())

    assert _worker(repository, runner, provider, Validator()).run_once()

    assert provider.requests == []
    assert runner.calls == 0
    assert _final(repository)["expected_state"] is JobState.CLAIMED
    assert _final(repository)["final_state"] is JobState.BLOCKED
    assert _final(repository)["reason_code"] == "ENGINE_BACKTEST_AUTHORITY_REQUIRED"


def test_worker_composition_requires_an_explicit_engine_provider_injection(
    monkeypatch, tmp_path: Path,
) -> None:
    from services.job_worker import main

    captured: dict[str, object] = {}
    paths = SimpleNamespace(
        artifact_root=tmp_path / "artifacts",
        reports_root=tmp_path / "reports",
        signals_root=tmp_path / "signals",
    )
    authority = SimpleNamespace(
        application_revision=CODE_COMMIT,
        runtime_authority=object(),
        runtime_paths=paths,
        safety_snapshot_path=tmp_path / "safety.json",
        safety_exporter_commit="a" * 40,
        safety_source_fingerprint="b" * 64,
    )
    provider = Provider()

    monkeypatch.setattr(
        main.ResearchEnvironmentSettings,
        "from_authority",
        lambda selected, values: object(),
    )
    monkeypatch.setattr(main, "SafetyStateClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        main,
        "AuthorityBoundSafetyPreflight",
        lambda selected, client: lambda: _safety(),
    )
    monkeypatch.setattr(main, "ArtifactWriter", lambda root: object())
    monkeypatch.setattr(main, "ProcessRunner", lambda artifacts: object())
    monkeypatch.setattr(main, "ResultValidator", lambda *roots: object())
    monkeypatch.setattr(
        main,
        "JobWorker",
        lambda *args, **kwargs: captured.update(kwargs) or object(),
    )

    main.build_worker(
        object(),
        {},
        authority=authority,
        engine_spawn_provider=provider,
    )

    assert isinstance(
        captured["engine_authority_factory"], BacktestEngineAuthorityFactory
    )
    assert captured["engine_spawn_provider"] is provider
    assert captured["engine_result_validator"].__class__.__name__ == (
        "EngineResultValidator"
    )

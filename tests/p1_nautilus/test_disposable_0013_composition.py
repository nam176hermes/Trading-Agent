from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.job_api.config import JobApiSettings
from packages.job_contracts import ActorIdentity
from packages.engine_event_ledger import EngineEventBatchReceipt
from packages.engine_portfolio_projection.parity import P1PortfolioParityReceipt
from packages.job_contracts import JobState, JobType, parse_payload
from packages.nautilus_runtime_contracts.result import P1_RESULT_VALIDATOR_ID
from services.job_store.records import (
    ArtifactRecord,
    AttemptRecord,
    JobDetailRecord,
    JobRecord,
)
from packages.domain import Currency
from tests.jobs.test_job_api import (
    AUTH,
    PRINCIPAL,
    Repository,
    enqueue_payload,
    isolated_job_plane_authority,
)


P1_REVISION = "0015_p1_accounting_closure_rotation"
GENERIC_REVISION = "0011_engine_backtest_worker_authority"
JOB_ID = "job_" + "1" * 32
ATTEMPT_ID = "attempt_" + "2" * 32
ENGINE_RUN_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
REQUEST_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
BATCH_SHA256 = "c" * 64
SEMANTIC_SHA256 = "d" * 64
LAST_DIGEST = "e" * 64
NOW = datetime(2026, 8, 5, 12, 1, tzinfo=UTC)


class _Result:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def fetchone(self) -> dict[str, object]:
        return self._row


class _Connection:
    def __init__(self, revision: str, *, user: str = "trading_job_worker") -> None:
        self._revision = revision
        self._user = user

    def execute(self, query: str) -> _Result:
        if "current_user" in query:
            return _Result(
                {"current_user": self._user, "version_num": self._revision}
            )
        if "version_num" in query:
            return _Result({"version_num": self._revision})
        return _Result({"?column?": 1})


class _Pool:
    def __init__(self, revision: str, *, user: str = "trading_job_worker") -> None:
        self._revision = revision
        self._user = user

    @contextmanager
    def connection(self):
        yield _Connection(self._revision, user=self._user)


def _engine_payload() -> dict[str, object]:
    return enqueue_payload(
        job_type="BACKTEST",
        payload={
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
                "start_time": "2026-08-05T12:00:00Z",
                "end_time": "2026-08-05T12:01:00Z",
            }
        },
        idempotency_key="p1:vertical-slice:btc-usdt:20260805",
    )


def _successful_detail() -> JobDetailRecord:
    engine_receipt = EngineEventBatchReceipt(
        batch_sha256=BATCH_SHA256,
        ingestion_digest="f" * 64,
        job_id=JOB_ID,
        attempt_id=ATTEMPT_ID,
        engine_run_id=ENGINE_RUN_ID,
        event_count=14,
        first_sequence=2,
        last_sequence=15,
        last_digest=LAST_DIGEST,
    )
    parity = P1PortfolioParityReceipt(
        schema_version="nautilus-p1-portfolio-parity-v1",
        normalization_version="nautilus-p1-portfolio-normalization-v1",
        engine_run_id=ENGINE_RUN_ID,
        batch_sha256=BATCH_SHA256,
        semantic_digest=SEMANTIC_SHA256,
        request_message_id=REQUEST_ID,
        engine_event_count=14,
        engine_last_sequence=15,
        engine_last_digest=LAST_DIGEST,
        projection_identity="1" * 64,
        portfolio_stream_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        portfolio_event_count=5,
        portfolio_last_sequence=5,
        restart_prefix_sequence=1,
        portfolio_state_hash="2" * 64,
        portfolio_prefix_history_hash="3" * 64,
        account_id="p1-btcusdt-fixture-account",
        account_currency=Currency.USDT,
        terminal_position=Decimal("0"),
        terminal_average_entry_price=None,
        terminal_mark_price=None,
        terminal_cash=Decimal("1000001"),
        terminal_fees=Decimal("0.2"),
        terminal_realized_pnl=Decimal("1.2"),
        terminal_unrealized_pnl=Decimal("0"),
        observed_at=NOW,
    )
    payload = parse_payload(JobType.BACKTEST, _engine_payload()["payload"])
    return JobDetailRecord(
        job=JobRecord(
            job_id=JOB_ID,
            job_type=JobType.BACKTEST,
            state=JobState.SUCCEEDED,
            payload=payload,
            payload_fingerprint="4" * 64,
            idempotency_key="p1:vertical-slice:btc-usdt:20260805",
            actor=ActorIdentity(
                actor_type="OPERATOR", actor_id="p1-vertical-slice"
            ),
            priority=0,
            requested_at=NOW,
            updated_at=NOW,
            attempt_count=1,
            max_attempts=1,
            reason_code="RESULT_VALIDATED",
            result_hash=BATCH_SHA256,
            cancel_requested_at=None,
            cancel_actor=None,
        ),
        attempts=(
            AttemptRecord(
                attempt_id=ATTEMPT_ID,
                job_id=JOB_ID,
                attempt_number=1,
                worker_id="worker-p1",
                outcome="SUCCEEDED",
                claimed_at=NOW,
                started_at=NOW,
                finished_at=NOW,
                exit_code=0,
                termination_reason="EXITED",
            ),
        ),
        artifacts=(
            ArtifactRecord(
                artifact_id="artifact_" + "5" * 32,
                job_id=JOB_ID,
                attempt_id=ATTEMPT_ID,
                artifact_type="engine_event_batch",
                relative_ref=f"engine-results/{JOB_ID}/{ATTEMPT_ID}/{BATCH_SHA256}.jsonl",
                sha256=BATCH_SHA256,
                size_bytes=1234,
                media_type="application/x-ndjson",
                truncated=False,
                validator_id=P1_RESULT_VALIDATOR_ID,
                validation_metadata={
                    "attempt_id": ATTEMPT_ID,
                    "engine_event_receipt": engine_receipt.model_dump(mode="json"),
                    "engine_run_id": str(ENGINE_RUN_ID),
                    "event_count": 14,
                    "job_id": JOB_ID,
                    "last_sequence": 15,
                    "p1_portfolio_parity": parity.model_dump(mode="json"),
                    "request_message_id": str(REQUEST_ID),
                    "semantic_digest": SEMANTIC_SHA256,
                },
                created_at=NOW,
            ),
        ),
    )


def test_disposable_revision_is_code_owned_without_changing_generic_runtime() -> None:
    from apps.job_api import config as api_config
    from services.job_store import config as store_config
    from services.job_worker import main as worker_main

    assert store_config.CANONICAL_DATABASE_REVISION == GENERIC_REVISION
    assert store_config.P1_DISPOSABLE_DATABASE_REVISION == P1_REVISION
    assert api_config.EXPECTED_REVISION == GENERIC_REVISION
    assert worker_main.EXPECTED_DATABASE_REVISION == GENERIC_REVISION
    assert JobApiSettings().expected_revision == GENERIC_REVISION
    with pytest.raises(ValueError, match="expected database revision"):
        JobApiSettings.from_env({"TRADING_JOB_API_EXPECTED_REVISION": P1_REVISION})


def test_dedicated_app_uses_only_0013_while_generic_app_remains_0011() -> None:
    from apps.job_api.app import create_app, create_p1_disposable_app

    settings = JobApiSettings(bearer_token="p1-test-token", principal=PRINCIPAL)
    authority = isolated_job_plane_authority()
    generic_repository = Repository()
    generic_repository._pool = _Pool(GENERIC_REVISION)
    p1_repository = Repository()
    p1_repository._pool = _Pool(P1_REVISION)

    generic = TestClient(create_app(settings, generic_repository, authority))
    p1 = TestClient(create_p1_disposable_app(settings, p1_repository, authority))

    assert generic.get("/health/ready").status_code == 200
    assert p1.get("/health/ready").status_code == 200
    assert TestClient(
        create_app(settings, p1_repository, authority)
    ).get("/health/ready").status_code == 503
    assert TestClient(
        create_p1_disposable_app(settings, generic_repository, authority)
    ).get("/health/ready").status_code == 503
    response = p1.post("/v1/jobs", json=_engine_payload(), headers={
        "Authorization": "Bearer p1-test-token"
    })
    assert response.status_code == 201
    assert p1_repository.last_enqueue[0].actor == PRINCIPAL


def test_stdlib_asgi_post_exercises_authenticated_p1_app() -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical
    from apps.job_api.app import create_p1_disposable_app

    settings = JobApiSettings(bearer_token="p1-test-token", principal=PRINCIPAL)
    authority = isolated_job_plane_authority()
    repository = Repository()
    repository._pool = _Pool(P1_REVISION)
    app = create_p1_disposable_app(settings, repository, authority)

    rejected_status, rejected = vertical._asgi_json_post(
        app,
        path="/v1/jobs",
        body=_engine_payload(),
        bearer_token="wrong-token",
    )
    assert rejected_status == 401
    assert rejected["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert repository.last_enqueue is None

    status, response = vertical._asgi_json_post(
        app,
        path="/v1/jobs",
        body=_engine_payload(),
        bearer_token="p1-test-token",
    )
    assert status == 201
    assert response["data"]["job"]["job_id"] == repository.jobs[0].job_id
    assert repository.last_enqueue[0].actor == PRINCIPAL


@pytest.mark.parametrize(
    "messages",
    (
        (
            {"type": "http.response.start", "status": 201, "headers": []},
            {"type": "http.response.start", "status": 201, "headers": []},
            {"type": "http.response.body", "body": b'{}'},
        ),
        (
            {"type": "http.response.start", "status": 201, "headers": []},
            {"type": "http.response.body", "body": b'{}'},
            {"type": "http.response.body", "body": b'{}'},
        ),
        (
            {"type": "http.response.start", "status": 201, "headers": []},
            {"type": "http.response.body", "body": b'{}', "more_body": True},
        ),
        (
            {"type": "http.response.start", "status": 201, "headers": []},
            {"type": "http.response.trailers", "headers": []},
        ),
        (
            {"type": "http.response.start", "status": 201, "headers": []},
            {"type": "http.response.body", "body": b'x' * (1024 * 1024 + 1)},
        ),
    ),
    ids=("multi-start", "multi-body", "streaming", "unexpected", "oversized"),
)
def test_stdlib_asgi_post_rejects_unclosed_response_sequences(
    messages: tuple[dict[str, object], ...],
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    async def malformed_app(
        _scope: dict[str, object],
        receive: object,
        send: object,
    ) -> None:
        request = await receive()  # type: ignore[operator]
        assert request["type"] == "http.request"
        for message in messages:
            await send(message)  # type: ignore[operator]

    with pytest.raises(vertical.VerticalSliceError):
        vertical._asgi_json_post(
            malformed_app,
            path="/v1/jobs",
            body=_engine_payload(),
            bearer_token="p1-test-token",
        )


def test_vertical_slice_runtime_import_does_not_require_testclient_or_httpx() -> None:
    source = (Path(__file__).parents[2] / "scripts/run_p1_nautilus_vertical_slice.py").read_text(
        encoding="utf-8"
    )
    assert "fastapi.testclient" not in source
    assert "httpx" not in source
    program = """
import builtins
original_import = builtins.__import__
def blocked_import(name, *args, **kwargs):
    if name == 'fastapi.testclient' or name.startswith('httpx'):
        raise ModuleNotFoundError(name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = blocked_import
import scripts.run_p1_nautilus_vertical_slice
"""
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_p1_worker_identity_is_no_arg_and_exact_0013() -> None:
    from inspect import signature

    from services.job_store.worker_repository import WorkerRepository

    repository = object.__new__(WorkerRepository)
    repository._pool = _Pool(P1_REVISION)

    assert list(
        signature(WorkerRepository.assert_p1_disposable_runtime_identity).parameters
    ) == ["self"]
    repository.assert_p1_disposable_runtime_identity()
    repository._pool = _Pool(GENERIC_REVISION)
    with pytest.raises(RuntimeError, match="authority is unavailable"):
        repository.assert_p1_disposable_runtime_identity()
    repository.assert_runtime_identity(
        expected_user="trading_job_worker",
        expected_revision=GENERIC_REVISION,
    )


def test_vertical_slice_runs_authenticated_enqueue_then_exactly_one_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    assert hasattr(vertical, "_run_p1_disposable_once")
    arguments = vertical._parser().parse_args(
        [
            "--p1-closure-root", str(tmp_path / "closure"),
            "--p1-closure-artifacts", str(tmp_path / "artifacts"),
            "--bubblewrap", str(tmp_path / "bwrap"),
            "--transport-root", str(tmp_path / "transport"),
            "--postgres-approval", str(tmp_path / "approval.json"),
            "--postgres-scope", "DISPOSABLE_PG_GREEN",
            "--pgdata", str(tmp_path / "pgdata"),
            "--pg-port", "49152",
            "--engine-configuration", str(tmp_path / "engine.json"),
            "--instrument-catalog", str(tmp_path / "catalog.json"),
            "--strategy-configuration", str(tmp_path / "strategy.json"),
            "--market-data", str(tmp_path / "market.jsonl"),
        ]
    )
    calls: list[str] = []

    class _ContextRepository:
        def __init__(self, settings: object, label: str) -> None:
            self.settings = settings
            self.label = label

        def __enter__(self):
            calls.append(f"{self.label}:enter")
            return self

        def __exit__(self, *_args: object) -> None:
            calls.append(f"{self.label}:exit")

        def assert_p1_disposable_runtime_identity(self) -> None:
            calls.append("worker:identity")

    class _ApiRepository(_ContextRepository):
        def __init__(self, settings: object) -> None:
            super().__init__(settings, "api")

        def get_job(self, job_id: str) -> JobDetailRecord:
            calls.append("api:get_job")
            assert job_id == JOB_ID
            return _successful_detail()

    class _WorkerRepository(_ContextRepository):
        def __init__(self, settings: object) -> None:
            super().__init__(settings, "worker")

    class _Worker:
        def run_once(self) -> bool:
            calls.append("worker:run_once")
            return True

    class _Settings:
        def __init__(self, **_kwargs: object) -> None:
            self.principal = ActorIdentity(
                actor_type="OPERATOR", actor_id="p1-vertical-slice"
            )

        def load_authority(self) -> str:
            return "job-plane-authority"

    monkeypatch.setattr(vertical, "JobRepository", _ApiRepository)
    monkeypatch.setattr(vertical, "WorkerRepository", _WorkerRepository)
    monkeypatch.setattr(vertical, "JobApiSettings", _Settings)
    def post(
        app: object,
        *,
        path: str,
        body: dict[str, object],
        bearer_token: str,
    ) -> tuple[int, dict[str, object]]:
        assert app == "p1-app"
        calls.append("client:post")
        assert path == "/v1/jobs"
        assert body["job_type"] == "BACKTEST"
        assert bearer_token
        return 201, {"data": {"job": {"job_id": JOB_ID}}}

    monkeypatch.setattr(vertical, "_asgi_json_post", post)
    monkeypatch.setattr(
        vertical,
        "create_p1_disposable_app",
        lambda *_args: calls.append("app:create") or "p1-app",
    )
    worker_authority = SimpleNamespace(
        application_revision="1" * 40,
        authority_document_sha256="3" * 64,
        backend_revision="1" * 40,
        runtime_authority=SimpleNamespace(source_tree="2" * 40),
    )
    monkeypatch.setattr(
        vertical,
        "attest_worker_runtime_authority",
        lambda: pytest.fail("execution must reuse the preflight authority"),
    )
    captured: dict[str, object] = {}
    safety_authority_refresher = object()

    def build(repository: object, source: object, **kwargs: object) -> _Worker:
        calls.append("worker:build")
        captured.update(repository=repository, source=source, **kwargs)
        return _Worker()

    monkeypatch.setattr(vertical, "build_p1_worker", build)

    evidence = vertical._run_p1_disposable_once(
        arguments,
        worker_authority,
        safety_authority_refresher=safety_authority_refresher,
    )

    assert evidence["job_id"] == JOB_ID
    assert evidence["attempt_id"] == ATTEMPT_ID
    assert evidence["batch_sha256"] == BATCH_SHA256
    assert evidence["semantic_digest"] == SEMANTIC_SHA256
    assert evidence["final_portfolio_state_hash"] == "2" * 64
    assert evidence["worker_run_count"] == 1
    assert calls == [
        "api:enter",
        "worker:enter",
        "worker:identity",
        "worker:build",
        "app:create",
        "client:post",
        "worker:run_once",
        "api:get_job",
        "worker:exit",
        "api:exit",
    ]
    assert captured["source"] == {}
    assert captured["authority"] is worker_authority
    assert captured["safety_authority_refresher"] is safety_authority_refresher


@pytest.mark.parametrize(
    ("failure_at", "expected_stage", "job_mutated"),
    (
        ("job_authority", "JOB_AUTHORITY", False),
        ("database", "DATABASE", False),
        ("worker_identity", "WORKER_IDENTITY", False),
        ("worker_composition", "WORKER_COMPOSITION", False),
        ("app_composition", "APP_COMPOSITION", False),
        ("enqueue", "ENQUEUE", True),
        ("worker", "WORKER", True),
        ("durable", "DURABLE_RESULT", True),
    ),
)
def test_vertical_execution_failure_stage_is_closed_at_each_trust_seam(
    failure_at: str,
    expected_stage: str,
    job_mutated: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical
    from tests.p1_nautilus.test_vertical_slice_e2e import _complete_arguments

    arguments = vertical._parser().parse_args(_complete_arguments(tmp_path))

    def fail_if(stage: str) -> None:
        if failure_at == stage:
            raise RuntimeError("sensitive arbitrary diagnostic text")

    class _Settings:
        def __init__(self, **_kwargs: object) -> None:
            fail_if("job_authority")

        @staticmethod
        def load_authority() -> object:
            return object()

    class _Repository:
        def __init__(self, _settings: object) -> None:
            pass

        def __enter__(self) -> _Repository:
            fail_if("database")
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        @staticmethod
        def assert_p1_disposable_runtime_identity() -> None:
            fail_if("worker_identity")

        @staticmethod
        def get_job(_job_id: str) -> JobDetailRecord:
            fail_if("durable")
            return _successful_detail()

    class _Worker:
        @staticmethod
        def run_once() -> bool:
            fail_if("worker")
            return True

    monkeypatch.setattr(vertical, "JobApiSettings", _Settings)
    monkeypatch.setattr(vertical, "JobRepository", _Repository)
    monkeypatch.setattr(vertical, "WorkerRepository", _Repository)
    def build(*_args: object, **_kwargs: object) -> _Worker:
        fail_if("worker_composition")
        return _Worker()

    def create_app(*_args: object) -> object:
        fail_if("app_composition")
        return object()

    monkeypatch.setattr(vertical, "build_p1_worker", build)
    monkeypatch.setattr(vertical, "create_p1_disposable_app", create_app)

    def post(*_args: object, **_kwargs: object) -> tuple[int, dict[str, object]]:
        fail_if("enqueue")
        return 201, {"data": {"job": {"job_id": JOB_ID}}}

    monkeypatch.setattr(vertical, "_asgi_json_post", post)

    with pytest.raises(vertical.VerticalSliceExecutionError) as captured:
        vertical._run_p1_disposable_once(arguments, object())

    assert captured.value.failure_stage == expected_stage
    assert captured.value.failure_family == (
        "OTHER" if expected_stage == "WORKER_COMPOSITION" else None
    )
    assert captured.value.job_mutated is job_mutated
    assert "sensitive arbitrary diagnostic text" not in str(captured.value)


def test_vertical_execution_failure_stage_rejects_arbitrary_codes() -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    error_type = getattr(vertical, "VerticalSliceExecutionError")
    with pytest.raises(ValueError, match="failure stage is invalid"):
        error_type(job_mutated=False, failure_stage="ARBITRARY")
    with pytest.raises(ValueError, match="failure family is invalid"):
        error_type(
            job_mutated=False,
            failure_stage="WORKER",
            failure_family="ENVIRONMENT",
        )


@pytest.mark.parametrize(
    ("failure", "expected_family"),
    (
        pytest.param(
            "environment",
            "ENVIRONMENT",
            id="validated-environment-refusal",
        ),
        pytest.param("safety", "SAFETY", id="validated-safety-refusal"),
        pytest.param(
            "engine",
            "ENGINE_COMPOSITION",
            id="validated-engine-refusal",
        ),
        pytest.param("other", "OTHER", id="unclassified-internal-refusal"),
    ),
)
def test_worker_composition_failure_family_is_closed_and_non_sensitive(
    failure: str,
    expected_family: str,
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical
    from services.job_worker.engine_spawn_interface import EngineSpawnError
    from services.job_worker.environment import EnvironmentValidationError
    from services.job_worker.errors import SafetyBlockedError

    failures = {
        "environment": EnvironmentValidationError(
            "ENVIRONMENT_ROOT_MISSING", "sensitive environment detail"
        ),
        "safety": SafetyBlockedError(
            "SAFETY_STATE_STALE", "sensitive safety detail"
        ),
        "engine": EngineSpawnError(
            "ENGINE_CLOSURE_INVALID", "sensitive engine detail"
        ),
        "other": RuntimeError("sensitive arbitrary diagnostic text"),
    }
    assert (
        vertical._worker_composition_failure_family(failures[failure])
        == expected_family
    )


@pytest.mark.parametrize(
    "detail",
    (
        replace(
            _successful_detail(),
            job=replace(_successful_detail().job, state=JobState.BLOCKED),
        ),
        replace(
            _successful_detail(),
            job=replace(_successful_detail().job, job_id="job_" + "9" * 32),
        ),
    ),
    ids=("run-once-true-but-not-succeeded", "wrong-job"),
)
def test_durable_success_rejects_non_success_or_other_job(
    detail: JobDetailRecord,
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    with pytest.raises(vertical.VerticalSliceExecutionError):
        vertical._durable_success_evidence(detail, expected_job_id=JOB_ID)


@pytest.mark.parametrize("mutation", ("missing", "mixed"))
def test_durable_success_rejects_missing_or_mixed_receipts(mutation: str) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical

    detail = _successful_detail()
    artifact = detail.artifacts[0]
    metadata = dict(artifact.validation_metadata)
    if mutation == "missing":
        metadata.pop("p1_portfolio_parity")
    else:
        parity = dict(metadata["p1_portfolio_parity"])
        parity["batch_sha256"] = "9" * 64
        metadata["p1_portfolio_parity"] = parity
    detail = replace(
        detail,
        artifacts=(replace(artifact, validation_metadata=metadata),),
    )

    with pytest.raises(vertical.VerticalSliceExecutionError):
        vertical._durable_success_evidence(detail, expected_job_id=JOB_ID)


def test_execute_receipt_is_pass_only_after_full_preflight_and_one_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical
    from tests.p1_nautilus.test_vertical_slice_e2e import _complete_arguments

    validation = {
        "closure_sha256": "7" * 64,
        "postgres_approval_sha256": "8" * 64,
        "runtime_authority_sha256": "9" * 64,
        "source_commit": "1" * 40,
        "source_tree": "2" * 40,
    }
    preflight_authority = object()
    safety_authority_refresher = object()
    calls: list[str] = []
    monkeypatch.setattr(vertical, "WorkerRuntimeAuthority", object)
    monkeypatch.setattr(vertical, "P1StagingSafetyAuthorityRefresher", object)

    def validate(_args: object, authority: object) -> tuple[dict[str, str], object]:
        calls.append("validate")
        assert authority is preflight_authority
        return validation, preflight_authority

    monkeypatch.setattr(vertical, "_validate_complete", validate)
    monkeypatch.setattr(
        vertical,
        "_run_p1_disposable_once",
        lambda _args, execution_authority, **kwargs: calls.append("execute")
        or calls.append(
            "authority-bound"
            if execution_authority is preflight_authority
            else "authority-mixed"
        )
        or calls.append(
            "refresher-bound"
            if kwargs.get("safety_authority_refresher")
            is safety_authority_refresher
            else "refresher-mixed"
        )
        or {"job_id": "job_" + "1" * 32, "worker_run_count": 1},
    )

    result = vertical.main(
        [*_complete_arguments(tmp_path), "--execute"],
        worker_authority=preflight_authority,  # type: ignore[arg-type]
        safety_authority_refresher=safety_authority_refresher,  # type: ignore[arg-type]
    )

    assert result == 0
    receipt = json.loads(capsys.readouterr().out)
    assert calls == [
        "validate",
        "execute",
        "authority-bound",
        "refresher-bound",
    ]
    assert receipt["status"] == "PASS"
    assert receipt["reason"] == "P1_VERTICAL_SLICE_COMPLETED"
    assert receipt["job_mutated"] is True
    assert receipt["evidence"] == {
        **validation,
        "job_id": "job_" + "1" * 32,
        "worker_run_count": 1,
    }


def test_execution_blocked_receipt_exposes_only_closed_failure_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical
    from tests.p1_nautilus.test_vertical_slice_e2e import _complete_arguments

    evidence = {
        "closure_sha256": "7" * 64,
        "postgres_approval_sha256": "8" * 64,
        "runtime_authority_sha256": "9" * 64,
        "source_commit": "1" * 40,
        "source_tree": "2" * 40,
    }
    monkeypatch.setattr(
        vertical,
        "_validate_complete",
        lambda _arguments: (evidence, object()),
    )

    def fail(
        _arguments: object, _authority: object, **_kwargs: object
    ) -> dict[str, object]:
        try:
            raise RuntimeError("sensitive arbitrary diagnostic text")
        except RuntimeError as cause:
            raise vertical.VerticalSliceExecutionError(
                job_mutated=True,
                failure_stage="WORKER",
            ) from cause

    monkeypatch.setattr(vertical, "_run_p1_disposable_once", fail)

    result = vertical.main([*_complete_arguments(tmp_path), "--execute"])

    output = capsys.readouterr().out
    receipt = json.loads(output)
    assert result == 2
    assert receipt["reason"] == "P1_EXECUTION_FAILED"
    assert receipt["external_authority"] == {
        "native": "VALID",
        "postgres": "VALID",
    }
    assert receipt["evidence"] == {**evidence, "failure_stage": "WORKER"}
    assert "sensitive arbitrary diagnostic text" not in output


def test_worker_composition_blocked_receipt_exposes_only_closed_family(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.run_p1_nautilus_vertical_slice as vertical
    from tests.p1_nautilus.test_vertical_slice_e2e import _complete_arguments

    evidence = {
        "closure_sha256": "7" * 64,
        "postgres_approval_sha256": "8" * 64,
        "runtime_authority_sha256": "9" * 64,
        "source_commit": "1" * 40,
        "source_tree": "2" * 40,
    }
    monkeypatch.setattr(
        vertical,
        "_validate_complete",
        lambda _arguments: (evidence, object()),
    )

    def fail(
        _arguments: object, _authority: object, **_kwargs: object
    ) -> dict[str, object]:
        try:
            raise RuntimeError("sensitive arbitrary diagnostic text")
        except RuntimeError as cause:
            raise vertical.VerticalSliceExecutionError(
                job_mutated=False,
                failure_stage="WORKER_COMPOSITION",
                failure_family="OTHER",
            ) from cause

    monkeypatch.setattr(vertical, "_run_p1_disposable_once", fail)

    result = vertical.main([*_complete_arguments(tmp_path), "--execute"])

    output = capsys.readouterr().out
    receipt = json.loads(output)
    assert result == 2
    assert receipt["evidence"] == {
        **evidence,
        "failure_family": "OTHER",
        "failure_stage": "WORKER_COMPOSITION",
    }
    assert "sensitive arbitrary diagnostic text" not in output

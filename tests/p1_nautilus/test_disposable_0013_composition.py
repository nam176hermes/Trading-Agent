from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.job_api.config import JobApiSettings
from packages.job_contracts import ActorIdentity
from tests.jobs.test_job_api import (
    AUTH,
    PRINCIPAL,
    Repository,
    enqueue_payload,
    isolated_job_plane_authority,
)


P1_REVISION = "0013_engine_backtest_enqueue_authority"
GENERIC_REVISION = "0011_engine_backtest_worker_authority"


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

    class _WorkerRepository(_ContextRepository):
        def __init__(self, settings: object) -> None:
            super().__init__(settings, "worker")

    class _Response:
        status_code = 201

        @staticmethod
        def json() -> dict[str, object]:
            return {"data": {"job": {"job_id": "job_" + "1" * 32}}}

    class _Client:
        def __init__(self, app: object) -> None:
            assert app == "p1-app"

        def __enter__(self):
            calls.append("client:enter")
            return self

        def __exit__(self, *_args: object) -> None:
            calls.append("client:exit")

        def post(
            self, path: str, *, json: dict[str, object], headers: dict[str, str]
        ) -> _Response:
            calls.append("client:post")
            assert path == "/v1/jobs"
            assert json["job_type"] == "BACKTEST"
            assert headers["Authorization"].startswith("Bearer ")
            return _Response()

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
    monkeypatch.setattr(vertical, "TestClient", _Client)
    monkeypatch.setattr(
        vertical,
        "create_p1_disposable_app",
        lambda *_args: calls.append("app:create") or "p1-app",
    )
    monkeypatch.setattr(
        vertical,
        "attest_worker_runtime_authority",
        lambda: calls.append("worker:attest") or object(),
    )
    captured: dict[str, object] = {}

    def build(repository: object, source: object, **kwargs: object) -> _Worker:
        calls.append("worker:build")
        captured.update(repository=repository, source=source, **kwargs)
        return _Worker()

    monkeypatch.setattr(vertical, "build_p1_worker", build)

    evidence = vertical._run_p1_disposable_once(arguments)

    assert evidence == {"job_id": "job_" + "1" * 32, "worker_run_count": 1}
    assert calls == [
        "api:enter",
        "worker:enter",
        "app:create",
        "client:enter",
        "client:post",
        "client:exit",
        "worker:identity",
        "worker:attest",
        "worker:build",
        "worker:run_once",
        "worker:exit",
        "api:exit",
    ]
    assert captured["source"] == {}


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
    calls: list[str] = []
    monkeypatch.setattr(
        vertical,
        "_validate_complete",
        lambda _args: calls.append("validate") or validation,
    )
    monkeypatch.setattr(
        vertical,
        "_run_p1_disposable_once",
        lambda _args: calls.append("execute")
        or {"job_id": "job_" + "1" * 32, "worker_run_count": 1},
    )

    result = vertical.main([*_complete_arguments(tmp_path), "--execute"])

    assert result == 0
    receipt = json.loads(capsys.readouterr().out)
    assert calls == ["validate", "execute"]
    assert receipt["status"] == "PASS"
    assert receipt["reason"] == "P1_VERTICAL_SLICE_COMPLETED"
    assert receipt["job_mutated"] is True
    assert receipt["evidence"] == {
        **validation,
        "job_id": "job_" + "1" * 32,
        "worker_run_count": 1,
    }

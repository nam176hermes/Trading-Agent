#!/usr/bin/env python3
"""Preflight the exact P1 BTCUSDT vertical slice without ambient authority."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Literal, cast
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.nautilus_runtime_contracts import (
    P1EngineConfigurationV1,
    P1InstrumentCatalogV1,
    P1TargetScheduleV1,
    parse_canonical_artifact,
)
from apps.job_api.app import create_p1_disposable_app
from apps.job_api.config import JobApiSettings
from packages.domain import (
    AccountBalanceSnapshot,
    AssetClass,
    Currency,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    LiquiditySide,
    Money,
    OrderQuantity,
    PortfolioOpeningEntry,
    Price,
    ProductType,
    ReconciliationSource,
)
from packages.engine_contracts import (
    ArtifactReference,
    EngineCommandEnvelope,
    RunBacktest,
    canonical_json_bytes,
)
from packages.engine_event_ledger import EngineEventBatchReceipt
from packages.engine_event_ledger.models import FIRST_ENGINE_EVENT_SEQUENCE
from packages.engine_portfolio_projection.models import ProjectionAuthority
from packages.engine_portfolio_projection.parity import P1PortfolioParityReceipt
from packages.engine_portfolio_projection.validation import canonical_authority
from packages.job_contracts import ActorIdentity, JobState
from packages.nautilus_runtime_contracts.result import P1_RESULT_VALIDATOR_ID
from packages.runtime_release import validate_job_plane_authority
from scripts.validate_disposable_postgres_approval import (
    BIND_HOST,
    CLUSTER_NAME,
    DISPOSABLE_DATABASE_NAME,
    DisposablePostgresApprovalContext,
    _runtime_setting_names,
    canonical_record_sha256,
    load_protected_approval_record,
    validate_disposable_postgres_approval,
    validate_disposable_postgres_approval_record,
    validate_source_binding_files,
)
from services.job_store.config import JobStoreSettings
from services.job_store.records import JobDetailRecord
from services.job_store.repository import JobRepository
from services.job_store.worker_repository import WorkerRepository
from services.job_worker.command_registry import (
    P1StagingSafetyAuthorityRefresher,
    WorkerRuntimeAuthority,
    attest_worker_runtime_authority,
)
from services.job_worker.engine_artifacts import EngineArtifactBinding
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
from services.job_worker.engine_spawn_interface import EngineSpawnError
from services.job_worker.environment import EnvironmentValidationError
from services.job_worker.errors import SafetyBlockedError
from services.job_worker.main import build_p1_worker
from services.job_worker.nautilus_closure import NautilusClosureConfig
from services.job_worker.p1_nautilus_closure import attest_p1_nautilus_closure


SCHEMA = "trading-agent-p1-nautilus-vertical-slice/v1"
OPERATION_ID = "p1-vertical-slice-v1"
TEST_PATH = "tests/p1_nautilus/test_vertical_slice_e2e.py"
START = "2026-08-05T12:00:00Z"
END = "2026-08-05T12:01:00Z"
FIXTURES = ROOT / "tests/fixtures/p1_nautilus"
SANDBOX_POLICY = ROOT / "engines/nautilus/sealed-uv-exec-policy.json"
CANONICAL_FIXTURES = {
    "engine_configuration": FIXTURES / "contracts/engine-configuration.json",
    "instrument_catalog": FIXTURES / "contracts/instrument-catalog.json",
    "strategy_configuration": FIXTURES / "contracts/target-schedule.json",
    "market_data": FIXTURES / "e2e/btcusdt-1m.jsonl",
}
EXPECTED_DIGESTS = {
    "engine_configuration": "38fa348e0422607052851028ed84b2478740d930ce09832dc5e42cbb86b78f60",
    "instrument_catalog": "22a6c061b06d0eef539509a5cfa4a1128843a80b1f48eb473a9b65126f74d822",
    "strategy_configuration": "c4002efb2f0f2b14c94699db59ef8c5733602e41c3bfe60999670fb7c0671470",
    "market_data": "d390750a1d51b6f333efc7092cd99f2c6752ca6ab51daeaa800171ea92005c9c",
}
_ARTIFACT_IDS = {
    "engine_configuration": UUID("11111111-1111-4111-8111-111111111111"),
    "instrument_catalog": UUID("22222222-2222-4222-8222-222222222222"),
    "strategy_configuration": UUID("33333333-3333-4333-8333-333333333333"),
    "market_data": UUID("44444444-4444-4444-8444-444444444444"),
}
_API_TOKEN = "p1-vertical-slice-disposable-token"
_API_PASSWORD = "test-only-job-api-credential-0005"
_WORKER_PASSWORD = "test-only-job-worker-credential-0006"
_PRINCIPAL = ActorIdentity(
    actor_type="OPERATOR", actor_id="p1-vertical-slice"
)
_COMMIT = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_ASGI_RESPONSE_BYTES = 1024 * 1024
_ExecutionFailureStage = Literal[
    "JOB_AUTHORITY",
    "DATABASE",
    "WORKER_IDENTITY",
    "WORKER_COMPOSITION",
    "APP_COMPOSITION",
    "ENQUEUE",
    "WORKER",
    "DURABLE_RESULT",
]
_WorkerCompositionFailureFamily = Literal[
    "ENVIRONMENT",
    "SAFETY",
    "ENGINE_COMPOSITION",
    "OTHER",
]
_EXECUTION_FAILURE_STAGES = (
    "JOB_AUTHORITY",
    "DATABASE",
    "WORKER_IDENTITY",
    "WORKER_COMPOSITION",
    "APP_COMPOSITION",
    "ENQUEUE",
    "WORKER",
    "DURABLE_RESULT",
)
_WORKER_COMPOSITION_FAILURE_FAMILIES = (
    "ENVIRONMENT",
    "SAFETY",
    "ENGINE_COMPOSITION",
    "OTHER",
)
_GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "XDG_CONFIG_HOME": "/nonexistent",
}
_AsgiMessage = dict[str, object]
_AsgiReceive = Callable[[], Awaitable[_AsgiMessage]]
_AsgiSend = Callable[[_AsgiMessage], Awaitable[None]]
_AsgiApplication = Callable[
    [dict[str, object], _AsgiReceive, _AsgiSend], Awaitable[None]
]


class VerticalSliceError(ValueError):
    """Exact qualification input is absent or invalid."""


class VerticalSliceExecutionError(RuntimeError):
    """The bounded execution failed, with exact mutation state retained."""

    def __init__(
        self,
        *,
        job_mutated: bool,
        failure_stage: _ExecutionFailureStage = "DURABLE_RESULT",
        failure_family: _WorkerCompositionFailureFamily | None = None,
    ) -> None:
        if failure_stage not in _EXECUTION_FAILURE_STAGES:
            raise ValueError("P1 execution failure stage is invalid")
        if failure_family is not None and (
            failure_stage != "WORKER_COMPOSITION"
            or failure_family not in _WORKER_COMPOSITION_FAILURE_FAMILIES
        ):
            raise ValueError("P1 worker composition failure family is invalid")
        super().__init__("P1 disposable execution failed")
        self.job_mutated = job_mutated
        self.failure_stage = failure_stage
        self.failure_family = failure_family


def _worker_composition_failure_family(
    error: Exception,
) -> _WorkerCompositionFailureFamily:
    if isinstance(error, EnvironmentValidationError):
        return "ENVIRONMENT"
    if isinstance(error, SafetyBlockedError):
        return "SAFETY"
    if isinstance(error, EngineSpawnError):
        return "ENGINE_COMPOSITION"
    return "OTHER"


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


async def _invoke_asgi_json_post(
    app: object,
    *,
    path: str,
    body: Mapping[str, object],
    bearer_token: str,
) -> tuple[int, dict[str, object]]:
    request_body = json.dumps(
        dict(body),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    scope: dict[str, object] = {
        "asgi": {"spec_version": "2.3", "version": "3.0"},
        "client": ("127.0.0.1", 0),
        "extensions": {},
        "headers": [
            (b"authorization", f"Bearer {bearer_token}".encode("utf-8")),
            (b"content-length", str(len(request_body)).encode("ascii")),
            (b"content-type", b"application/json"),
            (b"host", b"p1-disposable.local"),
        ],
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "scheme": "http",
        "server": ("p1-disposable.local", 80),
        "state": {},
        "type": "http",
    }
    request_delivered = False
    messages: list[_AsgiMessage] = []
    protocol_error: VerticalSliceError | None = None

    async def receive() -> _AsgiMessage:
        nonlocal request_delivered
        if request_delivered:
            return {"type": "http.disconnect"}
        request_delivered = True
        return {
            "body": request_body,
            "more_body": False,
            "type": "http.request",
        }

    async def send(message: _AsgiMessage) -> None:
        nonlocal protocol_error

        def reject() -> None:
            nonlocal protocol_error
            protocol_error = VerticalSliceError("ASGI response is invalid")
            raise protocol_error

        expected_type = (
            "http.response.start" if not messages else "http.response.body"
        )
        if (
            type(message) is not dict
            or len(messages) >= 2
            or message.get("type") != expected_type
        ):
            reject()
        if expected_type == "http.response.start":
            if (
                not {"type", "status", "headers"}.issubset(message)
                or not set(message).issubset(
                    {"type", "status", "headers", "trailers"}
                )
                or isinstance(message["status"], bool)
                or not isinstance(message["status"], int)
                or not 100 <= message["status"] <= 599
                or type(message["headers"]) is not list
                or message.get("trailers", False) is not False
            ):
                reject()
            headers = message["headers"]
            assert type(headers) is list
            if any(
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not bytes
                or type(item[1]) is not bytes
                for item in headers
            ):
                reject()
        else:
            if (
                not set(message).issubset({"type", "body", "more_body"})
                or type(message.get("body", b"")) is not bytes
                or message.get("more_body", False) is not False
            ):
                reject()
            observed_body = message.get("body", b"")
            assert isinstance(observed_body, bytes)
            if len(observed_body) > _MAX_ASGI_RESPONSE_BYTES:
                reject()
        messages.append(message)

    application = cast(_AsgiApplication, app)
    try:
        await application(scope, receive, send)
    except VerticalSliceError:
        if protocol_error is not None:
            raise protocol_error from None
        raise
    if protocol_error is not None:
        raise protocol_error
    if len(messages) != 2:
        raise VerticalSliceError("ASGI response is incomplete")
    start, finish = messages
    response_status = cast(int, start["status"])
    response_headers = cast(list[tuple[bytes, bytes]], start["headers"])
    response_body = cast(bytes, finish.get("body", b""))
    content_types = [
        value.split(b";", 1)[0].strip().lower()
        for key, value in response_headers
        if key.lower() == b"content-type"
    ]
    if content_types != [b"application/json"]:
        raise VerticalSliceError("ASGI response is not canonical JSON")
    try:
        decoded = json.loads(
            response_body,
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeDecodeError, ValueError):
        raise VerticalSliceError("ASGI response JSON is invalid") from None
    if type(decoded) is not dict:
        raise VerticalSliceError("ASGI response JSON is invalid")
    return response_status, decoded


def _asgi_json_post(
    app: object,
    *,
    path: str,
    body: Mapping[str, object],
    bearer_token: str,
) -> tuple[int, dict[str, object]]:
    """Invoke one bounded JSON ASGI request without an HTTP client dependency."""

    return asyncio.run(
        _invoke_asgi_json_post(
            app,
            path=path,
            body=body,
            bearer_token=bearer_token,
        )
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_receipt() -> dict[str, object]:
    observed = {name: _digest(path) for name, path in CANONICAL_FIXTURES.items()}
    if observed != EXPECTED_DIGESTS:
        raise VerticalSliceError("canonical fixture authority drifted")
    parse_canonical_artifact(
        P1EngineConfigurationV1,
        CANONICAL_FIXTURES["engine_configuration"].read_bytes(),
    )
    parse_canonical_artifact(
        P1InstrumentCatalogV1,
        CANONICAL_FIXTURES["instrument_catalog"].read_bytes(),
    )
    parse_canonical_artifact(
        P1TargetScheduleV1,
        CANONICAL_FIXTURES["strategy_configuration"].read_bytes(),
    )
    return {
        "account_id": "p1-btcusdt-fixture-account",
        "engine_configuration_sha256": observed["engine_configuration"],
        "instrument_catalog_sha256": observed["instrument_catalog"],
        "liquidity_side": "TAKER",
        "market_data_sha256": observed["market_data"],
        "opening_source": "p1-engine-configuration",
        "opening_source_revision": observed["engine_configuration"],
        "other_money": "0",
        "reconciliation_source": "VENUE",
        "starting_cash": "1000000",
        "starting_currency": "USDT",
        "strategy_configuration_sha256": observed["strategy_configuration"],
        "strategy_id": "p1-target-strategy-v1",
        "window": {"end": END, "start": START},
    }


def _artifact_references() -> dict[str, ArtifactReference]:
    return {
        name: ArtifactReference(
            artifact_id=_ARTIFACT_IDS[name],
            sha256=EXPECTED_DIGESTS[name],
            media_type=(
                "application/jsonl" if name == "market_data" else "application/json"
            ),
        )
        for name in CANONICAL_FIXTURES
    }


def _artifact_bindings(
    arguments: argparse.Namespace,
) -> tuple[EngineArtifactBinding, ...]:
    references = _artifact_references()
    return tuple(
        EngineArtifactBinding(
            reference=references[name],
            source=getattr(arguments, name),
        )
        for name in CANONICAL_FIXTURES
    )


def _enqueue_body() -> dict[str, object]:
    references = _artifact_references()
    return {
        "idempotency_key": "p1:vertical-slice:btc-usdt:20260805",
        "job_type": "BACKTEST",
        "payload": {
            "engine_backtest": {
                **{
                    name: reference.model_dump(mode="json")
                    for name, reference in references.items()
                },
                "end_time": END,
                "start_time": START,
            }
        },
        "priority": 0,
    }


class _FixedProjectionAuthorityFactory:
    """Build only the accepted BTCUSDT fixture authority from request-bound bytes."""

    def __init__(self, arguments: argparse.Namespace) -> None:
        self._catalog_path = arguments.instrument_catalog
        self._catalog_reference = _artifact_references()["instrument_catalog"]
        self._opening_revision = EXPECTED_DIGESTS["engine_configuration"]

    def from_request(self, request: EngineCommandEnvelope) -> ProjectionAuthority:
        if type(request) is not EngineCommandEnvelope or type(request.payload) is not RunBacktest:
            raise VerticalSliceError("P1 projection request is invalid")
        if request.payload.instrument_catalog != self._catalog_reference:
            raise VerticalSliceError("P1 projection catalog binding drifted")
        try:
            raw_catalog = self._catalog_path.read_bytes()
        except OSError as exc:
            raise VerticalSliceError("P1 projection catalog is unavailable") from exc
        if hashlib.sha256(raw_catalog).hexdigest() != self._catalog_reference.sha256:
            raise VerticalSliceError("P1 projection catalog digest drifted")
        try:
            catalog = parse_canonical_artifact(P1InstrumentCatalogV1, raw_catalog)
        except (TypeError, ValueError) as exc:
            raise VerticalSliceError("P1 projection catalog is invalid") from exc
        observed_at = datetime.fromisoformat(START.replace("Z", "+00:00"))
        instrument = InstrumentDefinition(
            instrument_id=InstrumentId(
                catalog.symbol, ProductType.CRYPTO_SPOT, catalog.venue
            ),
            raw_symbol=catalog.symbol,
            asset_class=AssetClass.CRYPTO,
            base_currency=Currency.BTC,
            quote_currency=Currency.USDT,
            settlement_currency=Currency.USDT,
            tick_size=Price(catalog.tick_size, Currency.USDT),
            size_increment=OrderQuantity(catalog.step_size, catalog.size_precision),
            minimum_quantity=OrderQuantity(
                catalog.min_quantity, catalog.size_precision
            ),
            maximum_quantity=OrderQuantity(
                Decimal("1000000"), catalog.size_precision
            ),
            minimum_notional=Money(catalog.min_notional, Currency.USDT),
            maximum_notional=Money(Decimal("100000000"), Currency.USDT),
            multiplier=Decimal("1"),
            margin=None,
            session_calendar="24X7",
            provenance=InstrumentProvenance(
                "P1CATALOG", catalog.provenance_sha256[:32], observed_at
            ),
        )
        zero = Money(Decimal("0"), Currency.USDT)
        account_id = "p1-btcusdt-fixture-account"
        balance = AccountBalanceSnapshot(
            account_id=account_id,
            currency=Currency.USDT,
            cash=Money(Decimal("1000000"), Currency.USDT),
            locked_funds=zero,
            margin_used=zero,
            realized_pnl=zero,
            unrealized_pnl=zero,
            fees=zero,
            funding=zero,
            observed_at=observed_at,
            schema_version="balance-v1",
        )
        return canonical_authority(
            ProjectionAuthority(
                request_message_id=request.message_id,
                catalog=catalog,
                instrument=instrument,
                opening=PortfolioOpeningEntry(
                    account_id=account_id,
                    reporting_currency=Currency.USDT,
                    balances=(balance,),
                    source_id="p1-engine-configuration",
                    source_revision=self._opening_revision,
                    effective_at=observed_at,
                    schema_version="portfolio-entry-v1",
                ),
                strategy_id="p1-target-strategy-v1",
                liquidity_side=LiquiditySide.TAKER,
                reconciliation_source=ReconciliationSource.VENUE,
            )
        )


def _receipt(
    status: str,
    reason: str,
    *,
    authority: dict[str, str],
    evidence: dict[str, object] | None = None,
    job_mutated: bool = False,
) -> dict[str, object]:
    return {
        "authority_limits": {
            "live_authorized": False,
            "network_trading_authorized": False,
            "production_authorized": False,
        },
        "evidence": evidence or {},
        "external_authority": authority,
        "fixture_authority": _fixture_receipt(),
        "job_mutated": job_mutated,
        "reason": reason,
        "schema": SCHEMA,
        "status": status,
    }


def _emit(
    status: str,
    reason: str,
    *,
    authority: dict[str, str],
    evidence: dict[str, object] | None = None,
    job_mutated: bool = False,
) -> int:
    try:
        document = _receipt(
            status,
            reason,
            authority=authority,
            evidence=evidence,
            job_mutated=job_mutated,
        )
    except (OSError, TypeError, ValueError):
        document = {
            "authority_limits": {
                "live_authorized": False,
                "network_trading_authorized": False,
                "production_authorized": False,
            },
            "evidence": {},
            "external_authority": authority,
            "fixture_authority": {},
            "job_mutated": job_mutated,
            "reason": "CANONICAL_FIXTURE_AUTHORITY_INVALID",
            "schema": SCHEMA,
            "status": "BLOCKED",
        }
        status = "BLOCKED"
    print(json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0 if status in {"DEFERRED", "PASS", "READY"} else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--p1-closure-root", type=Path)
    parser.add_argument("--p1-closure-artifacts", type=Path)
    parser.add_argument("--bubblewrap", type=Path)
    parser.add_argument("--transport-root", type=Path)
    parser.add_argument("--postgres-approval", type=Path)
    parser.add_argument("--postgres-scope")
    parser.add_argument("--pgdata", type=Path)
    parser.add_argument("--pg-port")
    for name in CANONICAL_FIXTURES:
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path)
    return parser


def _external_values(arguments: argparse.Namespace) -> tuple[object, ...]:
    return (
        arguments.p1_closure_root,
        arguments.p1_closure_artifacts,
        arguments.bubblewrap,
        arguments.transport_root,
        arguments.postgres_approval,
        arguments.postgres_scope,
        arguments.pgdata,
        arguments.pg_port,
        arguments.engine_configuration,
        arguments.instrument_catalog,
        arguments.strategy_configuration,
        arguments.market_data,
    )


def _source_identity() -> tuple[str, str]:
    status = subprocess.run(
        ("/usr/bin/git", "status", "--porcelain", "--untracked-files=all"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        env=_GIT_ENVIRONMENT,
        text=True,
    )
    if status.stdout:
        raise VerticalSliceError("source checkout is not committed")
    commit, tree = (
        subprocess.check_output(
            ("/usr/bin/git", "rev-parse", value),
            cwd=ROOT,
            env=_GIT_ENVIRONMENT,
            text=True,
        ).strip()
        for value in ("HEAD", "HEAD^{tree}")
    )
    if _COMMIT.fullmatch(commit) is None or _COMMIT.fullmatch(tree) is None:
        raise VerticalSliceError("source identity is invalid")
    return commit, tree


def _sealed_fixture(path: Path, *, expected: str) -> None:
    try:
        resolved = path.resolve(strict=True)
        observed = resolved.lstat()
    except OSError as exc:
        raise VerticalSliceError("external fixture is unavailable") from exc
    try:
        resolved.relative_to(ROOT.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise VerticalSliceError("external fixture must remain outside the checkout")
    if (
        resolved != path
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or observed.st_nlink != 1
        or stat.S_IMODE(observed.st_mode) != 0o400
        or _digest(resolved) != expected
    ):
        raise VerticalSliceError("external fixture authority is invalid")


def _validate_external_fixtures(arguments: argparse.Namespace) -> None:
    paths = {
        name: getattr(arguments, name)
        for name in CANONICAL_FIXTURES
    }
    for name in CANONICAL_FIXTURES:
        path = paths[name]
        assert isinstance(path, Path)
        _sealed_fixture(path, expected=EXPECTED_DIGESTS[name])


def _validate_transport_root(path: Path) -> None:
    try:
        resolved = path.resolve(strict=True)
        observed = path.lstat()
    except OSError as exc:
        raise VerticalSliceError("transport authority is unavailable") from exc
    try:
        resolved.relative_to(ROOT.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise VerticalSliceError("transport authority must remain outside the checkout")
    if (
        not path.is_absolute()
        or resolved != path
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        raise VerticalSliceError("transport authority is invalid")


def _validate_sandbox_executable(path: Path) -> None:
    try:
        policy = json.loads(SANDBOX_POLICY.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise VerticalSliceError("sandbox policy is unavailable") from exc
    if not isinstance(policy, dict):
        raise VerticalSliceError("sandbox policy is invalid")
    expected_path = policy.get("sandbox_path")
    expected_digest = policy.get("sandbox_sha256")
    expected_mode = policy.get("sandbox_mode")
    expected_uid = policy.get("sandbox_uid")
    expected_gid = policy.get("sandbox_gid")
    if (
        not isinstance(expected_path, str)
        or path != Path(expected_path)
        or not isinstance(expected_digest, str)
        or _SHA256.fullmatch(expected_digest) is None
        or not isinstance(expected_mode, str)
        or re.fullmatch(r"0[0-7]{3}", expected_mode, re.ASCII) is None
        or isinstance(expected_uid, bool)
        or not isinstance(expected_uid, int)
        or isinstance(expected_gid, bool)
        or not isinstance(expected_gid, int)
    ):
        raise VerticalSliceError("sandbox policy binding is invalid")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise VerticalSliceError("sandbox executable is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != expected_uid
        or before.st_gid != expected_gid
        or stat.S_IMODE(before.st_mode) != int(expected_mode, 8)
        or (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns, after.st_mtime_ns)
        or digest.hexdigest() != expected_digest
    ):
        raise VerticalSliceError("sandbox executable does not match policy")


def _validate_complete(
    arguments: argparse.Namespace,
    worker_authority: WorkerRuntimeAuthority | None = None,
) -> tuple[dict[str, object], WorkerRuntimeAuthority]:
    if worker_authority is not None and type(worker_authority) is not WorkerRuntimeAuthority:
        raise VerticalSliceError("injected worker runtime authority is invalid")
    commit, tree = _source_identity()
    if arguments.postgres_scope != "DISPOSABLE_PG_GREEN":
        raise VerticalSliceError("PostgreSQL scope is invalid")
    try:
        pg_port = int(arguments.pg_port)
    except (TypeError, ValueError) as exc:
        raise VerticalSliceError("PostgreSQL port is invalid") from exc
    _validate_external_fixtures(arguments)
    _validate_sandbox_executable(arguments.bubblewrap)
    _validate_transport_root(arguments.transport_root)
    approval = load_protected_approval_record(arguments.postgres_approval)
    validation_now = datetime.now(UTC)
    runtime_setting_names = _runtime_setting_names()
    validate_disposable_postgres_approval_record(
        approval,
        expected_scope=arguments.postgres_scope,
        expected_commit=commit,
        expected_tree=tree,
        expected_sql_sha256=None,
        runtime_setting_names=runtime_setting_names,
        now=validation_now,
    )
    context = DisposablePostgresApprovalContext(
        scope=arguments.postgres_scope,
        source_commit=commit,
        source_tree=tree,
        test_path=TEST_PATH,
        operation_id=OPERATION_ID,
        pgdata=str(arguments.pgdata),
        bind_host=BIND_HOST,
        port=pg_port,
        cluster_name=CLUSTER_NAME,
        database_name=DISPOSABLE_DATABASE_NAME,
        runtime_setting_names=runtime_setting_names,
        now=validation_now,
    )
    validate_disposable_postgres_approval(approval, context)
    validate_source_binding_files(approval, ROOT)
    if worker_authority is None:
        worker_authority = attest_worker_runtime_authority()
    if (
        worker_authority.application_revision != commit
        or worker_authority.backend_revision != commit
        or worker_authority.runtime_authority.source_tree != tree
    ):
        raise VerticalSliceError(
            "worker runtime authority does not match the exact checkout"
        )
    closure = attest_p1_nautilus_closure(
        NautilusClosureConfig(
            arguments.p1_closure_root,
            arguments.p1_closure_artifacts,
            arguments.bubblewrap,
        )
    )
    if closure.closure_sha256 != P1_REAL_BACKTEST_POLICY.closure_sha256:
        raise VerticalSliceError("P1 closure does not match the accepted policy")
    return (
        {
            "closure_sha256": closure.closure_sha256,
            "postgres_approval_sha256": canonical_record_sha256(approval),
            "runtime_authority_sha256": (
                worker_authority.authority_document_sha256
            ),
            "source_commit": commit,
            "source_tree": tree,
        },
        worker_authority,
    )


def _store_settings(
    arguments: argparse.Namespace, *, user: str, password: str
) -> JobStoreSettings:
    return JobStoreSettings(
        host=BIND_HOST,
        port=int(arguments.pg_port),
        database=DISPOSABLE_DATABASE_NAME,
        user=user,
        password=password,
    )


def _receipt_digest(receipt: EngineEventBatchReceipt | P1PortfolioParityReceipt) -> str:
    return hashlib.sha256(
        canonical_json_bytes(receipt.model_dump(mode="json"))
    ).hexdigest()


def _durable_success_evidence(
    detail: object, *, expected_job_id: str
) -> dict[str, object]:
    """Close PASS over one exact durable P1 result and parity authority."""

    if type(detail) is not JobDetailRecord:
        raise VerticalSliceExecutionError(job_mutated=True)
    job = detail.job
    if (
        job.job_id != expected_job_id
        or job.state is not JobState.SUCCEEDED
        or job.reason_code != "RESULT_VALIDATED"
        or job.attempt_count != 1
        or not isinstance(job.result_hash, str)
        or _SHA256.fullmatch(job.result_hash) is None
        or len(detail.attempts) != 1
    ):
        raise VerticalSliceExecutionError(job_mutated=True)
    attempt = detail.attempts[0]
    if (
        attempt.job_id != expected_job_id
        or attempt.attempt_number != 1
        or attempt.outcome != "SUCCEEDED"
        or attempt.started_at is None
        or attempt.finished_at is None
        or attempt.exit_code != 0
    ):
        raise VerticalSliceExecutionError(job_mutated=True)
    result_artifacts = tuple(
        artifact
        for artifact in detail.artifacts
        if artifact.artifact_type == "engine_event_batch"
    )
    if len(result_artifacts) != 1:
        raise VerticalSliceExecutionError(job_mutated=True)
    artifact = result_artifacts[0]
    metadata = artifact.validation_metadata
    if (
        artifact.job_id != expected_job_id
        or artifact.attempt_id != attempt.attempt_id
        or artifact.sha256 != job.result_hash
        or artifact.media_type != "application/x-ndjson"
        or artifact.truncated
        or artifact.validator_id != P1_RESULT_VALIDATOR_ID
        or not isinstance(metadata, Mapping)
    ):
        raise VerticalSliceExecutionError(job_mutated=True)
    engine_request_sha256 = metadata.get("engine_request_sha256")
    if (
        not isinstance(engine_request_sha256, str)
        or _SHA256.fullmatch(engine_request_sha256) is None
    ):
        raise VerticalSliceExecutionError(job_mutated=True)
    try:
        engine_receipt = EngineEventBatchReceipt.model_validate_json(
            json.dumps(
                dict(metadata["engine_event_receipt"]),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        parity_receipt = P1PortfolioParityReceipt.model_validate_json(
            json.dumps(
                dict(metadata["p1_portfolio_parity"]),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (KeyError, TypeError, ValueError):
        raise VerticalSliceExecutionError(job_mutated=True) from None
    metadata_identity = {
        "attempt_id": attempt.attempt_id,
        "engine_run_id": str(engine_receipt.engine_run_id),
        "event_count": engine_receipt.event_count,
        "job_id": expected_job_id,
        "last_sequence": engine_receipt.last_sequence,
        "request_message_id": str(parity_receipt.request_message_id),
        "semantic_digest": parity_receipt.semantic_digest,
    }
    if (
        any(metadata.get(name) != value for name, value in metadata_identity.items())
        or engine_receipt.job_id != expected_job_id
        or engine_receipt.attempt_id != attempt.attempt_id
        or engine_receipt.batch_sha256 != artifact.sha256
        or engine_receipt.first_sequence != FIRST_ENGINE_EVENT_SEQUENCE
        or engine_receipt.last_sequence
        != engine_receipt.first_sequence + engine_receipt.event_count - 1
        or parity_receipt.engine_run_id != engine_receipt.engine_run_id
        or parity_receipt.batch_sha256 != engine_receipt.batch_sha256
        or parity_receipt.engine_event_count != engine_receipt.event_count
        or parity_receipt.engine_last_sequence != engine_receipt.last_sequence
        or parity_receipt.engine_last_digest != engine_receipt.last_digest
    ):
        raise VerticalSliceExecutionError(job_mutated=True)
    return {
        "attempt_id": attempt.attempt_id,
        "batch_sha256": engine_receipt.batch_sha256,
        "engine_event_receipt_sha256": _receipt_digest(engine_receipt),
        "engine_request_sha256": engine_request_sha256,
        "engine_run_id": str(engine_receipt.engine_run_id),
        "event_count": engine_receipt.event_count,
        "final_job_state": job.state.value,
        "final_portfolio_state_hash": parity_receipt.portfolio_state_hash,
        "job_id": expected_job_id,
        "last_digest": engine_receipt.last_digest,
        "last_sequence": engine_receipt.last_sequence,
        "p1_portfolio_parity_sha256": _receipt_digest(parity_receipt),
        "result_sha256": artifact.sha256,
        "semantic_digest": parity_receipt.semantic_digest,
    }


def _run_p1_disposable_once(
    arguments: argparse.Namespace,
    worker_authority: WorkerRuntimeAuthority,
    *,
    safety_authority_refresher: P1StagingSafetyAuthorityRefresher | None = None,
) -> dict[str, object]:
    """Enqueue through the dedicated app, then run exactly one P1 worker claim."""

    job_mutated = False
    failure_stage: _ExecutionFailureStage = "JOB_AUTHORITY"
    try:
        api_settings = JobApiSettings(
            bearer_token=_API_TOKEN,
            principal=_PRINCIPAL,
            authority_factory=validate_job_plane_authority,
        )
        api_authority = api_settings.load_authority()
        failure_stage = "DATABASE"
        api_store = _store_settings(
            arguments, user="trading_job_api", password=_API_PASSWORD
        )
        worker_store = _store_settings(
            arguments, user="trading_job_worker", password=_WORKER_PASSWORD
        )
        with JobRepository(api_store) as api_repository, WorkerRepository(
            worker_store
        ) as worker_repository:
            failure_stage = "WORKER_IDENTITY"
            worker_repository.assert_p1_disposable_runtime_identity()
            failure_stage = "WORKER_COMPOSITION"
            worker = build_p1_worker(
                worker_repository,
                {},
                authority=worker_authority,
                closure_config=NautilusClosureConfig(
                    arguments.p1_closure_root,
                    arguments.p1_closure_artifacts,
                    arguments.bubblewrap,
                ),
                transport_root=arguments.transport_root,
                artifact_bindings=_artifact_bindings(arguments),
                p1_projection_authority_factory=_FixedProjectionAuthorityFactory(
                    arguments
                ),
                safety_authority_refresher=safety_authority_refresher,
            )
            failure_stage = "APP_COMPOSITION"
            app = create_p1_disposable_app(
                api_settings, api_repository, api_authority
            )
            failure_stage = "ENQUEUE"
            job_mutated = True
            status_code, response_body = _asgi_json_post(
                app,
                path="/v1/jobs",
                body=_enqueue_body(),
                bearer_token=_API_TOKEN,
            )
            if status_code != 201:
                raise VerticalSliceExecutionError(
                    job_mutated=True, failure_stage=failure_stage
                )
            try:
                job_id = response_body["data"]["job"]["job_id"]
            except (KeyError, TypeError, ValueError) as exc:
                raise VerticalSliceExecutionError(
                    job_mutated=True, failure_stage=failure_stage
                ) from exc
            if not isinstance(job_id, str) or re.fullmatch(
                r"job_[0-9a-f]{32}", job_id, re.ASCII
            ) is None:
                raise VerticalSliceExecutionError(
                    job_mutated=True, failure_stage=failure_stage
                )
            failure_stage = "WORKER"
            if worker.run_once() is not True:
                raise VerticalSliceExecutionError(
                    job_mutated=True, failure_stage=failure_stage
                )
            failure_stage = "DURABLE_RESULT"
            evidence = _durable_success_evidence(
                api_repository.get_job(job_id), expected_job_id=job_id
            )
        return {**evidence, "worker_run_count": 1}
    except Exception as exc:
        if isinstance(exc, VerticalSliceExecutionError):
            job_mutated = job_mutated or exc.job_mutated
        raise VerticalSliceExecutionError(
            job_mutated=job_mutated,
            failure_stage=failure_stage,
            failure_family=(
                _worker_composition_failure_family(exc)
                if failure_stage == "WORKER_COMPOSITION"
                else None
            ),
        ) from exc


def main(
    argv: list[str] | None = None,
    *,
    worker_authority: WorkerRuntimeAuthority | None = None,
    safety_authority_refresher: P1StagingSafetyAuthorityRefresher | None = None,
) -> int:
    if (
        worker_authority is not None
        and type(worker_authority) is not WorkerRuntimeAuthority
    ) or (
        safety_authority_refresher is not None
        and (
            type(safety_authority_refresher)
            is not P1StagingSafetyAuthorityRefresher
            or worker_authority is None
        )
    ):
        return _emit(
            "BLOCKED",
            "EXTERNAL_AUTHORITY_PARTIAL_OR_INVALID",
            authority={"native": "INVALID", "postgres": "INVALID"},
        )
    arguments, unknown = _parser().parse_known_args(argv)
    absent = not any(_external_values(arguments)) and not arguments.execute
    if unknown:
        return _emit(
            "BLOCKED",
            "OPERATOR_ARGUMENT_INVALID",
            authority={"native": "INVALID", "postgres": "INVALID"},
        )
    if absent:
        return _emit(
            "DEFERRED",
            "EXTERNAL_AUTHORITY_ABSENT",
            authority={"native": "ABSENT", "postgres": "ABSENT"},
        )
    if not all(_external_values(arguments)):
        return _emit(
            "BLOCKED",
            "EXTERNAL_AUTHORITY_PARTIAL_OR_INVALID",
            authority={"native": "INVALID", "postgres": "INVALID"},
        )
    try:
        if worker_authority is None:
            evidence, worker_authority = _validate_complete(arguments)
        else:
            evidence, worker_authority = _validate_complete(
                arguments, worker_authority
            )
    except Exception:
        return _emit(
            "BLOCKED",
            "EXTERNAL_AUTHORITY_PARTIAL_OR_INVALID",
            authority={"native": "INVALID", "postgres": "INVALID"},
        )
    if arguments.execute:
        try:
            execution = _run_p1_disposable_once(
                arguments,
                worker_authority,
                safety_authority_refresher=safety_authority_refresher,
            )
        except VerticalSliceExecutionError as error:
            failure_evidence = {**evidence, "failure_stage": error.failure_stage}
            if error.failure_family is not None:
                failure_evidence["failure_family"] = error.failure_family
            return _emit(
                "BLOCKED",
                "P1_EXECUTION_FAILED",
                authority={"native": "VALID", "postgres": "VALID"},
                evidence=failure_evidence,
                job_mutated=error.job_mutated,
            )
        return _emit(
            "PASS",
            "P1_VERTICAL_SLICE_COMPLETED",
            authority={"native": "VALID", "postgres": "VALID"},
            evidence={**evidence, **execution},
            job_mutated=True,
        )
    return _emit(
        "READY",
        "EXTERNAL_AUTHORITY_VALIDATED",
        authority={"native": "VALID", "postgres": "VALID"},
        evidence=evidence,
    )


if __name__ == "__main__":
    raise SystemExit(main())

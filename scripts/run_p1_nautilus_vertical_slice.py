#!/usr/bin/env python3
"""Preflight the exact P1 BTCUSDT vertical slice without ambient authority."""

from __future__ import annotations

import argparse
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
from fastapi.testclient import TestClient
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
from packages.engine_contracts import ArtifactReference, EngineCommandEnvelope, RunBacktest
from packages.engine_portfolio_projection.models import ProjectionAuthority
from packages.engine_portfolio_projection.validation import canonical_authority
from packages.job_contracts import ActorIdentity
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
from services.job_store.repository import JobRepository
from services.job_store.worker_repository import WorkerRepository
from services.job_worker.command_registry import attest_worker_runtime_authority
from services.job_worker.engine_artifacts import EngineArtifactBinding
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
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


class VerticalSliceError(ValueError):
    """Exact qualification input is absent or invalid."""


class VerticalSliceExecutionError(RuntimeError):
    """The bounded execution failed, with exact mutation state retained."""

    def __init__(self, *, job_mutated: bool) -> None:
        super().__init__("P1 disposable execution failed")
        self.job_mutated = job_mutated


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


def _validate_complete(arguments: argparse.Namespace) -> dict[str, object]:
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
    worker_authority = attest_worker_runtime_authority()
    closure = attest_p1_nautilus_closure(
        NautilusClosureConfig(
            arguments.p1_closure_root,
            arguments.p1_closure_artifacts,
            arguments.bubblewrap,
        )
    )
    if closure.closure_sha256 != P1_REAL_BACKTEST_POLICY.closure_sha256:
        raise VerticalSliceError("P1 closure does not match the accepted policy")
    return {
        "closure_sha256": closure.closure_sha256,
        "postgres_approval_sha256": canonical_record_sha256(approval),
        "runtime_authority_sha256": worker_authority.authority_document_sha256,
        "source_commit": commit,
        "source_tree": tree,
    }


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


def _run_p1_disposable_once(arguments: argparse.Namespace) -> dict[str, object]:
    """Enqueue through the dedicated app, then run exactly one P1 worker claim."""

    job_mutated = False
    try:
        api_settings = JobApiSettings(
            bearer_token=_API_TOKEN,
            principal=_PRINCIPAL,
            authority_factory=validate_job_plane_authority,
        )
        api_authority = api_settings.load_authority()
        api_store = _store_settings(
            arguments, user="trading_job_api", password=_API_PASSWORD
        )
        worker_store = _store_settings(
            arguments, user="trading_job_worker", password=_WORKER_PASSWORD
        )
        with JobRepository(api_store) as api_repository, WorkerRepository(
            worker_store
        ) as worker_repository:
            app = create_p1_disposable_app(
                api_settings, api_repository, api_authority
            )
            with TestClient(app) as client:
                job_mutated = True
                response = client.post(
                    "/v1/jobs",
                    json=_enqueue_body(),
                    headers={"Authorization": f"Bearer {_API_TOKEN}"},
                )
            if response.status_code != 201:
                raise VerticalSliceExecutionError(job_mutated=True)
            try:
                job_id = response.json()["data"]["job"]["job_id"]
            except (KeyError, TypeError, ValueError) as exc:
                raise VerticalSliceExecutionError(job_mutated=True) from exc
            if not isinstance(job_id, str) or re.fullmatch(
                r"job_[0-9a-f]{32}", job_id, re.ASCII
            ) is None:
                raise VerticalSliceExecutionError(job_mutated=True)
            worker_repository.assert_p1_disposable_runtime_identity()
            worker_authority = attest_worker_runtime_authority()
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
            )
            if worker.run_once() is not True:
                raise VerticalSliceExecutionError(job_mutated=True)
        return {"job_id": job_id, "worker_run_count": 1}
    except VerticalSliceExecutionError:
        raise
    except Exception as exc:
        raise VerticalSliceExecutionError(job_mutated=job_mutated) from exc


def main(argv: list[str] | None = None) -> int:
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
        evidence = _validate_complete(arguments)
    except Exception:
        return _emit(
            "BLOCKED",
            "EXTERNAL_AUTHORITY_PARTIAL_OR_INVALID",
            authority={"native": "INVALID", "postgres": "INVALID"},
        )
    if arguments.execute:
        try:
            execution = _run_p1_disposable_once(arguments)
        except VerticalSliceExecutionError as error:
            return _emit(
                "BLOCKED",
                "P1_EXECUTION_FAILED",
                authority={"native": "VALID", "postgres": "VALID"},
                evidence=evidence,
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

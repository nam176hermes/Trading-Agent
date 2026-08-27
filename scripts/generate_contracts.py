from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI

# Direct script execution puts ``scripts/`` first on sys.path.  Bootstrap both
# repository packages before importing application modules so callers do not
# need to provide an ambient PYTHONPATH (and cannot accidentally select a stale
# installed package).
ROOT = Path(__file__).resolve().parents[1]
for import_root in (ROOT, ROOT / "apps" / "control_api"):
    import_path = str(import_root)
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from apps.job_api.app import create_app as create_job_app
from apps.job_api.config import JobApiSettings
from control_api.app import create_app as create_control_app
from control_api.config import Settings
from control_api.contracts import (
    ApiErrorEnvelope,
    Asset,
    CapabilityEvidence,
    CostSummary,
    DataFreshness,
    DecisionRecord,
    DeploymentMeta,
    MarketReport,
    Signal,
    SystemStatus,
)
from packages.job_contracts import (
    ActorIdentity,
    ArtifactMetadata,
    AttemptMetadata,
    BacktestPayload,
    DebatePayload,
    EngineBacktestInput,
    EngineBacktestPayload,
    EngineBacktestSimulationInput,
    EngineBacktestSimulationPayload,
    EnqueueJobRequest,
    EventMetadata,
    JobDetail,
    JobMetadata,
    ReplayPayload,
    SnapshotPayload,
)
from packages.domain import (
    AccountBalanceSnapshot,
    AccountPortfolioSnapshot,
    AccountPositionSnapshot,
    EvidenceReference,
    EventEnvelope,
    ExposureSnapshot,
    FillEvent,
    InstrumentExposureSnapshot,
    MarketCandle,
    MarketContinuity,
    MarketDataProvenance,
    MarketSnapshot,
    OrderEvent,
    OrderIntent,
    OrderState,
    PortfolioSnapshot,
    PortfolioConversionEntry,
    PortfolioFillEntry,
    PortfolioFundingEntry,
    PortfolioMarkEntry,
    PortfolioOpeningEntry,
    PortfolioReconciliationEntry,
    PortfolioValuationRateEntry,
    PositionMark,
    PositionSnapshot,
    ResearchPacket,
    RiskDecision,
    RiskStateSnapshot,
    RuntimeInstrumentRiskSpec,
    RuntimeRiskMarketSnapshot,
    RuntimeRiskConversionRate,
    RuntimeVenueHealthRecord,
    PriorRuntimeCommandIdentity,
    RuntimeRiskPolicy,
    RuntimeRiskObservation,
    RuntimeOrderRiskDecision,
    DurableOrderApprovalRef,
    GlobalSafetyObservation,
    GlobalHaltState,
    GlobalHaltRecoveryAuthorization,
    GlobalHaltTransition,
    SubmitPermitPrepared,
    PreparedSubmitPermit,
    SubmitPermitConsumed,
    ConsumedSubmitAuthority,
    SandboxRecoveryCheckpointRecorded,
    SignalProposal,
    StrategyExposureSnapshot,
    TargetPortfolio,
    TargetPosition,
    VenueExposureSnapshot,
)
from packages.event_ledger import (
    AggregateReplayState,
    AppendOutcome,
    AppliedEvent,
    EventTypeCount,
    OutboxIntent,
    ReplayIssue,
    ReplayResult,
    SnapshotRecord,
    StoredEvent,
    StreamProjection,
)
from packages.portfolio_reducer import (
    PortfolioReplayResult,
    PortfolioSnapshotAuthority,
    PortfolioSnapshotRecord,
)
from packages.engine_contracts import (
    EngineCapabilities,
    EngineCommandEnvelope,
    EngineEventEnvelope,
    EngineRunManifest,
    ValidatePaperCompatibility,
)
from packages.nautilus_backtest import PaperCompatibilityResultV1
from packages.runtime_release import (
    RuntimeAuthority,
    ValidatedJobPlaneAuthority,
    validate_job_plane_authority,
)

DEFAULT_TOOL_ROOT = ROOT / "apps" / "dashboard"
TYPESCRIPT_FACTORY_COMPAT = ROOT / "scripts" / "typescript_factory_compat.cjs"
CONTROL_SCHEMA_MODELS = (
    Asset,
    MarketReport,
    Signal,
    DecisionRecord,
    SystemStatus,
    DataFreshness,
    CapabilityEvidence,
    CostSummary,
    DeploymentMeta,
    ApiErrorEnvelope,
)
JOB_SCHEMA_MODELS = (
    ActorIdentity,
    SnapshotPayload,
    DebatePayload,
    ReplayPayload,
    BacktestPayload,
    EngineBacktestInput,
    EngineBacktestPayload,
    EngineBacktestSimulationInput,
    EngineBacktestSimulationPayload,
    EnqueueJobRequest,
    JobMetadata,
    AttemptMetadata,
    EventMetadata,
    ArtifactMetadata,
    JobDetail,
)
DOMAIN_SCHEMA_MODELS = (
    MarketCandle,
    MarketDataProvenance,
    MarketSnapshot,
    MarketContinuity,
    EvidenceReference,
    ResearchPacket,
    SignalProposal,
    TargetPosition,
    TargetPortfolio,
    PositionSnapshot,
    PortfolioSnapshot,
    AccountBalanceSnapshot,
    PositionMark,
    AccountPositionSnapshot,
    ExposureSnapshot,
    InstrumentExposureSnapshot,
    StrategyExposureSnapshot,
    VenueExposureSnapshot,
    AccountPortfolioSnapshot,
    RiskStateSnapshot,
    RiskDecision,
    OrderIntent,
    OrderEvent,
    OrderState,
    FillEvent,
    PortfolioOpeningEntry,
    PortfolioFillEntry,
    PortfolioMarkEntry,
    PortfolioFundingEntry,
    PortfolioConversionEntry,
    PortfolioValuationRateEntry,
    PortfolioReconciliationEntry,
    PortfolioReplayResult,
    PortfolioSnapshotRecord,
    PortfolioSnapshotAuthority,
    EventEnvelope[SignalProposal],
    EventEnvelope[TargetPortfolio],
    EventEnvelope[RiskDecision],
    EventEnvelope[OrderIntent],
    EventEnvelope[OrderEvent],
    EventEnvelope[FillEvent],
    EventEnvelope[PortfolioOpeningEntry],
    EventEnvelope[PortfolioFillEntry],
    EventEnvelope[PortfolioMarkEntry],
    EventEnvelope[PortfolioFundingEntry],
    EventEnvelope[PortfolioConversionEntry],
    EventEnvelope[PortfolioValuationRateEntry],
    EventEnvelope[PortfolioReconciliationEntry],
    EventEnvelope[RuntimeOrderRiskDecision],
    EventEnvelope[GlobalHaltTransition],
    EventEnvelope[SubmitPermitPrepared],
    EventEnvelope[SubmitPermitConsumed],
    EventEnvelope[SandboxRecoveryCheckpointRecorded],
    StoredEvent,
    ReplayIssue,
    AppliedEvent,
    EventTypeCount,
    StreamProjection,
    AggregateReplayState,
    ReplayResult,
    SnapshotRecord,
    OutboxIntent,
    AppendOutcome,
    RuntimeInstrumentRiskSpec,
    RuntimeRiskMarketSnapshot,
    RuntimeRiskConversionRate,
    RuntimeVenueHealthRecord,
    PriorRuntimeCommandIdentity,
    RuntimeRiskPolicy,
    RuntimeRiskObservation,
    RuntimeOrderRiskDecision,
    DurableOrderApprovalRef,
    GlobalSafetyObservation,
    GlobalHaltState,
    GlobalHaltRecoveryAuthorization,
    GlobalHaltTransition,
    SubmitPermitPrepared,
    PreparedSubmitPermit,
    SubmitPermitConsumed,
    ConsumedSubmitAuthority,
    SandboxRecoveryCheckpointRecorded,
)
ENGINE_SCHEMA_MODELS = (
    EngineCapabilities,
    EngineCommandEnvelope,
    EngineEventEnvelope,
    EngineRunManifest,
    ValidatePaperCompatibility,
    PaperCompatibilityResultV1,
)
HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)


def isolated_job_contract_authority() -> ValidatedJobPlaneAuthority:
    """Issue an inert authority used only while rendering the static contract."""

    authority = object.__new__(RuntimeAuthority)
    object.__setattr__(authority, "_identity", (227, 229))
    object.__setattr__(authority, "_document_sha256", "8" * 64)
    return validate_job_plane_authority(
        authority_loader=lambda: authority,
        application_attestor=lambda candidate: candidate is authority,
    )


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o644)


def schema_filename(model: type[object]) -> str:
    """Map generic model names to stable, portable artifact filenames."""

    return model.__name__.replace("[", "_").replace("]", "_") + ".json"


def job_openapi_contract(app: FastAPI) -> dict[str, object]:
    """Render middleware-enforced service auth into the internal contract."""

    document = app.openapi()
    components = document.setdefault("components", {})
    components.setdefault("securitySchemes", {})["ServiceBearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
    }
    for path, path_item in document["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                operation["security"] = [{"ServiceBearerAuth": []}]
    return document


def render(destination: Path, tool_root: Path) -> dict[Path, Path]:
    control_app = create_control_app(
        Settings(
            data_root=Path("/legacy/trading-data"),
            git_commit="generated",
            build_time="generated",
            deployment_id="generated",
        ),
        env={"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"},
    )
    openapi_path = destination / "generated/openapi/openapi.json"
    write_json(openapi_path, control_app.openapi())
    for model in CONTROL_SCHEMA_MODELS:
        write_json(destination / "generated/json-schema" / f"{model.__name__}.json", model.model_json_schema())

    # App construction and OpenAPI rendering are pure: the inert repository is
    # never called, so contract generation cannot connect to or mutate a DB.
    job_app = create_job_app(
        JobApiSettings(
            bearer_token="contract-generation-only",
            principal=ActorIdentity(
                actor_type="OPERATOR", actor_id="contract-generator"
            ),
        ),
        repository=object(),
        authority=isolated_job_contract_authority(),
    )
    job_namespace = destination / "generated/job-api"
    job_openapi_path = job_namespace / "openapi/openapi.json"
    write_json(
        job_openapi_path,
        job_openapi_contract(job_app),
    )
    for model in JOB_SCHEMA_MODELS:
        write_json(
            job_namespace / "json-schema" / f"{model.__name__}.json",
            model.model_json_schema(),
        )

    for model in DOMAIN_SCHEMA_MODELS:
        write_json(
            destination / "generated/domain/json-schema" / schema_filename(model),
            model.model_json_schema(),
        )

    for model in ENGINE_SCHEMA_MODELS:
        write_json(
            destination / "generated/engine/json-schema" / schema_filename(model),
            model.model_json_schema(),
        )

    dashboard_output = destination / "generated" / "dashboard"
    dashboard_output.mkdir(parents=True, exist_ok=True)
    npm_bin = tool_root / "node_modules" / ".bin"
    subprocess.run(
        [
            str(npm_bin / "openapi-typescript"),
            str(openapi_path),
            "--output",
            str(dashboard_output / "api-types.ts"),
            "--immutable",
            "--alphabetize",
        ],
        check=True,
    )
    (dashboard_output / "api-types.ts").chmod(0o644)
    job_dashboard_output = job_namespace / "dashboard"
    job_dashboard_output.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(npm_bin / "openapi-typescript"),
            str(job_openapi_path),
            "--output",
            str(job_dashboard_output / "api-types.ts"),
            "--immutable",
            "--alphabetize",
        ],
        check=True,
    )
    (job_dashboard_output / "api-types.ts").chmod(0o644)
    node_executable = shutil.which("node")
    if node_executable is None:
        raise RuntimeError("node executable is required for contract generation")
    zod_client_entrypoint = (
        tool_root / "node_modules" / "openapi-zod-client" / "bin.js"
    )
    subprocess.run(
        [
            node_executable,
            "--require",
            str(TYPESCRIPT_FACTORY_COMPAT),
            str(zod_client_entrypoint),
            str(openapi_path),
            "--output",
            str(dashboard_output / "api-schemas.ts"),
            "--export-schemas",
            "--export-types",
            "--strict-objects",
            "--all-readonly",
            "--no-with-alias",
        ],
        check=True,
    )
    (dashboard_output / "api-schemas.ts").chmod(0o644)
    return {
        destination / "generated": ROOT / "generated",
        job_dashboard_output / "api-types.ts": (
            ROOT / "apps/dashboard/src/generated/job-api-types.ts"
        ),
    }


def same_file(source: Path, target: Path) -> bool:
    return target.is_file() and source.read_bytes() == target.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--tool-root",
        type=Path,
        default=DEFAULT_TOOL_ROOT,
        help="Node tool installation used only to generate TypeScript artifacts.",
    )
    parser.add_argument(
        "--dashboard-root",
        type=Path,
        default=(Path(os.environ["DASHBOARD_ROOT"]) if "DASHBOARD_ROOT" in os.environ else None),
        help="Optional consumer repository to receive an explicit generated-artifact sync.",
    )
    args = parser.parse_args()
    tool_root = args.tool_root.expanduser().resolve()
    dashboard_root = args.dashboard_root.expanduser().resolve() if args.dashboard_root else None
    with tempfile.TemporaryDirectory(prefix="trading-contracts-") as temporary:
        destination = Path(temporary)
        outputs = render(destination, tool_root)
        if dashboard_root is not None:
            dashboard_output = destination / "generated" / "dashboard"
            outputs.update({
                dashboard_output / "api-types.ts": dashboard_root / "src/generated/api-types.ts",
                dashboard_output / "api-schemas.ts": dashboard_root / "src/generated/api-schemas.ts",
            })
        if args.check:
            stale: list[str] = []
            for source, target in outputs.items():
                if source.is_dir():
                    source_files = sorted(path.relative_to(source) for path in source.rglob("*") if path.is_file())
                    target_files = sorted(path.relative_to(target) for path in target.rglob("*") if path.is_file()) if target.is_dir() else []
                    if source_files != target_files or any(not same_file(source / item, target / item) for item in source_files):
                        stale.append(str(target))
                elif not same_file(source, target):
                    stale.append(str(target))
            if stale:
                print("Generated contract artifacts are stale:")
                for path in stale:
                    print(f"- {path}")
                return 1
            return 0
        for source, target in outputs.items():
            if source.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(source, target)
                for generated_file in target.rglob("*"):
                    if generated_file.is_file():
                        generated_file.chmod(0o644)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                target.chmod(0o644)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Mapping

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import SCHEMA_VERSION
from .config import Settings
from .contracts import (
    ApiEnvelope,
    ApiError,
    ApiErrorEnvelope,
    CapabilityListData,
    CapabilityListEnvelope,
    CapabilityStatus,
    CostSummaryEnvelope,
    DecisionAction,
    DecisionDetailEnvelope,
    DecisionPageEnvelope,
    DeploymentMeta,
    HealthData,
    HealthEnvelope,
    MarketLatestData,
    MarketLatestEnvelope,
    MetaEnvelope,
    Signal,
    SignalListData,
    SignalListEnvelope,
    SystemStatusEnvelope,
)
from .errors import ControlApiError
from .middleware import RequestContextMiddleware
from .repositories.capabilities import LegacyCapabilityRepository
from .repositories.capabilities import PostgresCapabilityRepository
from .repositories.costs import LegacyCostRepository, PostgresCostRepository
from .repositories.decisions import LegacyDecisionRepository, PostgresDecisionRepository
from .repositories.decisions import MAX_DECISION_WINDOW
from .repositories.market import LegacyMarketReportRepository, PostgresMarketReportRepository
from .repositories.status import (
    LegacyOperationalStatusRepository,
    PostgresOperationalStatusRepository,
    PostgresReadinessProbe,
    TRUE_VALUES,
    authority_bound_safety_provider,
)
from trading_control.db import DatabaseSettings
from services.job_worker.safety import SafetySnapshot


def _now() -> datetime:
    return datetime.now(UTC)


def _trace_id(request: Request) -> str:
    return request.state.trace_id


def _envelope(request: Request, data, *, freshness=None):
    return ApiEnvelope(
        schema_version=SCHEMA_VERSION,
        trace_id=_trace_id(request),
        generated_at=_now(),
        data=data,
        freshness=freshness,
    )


def _error_response(request: Request, status_code: int, code: str, message: str, details=None) -> JSONResponse:
    payload = ApiErrorEnvelope(
        schema_version=SCHEMA_VERSION,
        trace_id=_trace_id(request),
        generated_at=_now(),
        error=ApiError(code=code, message=message, details=details or {}),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _identifier(path: Path) -> str:
    return path.name or "root"


def create_app(
    settings: Settings | None = None,
    *,
    env: Mapping[str, str] | None = None,
    safety_provider: Callable[[], SafetySnapshot] | None = None,
) -> FastAPI:
    configured = settings or Settings.from_env()
    environment = os.environ if env is None else env
    app = FastAPI(
        title="Trading Agent Control API",
        version=SCHEMA_VERSION,
        openapi_version="3.1.0",
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = configured
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Accept", "Content-Type", "X-Trace-Id"],
    )

    if configured.store_backend == "postgres":
        database_settings = DatabaseSettings.from_env(environment)
        selected_market_repository = PostgresMarketReportRepository(
            database_settings, stale_after_seconds=configured.stale_after_seconds,
        )
        selected_decision_repository = PostgresDecisionRepository(database_settings)
        selected_capability_repository = PostgresCapabilityRepository(database_settings)
        selected_cost_repository = PostgresCostRepository(database_settings)
        readiness_probe = PostgresReadinessProbe(database_settings)
        selected_status_repository = PostgresOperationalStatusRepository(
            database_settings,
            safety_provider=safety_provider or authority_bound_safety_provider(),
            stale_after_seconds=configured.stale_after_seconds,
            latest_research_at=lambda: selected_market_repository.latest().freshness.as_of,
        )
    else:
        selected_market_repository = LegacyMarketReportRepository(
            configured.data_root, stale_after_seconds=configured.stale_after_seconds,
        )
        selected_decision_repository = LegacyDecisionRepository(configured.data_root)
        selected_capability_repository = LegacyCapabilityRepository(configured.data_root)
        selected_cost_repository = LegacyCostRepository(configured.data_root)
        readiness_probe = None
        selected_status_repository = LegacyOperationalStatusRepository(
            configured.data_root,
            stale_after_seconds=configured.stale_after_seconds,
            env=environment,
            latest_research_at=lambda: selected_market_repository.latest().freshness.as_of,
        )

    def market_repository():
        return selected_market_repository

    def decision_repository():
        return selected_decision_repository

    def status_repository():
        return selected_status_repository

    @app.exception_handler(ControlApiError)
    async def handle_control_error(request: Request, error: ControlApiError):
        return _error_response(request, error.status_code, error.code, error.message, error.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, error: RequestValidationError):
        details = {"issues": [{"location": list(item["loc"]), "message": item["msg"]} for item in error.errors()]}
        return _error_response(request, 422, "INVALID_QUERY", "Request query is invalid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, error: StarletteHTTPException):
        code = "METHOD_NOT_ALLOWED" if error.status_code == 405 else "HTTP_ERROR"
        return _error_response(request, error.status_code, code, str(error.detail))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception):
        return _error_response(request, 500, "INTERNAL_ERROR", "An unexpected internal error occurred.")

    @app.get("/health/live", response_model=HealthEnvelope)
    def health_live(request: Request):
        return _envelope(request, HealthData(status="UP"))

    @app.get("/health/ready", response_model=HealthEnvelope)
    def health_ready(request: Request):
        ready = (
            readiness_probe.ready()
            if readiness_probe is not None
            else configured.data_root.is_dir() and os.access(configured.data_root, os.R_OK)
        )
        status = "READY" if ready else "NOT_READY"
        response = _envelope(request, HealthData(status=status))
        return JSONResponse(status_code=200 if ready else 503, content=response.model_dump(mode="json"))

    @app.get("/v1/meta", response_model=MetaEnvelope)
    def meta(request: Request):
        status = status_repository().get()
        enabled = environment.get("LIVE_EXECUTION_ENABLED", "false").lower() in TRUE_VALUES
        approved = environment.get("LIVE_TRADING_APPROVED", "false").lower() in TRUE_VALUES
        return _envelope(
            request,
            DeploymentMeta(
                service=configured.service_name,
                git_commit=configured.git_commit,
                build_time=configured.build_time,
                deployment_id=configured.deployment_id,
                repo_root_identifier="trading-agent-migration",
                data_root_identifier=_identifier(configured.data_root),
                schema_versions=[SCHEMA_VERSION],
                requested_mode=status.requested_mode,
                effective_mode=status.effective_mode,
                execution_capability=status.execution_capability,
                live_execution_enabled=enabled,
                live_trading_approved=approved,
                kill_switch_state=status.kill_switch_state,
            ),
        )

    @app.get("/v1/system/status", response_model=SystemStatusEnvelope)
    def system_status(request: Request):
        return _envelope(request, status_repository().get())

    @app.get("/v1/market/latest", response_model=MarketLatestEnvelope)
    def market_latest(request: Request):
        result = market_repository().latest()
        return _envelope(request, MarketLatestData(report=result.report), freshness=result.freshness)

    @app.get("/v1/signals", response_model=SignalListEnvelope)
    def signals(request: Request, asset: str | None = None):
        result = market_repository().latest()
        report = result.report
        if report is None:
            return _envelope(request, SignalListData(items=[], total=0), freshness=result.freshness)
        recent = decision_repository().list(page=1, page_size=20).items
        items = [
            Signal(
                asset=item,
                as_of=report.as_of,
                source_report_id=report.report_id,
                recent_decisions=[decision for decision in recent if decision.asset == item.symbol],
            )
            for item in report.assets
            if asset is None or item.symbol == asset.upper()
        ]
        return _envelope(request, SignalListData(items=items, total=len(items)), freshness=result.freshness)

    @app.get("/v1/decisions", response_model=DecisionPageEnvelope)
    def decisions(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        asset: str | None = None,
        action: DecisionAction | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        if page * page_size > MAX_DECISION_WINDOW:
            raise ControlApiError(
                422,
                "INVALID_QUERY",
                "Request query is invalid.",
                {"issues": [{"location": ["query", "page"], "message": "page window is too large"}]},
            )
        data = decision_repository().list(
            page=page,
            page_size=page_size,
            asset=asset,
            action=action,
            date_from=date_from,
            date_to=date_to,
        )
        return _envelope(request, data)

    @app.get("/v1/decisions/{decision_id}", response_model=DecisionDetailEnvelope)
    def decision_detail(request: Request, decision_id: str):
        decision = decision_repository().get(decision_id)
        if decision is None:
            raise ControlApiError(404, "DECISION_NOT_FOUND", "Decision was not found.")
        return _envelope(request, decision)

    @app.get("/v1/capabilities", response_model=CapabilityListEnvelope)
    def capabilities(request: Request):
        items = selected_capability_repository.list()
        verified = sum(item.status is CapabilityStatus.PASS for item in items)
        return _envelope(
            request,
            CapabilityListData(
                items=items,
                total=len(items),
                verified=verified,
                summary="Capability evidence is unavailable until a current benchmark is recorded.",
            ),
        )

    @app.get("/v1/costs", response_model=CostSummaryEnvelope)
    def costs(request: Request):
        return _envelope(request, selected_cost_repository.get())

    return app

"""Loopback Operator API application."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from packages.operator_control.contracts import SubmitOperatorCommandV1
from packages.operator_control.policy import OperatorCommandRejected
from packages.runtime_release import ProtectedAuthorityError
from services.job_worker.errors import SafetyBlockedError
from services.operator_control.journal import CommandJournalError
from services.operator_control.protected_fs import ProtectedFilesystemError
from services.operator_control.state_store import RecoveryError

from .config import OperatorApiConfigurationError, OperatorApiSettings
from .contracts import (
    OperatorApiErrorEnvelope,
    OperatorCommandData,
    OperatorCommandEnvelope,
    OperatorHealthData,
    OperatorHealthEnvelope,
    OperatorStateData,
    OperatorStateEnvelope,
)
from .errors import OperatorApiError
from .middleware import RequestBoundaryMiddleware, error_payload, now


SCHEMA_VERSION = "1.0.0"


def _envelope(request: Request, data: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "trace_id": request.state.trace_id,
        "generated_at": now(),
        "data": data,
    }


def _error_response(
    request: Request, status_code: int, code: str, message: str, details: Any = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(request.scope, code, message, details),
    )


def _message(code: str) -> str:
    return {
        "AUTHENTICATION_REQUIRED": "Valid bearer authentication is required.",
        "CAPABILITY_FORBIDDEN": "The authenticated principal lacks this capability.",
        "PAPER_ONLY_RELEASE": "Only PAPER mode is available in this release.",
        "LIVE_EXECUTION_DISABLED": "Live execution is disabled.",
        "EXPECTED_STATE_CONFLICT": "The expected source state does not match.",
        "EXPECTED_STATE_REQUIRED": "The current source state digest is required.",
        "IDEMPOTENCY_CONFLICT": "The idempotency identity belongs to another request.",
        "KILL_SWITCH_CLEAR_UNSAFE": "The kill switch cannot be cleared safely.",
        "KILL_SWITCH_NOT_ACTIVE": "The kill switch is not active.",
        "SAFETY_EVIDENCE_CHANGED": "Safety evidence changed before application.",
        "COMMAND_OUTCOME_UNKNOWN": "The command outcome is unknown.",
    }.get(code, "Operator authority is unavailable.")


def create_app(
    settings: OperatorApiSettings, service: Any, authenticator: Any
) -> FastAPI:
    """Construct the API without reading credentials, state, DB, or runtime."""

    app = FastAPI(
        title="Trading Agent Operator API",
        version=SCHEMA_VERSION,
        openapi_version="3.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.state.settings = settings
    app.state.service = service
    app.add_middleware(RequestBoundaryMiddleware, authenticator=authenticator)

    @app.exception_handler(OperatorApiError)
    async def handle_api_error(
        request: Request, error: OperatorApiError
    ) -> JSONResponse:
        return _error_response(request, error.status_code, error.code, error.message)

    @app.exception_handler(OperatorCommandRejected)
    async def handle_rejection(
        request: Request, error: OperatorCommandRejected
    ) -> JSONResponse:
        code = (
            error.code if error.http_status < 500 else "OPERATOR_AUTHORITY_UNAVAILABLE"
        )
        return _error_response(request, error.http_status, code, _message(code))

    @app.exception_handler(RecoveryError)
    async def handle_recovery(request: Request, error: RecoveryError) -> JSONResponse:
        return _error_response(
            request, 503, "COMMAND_OUTCOME_UNKNOWN", _message("COMMAND_OUTCOME_UNKNOWN")
        )

    @app.exception_handler(CommandJournalError)
    async def handle_unsafe_journal(
        request: Request, error: CommandJournalError
    ) -> JSONResponse:
        return _error_response(
            request,
            503,
            "OPERATOR_AUTHORITY_UNAVAILABLE",
            _message("OPERATOR_AUTHORITY_UNAVAILABLE"),
        )

    async def handle_authority_unavailable(
        request: Request, error: Exception
    ) -> JSONResponse:
        return _error_response(
            request,
            503,
            "OPERATOR_AUTHORITY_UNAVAILABLE",
            _message("OPERATOR_AUTHORITY_UNAVAILABLE"),
        )

    for error_type in (
        ProtectedFilesystemError,
        SafetyBlockedError,
        ProtectedAuthorityError,
        OperatorApiConfigurationError,
    ):
        app.add_exception_handler(error_type, handle_authority_unavailable)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        issues = [
            {"location": list(item["loc"]), "message": item["msg"]}
            for item in error.errors()
        ]
        return _error_response(
            request,
            422,
            "INVALID_REQUEST",
            "Request data is invalid.",
            {"issues": issues},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        code = "METHOD_NOT_ALLOWED" if error.status_code == 405 else "NOT_FOUND"
        message = (
            "Method is not allowed."
            if error.status_code == 405
            else "Route was not found."
        )
        return _error_response(request, error.status_code, code, message)

    def error(description: str) -> dict[str, Any]:
        return {"model": OperatorApiErrorEnvelope, "description": description}

    boundary_errors: dict[int | str, dict[str, Any]] = {
        413: error("Request body exceeds the bounded service limit."),
        500: error("An unexpected internal error occurred."),
    }
    authenticated_errors: dict[int | str, dict[str, Any]] = {
        401: error("Operator authentication is required."),
        403: error("The authenticated principal lacks this capability."),
        503: error("Operator authority or command outcome is unavailable."),
        **boundary_errors,
    }
    command_errors: dict[int | str, dict[str, Any]] = {
        409: error("The command conflicts with current durable state."),
        422: error("Request data is invalid."),
        **authenticated_errors,
    }

    @app.get(
        "/health/live",
        response_model=OperatorHealthEnvelope,
        responses=boundary_errors,
    )
    def health_live(request: Request) -> OperatorHealthEnvelope:
        return OperatorHealthEnvelope.model_validate(
            _envelope(request, OperatorHealthData(status="UP"))
        )

    @app.get(
        "/health/ready",
        response_model=OperatorHealthEnvelope,
        responses=boundary_errors,
    )
    def health_ready(request: Request) -> OperatorHealthEnvelope:
        return OperatorHealthEnvelope.model_validate(
            _envelope(request, OperatorHealthData(status="READY"))
        )

    @app.get(
        "/v1/state",
        response_model=OperatorStateEnvelope,
        responses=authenticated_errors,
    )
    def operator_state(request: Request) -> OperatorStateEnvelope:
        state = service.read_state(request.state.principal)
        return OperatorStateEnvelope.model_validate(
            _envelope(request, OperatorStateData(state=state))
        )

    @app.post(
        "/v1/commands",
        response_model=OperatorCommandEnvelope,
        responses=command_errors,
    )
    def submit_command(
        request: Request, command: SubmitOperatorCommandV1
    ) -> OperatorCommandEnvelope:
        result = service.execute(request.state.principal, command)
        return OperatorCommandEnvelope.model_validate(
            _envelope(request, OperatorCommandData(result=result))
        )

    return app


__all__ = ["create_app"]

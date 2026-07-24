from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Callable, TypeVar

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from packages.job_contracts import (
    ActorIdentity,
    ActorType,
    ArtifactMetadata,
    AttemptMetadata,
    EnqueueJobBody,
    EnqueueJobRequest,
    EventMetadata,
    JobMetadata,
    JobState,
    JobType,
)
from packages.job_contracts.transitions import InvalidTransition
from packages.runtime_release import (
    ProtectedAuthorityError,
    ValidatedJobPlaneAuthority,
)
from services.job_store import (
    IdempotencyConflict,
    InvalidJobFilters,
    InvalidTraceId,
    JobFilters,
    JobNotFound,
    JobStoreError,
    StaleTransition,
)

from .auth import BearerAuthenticator
from .config import JobApiSettings
from .contracts import (
    HealthLiveData,
    HealthLiveEnvelope,
    HealthReadyData,
    HealthReadyEnvelope,
    JobApiErrorEnvelope,
    JobDeduplicatedData,
    JobDeduplicatedEnvelope,
    JobDetailEnvelope,
    JobEnvelope,
    JobEnqueuedData,
    JobEnqueuedEnvelope,
    JobListData,
    JobListEnvelope,
)
from .errors import JobApiError


SCHEMA_VERSION = "1.0.0"
MAX_REQUEST_BODY_BYTES = 16 * 1024
LOGGER = logging.getLogger("job_api.request")
_T = TypeVar("_T")
_EnvelopeT = TypeVar("_EnvelopeT", bound=BaseModel)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _trace_id_from_scope(scope: Scope) -> str:
    return scope.setdefault("state", {}).setdefault(
        "trace_id", f"trace_{uuid.uuid4().hex}"
    )


def _error_payload(scope: Scope, code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "trace_id": _trace_id_from_scope(scope),
        "generated_at": _now(),
        "error": {"code": code, "message": message, "details": details or {}},
    }


class RequestBoundaryMiddleware:
    """Assign trace identity, cap bodies before parsing, and log metadata only."""

    def __init__(self, app: ASGIApp, authenticator: BearerAuthenticator) -> None:
        self.app = app
        self.authenticator = authenticator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        trace_id = f"trace_{uuid.uuid4().hex}"
        scope.setdefault("state", {})["trace_id"] = trace_id

        if self._requires_authentication(scope):
            try:
                scope["state"]["principal"] = self.authenticator.authenticate(scope)
            except JobApiError as error:
                response = JSONResponse(
                    status_code=error.status_code,
                    content=_error_payload(
                        scope, error.code, error.message, error.details
                    ),
                )
                await self._send_response(response, scope, receive, send, trace_id)
                self._log(scope, error.status_code, trace_id, started)
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                self._log(scope, 499, trace_id, started)
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BODY_BYTES:
                response = JSONResponse(
                    status_code=413,
                    content=_error_payload(
                        scope,
                        "REQUEST_BODY_TOO_LARGE",
                        "Request body exceeds the 16 KiB limit.",
                    ),
                )
                await self._send_response(response, scope, receive, send, trace_id)
                self._log(scope, 413, trace_id, started)
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        status_code = 500
        response_started = False

        async def wrapped_send(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                response_headers = list(message.get("headers", ()))
                response_headers.extend(
                    (
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-trace-id", trace_id.encode("ascii")),
                    )
                )
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, replay_receive, wrapped_send)
        except Exception as error:
            LOGGER.error(
                json.dumps(
                    {
                        "trace_id": trace_id,
                        "endpoint": self._endpoint(scope),
                        "event": "unexpected_error",
                        "exception_type": type(error).__name__,
                    }
                )
            )
            if not response_started:
                response = JSONResponse(
                    status_code=500,
                    content=_error_payload(
                        scope,
                        "INTERNAL_ERROR",
                        "An unexpected internal error occurred.",
                    ),
                )
                await self._send_response(response, scope, receive, send, trace_id)
            status_code = 500
        self._log(scope, status_code, trace_id, started)

    @staticmethod
    def _requires_authentication(scope: Scope) -> bool:
        path = scope.get("path", "")
        return path == "/v1" or path.startswith("/v1/")

    @staticmethod
    def _endpoint(scope: Scope) -> str:
        method = scope.get("method", "")
        path = scope.get("path", "")
        if path == "/health/live":
            return "health.live"
        if path == "/health/ready":
            return "health.ready"
        if path == "/v1/jobs":
            return "jobs.create" if method == "POST" else "jobs.list"
        if path.startswith("/v1/jobs/"):
            suffix = path[len("/v1/jobs/") :]
            if suffix and "/" not in suffix:
                return "jobs.detail"
            if suffix.endswith("/cancel") and "/" not in suffix[:-7]:
                return "jobs.cancel"
        return "unmatched"

    @staticmethod
    async def _send_response(
        response: JSONResponse,
        scope: Scope,
        receive: Receive,
        send: Send,
        trace_id: str,
    ) -> None:
        async def secured_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": list(message.get("headers", ()))
                    + [
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-trace-id", trace_id.encode("ascii")),
                    ],
                }
            await send(message)

        await response(scope, receive, secured_send)

    @staticmethod
    def _log(scope: Scope, status_code: int, trace_id: str, started: float) -> None:
        LOGGER.info(
            json.dumps(
                {
                    "trace_id": trace_id,
                    "method": scope["method"],
                    "endpoint": RequestBoundaryMiddleware._endpoint(scope),
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
        )


def _probe_repository(repository: Any, expected_revision: str) -> tuple[bool, bool]:
    if repository is None:
        return False, False
    pool = getattr(repository, "_pool", None)
    if pool is None:
        return False, False
    try:
        with pool.connection() as connection:
            connection.execute("SELECT 1").fetchone()
    except Exception:
        return False, False
    try:
        with pool.connection() as connection:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        revision = row.get("version_num") if isinstance(row, Mapping) else row[0]
        return True, bool(revision == expected_revision)
    except Exception:
        return True, False


def _envelope(request: Request, data: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "trace_id": request.state.trace_id,
        "generated_at": _now(),
        "data": data,
    }


def _contract_envelope(
    model: type[_EnvelopeT], request: Request, data: Any
) -> _EnvelopeT:
    return model.model_validate(_envelope(request, data))


def _error_response(
    request: Request, status_code: int, code: str, message: str, details: Any = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=_error_payload(request.scope, code, message, details),
    )


class CancelJobBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _job_metadata(record: Any) -> dict[str, Any]:
    return JobMetadata.model_validate(
        {
            "job_id": record.job_id,
            "job_type": record.job_type,
            "state": record.state,
            "payload": record.payload,
            "payload_fingerprint": record.payload_fingerprint,
            "actor": record.actor,
            "priority": record.priority,
            "requested_at": record.requested_at,
            "updated_at": record.updated_at,
            "attempt_count": record.attempt_count,
            "reason_code": record.reason_code,
            "result_hash": record.result_hash,
        }
    ).model_dump(mode="json")


def _job_detail(detail: Any) -> dict[str, Any]:
    artifact_counts: dict[str, int] = {}
    for artifact in detail.artifacts:
        artifact_counts[artifact.attempt_id] = artifact_counts.get(artifact.attempt_id, 0) + 1
    attempts = [
        AttemptMetadata(
            attempt_id=attempt.attempt_id,
            attempt_number=attempt.attempt_number,
            worker_id=attempt.worker_id,
            claimed_at=attempt.claimed_at,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            exit_code=attempt.exit_code,
            termination_reason=attempt.termination_reason,
            artifact_count=artifact_counts.get(attempt.attempt_id, 0),
        ).model_dump(mode="json")
        for attempt in detail.attempts
    ]
    events = [
        EventMetadata(
            event_id=event.event_id,
            sequence=event.sequence,
            from_state=event.from_state,
            to_state=event.to_state,
            reason_code=event.reason_code,
            actor=event.actor,
            trace_id=event.trace_id,
            created_at=event.created_at,
        ).model_dump(mode="json")
        for event in detail.events
    ]
    artifacts = [
        ArtifactMetadata(
            artifact_id=artifact.artifact_id,
            attempt_id=artifact.attempt_id,
            artifact_type=artifact.artifact_type,
            validator_id=artifact.validator_id,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            created_at=artifact.created_at,
        ).model_dump(mode="json")
        for artifact in detail.artifacts
    ]
    return {
        "job": _job_metadata(detail.job),
        "attempts": attempts,
        "events": events,
        "artifacts": artifacts,
    }


def _call_repository(operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except IdempotencyConflict as error:
        raise JobApiError(
            409,
            "IDEMPOTENCY_CONFLICT",
            "Idempotency identity belongs to a different request.",
        ) from error
    except JobNotFound as error:
        raise JobApiError(404, "JOB_NOT_FOUND", "Job was not found.") from error
    except (InvalidTransition, StaleTransition) as error:
        raise JobApiError(
            409, "INVALID_JOB_STATE", "Job state does not permit this operation."
        ) from error
    except (InvalidJobFilters, InvalidTraceId, ValueError) as error:
        raise JobApiError(422, "INVALID_REQUEST", "Request data is invalid.") from error
    except (ConnectionError, TimeoutError, psycopg.Error) as error:
        raise JobApiError(
            503,
            "REPOSITORY_UNAVAILABLE",
            "The durable job repository is unavailable.",
        ) from error
    except JobStoreError as error:
        raise JobApiError(
            503,
            "REPOSITORY_UNAVAILABLE",
            "The durable job repository is unavailable.",
        ) from error
    except Exception as error:
        raise JobApiError(
            500, "INTERNAL_ERROR", "An unexpected internal error occurred."
        ) from error


def _require_mutation_authority(
    authority: ValidatedJobPlaneAuthority,
    repository: Any,
    expected_revision: str,
) -> None:
    try:
        authority.recheck_mutation()
    except Exception:
        raise JobApiError(
            503,
            "JOB_PLANE_AUTHORITY_UNAVAILABLE",
            "Job-plane mutation authority is unavailable.",
        ) from None
    database_ready, revision_ready = _probe_repository(
        repository, expected_revision
    )
    if not database_ready or not revision_ready:
        raise JobApiError(
            503,
            "REPOSITORY_UNAVAILABLE",
            "The durable job repository is unavailable.",
        )


def _authority_ready(authority: ValidatedJobPlaneAuthority) -> bool:
    try:
        authority.recheck_mutation()
        return True
    except Exception:
        return False


def create_app(
    settings: JobApiSettings,
    repository: Any,
    authority: ValidatedJobPlaneAuthority,
) -> FastAPI:
    if not isinstance(authority, ValidatedJobPlaneAuthority):
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID") from None
    try:
        authority.recheck_mutation()
    except Exception:
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID") from None
    app = FastAPI(
        title="Trading Agent Job Command API",
        version=SCHEMA_VERSION,
        openapi_version="3.1.0",
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.repository = repository
    authenticate = BearerAuthenticator(settings)
    app.add_middleware(RequestBoundaryMiddleware, authenticator=authenticate)

    @app.exception_handler(JobApiError)
    async def handle_job_api_error(request: Request, error: JobApiError) -> JSONResponse:
        return _error_response(
            request, error.status_code, error.code, error.message, error.details
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        issues = [
            {"location": list(item["loc"]), "message": item["msg"]}
            for item in error.errors()
        ]
        return _error_response(
            request, 422, "INVALID_REQUEST", "Request data is invalid.", {"issues": issues}
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        code = "METHOD_NOT_ALLOWED" if error.status_code == 405 else "NOT_FOUND"
        message = "Method is not allowed." if error.status_code == 405 else "Route was not found."
        return _error_response(request, error.status_code, code, message)

    def error_response(description: str) -> dict[str, Any]:
        return {"model": JobApiErrorEnvelope, "description": description}
    common_errors = {
        401: error_response("Service authentication is required."),
        413: error_response("Request body exceeds the bounded service limit."),
        500: error_response("An unexpected internal error occurred."),
        503: error_response("Authentication or the durable repository is unavailable."),
    }
    health_boundary_errors = {
        413: error_response("Request body exceeds the bounded service limit."),
        500: error_response("An unexpected internal error occurred."),
    }

    @app.get(
        "/health/live",
        response_model=HealthLiveEnvelope,
        responses=health_boundary_errors,
    )
    def health_live(request: Request) -> HealthLiveEnvelope:
        return _contract_envelope(
            HealthLiveEnvelope, request, HealthLiveData(status="UP")
        )

    @app.get(
        "/health/ready",
        response_model=HealthReadyEnvelope,
        responses={
            503: {
                "model": HealthReadyEnvelope,
                "description": "Process is live but the service is not ready.",
            },
            **health_boundary_errors,
        },
    )
    def health_ready(request: Request) -> JSONResponse:
        database_ready, revision_ready = _probe_repository(
            repository, settings.expected_revision
        )
        ready = (
            settings.bearer_token is not None
            and settings.principal is not None
            and _authority_ready(authority)
            and database_ready
            and revision_ready
        )
        payload = _contract_envelope(
            HealthReadyEnvelope,
            request,
            HealthReadyData(status="READY" if ready else "NOT_READY"),
        )
        return JSONResponse(
            status_code=200 if ready else 503, content=payload.model_dump(mode="json")
        )

    @app.post(
        "/v1/jobs",
        status_code=201,
        response_model=JobEnqueuedEnvelope,
        responses={
            200: {
                "model": JobDeduplicatedEnvelope,
                "description": "An identical canonical job already exists.",
            },
            409: error_response("The idempotency key conflicts with another request."),
            422: error_response("Request data is invalid."),
            **common_errors,
        },
    )
    def create_job(request: Request, command: EnqueueJobBody) -> JSONResponse:
        authenticated_command = EnqueueJobRequest.model_validate(
            {**command.model_dump(mode="python"), "actor": request.state.principal}
        )
        _require_mutation_authority(
            authority, repository, settings.expected_revision
        )
        result = _call_repository(
            lambda: repository.enqueue(
                authenticated_command, trace_id=request.state.trace_id
            )
        )
        if result.outcome.value == "ENQUEUED":
            payload = _contract_envelope(
                JobEnqueuedEnvelope,
                request,
                JobEnqueuedData(outcome="ENQUEUED", job=_job_metadata(result.job)),
            )
        else:
            payload = _contract_envelope(
                JobDeduplicatedEnvelope,
                request,
                JobDeduplicatedData(
                    outcome="DEDUPLICATED", job=_job_metadata(result.job)
                ),
            )
        return JSONResponse(
            status_code=201 if result.outcome.value == "ENQUEUED" else 200,
            content=payload.model_dump(mode="json"),
        )

    @app.get(
        "/v1/jobs",
        response_model=JobListEnvelope,
        responses={422: error_response("Request filters are invalid."), **common_errors},
    )
    def list_jobs(
        request: Request,
        job_type: JobType | None = None,
        state: JobState | None = None,
        actor_type: ActorType | None = None,
        actor_id: str | None = Query(default=None, min_length=1, max_length=128),
        requested_from: datetime | None = None,
        requested_to: datetime | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> JobListEnvelope:
        filters = _call_repository(
            lambda: JobFilters(
                job_type=job_type,
                state=state,
                actor_type=actor_type.value if actor_type else None,
                actor_id=actor_id,
                requested_from=requested_from,
                requested_to=requested_to,
                limit=limit,
                offset=offset,
            )
        )
        items = _call_repository(lambda: repository.list_jobs(filters))
        return _contract_envelope(
            JobListEnvelope,
            request,
            JobListData(
                items=[_job_metadata(item) for item in items],
                limit=limit,
                offset=offset,
            ),
        )

    @app.get(
        "/v1/jobs/{job_id}",
        response_model=JobDetailEnvelope,
        responses={
            404: error_response("Job was not found."),
            422: error_response("Request data is invalid."),
            **common_errors,
        },
    )
    def job_detail(request: Request, job_id: str) -> JobDetailEnvelope:
        detail = _call_repository(lambda: repository.get_job(job_id))
        if detail is None:
            raise JobApiError(404, "JOB_NOT_FOUND", "Job was not found.")
        return _contract_envelope(JobDetailEnvelope, request, _job_detail(detail))

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        response_model=JobEnvelope,
        responses={
            404: error_response("Job was not found."),
            409: error_response("Job state does not permit cancellation."),
            422: error_response("Request data is invalid."),
            **common_errors,
        },
    )
    def cancel_job(
        request: Request, job_id: str, command: CancelJobBody
    ) -> JobEnvelope:
        _require_mutation_authority(
            authority, repository, settings.expected_revision
        )
        job = _call_repository(
            lambda: repository.request_cancel(
                job_id, request.state.principal, request.state.trace_id
            )
        )
        return _contract_envelope(JobEnvelope, request, _job_metadata(job))

    return app

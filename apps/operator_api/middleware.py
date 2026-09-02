"""Bounded request and response boundary for the Operator API."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import OperatorApiError


SCHEMA_VERSION = "1.0.0"
MAX_REQUEST_BODY_BYTES = 8 * 1024
LOGGER = logging.getLogger("operator_api.request")


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def trace_id(scope: Scope) -> str:
    return scope.setdefault("state", {}).setdefault(
        "trace_id", f"trace_{uuid.uuid4().hex}"
    )


def error_payload(
    scope: Scope, code: str, message: str, details: Any = None
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "trace_id": trace_id(scope),
        "generated_at": now(),
        "error": {"code": code, "message": message, "details": details or {}},
    }


class RequestBoundaryMiddleware:
    def __init__(self, app: ASGIApp, authenticator: Any) -> None:
        self.app = app
        self.authenticator = authenticator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        request_trace_id = f"trace_{uuid.uuid4().hex}"
        scope.setdefault("state", {})["trace_id"] = request_trace_id

        if self._requires_authentication(scope):
            try:
                scope["state"]["principal"] = self.authenticator.authenticate(scope)
            except OperatorApiError as error:
                response = JSONResponse(
                    status_code=error.status_code,
                    content=error_payload(scope, error.code, error.message),
                )
                await self._send_response(
                    response, scope, receive, send, request_trace_id
                )
                self._log(scope, error.status_code, request_trace_id, started)
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                self._log(scope, 499, request_trace_id, started)
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BODY_BYTES:
                response = JSONResponse(
                    status_code=413,
                    content=error_payload(
                        scope,
                        "REQUEST_BODY_TOO_LARGE",
                        "Request body exceeds the 8 KiB limit.",
                    ),
                )
                await self._send_response(
                    response, scope, receive, send, request_trace_id
                )
                self._log(scope, 413, request_trace_id, started)
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

        async def secured_send(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                message = self._with_headers(message, request_trace_id)
            await send(message)

        try:
            await self.app(scope, replay_receive, secured_send)
        except Exception:
            if not response_started:
                response = JSONResponse(
                    status_code=500,
                    content=error_payload(
                        scope,
                        "INTERNAL_ERROR",
                        "An unexpected internal error occurred.",
                    ),
                )
                await self._send_response(
                    response, scope, receive, send, request_trace_id
                )
            status_code = 500
        self._log(scope, status_code, request_trace_id, started)

    @staticmethod
    def _requires_authentication(scope: Scope) -> bool:
        path = scope.get("path", "")
        return path == "/v1" or path.startswith("/v1/")

    @staticmethod
    def _endpoint(scope: Scope) -> str:
        return {
            ("GET", "/health/live"): "health.live",
            ("GET", "/health/ready"): "health.ready",
            ("GET", "/v1/state"): "operator.state",
            ("POST", "/v1/commands"): "operator.commands",
        }.get((scope.get("method", ""), scope.get("path", "")), "unmatched")

    @staticmethod
    def _with_headers(message: Message, request_trace_id: str) -> Message:
        return {
            **message,
            "headers": list(message.get("headers", ()))
            + [
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
                (b"x-trace-id", request_trace_id.encode("ascii")),
            ],
        }

    @classmethod
    async def _send_response(
        cls,
        response: JSONResponse,
        scope: Scope,
        receive: Receive,
        send: Send,
        request_trace_id: str,
    ) -> None:
        async def secured_send(message: Message) -> None:
            await send(
                cls._with_headers(message, request_trace_id)
                if message["type"] == "http.response.start"
                else message
            )

        await response(scope, receive, secured_send)

    @classmethod
    def _log(
        cls, scope: Scope, status_code: int, request_trace_id: str, started: float
    ) -> None:
        record: dict[str, object] = {
            "trace_id": request_trace_id,
            "method": scope["method"],
            "endpoint": cls._endpoint(scope),
            "status_code": status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        principal = scope.get("state", {}).get("principal")
        if principal is not None:
            record["principal_id"] = principal.principal_id
        LOGGER.info(json.dumps(record))


__all__ = [
    "MAX_REQUEST_BODY_BYTES",
    "RequestBoundaryMiddleware",
    "error_payload",
    "now",
]

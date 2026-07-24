from __future__ import annotations

import json
import logging
import re
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from . import SCHEMA_VERSION

LOGGER = logging.getLogger("control_api.request")
TRACE_ID_PATTERN = re.compile(r"^trace_[A-Za-z0-9][A-Za-z0-9._:-]{0,121}$", re.ASCII)


def _trace_id(value: str) -> str:
    return value if TRACE_ID_PATTERN.fullmatch(value) else f"trace_{uuid.uuid4().hex}"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        trace_id = _trace_id(request.headers.get("x-trace-id", ""))
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Trace-Id"] = trace_id
        LOGGER.info(
            json.dumps(
                {
                    "trace_id": trace_id,
                    "route": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "schema_version": SCHEMA_VERSION,
                }
            )
        )
        return response

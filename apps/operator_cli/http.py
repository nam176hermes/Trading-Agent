"""Small fail-closed JSON client for fixed loopback APIs."""

from __future__ import annotations

import json
import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class HttpClientError(RuntimeError):
    """Sanitized upstream or transport failure."""

    def __init__(self, code: str, *, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


class BoundedJsonHttpClient:
    def __init__(
        self,
        origin: str,
        *,
        token: str | None = None,
        correlation_id: str | None = None,
        timeout_seconds: float = 5.0,
        max_response_bytes: int = 256 * 1024,
        opener: Any = None,
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("API origin must be literal loopback HTTP")
        canonical = f"http://127.0.0.1:{parsed.port}"
        if origin.rstrip("/") != canonical:
            raise ValueError("API origin must be literal loopback HTTP")
        if token is not None and (
            not token
            or token.strip() != token
            or not token.isascii()
            or any(ord(char) < 0x21 for char in token)
        ):
            raise ValueError("bearer token is invalid")
        if correlation_id is not None and _IDENTITY.fullmatch(correlation_id) is None:
            raise ValueError("correlation identity is invalid")
        if timeout_seconds <= 0 or max_response_bytes < 2:
            raise ValueError("HTTP bounds are invalid")
        self._origin = canonical
        self._token = token
        self._correlation_id = correlation_id
        self._timeout = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._opener = opener or build_opener(_NoRedirect())

    def get(self, path: str) -> dict[str, object]:
        return self._request("GET", path, None)

    def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self._request("POST", path, raw)

    def _url(self, path: str) -> str:
        parsed = urlsplit(path)
        if (
            not path.startswith("/v1/")
            or path.startswith("//")
            or "\\" in path
            or "%" in parsed.path
            or any(part in {".", ".."} for part in parsed.path.split("/"))
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
        ):
            raise ValueError("API path must be a versioned relative path")
        return f"{self._origin}{path}"

    def _request(self, method: str, path: str, body: bytes | None) -> dict[str, object]:
        url = self._url(path)
        headers = {"Accept": "application/json"}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._correlation_id is not None:
            headers["X-Trace-Id"] = self._correlation_id
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.geturl() != url:
                    raise HttpClientError("REDIRECT_REFUSED")
                return self._decode(response, status=None)
        except HTTPError as error:
            if 300 <= error.code < 400:
                raise HttpClientError("REDIRECT_REFUSED", status=error.code) from None
            try:
                payload = self._decode(error, status=error.code)
            except HttpClientError:
                raise HttpClientError("HTTP_STATUS", status=error.code) from None
            candidate = payload.get("error")
            code = candidate.get("code") if isinstance(candidate, dict) else None
            raise HttpClientError(
                code
                if isinstance(code, str) and _ERROR_CODE.fullmatch(code)
                else "HTTP_STATUS",
                status=error.code,
            ) from None
        except (TimeoutError, socket.timeout):
            raise HttpClientError("TIMEOUT") from None
        except (OSError, URLError):
            raise HttpClientError("API_UNAVAILABLE") from None

    def _decode(self, response: Any, *, status: int | None) -> dict[str, object]:
        content_type = response.headers.get("content-type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise HttpClientError("INVALID_CONTENT_TYPE", status=status)
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_response_bytes:
                    raise HttpClientError("RESPONSE_TOO_LARGE", status=status)
            except ValueError:
                raise HttpClientError("INVALID_RESPONSE", status=status) from None
        raw = response.read(self._max_response_bytes + 1)
        if len(raw) > self._max_response_bytes:
            raise HttpClientError("RESPONSE_TOO_LARGE", status=status)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpClientError("INVALID_RESPONSE", status=status) from None
        if not isinstance(payload, dict):
            raise HttpClientError("INVALID_RESPONSE", status=status)
        return payload


__all__ = ["BoundedJsonHttpClient", "HttpClientError"]

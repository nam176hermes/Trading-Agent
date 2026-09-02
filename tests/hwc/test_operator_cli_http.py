from __future__ import annotations

import io
import json
import socket
from email.message import Message
from urllib.error import HTTPError

import pytest

from apps.operator_cli.http import BoundedJsonHttpClient, HttpClientError


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        content_type: str = "application/json",
        url: str = "http://127.0.0.1:8400/v1/system/status",
    ) -> None:
        self._body = io.BytesIO(body)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.url = url

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def geturl(self) -> str:
        return self.url

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Opener:
    def __init__(self, result: object) -> None:
        self.result = result
        self.requests: list[tuple[object, float]] = []

    def open(self, request: object, *, timeout: float) -> _Response:
        self.requests.append((request, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        assert isinstance(self.result, _Response)
        return self.result


@pytest.mark.parametrize(
    "origin",
    [
        "https://127.0.0.1:8400",
        "http://localhost:8400",
        "http://127.0.0.2:8400",
        "http://user@127.0.0.1:8400",
        "http://127.0.0.1:8400/base",
        "http://127.0.0.1:8400?query=1",
    ],
)
def test_client_rejects_any_origin_outside_literal_loopback_http(origin: str) -> None:
    """Break caught: configuration can redirect CLI credentials off loopback."""
    with pytest.raises(ValueError, match="loopback"):
        BoundedJsonHttpClient(origin)


def test_post_emits_canonical_json_bytes_and_bearer_authentication() -> None:
    """Break caught: a mutation retry can change bytes or omit authentication."""
    opener = _Opener(_Response(b'{"ok":true}', url="http://127.0.0.1:8402/v1/commands"))
    client = BoundedJsonHttpClient(
        "http://127.0.0.1:8402",
        token=str.join("-", ("cli", "token")),
        correlation_id="corr_0123456789abcdef0123456789abcdef",
        opener=opener,
    )

    assert client.post("/v1/commands", {"z": 1, "a": "two"}) == {"ok": True}

    request, timeout = opener.requests[0]
    assert request.full_url == "http://127.0.0.1:8402/v1/commands"
    assert request.method == "POST"
    assert request.data == b'{"a":"two","z":1}'
    assert request.get_header("Authorization") == "Bearer cli-token"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("X-trace-id") == "corr_0123456789abcdef0123456789abcdef"
    assert timeout == 10.0


def test_client_default_response_bound_is_two_mibibytes() -> None:
    body = b'{"value":"' + b"x" * (256 * 1024) + b'"}'
    client = BoundedJsonHttpClient(
        "http://127.0.0.1:8400",
        opener=_Opener(_Response(body)),
    )

    assert len(client.get("/v1/system/status")["value"]) == 256 * 1024


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (_Response(b"{}", content_type="text/html"), "INVALID_CONTENT_TYPE"),
        (_Response(b"{}", url="http://127.0.0.1:9999/elsewhere"), "REDIRECT_REFUSED"),
        (_Response(b"x" * 17), "RESPONSE_TOO_LARGE"),
        (_Response(b"[]"), "INVALID_RESPONSE"),
        (_Response(b"{"), "INVALID_RESPONSE"),
        (socket.timeout(), "TIMEOUT"),
    ],
)
def test_client_fails_closed_for_untrusted_responses(
    response: object, code: str
) -> None:
    """Break caught: malformed or redirected upstream data is treated as trusted JSON."""
    client = BoundedJsonHttpClient(
        "http://127.0.0.1:8400",
        max_response_bytes=16,
        opener=_Opener(response),
    )
    with pytest.raises(HttpClientError) as raised:
        client.get("/v1/system/status")
    assert raised.value.code == code


def test_client_preserves_sanitized_upstream_error_code() -> None:
    """Break caught: operators lose the typed API reason on a non-2xx response."""
    body = json.dumps(
        {
            "schema_version": "1.0.0",
            "trace_id": "trace_test",
            "generated_at": "2026-09-01T00:00:00Z",
            "error": {
                "code": "EXPECTED_STATE_CONFLICT",
                "message": "state changed",
                "details": {},
            },
        }
    ).encode()
    headers = Message()
    headers["Content-Type"] = "application/json"
    error = HTTPError(
        "http://127.0.0.1:8402/v1/commands",
        409,
        "Conflict",
        headers,
        io.BytesIO(body),
    )

    client = BoundedJsonHttpClient("http://127.0.0.1:8402", opener=_Opener(error))
    with pytest.raises(HttpClientError) as raised:
        client.post("/v1/commands", {})
    assert (raised.value.status, raised.value.code) == (409, "EXPECTED_STATE_CONFLICT")


def test_client_rejects_absolute_or_non_v1_request_paths() -> None:
    """Break caught: a caller can escape its configured API origin or call an unversioned route."""
    client = BoundedJsonHttpClient(
        "http://127.0.0.1:8400", opener=_Opener(_Response(b"{}"))
    )
    for path in (
        "http://127.0.0.1:8401/v1/jobs",
        "//127.0.0.1:8401/v1/jobs",
        "/health/live",
        "/v1/../health/live",
        "/v1/%2e%2e/health/live",
    ):
        with pytest.raises(ValueError, match="path"):
            client.get(path)

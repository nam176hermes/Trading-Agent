from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.engine_contracts import EngineSessionIdentityV1


def _identity() -> EngineSessionIdentityV1:
    return EngineSessionIdentityV1(
        runtime_family="cython-v1",
        engine_version="1.231.0",
        engine_upstream_commit="2" * 40,
        closure_digest="3" * 64,
        request_protocol="engine-request-v1",
        event_schema="engine-event-v1",
        paper_schema="paper-session-v1",
    )


def test_engine_session_identity_is_strict_immutable_and_round_trips() -> None:
    identity = _identity()

    assert identity.schema_version == "engine-session-identity-v1"
    assert EngineSessionIdentityV1.model_validate_json(identity.model_dump_json()) == identity
    with pytest.raises(ValidationError, match="frozen"):
        identity.engine_version = "1.232.0"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EngineSessionIdentityV1.model_validate({**identity.model_dump(), "live": True})


@pytest.mark.parametrize(
    ("field", "value"),
    (("engine_upstream_commit", "a" * 39), ("closure_digest", "A" * 64)),
)
def test_engine_session_identity_rejects_unbound_source_identity(
    field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        EngineSessionIdentityV1.model_validate({**_identity().model_dump(), field: value})

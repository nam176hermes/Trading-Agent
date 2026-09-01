from __future__ import annotations

from uuid import UUID

from packages.engine_contracts import EngineSessionIdentityV1
from services.paper_runtime.nautilus_child import (
    EngineSessionPort,
    is_issued_engine_session_port,
    issue_engine_session_port,
)


def test_engine_session_port_is_issuer_bound_and_carries_stable_identity() -> None:
    identity = EngineSessionIdentityV1(
        runtime_family="cython-v1",
        engine_version="1.231.0",
        engine_upstream_commit="2" * 40,
        closure_digest="3" * 64,
        request_protocol="engine-request-v1",
        event_schema="engine-event-v1",
        paper_schema="paper-session-v1",
    )
    port = issue_engine_session_port(
        identity=identity,
        capability_sha256="4" * 64,
        custodian_authority_sha256="5" * 64,
        process_authority_sha256="6" * 64,
        paper_source_sha256="7" * 64,
        session_id=UUID("10000000-0000-4000-8000-000000000001"),
        owner_id=UUID("20000000-0000-4000-8000-000000000001"),
        exchange=lambda raw: raw,
        close_input=lambda: 0,
        abort=lambda: None,
        is_running=lambda: True,
    )

    assert type(port) is EngineSessionPort
    assert port.identity is identity
    assert port.closure_digest == identity.closure_digest
    assert is_issued_engine_session_port(port) is True
    assert len(port.authority_sha256) == 64


def test_freely_constructed_engine_session_port_is_impossible() -> None:
    try:
        EngineSessionPort()  # type: ignore[call-arg]
    except TypeError:
        pass
    else:
        raise AssertionError("engine session ports must be issuer-owned")


def test_paper_runtime_exports_only_the_stable_session_port_name() -> None:
    from services import paper_runtime

    assert paper_runtime.EngineSessionPort is EngineSessionPort
    assert paper_runtime.issue_engine_session_port is not None
    assert not hasattr(paper_runtime, "NautilusPaperChild")

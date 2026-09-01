"""Custody-bound handle for one issued P1 Nautilus paper child."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import re
import weakref
from uuid import UUID

from packages.engine_contracts import EngineSessionIdentityV1, canonical_json_bytes


_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


@dataclass(
    frozen=True,
    slots=True,
    init=False,
    eq=False,
    repr=False,
    weakref_slot=True,
)
class EngineSessionPort:
    identity: EngineSessionIdentityV1
    authority_sha256: str
    capability_sha256: str
    custodian_authority_sha256: str
    process_authority_sha256: str
    paper_source_sha256: str
    session_id: UUID
    owner_id: UUID
    exchange: Callable[[bytes], bytes]
    close_input: Callable[[], int]
    abort: Callable[[], None]
    is_running: Callable[[], bool]

    def __new__(cls) -> "EngineSessionPort":
        raise TypeError("engine session ports are issuer-owned")

    @property
    def closure_digest(self) -> str:
        return self.identity.closure_digest

    @property
    def runtime_family(self) -> str:
        return self.identity.runtime_family

    @property
    def engine_version(self) -> str:
        return self.identity.engine_version

    @property
    def engine_upstream_commit(self) -> str:
        return self.identity.engine_upstream_commit


_ISSUED: weakref.WeakSet[EngineSessionPort] = weakref.WeakSet()


def _authority_sha256(child: EngineSessionPort) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "capability_sha256": child.capability_sha256,
                "custodian_authority_sha256": child.custodian_authority_sha256,
                "identity": child.identity,
                "process_authority_sha256": child.process_authority_sha256,
                "paper_source_sha256": child.paper_source_sha256,
                "owner_id": str(child.owner_id),
                "session_id": str(child.session_id),
            }
        )
    ).hexdigest()


def issue_engine_session_port(
    *,
    identity: EngineSessionIdentityV1,
    capability_sha256: str,
    custodian_authority_sha256: str,
    process_authority_sha256: str,
    paper_source_sha256: str,
    session_id: UUID,
    owner_id: UUID,
    exchange: Callable[[bytes], bytes],
    close_input: Callable[[], int],
    abort: Callable[[], None],
    is_running: Callable[[], bool],
) -> EngineSessionPort:
    digests = (
        capability_sha256,
        custodian_authority_sha256,
        process_authority_sha256,
        paper_source_sha256,
    )
    if (
        type(identity) is not EngineSessionIdentityV1
        or any(
            type(value) is not str or _SHA256.fullmatch(value) is None
            for value in digests
        )
        or not all(callable(value) for value in (exchange, close_input, abort, is_running))
        or type(session_id) is not UUID
        or type(owner_id) is not UUID
    ):
        raise ValueError("Nautilus paper child authority is invalid")
    child = object.__new__(EngineSessionPort)
    values: dict[str, object] = {
        "identity": identity,
        "authority_sha256": "0" * 64,
        "capability_sha256": capability_sha256,
        "custodian_authority_sha256": custodian_authority_sha256,
        "process_authority_sha256": process_authority_sha256,
        "paper_source_sha256": paper_source_sha256,
        "session_id": session_id,
        "owner_id": owner_id,
        "exchange": exchange,
        "close_input": close_input,
        "abort": abort,
        "is_running": is_running,
    }
    for name, value in values.items():
        object.__setattr__(child, name, value)
    object.__setattr__(child, "authority_sha256", _authority_sha256(child))
    _ISSUED.add(child)
    return child


def is_issued_engine_session_port(child: object) -> bool:
    return (
        type(child) is EngineSessionPort
        and child in _ISSUED
        and child.authority_sha256 == _authority_sha256(child)
    )


__all__ = [
    "EngineSessionPort",
    "is_issued_engine_session_port",
    "issue_engine_session_port",
]

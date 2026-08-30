"""Custody-bound handle for one issued P1 Nautilus paper child."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import re
import weakref
from uuid import UUID

from packages.engine_contracts import canonical_json_bytes


_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


@dataclass(
    frozen=True,
    slots=True,
    init=False,
    eq=False,
    repr=False,
    weakref_slot=True,
)
class NautilusPaperChild:
    closure_digest: str
    authority_sha256: str
    capability_sha256: str
    custodian_authority_sha256: str
    process_authority_sha256: str
    paper_source_sha256: str
    session_id: UUID
    owner_id: UUID
    runtime_family: str
    engine_version: str
    engine_upstream_commit: str
    exchange: Callable[[bytes], bytes]
    close_input: Callable[[], int]
    abort: Callable[[], None]
    is_running: Callable[[], bool]


_ISSUED: weakref.WeakSet[NautilusPaperChild] = weakref.WeakSet()


def _authority_sha256(child: NautilusPaperChild) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "capability_sha256": child.capability_sha256,
                "closure_digest": child.closure_digest,
                "custodian_authority_sha256": child.custodian_authority_sha256,
                "engine_upstream_commit": child.engine_upstream_commit,
                "engine_version": child.engine_version,
                "process_authority_sha256": child.process_authority_sha256,
                "paper_source_sha256": child.paper_source_sha256,
                "runtime_family": child.runtime_family,
                "owner_id": str(child.owner_id),
                "session_id": str(child.session_id),
            }
        )
    ).hexdigest()


def issue_nautilus_paper_child(
    *,
    closure_digest: str,
    capability_sha256: str,
    custodian_authority_sha256: str,
    process_authority_sha256: str,
    paper_source_sha256: str,
    session_id: UUID,
    owner_id: UUID,
    runtime_family: str,
    engine_version: str,
    engine_upstream_commit: str,
    exchange: Callable[[bytes], bytes],
    close_input: Callable[[], int],
    abort: Callable[[], None],
    is_running: Callable[[], bool],
) -> NautilusPaperChild:
    digests = (
        closure_digest,
        capability_sha256,
        custodian_authority_sha256,
        process_authority_sha256,
        paper_source_sha256,
    )
    if (
        any(type(value) is not str or _SHA256.fullmatch(value) is None for value in digests)
        or runtime_family != "cython-v1"
        or engine_version != "1.231.0"
        or type(engine_upstream_commit) is not str
        or re.fullmatch(r"[0-9a-f]{40}", engine_upstream_commit) is None
        or not all(callable(value) for value in (exchange, close_input, abort, is_running))
        or type(session_id) is not UUID
        or type(owner_id) is not UUID
    ):
        raise ValueError("Nautilus paper child authority is invalid")
    child = object.__new__(NautilusPaperChild)
    values: dict[str, object] = {
        "closure_digest": closure_digest,
        "authority_sha256": "0" * 64,
        "capability_sha256": capability_sha256,
        "custodian_authority_sha256": custodian_authority_sha256,
        "process_authority_sha256": process_authority_sha256,
        "paper_source_sha256": paper_source_sha256,
        "session_id": session_id,
        "owner_id": owner_id,
        "runtime_family": runtime_family,
        "engine_version": engine_version,
        "engine_upstream_commit": engine_upstream_commit,
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


def is_issued_nautilus_paper_child(child: object) -> bool:
    return (
        type(child) is NautilusPaperChild
        and child in _ISSUED
        and child.authority_sha256 == _authority_sha256(child)
    )

"""V2-only fail-closed authority pin for Job API mutations."""

from __future__ import annotations

from dataclasses import dataclass, field
import hmac
from typing import Callable
from weakref import WeakSet

from .config import ProtectedAuthorityError, RuntimeAuthorityV2, attest_application_release_v2, load_runtime_authority_v2

AuthorityLoader = Callable[[], RuntimeAuthorityV2]
ApplicationAttestor = Callable[[RuntimeAuthorityV2], bool]

def _load(loader: AuthorityLoader) -> RuntimeAuthorityV2:
    try:
        authority = loader()
    except Exception:
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID") from None
    if type(authority) is not RuntimeAuthorityV2:
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID")
    return authority

def _attest(attestor: ApplicationAttestor, authority: RuntimeAuthorityV2) -> None:
    try:
        if attestor(authority) is not True:
            raise ValueError("application release is not attested")
    except Exception:
        raise ProtectedAuthorityError("APPLICATION_RELEASE_INVALID") from None

def _same(left: tuple[object, ...], right: tuple[object, ...]) -> bool:
    if len(left) != len(right):
        return False
    return all(hmac.compare_digest(a, b) if isinstance(a, str) and isinstance(b, str) else a == b for a, b in zip(left, right, strict=True))

@dataclass(frozen=True, slots=True, repr=False, init=False, eq=False, weakref_slot=True)
class ValidatedJobPlaneAuthority:
    _authority_pin: tuple[object, ...]
    _dynamic_evidence_pin: tuple[object, ...]
    _authority_loader: AuthorityLoader = field(repr=False, compare=False)
    _application_attestor: ApplicationAttestor = field(repr=False, compare=False)

    def __new__(cls) -> "ValidatedJobPlaneAuthority":
        raise TypeError("job-plane authority is factory-issued")

    def recheck_mutation(self) -> "ValidatedJobPlaneAuthority":
        if self not in _ISSUED:
            raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID")
        before = _load(self._authority_loader)
        if not _same(before._authority_pin, self._authority_pin):
            raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_CHANGED")
        _attest(self._application_attestor, before)
        after = _load(self._authority_loader)
        if not _same(after._authority_pin, self._authority_pin) or not _same(after._dynamic_evidence_pin, self._dynamic_evidence_pin):
            raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_CHANGED")
        return self

    def __repr__(self) -> str:
        return "ValidatedJobPlaneAuthority(validated=True)"

_ISSUED: WeakSet[ValidatedJobPlaneAuthority] = WeakSet()

def validate_job_plane_authority() -> ValidatedJobPlaneAuthority:
    before = _load(load_runtime_authority_v2)
    _attest(attest_application_release_v2, before)
    after = _load(load_runtime_authority_v2)
    if not _same(after._authority_pin, before._authority_pin) or not _same(after._dynamic_evidence_pin, before._dynamic_evidence_pin):
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_CHANGED")
    capability = object.__new__(ValidatedJobPlaneAuthority)
    object.__setattr__(capability, "_authority_pin", before._authority_pin)
    object.__setattr__(capability, "_dynamic_evidence_pin", before._dynamic_evidence_pin)
    object.__setattr__(capability, "_authority_loader", load_runtime_authority_v2)
    object.__setattr__(capability, "_application_attestor", attest_application_release_v2)
    _ISSUED.add(capability)
    return capability

__all__ = ["ValidatedJobPlaneAuthority", "validate_job_plane_authority"]

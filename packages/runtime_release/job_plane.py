"""Opaque, fail-closed authority pin for Job API mutations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import hmac
from weakref import WeakSet

from .config import (
    ProtectedAuthorityError,
    RuntimeAuthority,
    RuntimeAuthorityV2,
    attest_application_release,
    attest_application_release_v2,
    load_runtime_authority,
    load_runtime_authority_v2,
)


Authority = RuntimeAuthority | RuntimeAuthorityV2
AuthorityLoader = Callable[[], Authority]
ApplicationAttestor = Callable[[Authority], bool]


def _load_validated(
    loader: AuthorityLoader, *, expected_type: type[Authority]
) -> Authority:
    try:
        authority = loader()
    except Exception:
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID") from None
    if type(authority) is not expected_type:
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID") from None
    return authority


def _load_supported(loader: AuthorityLoader) -> Authority:
    try:
        authority = loader()
    except Exception:
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID") from None
    if type(authority) not in {RuntimeAuthority, RuntimeAuthorityV2}:
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID") from None
    return authority


def _attest_application(
    attestor: ApplicationAttestor, authority: Authority
) -> None:
    try:
        if attestor(authority) is not True:
            raise ValueError("application release is not attested")
    except Exception:
        raise ProtectedAuthorityError("APPLICATION_RELEASE_INVALID") from None


def _pin(authority: Authority) -> tuple[object, ...]:
    if isinstance(authority, RuntimeAuthorityV2):
        pin = getattr(authority, "_authority_pin", None)
        if not isinstance(pin, tuple) or not pin:
            raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID")
        return pin
    return (authority._identity, authority._document_sha256)


def _same_document(current: Authority, pin: tuple[object, ...]) -> bool:
    observed = _pin(current)
    if len(observed) != len(pin):
        return False
    return all(
        hmac.compare_digest(left, right)
        if isinstance(left, str) and isinstance(right, str)
        else left == right
        for left, right in zip(observed, pin, strict=True)
    )


def _dynamic_pin(authority: Authority) -> tuple[object, ...]:
    if not isinstance(authority, RuntimeAuthorityV2):
        return ()
    pin = getattr(authority, "_dynamic_evidence_pin", None)
    if not isinstance(pin, tuple) or not pin:
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID")
    return pin


def _same_pin(left: tuple[object, ...], right: tuple[object, ...]) -> bool:
    if len(left) != len(right):
        return False
    return all(
        hmac.compare_digest(first, second)
        if isinstance(first, str) and isinstance(second, str)
        else first == second
        for first, second in zip(left, right, strict=True)
    )


@dataclass(
    frozen=True,
    slots=True,
    repr=False,
    init=False,
    eq=False,
    weakref_slot=True,
)
class ValidatedJobPlaneAuthority:
    """Pinned authority identity that can only be consumed through recheck."""

    _document_identity: tuple[int, int]
    _document_sha256: str
    _authority_pin: tuple[object, ...]
    _authority_type: type[Authority]
    _authority_loader: AuthorityLoader = field(repr=False, compare=False)
    _application_attestor: ApplicationAttestor = field(repr=False, compare=False)

    def __new__(cls) -> "ValidatedJobPlaneAuthority":
        raise TypeError("job-plane authority is factory-issued")

    def recheck_mutation(self) -> "ValidatedJobPlaneAuthority":
        if self not in _ISSUED_AUTHORITIES:
            raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID")
        before = _load_validated(
            self._authority_loader, expected_type=self._authority_type
        )
        if not _same_document(before, self._authority_pin):
            raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_CHANGED")
        _attest_application(self._application_attestor, before)
        after = _load_validated(
            self._authority_loader, expected_type=self._authority_type
        )
        if not _same_document(after, self._authority_pin):
            raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_CHANGED")
        if not _same_pin(_dynamic_pin(before), _dynamic_pin(after)):
            raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_CHANGED")
        return self

    def __repr__(self) -> str:
        return "ValidatedJobPlaneAuthority(validated=True)"


_ISSUED_AUTHORITIES: WeakSet[ValidatedJobPlaneAuthority] = WeakSet()


def validate_job_plane_authority(
    *,
    authority_loader: AuthorityLoader | None = None,
    application_attestor: ApplicationAttestor | None = None,
) -> ValidatedJobPlaneAuthority:
    """Load protected authority once and return its opaque pinned capability."""

    if authority_loader is None and application_attestor is None:
        authority_loader = load_runtime_authority_v2
        application_attestor = attest_application_release_v2
        expected_type: type[Authority] = RuntimeAuthorityV2
        before = _load_validated(
            authority_loader, expected_type=expected_type
        )
    elif authority_loader is None or application_attestor is None:
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_INVALID")
    else:
        # Explicit injection is retained only for isolated legacy fixtures;
        # production composition supplies neither argument and is v2-only.
        before = _load_supported(authority_loader)
        expected_type = type(before)
    _attest_application(application_attestor, before)
    after = _load_validated(authority_loader, expected_type=expected_type)
    authority_pin = _pin(before)
    if not _same_document(after, authority_pin):
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_CHANGED")
    if not _same_pin(_dynamic_pin(before), _dynamic_pin(after)):
        raise ProtectedAuthorityError("JOB_PLANE_AUTHORITY_CHANGED")
    capability = object.__new__(ValidatedJobPlaneAuthority)
    if isinstance(after, RuntimeAuthority):
        identity = after._identity
        digest = after._document_sha256
    else:
        identity = (0, 0)
        digest = next(
            (item for item in reversed(authority_pin) if isinstance(item, str)),
            "0" * 64,
        )
    object.__setattr__(capability, "_document_identity", identity)
    object.__setattr__(capability, "_document_sha256", digest)
    object.__setattr__(capability, "_authority_pin", authority_pin)
    object.__setattr__(capability, "_authority_type", expected_type)
    object.__setattr__(capability, "_authority_loader", authority_loader)
    object.__setattr__(capability, "_application_attestor", application_attestor)
    _ISSUED_AUTHORITIES.add(capability)
    return capability


__all__ = ["ValidatedJobPlaneAuthority", "validate_job_plane_authority"]

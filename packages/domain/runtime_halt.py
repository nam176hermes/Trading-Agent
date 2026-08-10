"""Strict immutable contracts for global halt and submit authority."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, StrictInt, ValidationError

from packages.safety_evidence import CanonicalKillSwitchState

from .clock import require_utc
from .runtime_risk import RuntimeRiskModel, Sha256, _is_complete


class GlobalHaltStatus(str, Enum):
    ACTIVE = "ACTIVE"
    HALTED = "HALTED"


class GlobalHaltReasonCode(str, Enum):
    SAFETY_AUTHORITY_UNKNOWN = "SAFETY_AUTHORITY_UNKNOWN"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"
    RECOVERY_AUTHORIZED = "RECOVERY_AUTHORIZED"
    INITIALIZED_SAFE = "INITIALIZED_SAFE"


_HALT_REASON_ORDER = {
    reason: index for index, reason in enumerate(GlobalHaltReasonCode)
}
_BREAKER_REASONS = frozenset(
    {
        GlobalHaltReasonCode.SAFETY_AUTHORITY_UNKNOWN,
        GlobalHaltReasonCode.KILL_SWITCH_ACTIVE,
        GlobalHaltReasonCode.DAILY_LOSS_LIMIT,
        GlobalHaltReasonCode.DRAWDOWN_LIMIT,
    }
)
_PositiveGeneration = Annotated[StrictInt, Field(gt=0)]
_NonNegativeGeneration = Annotated[StrictInt, Field(ge=0)]


class _RuntimeHaltModel(RuntimeRiskModel):
    """Revalidate constructed instances before any public boundary operation."""

    @classmethod
    def model_validate(
        cls,
        obj: object,
        *,
        strict: bool | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if isinstance(obj, cls):
            try:
                obj = {name: getattr(obj, name) for name in cls.model_fields}
            except AttributeError as exc:
                raise ValidationError.from_exception_data(
                    cls.__name__,
                    [{"type": "missing", "loc": ("runtime_halt",), "input": obj}],
                ) from exc
        return super().model_validate(
            obj,
            strict=strict,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        canonical = type(self).model_validate(self)
        return BaseModel.model_dump(canonical, *args, **kwargs)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        canonical = type(self).model_validate(self)
        return BaseModel.model_dump_json(canonical, *args, **kwargs)


def _require_canonical_reasons(reason_codes: tuple[GlobalHaltReasonCode, ...]) -> None:
    if not reason_codes:
        raise ValueError("reason_codes must not be empty")
    if len(reason_codes) != len(set(reason_codes)):
        raise ValueError("reason_codes contain duplicates")
    if tuple(sorted(reason_codes, key=_HALT_REASON_ORDER.__getitem__)) != reason_codes:
        raise ValueError("reason_codes must follow canonical halt order")


def _require_halt_status_reasons(
    status: GlobalHaltStatus,
    reason_codes: tuple[GlobalHaltReasonCode, ...],
    *,
    initialization: bool,
    recovery_authorization_digest: str | None = None,
) -> None:
    _require_canonical_reasons(reason_codes)
    if status is GlobalHaltStatus.HALTED:
        if not set(reason_codes) <= _BREAKER_REASONS:
            raise ValueError("halted transitions require only breaker reasons")
        if recovery_authorization_digest is not None:
            raise ValueError("halted transitions cannot carry recovery authorization")
        return
    if initialization:
        if reason_codes != (GlobalHaltReasonCode.INITIALIZED_SAFE,):
            raise ValueError("active initialization requires only INITIALIZED_SAFE")
        if recovery_authorization_digest is not None:
            raise ValueError("active initialization cannot carry recovery authorization")
        return
    if reason_codes != (GlobalHaltReasonCode.RECOVERY_AUTHORIZED,):
        raise ValueError("active recovery requires only RECOVERY_AUTHORIZED")
    if recovery_authorization_digest is None:
        raise ValueError("active recovery requires recovery authorization")


class GlobalSafetyObservation(_RuntimeHaltModel):
    source_fingerprint: Sha256
    kill_switch_state: CanonicalKillSwitchState
    observed_at: datetime
    schema_version: Literal["global-safety-observation-v1"]

    def model_post_init(self, __context: Any) -> None:
        if _is_complete(self):
            require_utc(self.observed_at)


class GlobalHaltState(_RuntimeHaltModel):
    stream_id: UUID
    generation: _PositiveGeneration
    status: GlobalHaltStatus
    transition_event_id: UUID
    transition_digest: Sha256
    prior_transition_event_id: UUID | None
    prior_transition_digest: Sha256 | None
    runtime_policy_digest: Sha256
    runtime_observation_digest: Sha256
    portfolio_digest: Sha256
    safety_observation_digest: Sha256
    reason_codes: tuple[GlobalHaltReasonCode, ...]
    transitioned_at: datetime
    schema_version: Literal["global-halt-state-v1"]

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        require_utc(self.transitioned_at)
        has_prior_id = self.prior_transition_event_id is not None
        has_prior_digest = self.prior_transition_digest is not None
        if self.generation == 1:
            if has_prior_id or has_prior_digest:
                raise ValueError("generation one has no prior transition")
        elif not (has_prior_id and has_prior_digest):
            raise ValueError("later generations require prior transition identity and digest")
        _require_halt_status_reasons(
            self.status,
            self.reason_codes,
            initialization=self.generation == 1,
        )


class GlobalHaltRecoveryAuthorization(_RuntimeHaltModel):
    authorization_id: UUID
    authorization_digest: Sha256
    halted_generation: _PositiveGeneration
    halted_transition_digest: Sha256
    runtime_policy_digest: Sha256
    runtime_observation_digest: Sha256
    portfolio_digest: Sha256
    safety_binding_digest: Sha256
    issued_at: datetime
    expires_at: datetime
    operator_authority_digest: Sha256
    schema_version: Literal["global-halt-recovery-authorization-v1"]

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        require_utc(self.issued_at)
        require_utc(self.expires_at)
        if self.expires_at <= self.issued_at:
            raise ValueError("recovery authorization must expire after issuance")


class GlobalHaltTransition(_RuntimeHaltModel):
    transition_id: UUID
    prior_generation: _NonNegativeGeneration
    prior_transition_digest: Sha256 | None
    next_generation: _PositiveGeneration
    next_status: GlobalHaltStatus
    reason_codes: tuple[GlobalHaltReasonCode, ...]
    runtime_policy_digest: Sha256
    runtime_observation_digest: Sha256
    portfolio_digest: Sha256
    safety_observation_digest: Sha256
    recovery_authorization_digest: Sha256 | None
    decided_at: datetime
    schema_version: Literal["global-halt-transition-v1"]

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        require_utc(self.decided_at)
        if self.next_generation == 1:
            if self.prior_generation != 0 or self.prior_transition_digest is not None:
                raise ValueError("generation one has no prior transition")
        else:
            if self.prior_generation <= 0 or self.prior_transition_digest is None:
                raise ValueError("later generations require prior generation and digest")
            if self.next_generation != self.prior_generation + 1:
                raise ValueError("halt generations must increase by exactly one")
        _require_halt_status_reasons(
            self.next_status,
            self.reason_codes,
            initialization=self.next_generation == 1,
            recovery_authorization_digest=self.recovery_authorization_digest,
        )


class SubmitPermitPrepared(_RuntimeHaltModel):
    permit_id: UUID
    approval_event_id: UUID
    approval_reference_digest: Sha256
    intent_digest: Sha256
    policy_risk_decision_digest: Sha256
    runtime_risk_decision_digest: Sha256
    runtime_policy_digest: Sha256
    runtime_observation_digest: Sha256
    portfolio_digest: Sha256
    safety_binding_digest: Sha256
    halt_stream_id: UUID
    halt_generation: _PositiveGeneration
    halt_transition_event_id: UUID
    halt_transition_digest: Sha256
    prepared_at: datetime
    expires_at: datetime
    schema_version: Literal["submit-permit-prepared-v1"]

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        require_utc(self.prepared_at)
        require_utc(self.expires_at)
        if self.expires_at - self.prepared_at != timedelta(seconds=5):
            raise ValueError("submit permit lifetime must be exactly five seconds")


class PreparedSubmitPermit(SubmitPermitPrepared):
    prepared_event_id: UUID
    prepared_event_digest: Sha256
    schema_version: Literal["prepared-submit-permit-v1"]


class SubmitPermitConsumed(_RuntimeHaltModel):
    permit_id: UUID
    prepared_event_digest: Sha256
    halt_stream_id: UUID
    halt_generation: _PositiveGeneration
    halt_transition_digest: Sha256
    consumed_at: datetime
    schema_version: Literal["submit-permit-consumed-v1"]

    def model_post_init(self, __context: Any) -> None:
        if _is_complete(self):
            require_utc(self.consumed_at)


class ConsumedSubmitAuthority(SubmitPermitConsumed):
    consumed_event_id: UUID
    consumed_event_digest: Sha256
    schema_version: Literal["consumed-submit-authority-v1"]


__all__ = [
    "ConsumedSubmitAuthority",
    "GlobalHaltReasonCode",
    "GlobalHaltRecoveryAuthorization",
    "GlobalHaltState",
    "GlobalHaltStatus",
    "GlobalHaltTransition",
    "GlobalSafetyObservation",
    "PreparedSubmitPermit",
    "SubmitPermitConsumed",
    "SubmitPermitPrepared",
]

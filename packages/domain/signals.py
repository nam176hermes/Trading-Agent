"""Strict, immutable research and signal payload contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
import re
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from .clock import require_utc
from .instruments import InstrumentId
from .primitives import CANONICAL_DECIMAL_POLICY_VERSION, FiniteDecimal
_SIGNAL_SCORE_PATTERN = r"^(?:-1|-0\.\d*[1-9]|0|0\.\d*[1-9]|1)$"
_SIGNAL_CONFIDENCE_PATTERN = r"^(?:0|0\.\d*[1-9]|1)$"


SignalScore = Annotated[
    FiniteDecimal,
    Field(ge=Decimal("-1"), le=Decimal("1")),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": _SIGNAL_SCORE_PATTERN,
            "x-canonical-decimal-policy": CANONICAL_DECIMAL_POLICY_VERSION,
        },
        mode="validation",
    ),
]
SignalConfidence = Annotated[
    FiniteDecimal,
    Field(ge=Decimal("0"), le=Decimal("1")),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": _SIGNAL_CONFIDENCE_PATTERN,
            "x-canonical-decimal-policy": CANONICAL_DECIMAL_POLICY_VERSION,
        },
        mode="validation",
    ),
]
NonEmptyText = Annotated[str, Field(min_length=1)]
EvidenceAuthority = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9](?:[a-z0-9.-]{0,126}[a-z0-9])?$",
    ),
]
EvidencePathSegment = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._~-]{0,63})?$",
    ),
]
EvidencePath = Annotated[
    tuple[EvidencePathSegment, ...], Field(min_length=1, max_length=16)
]
_SENSITIVE_EVIDENCE_WORDS = frozenset(
    {
        "credential",
        "credentials",
        "secret",
        "token",
        "password",
        "account",
        "routing",
        "execution",
        "execute",
    }
)
_SENSITIVE_EVIDENCE_COMPOUNDS = frozenset(
    {
        "credential",
        "apikey",
        "password",
        "accountid",
        "accountnumber",
        "accountrouting",
        "routingnumber",
        "ordertype",
        "executioninstruction",
        "executiontext",
        "brokeraccount",
        "apisecret",
        "clientsecret",
        "apitoken",
        "accesstoken",
        "authtoken",
        "bearertoken",
        "sessiontoken",
    }
)
_SENSITIVE_EVIDENCE_EMBEDDED_PATTERNS = (
    r"token(?!iz)",
    r"secret(?!ar)",
    r"account(?!ing)",
    r"routing",
    r"execution",
    r"execute",
)


class DomainModel(BaseModel):
    """Common Pydantic v2 policy for D0.2 payloads."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class EvidenceSource(str, Enum):
    """Closed research-only source classes with no execution authority."""

    MARKET_DATA = "market_data"
    NEWS = "news"
    FILING = "filing"
    ON_CHAIN = "on_chain"
    RESEARCH = "research"


class EvidenceLocatorKind(str, Enum):
    """Supported locator namespaces; credentials and routing are not locators."""

    HTTPS = "https"
    DATASET = "dataset"
    DOCUMENT = "document"
    BLOCK = "block"


class EvidenceLocator(DomainModel):
    """Bounded structural reference without query, userinfo, or free-form text."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-prohibited-content": [
                "credentials",
                "account-routing",
                "order-type",
                "execution-text",
            ]
        }
    )

    kind: EvidenceLocatorKind
    authority: EvidenceAuthority
    path: EvidencePath

    @model_validator(mode="after")
    def _reject_sensitive_terms(self) -> "EvidenceLocator":
        components = (self.authority, *self.path)
        words = {
            word
            for component in components
            for word in re.split(r"[^a-z0-9]+", component.lower())
            if word
        }
        normalized = tuple(
            re.sub(r"[^a-z0-9]", "", component.lower())
            for component in components
        )
        candidates = (*normalized, "".join(normalized), "".join(normalized[1:]))
        if words & _SENSITIVE_EVIDENCE_WORDS:
            raise ValueError("evidence locator contains sensitive execution text")
        for candidate in candidates:
            if (
                candidate in _SENSITIVE_EVIDENCE_WORDS
                or any(term in candidate for term in _SENSITIVE_EVIDENCE_COMPOUNDS)
                or any(
                    re.search(pattern, candidate)
                    for pattern in _SENSITIVE_EVIDENCE_EMBEDDED_PATTERNS
                )
            ):
                raise ValueError("evidence locator contains sensitive execution text")
        return self


class EvidenceReference(DomainModel):
    evidence_id: UUID
    source: EvidenceSource
    locator: EvidenceLocator
    observed_at: datetime
    schema_version: NonEmptyText

    @field_validator("observed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class ResearchPacket(DomainModel):
    model_config = ConfigDict(
        json_schema_extra={
            "x-temporal-invariants": ["evidence.observed_at <= cutoff_at"]
        }
    )

    packet_id: UUID
    cutoff_at: datetime
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1)
    model_version: NonEmptyText
    schema_version: NonEmptyText

    @field_validator("cutoff_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _valid_point_in_time_boundary(self) -> "ResearchPacket":
        if any(item.observed_at > self.cutoff_at for item in self.evidence):
            raise ValueError("evidence observed_at must not be after cutoff_at")
        return self


class SignalProposal(DomainModel):
    """Research-only proposal; it intentionally contains no execution authority."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-temporal-invariants": [
                "research_packet_cutoff_at == cutoff_at",
                "evidence.observed_at <= cutoff_at",
                "cutoff_at < expires_at",
            ]
        }
    )

    signal_id: UUID
    research_packet_id: UUID
    instrument: InstrumentId
    direction: SignalDirection
    score: SignalScore
    confidence: SignalConfidence
    research_packet_cutoff_at: datetime
    cutoff_at: datetime
    expires_at: datetime
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1)
    model_version: NonEmptyText
    strategy_version: NonEmptyText
    schema_version: NonEmptyText

    @field_validator("research_packet_cutoff_at", "cutoff_at", "expires_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _valid_lifetime(self) -> "SignalProposal":
        if self.research_packet_cutoff_at != self.cutoff_at:
            raise ValueError("research packet cutoff must equal signal cutoff")
        if any(item.observed_at > self.cutoff_at for item in self.evidence):
            raise ValueError("evidence observed_at must not be after cutoff_at")
        if self.expires_at <= self.cutoff_at:
            raise ValueError("expires_at must be after cutoff_at")
        return self

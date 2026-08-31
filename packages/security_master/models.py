"""Canonical point-in-time security-master revision models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
import hashlib
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.domain import EvidenceReference, ProductType
from packages.engine_contracts.serialization import CanonicalUtcDateTime, Sha256Hex, canonical_json_bytes


_BoundedText = Annotated[str, Field(min_length=1, max_length=256)]
_Token = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9._-]*$")]
_Version = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
_PositiveDecimalText = Annotated[str, Field(min_length=1, max_length=128)]
_CANONICAL_POSITIVE_DECIMAL = re.compile(
    r"^(?:[1-9]\d*|(?:0|[1-9]\d*)\.\d*[1-9])$",
    re.ASCII,
)
_CANONICAL_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", re.ASCII)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class SecurityMasterIdentityKind(str, Enum):
    ISSUER = "ISSUER"
    ASSET = "ASSET"
    SECURITY = "SECURITY"
    VENUE = "VENUE"
    LISTING = "LISTING"
    SYMBOL_MAPPING = "SYMBOL_MAPPING"
    CORPORATE_ACTION = "CORPORATE_ACTION"


class SecurityMasterOperation(str, Enum):
    ASSERT = "ASSERT"
    RETRACT = "RETRACT"


class CorporateActionType(str, Enum):
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    SPLIT = "SPLIT"
    CASH_DIVIDEND = "CASH_DIVIDEND"
    DELISTING = "DELISTING"


class AssetKind(str, Enum):
    FIAT = "FIAT"
    CRYPTO = "CRYPTO"
    EQUITY = "EQUITY"


class SecurityMasterEvidenceV1(_FrozenModel):
    schema_version: Literal["security-master-evidence-v1"]
    reference: EvidenceReference
    fetched_at: CanonicalUtcDateTime
    known_at: CanonicalUtcDateTime
    content_sha256: Sha256Hex
    media_type: Literal[
        "application/json",
        "application/pdf",
        "text/csv",
        "text/html",
        "text/plain",
    ]
    source_revision: _Version
    normalization_version: _Version

    @model_validator(mode="after")
    def _valid_temporal_chain(self) -> "SecurityMasterEvidenceV1":
        if _CANONICAL_VERSION.fullmatch(self.reference.schema_version) is None:
            raise ValueError("reference schema_version must be a bounded canonical version")
        if not self.reference.observed_at <= self.fetched_at <= self.known_at:
            raise ValueError("evidence requires observed_at <= fetched_at <= known_at")
        return self


def _text(value: str, field_name: str) -> str:
    if value != value.strip() or any(ord(character) < 32 or ord(character) > 126 for character in value):
        raise ValueError(f"{field_name} must be bounded printable ASCII without edge whitespace")
    return value


def _positive_decimal(value: str) -> str:
    if _CANONICAL_POSITIVE_DECIMAL.fullmatch(value) is None:
        raise ValueError("financial value must use canonical decimal spelling")
    return value


class IssuerPayloadV1(_FrozenModel):
    issuer_id: UUID
    legal_name: _BoundedText
    jurisdiction: _Token

    @field_validator("legal_name")
    @classmethod
    def _legal_name(cls, value: str) -> str:
        return _text(value, "legal_name")


class AssetPayloadV1(_FrozenModel):
    asset_id: UUID
    code: _Token
    asset_kind: AssetKind
    issuer_id: UUID


class SecurityPayloadV1(_FrozenModel):
    security_id: UUID
    product_type: ProductType
    primary_asset_id: UUID


class VenuePayloadV1(_FrozenModel):
    venue_id: UUID
    code: _Token
    mic: Annotated[str, Field(pattern=r"^[A-Z0-9]{4}$")]
    timezone: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_+./-]+$")]


class ListingPayloadV1(_FrozenModel):
    listing_id: UUID
    security_id: UUID
    venue_id: UUID
    quote_asset_id: UUID
    session_calendar: _Token
    tick_size: _PositiveDecimalText
    size_increment: _PositiveDecimalText
    minimum_quantity: _PositiveDecimalText
    maximum_quantity: _PositiveDecimalText
    minimum_notional: _PositiveDecimalText
    maximum_notional: _PositiveDecimalText

    @field_validator(
        "tick_size",
        "size_increment",
        "minimum_quantity",
        "maximum_quantity",
        "minimum_notional",
        "maximum_notional",
    )
    @classmethod
    def _decimal(cls, value: str) -> str:
        return _positive_decimal(value)

    @model_validator(mode="after")
    def _ordered_bounds(self) -> "ListingPayloadV1":
        if Decimal(self.minimum_quantity) > Decimal(self.maximum_quantity):
            raise ValueError("minimum_quantity must not exceed maximum_quantity")
        if Decimal(self.minimum_notional) > Decimal(self.maximum_notional):
            raise ValueError("minimum_notional must not exceed maximum_notional")
        return self


class SymbolMappingPayloadV1(_FrozenModel):
    mapping_id: UUID
    provider: _Token
    raw_symbol: Annotated[str, Field(min_length=1, max_length=128)]
    canonical_symbol: _Token
    listing_id: UUID

    @field_validator("raw_symbol")
    @classmethod
    def _raw_symbol(cls, value: str) -> str:
        return _text(value, "raw_symbol")


class _CorporateActionPayload(_FrozenModel):
    action_id: UUID
    security_id: UUID


class SplitPayloadV1(_CorporateActionPayload):
    action_type: Literal[CorporateActionType.SPLIT]
    new_units: _PositiveDecimalText
    old_units: _PositiveDecimalText

    @field_validator("new_units", "old_units")
    @classmethod
    def _decimal(cls, value: str) -> str:
        return _positive_decimal(value)


class CashDividendPayloadV1(_CorporateActionPayload):
    action_type: Literal[CorporateActionType.CASH_DIVIDEND]
    amount: _PositiveDecimalText
    currency_asset_id: UUID

    @field_validator("amount")
    @classmethod
    def _decimal(cls, value: str) -> str:
        return _positive_decimal(value)


class SymbolChangePayloadV1(_CorporateActionPayload):
    action_type: Literal[CorporateActionType.SYMBOL_CHANGE]
    old_mapping_id: UUID
    new_mapping_id: UUID

    @model_validator(mode="after")
    def _different_mappings(self) -> "SymbolChangePayloadV1":
        if self.old_mapping_id == self.new_mapping_id:
            raise ValueError("symbol change requires distinct mapping identities")
        return self


class DelistingPayloadV1(_CorporateActionPayload):
    action_type: Literal[CorporateActionType.DELISTING]
    listing_id: UUID


SecurityMasterPayloadV1 = (
    IssuerPayloadV1
    | AssetPayloadV1
    | SecurityPayloadV1
    | VenuePayloadV1
    | ListingPayloadV1
    | SymbolMappingPayloadV1
    | SplitPayloadV1
    | CashDividendPayloadV1
    | SymbolChangePayloadV1
    | DelistingPayloadV1
)


_PAYLOAD_KIND = {
    IssuerPayloadV1: SecurityMasterIdentityKind.ISSUER,
    AssetPayloadV1: SecurityMasterIdentityKind.ASSET,
    SecurityPayloadV1: SecurityMasterIdentityKind.SECURITY,
    VenuePayloadV1: SecurityMasterIdentityKind.VENUE,
    ListingPayloadV1: SecurityMasterIdentityKind.LISTING,
    SymbolMappingPayloadV1: SecurityMasterIdentityKind.SYMBOL_MAPPING,
    SplitPayloadV1: SecurityMasterIdentityKind.CORPORATE_ACTION,
    CashDividendPayloadV1: SecurityMasterIdentityKind.CORPORATE_ACTION,
    SymbolChangePayloadV1: SecurityMasterIdentityKind.CORPORATE_ACTION,
    DelistingPayloadV1: SecurityMasterIdentityKind.CORPORATE_ACTION,
}
_PAYLOAD_ID_FIELD = {
    SecurityMasterIdentityKind.ISSUER: "issuer_id",
    SecurityMasterIdentityKind.ASSET: "asset_id",
    SecurityMasterIdentityKind.SECURITY: "security_id",
    SecurityMasterIdentityKind.VENUE: "venue_id",
    SecurityMasterIdentityKind.LISTING: "listing_id",
    SecurityMasterIdentityKind.SYMBOL_MAPPING: "mapping_id",
    SecurityMasterIdentityKind.CORPORATE_ACTION: "action_id",
}


class SecurityMasterRevisionV1(_FrozenModel):
    schema_version: Literal["security-master-revision-v1"]
    revision_id: UUID
    fact_id: UUID
    subject_id: UUID
    subject_kind: SecurityMasterIdentityKind
    revision_ordinal: Annotated[int, Field(ge=1, le=4096)]
    operation: SecurityMasterOperation
    effective_from: CanonicalUtcDateTime
    effective_to: CanonicalUtcDateTime | None
    known_at: CanonicalUtcDateTime
    supersedes_revision_id: UUID | None
    evidence: Annotated[tuple[SecurityMasterEvidenceV1, ...], Field(min_length=1, max_length=16)]
    payload: SecurityMasterPayloadV1 | None

    @model_validator(mode="after")
    def _closed_revision(self) -> "SecurityMasterRevisionV1":
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be strictly after effective_from")
        if self.revision_ordinal == 1:
            if self.supersedes_revision_id is not None or self.operation is not SecurityMasterOperation.ASSERT:
                raise ValueError("root revision must be ASSERT with no predecessor")
        elif self.supersedes_revision_id is None:
            raise ValueError("non-root revision requires a predecessor")
        evidence_ids = tuple(item.reference.evidence_id for item in self.evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence identifiers must be unique")
        ordered = tuple(sorted(self.evidence, key=lambda item: item.reference.evidence_id.bytes))
        object.__setattr__(self, "evidence", ordered)
        if self.known_at != max(item.known_at for item in ordered):
            raise ValueError("revision known_at must equal maximum evidence known_at")
        if self.operation is SecurityMasterOperation.RETRACT:
            if self.payload is not None:
                raise ValueError("RETRACT payload must be null")
        elif self.payload is None:
            raise ValueError("ASSERT payload is required")
        else:
            expected_kind = _PAYLOAD_KIND[type(self.payload)]
            if self.subject_kind is not expected_kind:
                raise ValueError("subject_kind does not match payload")
            identity = getattr(self.payload, _PAYLOAD_ID_FIELD[expected_kind])
            if self.subject_id != identity:
                raise ValueError("subject_id does not match payload identity")
        if self.subject_kind is SecurityMasterIdentityKind.CORPORATE_ACTION and self.effective_to is not None:
            raise ValueError("corporate action effective_to must be null")
        return self

    @property
    def canonical_revision_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_revision_bytes).hexdigest()


class PersistedSecurityMasterRevisionV1(_FrozenModel):
    """Canonical revision plus its database-owned transaction visibility time."""

    revision: SecurityMasterRevisionV1
    recorded_at: CanonicalUtcDateTime

    @model_validator(mode="after")
    def _recorded_after_evidence(self) -> "PersistedSecurityMasterRevisionV1":
        if self.recorded_at < self.revision.known_at:
            raise ValueError("recorded_at must not precede revision known_at")
        return self


__all__ = [
    "AssetKind",
    "AssetPayloadV1",
    "CashDividendPayloadV1",
    "CorporateActionType",
    "DelistingPayloadV1",
    "IssuerPayloadV1",
    "ListingPayloadV1",
    "PersistedSecurityMasterRevisionV1",
    "SecurityMasterEvidenceV1",
    "SecurityMasterIdentityKind",
    "SecurityMasterOperation",
    "SecurityMasterRevisionV1",
    "SecurityPayloadV1",
    "SplitPayloadV1",
    "SymbolChangePayloadV1",
    "SymbolMappingPayloadV1",
    "VenuePayloadV1",
]

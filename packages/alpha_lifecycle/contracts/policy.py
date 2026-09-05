"""Frozen P3 candidate and research-epoch policy contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from packages.data_contracts import ArtifactRefV1
from .base import AlphaId, DecimalText, DigestModel, SemVer, Sha256, StrictModel, Token
from .data import DateRange


Family = Literal["DONCHIAN", "DUAL_SMA", "ZSCORE", "TSMOM"]


def _json_array(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("value must be a JSON array")
    return tuple(value)


class CandidateParameters(StrictModel):
    family: Family
    entry: int | None
    exit: int | None
    fast: int | None
    slow: int | None
    window: int | None
    entry_z: DecimalText | None
    exit_z: DecimalText | None
    horizons: Annotated[
        tuple[int, int, int], BeforeValidator(_json_array), Field(min_length=3, max_length=3)
    ] | None
    positive_votes: int | None

    @model_validator(mode="after")
    def _family_mask(self) -> "CandidateParameters":
        permitted = {
            "DONCHIAN": {"entry", "exit"},
            "DUAL_SMA": {"fast", "slow"},
            "ZSCORE": {"window", "entry_z", "exit_z"},
            "TSMOM": {"horizons", "positive_votes"},
        }[self.family]
        supplied = {
            name
            for name in (
                "entry", "exit", "fast", "slow", "window", "entry_z", "exit_z",
                "horizons", "positive_votes",
            )
            if getattr(self, name) is not None
        }
        if supplied != permitted:
            raise ValueError("candidate family parameter mask is invalid")
        if self.family == "DONCHIAN" and not self.entry > self.exit > 0:
            raise ValueError("DONCHIAN requires entry > exit > 0")
        if self.family == "DUAL_SMA" and not self.slow > self.fast > 0:
            raise ValueError("DUAL_SMA requires slow > fast > 0")
        if self.family == "ZSCORE" and not (
            self.window > 1 and Decimal(self.entry_z) < Decimal(self.exit_z)
        ):
            raise ValueError("ZSCORE parameters are invalid")
        if self.family == "TSMOM" and not (
            tuple(sorted(set(self.horizons))) == self.horizons
            and self.horizons[0] > 0
            and 1 <= self.positive_votes <= 3
        ):
            raise ValueError("TSMOM parameters are invalid")
        return self


class Perturbation(StrictModel):
    perturbation_id: Annotated[str, Field(pattern=r"^p0[1-4]$")]
    parameters: CandidateParameters


_CANDIDATE_DIGESTS = {
    "a0.donchian-20-10-close-confirm": "c81154f3157832782f394d7b0dbd624d6f29c8fa6f26216697ae9112cf938445",
    "a1.dual-sma-50-200": "aeeaec6f37cf5da8ff0ef789b734633698f6242b153eb34af52468baa1328f53",
    "a2.zscore-20-long-reversion": "10f35fa111592a91a0d88ed15e5e8e0b55e416c07983b7b96aa8e17b5fdf5fc8",
    "a3.tsmom-21-63-126": "10ec0ffcba497bd3a53fc6712cb875504855d7eded6688d50d0e3ec0f94e4970",
}


class CandidateSpec(DigestModel):
    schema_version: Literal["p3-candidate-spec-v1"]
    alpha_id: AlphaId
    version: SemVer
    family: Family
    parameters: CandidateParameters
    perturbations: Annotated[
        tuple[Perturbation, ...],
        BeforeValidator(_json_array),
        Field(min_length=4, max_length=4),
    ]
    digest: Sha256

    @model_validator(mode="after")
    def _bound(self) -> "CandidateSpec":
        if self.parameters.family != self.family or any(
            item.parameters.family != self.family for item in self.perturbations
        ):
            raise ValueError("candidate family does not match its parameters")
        if tuple(item.perturbation_id for item in self.perturbations) != (
            "p01", "p02", "p03", "p04"
        ):
            raise ValueError("candidate perturbations must be p01 through p04")
        if _CANDIDATE_DIGESTS.get(self.alpha_id) != self.digest:
            raise ValueError("candidate digest is outside the accepted policy")
        return self


class ResearchEpoch(DigestModel):
    schema_version: Literal["p3-research-epoch-v1"]
    epoch_id: Token
    candidate_family_digest: Sha256
    development_range: DateRange
    validation_range: DateRange
    oos_ranges: Annotated[
        tuple[DateRange, ...], BeforeValidator(_json_array), Field(min_length=3, max_length=3)
    ]
    holdout_range: DateRange
    holdout_class: Literal[
        "HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND",
        "PROSPECTIVE_UNOBSERVED",
    ]
    previous_exposure_refs: Annotated[
        tuple[ArtifactRefV1, ...], BeforeValidator(_json_array), Field(max_length=128)
    ]
    source_policy_digest: Sha256
    digest: Sha256


__all__ = [
    "CandidateParameters",
    "CandidateSpec",
    "DateRange",
    "DecimalText",
    "Perturbation",
    "ResearchEpoch",
]

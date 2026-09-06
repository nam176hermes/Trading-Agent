"""Deterministic P3 evaluation-input contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from packages.alpha_lifecycle.metrics import CostModelV1
from packages.data_contracts import ArtifactRefV1

from .base import DecimalText, DigestModel, Sha256, SourceIdentity, Text, Token


def _tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("value must be a JSON array")
    return tuple(value)


class EnvironmentIdentity(DigestModel):
    schema_version: Literal["p3-environment-identity-v1"]
    python_version: Text
    root_lock_digest: Sha256
    native_manifest_digest: Sha256
    sandbox_policy_digest: Sha256
    platform: Literal["linux-x86_64"]
    decimal_precision: Literal[50]


class InputSet(DigestModel):
    schema_version: Literal["p3-input-set-v1"]
    source: SourceIdentity
    epoch_id: Token
    policy_digest: Sha256
    family_digest: Sha256
    dataset_evidence_ref: ArtifactRefV1
    fold_manifest_ref: ArtifactRefV1
    pit_proof_ref: ArtifactRefV1
    environment_ref: ArtifactRefV1
    cost_model: CostModelV1
    regime_threshold_ref: ArtifactRefV1
    integration_receipt_ref: ArtifactRefV1


class EvaluationManifest(DigestModel):
    schema_version: Literal["p3-evaluation-manifest-v1"]
    mode: Literal["OOS"]
    input_set_ref: ArtifactRefV1
    candidate_spec_ref: ArtifactRefV1
    baseline_selection_ref: ArtifactRefV1
    candidate_head_ref: ArtifactRefV1
    registration_proof_ref: ArtifactRefV1
    holdout_primary_ref: None


class BaselineManifest(DigestModel):
    schema_version: Literal["p3-baseline-manifest-v1"]
    input_set_ref: ArtifactRefV1
    required_baselines: Annotated[
        tuple[str, ...], BeforeValidator(_tuple), Field(min_length=5, max_length=5)
    ]

    @model_validator(mode="after")
    def _exact_baselines(self) -> "BaselineManifest":
        if self.required_baselines != (
            "B0_CASH", "B1_BUY_AND_HOLD", "B2_EQUAL_WEIGHT",
            "B3_SIMPLE_MOMENTUM", "B4_SIMPLE_MEAN_REVERSION",
        ):
            raise ValueError("baseline manifest must contain exact B0 through B4")
        return self


class HoldoutManifest(DigestModel):
    schema_version: Literal["p3-holdout-manifest-v1"]
    source: SourceIdentity
    primary_selection_ref: ArtifactRefV1
    candidate_spec_ref: ArtifactRefV1
    research_registration_ref: ArtifactRefV1
    holdout_dataset_ref: ArtifactRefV1
    context_dataset_ref: ArtifactRefV1
    buffer_ref: ArtifactRefV1
    policy_digest: Sha256
    selected_baseline: Text
    environment_ref: ArtifactRefV1


class InstrumentSpec(DigestModel):
    schema_version: Literal["p3-instrument-spec-v1"]
    instrument: Literal["BTCUSDT.BINANCE"]
    security_master_ref: ArtifactRefV1
    price_increment: DecimalText
    size_increment: DecimalText
    quote_quantum: DecimalText
    minimum_notional: DecimalText
    base_currency: Literal["BTC"]
    quote_currency: Literal["USDT"]
    historical_rule_claim: Literal["SOURCE_BOUND_SIMULATION_NOT_HISTORICAL_EXCHANGE_RULES"]

    @model_validator(mode="after")
    def _positive_increments(self) -> "InstrumentSpec":
        if min(
            Decimal(self.price_increment), Decimal(self.size_increment),
            Decimal(self.quote_quantum),
        ) <= 0 or Decimal(self.minimum_notional) < 0:
            raise ValueError("instrument increments and minimum notional are invalid")
        return self


__all__ = ["BaselineManifest", "EnvironmentIdentity", "EvaluationManifest", "HoldoutManifest", "InputSet", "InstrumentSpec"]

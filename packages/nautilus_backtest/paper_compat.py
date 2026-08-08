"""Root-Python contracts for one finite paper-compatibility validation."""

from __future__ import annotations

import hashlib
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from packages.engine_contracts import Sha256Hex, canonical_json_bytes


class PaperCompatibilityResultV1(BaseModel):
    """Digest-only proof emitted after one captured compatibility process."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    schema_version: Literal["nautilus-paper-compatibility-result-v1"]
    compatible: Literal[True]
    candidate_closure_sha256: Sha256Hex
    candidate_manifest_sha256: Sha256Hex
    engine_configuration_sha256: Sha256Hex
    instrument_catalog_sha256: Sha256Hex
    strategy_configuration_sha256: Sha256Hex
    strategy_source_sha256: Sha256Hex
    scenario_campaign_sha256: Sha256Hex
    parity_record_sha256: Sha256Hex
    launcher_result_sha256: Sha256Hex
    result_sha256: Sha256Hex

    @classmethod
    def create(
        cls,
        *,
        candidate_closure_sha256: str,
        candidate_manifest_sha256: str,
        engine_configuration_sha256: str,
        instrument_catalog_sha256: str,
        strategy_configuration_sha256: str,
        strategy_source_sha256: str,
        scenario_campaign_sha256: str,
        parity_record_sha256: str,
        launcher_result_sha256: str,
    ) -> Self:
        domain = {
            "candidate_closure_sha256": candidate_closure_sha256,
            "candidate_manifest_sha256": candidate_manifest_sha256,
            "compatible": True,
            "engine_configuration_sha256": engine_configuration_sha256,
            "instrument_catalog_sha256": instrument_catalog_sha256,
            "launcher_result_sha256": launcher_result_sha256,
            "parity_record_sha256": parity_record_sha256,
            "scenario_campaign_sha256": scenario_campaign_sha256,
            "schema_version": "nautilus-paper-compatibility-result-v1",
            "strategy_configuration_sha256": strategy_configuration_sha256,
            "strategy_source_sha256": strategy_source_sha256,
        }
        return cls(
            **domain,
            result_sha256=hashlib.sha256(canonical_json_bytes(domain)).hexdigest(),
        )

    @model_validator(mode="after")
    def _validate_result_digest(self) -> Self:
        domain = self.model_dump(exclude={"result_sha256"})
        expected = hashlib.sha256(canonical_json_bytes(domain)).hexdigest()
        if self.result_sha256 != expected:
            raise ValueError("paper compatibility result digest does not match")
        return self


__all__ = ["PaperCompatibilityResultV1"]

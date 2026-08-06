"""Offline WS-04 closure proof; this module grants no runtime authority."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import model_validator

from packages.data_catalog import MarketDatasetManifestV1
from packages.engine_contracts import EngineCommandEnvelope, EngineEventEnvelope, RunBacktest, canonical_json_bytes
from packages.engine_contracts.serialization import Sha256Hex, SourceCommit
from packages.nautilus_backtest import validate_isolated_backtest_result

from .evaluator import evaluate_research_gates
from .models import ResearchGateEvidenceV1, ResearchValidationModel


class ResearchClosureError(ValueError):
    """WS-04 evidence cannot be closed into a complete research proof."""


class Ws04ClosureV1(ResearchValidationModel):
    """Hash-bound source acceptance proof for 04A through 04D."""

    schema_version: Literal["ws04-closure-v1"] = "ws04-closure-v1"
    dataset_content_sha256: Sha256Hex
    canonical_rows_sha256: Sha256Hex
    engine_configuration_sha256: Sha256Hex
    instrument_catalog_sha256: Sha256Hex
    strategy_configuration_sha256: Sha256Hex
    market_data_sha256: Sha256Hex
    backtest_result_sha256: Sha256Hex
    research_evidence_sha256: Sha256Hex
    research_report_sha256: Sha256Hex
    source_commit: SourceCommit
    closure_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def _complete_digest(self) -> "Ws04ClosureV1":
        digest = hashlib.sha256(
            canonical_json_bytes(
                self.model_dump(mode="json", exclude={"closure_sha256"})
            )
        ).hexdigest()
        if self.closure_sha256 is not None and self.closure_sha256 != digest:
            raise ValueError("WS-04 closure digest does not match evidence")
        object.__setattr__(self, "closure_sha256", digest)
        return self


def _require_exact_bindings(
    manifest: MarketDatasetManifestV1,
    request: EngineCommandEnvelope,
    evidence: ResearchGateEvidenceV1,
    backtest_result_sha256: str,
) -> None:
    if type(request.payload) is not RunBacktest:
        raise ResearchClosureError("exact RunBacktest request is required")
    provenance = evidence.provenance
    payload = request.payload
    if provenance.dataset != manifest:
        raise ResearchClosureError("dataset manifest is not bound to research provenance")
    if (
        provenance.dataset_content_sha256 != manifest.content_digest
        or provenance.canonical_rows_sha256 != manifest.canonical_rows_sha256
        or provenance.market_data_sha256 != manifest.canonical_rows_sha256
    ):
        raise ResearchClosureError("dataset provenance digest drifted")
    if (
        provenance.engine_configuration_sha256 != payload.engine_configuration.sha256
        or provenance.instrument_catalog_sha256 != payload.instrument_catalog.sha256
        or provenance.strategy_configuration_sha256 != payload.strategy_configuration.sha256
        or provenance.market_data_sha256 != payload.market_data.sha256
    ):
        raise ResearchClosureError("request artifact binding drifted")
    if (
        provenance.backtest_result_sha256 != backtest_result_sha256
        or provenance.source_commit != request.source_commit
    ):
        raise ResearchClosureError("backtest result provenance drifted")


def close_ws04_research(
    manifest: MarketDatasetManifestV1,
    request: EngineCommandEnvelope,
    event: EngineEventEnvelope,
    evidence: ResearchGateEvidenceV1,
) -> Ws04ClosureV1:
    """Return WS-04 source proof only if the exact 04C run and all 04D gates pass."""

    if type(manifest) is not MarketDatasetManifestV1:
        raise TypeError("MarketDatasetManifestV1 is required")
    if type(evidence) is not ResearchGateEvidenceV1:
        raise TypeError("ResearchGateEvidenceV1 is required")
    result = validate_isolated_backtest_result(request, event)
    _require_exact_bindings(manifest, request, evidence, result.result_sha256)
    report = evaluate_research_gates(evidence)
    if not report.passed:
        raise ResearchClosureError("mandatory WS-04 research gates did not pass")
    assert isinstance(report.report_sha256, str)
    assert isinstance(request.payload, RunBacktest)
    return Ws04ClosureV1(
        dataset_content_sha256=manifest.content_digest,
        canonical_rows_sha256=manifest.canonical_rows_sha256,
        engine_configuration_sha256=request.payload.engine_configuration.sha256,
        instrument_catalog_sha256=request.payload.instrument_catalog.sha256,
        strategy_configuration_sha256=request.payload.strategy_configuration.sha256,
        market_data_sha256=request.payload.market_data.sha256,
        backtest_result_sha256=result.result_sha256,
        research_evidence_sha256=report.evidence_sha256,
        research_report_sha256=report.report_sha256,
        source_commit=request.source_commit,
    )

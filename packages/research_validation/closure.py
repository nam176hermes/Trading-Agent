"""Offline WS-04 closure proof; this module grants no runtime authority."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from packages.data_catalog import MarketDatasetManifestV1
from packages.engine_contracts import EngineCommandEnvelope, EngineEventEnvelope, RunBacktest, canonical_json_bytes
from packages.engine_contracts.serialization import Sha256Hex, SourceCommit
from packages.nautilus_backtest import validate_isolated_backtest_result
from packages.nautilus_backtest.scenarios import ScenarioId

from .artifacts import ResearchEvidenceArtifactReference, load_verified_evidence
from .evaluator import evaluate_research_campaign, evaluate_research_gates
from .models import (
    PHASE4_SCENARIO_IDS,
    ResearchCampaignEvidenceV2,
    ResearchGateEvidenceV1,
    ResearchValidationModel,
)


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
    research_evidence_artifact_sha256: Sha256Hex
    research_analysis_output_sha256: Sha256Hex
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
    backtest_event_sha256: str,
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
        or provenance.backtest_event_sha256 != backtest_event_sha256
        or provenance.source_commit != request.source_commit
    ):
        raise ResearchClosureError("backtest result provenance drifted")


def close_ws04_research(
    manifest: MarketDatasetManifestV1,
    request: EngineCommandEnvelope,
    event: EngineEventEnvelope,
    evidence_reference: ResearchEvidenceArtifactReference,
) -> Ws04ClosureV1:
    """Return WS-04 source proof only if the exact 04C run and all 04D gates pass."""

    if type(manifest) is not MarketDatasetManifestV1:
        raise TypeError("MarketDatasetManifestV1 is required")
    evidence, evidence_artifact = load_verified_evidence(evidence_reference)
    artifact_sha256 = hashlib.sha256(evidence_artifact).hexdigest()
    result = validate_isolated_backtest_result(request, event)
    event_sha256 = hashlib.sha256(canonical_json_bytes(event)).hexdigest()
    _require_exact_bindings(
        manifest, request, evidence, result.result_sha256, event_sha256
    )
    report = evaluate_research_gates(evidence)
    if not report.passed:
        raise ResearchClosureError("mandatory WS-04 research gates did not pass")
    assert isinstance(report.report_sha256, str)
    assert isinstance(report.evidence_sha256, str)
    if report.evidence_sha256 != artifact_sha256:
        raise ResearchClosureError("research evidence artifact hash drifted")
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
        research_evidence_artifact_sha256=artifact_sha256,
        research_analysis_output_sha256=evidence.analysis_output_sha256,
        research_report_sha256=report.report_sha256,
        source_commit=request.source_commit,
    )


class Ws04ScenarioClosureV2(ResearchValidationModel):
    schema_version: Literal["ws04-scenario-closure-v2"] = "ws04-scenario-closure-v2"
    scenario_id: ScenarioId
    engine_configuration_sha256: Sha256Hex
    instrument_catalog_sha256: Sha256Hex
    strategy_configuration_sha256: Sha256Hex
    market_data_sha256: Sha256Hex
    simulation_scenario_sha256: Sha256Hex
    reference_result_sha256: Sha256Hex
    reference_event_sha256: Sha256Hex
    nautilus_result_sha256: Sha256Hex
    nautilus_event_sha256: Sha256Hex
    legacy_result_sha256: Sha256Hex
    legacy_event_sha256: Sha256Hex
    legacy_disposition: Literal["explained-difference"]
    legacy_classification: Literal["legacy-minimum-50-bars"]
    legacy_selected: Literal[False]
    closure_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def _complete_digest(self) -> "Ws04ScenarioClosureV2":
        digest = hashlib.sha256(
            canonical_json_bytes(
                self.model_dump(mode="json", exclude={"closure_sha256"})
            )
        ).hexdigest()
        if self.closure_sha256 is not None and self.closure_sha256 != digest:
            raise ValueError("WS-04 scenario closure digest does not match")
        object.__setattr__(self, "closure_sha256", digest)
        return self


class Ws04CampaignClosureV2(ResearchValidationModel):
    schema_version: Literal["ws04-campaign-closure-v2"] = "ws04-campaign-closure-v2"
    scenario_campaign_sha256: Sha256Hex
    strategy_source_sha256: Sha256Hex
    candidate_closure_sha256: Sha256Hex
    candidate_manifest_sha256: Sha256Hex
    parity_record_sha256: Sha256Hex
    paper_record_sha256: Sha256Hex
    paper_result_sha256: Sha256Hex
    legacy_records_sha256: Sha256Hex
    research_evidence_sha256: Sha256Hex
    research_analysis_output_sha256: Sha256Hex
    research_report_sha256: Sha256Hex
    scenarios: tuple[Ws04ScenarioClosureV2, ...] = Field(
        min_length=8, max_length=8
    )
    promotion_authority: Literal["reference-and-nautilus"]
    closure_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def _complete_digest(self) -> "Ws04CampaignClosureV2":
        scenario_ids = tuple(item.scenario_id for item in self.scenarios)
        if set(scenario_ids) != set(PHASE4_SCENARIO_IDS):
            raise ValueError("WS-04 closure requires the exact eight-scenario set")
        if scenario_ids != PHASE4_SCENARIO_IDS:
            raise ValueError("WS-04 closure scenarios must be repository ordered")
        digest = hashlib.sha256(
            canonical_json_bytes(
                self.model_dump(mode="json", exclude={"closure_sha256"})
            )
        ).hexdigest()
        if self.closure_sha256 is not None and self.closure_sha256 != digest:
            raise ValueError("WS-04 campaign closure digest does not match")
        object.__setattr__(self, "closure_sha256", digest)
        return self


def _close_ws04_research_campaign_evidence(
    evidence: ResearchCampaignEvidenceV2,
) -> Ws04CampaignClosureV2:
    """Close the fixed campaign only after all derived 04D gates pass."""

    if type(evidence) is not ResearchCampaignEvidenceV2:
        raise TypeError("ResearchCampaignEvidenceV2 is required")
    report = evaluate_research_campaign(evidence)
    if not report.passed:
        raise ResearchClosureError("mandatory WS-04 campaign gates did not pass")
    scenarios = tuple(
        Ws04ScenarioClosureV2(
            scenario_id=item.scenario_id,
            engine_configuration_sha256=item.engine_configuration_sha256,
            instrument_catalog_sha256=item.instrument_catalog_sha256,
            strategy_configuration_sha256=item.strategy_configuration_sha256,
            market_data_sha256=item.market_data_sha256,
            simulation_scenario_sha256=item.simulation_scenario_sha256,
            reference_result_sha256=item.independent_reference_result_sha256,
            reference_event_sha256=item.independent_reference_event_sha256,
            nautilus_result_sha256=item.nautilus_result_sha256,
            nautilus_event_sha256=item.nautilus_event_sha256,
            legacy_result_sha256=item.legacy_result_sha256,
            legacy_event_sha256=item.legacy_event_sha256,
            legacy_disposition=item.legacy_disposition,
            legacy_classification=item.legacy_classification,
            legacy_selected=False,
        )
        for item in evidence.comparisons
    )
    assert report.report_sha256 is not None
    return Ws04CampaignClosureV2(
        scenario_campaign_sha256=evidence.scenario_campaign_sha256,
        strategy_source_sha256=evidence.strategy_source_sha256,
        candidate_closure_sha256=evidence.candidate_closure_sha256,
        candidate_manifest_sha256=evidence.candidate_manifest_sha256,
        parity_record_sha256=evidence.parity_record_sha256,
        paper_record_sha256=evidence.paper_record_sha256,
        paper_result_sha256=evidence.paper_result.result_sha256,
        legacy_records_sha256=evidence.legacy_records_sha256,
        research_evidence_sha256=report.evidence_sha256,
        research_analysis_output_sha256=evidence.analysis_output_sha256,
        research_report_sha256=report.report_sha256,
        scenarios=scenarios,
        promotion_authority="reference-and-nautilus",
    )


def _custody_sha256(value: str, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ResearchClosureError(f"{label} custody digest is invalid")
    return value


def close_ws04_research_campaign(
    *,
    campaign_directory: Path,
    campaign_sha256: str,
    parity_record: Path,
    parity_record_sha256: str,
    paper_record: Path,
    paper_record_sha256: str,
    legacy_record_directory: Path,
    legacy_records_sha256: str,
) -> Ws04CampaignClosureV2:
    """Reload reviewed sealed inputs and close only their selected custody digests."""

    from .producers import produce_research_campaign_evidence

    evidence = produce_research_campaign_evidence(
        campaign_directory=campaign_directory,
        parity_record=parity_record,
        paper_record=paper_record,
        legacy_record_directory=legacy_record_directory,
    )
    selected = {
        "campaign": _custody_sha256(campaign_sha256, label="campaign"),
        "parity record": _custody_sha256(
            parity_record_sha256, label="parity record"
        ),
        "paper record": _custody_sha256(
            paper_record_sha256, label="paper record"
        ),
        "legacy records": _custody_sha256(
            legacy_records_sha256, label="legacy records"
        ),
    }
    observed = {
        "campaign": evidence.scenario_campaign_sha256,
        "parity record": evidence.parity_record_sha256,
        "paper record": evidence.paper_record_sha256,
        "legacy records": evidence.legacy_records_sha256,
    }
    if selected != observed:
        raise ResearchClosureError("reviewed custody digest does not match evidence")
    return _close_ws04_research_campaign_evidence(evidence)

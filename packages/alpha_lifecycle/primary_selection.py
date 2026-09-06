"""Frozen one-primary selection across all four disclosed P3 candidates."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from statistics import median

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore
from packages.alpha_lifecycle.contracts.authority import FamilyReview, PrimarySelection
from packages.alpha_lifecycle.contracts.execution import InputSet
from packages.alpha_lifecycle.contracts.lifecycle import CampaignClosureReport, PublicationReceipt
from packages.alpha_lifecycle.contracts.results import QualificationBundle
from packages.alpha_lifecycle.protocol import AlphaQualificationEvidenceV1
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def ranking_key(
    alpha_id: str,
    fold_excess: tuple[Decimal, ...],
    aggregate_excess: Decimal,
    max_drawdown: Decimal,
) -> tuple[Decimal, Decimal, Decimal, str]:
    return (-median(fold_excess), -aggregate_excess, max_drawdown, alpha_id)


def _read(reader: ArtifactStore, ref: ArtifactRefV1, model):
    return model.model_validate_json(reader.read_bytes(ref))


def select_primary(review: FamilyReview, reader: ArtifactStore) -> PrimarySelection:
    review = FamilyReview.model_validate(review)
    if len(review.candidate_report_refs) != 4:
        raise ValueError("primary selection requires all four candidate reports")
    eligible = []
    for closure_ref in review.candidate_report_refs:
        closure = _read(reader, closure_ref, CampaignClosureReport)
        if closure.qualification_ref is None:
            raise ValueError("candidate closure lacks qualification evidence")
        qualification = _read(reader, closure.qualification_ref, QualificationBundle)
        evidence = _read(reader, qualification.legacy_evidence_ref, AlphaQualificationEvidenceV1)
        receipt = _read(reader, closure.publication_ref, PublicationReceipt)
        if qualification.alpha_verdict == "PASS" and all(item.passed for item in qualification.criteria):
            eligible.append((
                ranking_key(
                    evidence.alpha_id,
                    evidence.fold_excess_returns,
                    evidence.metrics.total_return - evidence.baseline_result.total_return,
                    evidence.metrics.max_drawdown,
                ),
                evidence,
                receipt.registry_event_refs[-1],
            ))
    input_set = _read(reader, review.input_set_ref, InputSet)
    review_ref = reader.put_bytes(canonical_json_bytes(review), media_type="application/json")
    if eligible:
        _, evidence, head_ref = min(eligible, key=lambda item: item[0])
        payload = {
            "schema_version":"p3-primary-selection-v1","family_review_ref":review_ref,
            "selection_policy_digest":input_set.policy_digest,
            "primary_alpha_id":evidence.alpha_id,"primary_version":evidence.alpha_version,
            "primary_candidate_head_ref":head_ref,"outcome":"SELECTED",
        }
    else:
        payload = {
            "schema_version":"p3-primary-selection-v1","family_review_ref":review_ref,
            "selection_policy_digest":input_set.policy_digest,
            "primary_alpha_id":None,"primary_version":None,
            "primary_candidate_head_ref":None,"outcome":"NONE_QUALIFIED",
        }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return PrimarySelection.model_validate(payload)


__all__ = ["ranking_key", "select_primary"]

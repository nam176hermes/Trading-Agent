"""Post-commit P3 receipt and closure artifacts."""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID
from typing import TypeVar

from pydantic import BaseModel

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore
from packages.alpha_lifecycle.contracts.lifecycle import (
    CampaignClosureReport,
    PrePublicationEvidence,
    PublicationReceipt,
    PublicationRequest,
)
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_publication_repository import JobCommitResult


Model = TypeVar("Model", bound=BaseModel)


def _read(store: ArtifactStore, ref: ArtifactRefV1, model: type[Model]) -> Model:
    raw = store.read_bytes(ref)
    value = model.model_validate_json(raw)
    if canonical_json_bytes(value) != raw:
        raise ValueError("publication artifact is not canonical")
    return value


def _seal(store: ArtifactStore, value: object) -> ArtifactRefV1:
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def _digest(payload: dict[str, object]) -> str:
    def json_value(value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, datetime):
            return value.isoformat().replace("+00:00", "Z")
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, dict):
            return {key: json_value(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [json_value(item) for item in value]
        return value

    return hashlib.sha256(canonical_json_bytes(json_value(payload))).hexdigest()


def build_publication_receipt(
    request_ref: ArtifactRefV1,
    commit: JobCommitResult,
    *,
    committed_at: datetime,
    store: ArtifactStore,
) -> PublicationReceipt:
    request = _read(store, request_ref, PublicationRequest)
    commit = JobCommitResult.model_validate(commit).bound_to(request)
    payload = {
        "schema_version": "p3-publication-receipt-v1",
        "request_ref": request_ref,
        "ledger_event_ids": commit.ledger_event_ids,
        "registry_event_refs": commit.registry_event_refs,
        "commit_result_ref": _seal(store, commit),
        "committed_at": committed_at,
        "outcome": "COMMITTED",
    }
    payload["digest"] = _digest(payload)
    return PublicationReceipt.model_validate(payload)


def build_closure_report(
    request: PublicationRequest,
    prepublication_ref: ArtifactRefV1,
    receipt: PublicationReceipt,
    *,
    store: ArtifactStore,
    projection_digest: str | None = None,
) -> CampaignClosureReport:
    request = PublicationRequest.model_validate(request)
    receipt = PublicationReceipt.model_validate(receipt)
    committed_request = _read(store, receipt.request_ref, PublicationRequest)
    commit = _read(store, receipt.commit_result_ref, JobCommitResult).bound_to(request)
    evidence = _read(store, prepublication_ref, PrePublicationEvidence)
    if (
        committed_request != request
        or prepublication_ref != request.evidence_ref
        or evidence.stage != request.stage
        or receipt.ledger_event_ids != commit.ledger_event_ids
        or receipt.registry_event_refs != commit.registry_event_refs
    ):
        raise ValueError("publication closure artifacts do not match the committed request")
    payload = {
        "schema_version": "p3-campaign-closure-report-v1",
        "stage": request.stage,
        "prepublication_ref": prepublication_ref,
        "publication_ref": _seal(store, receipt),
        "qualification_ref": evidence.qualification_bundle_ref,
        "exit_result_ref": evidence.exit_result_ref,
        "projection_digest": projection_digest,
        "projection_status": "READY" if projection_digest is not None else "PENDING",
    }
    payload["digest"] = _digest(payload)
    return CampaignClosureReport.model_validate(payload)


__all__ = ["build_closure_report", "build_publication_receipt"]

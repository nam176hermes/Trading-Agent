"""Provider-neutral evidence capture over the existing injected market-data boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from uuid import UUID

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import (
    ArtifactRefV1,
    ProviderCapabilityV1,
    ProviderReceiptV1,
    RawEvidenceArtifactV1,
)
from packages.domain import MarketSnapshot
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import MarketDataSnapshotRequest
from services.market_data.fixture import MarketDataProvider, ProviderObservation


class ProviderIngestionError(ValueError):
    """Provider output does not preserve the request and raw evidence identity."""


@dataclass(frozen=True, slots=True)
class ProviderIngestionV1:
    snapshot: MarketSnapshot
    evidence: RawEvidenceArtifactV1
    artifact: ArtifactRefV1
    receipt: ProviderReceiptV1


def ingest_market_data(
    provider: MarketDataProvider,
    request: MarketDataSnapshotRequest,
    *,
    store: LocalArtifactStore,
    evidence_id: UUID,
) -> ProviderIngestionV1:
    """Capture one provider observation without granting network or retry authority."""

    if not isinstance(request, MarketDataSnapshotRequest):
        raise ProviderIngestionError("market-data request has invalid type")
    observation = provider.fetch(request)
    if not isinstance(observation, ProviderObservation):
        raise ProviderIngestionError("provider returned an invalid observation")
    snapshot = MarketSnapshot.model_validate(observation.snapshot)
    raw = observation.raw_evidence
    if not isinstance(raw, bytes):
        raise ProviderIngestionError("provider raw evidence must be bytes")
    digest = hashlib.sha256(raw).hexdigest()
    if (
        snapshot.provenance.provider != request.provider
        or snapshot.provenance.raw_evidence_sha256 != digest
        or snapshot.provenance.observed_at > snapshot.provenance.fetched_at
    ):
        raise ProviderIngestionError("provider observation does not bind request and evidence")

    artifact = store.put_bytes(raw, media_type="application/json")
    evidence = RawEvidenceArtifactV1(
        evidence_id=evidence_id,
        provider=request.provider,
        media_type=artifact.media_type,
        byte_length=artifact.size_bytes,
        content_sha256=artifact.content_sha256,
        source_available_at=snapshot.provenance.observed_at,
        system_observed_at=snapshot.provenance.fetched_at,
        fetched_at=snapshot.provenance.fetched_at,
    )
    output_digest = hashlib.sha256(canonical_json_bytes(snapshot)).hexdigest()
    receipt = ProviderReceiptV1(
        provider=request.provider,
        capability=ProviderCapabilityV1.MARKET_BARS,
        query_sha256=hashlib.sha256(canonical_json_bytes(request)).hexdigest(),
        evidence=(evidence,),
        normalization_version=snapshot.normalization_version,
        output_sha256s=(output_digest,),
    )
    return ProviderIngestionV1(snapshot, evidence, artifact, receipt)


__all__ = [
    "ProviderIngestionError",
    "ProviderIngestionV1",
    "ingest_market_data",
]

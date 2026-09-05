from datetime import UTC, datetime, timedelta
from uuid import UUID

from packages.alpha_lifecycle.lifecycle import plan_transition, to_domain_payload
from packages.alpha_lifecycle.projection import rebuild_projection
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.domain.events import EventEnvelope
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_lifecycle import _evidence, _record
from packages.alpha_lifecycle.registry import AlphaLifecycleStatus


def test_projection_rebuilds_only_from_ledger_bound_registry_artifacts(tmp_path) -> None:
    root = tmp_path/"cas"; root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)
    evidence = _evidence()
    registry = plan_transition(_record(AlphaLifecycleStatus.IDEA),None,evidence)
    raw = canonical_json_bytes({
        "predecessor_sha256":registry.predecessor_sha256,"record":registry.record,
        "schema_version":registry.schema_version,"sequence":registry.sequence,
    })
    store.put_bytes(raw,media_type="application/json")
    now = datetime(2026,9,5,tzinfo=UTC)
    event = EventEnvelope(
        event_id=UUID("11111111-1111-5111-8111-111111111111"),
        event_type="AlphaRegistryTransitionRecordedV1",schema_version="event-envelope-v1",
        source="p3-alpha-lifecycle",stream_id=UUID("22222222-2222-5222-8222-222222222222"),
        sequence=1,observed_at=now,ingested_at=now,produced_at=now,effective_at=now,
        expires_at=now+timedelta(days=1),correlation_id=UUID(int=3),causation_id=UUID(int=4),
        trace_id=UUID(int=5),payload=to_domain_payload(registry,evidence,epoch_id="p3-btc-d1-e1"),
    )
    first = rebuild_projection((event,),store)
    assert first == rebuild_projection((event,),store)
    assert b'"lifecycle_status":"IDEA"' in first
    assert b'"publication_status":"COMMITTED_REPORT_PENDING"' in first

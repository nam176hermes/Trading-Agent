"""Stage unprivileged P3 proposals; only the existing SQL capability commits."""
from datetime import datetime
import hashlib
from uuid import UUID, uuid5

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore
from packages.alpha_lifecycle.contracts.lifecycle import ExpectedHead, PrePublicationEvidence, PublicationRequest
from packages.alpha_lifecycle.lifecycle import to_domain_payload
from packages.alpha_lifecycle.registry import AlphaRegistryEventV1
from packages.domain.alpha_events import AlphaRegistryTransitionRecordedV1
from packages.domain.events import EventEnvelope
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_sql import DomainAppendEntry, PublicationProposal


_P3_NAMESPACE = UUID('5d3f7ad6-7372-5cb7-80a7-68a179f1fdb2')


def prepare_family_registration(intent, *, job_id: str, observed_at: datetime,
    expires_at: datetime, store: ArtifactStore) -> PublicationProposal:
    from packages.alpha_lifecycle.baseline_campaign import _read
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from packages.alpha_lifecycle.contracts.policy import CandidateSpec
    from packages.alpha_lifecycle.lifecycle import plan_transition
    from packages.alpha_lifecycle.operation_input import P3OperationInput, RegisterFamilyInput, FAMILY_IDS
    from packages.alpha_lifecycle.publication import validate_registration_records
    from packages.alpha_lifecycle.registry import AlphaLifecycleStatus
    intent = P3OperationInput.model_validate(intent)
    if not isinstance(intent.body,RegisterFamilyInput):
        raise ValueError('family registration requires its exact operation intent')
    specs = tuple(_read(store,ref,CandidateSpec) for ref in intent.body.candidate_spec_refs)
    if tuple(spec.alpha_id for spec in specs) != FAMILY_IDS:
        raise ValueError('family registration requires all four ordered frozen specifications')
    value = dict(schema_version='p3-pre-publication-evidence-v1',stage='REGISTER',
        input_set_ref=intent.input_set_ref,baseline_selection_ref=intent.body.baseline_selection_ref,
        qualification_bundle_ref=None,exit_result_ref=None,candidate_record_refs=intent.body.candidate_record_refs)
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    evidence = PrePublicationEvidence.model_validate_json(canonical_json_bytes(value))
    records = validate_registration_records(evidence,store)
    inputs = _read(store,intent.input_set_ref,InputSet)
    events,heads = [],[]
    for record in records:
        idea = plan_transition(record,None,evidence)
        events.extend((idea,plan_transition(record.model_copy(update={'lifecycle_status':AlphaLifecycleStatus.CANDIDATE}),idea,evidence)))
        heads.append(ExpectedHead(alpha_id=record.alpha_id,version=record.version,sequence=0,event_digest=None))
    return build_publication_proposal(events=tuple(events),expected_heads=tuple(heads),evidence=evidence,
        epoch_id=inputs.epoch_id,job_id=job_id,observed_at=observed_at,expires_at=expires_at,store=store)


def build_publication_proposal(
    *, events: tuple[AlphaRegistryEventV1, ...], expected_heads: tuple[ExpectedHead, ...],
    evidence: PrePublicationEvidence, epoch_id: str, job_id: str,
    observed_at: datetime, expires_at: datetime, store: ArtifactStore,
) -> PublicationProposal:
    """Use the caller's retained informational interval, never completion time."""
    evidence = PrePublicationEvidence.model_validate(evidence)
    heads = tuple(ExpectedHead.model_validate(head) for head in expected_heads)
    pending = {(head.alpha_id,head.version):(head.sequence,head.event_digest) for head in heads}
    if len(pending) != len(heads):
        raise ValueError('publication predecessor heads repeat')
    evidence_ref = store.put_bytes(canonical_json_bytes(evidence),media_type='application/json')
    entries, refs, touched = [], [], set()
    for event in events:
        event = AlphaRegistryEventV1.model_validate(event)
        record = event.record
        state, decision = record.lifecycle_status.value, record.qualification_decision.value
        if (
            (state in {'IDEA','CANDIDATE'} and (decision != 'NOT_EVALUATED'
                or record.metrics_sha256 is not None or record.robustness_sha256 is not None))
            or (state in {'OOS_PASS','QUALIFIED'} and (decision != 'PASS'
                or record.metrics_sha256 is None or record.robustness_sha256 is None))
            or (state == 'REJECTED' and decision != 'FAIL')
        ):
            raise ValueError('publication lifecycle and qualification identity disagree')
        key = (event.record.alpha_id,event.record.version)
        if key not in pending or pending[key] != (event.sequence-1,event.predecessor_sha256):
            raise ValueError('publication events do not follow expected predecessors')
        pending[key] = (event.sequence,event.event_sha256)
        touched.add(key)
        payload = to_domain_payload(event,evidence,epoch_id=epoch_id)
        ref = store.put_bytes(payload.registry_event_text.encode('utf-8'),media_type='application/json')
        if ref != event.artifact:
            raise ValueError('publication registry artifact differs from planned event')
        refs.append(ref)
        stream_id = uuid5(_P3_NAMESPACE,canonical_json_bytes([epoch_id,*key]).decode())
        event_id = uuid5(stream_id,canonical_json_bytes([event.sequence,event.event_sha256]).decode())
        envelope = EventEnvelope[AlphaRegistryTransitionRecordedV1](
            event_id=event_id,event_type='AlphaRegistryTransitionRecordedV1',schema_version='event-envelope-v1',
            source='p3-alpha-lifecycle',stream_id=stream_id,sequence=event.sequence,
            observed_at=observed_at,ingested_at=observed_at,produced_at=observed_at,
            effective_at=observed_at,expires_at=expires_at,
            correlation_id=uuid5(_P3_NAMESPACE,epoch_id),
            causation_id=uuid5(_P3_NAMESPACE,evidence_ref.content_sha256),
            trace_id=uuid5(_P3_NAMESPACE,job_id),payload=payload,
        )
        entries.append(DomainAppendEntry(event_id=event_id,stream_id=stream_id,sequence=event.sequence,
            event_type='AlphaRegistryTransitionRecordedV1',canonical_event_text=canonical_json_bytes(envelope).decode(),
            topic='p3.alpha-registry',outbox_payload_text=canonical_json_bytes({'event_id':str(event_id)}).decode()))
    if touched != set(pending):
        raise ValueError('publication event batch omits expected heads')
    states = tuple(event.record.lifecycle_status.value for event in events)
    if not (
        (evidence.stage == 'REGISTER' and len(heads) == 4 and states == ('IDEA','CANDIDATE')*4)
        or (evidence.stage == 'RESEARCH_DECISION' and len(heads) == 1 and states in (
            ('RESEARCHED','OOS_PASS'),('RESEARCHED','REJECTED')))
        or (evidence.stage == 'EXIT_DECISION' and len(heads) == 1 and states in (('QUALIFIED',),('REJECTED',)))
    ):
        raise ValueError('publication batch does not contain the complete stage decision')
    semantic = dict(stage=evidence.stage,evidence_ref=evidence_ref,expected_heads=heads,proposed_event_refs=refs)
    digest = hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()
    request = dict(schema_version='p3-publication-request-v1',idempotency_key='publication.'+digest,
                   semantic_request_digest=digest,job_id=job_id,**semantic)
    request['digest'] = hashlib.sha256(canonical_json_bytes(request)).hexdigest()
    proposal = PublicationProposal(request=PublicationRequest.model_validate_json(canonical_json_bytes(request)),entries=tuple(entries))
    raw = canonical_json_bytes(proposal)
    retained = store.put_bytes(raw,media_type='application/json')
    if store.read_bytes(retained) != raw:
        raise ValueError('publication proposal readback differs')
    return proposal

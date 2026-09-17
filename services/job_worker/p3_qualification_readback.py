"""Read retained qualification bytes; only protected custody authenticates a run."""
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC,datetime
from decimal import Decimal
import hashlib
import json
import re
from uuid import NAMESPACE_URL,uuid5

from packages.engine_contracts import EngineCommandEnvelope,RunBacktest,canonical_json_bytes,payload_digest
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.replica_store import ArtifactStore
from packages.alpha_lifecycle.contracts.authority import IntegrationReceipt
from packages.job_contracts import AlphaCampaignPayload
from packages.data_contracts import ArtifactRefV1
from packages.nautilus_runtime_contracts.result import validate_p1_result, P1ValidatedResult
from .artifacts import MAX_STREAM_BYTES,ArtifactMetadata,_IDENTIFIER
from .engine_profiles import P1_REAL_BACKTEST_POLICY
from .engine_results import EngineResultValidator
from .process_runner import ProcessOutcome,ProcessLineage,_safety_metadata,_JOB_ID,_ATTEMPT_ID
from .safety_state import SafetyEvidence,validate_current_safety_evidence
from .safety import SafetyMode,KillSwitchState
from .errors import SafetyBlockedError
from .recovery import ProcessIdentity


_SHA=re.compile('[0-9a-f]{64}')


def _read(ref: ArtifactRefV1, store: ArtifactStore, *, media_type: str, max_bytes: int) -> bytes:
    ref=ArtifactRefV1.model_validate(ref)
    if (ref.media_type!=media_type or ref.locator!=ref.content_sha256+'.blob'
        or not 0<=ref.size_bytes<=max_bytes):
        raise ValueError('qualification reference media or bound differs')
    raw=store.read_bytes(ref)
    if len(raw)!=ref.size_bytes or hashlib.sha256(raw).hexdigest()!=ref.content_sha256:
        raise ValueError('qualification retained bytes differ')
    return raw


def _object(raw,keys):
    value=json.loads(raw)
    if not isinstance(value,dict) or set(value)!=set(keys) or canonical_json_bytes(value)!=raw:
        raise ValueError('qualification object shape or canonical bytes differ')
    return value


def validate_native_fixture_proof(
    ref: ArtifactRefV1, *, source: SourceIdentity, job_id: str, attempt_id: str, worker_id: str,
    expected_request_digest: str, store: ArtifactStore, progress: Callable[[], None] | None = None,
) -> tuple[tuple[EngineCommandEnvelope, P1ValidatedResult], ...]:
    """Recompute three exact fixture results; this does not attest their host."""
    progress=progress or (lambda:None)
    source=SourceIdentity.model_validate(source)
    if (any(not isinstance(value,str) or pattern.fullmatch(value) is None
            for value,pattern in ((job_id,_JOB_ID),(attempt_id,_ATTEMPT_ID),(worker_id,_IDENTIFIER)))
        or not isinstance(expected_request_digest,str) or _SHA.fullmatch(expected_request_digest) is None):
        raise ValueError('qualification job binding is invalid')
    proof=_object(_read(ref,store,media_type='application/json',max_bytes=MAX_STREAM_BYTES),
        ('schema_version','source','runs'))
    if (proof['schema_version']!='p3-native-fixture-proof-v1' or proof['source']!=source.model_dump(mode='json')
        or not isinstance(proof['runs'],list) or len(proof['runs'])!=3):
        raise ValueError('native qualification requires the exact source and three runs')
    validated=[]
    identities=set()
    backend_revision=None
    for replica,record in enumerate(proof['runs'],1):
        progress()
        if (not isinstance(record,dict) or set(record)!=set(('replica','request','outcome','result','stdout_ref','stderr_ref'))
            or type(record['replica']) is not int or record['replica']!=replica):
            raise ValueError('native qualification replica identity differs')
        request=EngineCommandEnvelope.model_validate_json(canonical_json_bytes(record['request']))
        if (type(request.payload) is not RunBacktest or request.source_commit!=source.commit_sha
            or request.producer_identity!=worker_id or request.stream_sequence!=1
            or request.event_time!=request.initialization_time
            or (validated and request.event_time!=validated[0][0].event_time)
            or request.payload_digest!=expected_request_digest or payload_digest(request.payload)!=expected_request_digest
            or request.config_digest!=payload_digest({name:getattr(request.payload,name) for name in (
                'engine_configuration','instrument_catalog','strategy_configuration')})
            or any(getattr(request,field)!=uuid5(NAMESPACE_URL,f'p3-fixture:{job_id}:{attempt_id}:{replica}:{purpose}')
                for field,purpose in (('message_id','message'),('correlation_id','correlation'),
                    ('causation_id','causation'),('engine_run_id','run')))):
            raise ValueError('native qualification request attribution differs')
        observed=record['outcome']
        if (not isinstance(observed,dict) or set(observed)!=set(ProcessOutcome.__dataclass_fields__)
            or type(observed['exit_code']) is not int or observed['exit_code']!=0
            or observed['termination_reason'] is not None or observed['safety_reason_code'] is not None
            or observed['p3_output_custody'] is not None or observed['p3_output_inventory_ref'] is not None
            or observed['result_validator_id']!=P1_REAL_BACKTEST_POLICY.result_validator_id
            or not isinstance(observed['capability_fingerprint'],str) or _SHA.fullmatch(observed['capability_fingerprint']) is None
            or not isinstance(observed['backend_revision'],str) or re.fullmatch('[0-9a-f]{40}',observed['backend_revision']) is None):
            raise ValueError('native qualification process outcome is not successful')
        identity=observed['identity']
        if (not isinstance(identity,dict) or set(identity)!=set(ProcessIdentity.__dataclass_fields__)
            or any(type(identity[field]) is not int for field in ('pid','process_group','start_ticks'))
            or identity['pid']!=identity['process_group']
            or not isinstance(identity['command_fingerprint'],str) or _SHA.fullmatch(identity['command_fingerprint']) is None):
            raise ValueError('native qualification process identity differs')
        ProcessIdentity(**identity)
        launch=(identity['pid'],identity['start_ticks'])
        if launch in identities or (backend_revision is not None and observed['backend_revision']!=backend_revision):
            raise ValueError('native qualification requires three distinct launches of one backend revision')
        identities.add(launch)
        backend_revision=observed['backend_revision']
        lineage=observed['lineage']
        if (not isinstance(lineage,dict) or set(lineage)!=set(ProcessLineage.__dataclass_fields__)
            or lineage['command']!=dict(engine_closure_sha256=P1_REAL_BACKTEST_POLICY.closure_sha256,
                os_sandbox_profile_sha256=P1_REAL_BACKTEST_POLICY.sandbox_profile_sha256,
                engine_request_sha256=payload_digest(request))):
            raise ValueError('native qualification pinned closure lineage differs')
        safety_records=[]
        for safety in (lineage['safety_initial'],lineage['safety_final']):
            if (not isinstance(safety,dict) or set(safety)!=set(SafetyEvidence.__dataclass_fields__)
                or safety.get('live_execution_enabled') is not False or safety.get('live_trading_approved') is not False
                or safety.get('requested_mode')!='PAPER' or safety.get('effective_mode')!='PAPER'
                or safety.get('kill_switch_state')!='INACTIVE'
                or any(not isinstance(safety.get(field),str) for field in ('snapshot_sha256','generated_at','expires_at'))):
                raise ValueError('native qualification safety authority differs')
            try:
                evidence=SafetyEvidence(requested_mode=SafetyMode(safety['requested_mode']),
                    effective_mode=SafetyMode(safety['effective_mode']),
                    kill_switch_state=KillSwitchState(safety['kill_switch_state']),
                    live_execution_enabled=safety['live_execution_enabled'],
                    live_trading_approved=safety['live_trading_approved'],snapshot_sha256=safety['snapshot_sha256'],
                    generated_at=datetime.fromisoformat(safety['generated_at']),
                    expires_at=datetime.fromisoformat(safety['expires_at']))
                # Historical shape/window validation is not a fresh safety approval.
                validate_current_safety_evidence(evidence,evidence.generated_at)
                if (any(getattr(evidence,key).astimezone(UTC).isoformat()!=safety[key]
                    for key in ('generated_at','expires_at'))
                    or canonical_json_bytes(_safety_metadata(evidence))!=canonical_json_bytes(safety)):
                    raise ValueError('native safety metadata differs from its producer')
            except (ValueError,TypeError,SafetyBlockedError) as error:
                raise ValueError('native qualification historical safety is invalid') from error
            safety_records.append(evidence)
        if safety_records[1].generated_at<safety_records[0].generated_at:
            raise ValueError('native qualification safety time regressed')
        streams={}
        for name,media in (('stdout','application/jsonl'),('stderr','application/octet-stream')):
            progress()
            artifact=ArtifactRefV1.model_validate(record[name+'_ref'])
            raw=_read(artifact,store,media_type=media,max_bytes=MAX_STREAM_BYTES)
            metadata=observed[name]
            if (not isinstance(metadata,dict) or set(metadata)!=set(ArtifactMetadata.__dataclass_fields__)
                or metadata!=dict(artifact_type=name,relative_ref=f'{job_id}/{attempt_id}/{name}.log',
                    sha256=artifact.content_sha256,size_bytes=len(raw),media_type='application/octet-stream',
                    truncated=False,validator_id='bounded-stream-v1') or type(metadata['truncated']) is not bool
                or type(metadata['size_bytes']) is not int):
                raise ValueError('native qualification stream custody differs')
            streams[name]=raw
            progress()
        events=EngineResultValidator._parse_canonical_batch(streams['stdout'],request,progress,p1_event_stream=True)
        result=validate_p1_result(request,events,raw=streams['stdout'],expected_closure_digest=P1_REAL_BACKTEST_POLICY.closure_sha256)
        metadata={key:str(value) if isinstance(value,Decimal) else value for key,value in asdict(result).items() if key!='events'}
        if canonical_json_bytes(record['result'])!=canonical_json_bytes(metadata) or (validated and result.semantic_sha256!=validated[0][1].semantic_sha256):
            raise ValueError('native qualification result differs from retained stream recomputation')
        validated.append((request,result))
        progress()
    return tuple(validated)


def validate_integration_receipt(
    ref: ArtifactRefV1, *, fixture_payload: AlphaCampaignPayload, source: SourceIdentity,
    job_id: str, attempt_id: str, worker_id: str, postgres_binary_sha256: str,
    store: ArtifactStore, progress: Callable[[], None] | None = None,
) -> IntegrationReceipt:
    """Recompute retained proof closure; caller authenticates the job/profile binding.

    Hash-valid CAS bytes and historical reviews alone never grant host authority.
    """
    from packages.alpha_lifecycle.authority import _FixtureAuthorization
    from packages.alpha_lifecycle.contracts.authority import IntegrationReceipt,ReviewApproval
    from packages.job_contracts import AlphaCampaignOperation,AlphaCampaignPayload
    from .p3_integration import FixturePlan,ROOT
    from .p3_fixture_sql import validate_sql_fixture_checks
    from .p3_fixture_native import native_fixture_definition

    progress=progress or (lambda:None)
    source=SourceIdentity.model_validate(source)
    if (type(fixture_payload) is not AlphaCampaignPayload
        or fixture_payload.operation is not AlphaCampaignOperation.PARITY
        or fixture_payload.logical_trial_id!='p3-integration-fixture-v1'
        or fixture_payload.expected_source!=source
        or not isinstance(postgres_binary_sha256,str) or _SHA.fullmatch(postgres_binary_sha256) is None):
        raise ValueError('qualification requires the exact fixture payload and protected PostgreSQL identity')

    def model(reference,contract):
        progress()
        raw=_read(reference,store,media_type='application/json',max_bytes=65536)
        result=contract.model_validate_json(raw)
        if canonical_json_bytes(result)!=raw:
            raise ValueError('qualification typed artifact is not canonical')
        return result

    receipt=model(ref,IntegrationReceipt)
    plan=model(fixture_payload.manifest_ref,FixturePlan)
    if plan.native_request_digest!=payload_digest(native_fixture_definition()[0]):
        raise ValueError('qualification plan differs from the fixed fixture command')
    authorization=model(fixture_payload.authorization_ref,_FixtureAuthorization)
    review=model(authorization.review_ref,ReviewApproval)
    if (receipt.source!=source or plan.source!=source or review.source!=source
        or authorization.fixture_plan_ref!=fixture_payload.manifest_ref
        or authorization.issuer_workflow!='p3-authority.yml'
        or (receipt.workflow_run_id,receipt.workflow_attempt)!=(authorization.issuer_run_id,authorization.issuer_attempt)
        or review.verdict!='APPROVED' or review.operator_identity==review.reviewer_identity
        or not review.issued_at<=authorization.issued_at<authorization.expires_at<=review.expires_at
        or plan.digest not in review.subject_digests
        or plan.policy_set_sha256!=hashlib.sha256((ROOT/'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes()).hexdigest()):
        raise ValueError('qualification source, workflow, policy or review binding differs')
    _read(review.evidence_ref,store,media_type=review.evidence_ref.media_type,max_bytes=64*1024*1024)
    native=validate_native_fixture_proof(receipt.native_fixture_proof_ref,source=source,
        job_id=job_id,attempt_id=attempt_id,worker_id=worker_id,
        expected_request_digest=plan.native_request_digest,store=store,progress=progress)
    progress()
    sql=_object(_read(receipt.sql_proof_ref,store,media_type='application/json',max_bytes=65536),
        ('source','checks','sql_revision','postgres_binary_sha256','started_at','finished_at','cleanup'))
    if (sql['source']!=source.model_dump(mode='json') or sql['sql_revision']!=plan.sql_revision
        or sql['postgres_binary_sha256']!=postgres_binary_sha256):
        raise ValueError('qualification SQL source, binary or required checks differ')
    validate_sql_fixture_checks(sql['checks'])
    instants=[]
    for key in ('started_at','finished_at'):
        value=sql[key]
        if not isinstance(value,str):
            raise ValueError('qualification SQL timestamp must be UTC')
        instant=datetime.fromisoformat(value)
        if instant.tzinfo is None or instant.astimezone(UTC).isoformat()!=value:
            raise ValueError('qualification SQL timestamp must match its UTC producer')
        instants.append(instant)
    began=native[0][0].event_time
    if (not began<=instants[0]<=instants[1]
        or not authorization.issued_at<=began<=instants[1]<authorization.expires_at
        or not review.issued_at<=began<=instants[1]<review.expires_at):
        raise ValueError('qualification execution is outside historical authority or review')
    expected_cleanup=dict(cluster_id='sql-'+hashlib.sha256(f'{job_id}/{attempt_id}'.encode()).hexdigest()[:32],
        root_absent=True,server_stopped=True)
    if canonical_json_bytes(sql['cleanup'])!=canonical_json_bytes(expected_cleanup):
        raise ValueError('qualification SQL cleanup is incomplete or belongs to another attempt')
    cleanup=_object(_read(receipt.cleanup_proof_ref,store,media_type='application/json',max_bytes=65536),
        ('schema_version','source','native_root_absent','native_process_cleanup','sql_cleanup'))
    if canonical_json_bytes(cleanup)!=canonical_json_bytes(dict(schema_version='p3-fixture-cleanup-v1',source=source,
        native_root_absent=True,native_process_cleanup=True,sql_cleanup=expected_cleanup)):
        raise ValueError('qualification cleanup proof differs from SQL/native custody')
    progress()
    return receipt


def validate_integration_job_result(
    ref: ArtifactRefV1, *, job_detail_ref: ArtifactRefV1, source: SourceIdentity,
    postgres_binary_sha256: str, store: ArtifactStore, progress: Callable[[], None] | None = None,
) -> IntegrationReceipt:
    """Bind retained Job API observation to its fixture result; root provenance is external."""
    from datetime import timedelta
    from apps.job_api.contracts import JobDetailEnvelope
    from packages.job_contracts import ActorType,JobType,JobState,AlphaCampaignPayload,payload_fingerprint
    from packages.alpha_lifecycle.authority import _FixtureAuthorization
    from packages.alpha_lifecycle.contracts.authority import ReviewApproval

    progress=progress or (lambda:None)
    progress()
    ref=ArtifactRefV1.model_validate(ref)
    raw=_read(job_detail_ref,store,media_type='application/json',max_bytes=MAX_STREAM_BYTES)
    envelope=JobDetailEnvelope.model_validate_json(raw)
    if canonical_json_bytes(envelope)!=raw:
        raise ValueError('qualification Job API observation is not canonical')
    detail=envelope.data;job=detail.job
    if (job.job_type is not JobType.ALPHA_CAMPAIGN or type(job.payload) is not AlphaCampaignPayload
        or job.state is not JobState.SUCCEEDED or job.priority!=0 or job.actor.actor_type is not ActorType.OPERATOR
        or job.reason_code!='RESULT_VALIDATED' or job.result_hash!=ref.content_sha256
        or job.payload_fingerprint!=payload_fingerprint(job.payload)
        or job.attempt_count!=1 or len(detail.attempts)!=1
        or detail.attempts[0].attempt_number!=1
        or _ATTEMPT_ID.fullmatch(detail.attempts[0].attempt_id) is None):
        raise ValueError('qualification Job API job or complete attempts differ')
    attempt=detail.attempts[-1]
    if (attempt.worker_id is None or attempt.exit_code is not None or attempt.termination_reason is not None
        or attempt.claimed_at is None or attempt.finished_at is None or attempt.started_at is None):
        raise ValueError('qualification requires a completed parent fixture attempt')
    times=[envelope.generated_at,job.requested_at,job.updated_at]
    times.extend(value for item in detail.attempts for value in (item.claimed_at,item.started_at,item.finished_at) if value is not None)
    times.extend(item.created_at for item in (*detail.artifacts,*detail.events))
    if any(value.tzinfo is None or value.utcoffset()!=timedelta(0) for value in times):
        raise ValueError('qualification Job API times must be UTC')
    if not job.requested_at<=attempt.claimed_at<=attempt.started_at<=attempt.finished_at<=job.updated_at<=envelope.generated_at:
        raise ValueError('qualification Job API attempt times differ')
    artifacts=detail.artifacts
    if (len(artifacts)!=1 or attempt.artifact_count!=1 or artifacts[0].attempt_id!=attempt.attempt_id
        or artifacts[0].artifact_type!='result'
        or artifacts[0].validator_id!='p3-integration-qualified-v1'
        or (artifacts[0].sha256,artifacts[0].size_bytes)!=(ref.content_sha256,ref.size_bytes)
        or not attempt.started_at<=artifacts[0].created_at<=attempt.finished_at):
        raise ValueError('qualification Job API result artifact differs')
    expected_events=((None,JobState.QUEUED,'ENQUEUED',job.actor.actor_type,job.actor.actor_id),
        (JobState.QUEUED,JobState.CLAIMED,'CLAIMED',ActorType.WORKER,attempt.worker_id),
        (JobState.CLAIMED,JobState.RUNNING,'STARTED',ActorType.WORKER,attempt.worker_id),
        (JobState.RUNNING,JobState.SUCCEEDED,'RESULT_VALIDATED',ActorType.WORKER,attempt.worker_id))
    if (len(detail.events)!=4 or len({event.event_id for event in detail.events})!=4
        or any(left.created_at>right.created_at for left,right in zip(detail.events,detail.events[1:]))):
        raise ValueError('qualification Job API event history differs')
    for sequence,(event,expected) in enumerate(zip(detail.events,expected_events,strict=True),1):
        progress()
        # This single-attempt workflow serializes transactions; event and row clocks still differ.
        if (event.sequence!=sequence
            or (event.from_state,event.to_state,event.reason_code,event.actor.actor_type,event.actor.actor_id)!=expected
            or not job.requested_at<=event.created_at<=job.updated_at):
            raise ValueError('qualification Job API event history differs')
    if not attempt.started_at<=detail.events[-1].created_at<=attempt.finished_at:
        raise ValueError('qualification Job API terminal event differs')
    receipt=validate_integration_receipt(ref,fixture_payload=job.payload,source=source,job_id=job.job_id,
        attempt_id=attempt.attempt_id,worker_id=attempt.worker_id,postgres_binary_sha256=postgres_binary_sha256,
        store=store,progress=progress)
    authorization=_FixtureAuthorization.model_validate_json(_read(job.payload.authorization_ref,store,
        media_type='application/json',max_bytes=65536))
    review=ReviewApproval.model_validate_json(_read(authorization.review_ref,store,media_type='application/json',max_bytes=65536))
    sql=json.loads(_read(receipt.sql_proof_ref,store,media_type='application/json',max_bytes=65536))
    native=json.loads(_read(receipt.native_fixture_proof_ref,store,media_type='application/json',max_bytes=MAX_STREAM_BYTES))
    began=datetime.fromisoformat(native['runs'][0]['request']['event_time'])
    if (review.operator_identity!=job.actor.actor_id
        or not authorization.issued_at<=job.requested_at<=attempt.claimed_at<=began<=attempt.finished_at<authorization.expires_at
        or not review.issued_at<=job.requested_at<=attempt.finished_at<review.expires_at
        or not attempt.started_at<=datetime.fromisoformat(sql['started_at'])<=datetime.fromisoformat(sql['finished_at'])
            <=detail.events[-1].created_at<=attempt.finished_at
        or datetime.fromisoformat(sql['finished_at'])>artifacts[0].created_at):
        raise ValueError('qualification Job API execution is outside the retained authority/proof interval')
    progress()
    return receipt

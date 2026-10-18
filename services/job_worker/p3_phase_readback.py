"""Export committed phase decisions without acquiring a view or executing a job."""
from datetime import datetime, UTC
import hashlib

from packages.alpha_lifecycle.contracts.authority import RUN_AUTHORIZATION,LocalRunAuthorization,GithubRunIdentity
from packages.alpha_lifecycle.contracts.execution import InputSet,EnvironmentIdentity
from packages.alpha_lifecycle.contracts.lifecycle import PublicationRequest,JobCommitResult,PrePublicationEvidence,ExpectedHead
from packages.alpha_lifecycle.contracts.results import ExitResult
from packages.alpha_lifecycle.lifecycle import read_registry_event
from packages.alpha_lifecycle.operation_input import P3OperationInput,PhaseExitInput
from packages.alpha_lifecycle.publication import build_publication_receipt,build_closure_report
from packages.alpha_lifecycle.replica_store import _read
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import AlphaCampaignPayload
from packages.p3_provenance import PhaseExitReceiptV2
from packages.data_contracts import ArtifactRefV1
from .p3_qualification_readback import _read as bounded_read,_object


def reconstruct_phase_exit(raw: bytes, *, store) -> PhaseExitReceiptV2:
    """Validate retained SQL observation; caller must authenticate the observation."""
    observation=_object(raw,('payload','publication_request','commit_result','committed_at',
        'attempt_id','output_inventory_ref','registry_head'))
    payload=AlphaCampaignPayload.model_validate_json(canonical_json_bytes(observation['payload']))
    request=PublicationRequest.model_validate_json(canonical_json_bytes(observation['publication_request']))
    commit=JobCommitResult.model_validate_json(canonical_json_bytes(observation['commit_result'])).bound_to(request)
    head=ExpectedHead.model_validate_json(canonical_json_bytes(observation['registry_head']))
    intent=_read(store,payload.manifest_ref,P3OperationInput)
    auth=_read(store,payload.authorization_ref,RUN_AUTHORIZATION)
    evidence=_read(store,request.evidence_ref,PrePublicationEvidence)
    inputs=_read(store,intent.input_set_ref,InputSet)
    if (payload.operation!='PHASE_EXIT' or payload.logical_trial_id!='p3-phase-exit-v1'
        or not isinstance(intent.body,PhaseExitInput) or request.stage!='EXIT_DECISION'
        or evidence.input_set_ref!=intent.input_set_ref or auth.input_set_ref!=intent.input_set_ref
        or auth.operation!='PHASE_EXIT' or auth.allowed_alpha_ids!=intent.allowed_alpha_ids
        or inputs.source!=payload.expected_source or evidence.exit_result_ref is None
        or evidence.qualification_bundle_ref!=intent.body.primary_qualification_ref
        or evidence.baseline_selection_ref!=intent.body.baseline_selection_ref
        or len(commit.registry_event_refs)!=1):
        raise ValueError('phase exit source, operation or publication differs')
    result=_read(store,evidence.exit_result_ref,ExitResult)
    event=read_registry_event(store,commit.registry_event_refs[0])
    for name in ('primary_selection_ref','holdout_request_ref','holdout_evaluation_ref','holdout_replay_ref',
        'executable_ref','baseline_executable_ref','parity_ref'):
        if getattr(result,name)!=getattr(intent.body,name):
            raise ValueError('phase exit retained result differs from approved inputs')
    if (result.verdict not in {'PASS','FAIL'} or result.verdict!=commit.alpha_outcome
        or event.record.qualification_decision.value!=result.verdict
        or event.record.lifecycle_status.value!=('QUALIFIED' if result.verdict=='PASS' else 'REJECTED')
        or event.record.source_sha!=inputs.source.commit_sha
        or event.record.robustness_sha256!=evidence.exit_result_ref.content_sha256
        or (head.alpha_id,head.version,head.sequence,head.event_digest)!=
            (event.record.alpha_id,event.record.version,event.sequence,event.event_sha256)
        or event.predecessor_sha256!=intent.body.current_primary_head_ref.content_sha256):
        raise ValueError('phase exit result or current registry head differs')
    from .process_runner import _ATTEMPT_ID
    if not isinstance(observation['attempt_id'],str) or _ATTEMPT_ID.fullmatch(observation['attempt_id']) is None:
        raise ValueError('phase exit attempt identity differs')
    committed_at=datetime.fromisoformat(observation['committed_at'])
    if committed_at.tzinfo is not UTC or not auth.issued_at<=committed_at<auth.expires_at:
        raise ValueError('phase exit commit is outside operation authority')
    from packages.alpha_lifecycle.authority import validate_operation_review
    review=validate_operation_review(auth,inputs.source,intent,store,now=committed_at)
    bounded_read(review.evidence_ref,store,media_type=review.evidence_ref.media_type,max_bytes=67108864)
    _read(store,inputs.environment_ref,EnvironmentIdentity)
    for name in ('primary_selection_ref','holdout_request_ref','holdout_evaluation_ref','holdout_replay_ref',
        'executable_ref','baseline_executable_ref','parity_ref'):
        bounded_read(getattr(result,name),store,media_type='application/json',max_bytes=67108864)
    # SQL binds the complete inventory to this exact committed attempt. Reuse its owner.
    from services.job_store.p3_publication_repository import validate_publication_inventory
    validate_publication_inventory(ArtifactRefV1.model_validate(observation['output_inventory_ref']),store=store)
    def put(value):return store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    publication=build_publication_receipt(put(request),commit,committed_at=committed_at,store=store)
    closure=build_closure_report(request,request.evidence_ref,publication,store=store)
    identity=(auth.identity if isinstance(auth,LocalRunAuthorization) else GithubRunIdentity(
        issuer_kind='GITHUB',issuer_identity='p3-authority',repository='nam176hermes/Trading-Agent',
        workflow_ref='p3-authority.yml',run_id=auth.issuer_run_id,attempt=auth.issuer_attempt))
    value=dict(schema_version='p3-phase-exit-v2',source=inputs.source,identity=identity,
        policy_digest=inputs.policy_digest,input_ref=intent.input_set_ref,environment_ref=inputs.environment_ref,
        job_id=request.job_id,attempt_id=observation['attempt_id'],authorization_ref=payload.authorization_ref,
        terminal_observation_ref=put(observation),publication_ref=put(publication),closure_report_ref=put(closure),
        result_ref=evidence.exit_result_ref,registry_head_ref=commit.registry_event_refs[0],outcome=result.verdict,
        authority=dict(broker=False,live=False,network=False,production=False))
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return PhaseExitReceiptV2.model_validate_json(canonical_json_bytes(value))


def export_phase_exit(job_id: str, *, repository, store) -> ArtifactRefV1:
    raw=repository.read_phase_exit(job_id)
    receipt=reconstruct_phase_exit(raw,store=store)
    if receipt.job_id!=job_id or repository.read_phase_exit(job_id)!=raw:
        raise ValueError('phase exit SQL observation changed during export')
    return store.put_bytes(canonical_json_bytes(receipt),media_type='application/json')


def validate_phase_exit_receipt(ref: ArtifactRefV1, *, repository, store) -> PhaseExitReceiptV2:
    receipt=_read(store,ref,PhaseExitReceiptV2)
    if receipt.outcome in {'NONE_QUALIFIED','HELD'}:
        if terminal_phase_observation(receipt.terminal_observation_ref,store=store)!=receipt:
            raise ValueError('terminal phase observation differs from its retained outcome')
        return receipt
    raw=bounded_read(receipt.terminal_observation_ref,store,media_type='application/json',max_bytes=131072)
    if (repository.read_phase_exit(receipt.job_id)!=raw
        or reconstruct_phase_exit(raw,store=store)!=receipt
        or repository.read_phase_exit(receipt.job_id)!=raw):
        raise ValueError('phase exit receipt is not its current committed SQL decision')
    return receipt


def terminal_phase_observation(job_detail_ref: ArtifactRefV1, *, store) -> PhaseExitReceiptV2:
    """Retain NONE_QUALIFIED/HELD without inventing a publication or a disclosure."""
    from apps.job_api.contracts import JobDetailEnvelope
    from packages.alpha_lifecycle.contracts.authority import PrimarySelection,FamilyReview
    from packages.alpha_lifecycle.primary_selection import select_primary
    from packages.alpha_lifecycle.operation_input import SelectPrimaryInput
    from packages.job_contracts import payload_fingerprint
    raw=bounded_read(job_detail_ref,store,media_type='application/json',max_bytes=1048576)
    envelope=JobDetailEnvelope.model_validate_json(raw)
    detail=envelope.data;job=detail.job
    if (canonical_json_bytes(envelope)!=raw or type(job.payload) is not AlphaCampaignPayload
        or job.payload_fingerprint!=payload_fingerprint(job.payload) or job.actor.actor_type!='OPERATOR'
        or job.attempt_count!=1 or len(detail.attempts)!=1 or detail.attempts[0].attempt_number!=1):
        raise ValueError('terminal phase observation has no exact single attempt')
    attempt=detail.attempts[0]
    intent=_read(store,job.payload.manifest_ref,P3OperationInput)
    auth=_read(store,job.payload.authorization_ref,RUN_AUTHORIZATION)
    inputs=_read(store,intent.input_set_ref,InputSet)
    if (inputs.source!=job.payload.expected_source or auth.input_set_ref!=intent.input_set_ref
        or auth.operation!=job.payload.operation or auth.allowed_alpha_ids!=intent.allowed_alpha_ids
        or job.payload.logical_trial_id!=intent.workflow_operation
        or attempt.finished_at is None or attempt.claimed_at is None
        or not job.requested_at<=attempt.claimed_at<=attempt.finished_at<=job.updated_at<=envelope.generated_at
        or not auth.issued_at<=job.requested_at<auth.expires_at):
        raise ValueError('terminal phase identity or attempt interval differs')
    result_ref=None;outcome='HELD'
    if job.state=='SUCCEEDED':
        if (not isinstance(intent.body,SelectPrimaryInput) or job.reason_code!='RESULT_VALIDATED'
            or len(detail.artifacts)!=1 or attempt.artifact_count!=1):
            raise ValueError('non-publication completion must be the primary selection')
        from packages.alpha_lifecycle.authority import validate_operation_review
        review=validate_operation_review(auth,inputs.source,intent,store,now=attempt.finished_at)
        if review.operator_identity!=job.actor.actor_id:
            raise ValueError('primary selection operator differs from approved review')
        bounded_read(review.evidence_ref,store,media_type=review.evidence_ref.media_type,max_bytes=67108864)
        artifact=detail.artifacts[0]
        result_ref=ArtifactRefV1(content_sha256=artifact.sha256,size_bytes=artifact.size_bytes,
            media_type='application/json',locator=artifact.sha256+'.blob')
        if (artifact.attempt_id!=attempt.attempt_id or artifact.artifact_type!='result'
            or artifact.validator_id!='p3-primary-selection-v1' or job.result_hash!=artifact.sha256
            or attempt.started_at is None or not attempt.started_at<=artifact.created_at<=attempt.finished_at):
            raise ValueError('primary selection result differs from its terminal job')
        result=_read(store,result_ref,PrimarySelection)
        family=_read(store,result.family_review_ref,FamilyReview)
        if (result.outcome!='NONE_QUALIFIED' or family.input_set_ref!=intent.input_set_ref
            or result.family_review_ref!=intent.body.family_review_ref or select_primary(family,store)!=result):
            raise ValueError('terminal primary selection is not the retained NONE_QUALIFIED decision')
        outcome='NONE_QUALIFIED'
    elif job.state not in {'BLOCKED','FAILED','CANCELLED'} or job.result_hash is not None:
        raise ValueError('non-publication terminal state must remain HELD')
    identity=(auth.identity if isinstance(auth,LocalRunAuthorization) else GithubRunIdentity(
        issuer_kind='GITHUB',issuer_identity='p3-authority',repository='nam176hermes/Trading-Agent',
        workflow_ref='p3-authority.yml',run_id=auth.issuer_run_id,attempt=auth.issuer_attempt))
    value=dict(schema_version='p3-phase-exit-v2',source=inputs.source,identity=identity,
        policy_digest=inputs.policy_digest,input_ref=intent.input_set_ref,environment_ref=inputs.environment_ref,
        job_id=job.job_id,attempt_id=attempt.attempt_id,authorization_ref=job.payload.authorization_ref,
        terminal_observation_ref=job_detail_ref,publication_ref=None,closure_report_ref=None,
        result_ref=result_ref,registry_head_ref=None,outcome=outcome,
        authority=dict(broker=False,live=False,network=False,production=False))
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return PhaseExitReceiptV2.model_validate_json(canonical_json_bytes(value))


def phase_exit_v1(ref: ArtifactRefV1, *, repository, store, run, research):
    """Bridge an authenticated committed GITHUB PASS to the tracked v1 receipt.

    `run` and `research` are the protected attestation of the exporting run and its
    reviewed research-input receipt. A recorded-API run, a REHEARSAL InputSet, a LOCAL
    issuer, another run or any non-PASS outcome produces no v1 receipt at all.
    """
    from packages.p3_provenance import PhaseExitReceipt
    if run.mode != 'GITHUB' or research.campaign != 'OFFICIAL':
        raise ValueError('only an official, API-attested campaign can export a v1 phase receipt')
    receipt=validate_phase_exit_receipt(ref,repository=repository,store=store)
    if receipt.outcome!='PASS':
        raise ValueError('only a committed PASS has a v1 phase receipt')
    if type(receipt.identity) is not GithubRunIdentity or receipt.identity!=run.identity:
        raise ValueError('phase receipt was not issued by this attested GitHub run')
    if receipt.input_ref!=research.input_ref or receipt.source!=research.source:
        raise ValueError('phase receipt does not bind the official research inputs')
    raw=bounded_read(receipt.terminal_observation_ref,store,media_type='application/json',max_bytes=131072)
    observation=_object(raw,('payload','publication_request','commit_result','committed_at',
        'attempt_id','output_inventory_ref','registry_head'))
    payload=AlphaCampaignPayload.model_validate_json(canonical_json_bytes(observation['payload']))
    intent=_read(store,payload.manifest_ref,P3OperationInput)
    inputs=_read(store,receipt.input_ref,InputSet)
    inventory=ArtifactRefV1.model_validate(observation['output_inventory_ref'])
    from services.job_store.p3_publication_repository import validate_publication_inventory
    validate_publication_inventory(inventory,store=store)
    if receipt.registry_head_ref is None or receipt.closure_report_ref is None:
        raise ValueError('committed PASS lacks its registry head or closure report')
    event=read_registry_event(store,receipt.registry_head_ref)
    if (not isinstance(intent.body,PhaseExitInput)
        or event.record.lifecycle_status.value!='QUALIFIED'
        or event.record.qualification_decision.value!='PASS'
        or repository.read_phase_exit(receipt.job_id)!=raw):
        raise ValueError('phase receipt registry head, intent or SQL observation differs')
    value=dict(schema_version='p3-phase-exit-v1',source=receipt.source,epoch_id=inputs.epoch_id,
        primary_selection_ref=intent.body.primary_selection_ref,exit_result_ref=receipt.result_ref,
        qualified_registry_head_ref=receipt.registry_head_ref,closure_report_ref=receipt.closure_report_ref,
        bundle_inventory_ref=inventory,qualification_workflow='p3-authority.yml',
        qualification_run_id=run.identity.run_id,qualification_attempt=run.identity.attempt,status='PASS',
        authority=dict(broker=False,live=False,network=False,production=False))
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return PhaseExitReceipt.model_validate_json(canonical_json_bytes(value))

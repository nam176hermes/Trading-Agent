"""Reconstruct supported operations from the claimed inputs and this attempt's outputs."""
from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_EVEN,localcontext

from packages.alpha_lifecycle.baseline_campaign import ReadbackStore,_read,run_baseline_pack,validate_baseline_selection,baseline_manifest_ref
from packages.alpha_lifecycle.contracts.authority import RunAuthorization
from packages.alpha_lifecycle.contracts.execution import BaselineManifest
from packages.alpha_lifecycle.contracts.results import BaselinePack,BaselineSelection,ReplayProof,ReplayReceipt
from packages.alpha_lifecycle.operation_input import P3OperationInput,BaselinesInput,RegisterFamilyInput
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import AlphaCampaignPayload,JobType
from services.job_store.records import ClaimedJob
from .p3_output import P3OutputCustody
from .p3_publication_producer import prepare_family_registration

_REF_FIELDS={'content_sha256','size_bytes','media_type','locator'}
_MAX_REFS=8192
_MAX_BYTES=1073741824


def _key(ref):
    return canonical_json_bytes(ArtifactRefV1.model_validate(ref))


def _reference(raw):
    digest=hashlib.sha256(raw).hexdigest()
    return ArtifactRefV1(content_sha256=digest,size_bytes=len(raw),media_type='application/json',locator=digest+'.blob')


class _ClosedReader:
    def __init__(self, store, references):
        self._store=store
        self._references={_key(ref) for ref in references}

    def read_bytes(self, ref: ArtifactRefV1) -> bytes:
        if _key(ref) not in self._references:
            raise ValueError('P3 result references an artifact outside its input/output closure')
        return self._store.read_bytes(ref)

    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
        ref=_reference(value)
        if media_type != ref.media_type or self.read_bytes(ref) != value:
            raise ValueError('P3 recomputation differs from retained output')
        return ref


def _closure(initial, store, *, output_refs=()):
    pending={_key(ref):ref for ref in initial}
    found={}
    output_inventory_refs={ref.content_sha256:ref for ref in output_refs}
    output_keys={_key(ref) for ref in output_refs}
    total=0
    while pending:
        _,next_ref=pending.popitem()
        ref=ArtifactRefV1.model_validate(next_ref)
        key=_key(ref)
        if key in found:
            continue
        total+=ref.size_bytes
        if len(found) >= _MAX_REFS or total > _MAX_BYTES:
            raise ValueError('P3 result closure exceeds its bound')
        raw=store.read_bytes(ref)
        found[key]=ref
        if ref.media_type != 'application/json':
            continue
        value=json.loads(raw)
        if canonical_json_bytes(value) != raw:
            raise ValueError('P3 input/output closure contains noncanonical JSON')
        if key in output_keys and isinstance(value,dict) and value.get('schema_version') == 'p3-replay-receipt-v1':
            inventory=output_inventory_refs.get(value['output_inventory_digest'])
            if inventory is None:
                raise ValueError('P3 replay inventory is absent from this attempt')
            if _key(inventory) not in found:
                pending[_key(inventory)]=inventory
        values=[(value,0)]
        while values:
            item,depth=values.pop()
            if depth > 64:
                raise ValueError('P3 reference nesting exceeds its bound')
            if isinstance(item,dict):
                if set(item) == _REF_FIELDS:
                    child_ref=ArtifactRefV1.model_validate(item)
                    child_key=_key(child_ref)
                    if child_key not in found and child_key not in pending:
                        if len(pending)+len(found) >= _MAX_REFS:
                            raise ValueError('P3 reference inventory exceeds its bound')
                        pending[child_key]=child_ref
                else:
                    values.extend((child,depth+1) for child in item.values())
            elif isinstance(item,list):
                values.extend((child,depth+1) for child in item)
    return found


def validate_official_output(job, result, custody: P3OutputCustody, inventory_ref: ArtifactRefV1):
    if (type(job) is not ClaimedJob or job.job_type is not JobType.ALPHA_CAMPAIGN
        or type(job.payload) is not AlphaCampaignPayload or type(custody) is not P3OutputCustody
        or not custody.matches(job.job_id,job.attempt_id) or inventory_ref != custody.inventory_ref):
        raise ValueError('P3 output custody does not belong to this claimed operation')
    store=custody._store
    inventory=custody.inventory
    output_refs=tuple(ref for path,ref in inventory.items() if path.startswith('artifacts/'))
    result_ref=_reference(canonical_json_bytes(result))
    if result_ref not in output_refs:
        raise ValueError('P3 terminal result is absent from this attempt output')
    intent=_read(store,job.payload.manifest_ref,P3OperationInput)
    if not isinstance(intent.body,(BaselinesInput,RegisterFamilyInput)):
        raise ValueError('HELD E_OPERATION: complete output validation is unavailable')
    inputs=_closure((job.payload.manifest_ref,job.payload.authorization_ref,baseline_manifest_ref(intent.input_set_ref)),store)
    reader=_ClosedReader(store,(*inputs.values(),*output_refs))
    outputs=_ClosedReader(store,output_refs)
    intent=_read(reader,job.payload.manifest_ref,P3OperationInput)
    authorization=_read(reader,job.payload.authorization_ref,RunAuthorization)
    from packages.alpha_lifecycle.authority import stage_alpha_campaign_payload
    expected_payload=stage_alpha_campaign_payload(ReadbackStore(reader,reader),authorization,
        job.payload.expected_source,job.payload.logical_trial_id,canonical_json_bytes(intent),operation_input=intent)
    if expected_payload != job.payload:
        raise ValueError('P3 result differs from claimed input or retained authorization')
    with localcontext() as context:
        context.prec=50
        context.rounding=ROUND_HALF_EVEN
        if isinstance(intent.body,BaselinesInput) and isinstance(result,BaselineSelection):
            manifest=_read(reader,intent.body.baseline_manifest_ref,BaselineManifest)
            if manifest.input_set_ref != intent.input_set_ref:
                raise ValueError('P3 baseline manifest belongs to another InputSet')
            pack=run_baseline_pack(manifest,ReadbackStore(reader,outputs))
            if pack != _read(outputs,result.pack_ref,BaselinePack):
                raise ValueError('P3 baseline result differs from frozen parent recomputation')
            if validate_baseline_selection(result_ref,intent.input_set_ref,reader) != result:
                raise ValueError('P3 baseline selection differs from this attempt')
            proof=_read(outputs,result.baseline_replay_proof_ref,ReplayProof)
            expected_paths={path for path in inventory if path.startswith('artifacts/')}
            for replica,receipt_ref in zip(('r1','r2','r3'),proof.receipt_refs,strict=True):
                receipt=_read(outputs,receipt_ref,ReplayReceipt)
                if receipt.logical_trial_id != job.payload.logical_trial_id:
                    raise ValueError('P3 replay belongs to another operation')
                if (inventory.get(replica+'/result.json') != result.pack_ref
                    or inventory.get(replica+'/manifest-ref.json') != _reference(canonical_json_bytes(intent.body.baseline_manifest_ref))):
                    raise ValueError('P3 replica files do not prove this baseline attempt')
                expected_paths.update((replica+'/result.json',replica+'/manifest-ref.json'))
                replica_inventory_ref=next((ref for ref in output_refs if ref.content_sha256 == receipt.output_inventory_digest),None)
                if replica_inventory_ref is None:
                    raise ValueError('P3 replica inventory is missing')
                physical={path:ref for path,ref in inventory.items() if path.startswith(replica+'/artifacts/')}
                expected_inventory=canonical_json_bytes(sorted((result.pack_ref,*physical.values()),key=lambda ref:ref.locator))
                if (outputs.read_bytes(replica_inventory_ref) != expected_inventory
                    or replica_inventory_ref != _reference(expected_inventory)):
                    raise ValueError('P3 replica inventory differs from exact physical output')
                expected_paths.update(physical)
            if set(inventory) != expected_paths:
                raise ValueError('P3 baseline output contains unexpected replica files')
        elif isinstance(intent.body,RegisterFamilyInput):
            if any(not path.startswith('artifacts/') for path in inventory):
                raise ValueError('P3 registration must not produce replica files')
            expected=prepare_family_registration(intent,job_id=job.job_id,
                observed_at=authorization.issued_at,expires_at=authorization.expires_at,store=ReadbackStore(reader,outputs))
            if result != expected:
                raise ValueError('P3 registration differs from the complete frozen family')
        else:
            raise ValueError('HELD E_OPERATION: complete output validation is unavailable')
    reachable=_closure((result_ref,),reader,output_refs=output_refs)
    if any(_key(ref) not in reachable for ref in output_refs):
        raise ValueError('P3 attempt contains artifacts outside the operation result closure')

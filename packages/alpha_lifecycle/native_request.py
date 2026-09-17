"""Private P3 native inputs and output normalization; no launch authority.

Requests contain protected prices and remain in memory/sealed transport, never CAS.
Only an authenticated parent launch can establish that output came from the engine.
"""
import hashlib
from typing import Annotated,Literal

from pydantic import BeforeValidator,Field,TypeAdapter

from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from .contracts.base import DigestModel,SourceIdentity,Sha256
from .contracts.models import json_array
from .contracts.execution import HoldoutManifest,InstrumentSpec
from .contracts.results import ExecutableResult
from .executable_reference import NextOpenStep,next_open_steps,executable_result_from_rows
from .holdout_view import HoldoutCalculationView
from .replica_store import ArtifactStore,_read


NativeRole=Literal['PRIMARY','SELECTED_BASELINE']


class NativeRequest(DigestModel):
    schema_version: Literal['p3-native-request-v1']
    role: NativeRole
    source: SourceIdentity
    environment_ref: ArtifactRefV1
    manifest_ref: ArtifactRefV1
    instrument_spec_ref: ArtifactRefV1
    policy_digest: Sha256
    transition_sha256: Sha256
    steps: Annotated[tuple[NextOpenStep,...],BeforeValidator(json_array),Field(min_length=366,max_length=366)]


class NativeCommitment(DigestModel):
    """Retained review subject for both roles; contains no protected prices."""

    schema_version: Literal['p3-native-commitment-v1']
    source: SourceIdentity
    environment_ref: ArtifactRefV1
    manifest_ref: ArtifactRefV1
    instrument_spec_ref: ArtifactRefV1
    primary_request_sha256: Sha256
    baseline_request_sha256: Sha256


def prepare_native_commitment(manifest_ref: ArtifactRefV1, spec_ref: ArtifactRefV1,
    view: HoldoutCalculationView,
) -> NativeCommitment:
    primary = prepare_native_request(manifest_ref, spec_ref, view, role='PRIMARY')
    baseline = prepare_native_request(manifest_ref, spec_ref, view, role='SELECTED_BASELINE')
    payload = dict(schema_version='p3-native-commitment-v1', source=primary.source,
        environment_ref=primary.environment_ref, manifest_ref=manifest_ref, instrument_spec_ref=spec_ref,
        primary_request_sha256=hashlib.sha256(canonical_json_bytes(primary)).hexdigest(),
        baseline_request_sha256=hashlib.sha256(canonical_json_bytes(baseline)).hexdigest())
    return NativeCommitment.model_validate({**payload, 'digest':hashlib.sha256(canonical_json_bytes(payload)).hexdigest()})


def validate_native_commitment(reference: ArtifactRefV1, manifest_ref: ArtifactRefV1,
    spec_ref: ArtifactRefV1, view: HoldoutCalculationView, store: ArtifactStore,
) -> NativeCommitment:
    from .pit_evidence import _reference
    _reference(reference, 4096)
    actual = _read(store, reference, NativeCommitment)
    if actual != prepare_native_commitment(manifest_ref, spec_ref, view):
        raise ValueError('native commitment differs from the released view or ordered roles')
    return actual


def prepare_native_request(manifest_ref: ArtifactRefV1,spec_ref: ArtifactRefV1,
    view: HoldoutCalculationView,*,role: NativeRole,
) -> NativeRequest:
    if type(view) is not HoldoutCalculationView or role not in ('PRIMARY','SELECTED_BASELINE'):
        raise ValueError('native request requires the released calculation view and exact role')
    manifest=_read(view,manifest_ref,HoldoutManifest)
    spec=_read(view,spec_ref,InstrumentSpec)
    steps=next_open_steps(manifest,spec,view,selected_baseline=role=='SELECTED_BASELINE')
    payload=dict(schema_version='p3-native-request-v1',role=role,source=manifest.source,
        environment_ref=manifest.environment_ref,manifest_ref=manifest_ref,instrument_spec_ref=spec_ref,
        policy_digest=manifest.policy_digest,
        transition_sha256=hashlib.sha256(canonical_json_bytes({'targets':[step.target for step in steps]})).hexdigest(),
        steps=steps)
    return NativeRequest.model_validate({**payload,'digest':hashlib.sha256(canonical_json_bytes(payload)).hexdigest()})


def validate_native_request(raw: bytes,manifest_ref: ArtifactRefV1,spec_ref: ArtifactRefV1,
    view: HoldoutCalculationView,*,role: NativeRole,
) -> NativeRequest:
    if type(raw) is not bytes or not 0<len(raw)<=262144:
        raise ValueError('native request exceeds its byte bound')
    expected=prepare_native_request(manifest_ref,spec_ref,view,role=role)
    if raw!=canonical_json_bytes(expected):
        raise ValueError('native request differs from its source, role or released view')
    return expected


def native_step_bytes(request: NativeRequest) -> bytes:
    """The existing pinned adapter accepts only precomputed daily steps."""
    request=NativeRequest.model_validate(request)
    return canonical_json_bytes([step.model_dump(mode='json',exclude={'source_artifact_ref'}) for step in request.steps])


def native_result_from_output(request: NativeRequest,raw: bytes,view: HoldoutCalculationView,
    output: ArtifactStore,
) -> ExecutableResult:
    request=validate_native_request(canonical_json_bytes(request),request.manifest_ref,
        request.instrument_spec_ref,view,role=request.role)
    if type(raw) is not bytes or not 0<len(raw)<=2097152:
        raise ValueError('native output exceeds its byte bound')
    rows=TypeAdapter(tuple[dict[str,object],...]).validate_json(raw,strict=True)
    if raw!=canonical_json_bytes(rows)+b'\n':
        raise ValueError('native output must be canonical with one final newline')
    return executable_result_from_rows(_read(view,request.manifest_ref,HoldoutManifest),
        _read(view,request.instrument_spec_ref,InstrumentSpec),request.steps,rows,output)

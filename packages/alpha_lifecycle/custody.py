"""Private custody bytes; only an authenticated custodian may supply plaintext."""
from typing import Annotated, Literal
from collections.abc import Mapping
import hashlib

from pydantic import BeforeValidator, Field, TypeAdapter, model_validator

from packages.alpha_lifecycle.contracts.authority import CustodyRecord
from packages.alpha_lifecycle.contracts.data import DatasetEvidence
from packages.alpha_lifecycle.contracts.execution import HoldoutManifest
from packages.alpha_lifecycle.contracts.models import Sha256, SourceIdentity, StrictModel, Token, json_array
from packages.alpha_lifecycle.holdout_view import HoldoutCalculationView, build_holdout_calculation_view
from packages.alpha_lifecycle.pit_evidence import _reference
from packages.alpha_lifecycle.replica_store import ArtifactStore, _read
from packages.alpha_lifecycle.sandbox_policy import MAX_VIEW_BYTES
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


class ReleaseRequest(StrictModel):
    schema_version: Literal['p3-holdout-release-request-v1']
    source: SourceIdentity
    job_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$')]
    attempt_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$')]
    worker_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')]
    authorization_digest: Sha256
    intent_digest: Sha256
    holdout_request_sha256: Sha256
    custody_record_ref: ArtifactRefV1
    holdout_commitment: Sha256
    plaintext_bundle_digest: Sha256
    row_inventory_digest: Sha256
    custodian_identity: Token
    research_identity: Token
    custodian_uid: Annotated[int, Field(gt=0)]
    research_uid: Annotated[int, Field(gt=0)]

    @model_validator(mode='after')
    def _separation(self) -> 'ReleaseRequest':
        if self.custodian_uid==self.research_uid or self.custodian_identity==self.research_identity:
            raise ValueError('release requires distinct custodian and research identities')
        return self


def validate_release_request(request: ReleaseRequest, attestation: 'CustodianAttestation',
    custody: CustodyRecord,
) -> None:
    request=ReleaseRequest.model_validate(request)
    validate_attestation(attestation,custody,custodian_uid=request.custodian_uid,research_uid=request.research_uid)
    raw=canonical_json_bytes(custody)
    _reference(request.custody_record_ref,65536)
    if (request.custody_record_ref.size_bytes!=len(raw)
        or request.custody_record_ref.content_sha256!=hashlib.sha256(raw).hexdigest()
        or request.row_inventory_digest!=hashlib.sha256(canonical_json_bytes(attestation.row_refs)).hexdigest()
        or any(getattr(request,key)!=getattr(custody,key) for key in
            ('holdout_commitment','plaintext_bundle_digest','custodian_identity','research_identity'))):
        raise ValueError('release request differs from retained custody')


class CustodianAttestation(StrictModel):
    schema_version: Literal['p3-custodian-attestation-v1']
    ciphertext_ref: ArtifactRefV1
    plaintext_bundle_digest: Sha256
    access_policy_digest: Sha256
    custodian_identity: Token
    research_identity: Token
    custodian_uid: Annotated[int, Field(gt=0)]
    research_uid: Annotated[int, Field(gt=0)]
    row_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(json_array), Field(min_length=366, max_length=366)]
    holdout_commitment: Sha256

    @model_validator(mode='after')
    def _binding(self) -> 'CustodianAttestation':
        if (self.custodian_uid == self.research_uid or self.custodian_identity == self.research_identity
            or len({ref.locator for ref in self.row_refs}) != 366):
            raise ValueError('custody identities or row inventory overlap')
        payload=self.model_dump(mode='json',exclude={'schema_version','holdout_commitment','custodian_uid','research_uid'})
        if self.holdout_commitment != _commitment(payload):
            raise ValueError('custody commitment differs from its inventory')
        return self


def _commitment(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes({'schema_version':'p3-holdout-commitment-v1',**payload})).hexdigest()


def build_attestation(*, ciphertext_ref: ArtifactRefV1, plaintext_bundle_digest: str,
    access_policy_digest: str, custodian_identity: str, research_identity: str,
    custodian_uid: int, research_uid: int, row_refs: tuple[ArtifactRefV1, ...],
) -> CustodianAttestation:
    payload=dict(ciphertext_ref=ciphertext_ref,plaintext_bundle_digest=plaintext_bundle_digest,
        access_policy_digest=access_policy_digest,custodian_identity=custodian_identity,
        research_identity=research_identity,row_refs=row_refs)
    return CustodianAttestation.model_validate(dict(schema_version='p3-custodian-attestation-v1',
        **payload,custodian_uid=custodian_uid,research_uid=research_uid,holdout_commitment=_commitment(payload)))


def validate_attestation(attestation: CustodianAttestation, custody: CustodyRecord, *,
    custodian_uid: int, research_uid: int,
) -> None:
    attestation=CustodianAttestation.model_validate(attestation)
    custody=CustodyRecord.model_validate(custody)
    raw=canonical_json_bytes(attestation)
    _reference(custody.custodian_attestation_ref,262144)
    if (type(custodian_uid) is not int or type(research_uid) is not int
        or (custodian_uid,research_uid)!=(attestation.custodian_uid,attestation.research_uid)
        or custody.custodian_attestation_ref.size_bytes!=len(raw)
        or custody.custodian_attestation_ref.content_sha256!=hashlib.sha256(raw).hexdigest()
        or any(getattr(custody,key)!=getattr(attestation,key) for key in
            ('holdout_commitment','ciphertext_ref','plaintext_bundle_digest','access_policy_digest',
             'custodian_identity','research_identity'))):
        raise ValueError('custodian attestation differs from approved custody')


def released_view(raw: bytes, attestation: CustodianAttestation, custody: CustodyRecord,
    manifest_ref: ArtifactRefV1, spec_ref: ArtifactRefV1, metadata: ArtifactStore, *,
    custodian_uid: int, research_uid: int,
) -> HoldoutCalculationView:
    """Build in memory after peer authentication and durable release admission.

    This pure validator grants no access authority. Never write the row bundle
    to the research CAS; calculations receive only the exact existing view.
    """
    validate_attestation(attestation,custody,custodian_uid=custodian_uid,research_uid=research_uid)
    if (type(raw) is not bytes or not 0<len(raw)<=MAX_VIEW_BYTES
        or hashlib.sha256(raw).hexdigest()!=custody.plaintext_bundle_digest):
        raise ValueError('released bundle digest or bound differs')
    records=TypeAdapter(dict[str,str]).validate_json(raw,strict=True)
    if (canonical_json_bytes(records)!=raw or set(records)!={ref.locator for ref in attestation.row_refs}):
        raise ValueError('released bundle inventory or canonical bytes differ')
    for ref in attestation.row_refs:
        _reference(ref,65536)
        value=records[ref.locator].encode()
        if len(value)!=ref.size_bytes or hashlib.sha256(value).hexdigest()!=ref.content_sha256:
            raise ValueError('released row differs from custody inventory')
    _reference(manifest_ref,65536)
    manifest=_read(metadata,manifest_ref,HoldoutManifest)
    for ref in (manifest.holdout_dataset_ref,manifest.buffer_ref):
        _reference(ref,2097152)
    holdout=_read(metadata,manifest.holdout_dataset_ref,DatasetEvidence)
    buffer=_read(metadata,manifest.buffer_ref,DatasetEvidence)
    if attestation.row_refs!=(*holdout.row_refs,*buffer.row_refs):
        raise ValueError('released rows differ from approved calculation manifest')

    class ReleasedInputs:
        def read_bytes(self, ref: ArtifactRefV1) -> bytes:
            if ref.locator in records:
                return records[ref.locator].encode()
            return metadata.read_bytes(ref)

        def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
            del value,media_type
            raise ValueError('released inputs are immutable')

    return HoldoutCalculationView(build_holdout_calculation_view(manifest_ref,spec_ref,ReleasedInputs()),
        manifest_ref,spec_ref)

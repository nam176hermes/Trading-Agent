"""One private custody response. No installation, keys or import-time IO."""
import fcntl
import os
import socket
import stat
import struct
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from packages.alpha_lifecycle.contracts.authority import CustodyRecord
from packages.alpha_lifecycle.contracts.models import StrictModel, Token
from packages.alpha_lifecycle.custody import CustodianAttestation, ReleaseRequest, validate_release_request
from packages.alpha_lifecycle.replica_store import ArtifactStore, _read
from packages.alpha_lifecycle.pit_evidence import _reference
from packages.alpha_lifecycle.sandbox_policy import MAX_VIEW_BYTES
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.runtime_release.config import _safe_directory
from services.job_worker.engine_spawn import _F_GET_SEALS, _REQUIRED_MEMFD_SEALS, _sealed_memfd
from services.job_store.p3_custodian_release import CustodianReleaseRepository
from services.job_store.records import ClaimedJob
from services.job_store.worker_repository import WorkerRepository
from packages.alpha_lifecycle.holdout_view import HoldoutCalculationView
from packages.data_contracts import ArtifactRefV1


class CustodianEndpoint(StrictModel):
    schema_version: Literal['p3-custodian-endpoint-v1']
    socket_path: str
    custodian_uid: Annotated[int,Field(gt=0)]
    research_uid: Annotated[int,Field(gt=0)]
    custodian_identity: Token
    research_identity: Token

    @model_validator(mode='after')
    def _valid(self) -> 'CustodianEndpoint':
        path=Path(self.socket_path)
        if (not path.is_absolute() or '..' in path.parts or str(path)!=self.socket_path
            or len(os.fsencode(path))>100 or self.custodian_uid==self.research_uid
            or self.custodian_identity==self.research_identity):
            raise ValueError('custodian endpoint or identity separation is invalid')
        return self


def _peer(channel: socket.socket, uid: int) -> None:
    if (channel.family!=socket.AF_UNIX or channel.type!=socket.SOCK_SEQPACKET
        or struct.unpack('3i',channel.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=uid):
        raise ValueError('custody peer identity or socket type differs')


def _request_identity(request: ReleaseRequest, endpoint: CustodianEndpoint) -> None:
    request=ReleaseRequest.model_validate(request)
    endpoint=CustodianEndpoint.model_validate(endpoint)
    if any(getattr(request,key)!=getattr(endpoint,key) for key in
        ('custodian_uid','research_uid','custodian_identity','research_identity')):
        raise ValueError('release request differs from the protected endpoint')


def request_bundle(endpoint: CustodianEndpoint, request: ReleaseRequest, *,
    fence: Callable[[],None], consume: Callable[[],None],
) -> bytes:
    """Research parent only, after metadata admission and committed disclosure.

    Root-owned ancestor descriptors pin pathname resolution through connect.
    The caller supplies an endpoint from its freshly rechecked protected profile.
    """
    _request_identity(request,endpoint)
    if os.geteuid()!=endpoint.research_uid:
        raise ValueError('release client is not the approved research UID')
    path=Path(endpoint.socket_path)
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
    directories: list[int]=[]
    try:
        current=os.open('/',flags);directories.append(current)
        if not _safe_directory(os.fstat(current)):raise ValueError('unprotected custody ancestor')
        for part in path.parts[1:-1]:
            current=os.open(part,flags,dir_fd=current);directories.append(current)
            if not _safe_directory(os.fstat(current)):raise ValueError('unprotected custody ancestor')
        before=os.stat(path.name,dir_fd=current,follow_symlinks=False)
        if (not stat.S_ISSOCK(before.st_mode) or before.st_uid!=endpoint.custodian_uid
            or stat.S_IMODE(before.st_mode)!=0o660):
            raise ValueError('custody socket ownership or mode differs')
        with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as channel:
            channel.settimeout(5)
            channel.connect(f'/proc/self/fd/{current}/{path.name}')
            _peer(channel,endpoint.custodian_uid)
            after=os.stat(path.name,dir_fd=current,follow_symlinks=False)
            if before!=after:raise ValueError('custody socket changed during connect')
            fence()
            consume()
            fence()
            raw=canonical_json_bytes(request)
            if channel.send(raw,socket.MSG_NOSIGNAL)!=len(raw):raise ValueError('incomplete custody request')
            bundle=receive_bundle(channel,hashlib.sha256(raw).hexdigest())
            fence()
            return bundle
    finally:
        for descriptor in reversed(directories):os.close(descriptor)


def release_holdout_view(claim: ClaimedJob, repository: WorkerRepository, store: ArtifactStore, *,
    endpoint: CustodianEndpoint, fence: Callable[[],None], trace_id: str,
) -> tuple[HoldoutCalculationView,ArtifactRefV1,ArtifactRefV1]:
    """Parent chain: metadata -> authenticated peer -> SQL -> released view.

    fence must recheck the caller's protected profile, safety and current claim.
    It must also be supplied to every replica's before_spawn callback. This
    source helper does not enable HOLDOUT in the official worker or SQL lane.
    """
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.holdout import validate_holdout_operation_input
    from packages.alpha_lifecycle.operation_input import P3OperationInput, HoldoutInput
    from packages.alpha_lifecycle.custody import released_view
    from packages.job_contracts import AlphaCampaignPayload, JobType
    if (type(repository) is not WorkerRepository or type(claim) is not ClaimedJob
        or claim.job_type is not JobType.ALPHA_CAMPAIGN or type(claim.payload) is not AlphaCampaignPayload
        or claim.payload.operation!='HOLDOUT' or claim.lease_expires_at<=datetime.now(UTC)):
        raise ValueError('release requires the exact current holdout claim and SQL owner')
    fence()
    for ref in (claim.payload.manifest_ref,claim.payload.authorization_ref):_reference(ref,65536)
    intent=_read(store,claim.payload.manifest_ref,P3OperationInput)
    authorization=_read(store,claim.payload.authorization_ref,RunAuthorization)
    request,manifest=validate_holdout_operation_input(intent,authorization,
        expected_source=claim.payload.expected_source,store=store,now=datetime.now(UTC))
    if not isinstance(intent.body,HoldoutInput):raise ValueError('holdout body is required')
    custody=_read(store,intent.body.custody_record_ref,CustodyRecord)
    _reference(custody.custodian_attestation_ref,262144)
    attestation=_read(store,custody.custodian_attestation_ref,CustodianAttestation)
    release=ReleaseRequest(schema_version='p3-holdout-release-request-v1',source=claim.payload.expected_source,
        job_id=claim.job_id,attempt_id=claim.attempt_id,worker_id=claim.worker_id,
        authorization_digest=claim.payload.authorization_ref.content_sha256,intent_digest=intent.digest,
        holdout_request_sha256=hashlib.sha256(canonical_json_bytes(request)).hexdigest(),
        custody_record_ref=intent.body.custody_record_ref,holdout_commitment=custody.holdout_commitment,
        plaintext_bundle_digest=custody.plaintext_bundle_digest,
        row_inventory_digest=hashlib.sha256(canonical_json_bytes(attestation.row_refs)).hexdigest(),
        custodian_identity=endpoint.custodian_identity,research_identity=endpoint.research_identity,
        custodian_uid=endpoint.custodian_uid,research_uid=endpoint.research_uid)
    validate_release_request(release,attestation,custody)
    def consume() -> None:
        _ = repository.consume_p3_holdout(claim,store,trace_id=trace_id)
    raw=request_bundle(endpoint,release,fence=fence,consume=consume)
    request_ref=store.put_bytes(canonical_json_bytes(request),media_type='application/json')
    manifest_ref=store.put_bytes(canonical_json_bytes(manifest),media_type='application/json')
    view=released_view(raw,attestation,custody,manifest_ref,intent.body.instrument_spec_ref,store,
        custodian_uid=endpoint.custodian_uid,research_uid=endpoint.research_uid)
    fence()
    return view,request_ref,manifest_ref


def serve_release(channel: socket.socket, endpoint: CustodianEndpoint, metadata: ArtifactStore, *,
    repository: CustodianReleaseRepository,
    request_sha256: str,
    read_plaintext: Callable[[CustodyRecord],bytes],
    recheck_profile: Callable[[],CustodianEndpoint],
) -> None:
    """One accepted connection in the independent custodian; never a daemon.

    The concrete custodian SQL owner commits and reads back before returning.
    Its unique durable release key survives process restart.
    Plaintext/key loading is exclusively the custodian's responsibility.
    """
    endpoint=CustodianEndpoint.model_validate(endpoint)
    if type(repository) is not CustodianReleaseRepository:
        raise ValueError('custodian requires its concrete SQL release owner')
    if os.geteuid()!=endpoint.custodian_uid:raise ValueError('unapproved custodian UID')
    _peer(channel,endpoint.research_uid)
    channel.settimeout(5)
    # recvmsg rather than recv ensures even rejected request-side FDs are closed.
    descriptors: list[int]=[]
    try:
        raw,ancillary,flags=channel.recvmsg(65537,socket.CMSG_SPACE(16*4),socket.MSG_CMSG_CLOEXEC)[:3]
        for level,kind,value in ancillary:
            if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                descriptors.extend(fd for (fd,) in struct.iter_unpack('i',value[:len(value)//4*4]))
        if ancillary or flags & ~(socket.MSG_EOR|socket.MSG_CMSG_CLOEXEC) or not 0<len(raw)<=65536:
            raise ValueError('invalid custody request frame')
        request=ReleaseRequest.model_validate_json(raw)
        if canonical_json_bytes(request)!=raw:raise ValueError('noncanonical custody request')
        if hashlib.sha256(raw).hexdigest()!=request_sha256:
            raise ValueError('custody request differs from protected profile')
        _request_identity(request,endpoint)
        _reference(request.custody_record_ref,65536)
        custody=_read(metadata,request.custody_record_ref,CustodyRecord)
        _reference(custody.custodian_attestation_ref,262144)
        attestation=_read(metadata,custody.custodian_attestation_ref,CustodianAttestation)
        validate_release_request(request,attestation,custody)
        if recheck_profile()!=endpoint:raise ValueError('custodian profile changed')
        repository.claim(request)
        repository.fence(request)
        if recheck_profile()!=endpoint:raise ValueError('custodian profile changed')
        plaintext=read_plaintext(custody)
        if hashlib.sha256(plaintext).hexdigest()!=custody.plaintext_bundle_digest:
            raise ValueError('custodian plaintext differs from commitment')
        if recheck_profile()!=endpoint:raise ValueError('custodian profile changed')
        repository.fence(request)
        send_bundle(channel,hashlib.sha256(raw).hexdigest(),plaintext)
    finally:
        for descriptor in descriptors:os.close(descriptor)


def receive_bundle(channel: socket.socket, request_sha256: str) -> bytes:
    """The caller authenticates the protected endpoint before sending a request.

    Close all delivered descriptors even on truncated/extra ancillary messages.
    Linux closes descriptors truncated out of the receive buffer itself.
    """
    descriptors: list[int]=[]
    try:
        packet,ancillary,flags=channel.recvmsg(513,socket.CMSG_SPACE(16*struct.calcsize('i')),
            socket.MSG_CMSG_CLOEXEC)[:3]
        malformed=False
        for level,kind,value in ancillary:
            if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                usable=len(value)-len(value)%struct.calcsize('i')
                descriptors.extend(fd for (fd,) in struct.iter_unpack('i',value[:usable]))
                malformed |= usable!=len(value)
            else:
                malformed=True
        if (malformed or flags & ~(socket.MSG_EOR|socket.MSG_CMSG_CLOEXEC)
            or len(descriptors)!=1
            or packet!=canonical_json_bytes({'request_sha256':request_sha256})):
            raise ValueError('custody response is truncated, unbound or has wrong descriptors')
        fd=descriptors[0]
        info=os.fstat(fd)
        _,uid,_=struct.unpack('3i',channel.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o400 or info.st_uid!=uid
            or not 0<info.st_size<=MAX_VIEW_BYTES or info.st_nlink!=0
            or fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE!=os.O_RDONLY
            or fcntl.fcntl(fd,_F_GET_SEALS)!=_REQUIRED_MEMFD_SEALS):
            raise ValueError('custody descriptor is not a bounded immutable read-only view')
        raw=os.pread(fd,info.st_size+1,0)
        if len(raw)!=info.st_size:
            raise ValueError('custody descriptor size changed')
        return raw
    finally:
        for fd in descriptors:
            os.close(fd)


def send_bundle(channel: socket.socket, request_sha256: str, raw: bytes) -> None:
    """Custodian only, after its independent SQL claim has committed and read back."""
    if type(raw) is not bytes or not 0<len(raw)<=MAX_VIEW_BYTES:
        raise ValueError('custody bundle is outside its byte bound')
    fd=_sealed_memfd('p3-holdout-release',raw,mode=0o400)
    try:
        packet=canonical_json_bytes({'request_sha256':request_sha256})
        count=channel.sendmsg([packet],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,struct.pack('i',fd))],socket.MSG_NOSIGNAL)
        if count!=len(packet):
            raise ValueError('custody response was not sent completely')
    finally:
        os.close(fd)

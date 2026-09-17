"""Confirm durable one-use SQL metadata; never decrypt, mount or launch holdout.

The protected caller owns metadata admission and custodian authentication.
The database owns the live claim fence, canonical bindings and uniqueness.
"""
from datetime import UTC,datetime,timedelta
import hashlib

from psycopg import OperationalError

from packages.alpha_lifecycle.authority import build_alpha_campaign_payload
from packages.alpha_lifecycle.contracts.authority import CustodyRecord,FamilyReview,PrimarySelection,RunAuthorization
from packages.alpha_lifecycle.contracts.execution import InputSet
from packages.alpha_lifecycle.holdout import derive_holdout_request
from packages.alpha_lifecycle.operation_input import HoldoutInput,P3OperationInput
from packages.alpha_lifecycle.pit_evidence import _ReadBudget,_reference
from packages.alpha_lifecycle.replica_store import _read
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import AlphaCampaignPayload,JobType
from .records import ClaimedJob


CONSUME = 'SELECT * FROM job_plane.worker_consume_p3_holdout(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'
READ = 'SELECT * FROM job_plane.worker_read_p3_holdout_disclosure(%s,%s,%s,%s,%s,%s,%s)'


def consume(pool,claim: ClaimedJob,store,*,trace_id: str) -> datetime:
    if (type(claim) is not ClaimedJob or claim.job_type is not JobType.ALPHA_CAMPAIGN
        or type(claim.payload) is not AlphaCampaignPayload or claim.payload.operation!='HOLDOUT'
        or not isinstance(claim.lease_expires_at,datetime) or claim.lease_expires_at.tzinfo is None
        or claim.lease_expires_at<=datetime.now(UTC)):
        raise ValueError('holdout disclosure requires a current HOLDOUT claim')
    reader=_ReadBudget(store)
    def read(ref,model):
        _reference(ref,65536)
        return _read(reader,ref,model)
    intent=read(claim.payload.manifest_ref,P3OperationInput)
    authorization=read(claim.payload.authorization_ref,RunAuthorization)
    if (not isinstance(intent.body,HoldoutInput) or build_alpha_campaign_payload(authorization,
        claim.payload.expected_source,claim.payload.logical_trial_id,operation_input=intent)!=claim.payload):
        raise ValueError('holdout disclosure payload differs from approved inputs')
    request=derive_holdout_request(intent,authorization,expected_source=claim.payload.expected_source)
    research=read(intent.input_set_ref,InputSet)
    holdout=read(intent.body.holdout_input_set_ref,InputSet)
    if research.source!=claim.payload.expected_source or holdout.source!=claim.payload.expected_source:
        raise ValueError('holdout disclosure InputSet source differs from its claim')
    primary=read(intent.body.primary_selection_ref,PrimarySelection)
    family=read(primary.family_review_ref,FamilyReview)
    custody=read(intent.body.custody_record_ref,CustodyRecord)
    raw=tuple(canonical_json_bytes(value).decode() for value in (request,research,holdout,family,primary,custody))
    expected=dict(authorization_digest=hashlib.sha256(canonical_json_bytes(authorization)).hexdigest(),
        intent_digest=intent.digest,holdout_request_sha256=hashlib.sha256(raw[0].encode()).hexdigest())
    def checked(row):
        if (type(row) is not dict or set(row)!=set(expected)|{'disclosed_at'}
            or any(row[key]!=value for key,value in expected.items())):
            raise ValueError('holdout SQL disclosure identity differs')
        stamp=row['disclosed_at']
        if (not isinstance(stamp,datetime) or stamp.tzinfo is None or stamp.utcoffset()!=timedelta(0)
            or not authorization.issued_at<=stamp<authorization.expires_at):
            raise ValueError('holdout SQL disclosure time differs from authorization')
        return stamp.astimezone(UTC)
    accepted=None
    try:
        with pool.connection() as connection:
            with connection.transaction():
                accepted=checked(connection.execute(CONSUME,
                    (claim.job_id,claim.attempt_id,claim.worker_id,claim.lease_token,*raw,trace_id)).fetchone())
    except OperationalError:
        # Never repeat an uncertain write. Only an exact committed SQL readback
        # can resolve the acknowledgement loss; plaintext remains unavailable.
        accepted=None
    try:
        with pool.connection() as connection:
            observed=checked(connection.execute(READ,(claim.job_id,claim.attempt_id,claim.worker_id,
                expected['authorization_digest'],expected['intent_digest'],expected['holdout_request_sha256'],trace_id)).fetchone())
    except (OperationalError,ValueError):
        raise RuntimeError('HELD E_HOLDOUT_DISCLOSURE: committed readback unavailable') from None
    if accepted is not None and observed!=accepted:
        raise ValueError('holdout disclosure changed after commit')
    return observed

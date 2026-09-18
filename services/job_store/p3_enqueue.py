"""Current LOCAL admission around a Job API transaction, without private CAS access."""
from packages.alpha_lifecycle.authority import (
    AuthorityHeld, FIXTURE_AUTHORIZATION, build_alpha_campaign_payload,
    validate_local_operation_authority,
)
from packages.alpha_lifecycle.contracts.authority import RUN_AUTHORIZATION
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.engine_contracts.serialization import canonical_json_bytes
from .errors import JobStoreError


def read_enqueue_authority(connection, payload):
    """Old hosted catalogs cannot contain LOCAL; 0031 is deliberately held."""
    row=connection.execute('SELECT version_num FROM public.alembic_version').fetchone()
    if row and row['version_num'] in {'0026_p3_holdout_disclosure','0030_p3_session_terminal_fences'}:
        return None
    if not row or row['version_num']!='0032_p3_enqueue_freshness':
        raise JobStoreError('P3 enqueue authority catalog is unavailable')
    row=connection.execute('SELECT * FROM job_plane.api_read_p3_enqueue_authority(%s)',
        (canonical_json_bytes(payload).decode(),)).fetchone()
    try:
        if row is None:raise ValueError
        fixture=payload.logical_trial_id=='p3-integration-fixture-v1'
        adapter=FIXTURE_AUTHORIZATION if fixture else RUN_AUTHORIZATION
        authorization=adapter.validate_json(row['authorization_text'])
        if not hasattr(authorization,'identity'):return None
        intent=None if fixture else P3OperationInput.model_validate_json(row['intent_text'])
        if build_alpha_campaign_payload(authorization,payload.expected_source,payload.logical_trial_id,
            operation_input=intent)!=payload:
            raise ValueError
        admitted=validate_local_operation_authority(authorization,payload.expected_source)
        if admitted.operation_input!=intent:raise ValueError
        return admitted
    except (OSError,ValueError,AuthorityHeld):
        raise JobStoreError('Current LOCAL enqueue authority is unavailable') from None


def recheck_enqueue_authority(admitted, payload):
    if admitted is None:return
    try:
        if validate_local_operation_authority(admitted.authorization,payload.expected_source)!=admitted:
            raise ValueError
    except (OSError,ValueError,AuthorityHeld):
        raise JobStoreError('Current LOCAL enqueue authority changed') from None

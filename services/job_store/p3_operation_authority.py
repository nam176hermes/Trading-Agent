"""Protected workflow acceptance; never a Job API or worker capability."""
from pathlib import Path
import hashlib

import psycopg

from packages.alpha_lifecycle.authority import AuthorityHeld,validate_local_operation_authority
from packages.alpha_lifecycle.contracts.authority import RUN_AUTHORIZATION, RunAuthorizationValue, ReviewApproval,LocalRunAuthorization
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.engine_contracts.serialization import canonical_json_bytes
from .config import JobStoreSettings, read_systemd_credential


def accept_operation_authorization(
    authorization: RunAuthorizationValue, operation_input: P3OperationInput,
    review: ReviewApproval, *, credential_directory: Path,
) -> None:
    """Accept exact reviewed bytes through the separately provisioned SQL role."""
    admitted=None
    authorization=RUN_AUTHORIZATION.validate_python(authorization)
    if isinstance(authorization,LocalRunAuthorization):
        admitted=validate_local_operation_authority(authorization,review.source)
        if admitted.operation_input!=operation_input:
            raise AuthorityHeld('HELD E_AUTHORITY: local SQL input differs from protected operation')
    raw = canonical_json_bytes(RUN_AUTHORIZATION.validate_python(authorization)).decode()
    intent = canonical_json_bytes(P3OperationInput.model_validate(operation_input)).decode()
    review_raw = canonical_json_bytes(ReviewApproval.model_validate(review)).decode()
    credentials = {'CREDENTIALS_DIRECTORY':str(credential_directory)}
    try:
        # Keep this principal out of JOB_PLANE_DATABASE_USERS. Its credential
        # directory is supplied only to the protected workflow process.
        settings = JobStoreSettings(
            host=read_systemd_credential(credentials,'database-host'),
            port=int(read_systemd_credential(credentials,'database-port')),
            database=read_systemd_credential(credentials,'database-name'),
            user='trading_p3_authority',
            password=read_systemd_credential(credentials,'database-password'),
        )
        with psycopg.connect(settings.conninfo(),connect_timeout=10) as connection:
            identity = connection.execute('SELECT session_user,current_user').fetchone()
            if identity != ('trading_p3_authority','trading_p3_authority'):
                raise AuthorityHeld('HELD E_SQL_AUTHORITY: protected acceptance identity differs')
            accepted = connection.execute(
                'SELECT job_plane.accept_p3_operation_authorization(%s,%s,%s)',
                (raw,intent,review_raw),
            ).fetchone()
            if accepted != (hashlib.sha256(raw.encode()).hexdigest(),):
                raise AuthorityHeld('HELD E_SQL_AUTHORITY: accepted authorization digest differs')
            if isinstance(authorization,LocalRunAuthorization):
                if validate_local_operation_authority(authorization,review.source)!=admitted:
                    raise AuthorityHeld('HELD E_AUTHORITY: local authority changed before SQL commit')
    except (OSError,ValueError,psycopg.Error):
        raise AuthorityHeld('HELD E_SQL_AUTHORITY: protected acceptance unavailable') from None

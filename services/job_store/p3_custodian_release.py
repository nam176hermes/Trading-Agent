"""Custodian-owned SQL claim/readback. No worker credentials or retry fallback."""
from datetime import UTC,datetime
from collections.abc import Mapping
import hashlib
from typing import LiteralString

import psycopg
from psycopg.rows import dict_row

from packages.alpha_lifecycle.custody import ReleaseRequest
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.config import JobStoreSettings,read_systemd_credential
from .p3_catalog import CUSTODIAN_CATALOG_SQL as CATALOG_SQL,CUSTODIAN_CATALOG_SHA256 as CATALOG_SHA256


REVISION='0027_p3_custodian_release'
CLAIM='SELECT * FROM job_plane.custodian_claim_p3_release(%s)'
READ='SELECT * FROM job_plane.custodian_read_p3_release(%s)'
IDENTITY="""SELECT current_user,session_user,(SELECT version_num FROM public.alembic_version),
  EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user
    AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
    AND NOT r.rolreplication AND NOT r.rolbypassrls AND NOT r.rolinherit
    AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m
      WHERE m.member=r.oid OR m.roleid=r.oid)) AS restricted"""


class CustodianReleaseRepository:
    @classmethod
    def from_systemd_credentials(cls,values: Mapping[str,str], *, session: bool = False) -> 'CustodianReleaseRepository':
        # Reuse the bounded credential reader without adding the custodian to
        # the API/worker/scheduler role allowlist.
        return cls(JobStoreSettings(
            host=read_systemd_credential(values,'database-host'),
            port=int(read_systemd_credential(values,'database-port')),
            database=read_systemd_credential(values,'database-name'),user='trading_p3_custodian',
            password=read_systemd_credential(values,'database-password')), session=session)

    def __init__(self,settings: JobStoreSettings, *, session: bool = False) -> None:
        if settings.user!='trading_p3_custodian':
            raise ValueError('custodian release requires its distinct database identity')
        if type(session) is not bool:
            raise ValueError('custodian session lane must be explicit')
        self._session = session
        self._settings: JobStoreSettings=settings

    def _execute(self,query: LiteralString,request: ReleaseRequest) -> datetime:
        from .p3_catalog import SESSION_CATALOG_SHA256, SESSION_REVISION
        revision = SESSION_REVISION if self._session else REVISION
        catalog = SESSION_CATALOG_SHA256 if self._session else CATALOG_SHA256
        request=ReleaseRequest.model_validate(request)
        raw=canonical_json_bytes(request)
        digest=hashlib.sha256(raw).hexdigest()
        # The connection context commits before returning. An exception including
        # lost acknowledgement propagates; claim must never be retried/read as PASS.
        with psycopg.Connection[dict[str,object]].connect(
            self._settings.conninfo(),row_factory=dict_row,connect_timeout=5,
            options=f'-c statement_timeout={min(self._settings.statement_timeout_ms,5000)} -c lock_timeout=2000 -c TimeZone=UTC -c search_path=pg_catalog',
        ) as connection:
            identity=connection.execute(IDENTITY).fetchone()
            if identity!={'current_user':'trading_p3_custodian','session_user':'trading_p3_custodian',
                'version_num':revision,'restricted':True}:
                raise ValueError('custodian database identity or revision differs')
            if connection.execute(CATALOG_SQL).fetchone()!={'catalog_sha256':catalog}:
                raise ValueError('custodian database catalog differs')
            row=connection.execute(query,(raw.decode(),)).fetchone()
            if (type(row) is not dict or set(row)!={'request_sha256','released_at'}
                or row['request_sha256']!=digest or not isinstance(row['released_at'],datetime)
                or row['released_at'].tzinfo is None or row['released_at'].utcoffset()!=UTC.utcoffset(None)):
                raise ValueError('custodian SQL release binding differs')
            return row['released_at']

    def claim(self,request: ReleaseRequest) -> None:
        committed=self._execute(CLAIM,request)
        if self._execute(READ,request)!=committed:
            raise ValueError('custodian release changed after commit')

    def fence(self,request: ReleaseRequest) -> None:
        _ = self._execute(READ,request)

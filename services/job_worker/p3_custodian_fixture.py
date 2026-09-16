"""Explicit disposable SQL checks for 0027; synthetic metadata, no plaintext."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
from typing import LiteralString
from typing_extensions import override

from alembic import command
from alembic.config import Config
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL,Identifier
from sqlalchemy import URL,create_engine

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.contracts.authority import CustodyRecord,HoldoutRequest
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_custodian_release import CLAIM,READ,REVISION,CustodianReleaseRepository
from services.job_store.config import JobStoreSettings
from packages.alpha_lifecycle.custody import ReleaseRequest
from services.job_store.p3_catalog import CUSTODIAN_CATALOG_SQL,CUSTODIAN_CATALOG_SHA256
from .p3_holdout_fixture import _inputs,_seed,CONSUME
from .p3_operation_fixture import _rejected


def check_custodian_release(sock: Path,name: str,source: SourceIdentity) -> None:
    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
        _ = owner.execute('CREATE ROLE trading_p3_custodian LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
        _ = owner.execute(SQL('GRANT CONNECT ON DATABASE {} TO trading_p3_custodian').format(Identifier(name)))
    engine=create_engine(URL.create('postgresql+psycopg',username='trading_owner',database=name,query={'host':str(sock)}))
    try:
        with engine.begin() as connection:
            config=Config(str(Path(__file__).resolve().parents[2]/'alembic.ini'))
            config.attributes['connection']=connection
            command.upgrade(config,REVISION)
    finally:engine.dispose()
    with psycopg.connect(host=str(sock),dbname=name,user='trading_p3_custodian',
        options='-c search_path=pg_catalog') as custodian:
        catalog=custodian.execute(CUSTODIAN_CATALOG_SQL).fetchone()
        assert catalog==(CUSTODIAN_CATALOG_SHA256,),catalog
    authorization,graph=_inputs(source,epoch='synthetic.release',material='release')
    claim=_seed(sock,name,source,'release',authorization)
    with psycopg.Connection[tuple[object,...]].connect(host=str(sock),dbname=name,user='trading_job_worker') as worker:
        row=worker.execute(CONSUME,(*claim,*graph,'test:release')).fetchone()
        assert row is not None
    custody=CustodyRecord.model_validate_json(graph[-1]); request=HoldoutRequest.model_validate_json(graph[0])
    value=dict(schema_version='p3-holdout-release-request-v1',source=source,
        job_id=claim[0],attempt_id=claim[1],worker_id=claim[2],authorization_digest=row[0],
        intent_digest=row[1],holdout_request_sha256=row[2],custody_record_ref=request.custody_record_ref,
        holdout_commitment=custody.holdout_commitment,plaintext_bundle_digest=custody.plaintext_bundle_digest,
        custodian_identity=custody.custodian_identity,research_identity=custody.research_identity,
        custodian_uid=17001,research_uid=17002,row_inventory_digest='9'*64)
    raw=canonical_json_bytes(value).decode()
    for key,replacement in (('source',dict(source.model_dump(),commit_sha='9'*40)),
        ('authorization_digest','0'*64),('holdout_request_sha256','0'*64),
        ('plaintext_bundle_digest','0'*64),('schema_version',None),('research_uid',17001)):
        with psycopg.connect(host=str(sock),dbname=name,user='trading_p3_custodian',autocommit=True) as custodian:
            with _rejected(psycopg.errors.InvalidParameterValue):
                _ = custodian.execute(CLAIM,(canonical_json_bytes({**value,key:replacement}).decode(),))
    for role in ('trading_job_worker','trading_job_api','trading_job_scheduler','trading_reader','trading_p3_authority'):
        with psycopg.connect(host=str(sock),dbname=name,user=role,autocommit=True) as connection:
            for query in (CLAIM,READ):
                with _rejected(psycopg.errors.InsufficientPrivilege):_ = connection.execute(query,(raw,))
    # Keep production settings TCP-only. This source-test adapter changes only
    # the address; the real client, driver, SQL transactions and fences execute.
    class SocketSettings(JobStoreSettings):
        @override
        def conninfo(self) -> str:
            return make_conninfo(host=str(sock),dbname=name,user=self.user)
    repository=CustodianReleaseRepository(SocketSettings('localhost',5432,name,'trading_p3_custodian','synthetic'))
    release=ReleaseRequest.model_validate_json(raw)
    def claim_once() -> bool:
        try:
            repository.claim(release)
            return True
        except psycopg.errors.UniqueViolation:return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(claim_once) for _ in range(2)]
        outcomes=tuple(future.result() for future in futures)
    assert outcomes.count(True)==1
    repository.fence(release)
    with psycopg.connect(host=str(sock),dbname=name,user='trading_p3_custodian') as custodian:
        accepted=custodian.execute(READ,(raw,)).fetchone()
    assert accepted is not None and accepted[0]==hashlib.sha256(raw.encode()).hexdigest()
    vectors: tuple[tuple[LiteralString,LiteralString],...]=(

        ('GRANT SELECT ON public.p3_custodian_releases TO trading_reader',
         'REVOKE SELECT ON public.p3_custodian_releases FROM trading_reader'),
        ('GRANT UPDATE (request_text) ON public.p3_custodian_releases TO trading_job_api',
         'REVOKE UPDATE (request_text) ON public.p3_custodian_releases FROM trading_job_api'),
        ('ALTER TABLE public.p3_custodian_releases DISABLE TRIGGER p3_custodian_releases_append_only',
         'ALTER TABLE public.p3_custodian_releases ENABLE TRIGGER p3_custodian_releases_append_only'),
        ('ALTER FUNCTION job_plane.custodian_read_p3_release(text) SET search_path=public',
         'ALTER FUNCTION job_plane.custodian_read_p3_release(text) SET search_path=pg_catalog'),
        ('ALTER FUNCTION job_plane.custodian_read_p3_release(text) SECURITY INVOKER',
         'ALTER FUNCTION job_plane.custodian_read_p3_release(text) SECURITY DEFINER'),
        ('ALTER FUNCTION job_plane.custodian_read_p3_release(text) STRICT',
         'ALTER FUNCTION job_plane.custodian_read_p3_release(text) CALLED ON NULL INPUT'),
        ('GRANT EXECUTE ON FUNCTION job_plane.custodian_read_p3_release(text) TO trading_reader',
         'REVOKE EXECUTE ON FUNCTION job_plane.custodian_read_p3_release(text) FROM trading_reader'),
        ('GRANT trading_p3_custodian TO trading_reader','REVOKE trading_p3_custodian FROM trading_reader'),
        ('ALTER ROLE trading_p3_custodian SET search_path=public','ALTER ROLE trading_p3_custodian RESET search_path'),
        ('CREATE RULE custodian_test_rule AS ON INSERT TO public.p3_custodian_releases DO INSTEAD NOTHING',
         'DROP RULE custodian_test_rule ON public.p3_custodian_releases'),
    )
    for damage,restore in vectors:
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
            _ = owner.execute(damage)
        try:
            with _rejected(ValueError):repository.fence(release)
        finally:
            with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
                _ = owner.execute(restore)
        repository.fence(release)
    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
        _ = owner.execute('ALTER ROLE trading_p3_custodian INHERIT')
    with psycopg.connect(host=str(sock),dbname=name,user='trading_p3_custodian',autocommit=True) as custodian:
        with _rejected(psycopg.errors.InsufficientPrivilege):_ = custodian.execute(READ,(raw,))
    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
        _ = owner.execute('ALTER ROLE trading_p3_custodian NOINHERIT')
    with psycopg.connect(host=str(sock),dbname=name,user='trading_p3_custodian',autocommit=True) as custodian:
        assert custodian.execute(READ,(raw,)).fetchone()==accepted
        with _rejected(psycopg.errors.UniqueViolation):_ = custodian.execute(CLAIM,(raw,))
        for query in ('SELECT * FROM public.p3_custodian_releases','DELETE FROM public.p3_custodian_releases',
            'UPDATE public.p3_custodian_releases SET released_at=clock_timestamp()',
            'TRUNCATE public.p3_custodian_releases','INSERT INTO public.p3_custodian_releases DEFAULT VALUES'):
            with _rejected(psycopg.errors.InsufficientPrivilege):_ = custodian.execute(query)
    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
        _ = owner.execute("UPDATE public.jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(claim[0],))
    with psycopg.connect(host=str(sock),dbname=name,user='trading_p3_custodian',autocommit=True) as custodian:
        with _rejected(psycopg.errors.InvalidParameterValue):_ = custodian.execute(READ,(raw,))
    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
        assert owner.execute('SELECT count(*) FROM public.p3_custodian_releases').fetchone()==(1,)
        with _rejected(psycopg.errors.ObjectNotInPrerequisiteState):_ = owner.execute('DELETE FROM public.p3_custodian_releases')

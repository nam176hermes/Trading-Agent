"""Manually invoked source check in a newly owned socket-only PostgreSQL cluster.
Not a protected-main qualification producer. Never accepts an existing database.
"""
import argparse
import sys
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, URL
import psycopg

BIN = Path('/usr/lib/postgresql/16/bin')



def _cleanup_cluster(root: Path, data: Path, sock: Path, started: bool, run) -> None:
    stop_error = None
    if started:
        try:
            run([str(BIN/'pg_ctl'), '-D', str(data), '-w', '-m', 'fast', 'stop'])
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            stop_error = error
        if (data/'postmaster.pid').exists() or tuple(sock.glob('.s.PGSQL.*')):
            raise RuntimeError(f"shutdown unverified; owned cluster retained at {root}") from stop_error
    shutil.rmtree(root)
    if root.exists():
        raise RuntimeError(f"owned cluster cleanup incomplete at {root}")
    if stop_error is not None:
        raise stop_error
    print('OWNED_CLUSTER_CLEANUP_PASS', flush=True)


def run_sql_source_check() -> None:
    if not __debug__:
        raise RuntimeError("source checks require assertions enabled")
    root = Path(tempfile.mkdtemp(prefix='p3-source-pg-', dir='/tmp'))
    data = root / 'data'
    sock = root / 'socket'
    sock.mkdir(mode=0o700)
    name = 'trading_agent_disposable_test'  # Legacy reviewed catalog name, newly owned isolated cluster.
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'LC_ALL': 'C', 'TZ': 'UTC'}
    started = False

    def run(args, raw=None):
        result = subprocess.run(args, input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, timeout=60)
        if result.returncode:
            raise RuntimeError(f'{Path(args[0]).name} failed with code {result.returncode}')
        return result

    def psql(raw, database='postgres'):
        run([str(BIN/'psql'), '-X', '-v', 'ON_ERROR_STOP=1', '-h', str(sock),
             '-U', 'postgres', '-d', database], raw.encode())

    def upgrade(revision):
        engine = create_engine(URL.create('postgresql+psycopg', username='trading_owner',
            database=name, query={'host': str(sock)}))
        try:
            with engine.begin() as connection:
                cfg = Config(str(ROOT/'alembic.ini'))
                cfg.attributes['connection'] = connection
                command.upgrade(cfg, revision)
        finally:
            engine.dispose()
        print('UPGRADE', revision, 'PASS', flush=True)

    try:
        run([str(BIN/'initdb'), '-D', str(data), '-U', 'postgres', '--auth-local=trust', '--auth-host=reject', '--no-locale', '--encoding=UTF8'])
        started = True
        run([str(BIN/'pg_ctl'), '-D', str(data), '-l', str(root/'postgres.log'), '-o',
             f"-k {sock} -c listen_addresses='' -c log_min_error_statement=panic", '-w', 'start'])
        passwords = {role: secrets.token_hex(24) for role in ('owner','migrator','reader','jobs')}
        prefix = ''.join(f"\\set {role}_password '{password}'\n" for role,password in passwords.items())
        base = (ROOT/'ops/postgres/provision-roles.sql').read_text().replace('trading_agent', name)
        psql(prefix + base)
        upgrade('0004_durable_research_jobs')
        role_sql = (ROOT/'ops/postgres/provision-job-roles.sql').read_text().replace('trading_agent', name)
        for role in ('trading_job_api','trading_job_worker','trading_job_scheduler'):
            password = secrets.token_hex(24)
            marker = f'\\password {role}\n'
            assert role_sql.count(marker) == 1
            role_sql = role_sql.replace(marker, marker + password + '\n' + password + '\n')
        psql("SET log_min_error_statement='panic'; SET track_activities=off;\n" + role_sql, name)
        upgrade('0019_p2_security_master')
        psql('CREATE ROLE trading_p3_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; GRANT trading_p3_owner TO trading_owner;', name)
        upgrade('0020_p3_alpha_campaign_authority')
        print('MIGRATION_CHAIN_PASS', flush=True)
        from tests.p3.test_job_api import _alpha_request
        from packages.engine_contracts import canonical_json_bytes
        from packages.job_contracts import AlphaCampaignOperation
        import hashlib
        payload = _alpha_request().payload.model_copy(update={"operation": AlphaCampaignOperation.PARITY, "logical_trial_id": "p3-integration-fixture-v1"})
        authorization = canonical_json_bytes({"purpose": "disposable-source-test"})
        authorization_digest = hashlib.sha256(authorization).hexdigest()
        payload = payload.model_copy(update={"authorization_ref": payload.authorization_ref.model_copy(update={
            "content_sha256": authorization_digest, "size_bytes": len(authorization),
            "locator": authorization_digest + ".blob",
        })})
        raw = canonical_json_bytes(payload).decode()
        with psycopg.connect(host=str(sock), dbname=name, user="trading_owner") as owner:
            owner.execute("SET ROLE trading_p3_owner")
            try:
                with owner.transaction():
                    owner.execute("INSERT INTO public.p3_campaign_authorizations(request_digest,input_set_digest,source_commit_sha,source_identity_text,operation,expires_at,authorization_text) VALUES (%s,%s,%s,%s,'PARITY',clock_timestamp()+interval '1 hour','{}')", ("b"*64,"a"*64,"c"*40,canonical_json_bytes(payload.expected_source).decode()))
            except psycopg.errors.CheckViolation:
                pass
            else:
                raise AssertionError("authorization digest mismatch accepted")
            owner.execute("INSERT INTO public.p3_campaign_authorizations(request_digest,input_set_digest,source_commit_sha,source_identity_text,operation,expires_at,authorization_text) VALUES (%s,%s,%s,%s,'PARITY',clock_timestamp()+interval '1 hour',%s)", (authorization_digest,"a"*64,"c"*40,canonical_json_bytes(payload.expected_source).decode(),authorization.decode()))
        print('AUTHORIZATION_DIGEST_BINDING_PASS', flush=True)
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_api") as api:
            row = api.execute("SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)",
                ("job_fixture",raw,hashlib.sha256(raw.encode()).hexdigest(),"fixture-1","source-test",0,"fixture:enqueue","event_enqueue")).fetchone()
            assert row == ("job_fixture", "ENQUEUED"), row
        print('API_ENQUEUE_PASS', flush=True)
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_worker") as worker:
            row = worker.execute("SELECT * FROM job_plane.worker_claim_alpha_campaign(%s,%s,%s,%s,%s,%s)",
                ("attempt_fixture","worker_fixture","t"*32,30,"fixture:claim","event_claim")).fetchone()
            assert row is not None and row[0] == "job_fixture", row
        print('WORKER_CLAIM_PASS', flush=True)
        from concurrent.futures import ThreadPoolExecutor
        import time
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_worker") as worker:
            row = worker.execute("SELECT job_plane.worker_start_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("job_fixture","attempt_fixture","worker_fixture","t"*32,100,100,100,"d"*64,"fixture:start","event_start")).fetchone()
            assert row == (True,), row
        print('WORKER_START_PASS', flush=True)
        replacement = "SELECT job_plane.worker_replace_alpha_fixture_process(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        params = ("job_fixture","attempt_fixture","worker_fixture","t"*32,100,100,100,"d"*64,101,101,101,"e"*64)
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_worker") as worker:
            assert worker.execute(replacement, params).fetchone() == (True,)
            assert worker.execute(replacement, params).fetchone() == (False,)
            assert worker.execute(replacement, (*params[:3],"z"*32,*params[4:])).fetchone() == (False,)
            assert worker.execute(replacement, (params[0],"wrong_attempt",*params[2:])).fetchone() == (False,)
            for index in range(4):
                malformed = list(params)
                malformed[index] = "bad space"
                try:
                    with worker.transaction():
                        worker.execute(replacement, malformed)
                except psycopg.errors.InvalidParameterValue:
                    pass
                else:
                    raise AssertionError('malformed authority accepted')
            try:
                with worker.transaction():
                    worker.execute("UPDATE public.job_attempts SET child_pid=777 WHERE attempt_id='attempt_fixture'")
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError('direct worker table write accepted')
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_api") as api:
            try:
                with api.transaction():
                    api.execute(replacement, params)
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError('API role acquired worker capability')
        print('REPLACE_FENCE_PRIVILEGES_PASS', flush=True)
        with psycopg.connect(host=str(sock), dbname=name, user="postgres") as locker, psycopg.connect(host=str(sock), dbname=name, user="trading_job_worker") as contender:
            contender.execute("SET statement_timeout='5s'")
            contender.commit()
            assert locker.info.backend_pid != contender.info.backend_pid
            locker.execute("SELECT job_id FROM public.jobs WHERE job_id='job_fixture' FOR UPDATE")
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: contender.execute(replacement,
                    (*params[:4],101,101,101,"e"*64,102,102,102,"f"*64)).fetchone())
                try:
                    deadline = time.monotonic()+3
                    while time.monotonic() < deadline:
                        waiting = locker.execute("SELECT wait_event_type FROM pg_catalog.pg_stat_activity WHERE pid=%s", (contender.info.backend_pid,)).fetchone()
                        if waiting == ('Lock',):
                            break
                        locker.execute("SELECT pg_stat_clear_snapshot()")
                        time.sleep(.01)
                    else:
                        raise AssertionError('second connection did not block on row lock')
                    locker.execute("UPDATE public.jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id='job_fixture'")
                    locker.execute("UPDATE public.job_attempts SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE attempt_id='attempt_fixture'")
                    locker.commit()
                    assert future.result(timeout=5) == (False,)
                finally:
                    locker.rollback()
        print('TWO_CONNECTION_EXPIRED_LEASE_PASS', flush=True)
        bad_payload = payload.model_copy(update={"manifest_ref": payload.manifest_ref.model_copy(update={"content_sha256":"f"*64,"locator":"f"*64+".blob"})})
        bad_raw = canonical_json_bytes(bad_payload).decode()
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_api") as api:
            try:
                with api.transaction():
                    api.execute("SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)",
                        ("job_bad_manifest",bad_raw,hashlib.sha256(bad_raw.encode()).hexdigest(),"bad-manifest","source-test",0,"fixture:bad","event_bad_manifest"))
            except psycopg.errors.InvalidParameterValue:
                pass
            else:
                raise AssertionError('unapproved manifest accepted by SQL authority')
        print('AUTHORIZATION_MANIFEST_BINDING_PASS', flush=True)
        for field, value in (("tree_sha", "0"*40), ("closure_schema_version", "wrong-v1"),
                             ("closure_policy_sha256", "0"*64), ("closure_sha256", "0"*64)):
            bad_source = payload.expected_source.model_copy(update={field: value})
            bad_raw = canonical_json_bytes(payload.model_copy(update={"expected_source": bad_source})).decode()
            with psycopg.connect(host=str(sock), dbname=name, user="trading_job_api") as api:
                try:
                    with api.transaction():
                        api.execute("SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)",
                            ("job_bad_source",bad_raw,hashlib.sha256(bad_raw.encode()).hexdigest(),"bad-source","source-test",0,"fixture:bad","event_bad_source"))
                except psycopg.errors.InvalidParameterValue:
                    pass
                else:
                    raise AssertionError(f'unapproved source {field} accepted by SQL authority')
        print('AUTHORIZATION_SOURCE_BINDING_PASS', flush=True)

        from psycopg_pool import ConnectionPool
        from psycopg.rows import dict_row
        from psycopg.conninfo import make_conninfo
        from services.job_store.worker_repository import WorkerRepository
        from services.job_worker.recovery import ProcessIdentity
        from services.job_worker.artifacts import ArtifactMetadata
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_api") as api:
            api.execute("SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)",
                ("job_final",raw,hashlib.sha256(raw.encode()).hexdigest(),"fixture-final","source-test",0,"fixture:final","event_final_enqueue"))
        # The socket-only source fixture injects a real pool; runtime settings remain loopback-only.
        repository = object.__new__(WorkerRepository)
        repository._pool = ConnectionPool(make_conninfo(host=str(sock), dbname=name, user="trading_job_worker"), min_size=1, max_size=2, kwargs={"row_factory":dict_row})
        try:
            repository.assert_p3_runtime_identity()
            claimed = repository.claim_next_alpha_campaign("worker_fixture",30,"fixture:final-claim")
            assert claimed is not None and claimed.job_id == "job_final"
            assert repository.start_attempt(claimed.job_id,claimed.attempt_id,claimed.worker_id,
                claimed.lease_token,ProcessIdentity(201,201,201,"d"*64),"fixture:final-start",alpha_campaign=True)
            repository.worker_heartbeat(claimed.worker_id,"c"*40,"BUSY",current_job_id=claimed.job_id,
                current_attempt_id=claimed.attempt_id,metadata={})
            print('WORKER_BUSY_HEARTBEAT_PASS', flush=True)
            artifact = ArtifactMetadata("stdout",f"{claimed.job_id}/{claimed.attempt_id}/stdout.log","a"*64,3,"application/octet-stream",False)
            assert repository.finalize(claimed.job_id,claimed.attempt_id,claimed.worker_id,claimed.lease_token,
                expected_state="RUNNING",expected_attempt_outcome="RUNNING",final_state="SUCCEEDED",
                reason_code="RESULT_VALIDATED",trace_id="fixture:final-result",exit_code=0,result_hash="a"*64,
                artifacts=(artifact,),alpha_campaign=True)
        finally:
            repository.close()
        with psycopg.connect(host=str(sock), dbname=name, user="postgres") as owner:
            assert owner.execute("SELECT state,result_hash FROM public.jobs WHERE job_id='job_final'").fetchone() == ('SUCCEEDED','a'*64)
            assert owner.execute("SELECT count(*) FROM public.job_artifacts WHERE job_id='job_final'").fetchone() == (1,)
        print('WORKER_DURABLE_RESULT_PASS', flush=True)
        from tests.p3.sql_publication_fixture import check_publication
        check_publication(sock, name, root, payload)



    finally:
        primary_error = sys.exception()
        try:
            _cleanup_cluster(root, data, sock, started, run)
        except BaseException as cleanup_error:
            if primary_error is not None:
                raise BaseExceptionGroup("source check and cleanup failed", [primary_error, cleanup_error]) from None
            raise


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable-sql", action="store_true")
    args = parser.parse_args(argv)
    if not args.run_disposable_sql:
        parser.error("explicit --run-disposable-sql is required to start the owned test cluster")
    run_sql_source_check()


if __name__ == "__main__":
    main()

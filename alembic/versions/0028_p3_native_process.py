"""Fenced native child attribution; existing worker/custodian profiles stay pinned."""
from alembic import op
from sqlalchemy import text

from services.job_store.p3_catalog import CUSTODIAN_CATALOG_SQL, CUSTODIAN_CATALOG_SHA256

revision = '0028_p3_native_process'
down_revision = '0027_p3_custodian_release'
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.execute(text("""SELECT current_user='trading_owner' AND session_user='trading_owner'
        AND (SELECT version_num FROM public.alembic_version)='0027_p3_custodian_release'
        AND NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='job_plane' AND p.proname='worker_replace_alpha_native_process')""")).scalar() is not True:
        raise RuntimeError('0028 requires the exact parent, owner and absent native capability')
    if connection.execute(text(CUSTODIAN_CATALOG_SQL)).scalar() != CUSTODIAN_CATALOG_SHA256:
        raise RuntimeError('0028 parent authority catalog differs')
    op.execute("""GRANT CREATE ON SCHEMA job_plane TO trading_p3_owner;
      SET LOCAL ROLE trading_p3_owner;
      CREATE FUNCTION job_plane.worker_replace_alpha_native_process(
        p_job_id text, p_attempt_id text, p_worker_id text, p_lease_token text,
        p_previous_pid bigint, p_previous_group bigint, p_previous_ticks bigint,
        p_previous_fingerprint text, p_pid bigint, p_group bigint,
        p_ticks bigint, p_fingerprint text
      ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
      SET search_path=pg_catalog
      AS $native_process$
      DECLARE j public.jobs%ROWTYPE; a public.job_attempts%ROWTYPE; checked_at timestamptz;
      BEGIN
        IF session_user<>'trading_job_worker'
          OR NOT EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname=session_user
            AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
            AND NOT r.rolreplication AND NOT r.rolbypassrls AND NOT r.rolinherit
            AND NOT EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid OR m.roleid=r.oid)) THEN
          RAISE EXCEPTION 'native process role rejected' USING ERRCODE='42501';
        END IF;
        IF (p_job_id~'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
          AND p_attempt_id~'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
          AND p_worker_id~'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
          AND p_lease_token~'^[A-Za-z0-9_-]{16,128}$'
          AND p_previous_pid>0 AND p_previous_group>0 AND p_previous_ticks>=0
          AND p_previous_fingerprint~'^[0-9a-f]{64}$'
          AND p_pid>0 AND p_group=p_pid AND p_ticks>=0 AND p_fingerprint~'^[0-9a-f]{64}$'
          AND (p_pid,p_ticks)<>(p_previous_pid,p_previous_ticks)) IS NOT TRUE THEN
          RAISE EXCEPTION 'native process identity rejected' USING ERRCODE='22023';
        END IF;
        PERFORM set_config('lock_timeout','2000',true);
        SELECT * INTO j FROM public.jobs WHERE job_id=p_job_id FOR UPDATE;
        IF NOT FOUND THEN RETURN false; END IF;
        SELECT * INTO a FROM public.job_attempts WHERE job_id=p_job_id AND attempt_id=p_attempt_id FOR UPDATE;
        IF NOT FOUND THEN RETURN false; END IF;
        checked_at:=clock_timestamp();
        IF (j.job_type='ALPHA_CAMPAIGN' AND j.state='RUNNING' AND a.outcome='RUNNING'
          AND j.payload->>'operation'='PARITY' AND j.payload->>'logical_trial_id'='p3-native-parity-v1'
          AND j.lease_owner=p_worker_id AND a.worker_id=p_worker_id
          AND j.lease_token=p_lease_token AND a.lease_token=p_lease_token
          AND j.attempt_count=a.attempt_number
          AND j.lease_expires_at>checked_at AND a.lease_expires_at>checked_at
          AND a.child_pid=p_previous_pid AND a.process_group_id=p_previous_group
          AND a.process_start_ticks=p_previous_ticks AND a.command_fingerprint=p_previous_fingerprint
          AND job_plane.p3_payload_authorized(j.payload,j.job_id)) IS NOT TRUE THEN
          RETURN false;
        END IF;
        UPDATE public.job_attempts SET child_pid=p_pid, process_group_id=p_group,
          process_start_ticks=p_ticks, command_fingerprint=p_fingerprint
          WHERE job_id=p_job_id AND attempt_id=p_attempt_id;
        IF j.lease_expires_at<=clock_timestamp() OR a.lease_expires_at<=clock_timestamp()
          OR NOT job_plane.p3_payload_authorized(j.payload,j.job_id) THEN
          RAISE EXCEPTION 'native process fence expired during update' USING ERRCODE='22023';
        END IF;
        RETURN true;
      END;
      $native_process$;
      REVOKE ALL ON FUNCTION job_plane.worker_replace_alpha_native_process(
        text,text,text,text,bigint,bigint,bigint,text,bigint,bigint,bigint,text)
        FROM PUBLIC,trading_owner,trading_jobs,trading_migrator,trading_reader,trading_job_api,
          trading_job_worker,trading_job_scheduler,trading_p3_authority,trading_p3_custodian;
      GRANT EXECUTE ON FUNCTION job_plane.worker_replace_alpha_native_process(
        text,text,text,text,bigint,bigint,bigint,text,bigint,bigint,bigint,text) TO trading_job_worker;
      RESET ROLE;
      REVOKE CREATE ON SCHEMA job_plane FROM trading_p3_owner;
    """)


def downgrade() -> None:
    raise RuntimeError('native process authority is forward-only; use a reviewed forward repair')

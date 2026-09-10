"""Fence explicit P3 lanes and current authority before process creation."""
from __future__ import annotations

import hashlib

from alembic import op
from sqlalchemy import text

revision = '0022_p3_worker_lane_isolation'
down_revision = '0021_p3_operation_authority'
branch_labels = None
depends_on = None


def _reviewed(name: str, signature: str, digest: str, *, defaults: int = 0,
    security: bool = True, volatility: str = 'v', parallel: str = 'u') -> str:
    arguments = {
        'worker_claim_alpha_campaign':'p_attempt_id,p_worker_id,p_lease_token,p_lease_seconds,p_trace_id,p_event_id,p_fixture_only,job_id,job_type,payload,attempt_number,max_attempts,lease_expires_at',
        'worker_control_alpha_campaign_lease':'p_job_id,p_attempt_id,p_worker_id,p_lease_token,p_lease_seconds,p_phase',
        'worker_recover_expired_alpha_campaign':'p_job_id,p_attempt_id,p_expected_state,p_expected_attempt_outcome,p_expected_lease_owner,p_expected_lease_token,p_expected_child_pid,p_expected_process_group_id,p_expected_process_start_ticks,p_expected_command_fingerprint,p_observation,p_trace_id,p_recovery_id,p_event_id,p_retry_event_id',
        'p3_worker_lane_matches':'payload,fixture_only',
    }[name].split(',')
    if name == 'worker_recover_expired_alpha_campaign' and signature.endswith(',boolean'):
        arguments.append('p_fixture_only')
    returns_set = name == 'worker_claim_alpha_campaign'
    result = ('TABLE(job_id text, job_type text, payload jsonb, attempt_number integer, max_attempts smallint, lease_expires_at timestamp with time zone)'
        if returns_set else 'boolean' if name == 'p3_worker_lane_matches' else 'text')
    modes = ['i']*7+['t']*6 if returns_set else None
    rows = op.get_bind().execute(text("""
        SELECT p.prosrc,pg_catalog.pg_get_functiondef(p.oid)
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE p.oid=pg_catalog.to_regprocedure(:signature)
          AND r.rolname='trading_p3_owner' AND p.prosecdef=:security AND p.provolatile=:volatility
          AND p.proparallel=:parallel AND p.pronargdefaults=:defaults AND p.prokind='f'
          AND l.lanname=:language
          AND p.proconfig=ARRAY['search_path=pg_catalog'] AND NOT p.proleakproof AND NOT p.proisstrict
          AND p.proretset=:returns_set AND pg_catalog.pg_get_function_result(p.oid)=:result
          AND p.proargnames=CAST(:arguments AS text[])
          AND p.proargmodes::text[] IS NOT DISTINCT FROM CAST(:modes AS text[])
          AND NOT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb
          AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
            WHERE a.grantee NOT IN (p.proowner,(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='trading_job_worker'))
               OR a.privilege_type<>'EXECUTE' OR a.is_grantable)
          AND EXISTS (SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a
            WHERE a.grantee=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='trading_job_worker')
              AND a.privilege_type='EXECUTE')
    """),{'signature':f'job_plane.{name}({signature})','security':security,'volatility':volatility,'parallel':parallel,'defaults':defaults,
        'returns_set':returns_set,'result':result,'arguments':arguments,'modes':modes,
        'language':'sql' if name == 'p3_worker_lane_matches' else 'plpgsql'}).fetchall()
    if len(rows) != 1 or hashlib.sha256(rows[0][0].encode()).hexdigest() != digest:
        raise RuntimeError(f'0022 reviewed function drift: {name}')
    return rows[0][1]


def _replace(definition: str, before: str, after: str) -> str:
    if definition.count(before) != 1:
        raise RuntimeError('0022 reviewed function seam drift')
    return definition.replace(before, after)


def _body_digest(definition: str) -> str:
    body = definition.partition('\nAS $function$')[2].rpartition('$function$')[0]
    if not body:
        raise RuntimeError('0022 reviewed function framing differs')
    return hashlib.sha256(body.encode()).hexdigest()


def upgrade() -> None:
    connection = op.get_bind()
    if connection.execute(text("""SELECT current_user='trading_owner' AND session_user='trading_owner'
        AND (SELECT version_num FROM public.alembic_version)='0021_p3_operation_authority'""")).scalar() is not True:
        raise RuntimeError('0022 requires the reviewed P3 migration parent and owner')
    # Prevent a concurrent old-source claim from creating stranded holdout custody.
    op.execute('LOCK TABLE public.jobs IN SHARE ROW EXCLUSIVE MODE')
    if connection.execute(text("""SELECT EXISTS(SELECT 1 FROM public.jobs WHERE job_type='ALPHA_CAMPAIGN'
        AND (payload->>'operation'='HOLDOUT' OR payload->>'logical_trial_id'='p3-holdout-primary-v1')
        AND state IN ('CLAIMED','RUNNING','CANCEL_REQUESTED'))""")).scalar() is True:
        raise RuntimeError('0022 requires reconciliation of active holdout custody')
    claim_sig = 'text,text,text,integer,text,text,boolean'
    recovery_sig = 'text,text,text,text,text,text,bigint,bigint,bigint,text,text,text,text,text,text'
    claim = _reviewed('worker_claim_alpha_campaign',claim_sig,'aebf4263e0c8a4044188bc69b9fd6e2ecaeddd57744c0698215273ac3feebc05',defaults=1)
    control = _reviewed('worker_control_alpha_campaign_lease','text,text,text,text,integer,text','81b383e79293768cdb3e37117a3f413d0cbcb4b0330b52c1c221799de102f511')
    recovery = _reviewed('worker_recover_expired_alpha_campaign',recovery_sig,'e0e7da9cbcc7a3afbdf11a78f3bae247f6c8a3bdb32038bbe61803b3756d862e')
    claim = _replace(claim,'p_fixture_only boolean DEFAULT false','p_fixture_only boolean')
    claim = _replace(claim,"""(NOT p_fixture_only OR (j.payload->>'logical_trial_id'='p3-integration-fixture-v1'
                 AND j.payload->>'operation'='PARITY'))""",
        "job_plane.p3_worker_lane_matches(j.payload,p_fixture_only) AND job_plane.p3_payload_authorized(j.payload,j.job_id)")
    claim = _replace(claim,'IF NOT FOUND THEN RETURN; END IF;',
        'IF NOT FOUND THEN RETURN; END IF;\n          IF NOT job_plane.p3_worker_lane_matches(v_job.payload,p_fixture_only) OR NOT job_plane.p3_payload_authorized(v_job.payload,v_job.job_id) THEN RETURN; END IF;')
    control = _replace(control,'          renewed_until := statement_timestamp()',
        """          IF p_phase='PRE_SPAWN' AND current_job.state<>'CANCEL_REQUESTED'
             AND NOT job_plane.p3_payload_authorized(current_job.payload,current_job.job_id) THEN
            RETURN 'STALE';
          END IF;
          renewed_until := statement_timestamp()""")
    recovery = _replace(recovery,'p_retry_event_id text)','p_retry_event_id text, p_fixture_only boolean)')
    recovery = _replace(recovery,"          IF p_job_id IS NULL", "          IF p_fixture_only IS NULL OR p_job_id IS NULL")
    recovery = _replace(recovery,'          effective_observation := p_observation;',
        """          IF NOT job_plane.p3_worker_lane_matches(current_job.payload,p_fixture_only) THEN
            RETURN 'LEASE_RECOVERY_STALE';
          END IF;
          effective_observation := p_observation;""")
    lane_body = """
          SELECT CASE WHEN fixture_only IS TRUE THEN coalesce(
            (payload->>'operation',payload->>'logical_trial_id')=('PARITY','p3-integration-fixture-v1'),false)
          WHEN fixture_only IS FALSE THEN coalesce((payload->>'operation',payload->>'logical_trial_id') IN (
            ('BASELINES','p3-baselines-v1'),('REGISTER_FAMILY','p3-register-family-v1'),
            ('OOS','p3-oos-a0-v1'),('OOS','p3-oos-a1-v1'),('OOS','p3-oos-a2-v1'),('OOS','p3-oos-a3-v1'),
            ('OOS','p3-select-primary-v1'),('PARITY','p3-native-parity-v1'),('PHASE_EXIT','p3-phase-exit-v1')),false)
          ELSE false END
        """
    op.execute(f"""
        GRANT CREATE ON SCHEMA job_plane TO trading_p3_owner;
        SET LOCAL ROLE trading_p3_owner;
        CREATE FUNCTION job_plane.p3_worker_lane_matches(payload jsonb,fixture_only boolean) RETURNS boolean
          LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog
        AS $lane${lane_body}$lane$;
    """)
    # Replace signatures atomically. No implicit official claim or old recovery entry remains.
    op.execute(f'DROP FUNCTION job_plane.worker_claim_alpha_campaign({claim_sig})')
    op.execute(claim)
    op.execute(control)
    op.execute(recovery)
    op.execute(f'DROP FUNCTION job_plane.worker_recover_expired_alpha_campaign({recovery_sig})')
    for name, signature in (('worker_claim_alpha_campaign',claim_sig),
                            ('worker_recover_expired_alpha_campaign',recovery_sig+',boolean'),
                            ('p3_worker_lane_matches','jsonb,boolean')):
        op.execute(f'REVOKE ALL ON FUNCTION job_plane.{name}({signature}) FROM PUBLIC,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler,trading_p3_authority')
        op.execute(f'GRANT EXECUTE ON FUNCTION job_plane.{name}({signature}) TO trading_job_worker')
    _reviewed('worker_claim_alpha_campaign',claim_sig,_body_digest(claim))
    _reviewed('worker_control_alpha_campaign_lease','text,text,text,text,integer,text',_body_digest(control))
    _reviewed('worker_recover_expired_alpha_campaign',recovery_sig+',boolean',_body_digest(recovery))
    _reviewed('p3_worker_lane_matches','jsonb,boolean',hashlib.sha256(lane_body.encode()).hexdigest(),security=False,volatility='i',parallel='s')
    op.execute('RESET ROLE; REVOKE CREATE ON SCHEMA job_plane FROM trading_p3_owner')


def downgrade() -> None:
    raise RuntimeError('0022 P3 lane isolation is forward-only; use a reviewed forward repair')

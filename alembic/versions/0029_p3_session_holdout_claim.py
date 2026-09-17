"""Private workflow-bound HOLDOUT claim; ordinary lanes stay closed."""
import importlib.util
from pathlib import Path

from alembic import op
from sqlalchemy import text

from services.job_store.p3_catalog import CUSTODIAN_CATALOG_SQL

revision = '0029_p3_session_holdout_claim'
down_revision = '0028_p3_native_process'
branch_labels = None
depends_on = None

# Observed only after the unchanged disposable 0028 chain; not a runtime bypass.
PARENT_CATALOG = '401791846c2f54247e81a75722b770e19ae6609c47751c9f291569bec2e2c4de'


def _lifecycle(connection, replace):
    """Derive only from the whole-catalog-verified parent; retain its locks/events."""
    definitions = []
    signatures = (
        ('start', 'worker_start_alpha_campaign', 'text,text,text,text,bigint,bigint,bigint,text,text,text'),
        ('control', 'worker_control_alpha_campaign_lease', 'text,text,text,text,integer,text'),
        ('finalize', 'worker_finalize_alpha_campaign', 'text,text,text,text,text,text,text,text,text,text,integer,text,text,jsonb,text,text,boolean,text,jsonb'),
        ('recover', 'worker_recover_expired_alpha_campaign', 'text,text,text,text,text,text,bigint,bigint,bigint,text,text,text,text,text,text,boolean'),
    )
    bound = 'job_plane.p3_session_holdout_bound(current_job.payload,p_job_id,p_run_id,p_run_attempt)'
    fresh = 'job_plane.p3_session_holdout_authorized(current_job.payload,p_job_id,p_run_id,p_run_attempt)'
    lease = """current_job.lease_expires_at IS NOT NULL AND current_job.lease_expires_at>clock_timestamp()
              AND current_attempt.lease_expires_at IS NOT NULL AND current_attempt.lease_expires_at>clock_timestamp()"""
    for action, source, signature in signatures:
        definition = connection.execute(text('SELECT pg_get_functiondef(to_regprocedure(:signature))'),
            {'signature': f'job_plane.{source}({signature})'}).scalar()
        if not isinstance(definition, str):
            raise RuntimeError('reviewed private lifecycle parent unavailable')
        definition = replace(definition, f'job_plane.{source}(', f'job_plane.worker_{action}_session_holdout(')
        header, separator, body = definition.partition('\n RETURNS ')
        if not separator or not header.endswith(')'):
            raise RuntimeError('reviewed lifecycle signature framing differs')
        if action == 'recover':
            header = replace(header, ', p_fixture_only boolean)', ')')
            body = replace(body, 'p_fixture_only IS NULL OR p_job_id IS NULL', 'p_job_id IS NULL')
            body = replace(body, 'job_plane.p3_worker_lane_matches(current_job.payload,p_fixture_only)', bound)
            body = replace(body, 'retry_job := current_job.attempt_count < current_job.max_attempts;', 'retry_job := false;')
            signature = signature.removesuffix(',boolean')
        else:
            body = replace(body, 'job_plane.p3_payload_authorized(current_job.payload,'+
                ('current_job.job_id)' if action == 'control' else 'p_job_id)'), fresh)
            boundary = '          renewed_until := statement_timestamp()' if action == 'control' else '          SELECT coalesce(max(event_row.sequence), 0) + 1'
            rejected = "'STALE'" if action == 'control' else 'false'
            guard = f"""          IF NOT {bound} OR ({lease}) IS NOT TRUE THEN RETURN {rejected}; END IF;
"""
            if action == 'control':
                guard += f"""          IF current_job.state='CANCEL_REQUESTED' THEN RETURN 'CANCEL'; END IF;
          IF NOT {fresh} THEN RETURN 'STALE'; END IF;
"""
                body = replace(body, "p_phase='PRE_SPAWN' AND current_job.state<>'CANCEL_REQUESTED'", "current_job.state<>'CANCEL_REQUESTED'")
            elif action == 'start':
                guard += """          IF NOT EXISTS(SELECT 1 FROM public.p3_holdout_disclosures d
              WHERE d.job_id=p_job_id AND d.attempt_id=p_attempt_id AND d.worker_id=p_worker_id)
              THEN RETURN false; END IF;
"""
            else:
                guard += """          IF p_final_state='SUCCEEDED' AND (p_result_hash IS NULL OR NOT EXISTS(
              SELECT 1 FROM public.p3_holdout_disclosures d WHERE d.job_id=p_job_id
              AND d.attempt_id=p_attempt_id AND d.worker_id=p_worker_id)) THEN RETURN false; END IF;
"""
            body = replace(body, boundary, guard+boundary)
            terminal = "          RETURN 'CONTINUE';" if action == 'control' else '          RETURN true;'
            condition = f'NOT {fresh} OR ({lease}) IS NOT TRUE'
            if action == 'finalize':
                condition = f"p_final_state='SUCCEEDED' AND ({condition})"
            body = replace(body, terminal, f"""          IF {condition} THEN
            RAISE EXCEPTION 'holdout authority expired during {action}' USING ERRCODE='22023';
          END IF;
"""+terminal)
        body = replace(body, '        BEGIN', """        BEGIN
          IF p_run_id IS NULL OR p_run_id<=0 OR p_run_attempt IS NULL OR p_run_attempt<=0 THEN
            RAISE EXCEPTION 'private workflow identity required' USING ERRCODE='22023';
          END IF;
          IF NOT EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname=session_user
              AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
              AND NOT r.rolreplication AND NOT r.rolbypassrls AND NOT r.rolinherit
              AND NOT EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid OR m.roleid=r.oid)) THEN
            RAISE EXCEPTION 'private lifecycle role rejected' USING ERRCODE='42501';
          END IF;""")
        definitions.append((header[:-1]+', p_run_id bigint, p_run_attempt bigint)'+separator+body,
            f'worker_{action}_session_holdout({signature},bigint,bigint)'))
    return definitions


def upgrade() -> None:
    connection = op.get_bind()
    if connection.execute(text("""SELECT current_user='trading_owner' AND session_user='trading_owner'
        AND (SELECT version_num FROM public.alembic_version)='0028_p3_native_process'
        AND NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='job_plane' AND p.proname IN
            ('worker_claim_session_holdout','p3_session_holdout_authorized','p3_session_holdout_bound',
             'worker_start_session_holdout','worker_control_session_holdout',
             'worker_finalize_session_holdout','worker_recover_session_holdout'))""")).scalar() is not True:
        raise RuntimeError('0029 requires the exact parent, owner and absent private capabilities')
    previous = connection.execute(text('SHOW search_path')).scalar()
    try:
        connection.execute(text("SELECT set_config('search_path','pg_catalog',true)"))
        catalog = connection.execute(text(CUSTODIAN_CATALOG_SQL)).scalar()
    finally:
        connection.execute(text("SELECT set_config('search_path',:value,true)"), {'value': previous})
    if catalog != PARENT_CATALOG:
        raise RuntimeError('0029 parent authority catalog differs')

    # Reuse the reviewed claim body and exact replacement guard, including its
    # row lock, lease creation, event chain and one-transaction attempt insert.
    spec = importlib.util.spec_from_file_location('session_claim_parent',
        Path(__file__).with_name('0023_p3_output_custody.py'))
    if spec is None or spec.loader is None:
        raise RuntimeError('reviewed claim transformation unavailable')
    parent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    definition = connection.execute(text("""SELECT pg_get_functiondef(
        'job_plane.worker_claim_bound_alpha_campaign(text,text,text,integer,text,text,boolean,text)'::regprocedure)""")).scalar()
    if not isinstance(definition, str):
        raise RuntimeError('reviewed bound claim unavailable')
    definition = parent._replace(definition, 'job_plane.worker_claim_bound_alpha_campaign(',
        'job_plane.worker_claim_session_holdout(')
    definition = parent._replace(definition, 'p_fixture_only boolean, p_expected_job_id text)',
        'p_expected_job_id text, p_run_id bigint, p_run_attempt bigint)')
    definition = parent._replace(definition, 'p_fixture_only IS NULL OR session_user',
        'p_run_id IS NULL OR p_run_id<=0 OR p_run_attempt IS NULL OR p_run_attempt<=0 OR session_user')
    definition = parent._replace(definition,
        'job_plane.p3_worker_lane_matches(j.payload,p_fixture_only) AND job_plane.p3_payload_authorized(j.payload,j.job_id)',
        'job_plane.p3_session_holdout_authorized(j.payload,j.job_id,p_run_id,p_run_attempt)')
    definition = parent._replace(definition,
        'NOT job_plane.p3_worker_lane_matches(v_job.payload,p_fixture_only) OR NOT job_plane.p3_payload_authorized(v_job.payload,v_job.job_id)',
        'NOT job_plane.p3_session_holdout_authorized(v_job.payload,v_job.job_id,p_run_id,p_run_attempt)')
    definition = parent._replace(definition, 'AND j.attempt_count < j.max_attempts',
        'AND j.attempt_count=0 AND j.max_attempts=1')
    definition = parent._replace(definition, '          RETURN NEXT;', '''
          IF NOT job_plane.p3_session_holdout_authorized(v_job.payload,v_job.job_id,p_run_id,p_run_attempt) THEN
            RAISE EXCEPTION 'holdout authority expired during claim' USING ERRCODE='22023';
          END IF;
          RETURN NEXT;''')
    definition = parent._replace(definition, '        BEGIN', '''        BEGIN
          IF NOT EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname=session_user
              AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
              AND NOT r.rolreplication AND NOT r.rolbypassrls AND NOT r.rolinherit
              AND NOT EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid OR m.roleid=r.oid)) THEN
            RAISE EXCEPTION 'holdout claim role rejected' USING ERRCODE='42501';
          END IF;
          IF (p_attempt_id IS NOT NULL AND p_worker_id IS NOT NULL AND p_lease_token IS NOT NULL
              AND p_lease_seconds IS NOT NULL AND p_trace_id IS NOT NULL AND p_event_id IS NOT NULL) IS NOT TRUE THEN
            RAISE EXCEPTION 'holdout claim identity required' USING ERRCODE='22023';
          END IF;''')
    if 'p_fixture_only' in definition:
        raise RuntimeError('fixture branch survived private claim transformation')
    lifecycle = _lifecycle(connection, parent._replace)
    op.execute('GRANT CREATE ON SCHEMA job_plane TO trading_p3_owner; SET LOCAL ROLE trading_p3_owner')
    op.execute("""CREATE FUNCTION job_plane.p3_session_holdout_bound(
        payload jsonb, bound_job text, run_id bigint, run_attempt bigint)
      RETURNS boolean LANGUAGE sql SECURITY DEFINER VOLATILE PARALLEL UNSAFE SET search_path=pg_catalog AS $holdout$
      SELECT coalesce(payload->>'operation'='HOLDOUT'
        AND payload->>'logical_trial_id'='p3-holdout-primary-v1'
        AND EXISTS(SELECT 1 FROM public.p3_operation_job_bindings b
          JOIN public.p3_campaign_authorizations a ON a.request_digest=b.authorization_digest
          WHERE b.job_id=bound_job AND b.authorization_digest=payload#>>'{authorization_ref,content_sha256}'
            AND a.authorization_text::jsonb->>'issuer_run_id'=run_id::text
            AND a.authorization_text::jsonb->>'issuer_attempt'=run_attempt::text),false)
      $holdout$;
      REVOKE ALL ON FUNCTION job_plane.p3_session_holdout_bound(jsonb,text,bigint,bigint)
        FROM PUBLIC,trading_owner,trading_jobs,trading_migrator,trading_reader,trading_job_api,
          trading_job_worker,trading_job_scheduler,trading_p3_authority,trading_p3_custodian;
    """)
    op.execute("""CREATE FUNCTION job_plane.p3_session_holdout_authorized(
        payload jsonb, bound_job text, run_id bigint, run_attempt bigint)
      RETURNS boolean LANGUAGE sql SECURITY DEFINER VOLATILE PARALLEL UNSAFE SET search_path=pg_catalog AS $fresh$
      SELECT coalesce(job_plane.p3_session_holdout_bound(payload,bound_job,run_id,run_attempt)
        AND job_plane.p3_payload_authorized(payload,NULL),false)
      $fresh$;
      REVOKE ALL ON FUNCTION job_plane.p3_session_holdout_authorized(jsonb,text,bigint,bigint)
        FROM PUBLIC,trading_owner,trading_jobs,trading_migrator,trading_reader,trading_job_api,
          trading_job_worker,trading_job_scheduler,trading_p3_authority,trading_p3_custodian;
    """)
    for body, signature in lifecycle:
        op.execute(body)
        op.execute(f'REVOKE ALL ON FUNCTION job_plane.{signature} FROM PUBLIC,trading_owner,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler,trading_p3_authority,trading_p3_custodian')
        op.execute(f'GRANT EXECUTE ON FUNCTION job_plane.{signature} TO trading_job_worker')
    op.execute(definition)
    op.execute("""REVOKE ALL ON FUNCTION job_plane.worker_claim_session_holdout(
        text,text,text,integer,text,text,text,bigint,bigint)
        FROM PUBLIC,trading_owner,trading_jobs,trading_migrator,trading_reader,trading_job_api,
          trading_job_worker,trading_job_scheduler,trading_p3_authority,trading_p3_custodian;
      GRANT EXECUTE ON FUNCTION job_plane.worker_claim_session_holdout(
        text,text,text,integer,text,text,text,bigint,bigint) TO trading_job_worker;
      RESET ROLE; REVOKE CREATE ON SCHEMA job_plane FROM trading_p3_owner;
    """)


def downgrade() -> None:
    raise RuntimeError('session holdout claim is forward-only; use a reviewed repair')

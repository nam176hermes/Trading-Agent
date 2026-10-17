"""Carry a typed local issuer through the existing single-disclosure lifecycle."""
from alembic import op
from sqlalchemy import text

from services.job_store.p3_catalog import SESSION_CATALOG_SQL

revision = '0031_p3_local_identity'
down_revision = '0030_p3_session_terminal_fences'
branch_labels = None
depends_on = None
PARENT_CATALOG = '506071c06cdd91fe7e9506a03c15df10949e41f97f80dbbdd27c305aef3a82d4'
_REVOKE = 'PUBLIC,trading_owner,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler,trading_p3_authority,trading_p3_custodian'


def _replace(value, old, new, *, count=1):
    if value.count(old) != count:
        raise RuntimeError('0031 reviewed identity boundary differs')
    return value.replace(old,new)


def upgrade():
    connection = op.get_bind()
    if connection.execute(text("""SELECT current_user='trading_owner' AND session_user='trading_owner'
        AND (SELECT version_num FROM public.alembic_version)='0030_p3_session_terminal_fences'""")).scalar() is not True:
        raise RuntimeError('0031 requires the exact session parent and owner')
    previous = connection.execute(text('SHOW search_path')).scalar()
    try:
        connection.execute(text("SET LOCAL search_path=pg_catalog"))
        if connection.execute(text(SESSION_CATALOG_SQL)).scalar() != PARENT_CATALOG:
            raise RuntimeError('0031 parent authority catalog differs')
        def definition(signature):
            value = connection.execute(text('SELECT pg_get_functiondef(to_regprocedure(:signature))'),
                {'signature':'job_plane.'+signature}).scalar()
            if not isinstance(value,str):
                raise RuntimeError('0031 reviewed function is unavailable')
            return value
        acceptance = definition('accept_p3_operation_authorization(text,text,text)')
        old = "a:=job_plane.p3_checked_object(auth_text,'p3-run-authorization-v1',ARRAY['schema_version','input_set_ref','review_ref','operation','allowed_alpha_ids','issued_at','expires_at','nonce','issuer_workflow','issuer_run_id','issuer_attempt','authority','digest']);"
        acceptance = _replace(acceptance,old,"""IF auth_text::jsonb->>'schema_version'='p3-local-run-authorization-v1' THEN
        a:=job_plane.p3_checked_object(auth_text,'p3-local-run-authorization-v1',ARRAY['schema_version','input_set_ref','review_ref','operation','allowed_alpha_ids','issued_at','expires_at','nonce','identity','authority','digest']);
      ELSE
        """+old+"\n      END IF;")
        github = """a->>'issuer_workflow'='p3-authority.yml'
          AND jsonb_typeof(a->'issuer_run_id')='number' AND a->>'issuer_run_id'~'^[1-9][0-9]*$'
          AND jsonb_typeof(a->'issuer_attempt')='number' AND a->>'issuer_attempt'~'^[1-9][0-9]*$'"""
        acceptance = _replace(acceptance,github,"(CASE WHEN a->>'schema_version'='p3-local-run-authorization-v1' THEN job_plane.p3_valid_local_identity(a->'identity') ELSE ("+github+") END)")
        signatures = (
            ('worker_claim_session_holdout','text,text,text,integer,text,text,text'),
            ('worker_start_session_holdout','text,text,text,text,bigint,bigint,bigint,text,text,text'),
            ('worker_control_session_holdout','text,text,text,text,integer,text'),
            ('worker_finalize_session_holdout','text,text,text,text,text,text,text,text,text,text,integer,text,text,jsonb,text,text,boolean,text,jsonb'),
            ('worker_recover_session_holdout','text,text,text,text,text,text,bigint,bigint,bigint,text,text,text,text,text,text'),
        )
        functions = []
        for name,arguments in signatures:
            body = definition(name+'('+arguments+',bigint,bigint)')
            body = _replace(body,'p_run_id bigint, p_run_attempt bigint','p_identity text')
            body = _replace(body,'p_run_id IS NULL OR p_run_id<=0 OR p_run_attempt IS NULL OR p_run_attempt<=0',
                "job_plane.p3_valid_local_identity(p_identity::jsonb) IS NOT TRUE OR public.canonical_domain_json(p_identity::jsonb) IS DISTINCT FROM p_identity")
            count = body.count('p_run_id,p_run_attempt')
            if count < 1:
                raise RuntimeError('0031 session identity predicates are unavailable')
            body = _replace(body,'p_run_id,p_run_attempt','p_identity',count=count)
            if 'p_run_id' in body or 'p_run_attempt' in body:
                raise RuntimeError('0031 numeric identity survived local overload')
            functions.append((body,name+'('+arguments+',text)'))
    finally:
        connection.execute(text("SELECT set_config('search_path',:value,true)"),{'value':previous})

    op.execute('GRANT CREATE ON SCHEMA job_plane TO trading_p3_owner; SET LOCAL ROLE trading_p3_owner')
    op.execute(("""CREATE FUNCTION job_plane.p3_valid_local_identity(i jsonb)
      RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $identity$
      SELECT coalesce(jsonb_typeof(i)='object' AND i=jsonb_build_object(
          'issuer_kind',i->'issuer_kind','issuer_identity',i->'issuer_identity','run_id',i->'run_id',
          'attempt',i->'attempt','host_id',i->'host_id','boot_id',i->'boot_id')
        AND i->>'issuer_kind'='LOCAL' AND jsonb_typeof(i->'issuer_identity')='string'
        AND i->>'issuer_identity'~'^[a-z][a-z0-9._-]{0,127}$'
        AND jsonb_typeof(i->'run_id')='string'
        AND i->>'run_id'~'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND jsonb_typeof(i->'attempt')='number' AND i->>'attempt'~'^[1-9][0-9]*$'
        AND jsonb_typeof(i->'host_id')='string' AND i->>'host_id'~'^[0-9a-f]{64}$'
        AND jsonb_typeof(i->'boot_id')='string'
        AND i->>'boot_id'~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',false)
      $identity$;
      REVOKE ALL ON FUNCTION job_plane.p3_valid_local_identity(jsonb) FROM PUBLIC;
      CREATE FUNCTION job_plane.p3_session_holdout_bound(payload jsonb,bound_job text,p_identity text)
      RETURNS boolean LANGUAGE sql SECURITY DEFINER VOLATILE PARALLEL UNSAFE SET search_path=pg_catalog AS $bound$
      SELECT coalesce(job_plane.p3_valid_local_identity(p_identity::jsonb)
        AND public.canonical_domain_json(p_identity::jsonb)=p_identity
        AND payload->>'operation'='HOLDOUT' AND payload->>'logical_trial_id'='p3-holdout-primary-v1'
        AND EXISTS(SELECT 1 FROM public.p3_operation_job_bindings b
          JOIN public.p3_campaign_authorizations a ON a.request_digest=b.authorization_digest
          WHERE b.job_id=bound_job AND b.authorization_digest=payload#>>'{authorization_ref,content_sha256}'
            AND a.authorization_text::jsonb->>'schema_version'='p3-local-run-authorization-v1'
            AND a.authorization_text::jsonb->'identity'=p_identity::jsonb),false)
      $bound$;
      REVOKE ALL ON FUNCTION job_plane.p3_session_holdout_bound(jsonb,text,text) FROM PUBLIC;
      CREATE FUNCTION job_plane.p3_session_holdout_authorized(payload jsonb,bound_job text,p_identity text)
      RETURNS boolean LANGUAGE sql SECURITY DEFINER VOLATILE PARALLEL UNSAFE SET search_path=pg_catalog AS $fresh$
      SELECT coalesce(job_plane.p3_session_holdout_bound(payload,bound_job,p_identity)
        AND job_plane.p3_payload_authorized(payload,NULL),false)
      $fresh$;
      REVOKE ALL ON FUNCTION job_plane.p3_session_holdout_authorized(jsonb,text,text) FROM PUBLIC;
    """).replace("FROM PUBLIC;",f"FROM {_REVOKE};"))
    op.execute(acceptance)
    for body,signature in functions:
        op.execute(body)
        op.execute(f'REVOKE ALL ON FUNCTION job_plane.{signature} FROM {_REVOKE}')
        op.execute(f'GRANT EXECUTE ON FUNCTION job_plane.{signature} TO trading_job_worker')
    op.execute("""CREATE FUNCTION job_plane.worker_read_phase_exit(p_job_id text)
      RETURNS text LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $exit$
      DECLARE answer text;
      BEGIN
        IF session_user<>'trading_job_worker' THEN
          RAISE EXCEPTION 'P3 exit reader session rejected' USING ERRCODE='42501';
        END IF;
        SELECT public.canonical_domain_json(jsonb_build_object(
          'payload',j.payload,'publication_request',r.request_text::jsonb,
          'commit_result',r.result_text::jsonb,'committed_at',r.committed_at,
          'attempt_id',r.output_attempt_id,'output_inventory_ref',r.output_inventory_ref_text::jsonb,
          'registry_head',jsonb_build_object('alpha_id',h.alpha_id,'version',h.alpha_version,
            'sequence',h.registry_sequence,'event_digest',h.registry_event_sha256))) INTO answer
        FROM job_plane.worker_read_alpha_publication(p_job_id) r
          JOIN public.jobs j ON j.job_id=p_job_id
          JOIN public.p3_alpha_heads h ON h.registry_event_sha256=
            r.result_text::jsonb#>>'{registry_event_refs,0,content_sha256}'
        WHERE j.payload->>'operation'='PHASE_EXIT' AND j.payload->>'logical_trial_id'='p3-phase-exit-v1'
          AND r.request_text::jsonb->>'stage'='EXIT_DECISION'
          AND jsonb_array_length(r.result_text::jsonb->'registry_event_refs')=1;
        IF answer IS NULL THEN
          RAISE EXCEPTION 'P3 committed exit or current head unavailable' USING ERRCODE='P3D07';
        END IF;
        RETURN answer;
      END;
      $exit$""")
    op.execute(f'REVOKE ALL ON FUNCTION job_plane.worker_read_phase_exit(text) FROM {_REVOKE}')
    op.execute('GRANT EXECUTE ON FUNCTION job_plane.worker_read_phase_exit(text) TO trading_job_worker')
    op.execute('RESET ROLE; REVOKE CREATE ON SCHEMA job_plane FROM trading_p3_owner')


def downgrade():
    raise RuntimeError('local identity is forward-only; use a reviewed repair')

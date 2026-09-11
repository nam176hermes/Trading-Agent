"""Bind official attempt outputs inside the existing atomic publication commit."""
from __future__ import annotations

import hashlib

from alembic import op
from sqlalchemy import text

revision = '0023_p3_output_custody'
down_revision = '0022_p3_worker_lane_isolation'
branch_labels = None
depends_on = None


def _reviewed(name, signature, digest, *, arguments, language, volatility, parallel, result, modes=None):
    rows=op.get_bind().execute(text("""
        SELECT p.prosrc,pg_catalog.pg_get_functiondef(p.oid)
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE p.oid=pg_catalog.to_regprocedure(:signature)
          AND r.rolname='trading_p3_owner' AND p.prosecdef AND p.provolatile=:volatility
          AND p.proparallel=:parallel AND p.pronargdefaults=0 AND p.prokind='f'
          AND l.lanname=:language AND p.proconfig=ARRAY['search_path=pg_catalog']
          AND NOT p.proleakproof AND NOT p.proisstrict AND p.proretset=:returns_set
          AND pg_catalog.pg_get_function_result(p.oid)=:result
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
    """),dict(signature='job_plane.'+name+'('+signature+')',arguments=arguments.split(','),
        language=language,volatility=volatility,parallel=parallel,result=result,modes=modes,
        returns_set=modes is not None)).fetchall()
    if len(rows)!=1 or hashlib.sha256(rows[0][0].encode()).hexdigest()!=digest:
        raise RuntimeError('0023 reviewed function drift: '+name)
    return rows[0][1]


def _replace(definition,before,after):
    if definition.count(before)!=1:
        raise RuntimeError('0023 reviewed function seam drift')
    return definition.replace(before,after)


def _wrapper(result,ref,attempt):
    return f"jsonb_build_object('result',{result},'output_inventory_ref',({ref})::jsonb,'output_attempt_id',{attempt})"


def upgrade():
    if op.get_bind().execute(text("""SELECT current_user='trading_owner' AND session_user='trading_owner'
        AND (SELECT version_num FROM public.alembic_version)='0022_p3_worker_lane_isolation'""")).scalar() is not True:
        raise RuntimeError('0023 requires the reviewed P3 parent and owner')
    op.execute('LOCK TABLE public.jobs IN SHARE ROW EXCLUSIVE MODE')
    commit=_reviewed('worker_commit_alpha_campaign','text,text,text,text,text,text',
        '7a31bf44f779b51220d2ab0e432025028dfa46a607f94d276becec64f230429a',
        arguments='p_job_id,p_attempt_id,p_worker_id,p_lease_token,p_request_text,p_trace_id',
        language='plpgsql',volatility='v',parallel='u',result='jsonb')
    read=_reviewed('read_alpha_commit','text,text,text',
        '18e72e9e9254af1ee3b69910ef5673ac1b1a4928a3a4b4251709f33c6576aca1',
        arguments='p_job_id,p_idempotency_key,p_semantic_request_digest',
        language='sql',volatility='s',parallel='s',result='jsonb')
    custody=_reviewed('worker_read_alpha_publication','text',
        'e9d604fde2b0d54ecfaa88c68bfc83fdef108ca0048e84ae504965d8f92b6d7b',
        arguments='p_job_id,request_text,result_text,committed_at',language='plpgsql',
        volatility='s',parallel='u',result='TABLE(request_text text, result_text text, committed_at timestamp with time zone)',
        modes=['i','t','t','t'])
    commit=_replace(commit,"          v_transport:=p_request_text::jsonb;", """          v_transport:=p_request_text::jsonb;
          IF (jsonb_typeof(v_transport)='object'
             AND v_transport=jsonb_build_object('job_id',v_transport->'job_id',
               'attempt_id',v_transport->'attempt_id','worker_id',v_transport->'worker_id',
               'lease_token',v_transport->'lease_token','request',v_transport->'request',
               'entries',v_transport->'entries','output_inventory_ref',v_transport->'output_inventory_ref')
             AND job_plane.p3_valid_ref(v_transport->'output_inventory_ref')
             AND v_transport#>>'{output_inventory_ref,media_type}'='application/json'
             AND (v_transport#>>'{output_inventory_ref,size_bytes}')::numeric BETWEEN 1 AND 4194304) IS NOT TRUE THEN
            RAISE EXCEPTION 'P3 output custody transport rejected' USING ERRCODE='22023';
          END IF;""")
    commit=_replace(commit,"jsonb_object_keys(v_transport))<>6","jsonb_object_keys(v_transport))<>7")
    commit=_replace(commit,
        "OR v_existing.publication_request_text IS DISTINCT FROM public.canonical_domain_json(v_request) THEN",
        "OR v_existing.publication_request_text IS DISTINCT FROM public.canonical_domain_json(v_request)\n              OR v_existing.output_inventory_ref_text IS DISTINCT FROM public.canonical_domain_json(v_transport->'output_inventory_ref')\n              OR v_existing.output_attempt_id IS DISTINCT FROM p_attempt_id THEN")
    commit=_replace(commit,'RETURN v_existing.result_json;',
        'RETURN '+_wrapper('v_existing.result_json','v_existing.output_inventory_ref_text','v_existing.output_attempt_id')+';')
    commit=_replace(commit,'result_digest,publication_request_text,committed_at)',
        'result_digest,publication_request_text,committed_at,output_inventory_ref_text,output_attempt_id)')
    commit=_replace(commit,'public.canonical_domain_json(v_request),clock_timestamp());',
        "public.canonical_domain_json(v_request),clock_timestamp(),public.canonical_domain_json(v_transport->'output_inventory_ref'),p_attempt_id);")
    commit=_replace(commit,'          RETURN v_result;',
        '          RETURN '+_wrapper('v_result',"v_transport->'output_inventory_ref'",'p_attempt_id')+';')
    read=_replace(read,'SELECT c.result_json FROM public.p3_alpha_job_commits c',
        'SELECT CASE WHEN c.output_inventory_ref_text IS NULL THEN c.result_json ELSE '+
        _wrapper('c.result_json','c.output_inventory_ref_text','c.output_attempt_id')+' END FROM public.p3_alpha_job_commits c')
    custody=_replace(custody,'AND j.result_hash=c.result_digest AND j.result_metadata=c.result_json;',
        """AND j.result_hash=c.result_digest AND j.result_metadata=c.result_json
          AND c.output_inventory_ref_text IS NOT NULL
          AND EXISTS(SELECT 1 FROM public.job_attempts a WHERE a.attempt_id=c.output_attempt_id
            AND a.job_id=c.job_id AND a.outcome='SUCCEEDED');""")
    op.execute("""GRANT CREATE ON SCHEMA job_plane TO trading_p3_owner;
        GRANT REFERENCES ON TABLE public.job_attempts TO trading_p3_owner;
        SET LOCAL ROLE trading_p3_owner;
        ALTER TABLE public.p3_alpha_job_commits
          ADD COLUMN output_inventory_ref_text text,
          ADD COLUMN output_attempt_id varchar(64) REFERENCES public.job_attempts(attempt_id) ON DELETE RESTRICT;
        ALTER TABLE public.p3_alpha_job_commits ADD CONSTRAINT p3_output_custody_bound CHECK (
          (output_inventory_ref_text IS NULL AND output_attempt_id IS NULL) OR
          (output_inventory_ref_text IS NOT NULL AND output_attempt_id IS NOT NULL
           AND length(output_attempt_id) BETWEEN 1 AND 64
           AND output_inventory_ref_text=public.canonical_domain_json_string(output_inventory_ref_text)
           AND job_plane.p3_valid_ref(output_inventory_ref_text::jsonb)
           AND output_inventory_ref_text::jsonb->>'media_type'='application/json'
           AND (output_inventory_ref_text::jsonb->>'size_bytes')::numeric BETWEEN 1 AND 4194304) IS TRUE);
        """)
    for definition in (commit,read,custody):
        op.execute(definition)
    op.execute("""RESET ROLE;
        REVOKE CREATE ON SCHEMA job_plane FROM trading_p3_owner;
        REVOKE REFERENCES ON TABLE public.job_attempts FROM trading_p3_owner;""")


def downgrade():
    raise RuntimeError('0023 P3 output custody is forward-only; use a reviewed forward repair')

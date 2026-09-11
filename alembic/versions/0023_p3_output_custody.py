"""Bind official attempt outputs inside the existing atomic publication commit."""
from __future__ import annotations

import hashlib

from alembic import op
from sqlalchemy import text

revision = '0023_p3_output_custody'
down_revision = '0022_p3_worker_lane_isolation'
branch_labels = None
depends_on = None


def _reviewed(name, signature, digest, *, arguments, language, volatility, parallel, result, modes=None, security=True, worker=True):
    rows=op.get_bind().execute(text("""
        SELECT p.prosrc,pg_catalog.pg_get_functiondef(p.oid)
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE p.oid=pg_catalog.to_regprocedure(:signature)
          AND r.rolname='trading_p3_owner' AND p.prosecdef=:security AND p.provolatile=:volatility
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
            WHERE (a.grantee<>p.proowner AND NOT (:worker AND a.grantee=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='trading_job_worker')))
              OR a.privilege_type<>'EXECUTE' OR a.is_grantable)
          AND :worker = EXISTS (SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a
            WHERE a.grantee=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='trading_job_worker')
              AND a.privilege_type='EXECUTE')
    """),dict(signature='job_plane.'+name+'('+signature+')',arguments=arguments.split(','),
        language=language,volatility=volatility,parallel=parallel,result=result,modes=modes,
        returns_set=modes is not None,security=security,worker=worker)).fetchall()
    if len(rows)!=1 or hashlib.sha256(rows[0][0].encode()).hexdigest()!=digest:
        raise RuntimeError('0023 reviewed function drift: '+name)
    return rows[0][1]


_PARENT_CONSTRAINTS={'p3_alpha_job_commits_check': ('c', '828198534b5227f0916cb4a76ed633ecf99dad88bf226f8aa2300e6fa7a61c4a', True), 'p3_alpha_job_commits_job_id_fkey': ('f', '36739895ab79faa1eea76ce9e7d23591e487ba129000db3db6c87b9884685562', True), 'p3_alpha_job_commits_pkey': ('p', '1cfd28865f81d3ea9eee531f4f43fa1748e122a9e210b1902ad62aa6e5d3758a', True), 'p3_alpha_job_commits_result_digest_check': ('c', 'cda46c3ac56206f7e531d434b232133f028514ad9823f74c36a2b4125f9e860e', True), 'p3_alpha_job_commits_result_digest_key': ('u', 'abbffe4c964329484493278908c55ba7e6931bcd5f592d7c4f8615e923936f9e', True), 'p3_alpha_job_commits_result_text_check': ('c', 'af458e6adf09568bcf5840da21eb767b49d04a8047003b6ad37296120aa3e24f', True), 'p3_alpha_job_commits_semantic_request_digest_check': ('c', '59e290293f578ecfc73101cee176ba3349348aec1d35e899b09840ff1f4dfa59', True), 'p3_publication_custody': ('c', 'd810837f060a44c185b97f0c57ac97a4a1422d9b413fd4a9b130903ad2c0ea4c', True)}

def _catalog(*, upgraded=False):
    connection=op.get_bind()
    _reviewed('p3_valid_ref','jsonb',
        '1558714089bb906369acc7f9c1f005a039db1b87076d76c1f330d65caa78da5f',
        arguments='ref',language='sql',volatility='i',parallel='u',result='boolean',security=False,worker=False)
    trigger=connection.execute(text("""
        SELECT p.prosrc FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE p.oid='public.reject_p3_accepted_mutation()'::regprocedure
          AND r.rolname='trading_owner' AND l.lanname='plpgsql'
          AND NOT p.prosecdef AND NOT p.proisstrict AND NOT p.proleakproof
          AND p.provolatile='v' AND p.proparallel='u' AND p.prokind='f'
          AND NOT p.proretset AND p.pronargs=0 AND p.pronargdefaults=0
          AND p.proconfig=ARRAY['search_path=pg_catalog'] AND p.prorettype='trigger'::regtype
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
            WHERE a.grantee<>p.proowner OR a.privilege_type<>'EXECUTE' OR a.is_grantable)
    """)).scalar()
    if trigger is None or hashlib.sha256(trigger.encode()).hexdigest()!='051dd0e20f1712cc653430c1831be32ce9e78100d8599e1e3d74a998b732dd74':
        raise RuntimeError('0023 append-only function drift')
    if connection.execute(text("""
        SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname='trading_job_worker'
          AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
          AND NOT r.rolreplication AND NOT r.rolbypassrls
          AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid OR m.roleid=r.oid))
        AND EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
          WHERE c.oid='public.p3_alpha_job_commits'::regclass AND c.relkind='r'
          AND r.rolname='trading_p3_owner' AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
             WHERE a.grantee<>c.relowner)
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attacl IS NOT NULL)
          AND (SELECT count(*) FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)=1
          AND EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid
            AND t.tgname='p3_alpha_job_commits_append_only' AND t.tgenabled='O' AND t.tgtype=58
            AND t.tgfoid='public.reject_p3_accepted_mutation()'::regprocedure
            AND NOT t.tgisinternal AND t.tgnargs=0 AND t.tgqual IS NULL AND t.tgattr=''::int2vector))
    """)).scalar() is not True:
        raise RuntimeError('0023 publication table, trigger or worker authority drift')
    columns=connection.execute(text("""
        SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,
          pg_catalog.pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated
        FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attrelid='public.p3_alpha_job_commits'::regclass AND a.attnum>0 ORDER BY a.attnum
    """)).fetchall()
    expected=[('job_id','character varying(64)',True,None),('idempotency_key','character varying(128)',True,None),
        ('semantic_request_digest','character(64)',True,None),('result_json','jsonb',True,None),
        ('result_text','text',True,None),('result_digest','character(64)',True,None),
        ('committed_at','timestamp with time zone',True,'transaction_timestamp()'),('publication_request_text','text',False,None)]
    if upgraded:
        expected += [('output_inventory_ref_text','text',False,None),('output_attempt_id','character varying(64)',False,None)]
    if [tuple(row) for row in columns] != [(*row,'','') for row in expected]:
        raise RuntimeError('0023 publication columns drift')
    constraints=connection.execute(text("""
        SELECT c.conname,c.contype,pg_catalog.pg_get_constraintdef(c.oid),c.convalidated
        FROM pg_catalog.pg_constraint c WHERE c.conrelid='public.p3_alpha_job_commits'::regclass
          AND NOT c.condeferrable AND NOT c.condeferred AND NOT c.connoinherit
          AND c.conislocal AND c.coninhcount=0 AND c.conparentid=0
    """)).fetchall()
    expected_constraints=dict(_PARENT_CONSTRAINTS)
    if upgraded:
        expected_constraints.update({
            'p3_alpha_job_commits_output_attempt_id_fkey':('f',hashlib.sha256(b'FOREIGN KEY (output_attempt_id) REFERENCES job_attempts(attempt_id) ON DELETE RESTRICT').hexdigest(),True),
            'p3_output_custody_bound':('c','PENDING_REVIEWED_DEPARSE',True),
            'p3_output_custody_new_rows':('c',hashlib.sha256(b'CHECK (((output_inventory_ref_text IS NOT NULL) AND (output_attempt_id IS NOT NULL))) NOT VALID').hexdigest(),False),
        })
    actual={name:(kind,hashlib.sha256(definition.encode()).hexdigest(),validated)
        for name,kind,definition,validated in constraints}
    total=connection.execute(text("SELECT count(*) FROM pg_catalog.pg_constraint WHERE conrelid='public.p3_alpha_job_commits'::regclass")).scalar()
    if actual != expected_constraints or len(actual)!=total:
        raise RuntimeError('0023 publication constraints drift: '+repr(constraints))
    if connection.execute(text("""
        SELECT NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c
          LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=c.conindid
          WHERE c.conrelid='public.p3_alpha_job_commits'::regclass AND c.contype IN ('p','u','f')
            AND (i.indexrelid IS NULL OR NOT i.indisvalid OR NOT i.indisready OR NOT i.indisunique
              OR i.indpred IS NOT NULL OR i.indexprs IS NOT NULL
              OR (c.contype IN ('p','u') AND (i.indrelid<>c.conrelid OR i.indisprimary<>(c.contype='p')))
              OR (c.contype='f' AND (i.indrelid<>c.confrelid OR c.confdeltype<>'r' OR c.confupdtype<>'a'
                OR c.confmatchtype<>'s' OR c.confkey<>ARRAY(SELECT unnest(i.indkey))))))
    """)).scalar() is not True:
        raise RuntimeError('0023 publication constraint index or foreign key drift')



def _body_digest(definition):
    body=definition.partition('\nAS $function$')[2].rpartition('$function$')[0]
    if not body:
        raise RuntimeError('0023 reviewed function framing drift')
    return hashlib.sha256(body.encode()).hexdigest()


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
    op.execute('LOCK TABLE public.jobs IN SHARE ROW EXCLUSIVE MODE; SET LOCAL ROLE trading_p3_owner; LOCK TABLE public.p3_alpha_job_commits IN SHARE ROW EXCLUSIVE MODE; RESET ROLE')
    _catalog()
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
    custody=_replace(custody,'committed_at timestamp with time zone)',
        'committed_at timestamp with time zone, output_inventory_ref_text text, output_attempt_id text)')
    custody=_replace(custody,'SELECT c.publication_request_text,c.result_text,c.committed_at',
        'SELECT c.publication_request_text,c.result_text,c.committed_at,c.output_inventory_ref_text,c.output_attempt_id::text')
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
        ALTER TABLE public.p3_alpha_job_commits ADD CONSTRAINT p3_output_custody_new_rows
          CHECK (output_inventory_ref_text IS NOT NULL AND output_attempt_id IS NOT NULL) NOT VALID;
        """)
    op.execute('DROP FUNCTION job_plane.worker_read_alpha_publication(text)')
    for definition in (commit,read,custody):
        op.execute(definition)
    op.execute('REVOKE ALL ON FUNCTION job_plane.worker_read_alpha_publication(text) FROM PUBLIC,trading_p3_authority,trading_job_api,trading_job_worker,trading_job_scheduler,trading_jobs,trading_reader,trading_migrator')
    op.execute('GRANT EXECUTE ON FUNCTION job_plane.worker_read_alpha_publication(text) TO trading_job_worker')
    _reviewed('worker_commit_alpha_campaign','text,text,text,text,text,text',_body_digest(commit),
        arguments='p_job_id,p_attempt_id,p_worker_id,p_lease_token,p_request_text,p_trace_id',
        language='plpgsql',volatility='v',parallel='u',result='jsonb')
    _reviewed('read_alpha_commit','text,text,text',_body_digest(read),
        arguments='p_job_id,p_idempotency_key,p_semantic_request_digest',
        language='sql',volatility='s',parallel='s',result='jsonb')
    _reviewed('worker_read_alpha_publication','text',_body_digest(custody),
        arguments='p_job_id,request_text,result_text,committed_at,output_inventory_ref_text,output_attempt_id',language='plpgsql',
        volatility='s',parallel='u',result='TABLE(request_text text, result_text text, committed_at timestamp with time zone, output_inventory_ref_text text, output_attempt_id text)',
        modes=['i','t','t','t','t','t'])
    op.execute("""RESET ROLE;
        REVOKE CREATE ON SCHEMA job_plane FROM trading_p3_owner;
        REVOKE REFERENCES ON TABLE public.job_attempts FROM trading_p3_owner;""")
    _catalog(upgraded=True)
    if op.get_bind().execute(text("""SELECT current_user=session_user AND current_user='trading_owner'
        AND NOT pg_catalog.has_schema_privilege('trading_p3_owner','job_plane','CREATE')
        AND NOT pg_catalog.has_table_privilege('trading_p3_owner','public.job_attempts','REFERENCES')""")).scalar() is not True:
        raise RuntimeError('0023 temporary migration privileges drift')


def downgrade():
    raise RuntimeError('0023 P3 output custody is forward-only; use a reviewed forward repair')

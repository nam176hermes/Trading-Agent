"""Persist one fenced holdout disclosure; normal HOLDOUT execution stays closed."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from alembic import op
from sqlalchemy import text

from services.job_store.p3_catalog import AUTHORITY_CATALOG_SQL as _CATALOG_SQL

revision = '0026_p3_holdout_disclosure'
down_revision = '0025_p3_holdout_intent'
branch_labels = None
depends_on = None


def _parent(filename):
    spec=importlib.util.spec_from_file_location('p3_disclosure_'+filename,
        Path(__file__).with_name(filename+'.py'))
    if spec is None or spec.loader is None:
        raise RuntimeError('0026 reviewed migration helper unavailable')
    parent=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    return parent


def upgrade():
    connection=op.get_bind()
    if connection.execute(text("""SELECT current_user='trading_owner' AND session_user='trading_owner'
        AND (SELECT version_num FROM public.alembic_version)='0025_p3_holdout_intent'
        AND to_regclass('public.p3_holdout_disclosures') IS NULL
        AND NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='job_plane' AND p.proname IN ('worker_consume_p3_holdout','worker_read_p3_holdout_disclosure'))""")).scalar() is not True:
        raise RuntimeError('0026 requires the exact parent, owner and absent disclosure capabilities')
    parent=_parent('0023_p3_output_custody')
    _catalog(after=False)
    op.execute('''GRANT EXECUTE ON FUNCTION public.reject_p3_accepted_mutation() TO trading_p3_owner;
        GRANT REFERENCES ON TABLE public.job_attempts TO trading_p3_owner;
        GRANT CREATE ON SCHEMA public,job_plane TO trading_p3_owner; SET LOCAL ROLE trading_p3_owner''')
    op.execute("""
    CREATE TABLE public.p3_holdout_disclosures (
      intent_digest char(64) PRIMARY KEY,
      intent_sha256 char(64) NOT NULL UNIQUE,
      authorization_digest char(64) NOT NULL UNIQUE REFERENCES public.p3_operation_authorizations(authorization_digest) ON DELETE RESTRICT,
      job_id varchar(64) NOT NULL UNIQUE,
      attempt_id varchar(64) NOT NULL UNIQUE,
      worker_id varchar(128) NOT NULL,
      inputs_digest char(64) NOT NULL,
      holdout_request_text text NOT NULL,
      holdout_request_sha256 char(64) NOT NULL UNIQUE,
      research_input_set_sha256 char(64) NOT NULL,
      holdout_input_set_sha256 char(64) NOT NULL UNIQUE,
      epoch_id varchar(128) NOT NULL,
      family_digest char(64) NOT NULL,
      source_identity_text text NOT NULL,
      policy_digest char(64) NOT NULL,
      family_review_sha256 char(64) NOT NULL,
      primary_selection_sha256 char(64) NOT NULL UNIQUE,
      custody_record_sha256 char(64) NOT NULL UNIQUE,
      primary_alpha_id varchar(64) NOT NULL,
      primary_version text NOT NULL,
      primary_head_sha256 char(64) NOT NULL,
      holdout_commitment char(64) NOT NULL UNIQUE,
      plaintext_bundle_digest char(64) NOT NULL UNIQUE,
      access_policy_digest char(64) NOT NULL,
      custodian_identity varchar(128) NOT NULL,
      research_identity varchar(128) NOT NULL,
      trace_id varchar(128) NOT NULL,
      disclosed_at timestamptz NOT NULL,
      UNIQUE(epoch_id,family_digest),
      FOREIGN KEY(job_id,attempt_id) REFERENCES public.job_attempts(job_id,attempt_id) ON DELETE RESTRICT,
      CHECK (octet_length(holdout_request_text) BETWEEN 1 AND 65536
        AND holdout_request_text=public.canonical_domain_json_string(holdout_request_text)
        AND holdout_request_sha256=encode(sha256(convert_to(holdout_request_text,'UTF8')),'hex')),
      CHECK (source_identity_text=public.canonical_domain_json_string(source_identity_text)),
      CHECK (custodian_identity<>research_identity),
      CONSTRAINT p3_holdout_identifiers CHECK (
        job_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$' AND attempt_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
        AND worker_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' AND trace_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
        AND epoch_id ~ '^[a-z][a-z0-9._-]{0,127}$' AND primary_alpha_id ~ '^[a-z][a-z0-9._-]{0,63}$'
        AND primary_version ~ '^[0-9]+[.][0-9]+[.][0-9]+$'
        AND custodian_identity ~ '^[a-z][a-z0-9._-]{0,127}$' AND research_identity ~ '^[a-z][a-z0-9._-]{0,127}$')
    );
    CREATE TRIGGER p3_holdout_disclosures_append_only
      BEFORE UPDATE OR DELETE OR TRUNCATE ON public.p3_holdout_disclosures
      FOR EACH STATEMENT EXECUTE FUNCTION public.reject_p3_accepted_mutation();
    REVOKE ALL ON TABLE public.p3_holdout_disclosures FROM PUBLIC,trading_owner,trading_jobs,
      trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler,trading_p3_authority;
    """)
    for column in _DIGEST_COLUMNS:
        op.execute('ALTER TABLE public.p3_holdout_disclosures ADD CONSTRAINT p3_holdout_'+column+
            '_hex CHECK ('+column+" ~ '^[0-9a-f]{64}$')")
    for name,body,arguments,count in _FUNCTIONS:
        signature=','.join(['text']*count)
        op.execute('CREATE FUNCTION job_plane.'+name+'('+','.join(arg+' text' for arg in arguments.split(','))+''')
          RETURNS TABLE(authorization_digest text,intent_digest text,holdout_request_sha256 text,disclosed_at timestamptz)
          LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE SET search_path=pg_catalog
          AS $disclosure$'''+body+'$disclosure$;')
        op.execute('REVOKE ALL ON FUNCTION job_plane.'+name+'('+signature+''') FROM PUBLIC,trading_owner,
          trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler,trading_p3_authority;
          GRANT EXECUTE ON FUNCTION job_plane.'''+name+'('+signature+') TO trading_job_worker')
        parent._reviewed(name,signature,hashlib.sha256(body.encode()).hexdigest(),
            arguments=arguments+',authorization_digest,intent_digest,holdout_request_sha256,disclosed_at',
            language='plpgsql',volatility='v',parallel='u',
            result='TABLE(authorization_digest text, intent_digest text, holdout_request_sha256 text, disclosed_at timestamp with time zone)',
            modes=['i']*count+['t']*4)
    op.execute('''RESET ROLE; REVOKE CREATE ON SCHEMA public,job_plane FROM trading_p3_owner;
        REVOKE REFERENCES ON TABLE public.job_attempts FROM trading_p3_owner;
        REVOKE EXECUTE ON FUNCTION public.reject_p3_accepted_mutation() FROM trading_p3_owner''')
    _catalog()


def _catalog(*,after=True):
    """Pin schema semantics, never object OIDs or mutable job/disclosure rows."""
    connection=op.get_bind()
    parent=_parent('0023_p3_output_custody')
    parent._catalog(upgraded=True)
    _parent('0025_p3_holdout_intent')._authority_definition(
        '0b567c5b0cc63d1f853d2514a144e1d2e3eabc29389d0cd03626b96ed5d4c366')
    parent._reviewed('p3_checked_object','text,text,text[]',
        'd15cb5d2a69030a30a564ae3c503e1cea9768922e7075fb9dbb4c32bd35c699c',
        arguments='raw,schema_name,keys',language='plpgsql',volatility='i',parallel='u',
        result='jsonb',security=False,worker=False)
    parent._reviewed('p3_payload_authorized','jsonb,text',
        '819d2f2e5832a9cda0080bfaf4c3c4678ed0dcb013daf291df41d5c1042335a6',
        arguments='payload,bound_job',language='plpgsql',volatility='v',parallel='u',result='boolean',worker=False)
    parent._reviewed('p3_bind_operation_job','jsonb,text',
        '422f70a0b328ce46b75596be4789b0710b60658abab182f20ffd9f9160f34103',
        arguments='payload,bound_job',language='plpgsql',volatility='v',parallel='u',result='void',worker=False)
    api=connection.execute(text("""SELECT p.prosrc FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_roles r ON r.oid=p.proowner JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE p.oid=pg_catalog.to_regprocedure('job_plane.api_enqueue_alpha_campaign(text,text,text,text,text,integer,text,text)')
          AND r.rolname='trading_p3_owner' AND l.lanname='plpgsql' AND p.prosecdef
          AND p.provolatile='v' AND p.proparallel='u' AND p.prokind='f'
          AND NOT p.proisstrict AND NOT p.proleakproof AND p.pronargdefaults=0 AND p.proretset
          AND p.proconfig=ARRAY['search_path=pg_catalog']
          AND pg_catalog.pg_get_function_result(p.oid)='TABLE(job_id text, outcome text)'
          AND p.proargnames=ARRAY['p_job_id','p_payload_text','p_payload_fingerprint','p_idempotency_key',
            'p_actor_id','p_priority','p_trace_id','p_event_id','job_id','outcome']
          AND p.proargmodes::text[]=ARRAY['i','i','i','i','i','i','i','i','t','t']
          AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
            WHERE a.grantee NOT IN (p.proowner,(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='trading_job_api'))
              OR a.privilege_type<>'EXECUTE' OR a.is_grantable)
          AND EXISTS(SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a
            WHERE a.grantee=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='trading_job_api')
              AND a.privilege_type='EXECUTE')
          AND (SELECT count(*) FROM pg_catalog.pg_proc f JOIN pg_catalog.pg_namespace n ON n.oid=f.pronamespace
            WHERE n.nspname='job_plane' AND f.proname IN ('api_enqueue_alpha_campaign','p3_bind_operation_job'))=2
    """)).scalar()
    if api is None or hashlib.sha256(api.encode()).hexdigest()!='eeb65b8f58366da39149a84578472af7e2a22b4df27f68730d0dd32063ac37a1':
        raise RuntimeError('0026 reviewed enqueue authority drift')
    if after:
        if connection.execute(text("""SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='job_plane' AND p.proname IN ('worker_consume_p3_holdout','worker_read_p3_holdout_disclosure')""")).scalar()!=2:
            raise RuntimeError('0026 disclosure overload inventory drift')
        for name,body,arguments,count in _FUNCTIONS:
            parent._reviewed(name,','.join(['text']*count),hashlib.sha256(body.encode()).hexdigest(),
                arguments=arguments+',authorization_digest,intent_digest,holdout_request_sha256,disclosed_at',
                language='plpgsql',volatility='v',parallel='u',
                result='TABLE(authorization_digest text, intent_digest text, holdout_request_sha256 text, disclosed_at timestamp with time zone)',
                modes=['i']*count+['t']*4)
    previous=connection.execute(text('SHOW search_path')).scalar()
    connection.execute(text("SELECT set_config('search_path','pg_catalog',true)"))
    try:
        tables=['p3_campaign_authorizations','p3_operation_authorizations','p3_operation_job_bindings']
        if after:
            tables.append('p3_holdout_disclosures')
        # relhasrules is a lazy historical hint; inspect the actual rule inventory.
        if connection.execute(text("""WITH roles AS (
            SELECT * FROM pg_roles WHERE rolname IN
              ('trading_owner','trading_p3_owner','trading_p3_authority','trading_job_api','trading_job_worker'))
            SELECT NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='public' AND c.relname=ANY(CAST(:tables AS text[]))
                AND EXISTS(SELECT 1 FROM pg_rewrite w WHERE w.ev_class=c.oid))
              AND NOT EXISTS(SELECT 1 FROM roles WHERE rolvaliduntil IS NOT NULL)
              AND NOT EXISTS(SELECT 1 FROM pg_db_role_setting s
                WHERE s.setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database())
                  AND (s.setrole=0 OR s.setrole IN (SELECT oid FROM roles))
                  AND NOT (s.setrole=(SELECT oid FROM roles WHERE rolname='trading_owner')
                    AND s.setconfig=ARRAY['TimeZone=UTC']))
              AND EXISTS(SELECT 1 FROM pg_db_role_setting s
                WHERE s.setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database())
                  AND s.setrole=(SELECT oid FROM roles WHERE rolname='trading_owner')
                  AND s.setconfig=ARRAY['TimeZone=UTC'])
              AND EXISTS(SELECT 1 FROM roles r WHERE r.rolname='trading_job_api' AND r.rolcanlogin
                AND NOT r.rolsuper AND NOT r.rolinherit AND NOT r.rolcreatedb AND NOT r.rolcreaterole
                AND NOT r.rolreplication AND NOT r.rolbypassrls AND r.rolconnlimit=-1
                AND r.rolconfig=ARRAY['TimeZone=UTC']
                AND NOT EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.roleid=r.oid OR m.member=r.oid))
        """),{'tables':[*tables,'p3_alpha_job_commits','jobs','job_attempts',
            'job_events','domain_events','event_outbox']}).scalar() is not True:
            raise RuntimeError('0026 rewrite or role-setting authority drift')
        snapshot=connection.execute(text(_CATALOG_SQL),{'tables':tables}).scalar()
    finally:
        connection.execute(text("SELECT set_config('search_path',:value,true)"),{'value':previous})
    encoded=json.dumps(snapshot,sort_keys=True,separators=(',',':'),ensure_ascii=True)
    digest=hashlib.sha256(encoded.encode()).hexdigest()
    expected=_CATALOG_AFTER if after else _CATALOG_BEFORE
    if digest!=expected:
        raise RuntimeError('0026 reviewed authority catalog drift: '+digest+' '+encoded)


_CONSUME = r"""
    DECLARE j public.jobs%ROWTYPE; a public.job_attempts%ROWTYPE;
      auth jsonb; intent jsonb; q jsonb; ri jsonb; hi jsonb; f jsonb; p jsonb; c jsonb;
      binding record; ref jsonb; id_value text; checked_at timestamptz; request_sha text; inputs_sha text;
      previous_lock_timeout text;
      input_keys text[]:=ARRAY['schema_version','source','epoch_id','policy_digest','family_digest',
        'dataset_evidence_ref','fold_manifest_ref','pit_proof_ref','environment_ref','cost_model',
        'regime_threshold_ref','integration_receipt_ref','digest'];
    BEGIN
      IF session_user<>'trading_job_worker' THEN
        RAISE EXCEPTION 'P3 holdout disclosure worker rejected' USING ERRCODE='42501';
      END IF;
      FOREACH id_value IN ARRAY ARRAY[p_job_id,p_attempt_id] LOOP
        IF id_value IS NULL OR id_value !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$' THEN
          RAISE EXCEPTION 'P3 holdout disclosure identity rejected' USING ERRCODE='22023';
        END IF;
      END LOOP;
      IF p_worker_id IS NULL OR p_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
         OR p_lease_token IS NULL OR p_lease_token !~ '^[A-Za-z0-9_-]{16,128}$'
         OR p_trace_id IS NULL OR p_trace_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
        RAISE EXCEPTION 'P3 holdout disclosure claim rejected' USING ERRCODE='22023';
      END IF;
      q:=job_plane.p3_checked_object(p_holdout_request_text,'p3-holdout-request-v1',
        ARRAY['schema_version','source','primary_selection_ref','policy_digest','holdout_input_set_ref',
          'custody_record_ref','review_ref','issued_at','expires_at','logical_trial_id','authority','digest']);
      ri:=job_plane.p3_checked_object(p_research_input_set_text,'p3-input-set-v1',input_keys);
      hi:=job_plane.p3_checked_object(p_holdout_input_set_text,'p3-input-set-v1',input_keys);
      f:=job_plane.p3_checked_object(p_family_review_text,'p3-family-review-v1',
        ARRAY['schema_version','input_set_ref','candidate_report_refs','trial_outcome_refs','review_ref','complete_disclosure','digest']);
      p:=job_plane.p3_checked_object(p_primary_selection_text,'p3-primary-selection-v1',
        ARRAY['schema_version','family_review_ref','selection_policy_digest','primary_alpha_id','primary_version','primary_candidate_head_ref','outcome','digest']);
      c:=job_plane.p3_checked_object(p_custody_record_text,'p3-custody-record-v1',
        ARRAY['schema_version','holdout_commitment','ciphertext_ref','plaintext_bundle_digest','custodian_identity',
          'research_identity','custodian_attestation_ref','access_policy_digest','classification','digest']);
      previous_lock_timeout:=current_setting('lock_timeout');
      PERFORM set_config('lock_timeout',(SELECT least(coalesce(nullif(setting::integer,0),2000),2000)::text
        FROM pg_catalog.pg_settings WHERE name='lock_timeout'),true);
      SELECT * INTO j FROM public.jobs WHERE job_id=p_job_id FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'P3 holdout job missing' USING ERRCODE='22023'; END IF;
      SELECT * INTO a FROM public.job_attempts WHERE job_id=p_job_id AND attempt_id=p_attempt_id FOR UPDATE;
      IF NOT FOUND OR (j.job_type='ALPHA_CAMPAIGN' AND j.state='CLAIMED' AND a.outcome='CLAIMED'
          AND j.payload->>'operation'='HOLDOUT' AND j.payload->>'logical_trial_id'='p3-holdout-primary-v1'
          AND j.attempt_count=a.attempt_number AND j.lease_owner=p_worker_id AND a.worker_id=p_worker_id
          AND j.lease_token=p_lease_token AND a.lease_token=p_lease_token
          AND j.lease_expires_at>clock_timestamp() AND a.lease_expires_at>clock_timestamp()
          AND job_plane.p3_payload_authorized(j.payload,NULL)) IS NOT TRUE THEN
        RAISE EXCEPTION 'P3 holdout disclosure fence rejected' USING ERRCODE='22023';
      END IF;
      SELECT ca.authorization_text::jsonb,o.intent_text::jsonb INTO auth,intent
        FROM public.p3_operation_job_bindings b JOIN public.p3_operation_authorizations o USING(authorization_digest)
        JOIN public.p3_campaign_authorizations ca ON ca.request_digest=o.authorization_digest
        WHERE b.job_id=p_job_id AND b.authorization_digest=j.payload#>>'{authorization_ref,content_sha256}';
      IF NOT FOUND THEN RAISE EXCEPTION 'P3 holdout accepted job binding missing' USING ERRCODE='22023'; END IF;
      FOR binding IN SELECT * FROM (VALUES
          (auth->'input_set_ref',p_research_input_set_text),(intent->'input_set_ref',p_research_input_set_text),
          (f->'input_set_ref',p_research_input_set_text),
          (intent#>'{body,holdout_input_set_ref}',p_holdout_input_set_text),(q->'holdout_input_set_ref',p_holdout_input_set_text),
          (p->'family_review_ref',p_family_review_text),
          (intent#>'{body,primary_selection_ref}',p_primary_selection_text),(q->'primary_selection_ref',p_primary_selection_text),
          (intent#>'{body,custody_record_ref}',p_custody_record_text),(q->'custody_record_ref',p_custody_record_text)) AS refs(reference,raw) LOOP
        IF (job_plane.p3_valid_ref(binding.reference) AND binding.reference->>'media_type'='application/json'
            AND (binding.reference->>'size_bytes')::numeric=octet_length(binding.raw)
            AND binding.reference->>'content_sha256'=encode(sha256(convert_to(binding.raw,'UTF8')),'hex')) IS NOT TRUE THEN
          RAISE EXCEPTION 'P3 holdout referenced bytes differ' USING ERRCODE='22023';
        END IF;
      END LOOP;
      IF (q->'source'=j.payload->'expected_source' AND q->'source'=ri->'source' AND ri->'source'=hi->'source'
          AND ri->'epoch_id'=hi->'epoch_id' AND jsonb_typeof(ri->'epoch_id')='string'
          AND ri->>'epoch_id'~'^[a-z][a-z0-9._-]{0,127}$'
          AND ri->'family_digest'=hi->'family_digest' AND jsonb_typeof(ri->'family_digest')='string'
          AND ri->>'family_digest'~'^[0-9a-f]{64}$'
          AND q->'policy_digest'=intent#>'{body,policy_digest}' AND q->'policy_digest'=ri->'policy_digest'
          AND ri->'policy_digest'=hi->'policy_digest' AND q->'policy_digest'=p->'selection_policy_digest'
          AND ri->'environment_ref'=hi->'environment_ref' AND hi->'environment_ref'=intent#>'{body,environment_ref}'
          AND ri->'cost_model'=hi->'cost_model' AND ri->'regime_threshold_ref'=hi->'regime_threshold_ref'
          AND ri->'integration_receipt_ref'=hi->'integration_receipt_ref'
          AND hi->'dataset_evidence_ref'=intent#>'{body,holdout_dataset_ref}'
          AND f->'complete_disclosure'='true'::jsonb AND p->>'outcome'='SELECTED'
          AND jsonb_typeof(p->'primary_alpha_id')='string' AND p->'primary_alpha_id'=intent#>'{allowed_alpha_ids,0}'
          AND jsonb_typeof(p->'primary_version')='string' AND p->>'primary_version'~'^[0-9]+\.[0-9]+\.[0-9]+$'
          AND job_plane.p3_valid_ref(p->'primary_candidate_head_ref')
          AND c->>'classification'='HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND'
          AND jsonb_typeof(c->'custodian_identity')='string' AND c->>'custodian_identity'~'^[a-z][a-z0-9._-]{0,127}$'
          AND jsonb_typeof(c->'research_identity')='string' AND c->>'research_identity'~'^[a-z][a-z0-9._-]{0,127}$'
          AND c->'custodian_identity'<>c->'research_identity'
          AND jsonb_typeof(c->'holdout_commitment')='string' AND c->>'holdout_commitment'~'^[0-9a-f]{64}$'
          AND jsonb_typeof(c->'plaintext_bundle_digest')='string' AND c->>'plaintext_bundle_digest'~'^[0-9a-f]{64}$'
          AND jsonb_typeof(c->'access_policy_digest')='string' AND c->>'access_policy_digest'~'^[0-9a-f]{64}$'
          AND q->'review_ref'=auth->'review_ref' AND q->'issued_at'=auth->'issued_at' AND q->'expires_at'=auth->'expires_at'
          AND q->>'logical_trial_id'='p3-holdout-primary-v1' AND q->'authority'=auth->'authority') IS NOT TRUE THEN
        RAISE EXCEPTION 'P3 holdout metadata closure rejected' USING ERRCODE='22023';
      END IF;
      FOR ref IN SELECT value FROM jsonb_each(ri) WHERE right(key,4)='_ref'
          UNION ALL SELECT value FROM jsonb_each(hi) WHERE right(key,4)='_ref'
          UNION ALL SELECT value FROM jsonb_each(c) WHERE right(key,4)='_ref'
          UNION ALL SELECT f->'review_ref' LOOP
        IF NOT job_plane.p3_valid_ref(ref) THEN RAISE EXCEPTION 'P3 holdout metadata reference rejected' USING ERRCODE='22023'; END IF;
      END LOOP;
      IF (jsonb_typeof(f->'candidate_report_refs')='array' AND jsonb_array_length(f->'candidate_report_refs')=4
          AND jsonb_typeof(f->'trial_outcome_refs')='array' AND jsonb_array_length(f->'trial_outcome_refs') BETWEEN 1 AND 256) IS NOT TRUE THEN
        RAISE EXCEPTION 'P3 holdout family disclosure shape rejected' USING ERRCODE='22023';
      END IF;
      FOR ref IN SELECT value FROM jsonb_array_elements(f->'candidate_report_refs')
          UNION ALL SELECT value FROM jsonb_array_elements(f->'trial_outcome_refs') LOOP
        IF NOT job_plane.p3_valid_ref(ref) THEN RAISE EXCEPTION 'P3 holdout family reference rejected' USING ERRCODE='22023'; END IF;
      END LOOP;
      request_sha:=encode(sha256(convert_to(p_holdout_request_text,'UTF8')),'hex');
      inputs_sha:=encode(sha256(convert_to(public.canonical_domain_json(jsonb_build_array(q,ri,hi,f,p,c)),'UTF8')),'hex');
      checked_at:=clock_timestamp();
      INSERT INTO public.p3_holdout_disclosures(intent_digest,intent_sha256,authorization_digest,job_id,attempt_id,
          worker_id,inputs_digest,holdout_request_text,holdout_request_sha256,research_input_set_sha256,holdout_input_set_sha256,
          epoch_id,family_digest,source_identity_text,policy_digest,family_review_sha256,primary_selection_sha256,custody_record_sha256,
          primary_alpha_id,primary_version,primary_head_sha256,holdout_commitment,plaintext_bundle_digest,access_policy_digest,
          custodian_identity,research_identity,trace_id,disclosed_at)
        VALUES(intent->>'digest',j.payload#>>'{manifest_ref,content_sha256}',j.payload#>>'{authorization_ref,content_sha256}',p_job_id,p_attempt_id,
          p_worker_id,inputs_sha,p_holdout_request_text,request_sha,auth#>>'{input_set_ref,content_sha256}',q#>>'{holdout_input_set_ref,content_sha256}',
          ri->>'epoch_id',ri->>'family_digest',public.canonical_domain_json(q->'source'),q->>'policy_digest',p#>>'{family_review_ref,content_sha256}',
          q#>>'{primary_selection_ref,content_sha256}',q#>>'{custody_record_ref,content_sha256}',p->>'primary_alpha_id',p->>'primary_version',
          p#>>'{primary_candidate_head_ref,content_sha256}',c->>'holdout_commitment',c->>'plaintext_bundle_digest',c->>'access_policy_digest',
          c->>'custodian_identity',c->>'research_identity',p_trace_id,checked_at) ON CONFLICT DO NOTHING;
      -- Uniqueness can wait on another job. Reject expiry after that wait too.
      IF j.lease_expires_at<=clock_timestamp() OR a.lease_expires_at<=clock_timestamp()
          OR NOT job_plane.p3_payload_authorized(j.payload,NULL) THEN
        RAISE EXCEPTION 'P3 holdout disclosure expired while waiting' USING ERRCODE='22023';
      END IF;
      RETURN QUERY SELECT d.authorization_digest::text,d.intent_digest::text,d.holdout_request_sha256::text,d.disclosed_at
        FROM public.p3_holdout_disclosures d WHERE d.job_id=p_job_id AND d.attempt_id=p_attempt_id
          AND d.worker_id=p_worker_id AND d.inputs_digest=inputs_sha AND d.holdout_request_text=p_holdout_request_text
          AND d.trace_id=p_trace_id
          AND d.authorization_digest=j.payload#>>'{authorization_ref,content_sha256}' AND d.intent_digest=intent->>'digest';
      IF NOT FOUND THEN RAISE EXCEPTION 'P3 holdout disclosure already consumed' USING ERRCODE='23505'; END IF;
      PERFORM set_config('lock_timeout',previous_lock_timeout,true);
    END;
"""


_READ = """
    BEGIN
      IF session_user<>'trading_job_worker' THEN
        RAISE EXCEPTION 'P3 holdout disclosure reader rejected' USING ERRCODE='42501';
      END IF;
      -- Durable readback proves only the historical row. It grants no release.
      RETURN QUERY SELECT d.authorization_digest::text,d.intent_digest::text,d.holdout_request_sha256::text,d.disclosed_at
        FROM public.p3_holdout_disclosures d WHERE d.job_id=p_job_id AND d.attempt_id=p_attempt_id
          AND d.worker_id=p_worker_id AND d.authorization_digest=p_authorization_digest
          AND d.intent_digest=p_intent_digest AND d.holdout_request_sha256=p_holdout_request_sha256 AND d.trace_id=p_trace_id;
    END;
"""

_DIGEST_COLUMNS = ('intent_digest','intent_sha256','authorization_digest','inputs_digest',
    'holdout_request_sha256','research_input_set_sha256','holdout_input_set_sha256','family_digest',
    'policy_digest','family_review_sha256','primary_selection_sha256','custody_record_sha256',
    'primary_head_sha256','holdout_commitment','plaintext_bundle_digest','access_policy_digest')

_FUNCTIONS = (
    ('worker_consume_p3_holdout',_CONSUME,
     'p_job_id,p_attempt_id,p_worker_id,p_lease_token,p_holdout_request_text,p_research_input_set_text,p_holdout_input_set_text,p_family_review_text,p_primary_selection_text,p_custody_record_text,p_trace_id',11),
    ('worker_read_p3_holdout_disclosure',_READ,
     'p_job_id,p_attempt_id,p_worker_id,p_authorization_digest,p_intent_digest,p_holdout_request_sha256,p_trace_id',7),
)

# Filled only from the controlled PostgreSQL 16 source fixture and reviewed DDL.
# An unpinned snapshot fails migration; it can never qualify or open execution.
_CATALOG_BEFORE = '24f4f3d6c1efcf61bdb06fdc82bd9eb332515661a40da0c78168dea327861c60'
_CATALOG_AFTER = 'c0406bef686171a6f3d23c075b7d79e5916e97ed7c3d6d8aac571540ed846454'



def downgrade():
    raise RuntimeError('0026 disclosure history is forward-only; use a reviewed forward repair')

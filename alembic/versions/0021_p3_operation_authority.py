"""Bind P3 jobs to protected, reviewed operation intents; source authority only."""
from __future__ import annotations

import hashlib

from alembic import op
from sqlalchemy import text

revision = "0021_p3_operation_authority"
down_revision = "0020_p3_alpha_campaign_authority"
branch_labels = None
depends_on = None


def _replace(name: str, digest: str, replacements: tuple[tuple[str, str], ...]) -> None:
    signatures = {
        "api_enqueue_alpha_campaign": "text,text,text,text,text,integer,text,text",
        "worker_start_alpha_campaign": "text,text,text,text,bigint,bigint,bigint,text,text,text",
        "worker_finalize_alpha_campaign": "text,text,text,text,text,text,text,text,text,text,integer,text,text,jsonb,text,text,boolean,text,jsonb",
        "worker_commit_alpha_campaign": "text,text,text,text,text,text",
    }
    service = "trading_job_api" if name.startswith("api_") else "trading_job_worker"
    rows = op.get_bind().execute(text("""
        SELECT p.prosrc, pg_catalog.pg_get_functiondef(p.oid)
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
        WHERE p.oid=pg_catalog.to_regprocedure(:signature)
          AND r.rolname='trading_p3_owner' AND p.prosecdef AND p.provolatile='v'
          AND p.proparallel='u' AND p.proconfig=ARRAY['search_path=pg_catalog']
          AND NOT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb
          AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
            WHERE a.grantee NOT IN (p.proowner,(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:service))
               OR a.privilege_type<>'EXECUTE' OR a.is_grantable
          )
          AND EXISTS (SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a
                      WHERE a.grantee=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:service)
                        AND a.privilege_type='EXECUTE')
    """), {"signature": f"job_plane.{name}({signatures[name]})", "service": service}).fetchall()
    if len(rows) != 1 or hashlib.sha256(rows[0][0].encode()).hexdigest() != digest:
        raise RuntimeError(f"0021 reviewed function drift: {name}")
    body, definition = rows[0]
    for before, after in replacements:
        if body.count(before) != 1 or definition.count(before) != 1:
            raise RuntimeError(f"0021 replacement seam drift: {name}")
        body, definition = body.replace(before, after), definition.replace(before, after)
    op.execute(definition)


def upgrade() -> None:
    op.execute(r"""
    DO $preflight$
    BEGIN
      IF current_user <> 'trading_owner' OR session_user <> 'trading_owner'
         OR (SELECT version_num FROM public.alembic_version) IS DISTINCT FROM '0020_p3_alpha_campaign_authority'
         OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='trading_p3_authority'
           AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
           AND NOT rolreplication AND NOT rolbypassrls)
         OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r ON r.oid=m.member
                    WHERE r.rolname='trading_p3_authority')
         OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname IN
           ('trading_job_api','trading_job_worker','trading_job_scheduler','trading_jobs')
           AND pg_catalog.pg_has_role(rolname,'trading_p3_authority','MEMBER')) THEN
        RAISE EXCEPTION 'P3 protected operation authority unavailable' USING ERRCODE='P3D08';
      END IF;
    END;
    $preflight$;
    GRANT USAGE,CREATE ON SCHEMA public,job_plane TO trading_p3_owner;
    GRANT USAGE ON SCHEMA job_plane TO trading_p3_authority;
    GRANT REFERENCES ON TABLE public.jobs TO trading_p3_owner;
    GRANT EXECUTE ON FUNCTION public.reject_p3_accepted_mutation() TO trading_p3_owner;
    SET LOCAL ROLE trading_p3_owner;

    CREATE TABLE public.p3_operation_authorizations (
      authorization_digest char(64) PRIMARY KEY REFERENCES public.p3_campaign_authorizations(request_digest),
      intent_text text NOT NULL,
      review_text text NOT NULL,
      nonce uuid NOT NULL UNIQUE,
      accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK (octet_length(intent_text) BETWEEN 1 AND 65536),
      CHECK (octet_length(review_text) BETWEEN 1 AND 65536),
      CHECK (intent_text=public.canonical_domain_json_string(intent_text)),
      CHECK (review_text=public.canonical_domain_json_string(review_text))
    );
    CREATE TABLE public.p3_operation_job_bindings (
      authorization_digest char(64) PRIMARY KEY REFERENCES public.p3_operation_authorizations(authorization_digest),
      job_id varchar(64) NOT NULL UNIQUE REFERENCES public.jobs(job_id),
      bound_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TRIGGER p3_operation_authorizations_append_only
      BEFORE UPDATE OR DELETE OR TRUNCATE ON public.p3_operation_authorizations
      FOR EACH STATEMENT EXECUTE FUNCTION public.reject_p3_accepted_mutation();
    CREATE TRIGGER p3_operation_job_bindings_append_only
      BEFORE UPDATE OR DELETE OR TRUNCATE ON public.p3_operation_job_bindings
      FOR EACH STATEMENT EXECUTE FUNCTION public.reject_p3_accepted_mutation();

    CREATE FUNCTION job_plane.p3_checked_object(raw text, schema_name text, keys text[])
      RETURNS jsonb LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog
    AS $object$
    DECLARE value jsonb;
    BEGIN
      IF raw IS NULL OR octet_length(raw) NOT BETWEEN 1 AND 65536 THEN
        RAISE EXCEPTION 'P3 object size rejected' USING ERRCODE='22023';
      END IF;
      value:=raw::jsonb;
      IF (jsonb_typeof(value)='object' AND value ?& keys
          AND (SELECT count(*) FROM jsonb_object_keys(value))=cardinality(keys)
          AND value->>'schema_version'=schema_name
          AND raw=public.canonical_domain_json_string(raw)
          AND jsonb_typeof(value->'digest')='string' AND value->>'digest'~'^[0-9a-f]{64}$'
          AND value->>'digest'=encode(sha256(convert_to(public.canonical_domain_json(value-'digest'),'UTF8')),'hex')) IS NOT TRUE THEN
        RAISE EXCEPTION 'P3 canonical object rejected' USING ERRCODE='22023';
      END IF;
      RETURN value;
    END;
    $object$;

    CREATE FUNCTION job_plane.p3_valid_ref(ref jsonb) RETURNS boolean
      LANGUAGE sql IMMUTABLE SET search_path=pg_catalog
    AS $ref$
      SELECT coalesce(jsonb_typeof(ref)='object'
        AND ref=jsonb_build_object('content_sha256',ref->'content_sha256','size_bytes',ref->'size_bytes',
                                  'media_type',ref->'media_type','locator',ref->'locator')
        AND jsonb_typeof(ref->'content_sha256')='string' AND ref->>'content_sha256'~'^[0-9a-f]{64}$'
        AND jsonb_typeof(ref->'size_bytes')='number' AND ref->>'size_bytes'~'^(0|[1-9][0-9]*)$'
        AND (ref->>'size_bytes')::numeric<=1099511627776
        AND jsonb_typeof(ref->'media_type')='string' AND length(ref->>'media_type') BETWEEN 1 AND 128
        AND ref->>'locator'=(ref->>'content_sha256')||'.blob',false)
    $ref$;

    CREATE FUNCTION job_plane.accept_p3_operation_authorization(auth_text text,intent_text text,review_text text)
      RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE SET search_path=pg_catalog
    AS $accept$
    DECLARE a jsonb; i jsonb; r jsonb; source jsonb; field record; ref jsonb;
      auth_sha text; review_sha text; expected_operation text; expected_keys text[]; expected_ids jsonb;
      family jsonb:='["a0.donchian-20-10-close-confirm","a1.dual-sma-50-200","a2.zscore-20-long-reversion","a3.tsmom-21-63-126"]';
      checked_at timestamptz;
    BEGIN
      IF session_user<>'trading_p3_authority' THEN
        RAISE EXCEPTION 'P3 acceptance authority rejected' USING ERRCODE='42501';
      END IF;
      a:=job_plane.p3_checked_object(auth_text,'p3-run-authorization-v1',ARRAY['schema_version','input_set_ref','review_ref','operation','allowed_alpha_ids','issued_at','expires_at','nonce','issuer_workflow','issuer_run_id','issuer_attempt','authority','digest']);
      i:=job_plane.p3_checked_object(intent_text,'p3-operation-input-v1',ARRAY['schema_version','workflow_operation','operation','input_set_ref','allowed_alpha_ids','body','digest']);
      r:=job_plane.p3_checked_object(review_text,'p3-review-approval-v1',ARRAY['schema_version','source','subject_digests','operator_identity','reviewer_identity','review_execution_id','verdict','issued_at','expires_at','evidence_ref','authority','digest']);
      auth_sha:=encode(sha256(convert_to(auth_text,'UTF8')),'hex');
      review_sha:=encode(sha256(convert_to(review_text,'UTF8')),'hex');
      source:=r->'source';
      -- DigestModel serialization uses seconds or six microsecond digits.
      -- Parsing shorter fractions is allowed upstream, but their canonical bytes normalize.
      FOR ref IN SELECT value FROM jsonb_array_elements(jsonb_build_array(
          a->'issued_at',a->'expires_at',r->'issued_at',r->'expires_at')) LOOP
        IF (jsonb_typeof(ref)='string' AND (ref#>>'{}')~'^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{6})?Z$') IS NOT TRUE THEN
          RAISE EXCEPTION 'P3 canonical UTC time rejected' USING ERRCODE='22023';
        END IF;
      END LOOP;
      IF jsonb_typeof(source) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'P3 source object rejected' USING ERRCODE='22023';
      END IF;
      IF EXISTS (SELECT 1 FROM jsonb_each(source) WHERE jsonb_typeof(value)<>'string')
         OR jsonb_typeof(r->'subject_digests') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'P3 source or review field type rejected' USING ERRCODE='22023';
      END IF;
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(r->'subject_digests') d
                 WHERE (jsonb_typeof(d)='string' AND (d#>>'{}')~'^[0-9a-f]{64}$') IS NOT TRUE) THEN
        RAISE EXCEPTION 'P3 review subject digest rejected' USING ERRCODE='22023';
      END IF;
      checked_at:=clock_timestamp();
      IF (a->'authority'=jsonb_build_object('broker',false,'live',false,'network',false,'production',false)
          AND r->'authority'=a->'authority' AND r->>'verdict'='APPROVED'
          AND jsonb_typeof(r->'operator_identity')='string' AND r->>'operator_identity'~'^[a-z][a-z0-9._-]{0,127}$'
          AND jsonb_typeof(r->'reviewer_identity')='string' AND r->>'reviewer_identity'~'^[a-z][a-z0-9._-]{0,127}$'
          AND jsonb_typeof(r->'review_execution_id')='string' AND r->>'review_execution_id'~'^[a-z][a-z0-9._-]{0,127}$'
          AND r->>'operator_identity'<>r->>'reviewer_identity'
          AND jsonb_typeof(r->'subject_digests')='array'
          AND jsonb_array_length(r->'subject_digests') BETWEEN 1 AND 64
          AND r->'subject_digests' ? (i->>'digest')
          AND a->'input_set_ref'=i->'input_set_ref'
          AND job_plane.p3_valid_ref(a->'input_set_ref')
          AND job_plane.p3_valid_ref(a->'review_ref') AND job_plane.p3_valid_ref(r->'evidence_ref')
          AND a#>>'{review_ref,content_sha256}'=review_sha
          AND (a#>>'{review_ref,size_bytes}')::numeric=octet_length(review_text)
          AND a->'operation'=i->'operation' AND a->'allowed_alpha_ids'=i->'allowed_alpha_ids'
          AND a->>'issuer_workflow'='p3-authority.yml'
          AND jsonb_typeof(a->'issuer_run_id')='number' AND a->>'issuer_run_id'~'^[1-9][0-9]*$'
          AND jsonb_typeof(a->'issuer_attempt')='number' AND a->>'issuer_attempt'~'^[1-9][0-9]*$'
          AND a->>'nonce'~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
          AND (r->>'issued_at')::timestamptz<=(a->>'issued_at')::timestamptz
          AND (a->>'issued_at')::timestamptz<=checked_at AND checked_at<(a->>'expires_at')::timestamptz
          AND (a->>'expires_at')::timestamptz<=(r->>'expires_at')::timestamptz
          AND (a->>'expires_at')::timestamptz<=(a->>'issued_at')::timestamptz+interval '24 hours'
          AND source=jsonb_build_object('commit_sha',source->'commit_sha','tree_sha',source->'tree_sha',
            'closure_schema_version',source->'closure_schema_version','closure_policy_sha256',source->'closure_policy_sha256','closure_sha256',source->'closure_sha256')
          AND source->>'commit_sha'~'^[0-9a-f]{40}$' AND source->>'tree_sha'~'^[0-9a-f]{40}$'
          AND length(source->>'closure_schema_version') BETWEEN 1 AND 512
          AND source->>'closure_policy_sha256'~'^[0-9a-f]{64}$' AND source->>'closure_sha256'~'^[0-9a-f]{64}$') IS NOT TRUE THEN
        RAISE EXCEPTION 'P3 operation review or authorization rejected' USING ERRCODE='22023';
      END IF;
      CASE i->>'workflow_operation'
        WHEN 'p3-baselines-v1' THEN expected_operation:='BASELINES'; expected_ids:='[]'; expected_keys:=ARRAY['baseline_manifest_ref'];
        WHEN 'p3-register-family-v1' THEN expected_operation:='REGISTER_FAMILY'; expected_ids:=family; expected_keys:=ARRAY['baseline_selection_ref','candidate_spec_refs','candidate_record_refs'];
        WHEN 'p3-select-primary-v1' THEN expected_operation:='OOS'; expected_ids:=family; expected_keys:=ARRAY['family_review_ref'];
        WHEN 'p3-oos-a0-v1','p3-oos-a1-v1','p3-oos-a2-v1','p3-oos-a3-v1' THEN
          expected_operation:='OOS'; expected_ids:=jsonb_build_array(family->(substr(i->>'workflow_operation',9,1)::integer)); expected_keys:=ARRAY['evaluation_manifest_ref'];
        WHEN 'p3-holdout-primary-v1' THEN expected_operation:='HOLDOUT'; expected_keys:=ARRAY['primary_selection_ref','candidate_spec_ref','registration_proof_ref','custody_record_ref','holdout_input_set_ref','holdout_dataset_ref','context_dataset_ref','buffer_ref','environment_ref','policy_digest'];
        WHEN 'p3-native-parity-v1' THEN expected_operation:='PARITY'; expected_keys:=ARRAY['holdout_manifest_ref','primary_reference_ref','baseline_reference_ref','instrument_spec_ref','native_request_ref'];
        WHEN 'p3-phase-exit-v1' THEN expected_operation:='PHASE_EXIT'; expected_keys:=ARRAY['primary_selection_ref','primary_qualification_ref','baseline_selection_ref','holdout_request_ref','holdout_evaluation_ref','holdout_replay_ref','executable_ref','baseline_executable_ref','parity_ref','current_primary_head_ref'];
        ELSE RAISE EXCEPTION 'P3 workflow operation rejected' USING ERRCODE='22023';
      END CASE;
      IF expected_ids IS NULL THEN
        IF (jsonb_typeof(i->'allowed_alpha_ids')='array' AND jsonb_array_length(i->'allowed_alpha_ids')=1
            AND family ? (i#>>'{allowed_alpha_ids,0}')) IS NOT TRUE THEN
          RAISE EXCEPTION 'P3 primary alpha scope rejected' USING ERRCODE='22023';
        END IF;
        expected_ids:=i->'allowed_alpha_ids';
      END IF;
      IF (i->>'operation'=expected_operation AND i->'allowed_alpha_ids'=expected_ids
          AND jsonb_typeof(i->'body')='object' AND (i->'body') ?& expected_keys
          AND (SELECT count(*) FROM jsonb_object_keys(i->'body'))=cardinality(expected_keys)) IS NOT TRUE THEN
        RAISE EXCEPTION 'P3 operation intent scope rejected' USING ERRCODE='22023';
      END IF;
      FOR field IN SELECT * FROM jsonb_each(i->'body') LOOP
        IF field.key='policy_digest' THEN
          IF (jsonb_typeof(field.value)='string' AND field.value#>>'{}'~'^[0-9a-f]{64}$') IS NOT TRUE THEN
            RAISE EXCEPTION 'P3 policy digest rejected' USING ERRCODE='22023';
          END IF;
        ELSIF right(field.key,5)='_refs' THEN
          IF (jsonb_typeof(field.value)='array' AND jsonb_array_length(field.value)=4) IS NOT TRUE THEN
            RAISE EXCEPTION 'P3 family roots rejected' USING ERRCODE='22023';
          END IF;
          FOR ref IN SELECT value FROM jsonb_array_elements(field.value) LOOP
            IF NOT job_plane.p3_valid_ref(ref) THEN RAISE EXCEPTION 'P3 reference rejected' USING ERRCODE='22023'; END IF;
          END LOOP;
        ELSIF NOT job_plane.p3_valid_ref(field.value) THEN
          RAISE EXCEPTION 'P3 reference rejected' USING ERRCODE='22023';
        END IF;
      END LOOP;
      INSERT INTO public.p3_campaign_authorizations(request_digest,input_set_digest,source_commit_sha,source_identity_text,operation,expires_at,authorization_text,accepted_at)
        VALUES(auth_sha,a#>>'{input_set_ref,content_sha256}',source->>'commit_sha',public.canonical_domain_json(source),a->>'operation',(a->>'expires_at')::timestamptz,auth_text,checked_at)
        ON CONFLICT DO NOTHING;
      IF NOT EXISTS (SELECT 1 FROM public.p3_campaign_authorizations c WHERE c.request_digest=auth_sha
          AND c.input_set_digest=a#>>'{input_set_ref,content_sha256}' AND c.source_identity_text=public.canonical_domain_json(source)
          AND c.operation=a->>'operation' AND c.expires_at=(a->>'expires_at')::timestamptz AND c.authorization_text=auth_text) THEN
        RAISE EXCEPTION 'P3 accepted authorization conflict' USING ERRCODE='23505';
      END IF;
      INSERT INTO public.p3_operation_authorizations(authorization_digest,intent_text,review_text,nonce,accepted_at)
        VALUES(auth_sha,intent_text,review_text,(a->>'nonce')::uuid,clock_timestamp()) ON CONFLICT DO NOTHING;
      IF NOT EXISTS (SELECT 1 FROM public.p3_operation_authorizations o WHERE o.authorization_digest=auth_sha
          AND o.intent_text=accept_p3_operation_authorization.intent_text AND o.review_text=accept_p3_operation_authorization.review_text
          AND o.nonce=(a->>'nonce')::uuid) THEN
        RAISE EXCEPTION 'P3 accepted operation conflict' USING ERRCODE='23505';
      END IF;
      IF clock_timestamp()>=(a->>'expires_at')::timestamptz THEN
        RAISE EXCEPTION 'P3 acceptance expired while waiting' USING ERRCODE='22023';
      END IF;
      RETURN auth_sha;
    END;
    $accept$;
    """)
    op.execute(r"""
    CREATE FUNCTION job_plane.p3_payload_authorized(payload jsonb, bound_job text)
      RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE SET search_path=pg_catalog
    AS $allowed$
    BEGIN
      IF payload->>'logical_trial_id'='p3-integration-fixture-v1' AND payload->>'operation'='PARITY' THEN
        RETURN EXISTS (SELECT 1 FROM public.p3_campaign_authorizations a
          WHERE a.request_digest=payload#>>'{authorization_ref,content_sha256}'
            AND a.input_set_digest=payload#>>'{manifest_ref,content_sha256}'
            AND a.operation='PARITY' AND a.source_identity_text=public.canonical_domain_json(payload->'expected_source')
            AND a.expires_at>clock_timestamp());
      END IF;
      RETURN EXISTS (
        SELECT 1 FROM public.p3_operation_authorizations o
        JOIN public.p3_campaign_authorizations a ON a.request_digest=o.authorization_digest
        WHERE a.request_digest=payload#>>'{authorization_ref,content_sha256}'
          AND job_plane.p3_valid_ref(payload->'authorization_ref')
          AND (payload#>>'{authorization_ref,size_bytes}')::numeric=octet_length(a.authorization_text)
          AND job_plane.p3_valid_ref(payload->'manifest_ref')
          AND payload#>>'{manifest_ref,content_sha256}'=encode(sha256(convert_to(o.intent_text,'UTF8')),'hex')
          AND (payload#>>'{manifest_ref,size_bytes}')::numeric=octet_length(o.intent_text)
          AND a.input_set_digest=o.intent_text::jsonb#>>'{input_set_ref,content_sha256}'
          AND payload->>'logical_trial_id'=o.intent_text::jsonb->>'workflow_operation'
          AND payload->>'operation'=a.operation
          AND a.source_identity_text=public.canonical_domain_json(payload->'expected_source')
          AND (a.authorization_text::jsonb->>'issued_at')::timestamptz<=clock_timestamp()
          AND a.expires_at>clock_timestamp()
          AND (bound_job IS NULL OR (a.operation<>'HOLDOUT' AND EXISTS (
            SELECT 1 FROM public.p3_operation_job_bindings b
            WHERE b.authorization_digest=o.authorization_digest AND b.job_id=bound_job)))
      );
    END;
    $allowed$;

    CREATE FUNCTION job_plane.p3_bind_operation_job(payload jsonb, bound_job text)
      RETURNS void LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE SET search_path=pg_catalog
    AS $bind$
    DECLARE auth_sha text:=payload#>>'{authorization_ref,content_sha256}';
    BEGIN
      IF payload->>'logical_trial_id'='p3-integration-fixture-v1' AND payload->>'operation'='PARITY' THEN RETURN; END IF;
      INSERT INTO public.p3_operation_job_bindings(authorization_digest,job_id)
        VALUES(auth_sha,bound_job) ON CONFLICT DO NOTHING;
      IF NOT EXISTS (SELECT 1 FROM public.p3_operation_job_bindings b
          WHERE b.authorization_digest=auth_sha AND b.job_id=bound_job) THEN
        RAISE EXCEPTION 'P3 authorization already belongs to another job' USING ERRCODE='23505';
      END IF;
    END;
    $bind$;

    CREATE FUNCTION job_plane.p3_publication_authorized(payload jsonb,bound_job text,request jsonb,entries jsonb)
      RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE SET search_path=pg_catalog
    AS $publication$
    DECLARE intent jsonb; expected_stage text;
    BEGIN
      IF NOT job_plane.p3_payload_authorized(payload,bound_job) THEN RETURN false; END IF;
      SELECT o.intent_text::jsonb INTO intent FROM public.p3_operation_authorizations o
        WHERE o.authorization_digest=payload#>>'{authorization_ref,content_sha256}';
      IF NOT FOUND THEN RETURN false; END IF;
      CASE intent->>'workflow_operation'
        WHEN 'p3-register-family-v1' THEN expected_stage:='REGISTER';
        WHEN 'p3-oos-a0-v1','p3-oos-a1-v1','p3-oos-a2-v1','p3-oos-a3-v1' THEN expected_stage:='RESEARCH_DECISION';
        WHEN 'p3-phase-exit-v1' THEN expected_stage:='EXIT_DECISION';
        ELSE RETURN false;
      END CASE;
      RETURN coalesce(request->>'stage'=expected_stage
        AND job_plane.p3_valid_ref(request->'evidence_ref')
        AND jsonb_array_length(request->'proposed_event_refs')=jsonb_array_length(entries)
        AND NOT EXISTS (
          SELECT 1 FROM jsonb_array_elements(entries) WITH ORDINALITY e(value,position)
          JOIN jsonb_array_elements(request->'proposed_event_refs') WITH ORDINALITY r(value,position)
            USING (position)
          WHERE NOT job_plane.p3_valid_ref(r.value)
            OR r.value->>'content_sha256' IS DISTINCT FROM (e.value->>'canonical_event_text')::jsonb#>>'{payload,registry_event_sha256}'
            OR r.value->>'media_type' IS DISTINCT FROM 'application/json'
            OR r.value->>'size_bytes' IS DISTINCT FROM octet_length(convert_to(
                 (e.value->>'canonical_event_text')::jsonb#>>'{payload,registry_event_text}','UTF8'))::text)
        AND (SELECT jsonb_agg(h->'alpha_id' ORDER BY h->>'alpha_id') FROM jsonb_array_elements(request->'expected_heads') h)=intent->'allowed_alpha_ids'
        AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(request->'expected_heads') h WHERE h->>'version' IS DISTINCT FROM '1.0.0')
        AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(entries) e
          WHERE ((intent->'allowed_alpha_ids') ? ((e->>'canonical_event_text')::jsonb#>>'{payload,alpha_id}')) IS NOT TRUE
            OR (e->>'canonical_event_text')::jsonb#>>'{payload,evidence_sha256}' IS DISTINCT FROM request#>>'{evidence_ref,content_sha256}'
            OR (e->>'canonical_event_text')::jsonb#>>'{payload,alpha_version}' IS DISTINCT FROM '1.0.0'
            OR ((e->>'canonical_event_text')::jsonb#>>'{payload,registry_event_text}')::jsonb#>>'{record,source_sha}'
               IS DISTINCT FROM payload#>>'{expected_source,commit_sha}'),false);
    END;
    $publication$;

    -- Extend the existing atomic commit row. Legacy rows stay immutable and HELD
    -- for receipt recovery because they did not retain their complete request.
    ALTER TABLE public.p3_alpha_job_commits ADD COLUMN publication_request_text text;
    ALTER TABLE public.p3_alpha_job_commits ADD CONSTRAINT p3_publication_custody CHECK (
      publication_request_text IS NULL OR (
        job_plane.p3_checked_object(publication_request_text,'p3-publication-request-v1',
          ARRAY['schema_version','idempotency_key','semantic_request_digest','stage','evidence_ref','expected_heads','proposed_event_refs','job_id','digest']) IS NOT NULL
        AND publication_request_text::jsonb->>'job_id'=job_id
        AND publication_request_text::jsonb->>'idempotency_key'=idempotency_key
        AND publication_request_text::jsonb->>'semantic_request_digest'=semantic_request_digest
      ) IS TRUE
    );
    CREATE FUNCTION job_plane.worker_read_alpha_publication(p_job_id text)
      RETURNS TABLE(request_text text,result_text text,committed_at timestamptz)
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
    AS $custody$
    BEGIN
      IF session_user<>'trading_job_worker' THEN
        RAISE EXCEPTION 'P3 publication reader session rejected' USING ERRCODE='42501';
      END IF;
      IF (SELECT count(*) FROM public.p3_alpha_job_commits c WHERE c.job_id=p_job_id)<>1 THEN
        RAISE EXCEPTION 'P3 publication custody is missing or ambiguous' USING ERRCODE='P3D07';
      END IF;
      RETURN QUERY SELECT c.publication_request_text,c.result_text,c.committed_at
        FROM public.p3_alpha_job_commits c JOIN public.jobs j ON j.job_id=c.job_id
        WHERE c.job_id=p_job_id AND j.state='SUCCEEDED' AND j.reason_code='P3_COMMITTED'
          AND j.result_hash=c.result_digest AND j.result_metadata=c.result_json;
    END;
    $custody$;
    ALTER FUNCTION job_plane.worker_read_alpha_publication(text) OWNER TO trading_p3_owner;
    REVOKE ALL ON FUNCTION job_plane.worker_read_alpha_publication(text)
      FROM PUBLIC,trading_p3_authority,trading_job_api,trading_job_worker,trading_job_scheduler,trading_jobs,trading_reader,trading_migrator;
    GRANT EXECUTE ON FUNCTION job_plane.worker_read_alpha_publication(text) TO trading_job_worker;

    ALTER TABLE public.p3_operation_authorizations OWNER TO trading_p3_owner;
    ALTER TABLE public.p3_operation_job_bindings OWNER TO trading_p3_owner;
    REVOKE ALL ON TABLE public.p3_operation_authorizations,public.p3_operation_job_bindings
      FROM PUBLIC,trading_p3_authority,trading_job_api,trading_job_worker,trading_job_scheduler,trading_jobs,trading_reader,trading_migrator;
    ALTER FUNCTION job_plane.p3_checked_object(text,text,text[]) OWNER TO trading_p3_owner;
    ALTER FUNCTION job_plane.p3_valid_ref(jsonb) OWNER TO trading_p3_owner;
    ALTER FUNCTION job_plane.accept_p3_operation_authorization(text,text,text) OWNER TO trading_p3_owner;
    ALTER FUNCTION job_plane.p3_payload_authorized(jsonb,text) OWNER TO trading_p3_owner;
    ALTER FUNCTION job_plane.p3_bind_operation_job(jsonb,text) OWNER TO trading_p3_owner;
    ALTER FUNCTION job_plane.p3_publication_authorized(jsonb,text,jsonb,jsonb) OWNER TO trading_p3_owner;
    REVOKE ALL ON FUNCTION job_plane.p3_checked_object(text,text,text[]),job_plane.p3_valid_ref(jsonb),
      job_plane.accept_p3_operation_authorization(text,text,text),job_plane.p3_payload_authorized(jsonb,text),
      job_plane.p3_bind_operation_job(jsonb,text),job_plane.p3_publication_authorized(jsonb,text,jsonb,jsonb)
      FROM PUBLIC,trading_p3_authority,trading_job_api,trading_job_worker,trading_job_scheduler,trading_jobs,trading_reader,trading_migrator;
    GRANT EXECUTE ON FUNCTION job_plane.accept_p3_operation_authorization(text,text,text) TO trading_p3_authority;
    """)
    _replace("api_enqueue_alpha_campaign", "4b8c6189a3beae4b777c46f3fcc4651de6a81ae78be1a28f396f48c0f2789f89", (
        ("a.input_set_digest = v_payload #>> '{manifest_ref,content_sha256}'",
         "job_plane.p3_payload_authorized(v_payload,NULL)"),
        ("          INSERT INTO public.jobs(", """          IF v_payload->>'logical_trial_id'<>'p3-integration-fixture-v1' AND NOT EXISTS (
            SELECT 1 FROM public.p3_operation_authorizations o
            WHERE o.authorization_digest=v_payload#>>'{authorization_ref,content_sha256}'
              AND p_actor_id=o.review_text::jsonb->>'operator_identity'
              AND p_idempotency_key='p3:'||(o.intent_text::jsonb->>'workflow_operation')||':'||o.nonce::text
          ) THEN RAISE EXCEPTION 'P3 enqueue actor or nonce rejected' USING ERRCODE='22023'; END IF;
          INSERT INTO public.jobs("""),
        ("            job_id := v_inserted;", "            PERFORM job_plane.p3_bind_operation_job(v_payload,v_inserted);\n            IF NOT job_plane.p3_payload_authorized(v_payload,NULL) THEN RAISE EXCEPTION 'P3 enqueue authorization expired' USING ERRCODE='22023'; END IF;\n            job_id := v_inserted;"),
        ("          job_id := v_existing.job_id;", "          PERFORM job_plane.p3_bind_operation_job(v_payload,v_existing.job_id);\n            IF NOT job_plane.p3_payload_authorized(v_payload,NULL) THEN RAISE EXCEPTION 'P3 enqueue authorization expired' USING ERRCODE='22023'; END IF;\n          job_id := v_existing.job_id;"),
    ))
    boundary = "          SELECT coalesce(max(event_row.sequence), 0) + 1"
    fence = """          IF NOT job_plane.p3_payload_authorized(current_job.payload,p_job_id)
             OR current_job.lease_expires_at IS NULL OR current_job.lease_expires_at<=clock_timestamp()
             OR current_attempt.lease_expires_at IS NULL OR current_attempt.lease_expires_at<=clock_timestamp() THEN
            RETURN false;
          END IF;
"""
    _replace("worker_start_alpha_campaign", "b578989ce143315f14ebcbd6e5531f8a901370d5e0b7d8fbf8ef2e54a6a9e286", ((boundary, fence+boundary),))
    _replace("worker_finalize_alpha_campaign", "7c767d86d6fd615a25a3846a22504b7627ec2812f05fc44a47107b8f7eacd7ab", (
        ("          IF p_retry AND current_job.attempt_count", """          IF p_retry THEN RETURN false; END IF;
          IF p_final_state='SUCCEEDED' THEN
""" + fence + """            IF current_job.payload->>'logical_trial_id' IN ('p3-register-family-v1','p3-oos-a0-v1','p3-oos-a1-v1','p3-oos-a2-v1','p3-oos-a3-v1','p3-phase-exit-v1') THEN RETURN false; END IF;
          END IF;
          IF p_retry AND current_job.attempt_count"""),
    ))
    publication_fence = """          IF v_job.lease_expires_at<=clock_timestamp() OR v_attempt.lease_expires_at<=clock_timestamp()
             OR NOT job_plane.p3_publication_authorized(v_job.payload,p_job_id,v_request,v_transport->'entries') THEN
            RAISE EXCEPTION 'P3 publication fence or operation expired' USING ERRCODE='P3D03';
          END IF;
"""
    _replace("worker_commit_alpha_campaign", "ed27b67489d3af9f5f8304858fe3fbb134fa4722808aaeccf9686e049cc3279e", (
        ("P3 source authorization expired", "P3 source or operation publication authority rejected"),
        ("IF v_existing.semantic_request_digest<>v_request->>'semantic_request_digest' THEN",
         "IF v_existing.semantic_request_digest<>v_request->>'semantic_request_digest' OR v_existing.publication_request_text IS DISTINCT FROM public.canonical_domain_json(v_request) THEN"),
        ("a.input_set_digest=v_job.payload#>>'{manifest_ref,content_sha256}'",
         "job_plane.p3_publication_authorized(v_job.payload,p_job_id,v_request,v_transport->'entries')"),
        ("          FOR v_entry IN SELECT value", publication_fence+"          FOR v_entry IN SELECT value"),
        ("          v_result:=pg_catalog.jsonb_build_object(", publication_fence+"          v_result:=pg_catalog.jsonb_build_object("),
        ("semantic_request_digest,result_json,result_text,result_digest)",
         "semantic_request_digest,result_json,result_text,result_digest,publication_request_text,committed_at)"),
        ("            v_result,v_result_text,v_result_digest);",
         "            v_result,v_result_text,v_result_digest,public.canonical_domain_json(v_request),clock_timestamp());"),
    ))
    op.execute("RESET ROLE; REVOKE CREATE ON SCHEMA public,job_plane FROM trading_p3_owner; REVOKE REFERENCES ON TABLE public.jobs FROM trading_p3_owner; REVOKE EXECUTE ON FUNCTION public.reject_p3_accepted_mutation() FROM trading_p3_owner")


def downgrade() -> None:
    raise RuntimeError("0021 P3 operation authority is forward-only; use a reviewed forward repair")

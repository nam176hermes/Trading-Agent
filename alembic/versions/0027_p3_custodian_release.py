"""One-use custodian release capability; deployment requires separate qualification."""
from __future__ import annotations

from alembic import op

revision='0027_p3_custodian_release'
down_revision='0026_p3_holdout_disclosure'
branch_labels=None
depends_on=None


# Both functions independently check the current job and committed disclosure.
# No worker role can call these functions or read/write the release table.
_FENCE=r"""
DECLARE q jsonb; j public.jobs%ROWTYPE; a public.job_attempts%ROWTYPE;
  d public.p3_holdout_disclosures%ROWTYPE; request_sha text; stamp timestamptz;
BEGIN
  IF session_user<>'trading_p3_custodian'
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user
       AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
       AND NOT r.rolreplication AND NOT r.rolbypassrls AND NOT r.rolinherit
       AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m
         WHERE m.member=r.oid OR m.roleid=r.oid)) THEN
    RAISE EXCEPTION 'custodian identity rejected' USING ERRCODE='42501';
  END IF;
  IF p_request IS NULL OR octet_length(p_request) NOT BETWEEN 1 AND 65536
     OR p_request<>public.canonical_domain_json_string(p_request) THEN
    RAISE EXCEPTION 'invalid release request bytes' USING ERRCODE='22023';
  END IF;
  q:=p_request::jsonb;
  IF (jsonb_typeof(q)='object' AND q->>'schema_version'='p3-holdout-release-request-v1'
     AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(q) key)=
       ARRAY['attempt_id','authorization_digest','custodian_identity','custodian_uid','custody_record_ref',
         'holdout_commitment','holdout_request_sha256','intent_digest','job_id','plaintext_bundle_digest',
         'research_identity','research_uid','row_inventory_digest','schema_version','source','worker_id']) IS NOT TRUE THEN
    RAISE EXCEPTION 'invalid release request shape' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('lock_timeout','2000',true);
  SELECT * INTO j FROM public.jobs WHERE job_id=q->>'job_id' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'release job missing' USING ERRCODE='22023'; END IF;
  SELECT * INTO a FROM public.job_attempts
    WHERE job_id=j.job_id AND attempt_id=q->>'attempt_id' FOR UPDATE;
  IF NOT FOUND OR (j.job_type='ALPHA_CAMPAIGN' AND j.state='CLAIMED' AND a.outcome='CLAIMED'
      AND j.payload->>'operation'='HOLDOUT' AND j.payload->>'logical_trial_id'='p3-holdout-primary-v1'
      AND j.attempt_count=a.attempt_number AND j.lease_owner=q->>'worker_id' AND a.worker_id=j.lease_owner
      AND j.lease_token=a.lease_token AND j.lease_expires_at>clock_timestamp()
      AND a.lease_expires_at>clock_timestamp() AND q->'source'=j.payload->'expected_source'
      AND job_plane.p3_payload_authorized(j.payload,NULL)) IS NOT TRUE THEN
    RAISE EXCEPTION 'release current claim rejected' USING ERRCODE='22023';
  END IF;
  SELECT * INTO d FROM public.p3_holdout_disclosures WHERE job_id=j.job_id AND attempt_id=a.attempt_id;
  IF NOT FOUND OR (d.worker_id=q->>'worker_id'
      AND d.authorization_digest=q->>'authorization_digest'
      AND d.authorization_digest=j.payload#>>'{authorization_ref,content_sha256}'
      AND d.intent_digest=q->>'intent_digest' AND d.intent_sha256=j.payload#>>'{manifest_ref,content_sha256}'
      AND d.holdout_request_sha256=q->>'holdout_request_sha256'
      AND job_plane.p3_valid_ref(q->'custody_record_ref')
      AND q->'custody_record_ref'=d.holdout_request_text::jsonb->'custody_record_ref'
      AND d.custody_record_sha256=q#>>'{custody_record_ref,content_sha256}'
      AND d.holdout_commitment=q->>'holdout_commitment'
      AND d.plaintext_bundle_digest=q->>'plaintext_bundle_digest'
      AND d.source_identity_text=public.canonical_domain_json(q->'source')
      AND d.custodian_identity=q->>'custodian_identity' AND d.research_identity=q->>'research_identity'
      AND jsonb_typeof(q->'custodian_uid')='number' AND jsonb_typeof(q->'research_uid')='number'
      AND q->>'custodian_uid'~'^[1-9][0-9]*$' AND q->>'research_uid'~'^[1-9][0-9]*$'
      AND q->'custodian_uid'<>q->'research_uid'
      AND jsonb_typeof(q->'row_inventory_digest')='string'
      AND q->>'row_inventory_digest'~'^[0-9a-f]{64}$') IS NOT TRUE THEN
    RAISE EXCEPTION 'release committed disclosure differs' USING ERRCODE='22023';
  END IF;
  request_sha:=encode(sha256(convert_to(p_request,'UTF8')),'hex');
"""
_TAIL="""
  IF j.lease_expires_at<=clock_timestamp() OR a.lease_expires_at<=clock_timestamp()
     OR NOT job_plane.p3_payload_authorized(j.payload,NULL) THEN
    RAISE EXCEPTION 'release expired while waiting' USING ERRCODE='22023';
  END IF;
  RETURN QUERY SELECT request_sha,stamp;
END;
"""
CLAIM_BODY=_FENCE+"""
  stamp:=clock_timestamp();
  -- Deliberately no ON CONFLICT: no retry after uncertainty or a process restart.
  INSERT INTO public.p3_custodian_releases(holdout_commitment,request_sha256,request_text,released_at)
    VALUES(d.holdout_commitment,request_sha,p_request,stamp);
"""+_TAIL
READ_BODY=_FENCE+"""
  SELECT r.released_at INTO stamp FROM public.p3_custodian_releases r
    WHERE r.holdout_commitment=d.holdout_commitment AND r.request_sha256=request_sha AND r.request_text=p_request;
  IF NOT FOUND THEN RAISE EXCEPTION 'release committed readback missing' USING ERRCODE='22023'; END IF;
"""+_TAIL


def upgrade() -> None:
    # The restricted login is provisioned externally, never created by source validation.
    op.execute("""DO $preflight$ BEGIN
      IF current_user<>'trading_owner' OR session_user<>'trading_owner'
         OR (SELECT version_num FROM public.alembic_version) IS DISTINCT FROM '0026_p3_holdout_disclosure'
         OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='trading_p3_custodian'
           AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
           AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit)
         OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r
           ON r.oid=m.member OR r.oid=m.roleid WHERE r.rolname='trading_p3_custodian')
         OR to_regclass('public.p3_custodian_releases') IS NOT NULL THEN
        RAISE EXCEPTION 'unqualified custodian migration authority' USING ERRCODE='42501';
      END IF;
    END $preflight$;
    GRANT USAGE ON SCHEMA public,job_plane TO trading_p3_custodian;
    GRANT SELECT ON public.alembic_version TO trading_p3_custodian;
    GRANT CREATE ON SCHEMA public,job_plane TO trading_p3_owner;
    GRANT EXECUTE ON FUNCTION public.reject_p3_accepted_mutation() TO trading_p3_owner;
    SET LOCAL ROLE trading_p3_owner;
    CREATE TABLE public.p3_custodian_releases(
      holdout_commitment char(64) PRIMARY KEY REFERENCES public.p3_holdout_disclosures(holdout_commitment) ON DELETE RESTRICT,
      request_sha256 char(64) NOT NULL UNIQUE CHECK(request_sha256~'^[0-9a-f]{64}$'),
      request_text text NOT NULL CHECK(octet_length(request_text) BETWEEN 1 AND 65536
        AND request_text=public.canonical_domain_json_string(request_text)
        AND request_sha256=encode(sha256(convert_to(request_text,'UTF8')),'hex')),
      released_at timestamptz NOT NULL
    );
    CREATE TRIGGER p3_custodian_releases_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
      ON public.p3_custodian_releases FOR EACH STATEMENT EXECUTE FUNCTION public.reject_p3_accepted_mutation();
    REVOKE ALL ON public.p3_custodian_releases FROM PUBLIC,trading_owner,trading_jobs,trading_migrator,
      trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler,trading_p3_authority,trading_p3_custodian;
    """)
    for name,body in (('custodian_claim_p3_release',CLAIM_BODY),('custodian_read_p3_release',READ_BODY)):
        op.execute('CREATE FUNCTION job_plane.'+name+'''(p_request text)
          RETURNS TABLE(request_sha256 text,released_at timestamptz)
          LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE SET search_path=pg_catalog
          AS $release$'''+body+'$release$;')
        op.execute('REVOKE ALL ON FUNCTION job_plane.'+name+'''(text) FROM PUBLIC,trading_owner,
          trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,
          trading_job_scheduler,trading_p3_authority;
          GRANT EXECUTE ON FUNCTION job_plane.'''+name+'(text) TO trading_p3_custodian;')
    op.execute('''RESET ROLE; REVOKE CREATE ON SCHEMA public,job_plane FROM trading_p3_owner;
      REVOKE EXECUTE ON FUNCTION public.reject_p3_accepted_mutation() FROM trading_p3_owner;''')


def downgrade() -> None:
    raise RuntimeError('custody release history is append-only; downgrade is prohibited')

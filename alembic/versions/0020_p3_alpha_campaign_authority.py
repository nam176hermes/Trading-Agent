"""Add the source-only P3 alpha campaign database authority.

Revision ID: 0020_p3_alpha_campaign_authority
Revises: 0019_p2_security_master

Applying this migration requires the separately approved disposable P3 profile.
"""
from __future__ import annotations

from alembic import op


revision = "0020_p3_alpha_campaign_authority"
down_revision = "0019_p2_security_master"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        r"""
        DO $p3_preflight$
        BEGIN
          PERFORM pg_catalog.set_config('search_path', 'pg_catalog', true);
          IF current_user <> 'trading_owner'
             OR session_user <> 'trading_owner'
             OR (SELECT version_num FROM public.alembic_version)
                  IS DISTINCT FROM '0019_p2_security_master'
             OR NOT EXISTS (
               SELECT 1 FROM pg_catalog.pg_roles
               WHERE rolname = 'trading_p3_owner'
                 AND NOT rolcanlogin AND NOT rolsuper
                 AND NOT rolcreatedb AND NOT rolcreaterole
                 AND NOT rolreplication AND NOT rolbypassrls
             )
             OR NOT pg_catalog.pg_has_role(
                  'trading_owner', 'trading_p3_owner', 'MEMBER'
                ) THEN
            RAISE EXCEPTION 'P3 database authority is unavailable'
              USING ERRCODE = 'P3D08';
          END IF;
        END;
        $p3_preflight$;

        ALTER TABLE public.jobs DROP CONSTRAINT ck_jobs_type;
        ALTER TABLE public.jobs ADD CONSTRAINT ck_jobs_type CHECK (
          job_type IN ('SNAPSHOT','DEBATE','REPLAY','BACKTEST','ALPHA_CAMPAIGN')
        );

        CREATE TABLE public.p3_alpha_heads (
          alpha_id varchar(64) NOT NULL,
          alpha_version varchar(64) NOT NULL,
          stream_id uuid NOT NULL UNIQUE,
          registry_sequence bigint NOT NULL CHECK (registry_sequence >= 1),
          registry_event_sha256 char(64) NOT NULL,
          registry_event_text text NOT NULL,
          ledger_event_id uuid NOT NULL UNIQUE
            REFERENCES public.domain_events(event_id) ON DELETE RESTRICT,
          updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          PRIMARY KEY (alpha_id, alpha_version),
          CHECK (registry_event_sha256 ~ '^[0-9a-f]{64}$')
        );
        CREATE TABLE public.p3_alpha_job_commits (
          job_id varchar(64) NOT NULL REFERENCES public.jobs(job_id) ON DELETE RESTRICT,
          idempotency_key varchar(128) NOT NULL,
          semantic_request_digest char(64) NOT NULL,
          result_json jsonb NOT NULL,
          result_text text NOT NULL,
          result_digest char(64) NOT NULL,
          committed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          PRIMARY KEY (job_id, idempotency_key),
          UNIQUE (result_digest),
          CHECK (semantic_request_digest ~ '^[0-9a-f]{64}$'),
          CHECK (result_digest ~ '^[0-9a-f]{64}$'),
          CHECK (result_text = public.canonical_domain_json_string(result_text)),
          CHECK (result_json = result_text::jsonb)
        );
        CREATE TABLE public.p3_alpha_projection (
          alpha_id varchar(64) NOT NULL,
          alpha_version varchar(64) NOT NULL,
          registry_sequence bigint NOT NULL CHECK (registry_sequence >= 1),
          registry_event_sha256 char(64) NOT NULL,
          projection jsonb NOT NULL,
          rebuilt_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          PRIMARY KEY (alpha_id, alpha_version)
        );
        CREATE TABLE public.p3_campaign_authorizations (
          request_digest char(64) PRIMARY KEY,
          input_set_digest char(64) NOT NULL,
          source_commit_sha char(40) NOT NULL,
          operation varchar(32) NOT NULL CHECK (
            operation IN ('BASELINES','REGISTER_FAMILY','OOS','HOLDOUT','PARITY','PHASE_EXIT')
          ),
          expires_at timestamptz NOT NULL,
          authorization_text text NOT NULL,
          accepted_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CHECK (request_digest ~ '^[0-9a-f]{64}$'),
          CHECK (input_set_digest ~ '^[0-9a-f]{64}$'),
          CHECK (source_commit_sha ~ '^[0-9a-f]{40}$'),
          CHECK (authorization_text = public.canonical_domain_json_string(authorization_text))
        );
        CREATE FUNCTION public.reject_p3_accepted_mutation() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog
        AS $reject_p3_accepted_mutation$
        BEGIN
          RAISE EXCEPTION 'accepted P3 authority records are append-only'
            USING ERRCODE='55000';
        END;
        $reject_p3_accepted_mutation$;
        CREATE TRIGGER p3_alpha_job_commits_append_only
          BEFORE UPDATE OR DELETE OR TRUNCATE ON public.p3_alpha_job_commits
          FOR EACH STATEMENT EXECUTE FUNCTION public.reject_p3_accepted_mutation();
        CREATE TRIGGER p3_campaign_authorizations_append_only
          BEFORE UPDATE OR DELETE OR TRUNCATE ON public.p3_campaign_authorizations
          FOR EACH STATEMENT EXECUTE FUNCTION public.reject_p3_accepted_mutation();
        ALTER TABLE public.p3_alpha_heads OWNER TO trading_p3_owner;
        ALTER TABLE public.p3_alpha_job_commits OWNER TO trading_p3_owner;
        ALTER TABLE public.p3_alpha_projection OWNER TO trading_p3_owner;
        ALTER TABLE public.p3_campaign_authorizations OWNER TO trading_p3_owner;

        CREATE FUNCTION job_plane.api_enqueue_alpha_campaign(
          p_job_id text, p_payload_text text, p_payload_fingerprint text,
          p_idempotency_key text, p_actor_id text, p_priority integer,
          p_trace_id text, p_event_id text
        ) RETURNS TABLE(job_id text, outcome text)
        LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $api_enqueue_alpha_campaign$
        DECLARE
          v_payload jsonb;
          v_existing public.jobs%ROWTYPE;
          v_inserted text;
          v_fingerprint text;
        BEGIN
          IF session_user <> 'trading_job_api' OR p_payload_text IS NULL
             OR pg_catalog.octet_length(p_payload_text) > 1048576 THEN
            RAISE EXCEPTION 'P3 enqueue authority rejected' USING ERRCODE='42501';
          END IF;
          v_payload := p_payload_text::jsonb;
          IF p_payload_text IS DISTINCT FROM public.canonical_domain_json_string(p_payload_text)
             OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(v_payload)) <> 6
             OR v_payload ->> 'schema_version' <> 'p3-alpha-campaign-payload-v1'
             OR v_payload #>> '{authorization_ref,content_sha256}' !~ '^[0-9a-f]{64}$'
             OR v_payload #>> '{manifest_ref,content_sha256}' !~ '^[0-9a-f]{64}$'
             OR v_payload #>> '{expected_source,commit_sha}' !~ '^[0-9a-f]{40}$'
             OR v_payload ->> 'logical_trial_id' !~ '^[a-z][a-z0-9._-]{0,127}$'
             OR v_payload ->> 'operation' NOT IN (
                  'BASELINES','REGISTER_FAMILY','OOS','HOLDOUT','PARITY','PHASE_EXIT'
                )
             OR NOT EXISTS (
               SELECT 1 FROM public.p3_campaign_authorizations a
               WHERE a.request_digest = v_payload #>> '{authorization_ref,content_sha256}'
                 AND a.operation = v_payload ->> 'operation'
                 AND a.source_commit_sha = v_payload #>> '{expected_source,commit_sha}'
                 AND a.expires_at > pg_catalog.clock_timestamp()
             ) THEN
            RAISE EXCEPTION 'P3 enqueue payload rejected' USING ERRCODE='22023';
          END IF;
          v_fingerprint := pg_catalog.encode(pg_catalog.sha256(
            pg_catalog.convert_to(p_payload_text,'UTF8')), 'hex');
          IF p_job_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_event_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_job_id = p_event_id OR p_payload_fingerprint <> v_fingerprint
             OR p_idempotency_key !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_actor_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_priority NOT BETWEEN 0 AND 100
             OR p_trace_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
            RAISE EXCEPTION 'P3 enqueue input rejected' USING ERRCODE='22023';
          END IF;
          INSERT INTO public.jobs(
            job_id,job_type,state,payload,payload_fingerprint,idempotency_key,
            actor_type,actor_id,priority,max_attempts
          ) VALUES (
            p_job_id,'ALPHA_CAMPAIGN','QUEUED',v_payload,v_fingerprint,
            p_idempotency_key,'OPERATOR',p_actor_id,p_priority,3
          ) ON CONFLICT(job_type,idempotency_key) DO NOTHING
          RETURNING public.jobs.job_id INTO v_inserted;
          IF v_inserted IS NOT NULL THEN
            INSERT INTO public.job_events(
              event_id,job_id,sequence,from_state,to_state,reason_code,
              actor_type,actor_id,trace_id,metadata
            ) VALUES (p_event_id,v_inserted,1,NULL,'QUEUED','ENQUEUED',
              'OPERATOR',p_actor_id,p_trace_id,'{}'::jsonb);
            job_id := v_inserted; outcome := 'ENQUEUED'; RETURN NEXT; RETURN;
          END IF;
          SELECT j.* INTO v_existing FROM public.jobs j
          WHERE j.job_type='ALPHA_CAMPAIGN' AND j.idempotency_key=p_idempotency_key
          FOR UPDATE;
          IF NOT FOUND OR v_existing.payload_fingerprint <> v_fingerprint
             OR v_existing.actor_id <> p_actor_id OR v_existing.priority <> p_priority THEN
            RAISE EXCEPTION 'P3 enqueue idempotency conflict' USING ERRCODE='23505';
          END IF;
          job_id := v_existing.job_id; outcome := 'DEDUPLICATED'; RETURN NEXT;
        END;
        $api_enqueue_alpha_campaign$;

        CREATE FUNCTION job_plane.worker_claim_alpha_campaign(
          p_attempt_id text, p_worker_id text, p_lease_token text,
          p_lease_seconds integer, p_trace_id text, p_event_id text
        ) RETURNS TABLE(job_id text,job_type text,payload jsonb,
          attempt_number integer,max_attempts smallint,lease_expires_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $worker_claim_alpha_campaign$
        DECLARE
          v_job public.jobs%ROWTYPE;
          v_now timestamptz;
          v_sequence bigint;
        BEGIN
          IF session_user <> 'trading_job_worker'
             OR p_attempt_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_lease_token !~ '^[A-Za-z0-9_-]{16,128}$'
             OR p_lease_seconds NOT BETWEEN 1 AND 3600
             OR p_trace_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_event_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$' THEN
            RAISE EXCEPTION 'P3 claim rejected' USING ERRCODE='22023';
          END IF;
          SELECT j.* INTO v_job FROM public.jobs j
          WHERE j.job_type='ALPHA_CAMPAIGN' AND j.state='QUEUED'
            AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= pg_catalog.clock_timestamp())
            AND j.attempt_count < j.max_attempts
          ORDER BY j.priority DESC,j.requested_at,j.job_id FOR UPDATE SKIP LOCKED LIMIT 1;
          IF NOT FOUND THEN RETURN; END IF;
          v_now := pg_catalog.clock_timestamp();
          SELECT coalesce(max(e.sequence),0)+1 INTO v_sequence
          FROM public.job_events e WHERE e.job_id=v_job.job_id;
          UPDATE public.jobs j SET state='CLAIMED',attempt_count=j.attempt_count+1,
            lease_owner=p_worker_id,lease_token=p_lease_token,
            lease_expires_at=v_now+(p_lease_seconds*interval '1 second'),
            next_attempt_at=NULL,reason_code='CLAIMED',updated_at=v_now
          WHERE j.job_id=v_job.job_id
          RETURNING j.job_id,j.job_type,j.payload,j.attempt_count,j.max_attempts,j.lease_expires_at
          INTO job_id,job_type,payload,attempt_number,max_attempts,lease_expires_at;
          INSERT INTO public.job_attempts(
            attempt_id,job_id,attempt_number,worker_id,outcome,lease_token,
            lease_expires_at,claimed_at
          ) VALUES (p_attempt_id,job_id,attempt_number,p_worker_id,'CLAIMED',
            p_lease_token,lease_expires_at,v_now);
          INSERT INTO public.job_events(
            event_id,job_id,attempt_id,sequence,from_state,to_state,reason_code,
            actor_type,actor_id,trace_id,metadata
          ) VALUES (p_event_id,job_id,p_attempt_id,v_sequence,'QUEUED','CLAIMED',
            'CLAIMED','WORKER',p_worker_id,p_trace_id,'{}'::jsonb);
          RETURN NEXT;
        END;
        $worker_claim_alpha_campaign$;

        CREATE FUNCTION job_plane.api_cancel_alpha_campaign(
          p_job_id text,p_actor_id text,p_trace_id text,p_event_id text
        ) RETURNS TABLE(job_id text,state text,changed boolean)
        LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $api_cancel_alpha_campaign$
        DECLARE v_job public.jobs%ROWTYPE; v_target text; v_sequence bigint;
        BEGIN
          IF session_user <> 'trading_job_api' THEN
            RAISE EXCEPTION 'P3 cancel authority rejected' USING ERRCODE='42501';
          END IF;
          SELECT j.* INTO v_job FROM public.jobs j WHERE j.job_id=p_job_id FOR UPDATE;
          IF NOT FOUND THEN RETURN; END IF;
          IF v_job.job_type <> 'ALPHA_CAMPAIGN' THEN
            RAISE EXCEPTION 'P3 cancel target rejected' USING ERRCODE='22023';
          END IF;
          IF v_job.state IN ('SUCCEEDED','FAILED','BLOCKED','TIMED_OUT','CANCEL_REQUESTED','CANCELLED') THEN
            job_id:=v_job.job_id; state:=v_job.state; changed:=false; RETURN NEXT; RETURN;
          END IF;
          v_target := CASE WHEN v_job.state='QUEUED' THEN 'CANCELLED'
            WHEN v_job.state IN ('CLAIMED','RUNNING') THEN 'CANCEL_REQUESTED' END;
          IF v_target IS NULL THEN
            RAISE EXCEPTION 'P3 cancel state rejected' USING ERRCODE='22023';
          END IF;
          SELECT coalesce(max(e.sequence),0)+1 INTO v_sequence
          FROM public.job_events e WHERE e.job_id=p_job_id;
          UPDATE public.jobs j SET state=v_target,updated_at=pg_catalog.clock_timestamp(),
            reason_code='CANCEL_REQUESTED',cancel_requested_at=pg_catalog.clock_timestamp(),
            cancel_actor_type='OPERATOR',cancel_actor_id=p_actor_id,
            finished_at=CASE WHEN v_target='CANCELLED' THEN pg_catalog.clock_timestamp() ELSE j.finished_at END
          WHERE j.job_id=p_job_id;
          INSERT INTO public.job_events(event_id,job_id,sequence,from_state,to_state,
            reason_code,actor_type,actor_id,trace_id,metadata)
          VALUES(p_event_id,p_job_id,v_sequence,v_job.state,v_target,'CANCEL_REQUESTED',
            'OPERATOR',p_actor_id,p_trace_id,'{}'::jsonb);
          job_id:=p_job_id; state:=v_target; changed:=true; RETURN NEXT;
        END;
        $api_cancel_alpha_campaign$;

        CREATE FUNCTION job_plane.read_alpha_commit(
          p_job_id text,p_idempotency_key text,p_semantic_request_digest text
        ) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER PARALLEL SAFE
        SET search_path = pg_catalog
        AS $read_alpha_commit$
          SELECT c.result_json FROM public.p3_alpha_job_commits c
          WHERE c.job_id=p_job_id AND c.idempotency_key=p_idempotency_key
            AND c.semantic_request_digest=p_semantic_request_digest
        $read_alpha_commit$;

        CREATE FUNCTION job_plane.worker_commit_alpha_campaign(
          p_job_id text,p_attempt_id text,p_worker_id text,p_lease_token text,
          p_request_text text,p_trace_id text
        ) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $worker_commit_alpha_campaign$
        DECLARE
          v_job public.jobs%ROWTYPE;
          v_attempt public.job_attempts%ROWTYPE;
          v_transport jsonb;
          v_request jsonb;
          v_entry jsonb;
          v_expected jsonb;
          v_event jsonb;
          v_registry jsonb;
          v_previous jsonb;
          v_existing public.p3_alpha_job_commits%ROWTYPE;
          v_result jsonb;
          v_result_text text;
          v_result_digest text;
          v_ledger_ids jsonb := '[]'::jsonb;
        BEGIN
          PERFORM pg_catalog.set_config('lock_timeout','2s',true);
          PERFORM pg_catalog.set_config('statement_timeout','5s',true);
          IF session_user <> 'trading_job_worker' OR p_request_text IS NULL
             OR pg_catalog.octet_length(p_request_text)>1048576 THEN
            RAISE EXCEPTION 'P3 commit authority rejected' USING ERRCODE='42501';
          END IF;
          v_transport:=p_request_text::jsonb;
          IF p_request_text <> public.canonical_domain_json_string(p_request_text)
             OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(v_transport))<>6
             OR v_transport->>'job_id'<>p_job_id
             OR v_transport->>'attempt_id'<>p_attempt_id
             OR v_transport->>'worker_id'<>p_worker_id
             OR v_transport->>'lease_token'<>p_lease_token
             OR pg_catalog.jsonb_typeof(v_transport->'entries')<>'array'
             OR pg_catalog.jsonb_array_length(v_transport->'entries') NOT BETWEEN 1 AND 8 THEN
            RAISE EXCEPTION 'P3 commit transport rejected' USING ERRCODE='22023';
          END IF;
          v_request:=v_transport->'request';
          IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(v_request))<>9
             OR v_request->>'schema_version'<>'p3-publication-request-v1'
             OR v_request->>'job_id'<>p_job_id
             OR v_request->>'stage' NOT IN ('REGISTER','RESEARCH_DECISION','EXIT_DECISION')
             OR pg_catalog.jsonb_typeof(v_request->'expected_heads')<>'array'
             OR pg_catalog.jsonb_array_length(v_request->'expected_heads') NOT BETWEEN 1 AND 4
             OR pg_catalog.jsonb_typeof(v_request->'proposed_event_refs')<>'array'
             OR pg_catalog.jsonb_array_length(v_request->'proposed_event_refs')
                  <> pg_catalog.jsonb_array_length(v_transport->'entries')
             OR v_request->>'semantic_request_digest'!~'^[0-9a-f]{64}$'
             OR v_request->>'digest'<>pg_catalog.encode(pg_catalog.sha256(
               pg_catalog.convert_to(public.canonical_domain_json(v_request-'digest'),'UTF8')),'hex') THEN
            RAISE EXCEPTION 'P3 publication request rejected' USING ERRCODE='22023';
          END IF;
          IF (v_request->>'stage'='REGISTER' AND (
                pg_catalog.jsonb_array_length(v_request->'expected_heads')<>4
                OR pg_catalog.jsonb_array_length(v_transport->'entries')<>8
             )) THEN
            RAISE EXCEPTION 'P3 registration batch must contain four IDEA/CANDIDATE pairs'
              USING ERRCODE='22023';
          END IF;
          IF (SELECT count(*) FROM (
                SELECT value->>'alpha_id',value->>'version'
                FROM pg_catalog.jsonb_array_elements(v_request->'expected_heads')
                GROUP BY value->>'alpha_id',value->>'version'
              ) AS distinct_heads)
             <> pg_catalog.jsonb_array_length(v_request->'expected_heads') THEN
            RAISE EXCEPTION 'P3 expected heads contain duplicate identities'
              USING ERRCODE='22023';
          END IF;

          SELECT j.* INTO v_job FROM public.jobs j
          WHERE j.job_id=p_job_id AND j.job_type='ALPHA_CAMPAIGN' FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'E_STALE_FENCE' USING ERRCODE='P3D01'; END IF;
          SELECT c.* INTO v_existing FROM public.p3_alpha_job_commits c
          WHERE c.job_id=p_job_id AND c.idempotency_key=v_request->>'idempotency_key';
          IF FOUND THEN
            IF v_existing.semantic_request_digest<>v_request->>'semantic_request_digest' THEN
              RAISE EXCEPTION 'E_IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
            END IF;
            RETURN v_existing.result_json;
          END IF;
          IF v_job.state='CANCEL_REQUESTED' THEN
            RAISE EXCEPTION 'CANCELLED_BEFORE_COMMIT' USING ERRCODE='P3D02';
          END IF;
          IF v_job.state<>'RUNNING' OR v_job.lease_owner<>p_worker_id
             OR v_job.lease_token<>p_lease_token
             OR v_job.lease_expires_at<=pg_catalog.clock_timestamp() THEN
            RAISE EXCEPTION 'E_STALE_FENCE' USING ERRCODE='P3D01';
          END IF;
          SELECT a.* INTO v_attempt FROM public.job_attempts a
          WHERE a.attempt_id=p_attempt_id AND a.job_id=p_job_id FOR UPDATE;
          IF NOT FOUND OR v_attempt.outcome<>'RUNNING'
             OR v_attempt.worker_id<>p_worker_id OR v_attempt.lease_token<>p_lease_token
             OR v_attempt.lease_expires_at<=pg_catalog.clock_timestamp()
             OR v_attempt.attempt_number<>v_job.attempt_count THEN
            RAISE EXCEPTION 'E_STALE_FENCE' USING ERRCODE='P3D01';
          END IF;
          IF NOT EXISTS(SELECT 1 FROM public.p3_campaign_authorizations a
            WHERE a.request_digest=v_job.payload#>>'{authorization_ref,content_sha256}'
              AND a.operation=v_job.payload->>'operation'
              AND a.source_commit_sha=v_job.payload#>>'{expected_source,commit_sha}'
              AND a.expires_at>pg_catalog.clock_timestamp()) THEN
            RAISE EXCEPTION 'P3 source authorization expired' USING ERRCODE='P3D03';
          END IF;

          FOR v_expected IN SELECT value FROM pg_catalog.jsonb_array_elements(
            v_request->'expected_heads') ORDER BY value->>'alpha_id',value->>'version'
          LOOP
            IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(v_expected))<>4
               OR v_expected->>'alpha_id'!~'^[a-z][a-z0-9._-]{0,63}$'
               OR v_expected->>'version'!~'^[0-9]+\.[0-9]+\.[0-9]+$'
               OR v_expected->>'sequence'!~'^(0|[1-9][0-9]*)$'
               OR ((v_expected->>'sequence')::bigint=0)
                    IS DISTINCT FROM (v_expected->'event_digest'='null'::jsonb)
               OR (v_expected->'event_digest'<>'null'::jsonb
                    AND v_expected->>'event_digest'!~'^[0-9a-f]{64}$') THEN
              RAISE EXCEPTION 'P3 expected head is invalid' USING ERRCODE='22023';
            END IF;
            PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
              (v_expected->>'alpha_id')||':'||(v_expected->>'version'),3));
            PERFORM 1 FROM public.p3_alpha_heads h
            WHERE h.alpha_id=v_expected->>'alpha_id'
              AND h.alpha_version=v_expected->>'version' FOR UPDATE;
            IF coalesce((SELECT h.registry_sequence FROM public.p3_alpha_heads h
                 WHERE h.alpha_id=v_expected->>'alpha_id' AND h.alpha_version=v_expected->>'version'),0)
                 <> (v_expected->>'sequence')::bigint
               OR (SELECT h.registry_event_sha256 FROM public.p3_alpha_heads h
                 WHERE h.alpha_id=v_expected->>'alpha_id' AND h.alpha_version=v_expected->>'version')
                 IS DISTINCT FROM v_expected->>'event_digest' THEN
              RAISE EXCEPTION 'E_STALE_HEAD' USING ERRCODE='40001';
            END IF;
          END LOOP;

          FOR v_entry IN SELECT value FROM pg_catalog.jsonb_array_elements(v_transport->'entries')
          LOOP
            IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(v_entry))<>7
               OR v_entry->>'event_type'<>'AlphaRegistryTransitionRecordedV1'
               OR v_entry->>'topic'<>'p3.alpha-registry'
               OR v_entry->>'event_id'!~'^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
               OR v_entry->>'stream_id'!~'^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
               OR v_entry->>'sequence'!~'^[1-9][0-9]*$'
               OR pg_catalog.octet_length(v_entry->>'outbox_payload_text')>65536 THEN
              RAISE EXCEPTION 'P3 append entry rejected' USING ERRCODE='22023';
            END IF;
            v_event:=(v_entry->>'canonical_event_text')::jsonb;
            IF v_entry->>'canonical_event_text'<>public.canonical_domain_json_string(v_entry->>'canonical_event_text')
               OR v_event->>'event_id'<>v_entry->>'event_id'
               OR v_event->>'stream_id'<>v_entry->>'stream_id'
               OR (v_event->>'sequence')::bigint<>(v_entry->>'sequence')::bigint
               OR v_event->>'event_type'<>'AlphaRegistryTransitionRecordedV1' THEN
              RAISE EXCEPTION 'P3 event envelope rejected' USING ERRCODE='22023';
            END IF;
            IF v_entry->>'outbox_payload_text'
                 <> public.canonical_domain_json_string(v_entry->>'outbox_payload_text')
               OR (v_entry->>'outbox_payload_text')::jsonb->>'event_id'
                    <> v_entry->>'event_id' THEN
              RAISE EXCEPTION 'P3 outbox payload rejected' USING ERRCODE='22023';
            END IF;
            v_registry:=(v_event#>>'{payload,registry_event_text}')::jsonb;
            IF v_event#>>'{payload,registry_event_sha256}'<>pg_catalog.encode(pg_catalog.sha256(
                 pg_catalog.convert_to(public.canonical_domain_json(v_registry),'UTF8')),'hex')
               OR v_registry->>'schema_version'<>'alpha-registry-event-v1'
               OR (v_registry->>'sequence')::bigint<>(v_entry->>'sequence')::bigint
               OR v_registry->>'predecessor_sha256' IS DISTINCT FROM
                    v_event#>>'{payload,predecessor_sha256}'
               OR v_registry#>>'{record,alpha_id}'<>v_event#>>'{payload,alpha_id}'
               OR v_registry#>>'{record,version}'<>v_event#>>'{payload,alpha_version}'
               OR v_registry#>>'{record,schema_version}'<>'alpha-record-v1'
               OR NOT EXISTS (
                 SELECT 1 FROM pg_catalog.jsonb_array_elements(v_request->'proposed_event_refs') r
                 WHERE r->>'content_sha256'=v_event#>>'{payload,registry_event_sha256}'
                   AND r->>'locator'=(r->>'content_sha256')||'.blob'
               ) THEN
              RAISE EXCEPTION 'P3 registry event rejected' USING ERRCODE='22023';
            END IF;
            SELECT h.registry_event_text::jsonb INTO v_previous
            FROM public.p3_alpha_heads h
            WHERE h.alpha_id=v_registry#>>'{record,alpha_id}'
              AND h.alpha_version=v_registry#>>'{record,version}' FOR UPDATE;
            IF NOT FOUND THEN
              IF (v_entry->>'sequence')::bigint<>1
                 OR v_registry->'predecessor_sha256'<>'null'::jsonb
                 OR v_registry#>>'{record,lifecycle_status}'<>'IDEA'
                 OR v_request->>'stage'<>'REGISTER' THEN
                RAISE EXCEPTION 'P3 initial registry transition rejected' USING ERRCODE='P3D04';
              END IF;
            ELSE
              IF (v_entry->>'sequence')::bigint<>(v_previous->>'sequence')::bigint+1
                 OR v_registry->>'predecessor_sha256'<>
                    pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                      public.canonical_domain_json(v_previous),'UTF8')),'hex')
                 OR v_registry#>'{record,alpha_id}' IS DISTINCT FROM v_previous#>'{record,alpha_id}'
                 OR v_registry#>'{record,version}' IS DISTINCT FROM v_previous#>'{record,version}'
                 OR v_registry#>'{record,source_sha}' IS DISTINCT FROM v_previous#>'{record,source_sha}'
                 OR v_registry#>'{record,implementation_identity}' IS DISTINCT FROM v_previous#>'{record,implementation_identity}'
                 OR v_registry#>'{record,dataset_snapshot_sha256}' IS DISTINCT FROM v_previous#>'{record,dataset_snapshot_sha256}'
                 OR v_registry#>'{record,feature_set}' IS DISTINCT FROM v_previous#>'{record,feature_set}'
                 OR v_registry#>'{record,parameter_set_sha256}' IS DISTINCT FROM v_previous#>'{record,parameter_set_sha256}'
                 OR v_registry#>'{record,training_start_at}' IS DISTINCT FROM v_previous#>'{record,training_start_at}'
                 OR v_registry#>'{record,training_end_at}' IS DISTINCT FROM v_previous#>'{record,training_end_at}'
                 OR v_registry#>'{record,validation_start_at}' IS DISTINCT FROM v_previous#>'{record,validation_start_at}'
                 OR v_registry#>'{record,validation_end_at}' IS DISTINCT FROM v_previous#>'{record,validation_end_at}'
                 OR v_registry#>'{record,oos_start_at}' IS DISTINCT FROM v_previous#>'{record,oos_start_at}'
                 OR v_registry#>'{record,oos_end_at}' IS DISTINCT FROM v_previous#>'{record,oos_end_at}'
                 OR v_registry#>'{record,universe}' IS DISTINCT FROM v_previous#>'{record,universe}'
                 OR v_registry#>'{record,cost_model_sha256}' IS DISTINCT FROM v_previous#>'{record,cost_model_sha256}'
                 OR v_registry#>'{record,baseline_id}' IS DISTINCT FROM v_previous#>'{record,baseline_id}'
                 OR v_registry#>'{record,baseline_version}' IS DISTINCT FROM v_previous#>'{record,baseline_version}'
                 OR v_registry#>'{record,lineage}' IS DISTINCT FROM v_previous#>'{record,lineage}'
                 OR v_registry#>'{record,superseded_version}' IS DISTINCT FROM v_previous#>'{record,superseded_version}' THEN
                RAISE EXCEPTION 'P3 registry predecessor or identity rejected' USING ERRCODE='P3D04';
              END IF;
              IF NOT CASE v_previous#>>'{record,lifecycle_status}'
                WHEN 'IDEA' THEN v_registry#>>'{record,lifecycle_status}' IN ('CANDIDATE','REJECTED','RETIRED')
                WHEN 'CANDIDATE' THEN v_registry#>>'{record,lifecycle_status}' IN ('RESEARCHED','REJECTED','RETIRED')
                WHEN 'RESEARCHED' THEN v_registry#>>'{record,lifecycle_status}' IN ('OOS_PASS','REJECTED','RETIRED')
                WHEN 'OOS_PASS' THEN v_registry#>>'{record,lifecycle_status}' IN ('QUALIFIED','REJECTED','RETIRED')
                WHEN 'QUALIFIED' THEN v_registry#>>'{record,lifecycle_status}' IN ('PAPER_OBSERVED','RETIRED')
                WHEN 'PAPER_OBSERVED' THEN v_registry#>>'{record,lifecycle_status}'='RETIRED'
                WHEN 'REJECTED' THEN v_registry#>>'{record,lifecycle_status}'='RETIRED'
                ELSE false END THEN
                RAISE EXCEPTION 'P3 illegal registry transition' USING ERRCODE='P3D04';
              END IF;
            END IF;
            IF (v_request->>'stage'='REGISTER' AND v_registry#>>'{record,lifecycle_status}' NOT IN ('IDEA','CANDIDATE'))
               OR (v_request->>'stage'='RESEARCH_DECISION' AND v_registry#>>'{record,lifecycle_status}' NOT IN ('RESEARCHED','OOS_PASS','REJECTED'))
               OR (v_request->>'stage'='EXIT_DECISION' AND v_registry#>>'{record,lifecycle_status}' NOT IN ('QUALIFIED','REJECTED')) THEN
              RAISE EXCEPTION 'P3 registry stage transition rejected' USING ERRCODE='P3D04';
            END IF;
            PERFORM public.append_domain_event(
              (v_entry->>'event_id')::uuid,(v_entry->>'stream_id')::uuid,
              (v_entry->>'sequence')::bigint,v_entry->>'event_type',
              v_entry->>'canonical_event_text',v_entry->>'topic',v_entry->>'outbox_payload_text');
            INSERT INTO public.p3_alpha_heads(alpha_id,alpha_version,stream_id,
              registry_sequence,registry_event_sha256,registry_event_text,ledger_event_id)
            VALUES(v_event#>>'{payload,alpha_id}',v_event#>>'{payload,alpha_version}',
              (v_entry->>'stream_id')::uuid,(v_entry->>'sequence')::bigint,
              v_event#>>'{payload,registry_event_sha256}',v_event#>>'{payload,registry_event_text}',
              (v_entry->>'event_id')::uuid)
            ON CONFLICT(alpha_id,alpha_version) DO UPDATE SET
              registry_sequence=excluded.registry_sequence,
              registry_event_sha256=excluded.registry_event_sha256,
              registry_event_text=excluded.registry_event_text,
              ledger_event_id=excluded.ledger_event_id,
              updated_at=pg_catalog.clock_timestamp();
            v_ledger_ids:=v_ledger_ids||pg_catalog.jsonb_build_array(v_entry->'event_id');
          END LOOP;

          v_result:=pg_catalog.jsonb_build_object(
            'schema_version','p3-job-commit-result-v1','job_id',p_job_id,
            'idempotency_key',v_request->'idempotency_key',
            'semantic_request_digest',v_request->'semantic_request_digest',
            'prepublication_ref',v_request->'evidence_ref','ledger_event_ids',v_ledger_ids,
            'registry_event_refs',v_request->'proposed_event_refs',
            'alpha_outcome',CASE WHEN v_request->>'stage'='REGISTER' THEN 'NOT_EVALUATED'
              WHEN v_registry#>>'{record,qualification_decision}'='PASS' THEN 'PASS' ELSE 'FAIL' END);
          v_result_digest:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
            public.canonical_domain_json(v_result),'UTF8')),'hex');
          v_result:=v_result||pg_catalog.jsonb_build_object('digest',v_result_digest);
          v_result_text:=public.canonical_domain_json(v_result);
          INSERT INTO public.p3_alpha_job_commits(job_id,idempotency_key,
            semantic_request_digest,result_json,result_text,result_digest)
          VALUES(p_job_id,v_request->>'idempotency_key',v_request->>'semantic_request_digest',
            v_result,v_result_text,v_result_digest);
          UPDATE public.jobs SET state='SUCCEEDED',reason_code='P3_COMMITTED',
            result_hash=v_result_digest,result_metadata=v_result,finished_at=pg_catalog.clock_timestamp(),
            updated_at=pg_catalog.clock_timestamp(),lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL
          WHERE job_id=p_job_id;
          UPDATE public.job_attempts SET outcome='SUCCEEDED',finished_at=pg_catalog.clock_timestamp()
          WHERE attempt_id=p_attempt_id AND job_id=p_job_id;
          RETURN v_result;
        END;
        $worker_commit_alpha_campaign$;

        -- Derive the four remaining lifecycle functions from the exact reviewed
        -- 0011 bodies, replacing only their name and closed job-type predicate.
        DO $promote_p3_lifecycle$
        DECLARE
          source_names text[] := ARRAY[
            'worker_start_paper','worker_control_paper_lease',
            'worker_finalize_paper','worker_recover_expired_paper'
          ];
          target_names text[] := ARRAY[
            'worker_start_alpha_campaign','worker_control_alpha_campaign_lease',
            'worker_finalize_alpha_campaign','worker_recover_expired_alpha_campaign'
          ];
          source_signatures text[] := ARRAY[
            'job_plane.worker_start_paper(text,text,text,text,bigint,bigint,bigint,text,text,text)',
            'job_plane.worker_control_paper_lease(text,text,text,text,integer,text)',
            'job_plane.worker_finalize_paper(text,text,text,text,text,text,text,text,text,text,integer,text,text,jsonb,text,text,boolean,text,jsonb)',
            'job_plane.worker_recover_expired_paper(text,text,text,text,text,text,bigint,bigint,bigint,text,text,text,text,text,text)'
          ];
          source_hashes text[] := ARRAY[
            '561169daa1d7699a1636575da0f0db99852ae440b861765fcbd9e4820ce4c1a0',
            '233f8ba48fe8299b4b2e31a3086b99d08d87389c367d9f955659ba9ea725dbe6',
            '004bb49c1cca92356caac48879ffeaa18c0934291b4b13a25f278caf4c53639c',
            'b084073af25fa0fb5d06506815a9496cb56c8620e10e0997ede767eb90e592f2'
          ];
          source_body text;
          source_definition text;
          promoted_definition text;
          function_index integer;
        BEGIN
          FOR function_index IN 1..4 LOOP
            SELECT p.prosrc,pg_catalog.pg_get_functiondef(p.oid)
            INTO source_body,source_definition
            FROM pg_catalog.pg_proc p
            WHERE p.oid=source_signatures[function_index]::pg_catalog.regprocedure
              AND p.prosecdef AND p.provolatile='v' AND p.proparallel='u'
              AND p.proconfig=ARRAY['search_path=pg_catalog']
              AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                p.prosrc,'UTF8')),'hex')=source_hashes[function_index];
            IF NOT FOUND OR pg_catalog.strpos(
              source_body,
              'job_plane.paper_worker_job_allowed(job_row.job_type, job_row.payload)'
            )=0 THEN
              RAISE EXCEPTION 'P3 lifecycle source drifted: %',source_names[function_index]
                USING ERRCODE='P3D08';
            END IF;
            promoted_definition:=pg_catalog.replace(
              pg_catalog.replace(source_definition,source_names[function_index],target_names[function_index]),
              'job_plane.paper_worker_job_allowed(job_row.job_type, job_row.payload)',
              'job_row.job_type = ''ALPHA_CAMPAIGN'''
            );
            EXECUTE promoted_definition;
          END LOOP;
        END;
        $promote_p3_lifecycle$;

        ALTER FUNCTION job_plane.api_enqueue_alpha_campaign(text,text,text,text,text,integer,text,text) OWNER TO trading_p3_owner;
        ALTER FUNCTION job_plane.worker_claim_alpha_campaign(text,text,text,integer,text,text) OWNER TO trading_p3_owner;
        ALTER FUNCTION job_plane.worker_commit_alpha_campaign(text,text,text,text,text,text) OWNER TO trading_p3_owner;
        ALTER FUNCTION job_plane.read_alpha_commit(text,text,text) OWNER TO trading_p3_owner;
        ALTER FUNCTION job_plane.api_cancel_alpha_campaign(text,text,text,text) OWNER TO trading_p3_owner;
        ALTER FUNCTION job_plane.worker_start_alpha_campaign(text,text,text,text,bigint,bigint,bigint,text,text,text) OWNER TO trading_p3_owner;
        ALTER FUNCTION job_plane.worker_control_alpha_campaign_lease(text,text,text,text,integer,text) OWNER TO trading_p3_owner;
        ALTER FUNCTION job_plane.worker_finalize_alpha_campaign(text,text,text,text,text,text,text,text,text,text,integer,text,text,jsonb,text,text,boolean,text,jsonb) OWNER TO trading_p3_owner;
        ALTER FUNCTION job_plane.worker_recover_expired_alpha_campaign(text,text,text,text,text,text,bigint,bigint,bigint,text,text,text,text,text,text) OWNER TO trading_p3_owner;

        GRANT SELECT,INSERT,UPDATE ON public.jobs,public.job_attempts TO trading_p3_owner;
        GRANT SELECT,INSERT ON public.job_events TO trading_p3_owner;
        GRANT SELECT,INSERT ON public.domain_events,public.event_append_idempotency,public.event_outbox TO trading_p3_owner;
        GRANT EXECUTE ON FUNCTION public.append_domain_event(uuid,uuid,bigint,text,text,text,text) TO trading_p3_owner;

        REVOKE ALL PRIVILEGES ON TABLE public.p3_alpha_heads,
          public.p3_alpha_job_commits,public.p3_alpha_projection,
          public.p3_campaign_authorizations FROM PUBLIC,trading_jobs,trading_migrator,
          trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler;
        GRANT SELECT ON public.p3_alpha_projection TO trading_reader;

        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.api_enqueue_alpha_campaign(text,text,text,text,text,integer,text,text)
          FROM PUBLIC,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.worker_claim_alpha_campaign(text,text,text,integer,text,text)
          FROM PUBLIC,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.worker_commit_alpha_campaign(text,text,text,text,text,text)
          FROM PUBLIC,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.read_alpha_commit(text,text,text)
          FROM PUBLIC,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.api_cancel_alpha_campaign(text,text,text,text)
          FROM PUBLIC,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION job_plane.worker_start_alpha_campaign(text,text,text,text,bigint,bigint,bigint,text,text,text)
          FROM PUBLIC,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION job_plane.worker_control_alpha_campaign_lease(text,text,text,text,integer,text)
          FROM PUBLIC,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION job_plane.worker_finalize_alpha_campaign(text,text,text,text,text,text,text,text,text,text,integer,text,text,jsonb,text,text,boolean,text,jsonb)
          FROM PUBLIC,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION job_plane.worker_recover_expired_alpha_campaign(text,text,text,text,text,text,bigint,bigint,bigint,text,text,text,text,text,text)
          FROM PUBLIC,trading_jobs,trading_migrator,trading_reader,trading_job_api,trading_job_worker,trading_job_scheduler;
        GRANT EXECUTE ON FUNCTION job_plane.api_enqueue_alpha_campaign(text,text,text,text,text,integer,text,text) TO trading_job_api;
        GRANT EXECUTE ON FUNCTION job_plane.api_cancel_alpha_campaign(text,text,text,text) TO trading_job_api;
        GRANT EXECUTE ON FUNCTION job_plane.worker_claim_alpha_campaign(text,text,text,integer,text,text) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_commit_alpha_campaign(text,text,text,text,text,text) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.read_alpha_commit(text,text,text) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_start_alpha_campaign(text,text,text,text,bigint,bigint,bigint,text,text,text) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_control_alpha_campaign_lease(text,text,text,text,integer,text) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_finalize_alpha_campaign(text,text,text,text,text,text,text,text,text,text,integer,text,text,jsonb,text,text,boolean,text,jsonb) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_recover_expired_alpha_campaign(text,text,text,text,text,text,bigint,bigint,bigint,text,text,text,text,text,text) TO trading_job_worker;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0020 P3 alpha campaign authority is forward-only; use a reviewed forward repair"
    )

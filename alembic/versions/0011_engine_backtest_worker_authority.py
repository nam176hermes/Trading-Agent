"""Activate protected paper BACKTEST worker and durable result authority.

Revision ID: 0011_engine_backtest_worker_authority
Revises: 0010_engine_event_ledger

This is source authority only. Applying it requires the separately reviewed
migration workflow; importing this module never opens a database.
"""
from __future__ import annotations

from alembic import op


revision = "0011_engine_backtest_worker_authority"
down_revision = "0010_engine_event_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE public.engine_job_results (
          job_id varchar(64) PRIMARY KEY
            REFERENCES public.jobs(job_id) ON DELETE RESTRICT,
          batch_sha256 char(64) NOT NULL UNIQUE
            REFERENCES public.engine_event_batch_receipts(batch_sha256)
            ON DELETE RESTRICT,
          attempt_id varchar(64) NOT NULL,
          bound_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CHECK (job_id ~ '^job_[0-9a-f]{32}$'),
          CHECK (attempt_id ~ '^attempt_[0-9a-f]{32}$')
        );
        CREATE TRIGGER engine_job_results_append_only
          BEFORE UPDATE OR DELETE ON public.engine_job_results
          FOR EACH ROW EXECUTE FUNCTION public.engine_event_records_append_only();
        CREATE TRIGGER engine_job_results_reject_truncate
          BEFORE TRUNCATE ON public.engine_job_results
          FOR EACH STATEMENT EXECUTE FUNCTION public.engine_event_records_append_only();

        CREATE FUNCTION job_plane.paper_worker_job_allowed(
          p_job_type text,
          p_payload jsonb
        )
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $paper_worker_job_allowed$
          SELECT CASE
            WHEN p_job_type = 'SNAPSHOT' THEN true
            WHEN p_job_type IS NULL
              OR p_payload IS NULL
              OR p_job_type <> 'BACKTEST'
              OR jsonb_typeof(p_payload) <> 'object'
              OR p_payload IS DISTINCT FROM jsonb_build_object(
                   'engine_backtest', p_payload -> 'engine_backtest'
                 )
              OR jsonb_typeof(p_payload -> 'engine_backtest') <> 'object'
              OR p_payload -> 'engine_backtest' IS DISTINCT FROM
                   jsonb_build_object(
                     'engine_configuration',
                       p_payload #> '{engine_backtest,engine_configuration}',
                     'instrument_catalog',
                       p_payload #> '{engine_backtest,instrument_catalog}',
                     'strategy_configuration',
                       p_payload #> '{engine_backtest,strategy_configuration}',
                     'market_data',
                       p_payload #> '{engine_backtest,market_data}',
                     'start_time',
                       p_payload #> '{engine_backtest,start_time}',
                     'end_time',
                       p_payload #> '{engine_backtest,end_time}'
                   )
              OR NOT (
                SELECT bool_and(coalesce(
                  jsonb_typeof(artifact.value) = 'object'
                    AND artifact.value IS NOT DISTINCT FROM jsonb_build_object(
                      'artifact_id', artifact.value -> 'artifact_id',
                      'sha256', artifact.value -> 'sha256',
                      'media_type', artifact.value -> 'media_type'
                    )
                    AND jsonb_typeof(artifact.value -> 'artifact_id') = 'string'
                    AND jsonb_typeof(artifact.value -> 'sha256') = 'string'
                    AND jsonb_typeof(artifact.value -> 'media_type') = 'string'
                    AND artifact.value ->> 'artifact_id'
                      ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                    AND artifact.value ->> 'sha256' ~ '^[0-9a-f]{64}$'
                    AND artifact.value ->> 'media_type' IN (
                      'application/json', 'application/jsonl'
                    ),
                  false
                ))
                FROM (VALUES
                  (p_payload #> '{engine_backtest,engine_configuration}'),
                  (p_payload #> '{engine_backtest,instrument_catalog}'),
                  (p_payload #> '{engine_backtest,strategy_configuration}'),
                  (p_payload #> '{engine_backtest,market_data}')
                ) AS artifact(value)
              )
            THEN false
            WHEN jsonb_typeof(
                   p_payload #> '{engine_backtest,start_time}'
                 ) <> 'string'
              OR jsonb_typeof(
                   p_payload #> '{engine_backtest,end_time}'
                 ) <> 'string'
              OR left(p_payload #>> '{engine_backtest,start_time}', 4) = '0000'
              OR left(p_payload #>> '{engine_backtest,end_time}', 4) = '0000'
              OR p_payload #>> '{engine_backtest,start_time}'
                   !~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?Z$'
              OR p_payload #>> '{engine_backtest,end_time}'
                   !~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?Z$'
            THEN false
            WHEN to_char(to_date(
                   left(p_payload #>> '{engine_backtest,start_time}', 10),
                   'YYYY-MM-DD'
                 ), 'YYYY-MM-DD') IS DISTINCT FROM
                 left(p_payload #>> '{engine_backtest,start_time}', 10)
              OR to_char(to_date(
                   left(p_payload #>> '{engine_backtest,end_time}', 10),
                   'YYYY-MM-DD'
                 ), 'YYYY-MM-DD') IS DISTINCT FROM
                 left(p_payload #>> '{engine_backtest,end_time}', 10)
            THEN false
            WHEN rtrim(
                   p_payload #>> '{engine_backtest,start_time}', 'Z'
                 )::timestamp without time zone >= rtrim(
                   p_payload #>> '{engine_backtest,end_time}', 'Z'
                 )::timestamp without time zone
            THEN false
            ELSE true
          END
        $paper_worker_job_allowed$;

        CREATE FUNCTION job_plane.ingest_engine_job_result(
          p_job_id text,
          p_attempt_id text,
          p_worker_id text,
          p_lease_token text,
          p_batch_document text
        )
        RETURNS TABLE (
          batch_sha256 char(64),
          ingestion_digest char(64),
          job_id varchar(36),
          attempt_id varchar(40),
          engine_run_id uuid,
          event_count bigint,
          first_sequence bigint,
          last_sequence bigint,
          last_digest char(64)
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $ingest_engine_job_result$
        DECLARE
          accepted record;
          binding public.engine_job_results%ROWTYPE;
          current_job public.jobs%ROWTYPE;
          current_attempt public.job_attempts%ROWTYPE;
        BEGIN
          IF session_user <> 'trading_job_worker' THEN
            RAISE EXCEPTION 'engine job result authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_job_id IS NULL
             OR p_job_id !~ '^job_[0-9a-f]{32}$'
             OR p_attempt_id IS NULL
             OR p_attempt_id !~ '^attempt_[0-9a-f]{32}$'
             OR p_worker_id IS NULL
             OR p_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_lease_token IS NULL
             OR p_lease_token !~ '^[A-Za-z0-9_-]{16,128}$' THEN
            RAISE EXCEPTION 'engine job result identity rejected'
              USING ERRCODE = '22023';
          END IF;

          SELECT job_row.* INTO current_job
          FROM public.jobs AS job_row
          WHERE job_row.job_id = p_job_id
            AND job_row.job_type = 'BACKTEST'
            AND job_plane.paper_worker_job_allowed(
                  job_row.job_type, job_row.payload
                )
          FOR UPDATE;
          IF NOT FOUND
             OR current_job.state <> 'RUNNING'
             OR current_job.lease_owner IS DISTINCT FROM p_worker_id
             OR current_job.lease_token IS DISTINCT FROM p_lease_token
             OR current_job.lease_expires_at <= statement_timestamp() THEN
            RAISE EXCEPTION 'engine job result current job authority rejected'
              USING ERRCODE = 'P2D01';
          END IF;

          SELECT attempt_row.* INTO current_attempt
          FROM public.job_attempts AS attempt_row
          WHERE attempt_row.attempt_id = p_attempt_id
            AND attempt_row.job_id = p_job_id
          FOR UPDATE;
          IF NOT FOUND
             OR current_attempt.attempt_number <> current_job.attempt_count
             OR current_attempt.outcome <> 'RUNNING'
             OR current_attempt.worker_id IS DISTINCT FROM p_worker_id
             OR current_attempt.lease_token IS DISTINCT FROM p_lease_token
             OR current_attempt.lease_expires_at <= statement_timestamp() THEN
            RAISE EXCEPTION 'engine job result current attempt authority rejected'
              USING ERRCODE = 'P2D01';
          END IF;

          SELECT * INTO accepted
          FROM public.ingest_engine_event_batch(p_batch_document);
          IF NOT FOUND THEN
            RAISE EXCEPTION 'engine job result receipt is unavailable'
              USING ERRCODE = 'P2D01';
          END IF;
          IF accepted.job_id IS DISTINCT FROM p_job_id
             OR accepted.attempt_id IS DISTINCT FROM p_attempt_id THEN
            RAISE EXCEPTION 'engine job result batch authority rejected'
              USING ERRCODE = 'P2D01';
          END IF;

          INSERT INTO public.engine_job_results (
            job_id, batch_sha256, attempt_id
          ) VALUES (
            accepted.job_id, accepted.batch_sha256, accepted.attempt_id
          ) ON CONFLICT (job_id) DO NOTHING;

          SELECT result.* INTO binding
          FROM public.engine_job_results AS result
          WHERE result.job_id = accepted.job_id
          FOR SHARE;
          IF NOT FOUND
             OR binding.batch_sha256 IS DISTINCT FROM accepted.batch_sha256
             OR binding.attempt_id IS DISTINCT FROM accepted.attempt_id THEN
            RAISE EXCEPTION 'job already has a different engine result'
              USING ERRCODE = 'P2D01';
          END IF;
          RETURN QUERY SELECT
            accepted.batch_sha256,
            accepted.ingestion_digest,
            accepted.job_id,
            accepted.attempt_id,
            accepted.engine_run_id,
            accepted.event_count,
            accepted.first_sequence,
            accepted.last_sequence,
            accepted.last_digest;
        END;
        $ingest_engine_job_result$;

        -- The five 0006 functions are the reviewed transition authority.  Pin
        -- every complete PL/pgSQL body and catalog property before deriving a
        -- paper-only name with one exact predicate replacement.  This avoids
        -- inheriting any unreviewed source-function drift.
        DO $promote_paper_worker$
        DECLARE
          source_signatures text[] := ARRAY[
            'job_plane.worker_claim_snapshot(text,text,text,integer,text,text)',
            'job_plane.worker_start_snapshot(text,text,text,text,bigint,bigint,bigint,text,text,text)',
            'job_plane.worker_control_snapshot_lease(text,text,text,text,integer,text)',
            'job_plane.worker_finalize_snapshot(text,text,text,text,text,text,text,text,text,text,integer,text,text,jsonb,text,text,boolean,text,jsonb)',
            'job_plane.worker_recover_expired_snapshot(text,text,text,text,text,text,bigint,bigint,bigint,text,text,text,text,text,text)'
          ];
          source_names text[] := ARRAY[
            'worker_claim_snapshot',
            'worker_start_snapshot',
            'worker_control_snapshot_lease',
            'worker_finalize_snapshot',
            'worker_recover_expired_snapshot'
          ];
          target_names text[] := ARRAY[
            'worker_claim_paper',
            'worker_start_paper',
            'worker_control_paper_lease',
            'worker_finalize_paper',
            'worker_recover_expired_paper'
          ];
          source_hashes text[] := ARRAY[
            '0755231aaba81d1692581ff62b44f5babb03dcc0ed352687fc3a67b0ad2ee80a',
            'a7a402f871b4790f74dd774e5b8ce9417294dc78f6bca436062acddb933fa905',
            '47d09d4157c6992e8e21c3f6f08866e32176e8fda04a74bb2b4f49582ad5233d',
            '4a19cdf22297ee97dc7e31f354f42c020075e9a61e3cd411fb9dc8b160c88181',
            '90d7cda5361ccc9e2364821baad7a8a5c4ce56f5d3552b7ba835e980eb175b82'
          ];
          source_definition_hashes text[] := ARRAY[
            'fb08db2e2dfb7e1f796fda9217a97ea0c5a1716d4d0d94fac5a3eef4e3ce96bd',
            'fe83bcf5d045cc540f7f2c22e23116bf878b035fedfb1c94feead2360f3938b6',
            '3624b4b8934a496362a6ded5410b1f72353317ed36788e8852da25ea06256b74',
            '4ce0b71a3fefc88d390f6f3ffcd8d6ba3f7db997627ed2d11c268d2e99e7efd8',
            '899203b3cef59a7de5067613e77e184585a9d3ed28006d8038ede04ae01a9450'
          ];
          target_definition_hashes text[] := ARRAY[
            'f032ba72968d7b729ca6af4627a167089413a92f9813b4f945815f52ed9bd5ac',
            '21c8c57d2aa4836f2c83800d63f5b72e72fa8bc9ca2ee859994cc3b89e04afff',
            '67cf73d64efe4173fb6e01c699eb32ed8ce8c49137f12f6e7991702d093b4ab5',
            '3086de5117abf87c2dc0cdbced27cc928423786b3c1664cf10d89244dad5aab5',
            'a9a4560d441bce351dd7bff140f3a02cf263c3d160462096811e174c2eae112b'
          ];
          expected_results text[] := ARRAY[
            'TABLE(job_id text, job_type text, payload jsonb, attempt_number integer, max_attempts smallint, lease_expires_at timestamp with time zone)',
            'boolean', 'text', 'boolean', 'text'
          ];
          expected_argument_declarations text[] := ARRAY[
            'p_attempt_id text, p_worker_id text, p_lease_token text, p_lease_seconds integer, p_trace_id text, p_event_id text',
            'p_job_id text, p_attempt_id text, p_worker_id text, p_lease_token text, p_child_pid bigint, p_process_group_id bigint, p_process_start_ticks bigint, p_command_fingerprint text, p_trace_id text, p_event_id text',
            'p_job_id text, p_attempt_id text, p_worker_id text, p_lease_token text, p_lease_seconds integer, p_phase text',
            'p_job_id text, p_attempt_id text, p_worker_id text, p_lease_token text, p_expected_state text, p_expected_attempt_outcome text, p_final_state text, p_reason_code text, p_trace_id text, p_event_id text, p_exit_code integer, p_termination_reason text, p_result_hash text, p_result_metadata jsonb, p_error_code text, p_error_message text, p_retry boolean, p_retry_event_id text, p_event_metadata jsonb',
            'p_job_id text, p_attempt_id text, p_expected_state text, p_expected_attempt_outcome text, p_expected_lease_owner text, p_expected_lease_token text, p_expected_child_pid bigint, p_expected_process_group_id bigint, p_expected_process_start_ticks bigint, p_expected_command_fingerprint text, p_observation text, p_trace_id text, p_recovery_id text, p_event_id text, p_retry_event_id text'
          ];
          expected_argument_names text[] := ARRAY[
            'p_attempt_id,p_worker_id,p_lease_token,p_lease_seconds,p_trace_id,p_event_id,job_id,job_type,payload,attempt_number,max_attempts,lease_expires_at',
            'p_job_id,p_attempt_id,p_worker_id,p_lease_token,p_child_pid,p_process_group_id,p_process_start_ticks,p_command_fingerprint,p_trace_id,p_event_id',
            'p_job_id,p_attempt_id,p_worker_id,p_lease_token,p_lease_seconds,p_phase',
            'p_job_id,p_attempt_id,p_worker_id,p_lease_token,p_expected_state,p_expected_attempt_outcome,p_final_state,p_reason_code,p_trace_id,p_event_id,p_exit_code,p_termination_reason,p_result_hash,p_result_metadata,p_error_code,p_error_message,p_retry,p_retry_event_id,p_event_metadata',
            'p_job_id,p_attempt_id,p_expected_state,p_expected_attempt_outcome,p_expected_lease_owner,p_expected_lease_token,p_expected_child_pid,p_expected_process_group_id,p_expected_process_start_ticks,p_expected_command_fingerprint,p_observation,p_trace_id,p_recovery_id,p_event_id,p_retry_event_id'
          ];
          expected_argument_modes text[] := ARRAY[
            'i,i,i,i,i,i,t,t,t,t,t,t', '', '', '', ''
          ];
          expected_argument_counts integer[] := ARRAY[6, 10, 6, 19, 15];
          expected_returns_set boolean[] := ARRAY[true, false, false, false, false];
          expected_rows real[] := ARRAY[
            1000::real, 0::real, 0::real, 0::real, 0::real
          ];
          source_oid oid;
          target_oid oid;
          source_body text;
          promoted_body text;
          source_definition text;
          target_definition text;
          expected_target_definition text;
          promoted_definition text;
          reviewed_predicate text := 'job_row.job_type = ''SNAPSHOT''';
          paper_predicate text :=
            'job_plane.paper_worker_job_allowed(job_row.job_type, job_row.payload)';
          occurrences integer;
          function_index integer;
        BEGIN
          IF (SELECT version_num FROM public.alembic_version)
             <> '0010_engine_event_ledger' THEN
            RAISE EXCEPTION '0011 parent head changed during migration';
          END IF;
          IF (
            SELECT count(*)
            FROM pg_catalog.pg_proc AS procedure_row
            WHERE procedure_row.pronamespace = 'job_plane'::regnamespace
              AND procedure_row.proname = ANY(source_names)
          ) <> array_length(source_names, 1) THEN
            RAISE EXCEPTION 'paper worker source identity set drifted';
          END IF;
          FOR function_index IN 1..array_length(source_signatures, 1) LOOP
            source_oid := source_signatures[function_index]::regprocedure;
            SELECT procedure_row.prosrc,
                   pg_get_functiondef(procedure_row.oid)
            INTO source_body, source_definition
            FROM pg_catalog.pg_proc AS procedure_row
            JOIN pg_catalog.pg_language AS language_row
              ON language_row.oid = procedure_row.prolang
            WHERE procedure_row.oid = source_oid
              AND procedure_row.pronamespace = 'job_plane'::regnamespace
              AND pg_get_userbyid(procedure_row.proowner) = 'trading_owner'
              AND language_row.lanname = 'plpgsql'
              AND procedure_row.prosecdef
              AND NOT procedure_row.proleakproof
              AND procedure_row.prokind = 'f'
              AND procedure_row.provolatile = 'v'
              AND procedure_row.proparallel = 'u'
              AND procedure_row.proconfig = ARRAY['search_path=pg_catalog']
              AND procedure_row.pronargs =
                    expected_argument_counts[function_index]
              AND procedure_row.pronargdefaults = 0
              AND procedure_row.proargdefaults IS NULL
              AND procedure_row.provariadic = 0
              AND array_to_string(procedure_row.proargnames, ',') =
                    expected_argument_names[function_index]
              AND coalesce(
                    array_to_string(procedure_row.proargmodes, ','), ''
                  ) = expected_argument_modes[function_index]
              AND NOT procedure_row.proisstrict
              AND procedure_row.proretset =
                    expected_returns_set[function_index]
              AND procedure_row.procost = 100
              AND procedure_row.prorows = expected_rows[function_index]
              AND procedure_row.prosupport = 0
              AND procedure_row.protrftypes IS NULL
              AND procedure_row.probin IS NULL
              AND procedure_row.prosqlbody IS NULL
              AND pg_get_function_result(procedure_row.oid)
                    = expected_results[function_index]
              AND encode(public.digest(
                    convert_to(procedure_row.prosrc, 'UTF8'), 'sha256'
                  ), 'hex') = source_hashes[function_index]
              AND encode(public.digest(
                    convert_to(
                      pg_get_functiondef(procedure_row.oid), 'UTF8'
                    ), 'sha256'
                  ), 'hex') = source_definition_hashes[function_index]
              AND has_function_privilege(
                    'trading_job_worker', procedure_row.oid, 'EXECUTE'
                  )
              AND NOT has_function_privilege(
                    'trading_job_api', procedure_row.oid, 'EXECUTE'
                  )
              AND NOT has_function_privilege(
                    'trading_job_scheduler', procedure_row.oid, 'EXECUTE'
                  );
            IF NOT FOUND THEN
              RAISE EXCEPTION 'paper worker source body or catalog drifted: %',
                source_names[function_index];
            END IF;
            IF EXISTS (
              SELECT 1
              FROM pg_catalog.pg_proc AS procedure_row
              CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(
                procedure_row.proacl,
                pg_catalog.acldefault('f', procedure_row.proowner)
              )) AS acl
              LEFT JOIN pg_catalog.pg_roles AS grantee_role
                ON grantee_role.oid = acl.grantee
              WHERE procedure_row.oid = source_oid
                AND acl.privilege_type = 'EXECUTE'
                AND (
                  acl.is_grantable
                  OR acl.grantee = 0
                  OR (
                    acl.grantee <> procedure_row.proowner
                    AND grantee_role.rolname IS DISTINCT FROM
                          'trading_job_worker'
                  )
                )
            ) THEN
              RAISE EXCEPTION 'paper worker source ACL drifted: %',
                source_names[function_index];
            END IF;
            occurrences := (
              length(source_body)
              - length(replace(source_body, reviewed_predicate, ''))
            ) / length(reviewed_predicate);
            IF occurrences <> 1 THEN
              RAISE EXCEPTION 'paper worker source predicate drifted: %',
                source_names[function_index];
            END IF;
            expected_target_definition := replace(
              source_definition,
              source_names[function_index],
              target_names[function_index]
            );
            promoted_body := replace(
              source_body, reviewed_predicate, paper_predicate
            );
            expected_target_definition := replace(
              expected_target_definition,
              reviewed_predicate,
              paper_predicate
            );
            promoted_definition := format(
              'CREATE FUNCTION job_plane.%I(%s) RETURNS %s '
              'LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE '
              'SET search_path = pg_catalog AS %L',
              target_names[function_index],
              expected_argument_declarations[function_index],
              expected_results[function_index],
              promoted_body
            );
            IF expected_target_definition = source_definition
               OR position(target_names[function_index] IN promoted_definition) = 0
               OR position(paper_predicate IN promoted_definition) = 0 THEN
                RAISE EXCEPTION 'paper worker promotion failed closed: %',
                source_names[function_index];
            END IF;
            EXECUTE promoted_definition;
            target_oid := replace(
              source_signatures[function_index],
              source_names[function_index],
              target_names[function_index]
            )::regprocedure;
            SELECT pg_get_functiondef(procedure_row.oid)
            INTO target_definition
            FROM pg_catalog.pg_proc AS procedure_row
            WHERE procedure_row.oid = target_oid;
            IF (
              SELECT procedure_row.prosrc IS DISTINCT FROM promoted_body
                     OR encode(public.digest(
                          convert_to(procedure_row.prosrc, 'UTF8'), 'sha256'
                        ), 'hex') IS DISTINCT FROM encode(public.digest(
                          convert_to(promoted_body, 'UTF8'), 'sha256'
                        ), 'hex')
                     OR procedure_row.pronargs IS DISTINCT FROM
                          expected_argument_counts[function_index]
                     OR procedure_row.pronargdefaults IS DISTINCT FROM 0
                     OR procedure_row.proargdefaults IS NOT NULL
                     OR procedure_row.provariadic IS DISTINCT FROM 0
                     OR procedure_row.protrftypes IS NOT NULL
                     OR array_to_string(procedure_row.proargnames, ',')
                          IS DISTINCT FROM
                          expected_argument_names[function_index]
                     OR coalesce(
                          array_to_string(procedure_row.proargmodes, ','), ''
                        ) IS DISTINCT FROM
                          expected_argument_modes[function_index]
                     OR target_definition IS DISTINCT FROM
                          expected_target_definition
                     OR encode(public.digest(
                          convert_to(target_definition, 'UTF8'), 'sha256'
                        ), 'hex') IS DISTINCT FROM encode(public.digest(
                          convert_to(
                            expected_target_definition, 'UTF8'
                          ), 'sha256'
                        ), 'hex')
                     OR encode(public.digest(
                          convert_to(target_definition, 'UTF8'), 'sha256'
                        ), 'hex') IS DISTINCT FROM
                          target_definition_hashes[function_index]
              FROM pg_catalog.pg_proc AS procedure_row
              WHERE procedure_row.oid = target_oid
            ) IS DISTINCT FROM false THEN
              RAISE EXCEPTION 'paper worker promoted definition postflight failed: %',
                target_names[function_index];
            END IF;
          END LOOP;
        END;
        $promote_paper_worker$;

        REVOKE ALL PRIVILEGES ON TABLE public.engine_job_results FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.paper_worker_job_allowed(text, jsonb)
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.ingest_engine_job_result(text, text, text, text, text)
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION job_plane.worker_claim_paper(
          text, text, text, integer, text, text
        ) FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION job_plane.worker_start_paper(
          text, text, text, text, bigint, bigint, bigint, text, text, text
        ) FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION job_plane.worker_control_paper_lease(
          text, text, text, text, integer, text
        ) FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION job_plane.worker_finalize_paper(
          text, text, text, text, text, text, text, text, text, text,
          integer, text, text, jsonb, text, text, boolean, text, jsonb
        ) FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.worker_recover_expired_paper(
            text, text, text, text, text, text, bigint, bigint, bigint,
            text, text, text, text, text, text
          ) FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
                 trading_job_api, trading_job_worker, trading_job_scheduler;
        GRANT SELECT ON TABLE public.engine_event_batch_receipts
          TO trading_job_worker;
        GRANT SELECT ON TABLE public.engine_job_results
          TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.ingest_engine_job_result(
          text, text, text, text, text
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_claim_paper(
          text, text, text, integer, text, text
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_start_paper(
          text, text, text, text, bigint, bigint, bigint, text, text, text
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_control_paper_lease(
          text, text, text, text, integer, text
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_finalize_paper(
          text, text, text, text, text, text, text, text, text, text,
          integer, text, text, jsonb, text, text, boolean, text, jsonb
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_recover_expired_paper(
          text, text, text, text, text, text, bigint, bigint, bigint,
          text, text, text, text, text, text
        ) TO trading_job_worker;

        DO $paper_worker_postflight$
        BEGIN
          IF EXISTS (
            WITH expected(
              procedure_name, identity_types, result_type, argument_names,
              argument_modes, argument_count, returns_set, expected_rows,
              body_sha256, definition_sha256
            ) AS (
              VALUES
                ('worker_claim_paper',
                 'text, text, text, integer, text, text',
                 'TABLE(job_id text, job_type text, payload jsonb, attempt_number integer, max_attempts smallint, lease_expires_at timestamp with time zone)',
                 'p_attempt_id,p_worker_id,p_lease_token,p_lease_seconds,p_trace_id,p_event_id,job_id,job_type,payload,attempt_number,max_attempts,lease_expires_at',
                 'i,i,i,i,i,i,t,t,t,t,t,t', 6, true, 1000::real,
                 '813007621b82e3b8abf168641e512309d16d2fdbf85c14fcb4fd9058f2a35a59',
                 'f032ba72968d7b729ca6af4627a167089413a92f9813b4f945815f52ed9bd5ac'),
                ('worker_start_paper',
                 'text, text, text, text, bigint, bigint, bigint, text, text, text',
                 'boolean',
                 'p_job_id,p_attempt_id,p_worker_id,p_lease_token,p_child_pid,p_process_group_id,p_process_start_ticks,p_command_fingerprint,p_trace_id,p_event_id',
                 '', 10, false, 0::real,
                 '561169daa1d7699a1636575da0f0db99852ae440b861765fcbd9e4820ce4c1a0',
                 '21c8c57d2aa4836f2c83800d63f5b72e72fa8bc9ca2ee859994cc3b89e04afff'),
                ('worker_control_paper_lease',
                 'text, text, text, text, integer, text', 'text',
                 'p_job_id,p_attempt_id,p_worker_id,p_lease_token,p_lease_seconds,p_phase',
                 '', 6, false, 0::real,
                 '233f8ba48fe8299b4b2e31a3086b99d08d87389c367d9f955659ba9ea725dbe6',
                 '67cf73d64efe4173fb6e01c699eb32ed8ce8c49137f12f6e7991702d093b4ab5'),
                ('worker_finalize_paper',
                 'text, text, text, text, text, text, text, text, text, text, integer, text, text, jsonb, text, text, boolean, text, jsonb',
                 'boolean',
                 'p_job_id,p_attempt_id,p_worker_id,p_lease_token,p_expected_state,p_expected_attempt_outcome,p_final_state,p_reason_code,p_trace_id,p_event_id,p_exit_code,p_termination_reason,p_result_hash,p_result_metadata,p_error_code,p_error_message,p_retry,p_retry_event_id,p_event_metadata',
                 '', 19, false, 0::real,
                 '004bb49c1cca92356caac48879ffeaa18c0934291b4b13a25f278caf4c53639c',
                 '3086de5117abf87c2dc0cdbced27cc928423786b3c1664cf10d89244dad5aab5'),
                ('worker_recover_expired_paper',
                 'text, text, text, text, text, text, bigint, bigint, bigint, text, text, text, text, text, text',
                 'text',
                 'p_job_id,p_attempt_id,p_expected_state,p_expected_attempt_outcome,p_expected_lease_owner,p_expected_lease_token,p_expected_child_pid,p_expected_process_group_id,p_expected_process_start_ticks,p_expected_command_fingerprint,p_observation,p_trace_id,p_recovery_id,p_event_id,p_retry_event_id',
                 '', 15, false, 0::real,
                 'b084073af25fa0fb5d06506815a9496cb56c8620e10e0997ede767eb90e592f2',
                 'a9a4560d441bce351dd7bff140f3a02cf263c3d160462096811e174c2eae112b')
            ),
            actual(
              procedure_name, identity_types, result_type, argument_names,
              argument_modes, argument_count, returns_set, expected_rows,
              body_sha256, definition_sha256
            ) AS (
              SELECT procedure_row.proname,
                     oidvectortypes(procedure_row.proargtypes),
                     pg_get_function_result(procedure_row.oid),
                     array_to_string(procedure_row.proargnames, ','),
                     coalesce(
                       array_to_string(procedure_row.proargmodes, ','), ''
                     ),
                     procedure_row.pronargs::integer,
                     procedure_row.proretset,
                     procedure_row.prorows,
                     encode(public.digest(
                       convert_to(procedure_row.prosrc, 'UTF8'), 'sha256'
                     ), 'hex'),
                     encode(public.digest(
                       convert_to(
                         pg_get_functiondef(procedure_row.oid), 'UTF8'
                       ), 'sha256'
                     ), 'hex')
              FROM pg_catalog.pg_proc AS procedure_row
              JOIN pg_catalog.pg_language AS language_row
                ON language_row.oid = procedure_row.prolang
              JOIN expected
                ON expected.procedure_name = procedure_row.proname
               AND expected.identity_types =
                     oidvectortypes(procedure_row.proargtypes)
              WHERE procedure_row.pronamespace = 'job_plane'::regnamespace
                AND pg_get_userbyid(procedure_row.proowner) = 'trading_owner'
                AND language_row.lanname = 'plpgsql'
                AND procedure_row.prosecdef
                AND NOT procedure_row.proleakproof
                AND procedure_row.prokind = 'f'
                AND procedure_row.provolatile = 'v'
                AND procedure_row.proparallel = 'u'
                AND procedure_row.proconfig = ARRAY['search_path=pg_catalog']
                AND procedure_row.pronargdefaults = 0
                AND procedure_row.proargdefaults IS NULL
                AND procedure_row.provariadic = 0
                AND NOT procedure_row.proisstrict
                AND procedure_row.procost = 100
                AND procedure_row.prosupport = 0
                AND procedure_row.protrftypes IS NULL
                AND procedure_row.probin IS NULL
                AND procedure_row.prosqlbody IS NULL
            ),
            differences AS (
              (SELECT * FROM expected EXCEPT SELECT * FROM actual)
              UNION ALL
              (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            )
            SELECT 1 FROM differences
          ) OR (
            SELECT count(*)
            FROM pg_catalog.pg_proc AS procedure_row
            WHERE procedure_row.pronamespace = 'job_plane'::regnamespace
              AND procedure_row.proname IN (
                'worker_claim_paper', 'worker_start_paper',
                'worker_control_paper_lease', 'worker_finalize_paper',
                'worker_recover_expired_paper'
              )
          ) <> 5 THEN
            RAISE EXCEPTION 'paper worker target catalog postflight failed';
          END IF;

          IF EXISTS (
            WITH expected(
              procedure_name, identity_types, result_type, language_name,
              security_definer, volatility, parallel_safety, argument_names,
              argument_modes, argument_count, returns_set, expected_rows,
              body_sha256
            ) AS (
              VALUES
                ('paper_worker_job_allowed', 'text, jsonb', 'boolean',
                 'sql', false, 'i'::"char", 's'::"char",
                 'p_job_type,p_payload', '', 2, false, 0::real,
                 '342200fa9e9feefd84031d758232273aef8aa00c05880b5ee16f42fa5b967253'),
                ('ingest_engine_job_result',
                 'text, text, text, text, text',
                 'TABLE(batch_sha256 character, ingestion_digest character, job_id character varying, attempt_id character varying, engine_run_id uuid, event_count bigint, first_sequence bigint, last_sequence bigint, last_digest character)',
                 'plpgsql', true, 'v'::"char", 'u'::"char",
                 'p_job_id,p_attempt_id,p_worker_id,p_lease_token,p_batch_document,batch_sha256,ingestion_digest,job_id,attempt_id,engine_run_id,event_count,first_sequence,last_sequence,last_digest',
                 'i,i,i,i,i,t,t,t,t,t,t,t,t,t', 5, true, 1000::real,
                 '10e24e84094478e5b4994dab8ffdb22dec021ca235cfe51a05e94ae49e62fd34')
            ),
            actual(
              procedure_name, identity_types, result_type, language_name,
              security_definer, volatility, parallel_safety, argument_names,
              argument_modes, argument_count, returns_set, expected_rows,
              body_sha256
            ) AS (
              SELECT procedure_row.proname,
                     oidvectortypes(procedure_row.proargtypes),
                     pg_get_function_result(procedure_row.oid),
                     language_row.lanname,
                     procedure_row.prosecdef,
                     procedure_row.provolatile,
                     procedure_row.proparallel,
                     array_to_string(procedure_row.proargnames, ','),
                     coalesce(
                       array_to_string(procedure_row.proargmodes, ','), ''
                     ),
                     procedure_row.pronargs::integer,
                     procedure_row.proretset,
                     procedure_row.prorows,
                     encode(public.digest(
                       convert_to(procedure_row.prosrc, 'UTF8'), 'sha256'
                     ), 'hex')
              FROM pg_catalog.pg_proc AS procedure_row
              JOIN pg_catalog.pg_language AS language_row
                ON language_row.oid = procedure_row.prolang
              JOIN expected
                ON expected.procedure_name = procedure_row.proname
               AND expected.identity_types =
                     oidvectortypes(procedure_row.proargtypes)
              WHERE procedure_row.pronamespace = 'job_plane'::regnamespace
                AND pg_get_userbyid(procedure_row.proowner) = 'trading_owner'
                AND NOT procedure_row.proleakproof
                AND procedure_row.proconfig = ARRAY['search_path=pg_catalog']
                AND procedure_row.prokind = 'f'
                AND procedure_row.pronargdefaults = 0
                AND procedure_row.proargdefaults IS NULL
                AND procedure_row.provariadic = 0
                AND NOT procedure_row.proisstrict
                AND procedure_row.procost = 100
                AND procedure_row.prosupport = 0
                AND procedure_row.protrftypes IS NULL
                AND procedure_row.probin IS NULL
                AND procedure_row.prosqlbody IS NULL
            ),
            differences AS (
              (SELECT * FROM expected EXCEPT SELECT * FROM actual)
              UNION ALL
              (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            )
            SELECT 1 FROM differences
          ) OR (
            SELECT count(*)
            FROM pg_catalog.pg_proc AS procedure_row
            WHERE procedure_row.pronamespace = 'job_plane'::regnamespace
              AND procedure_row.proname IN (
                'paper_worker_job_allowed', 'ingest_engine_job_result'
              )
          ) <> 2 THEN
            RAISE EXCEPTION 'paper worker support catalog postflight failed';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc AS procedure_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(
              procedure_row.proacl,
              pg_catalog.acldefault('f', procedure_row.proowner)
            )) AS acl
            LEFT JOIN pg_catalog.pg_roles AS grantee_role
              ON grantee_role.oid = acl.grantee
            WHERE procedure_row.pronamespace = 'job_plane'::regnamespace
              AND procedure_row.proname IN (
                'worker_claim_paper', 'worker_start_paper',
                'worker_control_paper_lease', 'worker_finalize_paper',
                'worker_recover_expired_paper', 'ingest_engine_job_result'
              )
              AND acl.privilege_type = 'EXECUTE'
              AND (
                acl.is_grantable
                OR acl.grantee = 0
                OR (
                  acl.grantee <> procedure_row.proowner
                  AND grantee_role.rolname IS DISTINCT FROM
                        'trading_job_worker'
                )
              )
          ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc AS procedure_row
            CROSS JOIN unnest(ARRAY[
              'trading_job_api', 'trading_job_worker',
              'trading_job_scheduler'
            ]) AS role_name(name)
            WHERE procedure_row.pronamespace = 'job_plane'::regnamespace
              AND procedure_row.proname IN (
                'worker_claim_paper', 'worker_start_paper',
                'worker_control_paper_lease', 'worker_finalize_paper',
                'worker_recover_expired_paper', 'ingest_engine_job_result'
              )
              AND has_function_privilege(
                    role_name.name, procedure_row.oid, 'EXECUTE'
                  ) IS DISTINCT FROM
                  (role_name.name = 'trading_job_worker')
          ) THEN
            RAISE EXCEPTION 'paper worker target ACL postflight failed';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM unnest(ARRAY[
              'trading_job_api', 'trading_job_worker',
              'trading_job_scheduler'
            ]) AS role_name(name)
            WHERE has_function_privilege(
              role_name.name,
              'job_plane.paper_worker_job_allowed(text,jsonb)'::regprocedure,
              'EXECUTE'
            )
          ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc AS procedure_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(
              procedure_row.proacl,
              pg_catalog.acldefault('f', procedure_row.proowner)
            )) AS acl
            WHERE procedure_row.oid =
                    'job_plane.paper_worker_job_allowed(text,jsonb)'::regprocedure
              AND acl.privilege_type = 'EXECUTE'
              AND (
                acl.is_grantable
                OR acl.grantee = 0
                OR acl.grantee <> procedure_row.proowner
              )
          ) THEN
            RAISE EXCEPTION 'paper worker support ACL postflight failed';
          END IF;
        END;
        $paper_worker_postflight$;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0011 engine BACKTEST worker authority is forward-only; use a reviewed repair"
    )

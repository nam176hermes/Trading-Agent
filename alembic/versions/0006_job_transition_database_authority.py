"""Make PostgreSQL the atomic authority for durable-job transitions.

Revision ID: 0006_job_transition_database_authority
Revises: 0005_job_plane_role_split

This forward-only migration removes direct state and event DML from runtime
roles.  Each role receives only fixed, SECURITY DEFINER capabilities that
mutate a job and append the matching event in the same database transaction.
"""
from __future__ import annotations

from alembic import op


revision = "0006_job_transition_database_authority"
down_revision = "0005_job_plane_role_split"
branch_labels = None
depends_on = None


RUNTIME_ROLES = (
    "trading_job_api",
    "trading_job_worker",
    "trading_job_scheduler",
)

EXPECTED_0005_POLICIES = (
    "job_plane_api_artifacts_select",
    "job_plane_api_attempts_select",
    "job_plane_api_events_insert",
    "job_plane_api_events_select",
    "job_plane_api_jobs_cancel",
    "job_plane_api_jobs_insert",
    "job_plane_api_jobs_select",
    "job_plane_scheduler_events_insert",
    "job_plane_scheduler_events_select",
    "job_plane_scheduler_heartbeats_insert",
    "job_plane_scheduler_jobs_insert",
    "job_plane_scheduler_jobs_select",
    "job_plane_worker_artifacts_insert",
    "job_plane_worker_attempts_insert",
    "job_plane_worker_attempts_select",
    "job_plane_worker_attempts_update",
    "job_plane_worker_events_insert",
    "job_plane_worker_events_select",
    "job_plane_worker_heartbeats_insert",
    "job_plane_worker_heartbeats_select",
    "job_plane_worker_heartbeats_update",
    "job_plane_worker_jobs_select",
    "job_plane_worker_jobs_update",
)

EXPECTED_0006_POLICIES = tuple(
    name
    for name in EXPECTED_0005_POLICIES
    if name
    not in {
        "job_plane_api_events_insert",
        "job_plane_api_jobs_cancel",
        "job_plane_api_jobs_insert",
        "job_plane_scheduler_events_insert",
        "job_plane_scheduler_jobs_insert",
        "job_plane_worker_attempts_insert",
        "job_plane_worker_attempts_update",
        "job_plane_worker_events_insert",
        "job_plane_worker_jobs_update",
    }
)


def _policy_values(names: tuple[str, ...]) -> str:
    return ",\n".join(f"('{name}')" for name in names)


def upgrade() -> None:
    # Do not install capabilities onto an approximate 0005 database.  This
    # preflight deliberately checks the frozen authority surface, role shape,
    # object ownership, trigger identity, and event history before any DDL.
    op.execute(
        rf"""
        DO $job_transition_preflight$
        DECLARE
          active_head text;
        BEGIN
          IF current_user <> 'trading_owner'
             OR session_user <> 'trading_owner' THEN
            RAISE EXCEPTION '0006 requires exact trading_owner session'
              USING ERRCODE = '42501';
          END IF;

          IF current_setting('server_version_num')::integer / 10000 <> 16 THEN
            RAISE EXCEPTION '0006 requires PostgreSQL 16';
          END IF;

          IF (
            SELECT pg_get_userbyid(database_row.datdba)
            FROM pg_catalog.pg_database database_row
            WHERE database_row.datname = current_database()
          ) <> 'trading_owner' THEN
            RAISE EXCEPTION '0006 requires trading_owner database ownership';
          END IF;

          IF (
            SELECT pg_get_userbyid(namespace_row.nspowner)
            FROM pg_catalog.pg_namespace namespace_row
            WHERE namespace_row.nspname = 'public'
          ) <> 'trading_owner' THEN
            RAISE EXCEPTION '0006 requires trading_owner public-schema owner';
          END IF;

          SELECT CASE WHEN count(*) = 1 THEN min(version_num) END
          INTO active_head
          FROM public.alembic_version;
          IF active_head IS DISTINCT FROM '0005_job_plane_role_split' THEN
            RAISE EXCEPTION '0006 requires exact 0005 head';
          END IF;

          IF to_regnamespace('job_plane') IS NOT NULL THEN
            RAISE EXCEPTION 'job_plane authority schema already exists';
          END IF;

          IF (
            SELECT count(*)
            FROM pg_catalog.pg_class relation
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname IN (
                'alembic_version', 'jobs', 'job_attempts', 'job_events',
                'scheduler_heartbeats', 'job_artifacts', 'worker_heartbeats'
              )
              AND pg_get_userbyid(relation.relowner) = 'trading_owner'
          ) <> 7 THEN
            RAISE EXCEPTION '0005 relation ownership is not exact';
          END IF;

          IF (
            SELECT count(*)
            FROM pg_catalog.pg_roles role_row
            WHERE role_row.rolname IN (
              'trading_job_api', 'trading_job_worker',
              'trading_job_scheduler'
            )
              AND role_row.rolcanlogin
              AND NOT role_row.rolsuper
              AND NOT role_row.rolcreatedb
              AND NOT role_row.rolcreaterole
              AND NOT role_row.rolinherit
              AND NOT role_row.rolreplication
              AND NOT role_row.rolbypassrls
          ) <> 3 THEN
            RAISE EXCEPTION '0005 runtime role shape is not exact';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_auth_members membership
            JOIN pg_catalog.pg_roles granted_role
              ON granted_role.oid = membership.roleid
            JOIN pg_catalog.pg_roles member_role
              ON member_role.oid = membership.member
            WHERE granted_role.rolname IN (
              'trading_jobs', 'trading_job_api', 'trading_job_worker',
              'trading_job_scheduler'
            )
               OR member_role.rolname IN (
              'trading_jobs', 'trading_job_api', 'trading_job_worker',
              'trading_job_scheduler'
            )
          ) THEN
            RAISE EXCEPTION 'job-plane roles must have no memberships';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_stat_activity
            WHERE usename IN (
              'trading_jobs', 'trading_job_api', 'trading_job_worker',
              'trading_job_scheduler'
            )
              AND pid <> pg_backend_pid()
          ) THEN
            RAISE EXCEPTION 'job-plane sessions must be zero for 0006';
          END IF;

          IF NOT has_any_column_privilege(
                   'trading_job_api', 'public.jobs', 'INSERT'
                 )
             OR NOT has_column_privilege(
                   'trading_job_api', 'public.jobs', 'state', 'UPDATE'
                 )
             OR NOT has_any_column_privilege(
                   'trading_job_api', 'public.job_events', 'INSERT'
                 )
             OR NOT has_column_privilege(
                   'trading_job_worker', 'public.jobs', 'state', 'UPDATE'
                 )
             OR NOT has_any_column_privilege(
                   'trading_job_worker', 'public.job_attempts', 'INSERT'
                 )
             OR NOT has_column_privilege(
                   'trading_job_worker', 'public.job_attempts',
                   'outcome', 'UPDATE'
                 )
             OR NOT has_any_column_privilege(
                   'trading_job_worker', 'public.job_events', 'INSERT'
                 )
             OR NOT has_any_column_privilege(
                   'trading_job_scheduler', 'public.jobs', 'INSERT'
                 )
             OR NOT has_any_column_privilege(
                   'trading_job_scheduler', 'public.job_events', 'INSERT'
                 ) THEN
            RAISE EXCEPTION '0005 mutation ACL surface is not exact';
          END IF;

          IF EXISTS (
            WITH expected(policy_name) AS (
              VALUES {_policy_values(EXPECTED_0005_POLICIES)}
            ),
            actual(policy_name) AS (
              SELECT policy.polname
              FROM pg_catalog.pg_policy policy
              JOIN pg_catalog.pg_class relation
                ON relation.oid = policy.polrelid
              WHERE relation.relnamespace = 'public'::regnamespace
                AND relation.relname IN (
                  'jobs', 'job_attempts', 'job_events',
                  'scheduler_heartbeats', 'job_artifacts', 'worker_heartbeats'
                )
            ),
            differences(policy_name) AS (
              (SELECT * FROM expected EXCEPT SELECT * FROM actual)
              UNION ALL
              (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            )
            SELECT 1 FROM differences
          ) THEN
            RAISE EXCEPTION '0005 RLS policy catalog is not exact';
          END IF;

          IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_trigger trigger_row
            WHERE trigger_row.tgrelid = 'public.job_events'::regclass
              AND trigger_row.tgname = 'trg_job_events_append_only'
              AND NOT trigger_row.tgisinternal
              AND trigger_row.tgenabled = 'O'
              AND trigger_row.tgfoid =
                    'public.reject_job_event_mutation()'::regprocedure
          ) OR NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_trigger trigger_row
            WHERE trigger_row.tgrelid = 'public.jobs'::regclass
              AND trigger_row.tgname = 'trg_jobs_job_api_cancellation'
              AND NOT trigger_row.tgisinternal
              AND trigger_row.tgenabled = 'O'
              AND trigger_row.tgfoid =
                    'public.enforce_job_api_cancellation()'::regprocedure
          ) THEN
            RAISE EXCEPTION '0005 protected trigger catalog is not exact';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM public.jobs job_row
            LEFT JOIN LATERAL (
              SELECT count(*) AS event_count,
                     count(DISTINCT event_row.sequence) AS distinct_count,
                     min(event_row.sequence) AS minimum_sequence,
                     max(event_row.sequence) AS maximum_sequence
              FROM public.job_events event_row
              WHERE event_row.job_id = job_row.job_id
            ) statistics ON true
            LEFT JOIN LATERAL (
              SELECT event_row.to_state
              FROM public.job_events event_row
              WHERE event_row.job_id = job_row.job_id
              ORDER BY event_row.sequence DESC
              LIMIT 1
            ) latest ON true
            WHERE statistics.event_count = 0
               OR statistics.minimum_sequence <> 1
               OR statistics.maximum_sequence <> statistics.event_count
               OR statistics.distinct_count <> statistics.event_count
               OR latest.to_state IS DISTINCT FROM job_row.state
          ) THEN
            RAISE EXCEPTION 'job/event history is not contiguous and current';
          END IF;
        END
        $job_transition_preflight$;
        """
    )

    # Alembic's stock version table is VARCHAR(32), while this mandated
    # revision identity is 38 characters.  Widen it before Alembic stamps the
    # new head after upgrade() returns.
    op.execute(
        "ALTER TABLE public.alembic_version "
        "ALTER COLUMN version_num TYPE varchar(64)"
    )

    op.execute(
        r"""
        CREATE SCHEMA job_plane AUTHORIZATION trading_owner;
        REVOKE ALL PRIVILEGES ON SCHEMA job_plane FROM PUBLIC;
        GRANT USAGE ON SCHEMA job_plane
          TO trading_job_api, trading_job_worker, trading_job_scheduler;
        ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA job_plane
          REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
        """
    )

    # Operator enqueue and cancellation capabilities.
    op.execute(
        r"""
        CREATE FUNCTION job_plane.api_enqueue_snapshot(
          p_job_id text,
          p_payload jsonb,
          p_payload_fingerprint text,
          p_idempotency_key text,
          p_actor_id text,
          p_priority smallint,
          p_trace_id text,
          p_event_id text
        )
        RETURNS TABLE(job_id text, outcome text)
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          existing_job public.jobs%ROWTYPE;
          inserted_job_id text;
        BEGIN
          IF session_user <> 'trading_job_api' THEN
            RAISE EXCEPTION 'operator enqueue authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_job_id IS NULL
             OR p_job_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_event_id IS NULL
             OR p_event_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_job_id = p_event_id
             OR p_payload IS DISTINCT FROM
                  pg_catalog.jsonb_build_object(
                    'scope', 'default', 'requested_as_of', NULL
                  )
             OR p_payload_fingerprint IS NULL
             OR p_payload_fingerprint IS DISTINCT FROM
                  'dc993577d7fe81a0fc6b23e281e0b7e2a182d557143cfa312d21078271b4091a'
             OR p_idempotency_key IS NULL
             OR p_idempotency_key !~
                  '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_idempotency_key ~ '^schedule:'
             OR p_actor_id IS NULL
             OR p_actor_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_priority IS NULL OR p_priority < 0 OR p_priority > 100
             OR p_trace_id IS NULL
             OR p_trace_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
            RAISE EXCEPTION 'operator enqueue input rejected'
              USING ERRCODE = '22023';
          END IF;

          INSERT INTO public.jobs (
            job_id, job_type, state, payload, payload_fingerprint,
            idempotency_key, actor_type, actor_id, priority, max_attempts
          ) VALUES (
            p_job_id, 'SNAPSHOT', 'QUEUED', p_payload,
            p_payload_fingerprint, p_idempotency_key, 'OPERATOR', p_actor_id,
            p_priority, 2
          )
          ON CONFLICT (job_type, idempotency_key) DO NOTHING
          RETURNING public.jobs.job_id INTO inserted_job_id;

          IF inserted_job_id IS NOT NULL THEN
            INSERT INTO public.job_events (
              event_id, job_id, attempt_id, sequence, from_state, to_state,
              reason_code, actor_type, actor_id, trace_id, metadata
            ) VALUES (
              p_event_id, inserted_job_id, NULL, 1, NULL, 'QUEUED',
              'ENQUEUED', 'OPERATOR', p_actor_id, p_trace_id, '{}'::jsonb
            );
            job_id := inserted_job_id;
            outcome := 'ENQUEUED';
            RETURN NEXT;
            RETURN;
          END IF;

          SELECT job_row.*
          INTO existing_job
          FROM public.jobs job_row
          WHERE job_row.job_type = 'SNAPSHOT'
            AND job_row.idempotency_key = p_idempotency_key
          FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'idempotency recovery failed'
              USING ERRCODE = '40001';
          END IF;
          IF existing_job.payload_fingerprint IS DISTINCT FROM
                 p_payload_fingerprint
             OR existing_job.actor_type IS DISTINCT FROM 'OPERATOR'
             OR existing_job.actor_id IS DISTINCT FROM p_actor_id
             OR existing_job.priority IS DISTINCT FROM p_priority THEN
            RAISE EXCEPTION 'idempotency identity conflict'
              USING ERRCODE = '23505',
                    CONSTRAINT = 'job_plane_idempotency_identity';
          END IF;
          job_id := existing_job.job_id;
          outcome := 'DEDUPLICATED';
          RETURN NEXT;
        END;
        $function$;

        CREATE FUNCTION job_plane.api_cancel_snapshot(
          p_job_id text,
          p_actor_id text,
          p_trace_id text,
          p_event_id text
        )
        RETURNS TABLE(job_id text, state text, changed boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          current_job public.jobs%ROWTYPE;
          target_state text;
          next_sequence bigint;
        BEGIN
          IF session_user <> 'trading_job_api' THEN
            RAISE EXCEPTION 'operator cancellation authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_job_id IS NULL
             OR p_job_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_actor_id IS NULL
             OR p_actor_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_trace_id IS NULL
             OR p_trace_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_event_id IS NULL
             OR p_event_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$' THEN
            RAISE EXCEPTION 'operator cancellation input rejected'
              USING ERRCODE = '22023';
          END IF;

          SELECT job_row.*
          INTO current_job
          FROM public.jobs job_row
          WHERE job_row.job_id = p_job_id
          FOR UPDATE;
          IF NOT FOUND THEN
            RETURN;
          END IF;
          IF current_job.job_type <> 'SNAPSHOT' THEN
            RAISE EXCEPTION 'operator cancellation target rejected'
              USING ERRCODE = '22023';
          END IF;
          IF current_job.state IN (
            'SUCCEEDED', 'FAILED', 'BLOCKED', 'TIMED_OUT',
            'CANCEL_REQUESTED', 'CANCELLED'
          ) THEN
            job_id := current_job.job_id;
            state := current_job.state;
            changed := false;
            RETURN NEXT;
            RETURN;
          END IF;
          IF current_job.state = 'QUEUED' THEN
            target_state := 'CANCELLED';
          ELSIF current_job.state IN ('CLAIMED', 'RUNNING') THEN
            target_state := 'CANCEL_REQUESTED';
          ELSE
            RAISE EXCEPTION 'operator cancellation state rejected'
              USING ERRCODE = '22023';
          END IF;

          SELECT coalesce(max(event_row.sequence), 0) + 1
          INTO next_sequence
          FROM public.job_events event_row
          WHERE event_row.job_id = current_job.job_id;

          UPDATE public.jobs job_row
          SET state = target_state,
              updated_at = statement_timestamp(),
              reason_code = 'CANCEL_REQUESTED',
              cancel_requested_at = statement_timestamp(),
              cancel_actor_type = 'OPERATOR',
              cancel_actor_id = p_actor_id,
              finished_at = CASE
                WHEN target_state = 'CANCELLED' THEN statement_timestamp()
                ELSE job_row.finished_at
              END
          WHERE job_row.job_id = current_job.job_id;

          INSERT INTO public.job_events (
            event_id, job_id, attempt_id, sequence, from_state, to_state,
            reason_code, actor_type, actor_id, trace_id, metadata
          ) VALUES (
            p_event_id, current_job.job_id, NULL, next_sequence,
            current_job.state, target_state, 'CANCEL_REQUESTED', 'OPERATOR',
            p_actor_id, p_trace_id, '{}'::jsonb
          );
          job_id := current_job.job_id;
          state := target_state;
          changed := true;
          RETURN NEXT;
        END;
        $function$;
        """
    )

    # Terminal transitions and expired-lease recovery.  A caller may select
    # only evidence fields; the state matrices and emitted actor identities
    # are fixed in these capabilities.
    op.execute(
        r"""
        CREATE FUNCTION job_plane.worker_finalize_snapshot(
          p_job_id text,
          p_attempt_id text,
          p_worker_id text,
          p_lease_token text,
          p_expected_state text,
          p_expected_attempt_outcome text,
          p_final_state text,
          p_reason_code text,
          p_trace_id text,
          p_event_id text,
          p_exit_code integer,
          p_termination_reason text,
          p_result_hash text,
          p_result_metadata jsonb,
          p_error_code text,
          p_error_message text,
          p_retry boolean,
          p_retry_event_id text,
          p_event_metadata jsonb
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          current_job public.jobs%ROWTYPE;
          current_attempt public.job_attempts%ROWTYPE;
          next_sequence bigint;
        BEGIN
          IF session_user <> 'trading_job_worker' THEN
            RAISE EXCEPTION 'worker finalization authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_job_id IS NULL
             OR p_job_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_attempt_id IS NULL
             OR p_attempt_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_worker_id IS NULL
             OR p_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_lease_token IS NULL
             OR p_lease_token !~ '^[A-Za-z0-9_-]{16,128}$'
             OR p_reason_code IS NULL
             OR p_reason_code !~ '^[A-Z][A-Z0-9_]{0,127}$'
             OR p_trace_id IS NULL
             OR p_trace_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_event_id IS NULL
             OR p_event_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_result_hash IS NOT NULL
                AND p_result_hash !~ '^[0-9a-f]{64}$'
             OR p_result_metadata IS NULL
             OR jsonb_typeof(p_result_metadata) <> 'object'
             OR p_event_metadata IS NULL
             OR jsonb_typeof(p_event_metadata) <> 'object'
             OR p_expected_state IS NULL
             OR p_expected_attempt_outcome IS NULL
             OR p_final_state IS NULL
             OR p_termination_reason IS NOT NULL
                AND char_length(p_termination_reason) > 128
             OR p_error_code IS NOT NULL
                AND p_error_code !~ '^[A-Z][A-Z0-9_]{0,127}$'
             OR p_error_message IS NOT NULL
                AND char_length(p_error_message) > 512
             OR p_retry IS NULL
             OR p_retry_event_id IS NOT NULL
                AND p_retry_event_id !~
                  '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_retry
                AND (p_retry_event_id IS NULL OR p_retry_event_id = p_event_id)
             OR NOT (
               (
                 p_expected_state = 'CLAIMED'
                 AND p_expected_attempt_outcome = 'CLAIMED'
                 AND p_final_state = 'BLOCKED'
               )
               OR (
                 p_expected_state = 'RUNNING'
                 AND p_expected_attempt_outcome = 'RUNNING'
                 AND p_final_state IN (
                   'SUCCEEDED', 'FAILED', 'BLOCKED', 'TIMED_OUT'
                 )
               )
               OR (
                 p_expected_state = 'CANCEL_REQUESTED'
                 AND p_expected_attempt_outcome IN ('CLAIMED', 'RUNNING')
                 AND p_final_state IN ('CANCELLED', 'BLOCKED')
               )
             )
             OR p_retry AND p_final_state NOT IN ('FAILED', 'TIMED_OUT') THEN
            RAISE EXCEPTION 'worker finalization input rejected'
              USING ERRCODE = '22023';
          END IF;

          SELECT job_row.*
          INTO current_job
          FROM public.jobs job_row
          WHERE job_row.job_id = p_job_id
            AND job_row.job_type = 'SNAPSHOT'
          FOR UPDATE;
          IF NOT FOUND
             OR current_job.state IS DISTINCT FROM p_expected_state
             OR current_job.lease_owner IS DISTINCT FROM p_worker_id
             OR current_job.lease_token IS DISTINCT FROM p_lease_token
             OR current_job.lease_expires_at <= statement_timestamp() THEN
            RETURN false;
          END IF;

          SELECT attempt_row.*
          INTO current_attempt
          FROM public.job_attempts attempt_row
          WHERE attempt_row.attempt_id = p_attempt_id
            AND attempt_row.job_id = p_job_id
          FOR UPDATE;
          IF NOT FOUND
             OR current_attempt.attempt_number <> current_job.attempt_count
             OR current_attempt.outcome IS DISTINCT FROM
                  p_expected_attempt_outcome
             OR current_attempt.worker_id IS DISTINCT FROM p_worker_id
             OR current_attempt.lease_token IS DISTINCT FROM p_lease_token THEN
            RETURN false;
          END IF;
          IF p_retry AND current_job.attempt_count >= current_job.max_attempts THEN
            RAISE EXCEPTION 'worker retry policy rejected'
              USING ERRCODE = '22023';
          END IF;

          SELECT coalesce(max(event_row.sequence), 0) + 1
          INTO next_sequence
          FROM public.job_events event_row
          WHERE event_row.job_id = p_job_id;

          UPDATE public.jobs job_row
          SET state = p_final_state,
              reason_code = p_reason_code,
              result_hash = p_result_hash,
              result_metadata = p_result_metadata,
              error_code = p_error_code,
              error_message = p_error_message,
              finished_at = statement_timestamp(),
              updated_at = statement_timestamp(),
              lease_owner = NULL,
              lease_token = NULL,
              lease_expires_at = NULL
          WHERE job_row.job_id = p_job_id;
          UPDATE public.job_attempts attempt_row
          SET outcome = p_final_state,
              finished_at = statement_timestamp(),
              exit_code = p_exit_code,
              termination_reason = p_termination_reason,
              error_code = p_error_code,
              error_message = p_error_message
          WHERE attempt_row.attempt_id = p_attempt_id
            AND attempt_row.job_id = p_job_id;
          INSERT INTO public.job_events (
            event_id, job_id, attempt_id, sequence, from_state, to_state,
            reason_code, actor_type, actor_id, trace_id, metadata
          ) VALUES (
            p_event_id, p_job_id, p_attempt_id, next_sequence,
            p_expected_state, p_final_state, p_reason_code, 'WORKER',
            p_worker_id, p_trace_id, p_event_metadata
          );

          IF p_retry THEN
            UPDATE public.jobs job_row
            SET state = 'QUEUED',
                reason_code = 'PROCESS_RETRY_SCHEDULED',
                next_attempt_at = statement_timestamp() + interval '30 seconds',
                finished_at = NULL,
                result_hash = NULL,
                result_metadata = '{}'::jsonb,
                error_code = NULL,
                error_message = NULL,
                cancel_requested_at = NULL,
                cancel_actor_type = NULL,
                cancel_actor_id = NULL,
                updated_at = statement_timestamp()
            WHERE job_row.job_id = p_job_id;
            INSERT INTO public.job_events (
              event_id, job_id, attempt_id, sequence, from_state, to_state,
              reason_code, actor_type, actor_id, trace_id, metadata
            ) VALUES (
              p_retry_event_id, p_job_id, p_attempt_id, next_sequence + 1,
              p_final_state, 'QUEUED', 'PROCESS_RETRY_SCHEDULED', 'WORKER',
              p_worker_id, p_trace_id, '{}'::jsonb
            );
          END IF;
          RETURN true;
        END;
        $function$;

        CREATE FUNCTION job_plane.worker_recover_expired_snapshot(
          p_job_id text,
          p_attempt_id text,
          p_expected_state text,
          p_expected_attempt_outcome text,
          p_expected_lease_owner text,
          p_expected_lease_token text,
          p_expected_child_pid bigint,
          p_expected_process_group_id bigint,
          p_expected_process_start_ticks bigint,
          p_expected_command_fingerprint text,
          p_observation text,
          p_trace_id text,
          p_recovery_id text,
          p_event_id text,
          p_retry_event_id text
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          current_job public.jobs%ROWTYPE;
          current_attempt public.job_attempts%ROWTYPE;
          effective_observation text;
          reason text;
          target_state text;
          target_attempt_outcome text;
          retry_job boolean := false;
          next_sequence bigint;
        BEGIN
          IF session_user <> 'trading_job_worker' THEN
            RAISE EXCEPTION 'worker recovery authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_job_id IS NULL
             OR p_job_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_attempt_id IS NULL
             OR p_attempt_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_expected_lease_owner IS NULL
             OR p_expected_lease_owner !~
                  '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_expected_lease_token IS NULL
             OR p_expected_lease_token !~ '^[A-Za-z0-9_-]{16,128}$'
             OR p_expected_child_pid IS NOT NULL
                AND p_expected_child_pid <= 0
             OR p_expected_process_group_id IS NOT NULL
                AND p_expected_process_group_id <= 0
             OR p_expected_process_start_ticks IS NOT NULL
                AND p_expected_process_start_ticks < 0
             OR p_expected_command_fingerprint IS NOT NULL
                AND p_expected_command_fingerprint !~ '^[0-9a-f]{64}$'
             OR p_expected_state IS NULL
             OR p_expected_attempt_outcome IS NULL
             OR p_observation IS NULL
             OR p_observation NOT IN (
               'ABSENT', 'STILL_RUNNING', 'IDENTITY_MISMATCH', 'UNVERIFIABLE'
             )
             OR p_trace_id IS NULL
             OR p_trace_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_recovery_id IS NULL
             OR p_recovery_id NOT IN (
               'lease-recovery', 'worker-startup-recovery'
             )
             OR p_event_id IS NULL
             OR p_event_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_retry_event_id IS NOT NULL
                AND p_retry_event_id !~
                  '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_retry_event_id = p_event_id
             OR NOT (
               (p_expected_state = 'CLAIMED'
                AND p_expected_attempt_outcome = 'CLAIMED')
               OR (p_expected_state = 'RUNNING'
                   AND p_expected_attempt_outcome = 'RUNNING')
               OR (p_expected_state = 'CANCEL_REQUESTED'
                   AND p_expected_attempt_outcome IN ('CLAIMED', 'RUNNING'))
             ) THEN
            RAISE EXCEPTION 'worker recovery input rejected'
              USING ERRCODE = '22023';
          END IF;

          SELECT job_row.*
          INTO current_job
          FROM public.jobs job_row
          WHERE job_row.job_id = p_job_id
            AND job_row.job_type = 'SNAPSHOT'
          FOR UPDATE;
          IF NOT FOUND
             OR current_job.state IS DISTINCT FROM p_expected_state
             OR current_job.lease_owner IS DISTINCT FROM
                  p_expected_lease_owner
             OR current_job.lease_token IS DISTINCT FROM
                  p_expected_lease_token
             OR current_job.lease_expires_at > statement_timestamp() THEN
            RETURN 'LEASE_RECOVERY_STALE';
          END IF;

          SELECT attempt_row.*
          INTO current_attempt
          FROM public.job_attempts attempt_row
          WHERE attempt_row.attempt_id = p_attempt_id
            AND attempt_row.job_id = p_job_id
          FOR UPDATE;
          IF NOT FOUND
             OR current_attempt.attempt_number <> current_job.attempt_count
             OR current_attempt.outcome IS DISTINCT FROM
                  p_expected_attempt_outcome
             OR current_attempt.worker_id IS DISTINCT FROM
                  p_expected_lease_owner
             OR current_attempt.lease_token IS DISTINCT FROM
                  p_expected_lease_token THEN
            RETURN 'LEASE_RECOVERY_STALE';
          END IF;

          effective_observation := p_observation;
          IF current_attempt.child_pid IS NULL
             OR current_attempt.process_group_id IS NULL
             OR current_attempt.process_start_ticks IS NULL
             OR current_attempt.command_fingerprint IS NULL
             OR current_attempt.child_pid IS DISTINCT FROM p_expected_child_pid
             OR current_attempt.process_group_id IS DISTINCT FROM
                  p_expected_process_group_id
             OR current_attempt.process_start_ticks IS DISTINCT FROM
                  p_expected_process_start_ticks
             OR current_attempt.command_fingerprint IS DISTINCT FROM
                  p_expected_command_fingerprint THEN
            effective_observation := 'UNVERIFIABLE';
          END IF;

          IF current_job.result_hash IS NOT NULL
             OR current_job.result_metadata <> '{}'::jsonb THEN
            reason := 'RESULT_RECONCILIATION_REQUIRED';
            target_state := 'BLOCKED';
            target_attempt_outcome := 'BLOCKED';
          ELSIF effective_observation = 'STILL_RUNNING' THEN
            reason := 'LEASE_EXPIRED_CHILD_STILL_RUNNING';
            target_state := 'BLOCKED';
            target_attempt_outcome := 'BLOCKED';
          ELSIF effective_observation = 'IDENTITY_MISMATCH' THEN
            reason := 'LEASE_EXPIRED_CHILD_IDENTITY_MISMATCH';
            target_state := 'BLOCKED';
            target_attempt_outcome := 'BLOCKED';
          ELSIF effective_observation = 'UNVERIFIABLE' THEN
            reason := 'LEASE_EXPIRED_CHILD_IDENTITY_UNVERIFIABLE';
            target_state := 'BLOCKED';
            target_attempt_outcome := 'BLOCKED';
          ELSIF current_job.state <> 'RUNNING' THEN
            reason := 'LEASE_EXPIRED_BEFORE_RESULT_STATE_UNCERTAIN';
            target_state := 'BLOCKED';
            target_attempt_outcome := 'BLOCKED';
          ELSE
            target_state := 'FAILED';
            target_attempt_outcome := 'INTERRUPTED';
            retry_job := current_job.attempt_count < current_job.max_attempts;
            reason := CASE WHEN retry_job
              THEN 'LEASE_EXPIRED_CHILD_ABSENT'
              ELSE 'LEASE_EXPIRED_ATTEMPTS_EXHAUSTED'
            END;
          END IF;
          IF retry_job AND p_retry_event_id IS NULL THEN
            RAISE EXCEPTION 'worker recovery retry identity rejected'
              USING ERRCODE = '22023';
          END IF;

          SELECT coalesce(max(event_row.sequence), 0) + 1
          INTO next_sequence
          FROM public.job_events event_row
          WHERE event_row.job_id = p_job_id;

          UPDATE public.job_attempts attempt_row
          SET outcome = target_attempt_outcome,
              finished_at = statement_timestamp(),
              termination_reason = reason
          WHERE attempt_row.attempt_id = p_attempt_id
            AND attempt_row.job_id = p_job_id;
          UPDATE public.jobs job_row
          SET state = target_state,
              reason_code = reason,
              finished_at = statement_timestamp(),
              updated_at = statement_timestamp(),
              lease_owner = NULL,
              lease_token = NULL,
              lease_expires_at = NULL
          WHERE job_row.job_id = p_job_id;
          INSERT INTO public.job_events (
            event_id, job_id, attempt_id, sequence, from_state, to_state,
            reason_code, actor_type, actor_id, trace_id, metadata
          ) VALUES (
            p_event_id, p_job_id, p_attempt_id, next_sequence,
            p_expected_state, target_state, reason, 'RECOVERY', p_recovery_id,
            p_trace_id, '{}'::jsonb
          );

          IF retry_job THEN
            UPDATE public.jobs job_row
            SET state = 'QUEUED',
                reason_code = 'LEASE_EXPIRED_RETRY_SCHEDULED',
                next_attempt_at = statement_timestamp() + interval '30 seconds',
                finished_at = NULL,
                result_hash = NULL,
                result_metadata = '{}'::jsonb,
                error_code = NULL,
                error_message = NULL,
                cancel_requested_at = NULL,
                cancel_actor_type = NULL,
                cancel_actor_id = NULL,
                updated_at = statement_timestamp()
            WHERE job_row.job_id = p_job_id;
            INSERT INTO public.job_events (
              event_id, job_id, attempt_id, sequence, from_state, to_state,
              reason_code, actor_type, actor_id, trace_id, metadata
            ) VALUES (
              p_retry_event_id, p_job_id, p_attempt_id, next_sequence + 1,
              'FAILED', 'QUEUED', 'LEASE_EXPIRED_RETRY_SCHEDULED',
              'RECOVERY', p_recovery_id, p_trace_id, '{}'::jsonb
            );
            RETURN 'LEASE_EXPIRED_RETRY_SCHEDULED';
          END IF;
          RETURN reason;
        END;
        $function$;
        """
    )

    # Scheduler enqueue is a separate capability so the reserved namespace,
    # actor type, priority, and max-attempt policy cannot be caller-selected.
    op.execute(
        r"""
        CREATE FUNCTION job_plane.scheduler_enqueue_snapshot(
          p_job_id text,
          p_payload jsonb,
          p_payload_fingerprint text,
          p_idempotency_key text,
          p_actor_id text,
          p_trace_id text,
          p_event_id text
        )
        RETURNS TABLE(job_id text, outcome text)
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          existing_job public.jobs%ROWTYPE;
          inserted_job_id text;
        BEGIN
          IF session_user <> 'trading_job_scheduler' THEN
            RAISE EXCEPTION 'scheduler enqueue authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_job_id IS NULL
             OR p_job_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_event_id IS NULL
             OR p_event_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_job_id = p_event_id
             OR p_payload IS DISTINCT FROM
                  pg_catalog.jsonb_build_object(
                    'scope', 'default', 'requested_as_of', NULL
                  )
             OR p_payload_fingerprint IS NULL
             OR p_payload_fingerprint IS DISTINCT FROM
                  'dc993577d7fe81a0fc6b23e281e0b7e2a182d557143cfa312d21078271b4091a'
             OR p_idempotency_key IS NULL
             OR p_idempotency_key !~
                  '^schedule:snapshot:[0-9]{4}-(0[1-9]|1[0-2])-'
                  '(0[1-9]|[12][0-9]|3[01])T'
                  '([01][0-9]|2[0-3]):[0-5][0-9]Z$'
             OR NOT pg_catalog.pg_input_is_valid(
                  substring(
                    p_idempotency_key FROM
                    '^schedule:snapshot:([0-9]{4}-[0-9]{2}-[0-9]{2})T'
                  ),
                  'date'
                )
             OR p_actor_id IS NULL
             OR p_actor_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_trace_id IS NULL
             OR p_trace_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
            RAISE EXCEPTION 'scheduler enqueue input rejected'
              USING ERRCODE = '22023';
          END IF;

          INSERT INTO public.jobs (
            job_id, job_type, state, payload, payload_fingerprint,
            idempotency_key, actor_type, actor_id, priority, max_attempts
          ) VALUES (
            p_job_id, 'SNAPSHOT', 'QUEUED', p_payload,
            p_payload_fingerprint, p_idempotency_key, 'SCHEDULER', p_actor_id,
            0, 2
          )
          ON CONFLICT (job_type, idempotency_key) DO NOTHING
          RETURNING public.jobs.job_id INTO inserted_job_id;

          IF inserted_job_id IS NOT NULL THEN
            INSERT INTO public.job_events (
              event_id, job_id, attempt_id, sequence, from_state, to_state,
              reason_code, actor_type, actor_id, trace_id, metadata
            ) VALUES (
              p_event_id, inserted_job_id, NULL, 1, NULL, 'QUEUED',
              'ENQUEUED', 'SCHEDULER', p_actor_id, p_trace_id, '{}'::jsonb
            );
            job_id := inserted_job_id;
            outcome := 'ENQUEUED';
            RETURN NEXT;
            RETURN;
          END IF;

          SELECT job_row.*
          INTO existing_job
          FROM public.jobs job_row
          WHERE job_row.job_type = 'SNAPSHOT'
            AND job_row.idempotency_key = p_idempotency_key
          FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'idempotency recovery failed'
              USING ERRCODE = '40001';
          END IF;
          IF existing_job.payload_fingerprint IS DISTINCT FROM
                 p_payload_fingerprint
             OR existing_job.actor_type IS DISTINCT FROM 'SCHEDULER'
             OR existing_job.actor_id IS DISTINCT FROM p_actor_id
             OR existing_job.priority IS DISTINCT FROM 0 THEN
            RAISE EXCEPTION 'idempotency identity conflict'
              USING ERRCODE = '23505',
                    CONSTRAINT = 'job_plane_idempotency_identity';
          END IF;
          job_id := existing_job.job_id;
          outcome := 'DEDUPLICATED';
          RETURN NEXT;
        END;
        $function$;
        """
    )

    # Claim, start, and lease-control capabilities.  All lifecycle functions
    # acquire the job row before the attempt row, establishing one lock order.
    op.execute(
        r"""
        CREATE FUNCTION job_plane.worker_claim_snapshot(
          p_attempt_id text,
          p_worker_id text,
          p_lease_token text,
          p_lease_seconds integer,
          p_trace_id text,
          p_event_id text
        )
        RETURNS TABLE(
          job_id text,
          job_type text,
          payload jsonb,
          attempt_number integer,
          max_attempts smallint,
          lease_expires_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          selected_job public.jobs%ROWTYPE;
          claimed_at timestamptz;
          next_sequence bigint;
          token_sha256 text;
        BEGIN
          IF session_user <> 'trading_job_worker' THEN
            RAISE EXCEPTION 'worker claim authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_attempt_id IS NULL
             OR p_attempt_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_event_id IS NULL
             OR p_event_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_attempt_id = p_event_id
             OR p_worker_id IS NULL
             OR p_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_lease_token IS NULL
             OR p_lease_token !~ '^[A-Za-z0-9_-]{16,128}$'
             OR p_lease_seconds IS NULL
             OR p_lease_seconds < 1 OR p_lease_seconds > 3600
             OR p_trace_id IS NULL
             OR p_trace_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
            RAISE EXCEPTION 'worker claim input rejected'
              USING ERRCODE = '22023';
          END IF;

          SELECT job_row.*
          INTO selected_job
          FROM public.jobs job_row
          WHERE job_row.state = 'QUEUED'
            AND job_row.job_type = 'SNAPSHOT'
            AND (
              job_row.next_attempt_at IS NULL
              OR job_row.next_attempt_at <= statement_timestamp()
            )
            AND job_row.attempt_count < job_row.max_attempts
          ORDER BY job_row.priority DESC,
                   job_row.requested_at ASC,
                   job_row.job_id ASC
          FOR UPDATE SKIP LOCKED
          LIMIT 1;
          IF NOT FOUND THEN
            RETURN;
          END IF;

          claimed_at := statement_timestamp();
          next_sequence := (
            SELECT coalesce(max(event_row.sequence), 0) + 1
            FROM public.job_events event_row
            WHERE event_row.job_id = selected_job.job_id
          );
          token_sha256 := encode(
            pg_catalog.sha256(
              pg_catalog.convert_to(p_lease_token, 'UTF8')
            ),
            'hex'
          );

          UPDATE public.jobs job_row
          SET state = 'CLAIMED',
              attempt_count = job_row.attempt_count + 1,
              lease_owner = p_worker_id,
              lease_token = p_lease_token,
              lease_expires_at = claimed_at
                + (p_lease_seconds * interval '1 second'),
              next_attempt_at = NULL,
              reason_code = 'CLAIMED',
              updated_at = claimed_at
          WHERE job_row.job_id = selected_job.job_id
          RETURNING job_row.job_id, job_row.job_type, job_row.payload,
                    job_row.attempt_count, job_row.max_attempts,
                    job_row.lease_expires_at
          INTO job_id, job_type, payload, attempt_number, max_attempts,
               lease_expires_at;

          INSERT INTO public.job_attempts (
            attempt_id, job_id, attempt_number, worker_id, outcome,
            lease_token, lease_expires_at, claimed_at
          ) VALUES (
            p_attempt_id, job_id, attempt_number, p_worker_id, 'CLAIMED',
            p_lease_token, lease_expires_at, claimed_at
          );

          INSERT INTO public.job_events (
            event_id, job_id, attempt_id, sequence, from_state, to_state,
            reason_code, actor_type, actor_id, trace_id, metadata
          ) VALUES (
            p_event_id, job_id, p_attempt_id, next_sequence,
            'QUEUED', 'CLAIMED', 'CLAIMED', 'WORKER', p_worker_id,
            p_trace_id, jsonb_build_object('lease_token_sha256', token_sha256)
          );
          RETURN NEXT;
        END;
        $function$;

        CREATE FUNCTION job_plane.worker_start_snapshot(
          p_job_id text,
          p_attempt_id text,
          p_worker_id text,
          p_lease_token text,
          p_child_pid bigint,
          p_process_group_id bigint,
          p_process_start_ticks bigint,
          p_command_fingerprint text,
          p_trace_id text,
          p_event_id text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          current_job public.jobs%ROWTYPE;
          current_attempt public.job_attempts%ROWTYPE;
          next_sequence bigint;
        BEGIN
          IF session_user <> 'trading_job_worker' THEN
            RAISE EXCEPTION 'worker start authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_job_id IS NULL
             OR p_job_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_attempt_id IS NULL
             OR p_attempt_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_worker_id IS NULL
             OR p_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_lease_token IS NULL
             OR p_lease_token !~ '^[A-Za-z0-9_-]{16,128}$'
             OR p_child_pid IS NULL OR p_child_pid <= 0
             OR p_process_group_id IS NULL OR p_process_group_id <= 0
             OR p_process_start_ticks IS NULL OR p_process_start_ticks < 0
             OR p_command_fingerprint IS NULL
             OR p_command_fingerprint !~ '^[0-9a-f]{64}$'
             OR p_trace_id IS NULL
             OR p_trace_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_event_id IS NULL
             OR p_event_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$' THEN
            RAISE EXCEPTION 'worker start input rejected'
              USING ERRCODE = '22023';
          END IF;

          SELECT job_row.*
          INTO current_job
          FROM public.jobs job_row
          WHERE job_row.job_id = p_job_id
            AND job_row.job_type = 'SNAPSHOT'
          FOR UPDATE;
          IF NOT FOUND
             OR current_job.state <> 'CLAIMED'
             OR current_job.lease_owner IS DISTINCT FROM p_worker_id
             OR current_job.lease_token IS DISTINCT FROM p_lease_token
             OR current_job.lease_expires_at <= statement_timestamp() THEN
            RETURN false;
          END IF;

          SELECT attempt_row.*
          INTO current_attempt
          FROM public.job_attempts attempt_row
          WHERE attempt_row.attempt_id = p_attempt_id
            AND attempt_row.job_id = p_job_id
          FOR UPDATE;
          IF NOT FOUND
             OR current_attempt.outcome <> 'CLAIMED'
             OR current_attempt.worker_id IS DISTINCT FROM p_worker_id
             OR current_attempt.lease_token IS DISTINCT FROM p_lease_token
             OR current_attempt.attempt_number <> current_job.attempt_count THEN
            RETURN false;
          END IF;

          SELECT coalesce(max(event_row.sequence), 0) + 1
          INTO next_sequence
          FROM public.job_events event_row
          WHERE event_row.job_id = p_job_id;

          UPDATE public.jobs job_row
          SET state = 'RUNNING',
              reason_code = 'STARTED',
              updated_at = statement_timestamp()
          WHERE job_row.job_id = p_job_id;
          UPDATE public.job_attempts attempt_row
          SET outcome = 'RUNNING',
              started_at = statement_timestamp(),
              heartbeat_at = statement_timestamp(),
              child_pid = p_child_pid,
              process_group_id = p_process_group_id,
              process_start_ticks = p_process_start_ticks,
              command_fingerprint = p_command_fingerprint
          WHERE attempt_row.attempt_id = p_attempt_id
            AND attempt_row.job_id = p_job_id;
          INSERT INTO public.job_events (
            event_id, job_id, attempt_id, sequence, from_state, to_state,
            reason_code, actor_type, actor_id, trace_id, metadata
          ) VALUES (
            p_event_id, p_job_id, p_attempt_id, next_sequence,
            'CLAIMED', 'RUNNING', 'STARTED', 'WORKER', p_worker_id,
            p_trace_id, '{}'::jsonb
          );
          RETURN true;
        END;
        $function$;

        CREATE FUNCTION job_plane.worker_control_snapshot_lease(
          p_job_id text,
          p_attempt_id text,
          p_worker_id text,
          p_lease_token text,
          p_lease_seconds integer,
          p_phase text
        )
        RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          current_job public.jobs%ROWTYPE;
          current_attempt public.job_attempts%ROWTYPE;
          expected_attempt_outcome text;
          renewed_until timestamptz;
        BEGIN
          IF session_user <> 'trading_job_worker' THEN
            RAISE EXCEPTION 'worker lease authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_job_id IS NULL
             OR p_job_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_attempt_id IS NULL
             OR p_attempt_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_worker_id IS NULL
             OR p_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_lease_token IS NULL
             OR p_lease_token !~ '^[A-Za-z0-9_-]{16,128}$'
             OR p_lease_seconds IS NULL
             OR p_lease_seconds < 1 OR p_lease_seconds > 3600
             OR p_phase IS NULL
             OR p_phase NOT IN ('PRE_SPAWN', 'RUNNING') THEN
            RAISE EXCEPTION 'worker lease input rejected'
              USING ERRCODE = '22023';
          END IF;
          expected_attempt_outcome := CASE p_phase
            WHEN 'PRE_SPAWN' THEN 'CLAIMED'
            ELSE 'RUNNING'
          END;

          SELECT job_row.*
          INTO current_job
          FROM public.jobs job_row
          WHERE job_row.job_id = p_job_id
            AND job_row.job_type = 'SNAPSHOT'
          FOR UPDATE;
          IF NOT FOUND
             OR current_job.state NOT IN (
               CASE p_phase WHEN 'PRE_SPAWN' THEN 'CLAIMED' ELSE 'RUNNING' END,
               'CANCEL_REQUESTED'
             )
             OR current_job.lease_owner IS DISTINCT FROM p_worker_id
             OR current_job.lease_token IS DISTINCT FROM p_lease_token
             OR current_job.lease_expires_at <= statement_timestamp() THEN
            RETURN 'STALE';
          END IF;

          SELECT attempt_row.*
          INTO current_attempt
          FROM public.job_attempts attempt_row
          WHERE attempt_row.attempt_id = p_attempt_id
            AND attempt_row.job_id = p_job_id
          FOR UPDATE;
          IF NOT FOUND
             OR current_attempt.outcome <> expected_attempt_outcome
             OR current_attempt.worker_id IS DISTINCT FROM p_worker_id
             OR current_attempt.lease_token IS DISTINCT FROM p_lease_token
             OR current_attempt.attempt_number <> current_job.attempt_count THEN
            RETURN 'STALE';
          END IF;

          renewed_until := statement_timestamp()
            + (p_lease_seconds * interval '1 second');
          UPDATE public.jobs job_row
          SET lease_expires_at = renewed_until,
              updated_at = statement_timestamp()
          WHERE job_row.job_id = p_job_id;
          UPDATE public.job_attempts attempt_row
          SET lease_expires_at = renewed_until,
              heartbeat_at = statement_timestamp()
          WHERE attempt_row.attempt_id = p_attempt_id
            AND attempt_row.job_id = p_job_id;
          IF current_job.state = 'CANCEL_REQUESTED' THEN
            RETURN 'CANCEL';
          END IF;
          RETURN 'CONTINUE';
        END;
        $function$;
        """
    )

    # Remove all column-level mutation authority installed by 0005.  SELECT,
    # scheduler heartbeat, artifact, and worker-heartbeat permissions remain.
    op.execute(
        r"""
        REVOKE INSERT (
          job_id, job_type, state, payload, payload_fingerprint,
          idempotency_key, actor_type, actor_id, priority, max_attempts
        ) ON TABLE public.jobs FROM trading_job_api;
        REVOKE UPDATE (
          state, updated_at, reason_code, cancel_requested_at,
          cancel_actor_type, cancel_actor_id
        ) ON TABLE public.jobs FROM trading_job_api;
        REVOKE INSERT (
          event_id, job_id, attempt_id, sequence, from_state, to_state,
          reason_code, actor_type, actor_id, trace_id, metadata
        ) ON TABLE public.job_events FROM trading_job_api;

        REVOKE UPDATE (
          state, updated_at, attempt_count, next_attempt_at, lease_owner,
          lease_token, lease_expires_at, reason_code, result_hash,
          result_metadata, error_code, error_message, finished_at
        ) ON TABLE public.jobs FROM trading_job_worker;
        REVOKE INSERT (
          attempt_id, job_id, attempt_number, worker_id, outcome, lease_token,
          lease_expires_at, claimed_at
        ) ON TABLE public.job_attempts FROM trading_job_worker;
        REVOKE UPDATE (
          outcome, lease_expires_at, started_at, heartbeat_at, finished_at,
          child_pid, process_group_id, process_start_ticks,
          command_fingerprint, exit_code, termination_reason,
          error_code, error_message
        ) ON TABLE public.job_attempts FROM trading_job_worker;
        REVOKE INSERT (
          event_id, job_id, attempt_id, sequence, from_state, to_state,
          reason_code, actor_type, actor_id, trace_id, metadata
        ) ON TABLE public.job_events FROM trading_job_worker;

        REVOKE INSERT (
          job_id, job_type, state, payload, payload_fingerprint,
          idempotency_key, actor_type, actor_id, priority, max_attempts
        ) ON TABLE public.jobs FROM trading_job_scheduler;
        REVOKE INSERT (
          event_id, job_id, attempt_id, sequence, from_state, to_state,
          reason_code, actor_type, actor_id, trace_id, metadata
        ) ON TABLE public.job_events FROM trading_job_scheduler;

        DROP POLICY job_plane_api_jobs_insert ON public.jobs;
        DROP POLICY job_plane_api_jobs_cancel ON public.jobs;
        DROP POLICY job_plane_worker_jobs_update ON public.jobs;
        DROP POLICY job_plane_scheduler_jobs_insert ON public.jobs;
        DROP POLICY job_plane_worker_attempts_insert ON public.job_attempts;
        DROP POLICY job_plane_worker_attempts_update ON public.job_attempts;
        DROP POLICY job_plane_api_events_insert ON public.job_events;
        DROP POLICY job_plane_worker_events_insert ON public.job_events;
        DROP POLICY job_plane_scheduler_events_insert ON public.job_events;

        REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA job_plane
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        GRANT EXECUTE ON FUNCTION job_plane.api_enqueue_snapshot(
          text, jsonb, text, text, text, smallint, text, text
        ) TO trading_job_api;
        GRANT EXECUTE ON FUNCTION job_plane.api_cancel_snapshot(
          text, text, text, text
        ) TO trading_job_api;
        GRANT EXECUTE ON FUNCTION job_plane.scheduler_enqueue_snapshot(
          text, jsonb, text, text, text, text, text
        ) TO trading_job_scheduler;
        GRANT EXECUTE ON FUNCTION job_plane.worker_claim_snapshot(
          text, text, text, integer, text, text
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_start_snapshot(
          text, text, text, text, bigint, bigint, bigint, text, text, text
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_control_snapshot_lease(
          text, text, text, text, integer, text
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_finalize_snapshot(
          text, text, text, text, text, text, text, text, text, text,
          integer, text, text, jsonb, text, text, boolean, text, jsonb
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_recover_expired_snapshot(
          text, text, text, text, text, text, bigint, bigint, bigint,
          text, text, text, text, text, text
        ) TO trading_job_worker;
        """
    )

    # Catalog-only postflight.  Alembic stamps 0006 only after this returns,
    # so the active row must still be the exact 0005 parent here.
    op.execute(
        rf"""
        DO $job_transition_postflight$
        BEGIN
          IF (
            SELECT version_num FROM public.alembic_version
          ) <> '0005_job_plane_role_split' THEN
            RAISE EXCEPTION '0006 parent head changed during migration';
          END IF;
          IF (
            SELECT character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'alembic_version'
              AND column_name = 'version_num'
          ) <> 64 THEN
            RAISE EXCEPTION 'Alembic revision identity width is not exact';
          END IF;

          IF (
            SELECT pg_get_userbyid(namespace_row.nspowner)
            FROM pg_catalog.pg_namespace namespace_row
            WHERE namespace_row.nspname = 'job_plane'
          ) <> 'trading_owner'
             OR EXISTS (
               SELECT 1
               FROM pg_catalog.pg_namespace namespace_row
               CROSS JOIN LATERAL pg_catalog.aclexplode(
                 coalesce(
                   namespace_row.nspacl,
                   pg_catalog.acldefault('n', namespace_row.nspowner)
                 )
               ) acl
               WHERE namespace_row.nspname = 'job_plane'
                 AND acl.grantee = 0
                 AND acl.privilege_type IN ('USAGE', 'CREATE')
             )
             OR EXISTS (
               SELECT 1
               FROM unnest(ARRAY[
                 'trading_job_api', 'trading_job_worker',
                 'trading_job_scheduler'
               ]) role_name(name)
               WHERE NOT has_schema_privilege(
                       role_name.name, 'job_plane', 'USAGE'
                     )
                  OR has_schema_privilege(
                       role_name.name, 'job_plane', 'CREATE'
                     )
             ) THEN
            RAISE EXCEPTION 'job_plane schema authority is not exact';
          END IF;

          IF EXISTS (
            WITH expected(
              procedure_name, identity_types, result_type, allowed_role
            ) AS (
              VALUES
                ('api_enqueue_snapshot',
                 'text, jsonb, text, text, text, smallint, text, text',
                 'TABLE(job_id text, outcome text)',
                 'trading_job_api'),
                ('api_cancel_snapshot',
                 'text, text, text, text',
                 'TABLE(job_id text, state text, changed boolean)',
                 'trading_job_api'),
                ('scheduler_enqueue_snapshot',
                 'text, jsonb, text, text, text, text, text',
                 'TABLE(job_id text, outcome text)',
                 'trading_job_scheduler'),
                ('worker_claim_snapshot',
                 'text, text, text, integer, text, text',
                 'TABLE(job_id text, job_type text, payload jsonb, attempt_number integer, max_attempts smallint, lease_expires_at timestamp with time zone)',
                 'trading_job_worker'),
                ('worker_start_snapshot',
                 'text, text, text, text, bigint, bigint, bigint, text, text, text',
                 'boolean',
                 'trading_job_worker'),
                ('worker_control_snapshot_lease',
                 'text, text, text, text, integer, text',
                 'text',
                 'trading_job_worker'),
                ('worker_finalize_snapshot',
                 'text, text, text, text, text, text, text, text, text, text, integer, text, text, jsonb, text, text, boolean, text, jsonb',
                 'boolean',
                 'trading_job_worker'),
                ('worker_recover_expired_snapshot',
                 'text, text, text, text, text, text, bigint, bigint, bigint, text, text, text, text, text, text',
                 'text',
                 'trading_job_worker')
            ),
            actual(
              procedure_name, identity_types, result_type, allowed_role
            ) AS (
              SELECT procedure_row.proname,
                     oidvectortypes(procedure_row.proargtypes),
                     pg_get_function_result(procedure_row.oid),
                     expected.allowed_role
              FROM pg_catalog.pg_proc procedure_row
              JOIN pg_catalog.pg_language language_row
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
                AND procedure_row.provolatile = 'v'
                AND procedure_row.proparallel = 'u'
                AND procedure_row.proconfig = ARRAY['search_path=pg_catalog']
                AND upper(procedure_row.prosrc) NOT LIKE '%EXECUTE%'
                AND upper(procedure_row.prosrc) LIKE '%SESSION_USER%'
            ),
            differences(
              procedure_name, identity_types, result_type, allowed_role
            ) AS (
              (SELECT * FROM expected EXCEPT SELECT * FROM actual)
              UNION ALL
              (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            )
            SELECT 1 FROM differences
          ) OR (
            SELECT count(*)
            FROM pg_catalog.pg_proc procedure_row
            WHERE procedure_row.pronamespace = 'job_plane'::regnamespace
          ) <> 8 THEN
            RAISE EXCEPTION 'job_plane function catalog is not exact';
          END IF;

          IF EXISTS (
            WITH expected(procedure_name, allowed_role) AS (
              VALUES
                ('api_enqueue_snapshot', 'trading_job_api'),
                ('api_cancel_snapshot', 'trading_job_api'),
                ('scheduler_enqueue_snapshot', 'trading_job_scheduler'),
                ('worker_claim_snapshot', 'trading_job_worker'),
                ('worker_start_snapshot', 'trading_job_worker'),
                ('worker_control_snapshot_lease', 'trading_job_worker'),
                ('worker_finalize_snapshot', 'trading_job_worker'),
                ('worker_recover_expired_snapshot', 'trading_job_worker')
            )
            SELECT 1
            FROM expected
            JOIN pg_catalog.pg_proc procedure_row
              ON procedure_row.pronamespace = 'job_plane'::regnamespace
             AND procedure_row.proname = expected.procedure_name
            CROSS JOIN unnest(ARRAY[
              'trading_job_api', 'trading_job_worker',
              'trading_job_scheduler'
            ]) role_name(name)
            WHERE has_function_privilege(
                    role_name.name, procedure_row.oid, 'EXECUTE'
                  ) IS DISTINCT FROM
                  (role_name.name = expected.allowed_role)
          ) OR EXISTS (
            WITH expected(procedure_name, allowed_role) AS (
              VALUES
                ('api_enqueue_snapshot', 'trading_job_api'),
                ('api_cancel_snapshot', 'trading_job_api'),
                ('scheduler_enqueue_snapshot', 'trading_job_scheduler'),
                ('worker_claim_snapshot', 'trading_job_worker'),
                ('worker_start_snapshot', 'trading_job_worker'),
                ('worker_control_snapshot_lease', 'trading_job_worker'),
                ('worker_finalize_snapshot', 'trading_job_worker'),
                ('worker_recover_expired_snapshot', 'trading_job_worker')
            )
            SELECT 1
            FROM expected
            JOIN pg_catalog.pg_proc procedure_row
              ON procedure_row.pronamespace = 'job_plane'::regnamespace
             AND procedure_row.proname = expected.procedure_name
            CROSS JOIN LATERAL pg_catalog.aclexplode(
              coalesce(
                procedure_row.proacl,
                pg_catalog.acldefault('f', procedure_row.proowner)
              )
            ) acl
            LEFT JOIN pg_catalog.pg_roles grantee_role
              ON grantee_role.oid = acl.grantee
            WHERE acl.privilege_type = 'EXECUTE'
              AND (
                acl.is_grantable
                OR acl.grantee = 0
                OR (
                  acl.grantee <> procedure_row.proowner
                  AND grantee_role.rolname IS DISTINCT FROM
                        expected.allowed_role
                )
              )
          ) THEN
            RAISE EXCEPTION 'job_plane function ACL is not exact';
          END IF;

          IF has_any_column_privilege(
               'trading_job_api', 'public.jobs', 'INSERT'
             )
             OR has_column_privilege(
               'trading_job_api', 'public.jobs', 'state', 'UPDATE'
             )
             OR has_column_privilege(
               'trading_job_worker', 'public.jobs', 'state', 'UPDATE'
             )
             OR has_any_column_privilege(
               'trading_job_worker', 'public.job_attempts', 'INSERT'
             )
             OR has_column_privilege(
               'trading_job_worker', 'public.job_attempts', 'outcome', 'UPDATE'
             )
             OR has_any_column_privilege(
               'trading_job_scheduler', 'public.jobs', 'INSERT'
             )
             OR EXISTS (
               SELECT 1
               FROM unnest(ARRAY[
                 'trading_job_api', 'trading_job_worker',
                 'trading_job_scheduler'
               ]) role_name(name)
               WHERE has_any_column_privilege(
                 role_name.name, 'public.job_events', 'INSERT'
               )
             ) THEN
            RAISE EXCEPTION 'direct runtime transition DML remains';
          END IF;

          IF EXISTS (
            WITH expected(policy_name) AS (
              VALUES {_policy_values(EXPECTED_0006_POLICIES)}
            ),
            actual(policy_name) AS (
              SELECT policy.polname
              FROM pg_catalog.pg_policy policy
              JOIN pg_catalog.pg_class relation
                ON relation.oid = policy.polrelid
              WHERE relation.relnamespace = 'public'::regnamespace
                AND relation.relname IN (
                  'jobs', 'job_attempts', 'job_events',
                  'scheduler_heartbeats', 'job_artifacts', 'worker_heartbeats'
                )
            ),
            differences(policy_name) AS (
              (SELECT * FROM expected EXCEPT SELECT * FROM actual)
              UNION ALL
              (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            )
            SELECT 1 FROM differences
          ) THEN
            RAISE EXCEPTION '0006 RLS policy catalog is not exact';
          END IF;

          IF to_regprocedure('public.enforce_job_api_cancellation()')
               IS NULL
             OR NOT EXISTS (
               SELECT 1
               FROM pg_catalog.pg_trigger trigger_row
               WHERE trigger_row.tgrelid = 'public.jobs'::regclass
                 AND trigger_row.tgname = 'trg_jobs_job_api_cancellation'
                 AND NOT trigger_row.tgisinternal
             )
             OR NOT EXISTS (
               SELECT 1
               FROM pg_catalog.pg_trigger trigger_row
               WHERE trigger_row.tgrelid = 'public.job_events'::regclass
                 AND trigger_row.tgname = 'trg_job_events_append_only'
                 AND NOT trigger_row.tgisinternal
                 AND trigger_row.tgenabled = 'O'
                 AND trigger_row.tgfoid =
                       'public.reject_job_event_mutation()'::regprocedure
             ) THEN
            RAISE EXCEPTION 'protected trigger catalog is not exact';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM public.jobs job_row
            LEFT JOIN LATERAL (
              SELECT count(*) AS event_count,
                     count(DISTINCT event_row.sequence) AS distinct_count,
                     min(event_row.sequence) AS minimum_sequence,
                     max(event_row.sequence) AS maximum_sequence
              FROM public.job_events event_row
              WHERE event_row.job_id = job_row.job_id
            ) statistics ON true
            LEFT JOIN LATERAL (
              SELECT event_row.to_state
              FROM public.job_events event_row
              WHERE event_row.job_id = job_row.job_id
              ORDER BY event_row.sequence DESC
              LIMIT 1
            ) latest ON true
            WHERE statistics.event_count = 0
               OR statistics.minimum_sequence <> 1
               OR statistics.maximum_sequence <> statistics.event_count
               OR statistics.distinct_count <> statistics.event_count
               OR latest.to_state IS DISTINCT FROM job_row.state
          ) THEN
            RAISE EXCEPTION 'job/event invariant changed during 0006';
          END IF;
        END
        $job_transition_postflight$;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0006 job transition database authority is forward-only; "
        "use a reviewed forward repair"
    )

"""Split durable-job database authority across API, worker, and scheduler.

Revision ID: 0005_job_plane_role_split
Revises: 0004_durable_research_jobs

This is an intentionally forward-only security gate. Cluster-global LOGIN
roles and their distinct credentials must be provisioned by an administrator
before Alembic enters this transaction. The migration owns only in-database
ACL, row-policy, and namespace enforcement.
"""
from __future__ import annotations

from alembic import op


revision = "0005_job_plane_role_split"
down_revision = "0004_durable_research_jobs"
branch_labels = None
depends_on = None


JOB_TABLES = (
    "jobs",
    "job_attempts",
    "job_events",
    "scheduler_heartbeats",
    "job_artifacts",
    "worker_heartbeats",
)

RUNTIME_ROLES = (
    "trading_job_api",
    "trading_job_worker",
    "trading_job_scheduler",
)


def upgrade() -> None:
    # Alembic reaches this revision with the 0004 row still active. The owner is
    # the only accepted executor in both production and disposable verification
    # so object ownership and ACL evidence are identical in both environments.
    op.execute(
        r"""
        DO $job_plane_preflight$
        DECLARE
          active_head text;
        BEGIN
          IF current_user <> 'trading_owner' THEN
            RAISE EXCEPTION '0005 requires exact trading_owner executor';
          END IF;

          IF current_setting('server_version_num')::integer / 10000 <> 16 THEN
            RAISE EXCEPTION '0005 requires PostgreSQL 16';
          END IF;

          IF (
            SELECT pg_get_userbyid(database_row.datdba)
            FROM pg_catalog.pg_database database_row
            WHERE database_row.datname = current_database()
          ) <> 'trading_owner' THEN
            RAISE EXCEPTION '0005 requires trading_owner database ownership';
          END IF;

          IF (
            SELECT pg_get_userbyid(namespace_row.nspowner)
            FROM pg_catalog.pg_namespace namespace_row
            WHERE namespace_row.nspname = 'public'
          ) <> 'trading_owner' THEN
            RAISE EXCEPTION '0005 requires trading_owner public-schema owner';
          END IF;

          SELECT CASE WHEN count(*) = 1 THEN min(version_num) END
          INTO active_head
          FROM public.alembic_version;
          IF active_head IS DISTINCT FROM '0004_durable_research_jobs' THEN
            RAISE EXCEPTION '0005 requires exact 0004 head';
          END IF;

          IF (
            SELECT count(*)
            FROM pg_catalog.pg_class relation
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname IN (
                'alembic_version',
                'jobs',
                'job_attempts',
                'job_events',
                'scheduler_heartbeats',
                'job_artifacts',
                'worker_heartbeats'
              )
              AND pg_get_userbyid(relation.relowner) = 'trading_owner'
          ) <> 7 THEN
            RAISE EXCEPTION '0004 job-plane relation ownership is not exact';
          END IF;

          IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles
            WHERE rolname = 'trading_jobs'
              AND NOT rolcanlogin
              AND NOT rolsuper
              AND NOT rolcreatedb
              AND NOT rolcreaterole
              AND NOT rolinherit
              AND NOT rolreplication
              AND NOT rolbypassrls
          ) THEN
            RAISE EXCEPTION
              'shared trading_jobs role must exist as exact NOLOGIN role';
          END IF;

          IF (
            SELECT count(*)
            FROM pg_catalog.pg_roles
            WHERE rolname IN (
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
              AND rolcanlogin
              AND NOT rolsuper
              AND NOT rolcreatedb
              AND NOT rolcreaterole
              AND NOT rolinherit
              AND NOT rolreplication
              AND NOT rolbypassrls
          ) <> 3 THEN
            RAISE EXCEPTION
              'all three exact job-plane LOGIN roles must be provisioned first';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles role_row
            WHERE role_row.rolname IN (
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
              AND (
                cardinality(role_row.rolconfig) <> 1
                OR NOT EXISTS (
                  SELECT 1
                  FROM unnest(role_row.rolconfig) setting(value)
                  WHERE lower(split_part(setting.value, '=', 1)) = 'timezone'
                    AND split_part(setting.value, '=', 2) = 'UTC'
                )
              )
          ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles role_row
            WHERE role_row.rolname = 'trading_jobs'
              AND role_row.rolconfig IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'job-plane global role settings are not exact';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_db_role_setting role_setting
            JOIN pg_catalog.pg_roles role_row
              ON role_row.oid = role_setting.setrole
            WHERE role_row.rolname IN (
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
              AND role_setting.setdatabase <> 0
          ) THEN
            RAISE EXCEPTION
              'job-plane database-local role settings remain in the cluster';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_auth_members membership
            JOIN pg_catalog.pg_roles granted_role
              ON granted_role.oid = membership.roleid
            JOIN pg_catalog.pg_roles member_role
              ON member_role.oid = membership.member
            WHERE granted_role.rolname IN (
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
            OR member_role.rolname IN (
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
          ) THEN
            RAISE EXCEPTION 'job-plane roles must have no memberships';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_shdepend dependency
            JOIN pg_catalog.pg_roles role_row
              ON role_row.oid = dependency.refobjid
            WHERE role_row.rolname IN (
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
              AND dependency.deptype IN ('a', 'o')
          ) THEN
            RAISE EXCEPTION
              'fresh runtime role already owns an object or has an ACL';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_stat_activity
            WHERE usename IN (
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
              AND pid <> pg_backend_pid()
          ) THEN
            RAISE EXCEPTION
              'job-plane sessions must be zero before authority split';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_policy policy
            JOIN pg_catalog.pg_class relation
              ON relation.oid = policy.polrelid
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname IN (
                'jobs',
                'job_attempts',
                'job_events',
                'scheduler_heartbeats',
                'job_artifacts',
                'worker_heartbeats'
              )
          ) THEN
            RAISE EXCEPTION 'unexpected pre-0005 job-plane RLS policy';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM public.jobs
            WHERE NOT (
              (
                actor_type = 'SCHEDULER'
                AND job_type = 'SNAPSHOT'
                AND priority = 0
                AND idempotency_key ~
                  '^schedule:snapshot:[0-9]{4}-(0[1-9]|1[0-2])-'
                  '(0[1-9]|[12][0-9]|3[01])T'
                  '([01][0-9]|2[0-3]):[0-5][0-9]Z$'
                AND pg_catalog.pg_input_is_valid(
                  substring(
                    idempotency_key FROM
                    '^schedule:snapshot:'
                    '([0-9]{4}-[0-9]{2}-[0-9]{2})T'
                  ),
                  'date'
                )
              )
              OR (
                actor_type <> 'SCHEDULER'
                AND idempotency_key !~ '^schedule:'
              )
            )
          ) THEN
            RAISE EXCEPTION
              'existing job violates the future schedule namespace';
          END IF;

          IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_trigger trigger_row
            WHERE trigger_row.tgrelid = 'public.job_events'::regclass
              AND trigger_row.tgname = 'trg_job_events_append_only'
              AND NOT trigger_row.tgisinternal
              AND trigger_row.tgenabled = 'O'
              AND trigger_row.tgtype = 27
              AND trigger_row.tgfoid =
                'public.reject_job_event_mutation()'::regprocedure
              AND trigger_row.tgqual IS NULL
              AND trigger_row.tgnargs = 0
              AND cardinality(trigger_row.tgattr::smallint[]) = 0
              AND trigger_row.tgconstraint = 0
          ) THEN
            RAISE EXCEPTION '0004 append-only event trigger is absent or changed';
          END IF;

          IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc procedure_row
            JOIN pg_catalog.pg_language language_row
              ON language_row.oid = procedure_row.prolang
            WHERE procedure_row.oid =
              'public.reject_job_event_mutation()'::regprocedure
              AND pg_get_userbyid(procedure_row.proowner) = 'trading_owner'
              AND language_row.lanname = 'plpgsql'
              AND NOT procedure_row.prosecdef
              AND NOT procedure_row.proleakproof
              AND NOT procedure_row.proisstrict
              AND procedure_row.provolatile = 'v'
              AND procedure_row.proparallel = 'u'
              AND procedure_row.proconfig IS NULL
              AND btrim(regexp_replace(
                    procedure_row.prosrc, '[[:space:]]+', ' ', 'g'
                  )) =
                  'BEGIN RAISE EXCEPTION ''job_events is append-only'' '
                  'USING ERRCODE = ''55000''; END;'
          ) THEN
            RAISE EXCEPTION '0004 append-only function definition is not exact';
          END IF;

          IF (
            SELECT count(*)
            FROM pg_catalog.pg_trigger trigger_row
            WHERE NOT trigger_row.tgisinternal
              AND trigger_row.tgrelid IN (
                'public.jobs'::regclass,
                'public.job_attempts'::regclass,
                'public.job_events'::regclass,
                'public.scheduler_heartbeats'::regclass,
                'public.job_artifacts'::regclass,
                'public.worker_heartbeats'::regclass
              )
          ) <> 1 THEN
            RAISE EXCEPTION 'unexpected pre-0005 job-plane trigger exists';
          END IF;
        END
        $job_plane_preflight$;
        """
    )

    # Reserve the entire schedule: namespace at the database boundary. Every
    # scheduler-authored job is one canonical SNAPSHOT slot at priority zero;
    # no other actor may claim that namespace.
    op.execute(
        r"""
        ALTER TABLE public.jobs
        ADD CONSTRAINT ck_jobs_schedule_namespace
        CHECK (
          (
            actor_type = 'SCHEDULER'
            AND job_type = 'SNAPSHOT'
            AND priority = 0
            AND idempotency_key ~
              '^schedule:snapshot:[0-9]{4}-(0[1-9]|1[0-2])-'
              '(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]Z$'
            AND pg_catalog.pg_input_is_valid(
              substring(
                idempotency_key FROM
                '^schedule:snapshot:([0-9]{4}-[0-9]{2}-[0-9]{2})T'
              ),
              'date'
            )
          )
          OR (
            actor_type <> 'SCHEDULER'
            AND idempotency_key !~ '^schedule:'
          )
        );
        """
    )

    # RLS cannot compare OLD and NEW rows. A protected invoker trigger makes
    # the API's only UPDATE authority an exact cancellation transition while
    # leaving owner/worker state-machine writes to their separately gated SQL.
    op.execute(
        r"""
        CREATE FUNCTION public.enforce_job_api_cancellation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $function$
        BEGIN
          IF current_user = 'trading_job_api' THEN
            IF OLD.job_type <> 'SNAPSHOT'
               OR OLD.state NOT IN ('QUEUED', 'CLAIMED', 'RUNNING')
               OR NEW.state IS DISTINCT FROM (CASE OLD.state
                    WHEN 'QUEUED' THEN 'CANCELLED'
                    ELSE 'CANCEL_REQUESTED'
                  END)
               OR NEW.reason_code IS DISTINCT FROM 'CANCEL_REQUESTED'
               OR NEW.cancel_requested_at IS NULL
               OR NEW.cancel_requested_at < OLD.requested_at
               OR NEW.cancel_actor_type IS DISTINCT FROM 'OPERATOR'
               OR NEW.cancel_actor_id IS NULL
               OR btrim(NEW.cancel_actor_id) = ''
               OR NEW.updated_at < OLD.updated_at THEN
              RAISE EXCEPTION 'job API cancellation transition rejected'
                USING ERRCODE = '42501';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $function$;

        CREATE TRIGGER trg_jobs_job_api_cancellation
        BEFORE UPDATE ON public.jobs
        FOR EACH ROW EXECUTE FUNCTION public.enforce_job_api_cancellation();
        """
    )

    job_tables = ", ".join(f"public.{name}" for name in JOB_TABLES)
    grantees = ", ".join(
        (
            "PUBLIC",
            "trading_jobs",
            "trading_migrator",
            "trading_reader",
            *RUNTIME_ROLES,
        )
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON TABLE {job_tables} FROM {grantees}"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.alembic_version FROM "
        "trading_jobs, trading_job_api, trading_job_worker, "
        "trading_job_scheduler"
    )

    # Table-level REVOKE does not remove independently granted column ACLs.
    # Remove PUBLIC/shared/runtime column authority from every relation in the
    # public schema, then remove legacy reader/migrator column authority from
    # the six job tables before adding the explicit matrix below.
    op.execute(
        r"""
        DO $clear_public_column_acls$
        DECLARE
          target_relation regclass;
          column_list text;
        BEGIN
          FOR target_relation IN
            SELECT relation.oid::regclass
            FROM pg_catalog.pg_class relation
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
            ORDER BY relation.oid
          LOOP
            SELECT string_agg(format('%I', attribute.attname), ', '
                              ORDER BY attribute.attnum)
            INTO column_list
            FROM pg_catalog.pg_attribute attribute
            WHERE attribute.attrelid = target_relation
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped;

            IF column_list IS NOT NULL THEN
              EXECUTE format(
                'REVOKE ALL PRIVILEGES (%s) ON TABLE %s FROM PUBLIC, '
                'trading_jobs, trading_job_api, trading_job_worker, '
                'trading_job_scheduler',
                column_list,
                target_relation
              );
            END IF;
          END LOOP;
        END
        $clear_public_column_acls$;

        DO $clear_legacy_job_column_acls$
        DECLARE
          target_table text;
          column_list text;
        BEGIN
          FOREACH target_table IN ARRAY ARRAY[
            'jobs', 'job_attempts', 'job_events',
            'scheduler_heartbeats', 'job_artifacts', 'worker_heartbeats'
          ]
          LOOP
            SELECT string_agg(format('%I', attribute.attname), ', '
                              ORDER BY attribute.attnum)
            INTO column_list
            FROM pg_catalog.pg_attribute attribute
            WHERE attribute.attrelid =
                    format('public.%I', target_table)::regclass
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped;

            EXECUTE format(
              'REVOKE ALL PRIVILEGES (%s) ON TABLE public.%I FROM '
              'trading_migrator, trading_reader',
              column_list,
              target_table
            );
          END LOOP;
        END
        $clear_legacy_job_column_acls$;
        """
    )

    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.reject_job_event_mutation() FROM PUBLIC, trading_jobs, "
        "trading_migrator, trading_reader, trading_job_api, "
        "trading_job_worker, trading_job_scheduler"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.enforce_job_api_cancellation() FROM PUBLIC, trading_jobs, "
        "trading_migrator, trading_reader, trading_job_api, "
        "trading_job_worker, trading_job_scheduler"
    )

    # Clear all ambient PUBLIC/shared/runtime object authority in public before
    # adding the exact job-plane grants. This also prevents a preexisting PUBLIC
    # grant on a legacy object from becoming runtime-role authority.
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "
        "PUBLIC, trading_jobs, trading_job_api, trading_job_worker, "
        "trading_job_scheduler"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM "
        "PUBLIC, trading_jobs, trading_job_api, trading_job_worker, "
        "trading_job_scheduler"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM "
        "PUBLIC, trading_jobs, trading_job_api, trading_job_worker, "
        "trading_job_scheduler"
    )

    # 0001 default ACLs leaked reader/migrator privileges onto every 0004 job
    # table. Remove that future inheritance rather than granting runtime roles
    # through defaults. Every later job-plane object must be explicitly gated.
    for role in (
        "trading_migrator",
        "trading_reader",
        "trading_jobs",
        *RUNTIME_ROLES,
    ):
        op.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA public "
            f"REVOKE ALL PRIVILEGES ON TABLES FROM {role}"
        )
        op.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA public "
            f"REVOKE ALL PRIVILEGES ON SEQUENCES FROM {role}"
        )
        op.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA public "
            f"REVOKE ALL PRIVILEGES ON FUNCTIONS FROM {role}"
        )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA public "
        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )

    # PUBLIC CONNECT would make an explicit REVOKE from trading_jobs
    # ineffective. Replace the database CONNECT ACL with the exact operational
    # identities, then remove shared schema authority. Legacy reader/migrator
    # retain their non-job-plane responsibilities but have zero ACL on all six
    # job tables.
    op.execute(
        r"""
        DO $database_acl$
        BEGIN
          EXECUTE format(
            'REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC, '
            'trading_jobs, trading_migrator, trading_reader, '
            'trading_job_api, trading_job_worker, trading_job_scheduler',
            current_database()
          );
          EXECUTE format(
            'GRANT CONNECT ON DATABASE %I TO trading_migrator, '
            'trading_reader, trading_job_api, '
            'trading_job_worker, trading_job_scheduler',
            current_database()
          );
          EXECUTE format(
            'GRANT TEMPORARY ON DATABASE %I TO trading_migrator',
            current_database()
          );
        END
        $database_acl$;

        REVOKE USAGE, CREATE ON SCHEMA public
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        GRANT USAGE ON SCHEMA public
          TO trading_migrator, trading_reader,
             trading_job_api, trading_job_worker, trading_job_scheduler;
        GRANT SELECT ON TABLE public.alembic_version
          TO trading_job_api, trading_job_worker, trading_job_scheduler;
        """
    )

    # Job API: public job projection, append-only operator events, initial
    # SNAPSHOT insert, and cancellation-only mutation columns. Lease, result
    # metadata, worker heartbeat, and scheduler heartbeat authority is absent.
    op.execute(
        r"""
        GRANT SELECT (
          job_id, job_type, state, payload, payload_fingerprint,
          idempotency_key, actor_type, actor_id, priority, requested_at,
          updated_at, attempt_count, max_attempts, reason_code, result_hash,
          cancel_requested_at, cancel_actor_type, cancel_actor_id
        ) ON TABLE public.jobs TO trading_job_api;
        GRANT INSERT (
          job_id, job_type, state, payload, payload_fingerprint,
          idempotency_key, actor_type, actor_id, priority, max_attempts
        ) ON TABLE public.jobs TO trading_job_api;
        GRANT UPDATE (
          state, updated_at, reason_code, cancel_requested_at,
          cancel_actor_type, cancel_actor_id
        ) ON TABLE public.jobs TO trading_job_api;

        GRANT SELECT (
          attempt_id, job_id, attempt_number, worker_id, outcome, claimed_at,
          started_at, finished_at, exit_code, termination_reason
        ) ON TABLE public.job_attempts TO trading_job_api;
        GRANT SELECT ON TABLE public.job_events TO trading_job_api;
        GRANT INSERT (
          event_id, job_id, attempt_id, sequence, from_state, to_state,
          reason_code, actor_type, actor_id, trace_id, metadata
        ) ON TABLE public.job_events TO trading_job_api;
        GRANT SELECT ON TABLE public.job_artifacts TO trading_job_api;
        """
    )

    # Worker: claim/fence/finalize SNAPSHOT rows, own attempts/artifacts/events,
    # and publish its heartbeat. It cannot INSERT jobs or access scheduler
    # heartbeats, so it cannot enqueue or impersonate the scheduler plane.
    op.execute(
        r"""
        GRANT SELECT ON TABLE public.jobs TO trading_job_worker;
        GRANT UPDATE (
          state, updated_at, attempt_count, next_attempt_at, lease_owner,
          lease_token, lease_expires_at, reason_code, result_hash,
          result_metadata, error_code, error_message, finished_at
        ) ON TABLE public.jobs TO trading_job_worker;

        GRANT SELECT ON TABLE public.job_attempts TO trading_job_worker;
        GRANT INSERT (
          attempt_id, job_id, attempt_number, worker_id, outcome, lease_token,
          lease_expires_at, claimed_at
        ) ON TABLE public.job_attempts TO trading_job_worker;
        GRANT UPDATE (
          outcome, lease_expires_at, started_at, heartbeat_at, finished_at,
          child_pid, process_group_id, process_start_ticks,
          command_fingerprint, exit_code, termination_reason,
          error_code, error_message
        ) ON TABLE public.job_attempts TO trading_job_worker;

        GRANT SELECT ON TABLE public.job_events TO trading_job_worker;
        GRANT INSERT (
          event_id, job_id, attempt_id, sequence, from_state, to_state,
          reason_code, actor_type, actor_id, trace_id, metadata
        ) ON TABLE public.job_events TO trading_job_worker;
        GRANT INSERT (
          artifact_id, job_id, attempt_id, artifact_type, relative_ref,
          sha256, size_bytes, media_type, truncated, validator_id,
          validation_metadata
        ) ON TABLE public.job_artifacts TO trading_job_worker;
        GRANT SELECT ON TABLE public.worker_heartbeats TO trading_job_worker;
        GRANT INSERT (
          worker_id, code_commit, status, current_job_id,
          current_attempt_id, heartbeat_at, metadata
        ) ON TABLE public.worker_heartbeats TO trading_job_worker;
        GRANT UPDATE (
          code_commit, status, current_job_id, current_attempt_id,
          heartbeat_at, metadata
        ) ON TABLE public.worker_heartbeats TO trading_job_worker;
        """
    )

    # Scheduler: exact scheduled-SNAPSHOT insert/dedupe projection, its initial
    # event, and append-only scheduler heartbeat. It receives no UPDATE column,
    # attempt/artifact/worker table privilege, or lease-column SELECT.
    op.execute(
        r"""
        GRANT SELECT (
          job_id, job_type, state, payload, payload_fingerprint,
          idempotency_key, actor_type, actor_id, priority, requested_at,
          updated_at, attempt_count, max_attempts, reason_code, result_hash,
          cancel_requested_at, cancel_actor_type, cancel_actor_id
        ) ON TABLE public.jobs TO trading_job_scheduler;
        GRANT INSERT (
          job_id, job_type, state, payload, payload_fingerprint,
          idempotency_key, actor_type, actor_id, priority, max_attempts
        ) ON TABLE public.jobs TO trading_job_scheduler;
        GRANT SELECT ON TABLE public.job_events TO trading_job_scheduler;
        GRANT INSERT (
          event_id, job_id, attempt_id, sequence, from_state, to_state,
          reason_code, actor_type, actor_id, trace_id, metadata
        ) ON TABLE public.job_events TO trading_job_scheduler;
        GRANT INSERT (
          heartbeat_id, scheduler_id, code_commit, actor_id, trace_id,
          tick_at, slot_at, outcome, job_id, reason_code, metadata
        ) ON TABLE public.scheduler_heartbeats TO trading_job_scheduler;
        """
    )

    # RLS supplements the column ACL matrix with actor and namespace
    # invariants. The table owner remains the migration/break-glass authority;
    # production services are the three unprivileged, NOBYPASSRLS roles.
    op.execute(
        r"""
        ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.job_attempts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.job_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.scheduler_heartbeats ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.job_artifacts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.worker_heartbeats ENABLE ROW LEVEL SECURITY;

        CREATE POLICY job_plane_api_jobs_select
          ON public.jobs FOR SELECT TO trading_job_api
          USING (true);
        CREATE POLICY job_plane_api_jobs_insert
          ON public.jobs FOR INSERT TO trading_job_api
          WITH CHECK (
            job_type = 'SNAPSHOT'
            AND state = 'QUEUED'
            AND actor_type = 'OPERATOR'
            AND btrim(actor_id) <> ''
            AND idempotency_key !~ '^schedule:'
            AND attempt_count = 0
            AND max_attempts = 2
            AND next_attempt_at IS NULL
            AND lease_owner IS NULL
            AND lease_token IS NULL
            AND lease_expires_at IS NULL
            AND cancel_requested_at IS NULL
            AND cancel_actor_type IS NULL
            AND cancel_actor_id IS NULL
            AND result_hash IS NULL
            AND result_metadata = '{}'::jsonb
            AND error_code IS NULL
            AND error_message IS NULL
            AND finished_at IS NULL
          );
        CREATE POLICY job_plane_api_jobs_cancel
          ON public.jobs FOR UPDATE TO trading_job_api
          USING (
            job_type = 'SNAPSHOT'
            AND state IN ('QUEUED', 'CLAIMED', 'RUNNING')
          )
          WITH CHECK (
            job_type = 'SNAPSHOT'
            AND state IN ('CANCEL_REQUESTED', 'CANCELLED')
            AND reason_code = 'CANCEL_REQUESTED'
            AND cancel_requested_at IS NOT NULL
            AND cancel_actor_type = 'OPERATOR'
            AND cancel_actor_id IS NOT NULL
          );

        CREATE POLICY job_plane_worker_jobs_select
          ON public.jobs FOR SELECT TO trading_job_worker
          USING (job_type = 'SNAPSHOT');
        CREATE POLICY job_plane_worker_jobs_update
          ON public.jobs FOR UPDATE TO trading_job_worker
          USING (job_type = 'SNAPSHOT')
          WITH CHECK (job_type = 'SNAPSHOT');

        CREATE POLICY job_plane_scheduler_jobs_select
          ON public.jobs FOR SELECT TO trading_job_scheduler
          USING (
            job_type = 'SNAPSHOT'
            AND actor_type = 'SCHEDULER'
            AND priority = 0
            AND idempotency_key ~
              '^schedule:snapshot:[0-9]{4}-(0[1-9]|1[0-2])-'
              '(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]Z$'
            AND pg_catalog.pg_input_is_valid(
              substring(
                idempotency_key FROM
                '^schedule:snapshot:([0-9]{4}-[0-9]{2}-[0-9]{2})T'
              ),
              'date'
            )
          );
        CREATE POLICY job_plane_scheduler_jobs_insert
          ON public.jobs FOR INSERT TO trading_job_scheduler
          WITH CHECK (
            job_type = 'SNAPSHOT'
            AND state = 'QUEUED'
            AND actor_type = 'SCHEDULER'
            AND btrim(actor_id) <> ''
            AND priority = 0
            AND idempotency_key ~
              '^schedule:snapshot:[0-9]{4}-(0[1-9]|1[0-2])-'
              '(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]Z$'
            AND pg_catalog.pg_input_is_valid(
              substring(
                idempotency_key FROM
                '^schedule:snapshot:([0-9]{4}-[0-9]{2}-[0-9]{2})T'
              ),
              'date'
            )
            AND attempt_count = 0
            AND max_attempts = 2
            AND next_attempt_at IS NULL
            AND lease_owner IS NULL
            AND lease_token IS NULL
            AND lease_expires_at IS NULL
            AND cancel_requested_at IS NULL
            AND cancel_actor_type IS NULL
            AND cancel_actor_id IS NULL
            AND result_hash IS NULL
            AND result_metadata = '{}'::jsonb
            AND error_code IS NULL
            AND error_message IS NULL
            AND finished_at IS NULL
          );

        CREATE POLICY job_plane_api_attempts_select
          ON public.job_attempts FOR SELECT TO trading_job_api
          USING (
            EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = job_attempts.job_id
            )
          );
        CREATE POLICY job_plane_worker_attempts_select
          ON public.job_attempts FOR SELECT TO trading_job_worker
          USING (
            EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = job_attempts.job_id
                AND jobs.job_type = 'SNAPSHOT'
            )
          );
        CREATE POLICY job_plane_worker_attempts_insert
          ON public.job_attempts FOR INSERT TO trading_job_worker
          WITH CHECK (
            EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = job_attempts.job_id
                AND jobs.job_type = 'SNAPSHOT'
            )
          );
        CREATE POLICY job_plane_worker_attempts_update
          ON public.job_attempts FOR UPDATE TO trading_job_worker
          USING (
            EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = job_attempts.job_id
                AND jobs.job_type = 'SNAPSHOT'
            )
          )
          WITH CHECK (
            EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = job_attempts.job_id
                AND jobs.job_type = 'SNAPSHOT'
            )
          );

        CREATE POLICY job_plane_api_events_select
          ON public.job_events FOR SELECT TO trading_job_api
          USING (
            EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = job_events.job_id
            )
          );
        CREATE POLICY job_plane_api_events_insert
          ON public.job_events FOR INSERT TO trading_job_api
          WITH CHECK (
            actor_type = 'OPERATOR'
            AND attempt_id IS NULL
            AND (
              (
                sequence = 1
                AND
                from_state IS NULL
                AND to_state = 'QUEUED'
                AND reason_code = 'ENQUEUED'
                AND EXISTS (
                  SELECT 1 FROM public.jobs
                  WHERE jobs.job_id = job_events.job_id
                    AND jobs.actor_type = 'OPERATOR'
                    AND jobs.actor_id = job_events.actor_id
                    AND jobs.state = 'QUEUED'
                )
              )
              OR (
                sequence >= 2
                AND
                from_state IN ('QUEUED', 'CLAIMED', 'RUNNING')
                AND to_state IN ('CANCEL_REQUESTED', 'CANCELLED')
                AND reason_code = 'CANCEL_REQUESTED'
                AND EXISTS (
                  SELECT 1 FROM public.jobs
                  WHERE jobs.job_id = job_events.job_id
                    AND jobs.state = job_events.to_state
                    AND jobs.reason_code = job_events.reason_code
                    AND jobs.cancel_actor_type = 'OPERATOR'
                    AND jobs.cancel_actor_id = job_events.actor_id
                )
              )
            )
          );
        CREATE POLICY job_plane_worker_events_select
          ON public.job_events FOR SELECT TO trading_job_worker
          USING (
            EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = job_events.job_id
                AND jobs.job_type = 'SNAPSHOT'
            )
          );
        CREATE POLICY job_plane_worker_events_insert
          ON public.job_events FOR INSERT TO trading_job_worker
          WITH CHECK (
            actor_type IN ('WORKER', 'RECOVERY')
            AND attempt_id IS NOT NULL
            AND sequence >= 2
            AND EXISTS (
              SELECT 1
              FROM public.jobs
              JOIN public.job_attempts
                ON job_attempts.job_id = jobs.job_id
               AND job_attempts.attempt_id = job_events.attempt_id
              WHERE jobs.job_id = job_events.job_id
                AND jobs.job_type = 'SNAPSHOT'
                AND jobs.state = job_events.to_state
                AND jobs.reason_code = job_events.reason_code
                AND (
                  (
                    job_events.actor_type = 'WORKER'
                    AND job_attempts.worker_id = job_events.actor_id
                  )
                  OR (
                    job_events.actor_type = 'RECOVERY'
                    AND job_events.actor_id IN (
                      'lease-recovery', 'worker-startup-recovery'
                    )
                  )
                )
            )
          );
        CREATE POLICY job_plane_scheduler_events_select
          ON public.job_events FOR SELECT TO trading_job_scheduler
          USING (
            EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = job_events.job_id
                AND jobs.actor_type = 'SCHEDULER'
            )
          );
        CREATE POLICY job_plane_scheduler_events_insert
          ON public.job_events FOR INSERT TO trading_job_scheduler
          WITH CHECK (
            actor_type = 'SCHEDULER'
            AND attempt_id IS NULL
            AND sequence = 1
            AND from_state IS NULL
            AND to_state = 'QUEUED'
            AND reason_code = 'ENQUEUED'
            AND EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = job_events.job_id
                AND jobs.actor_type = 'SCHEDULER'
                AND jobs.actor_id = job_events.actor_id
                AND jobs.state = 'QUEUED'
            )
          );

        CREATE POLICY job_plane_scheduler_heartbeats_insert
          ON public.scheduler_heartbeats FOR INSERT TO trading_job_scheduler
          WITH CHECK (
            btrim(scheduler_id) <> ''
            AND btrim(actor_id) <> ''
            AND (
              job_id IS NULL
              OR EXISTS (
                SELECT 1 FROM public.jobs
                WHERE jobs.job_id = scheduler_heartbeats.job_id
                  AND jobs.actor_type = 'SCHEDULER'
                  AND jobs.actor_id = scheduler_heartbeats.actor_id
              )
            )
          );

        CREATE POLICY job_plane_api_artifacts_select
          ON public.job_artifacts FOR SELECT TO trading_job_api
          USING (
            EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = job_artifacts.job_id
            )
          );
        CREATE POLICY job_plane_worker_artifacts_insert
          ON public.job_artifacts FOR INSERT TO trading_job_worker
          WITH CHECK (
            EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = job_artifacts.job_id
                AND jobs.job_type = 'SNAPSHOT'
            )
          );

        CREATE POLICY job_plane_worker_heartbeats_select
          ON public.worker_heartbeats FOR SELECT TO trading_job_worker
          USING (true);
        CREATE POLICY job_plane_worker_heartbeats_insert
          ON public.worker_heartbeats FOR INSERT TO trading_job_worker
          WITH CHECK (
            current_job_id IS NULL
            OR EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = worker_heartbeats.current_job_id
                AND jobs.job_type = 'SNAPSHOT'
            )
          );
        CREATE POLICY job_plane_worker_heartbeats_update
          ON public.worker_heartbeats FOR UPDATE TO trading_job_worker
          USING (true)
          WITH CHECK (
            current_job_id IS NULL
            OR EXISTS (
              SELECT 1 FROM public.jobs
              WHERE jobs.job_id = worker_heartbeats.current_job_id
                AND jobs.job_type = 'SNAPSHOT'
            )
          );
        """
    )

    # Finish with catalog-only postconditions inside the same transaction. Any
    # mismatch rolls back all ACL and policy changes and leaves 0004 active.
    op.execute(
        r"""
        DO $job_plane_postflight$
        BEGIN
          IF (
            SELECT count(*)
            FROM pg_catalog.pg_class relation
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname IN (
                'jobs',
                'job_attempts',
                'job_events',
                'scheduler_heartbeats',
                'job_artifacts',
                'worker_heartbeats'
              )
              AND relation.relrowsecurity
              AND NOT relation.relforcerowsecurity
          ) <> 6 THEN
            RAISE EXCEPTION 'job-plane RLS flags do not match 0005 policy';
          END IF;

          IF (
            SELECT count(*)
            FROM pg_catalog.pg_policy policy
            JOIN pg_catalog.pg_class relation
              ON relation.oid = policy.polrelid
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname IN (
                'jobs',
                'job_attempts',
                'job_events',
                'scheduler_heartbeats',
                'job_artifacts',
                'worker_heartbeats'
              )
          ) <> 23 THEN
            RAISE EXCEPTION 'job-plane policy set is incomplete';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles role_row
            WHERE role_row.rolname IN (
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
              AND (
                cardinality(role_row.rolconfig) <> 1
                OR NOT EXISTS (
                  SELECT 1
                  FROM unnest(role_row.rolconfig) setting(value)
                  WHERE lower(split_part(setting.value, '=', 1)) = 'timezone'
                    AND split_part(setting.value, '=', 2) = 'UTC'
                )
              )
          ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles role_row
            WHERE role_row.rolname = 'trading_jobs'
              AND role_row.rolconfig IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'job-plane global role settings changed';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_db_role_setting role_setting
            JOIN pg_catalog.pg_roles role_row
              ON role_row.oid = role_setting.setrole
            WHERE role_row.rolname IN (
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
              AND role_setting.setdatabase <> 0
          ) THEN
            RAISE EXCEPTION 'database-local job-role setting remains';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_auth_members membership
            JOIN pg_catalog.pg_roles granted_role
              ON granted_role.oid = membership.roleid
            JOIN pg_catalog.pg_roles member_role
              ON member_role.oid = membership.member
            WHERE granted_role.rolname IN (
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
            OR member_role.rolname IN (
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
          ) THEN
            RAISE EXCEPTION 'job-plane role membership appeared during 0005';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_shdepend dependency
            JOIN pg_catalog.pg_roles role_row
              ON role_row.oid = dependency.refobjid
            WHERE role_row.rolname IN (
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
              AND dependency.deptype = 'o'
          ) THEN
            RAISE EXCEPTION 'job-plane role owns an object';
          END IF;

          IF (
            SELECT count(*)
            FROM pg_catalog.pg_class relation
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname IN (
                'alembic_version',
                'jobs',
                'job_attempts',
                'job_events',
                'scheduler_heartbeats',
                'job_artifacts',
                'worker_heartbeats'
              )
              AND pg_get_userbyid(relation.relowner) = 'trading_owner'
          ) <> 7 THEN
            RAISE EXCEPTION 'job-plane relation ownership is not exact';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            JOIN pg_catalog.pg_attribute attribute
              ON attribute.attrelid = relation.oid
            CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) acl
            WHERE relation.relnamespace = 'public'::regnamespace
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
              AND acl.grantee = 0
          ) THEN
            RAISE EXCEPTION 'PUBLIC column ACL remains in public schema';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            CROSS JOIN unnest(ARRAY[
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            ]) AS role_name(name)
            CROSS JOIN unnest(ARRAY[
              'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
              'REFERENCES', 'TRIGGER'
            ]) AS privilege_name(name)
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND relation.relname NOT IN (
                'alembic_version',
                'jobs',
                'job_attempts',
                'job_events',
                'scheduler_heartbeats',
                'job_artifacts',
                'worker_heartbeats'
              )
              AND has_table_privilege(
                role_name.name, relation.oid, privilege_name.name
              )
          ) THEN
            RAISE EXCEPTION
              'job-plane role retains authority on a non-job relation';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class sequence_row
            CROSS JOIN unnest(ARRAY[
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            ]) AS role_name(name)
            CROSS JOIN unnest(ARRAY[
              'USAGE', 'SELECT', 'UPDATE'
            ]) AS privilege_name(name)
            WHERE sequence_row.relnamespace = 'public'::regnamespace
              AND sequence_row.relkind = 'S'
              AND has_sequence_privilege(
                role_name.name, sequence_row.oid, privilege_name.name
              )
          ) THEN
            RAISE EXCEPTION 'job-plane role retains sequence authority';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc procedure_row
            CROSS JOIN unnest(ARRAY[
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            ]) AS role_name(name)
            WHERE procedure_row.pronamespace = 'public'::regnamespace
              AND has_function_privilege(
                role_name.name, procedure_row.oid, 'EXECUTE'
              )
          ) THEN
            RAISE EXCEPTION 'job-plane role retains function authority';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) acl
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname IN (
                'jobs',
                'job_attempts',
                'job_events',
                'scheduler_heartbeats',
                'job_artifacts',
                'worker_heartbeats'
              )
              AND acl.grantee IN (
                'trading_jobs'::regrole,
                'trading_migrator'::regrole,
                'trading_reader'::regrole,
                'trading_job_api'::regrole,
                'trading_job_worker'::regrole,
                'trading_job_scheduler'::regrole
              )
              AND acl.is_grantable
          ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            JOIN pg_catalog.pg_attribute attribute
              ON attribute.attrelid = relation.oid
            CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) acl
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname IN (
                'jobs',
                'job_attempts',
                'job_events',
                'scheduler_heartbeats',
                'job_artifacts',
                'worker_heartbeats'
              )
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
              AND acl.grantee IN (
                'trading_jobs'::regrole,
                'trading_migrator'::regrole,
                'trading_reader'::regrole,
                'trading_job_api'::regrole,
                'trading_job_worker'::regrole,
                'trading_job_scheduler'::regrole
              )
              AND acl.is_grantable
          ) THEN
            RAISE EXCEPTION 'job-plane grant option remains';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM unnest(ARRAY[
              'jobs',
              'job_attempts',
              'job_events',
              'scheduler_heartbeats',
              'job_artifacts',
              'worker_heartbeats'
            ]) AS table_name(name)
            CROSS JOIN unnest(ARRAY[
              'trading_jobs',
              'trading_migrator',
              'trading_reader'
            ]) AS role_name(name)
            WHERE EXISTS (
                    SELECT 1
                    FROM unnest(ARRAY[
                      'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
                      'REFERENCES', 'TRIGGER'
                    ]) AS privilege_name(name)
                    WHERE has_table_privilege(
                      role_name.name,
                      format('public.%I', table_name.name),
                      privilege_name.name
                    )
                  )
               OR EXISTS (
                    SELECT 1
                    FROM unnest(ARRAY[
                      'SELECT', 'INSERT', 'UPDATE', 'REFERENCES'
                    ]) AS privilege_name(name)
                    WHERE has_any_column_privilege(
                      role_name.name,
                      format('public.%I', table_name.name),
                      privilege_name.name
                    )
                  )
          ) THEN
            RAISE EXCEPTION
              'legacy/shared job-plane object privilege remains';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_default_acl default_acl
            CROSS JOIN LATERAL pg_catalog.aclexplode(default_acl.defaclacl) acl
            WHERE acl.grantee IN (
              'trading_jobs'::regrole,
              'trading_migrator'::regrole,
              'trading_reader'::regrole,
              'trading_job_api'::regrole,
              'trading_job_worker'::regrole,
              'trading_job_scheduler'::regrole
            )
          ) THEN
            RAISE EXCEPTION 'legacy/runtime default ACL leakage remains';
          END IF;

          IF (
            SELECT pg_get_userbyid(database_row.datdba)
            FROM pg_catalog.pg_database database_row
            WHERE database_row.datname = current_database()
          ) <> 'trading_owner' THEN
            RAISE EXCEPTION 'database ownership changed during 0005';
          END IF;

          IF has_database_privilege(
               'trading_jobs', current_database(), 'CONNECT'
             ) THEN
            RAISE EXCEPTION 'shared trading_jobs retains effective CONNECT';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM unnest(ARRAY[
              'trading_owner',
              'trading_migrator',
              'trading_reader',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            ]) AS role_name(name)
            WHERE NOT has_database_privilege(
              role_name.name, current_database(), 'CONNECT'
            )
          ) THEN
            RAISE EXCEPTION 'required role lacks effective database CONNECT';
          END IF;

          IF EXISTS (
            WITH expected(role_name, is_grantable) AS (
              VALUES
                ('trading_owner', false),
                ('trading_migrator', false),
                ('trading_reader', false),
                ('trading_job_api', false),
                ('trading_job_worker', false),
                ('trading_job_scheduler', false)
            ),
            actual(role_name, is_grantable) AS (
              SELECT CASE
                       WHEN acl.grantee = 0 THEN 'PUBLIC'
                       ELSE pg_get_userbyid(acl.grantee)
                     END,
                     acl.is_grantable
              FROM pg_catalog.pg_database database_row
              CROSS JOIN LATERAL pg_catalog.aclexplode(
                coalesce(
                  database_row.datacl,
                  pg_catalog.acldefault('d', database_row.datdba)
                )
              ) acl
              WHERE database_row.datname = current_database()
                AND acl.privilege_type = 'CONNECT'
            ),
            differences(role_name, is_grantable) AS (
              (SELECT * FROM expected EXCEPT SELECT * FROM actual)
              UNION ALL
              (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            )
            SELECT 1 FROM differences
          ) THEN
            RAISE EXCEPTION 'database CONNECT ACL is not the exact role set';
          END IF;

          IF has_database_privilege(
               'trading_jobs', current_database(), 'TEMPORARY'
             ) OR EXISTS (
               SELECT 1
               FROM unnest(ARRAY[
                 'trading_reader',
                 'trading_job_api',
                 'trading_job_worker',
                 'trading_job_scheduler'
               ]) AS role_name(name)
               WHERE has_database_privilege(
                 role_name.name, current_database(), 'TEMPORARY'
               )
             ) THEN
            RAISE EXCEPTION 'unapproved role retains effective TEMPORARY';
          END IF;

          IF NOT has_database_privilege(
                   'trading_owner', current_database(), 'TEMPORARY'
                 )
             OR NOT has_database_privilege(
                   'trading_migrator', current_database(), 'TEMPORARY'
                 ) THEN
            RAISE EXCEPTION 'owner/migrator TEMPORARY authority is incomplete';
          END IF;

          IF EXISTS (
            WITH expected(role_name, is_grantable) AS (
              VALUES
                ('trading_owner', false),
                ('trading_migrator', false)
            ),
            actual(role_name, is_grantable) AS (
              SELECT CASE
                       WHEN acl.grantee = 0 THEN 'PUBLIC'
                       ELSE pg_get_userbyid(acl.grantee)
                     END,
                     acl.is_grantable
              FROM pg_catalog.pg_database database_row
              CROSS JOIN LATERAL pg_catalog.aclexplode(
                coalesce(
                  database_row.datacl,
                  pg_catalog.acldefault('d', database_row.datdba)
                )
              ) acl
              WHERE database_row.datname = current_database()
                AND acl.privilege_type = 'TEMPORARY'
            ),
            differences(role_name, is_grantable) AS (
              (SELECT * FROM expected EXCEPT SELECT * FROM actual)
              UNION ALL
              (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            )
            SELECT 1 FROM differences
          ) THEN
            RAISE EXCEPTION 'database TEMPORARY ACL is not the exact role set';
          END IF;

          IF (
            SELECT pg_get_userbyid(namespace_row.nspowner)
            FROM pg_catalog.pg_namespace namespace_row
            WHERE namespace_row.nspname = 'public'
          ) <> 'trading_owner' THEN
            RAISE EXCEPTION 'public schema ownership changed during 0005';
          END IF;

          IF has_schema_privilege('trading_jobs', 'public', 'USAGE') THEN
            RAISE EXCEPTION 'shared trading_jobs retains effective schema USAGE';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM unnest(ARRAY[
              'trading_owner',
              'trading_migrator',
              'trading_reader',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            ]) AS role_name(name)
            WHERE NOT has_schema_privilege(role_name.name, 'public', 'USAGE')
          ) THEN
            RAISE EXCEPTION 'required role lacks effective schema USAGE';
          END IF;

          IF EXISTS (
            WITH expected(role_name, privilege_name, is_grantable) AS (
              VALUES
                ('trading_owner', 'USAGE', false),
                ('trading_owner', 'CREATE', false),
                ('trading_migrator', 'USAGE', false),
                ('trading_reader', 'USAGE', false),
                ('trading_job_api', 'USAGE', false),
                ('trading_job_worker', 'USAGE', false),
                ('trading_job_scheduler', 'USAGE', false)
            ),
            actual(role_name, privilege_name, is_grantable) AS (
              SELECT CASE
                       WHEN acl.grantee = 0 THEN 'PUBLIC'
                       ELSE pg_get_userbyid(acl.grantee)
                     END,
                     acl.privilege_type,
                     acl.is_grantable
              FROM pg_catalog.pg_namespace namespace_row
              CROSS JOIN LATERAL pg_catalog.aclexplode(
                coalesce(
                  namespace_row.nspacl,
                  pg_catalog.acldefault('n', namespace_row.nspowner)
                )
              ) acl
              WHERE namespace_row.nspname = 'public'
                AND acl.privilege_type IN ('USAGE', 'CREATE')
            ),
            differences(role_name, privilege_name, is_grantable) AS (
              (SELECT * FROM expected EXCEPT SELECT * FROM actual)
              UNION ALL
              (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            )
            SELECT 1 FROM differences
          ) THEN
            RAISE EXCEPTION 'public schema ACL is not the exact role set';
          END IF;

          IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_constraint constraint_row
            WHERE constraint_row.conrelid = 'public.jobs'::regclass
              AND constraint_row.conname = 'ck_jobs_schedule_namespace'
              AND constraint_row.contype = 'c'
              AND constraint_row.convalidated
          ) THEN
            RAISE EXCEPTION 'scheduler namespace constraint is not validated';
          END IF;

          IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_trigger trigger_row
            WHERE trigger_row.tgrelid = 'public.job_events'::regclass
              AND trigger_row.tgname = 'trg_job_events_append_only'
              AND NOT trigger_row.tgisinternal
              AND trigger_row.tgenabled = 'O'
              AND trigger_row.tgtype = 27
              AND trigger_row.tgfoid =
                'public.reject_job_event_mutation()'::regprocedure
              AND trigger_row.tgqual IS NULL
              AND trigger_row.tgnargs = 0
              AND cardinality(trigger_row.tgattr::smallint[]) = 0
              AND trigger_row.tgconstraint = 0
          ) THEN
            RAISE EXCEPTION 'append-only event trigger changed during 0005';
          END IF;

          IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_trigger trigger_row
            WHERE trigger_row.tgrelid = 'public.jobs'::regclass
              AND trigger_row.tgname = 'trg_jobs_job_api_cancellation'
              AND NOT trigger_row.tgisinternal
              AND trigger_row.tgenabled = 'O'
              AND trigger_row.tgtype = 19
              AND trigger_row.tgfoid =
                'public.enforce_job_api_cancellation()'::regprocedure
              AND trigger_row.tgqual IS NULL
              AND trigger_row.tgnargs = 0
              AND cardinality(trigger_row.tgattr::smallint[]) = 0
              AND trigger_row.tgconstraint = 0
          ) THEN
            RAISE EXCEPTION 'job API cancellation trigger is absent or changed';
          END IF;

          IF (
            SELECT count(*)
            FROM pg_catalog.pg_trigger trigger_row
            WHERE NOT trigger_row.tgisinternal
              AND trigger_row.tgrelid IN (
                'public.jobs'::regclass,
                'public.job_attempts'::regclass,
                'public.job_events'::regclass,
                'public.scheduler_heartbeats'::regclass,
                'public.job_artifacts'::regclass,
                'public.worker_heartbeats'::regclass
              )
          ) <> 2 THEN
            RAISE EXCEPTION 'unexpected job-plane DML trigger exists';
          END IF;

          IF (
            SELECT count(*)
            FROM pg_catalog.pg_proc procedure_row
            JOIN pg_catalog.pg_language language_row
              ON language_row.oid = procedure_row.prolang
            WHERE procedure_row.oid IN (
              'public.reject_job_event_mutation()'::regprocedure,
              'public.enforce_job_api_cancellation()'::regprocedure
            )
              AND pg_get_userbyid(procedure_row.proowner) = 'trading_owner'
              AND language_row.lanname = 'plpgsql'
              AND NOT procedure_row.prosecdef
              AND NOT procedure_row.proleakproof
              AND NOT procedure_row.proisstrict
              AND procedure_row.provolatile = 'v'
              AND procedure_row.proparallel = 'u'
              AND procedure_row.proconfig IS NULL
              AND procedure_row.prorettype = 'trigger'::regtype
          ) <> 2 THEN
            RAISE EXCEPTION 'protected trigger-function identity is not exact';
          END IF;

          IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc procedure_row
            WHERE procedure_row.oid =
                    'public.reject_job_event_mutation()'::regprocedure
              AND btrim(regexp_replace(
                    procedure_row.prosrc, '[[:space:]]+', ' ', 'g'
                  )) =
                  'BEGIN RAISE EXCEPTION ''job_events is append-only'' '
                  'USING ERRCODE = ''55000''; END;'
          ) THEN
            RAISE EXCEPTION 'append-only function body changed during 0005';
          END IF;

          IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc procedure_row
            WHERE procedure_row.oid =
                    'public.enforce_job_api_cancellation()'::regprocedure
              AND btrim(regexp_replace(
                    procedure_row.prosrc, '[[:space:]]+', ' ', 'g'
                  )) =
                  'BEGIN IF current_user = ''trading_job_api'' THEN '
                  'IF OLD.job_type <> ''SNAPSHOT'' OR OLD.state NOT IN '
                  '(''QUEUED'', ''CLAIMED'', ''RUNNING'') OR NEW.state IS '
                  'DISTINCT FROM (CASE OLD.state WHEN ''QUEUED'' THEN '
                  '''CANCELLED'' ELSE ''CANCEL_REQUESTED'' END) OR '
                  'NEW.reason_code IS DISTINCT FROM ''CANCEL_REQUESTED'' OR '
                  'NEW.cancel_requested_at IS NULL OR '
                  'NEW.cancel_requested_at < OLD.requested_at OR '
                  'NEW.cancel_actor_type IS DISTINCT FROM ''OPERATOR'' OR '
                  'NEW.cancel_actor_id IS NULL OR '
                  'btrim(NEW.cancel_actor_id) = '''' OR '
                  'NEW.updated_at < OLD.updated_at THEN RAISE EXCEPTION '
                  '''job API cancellation transition rejected'' USING '
                  'ERRCODE = ''42501''; END IF; END IF; RETURN NEW; END;'
          ) THEN
            RAISE EXCEPTION 'job API cancellation function body is not exact';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM unnest(ARRAY[
              'trading_jobs',
              'trading_migrator',
              'trading_reader',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            ]) AS role_name(name)
            CROSS JOIN unnest(ARRAY[
              'public.reject_job_event_mutation()'::regprocedure,
              'public.enforce_job_api_cancellation()'::regprocedure
            ]) AS procedure_oid(oid)
            WHERE has_function_privilege(
              role_name.name, procedure_oid.oid, 'EXECUTE'
            )
          ) OR NOT has_function_privilege(
            'trading_owner',
            'public.reject_job_event_mutation()'::regprocedure,
            'EXECUTE'
          ) OR NOT has_function_privilege(
            'trading_owner',
            'public.enforce_job_api_cancellation()'::regprocedure,
            'EXECUTE'
          ) THEN
            RAISE EXCEPTION 'protected function EXECUTE ACL is not exact';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM unnest(ARRAY[
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            ]) AS role_name(name)
            CROSS JOIN unnest(ARRAY[
              'jobs',
              'job_attempts',
              'job_events',
              'scheduler_heartbeats',
              'job_artifacts',
              'worker_heartbeats'
            ]) AS table_name(name)
            WHERE has_schema_privilege(role_name.name, 'public', 'CREATE')
               OR EXISTS (
                    SELECT 1
                    FROM unnest(ARRAY[
                      'DELETE', 'TRUNCATE', 'TRIGGER'
                    ]) AS privilege_name(name)
                    WHERE has_table_privilege(
                      role_name.name,
                      format('public.%I', table_name.name),
                      privilege_name.name
                    )
                  )
          ) THEN
            RAISE EXCEPTION 'runtime role retains destructive or DDL authority';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_stat_activity
            WHERE usename IN (
              'trading_jobs',
              'trading_job_api',
              'trading_job_worker',
              'trading_job_scheduler'
            )
              AND pid <> pg_backend_pid()
          ) THEN
            RAISE EXCEPTION
              'job-plane session appeared during 0005 authority split';
          END IF;
        END
        $job_plane_postflight$;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0005_job_plane_role_split is intentionally non-reversible; "
        "preserve rows/events/artifacts, stop runtime identities, and use a "
        "reviewed forward repair instead of restoring trading_jobs LOGIN"
    )

\set ON_ERROR_STOP on

-- Cluster-admin pre-step for 0005_job_plane_role_split.
--
-- Run only through the separately approved B1 role-split runbook. Its
-- protected-stdin driver supplies each validated password twice to psql's
-- purpose-built \password command below. Cleartext is never interpolated into
-- SQL, placed in process argv, selected, echoed, or accepted as a tracked
-- literal. EOF or a mismatched confirmation makes the wrapper fail closed.

SELECT current_database() = 'trading_agent' AS exact_target_database
\gset
\if :exact_target_database
\else
  \warn 'job-role provisioning requires exact trading_agent database target'
  \quit 3
\endif

SELECT current_user = 'postgres' AND rolsuper AS exact_admin_executor
FROM pg_catalog.pg_roles
WHERE rolname = current_user
\gset
\if :exact_admin_executor
\else
  \warn 'job-role provisioning requires exact postgres superuser executor'
  \quit 3
\endif

SELECT current_setting('server_version_num')::integer / 10000 = 16
  AS exact_postgresql_major
\gset
\if :exact_postgresql_major
\else
  \warn 'job-role provisioning requires PostgreSQL 16'
  \quit 3
\endif

SELECT pg_get_userbyid(datdba) = 'trading_owner' AS exact_database_owner
FROM pg_catalog.pg_database
WHERE datname = current_database()
\gset
\if :exact_database_owner
\else
  \warn 'trading_agent must be owned by trading_owner'
  \quit 3
\endif

SELECT count(*) = 1
       AND min(version_num) = '0004_durable_research_jobs'
       AND max(version_num) = '0004_durable_research_jobs'
  AS exact_pre_migration_head
FROM public.alembic_version
\gset
\if :exact_pre_migration_head
\else
  \warn 'job-role provisioning requires exact 0004 head'
  \quit 3
\endif

SELECT current_setting('password_encryption') = 'scram-sha-256'
  AS scram_password_encryption_active
\gset
\if :scram_password_encryption_active
\else
  \warn 'password_encryption must be scram-sha-256'
  \quit 3
\endif

-- Password-bearing ALTER/CREATE ROLE statements must not enter PostgreSQL's
-- statement/error logging path. The approved wrapper supplies these session
-- settings before this file is parsed and separately attests cluster logging.
SELECT current_setting('log_statement') = 'none'
       AND current_setting('log_min_error_statement') = 'panic'
       AND current_setting('log_min_duration_statement')::integer = -1
       AND current_setting('log_min_duration_sample')::integer = -1
       AND current_setting('log_parameter_max_length_on_error')::integer = 0
       AND current_setting('log_duration') = 'off'
       AND current_setting('debug_print_parse') = 'off'
       AND current_setting('debug_print_rewritten') = 'off'
       AND current_setting('debug_print_plan') = 'off'
       AND current_setting('log_parser_stats') = 'off'
       AND current_setting('log_planner_stats') = 'off'
       AND current_setting('log_executor_stats') = 'off'
       AND current_setting('log_statement_stats') = 'off'
       AND current_setting('track_activities') = 'off'
       AND current_setting('shared_preload_libraries') = ''
       AND current_setting('session_preload_libraries') = ''
       AND current_setting('local_preload_libraries') = ''
  AS protected_logging_posture
\gset
\if :protected_logging_posture
\else
  \warn 'protected logging/preload posture is not exact'
  \quit 3
\endif

-- A pre-created runtime identity may already own objects, carry ACLs in an
-- uninspected database, or retain role-local settings. This first-use script
-- therefore refuses all three names instead of attempting to normalize them.
SELECT NOT EXISTS (
  SELECT 1
  FROM pg_catalog.pg_roles
  WHERE rolname IN (
    'trading_job_api',
    'trading_job_worker',
    'trading_job_scheduler'
  )
) AS runtime_role_names_absent
\gset
\if :runtime_role_names_absent
\else
  \warn 'runtime job role already exists; use a separately reviewed rotation procedure'
  \quit 3
\endif

SELECT EXISTS (
  SELECT 1
  FROM pg_catalog.pg_roles
  WHERE rolname = 'trading_jobs'
    AND rolcanlogin
    AND NOT rolsuper
    AND NOT rolcreatedb
    AND NOT rolcreaterole
    AND NOT rolinherit
    AND NOT rolreplication
    AND NOT rolbypassrls
) AS shared_role_exists
\gset
\if :shared_role_exists
\else
  \warn 'expected 0004 shared trading_jobs role is absent'
  \quit 3
\endif

SELECT count(*) = 7 AS exact_job_relation_owners
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
\gset
\if :exact_job_relation_owners
\else
  \warn '0004 job-plane relation ownership is not exact'
  \quit 3
\endif

SELECT count(*) = 1
       AND bool_and(pg_get_userbyid(procedure_row.proowner) = 'trading_owner')
       AND bool_and(language_row.lanname = 'plpgsql')
       AND bool_and(NOT procedure_row.prosecdef)
       AND bool_and(NOT procedure_row.proleakproof)
       AND bool_and(NOT procedure_row.proisstrict)
       AND bool_and(procedure_row.provolatile = 'v')
       AND bool_and(procedure_row.proparallel = 'u')
       AND bool_and(procedure_row.proconfig IS NULL)
       AND bool_and(
         btrim(regexp_replace(
           procedure_row.prosrc, '[[:space:]]+', ' ', 'g'
         )) =
         'BEGIN RAISE EXCEPTION ''job_events is append-only'' '
         'USING ERRCODE = ''55000''; END;'
       ) AS exact_append_only_function
FROM pg_catalog.pg_proc procedure_row
JOIN pg_catalog.pg_language language_row
  ON language_row.oid = procedure_row.prolang
WHERE procedure_row.oid =
  'public.reject_job_event_mutation()'::regprocedure
\gset
\if :exact_append_only_function
\else
  \warn '0004 append-only function definition is not exact'
  \quit 3
\endif

SELECT count(*) = 1 AS exact_append_only_trigger
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
\gset
\if :exact_append_only_trigger
\else
  \warn '0004 append-only trigger definition is not exact'
  \quit 3
\endif

SELECT count(*) = 1 AS exact_pre_0005_user_trigger_set
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
\gset
\if :exact_pre_0005_user_trigger_set
\else
  \warn 'unexpected pre-0005 job-plane trigger exists'
  \quit 3
\endif

-- A scheduler row incompatible with the future validated CHECK would make the
-- migration fail after cluster-global role retirement. Reject it before BEGIN.
SELECT NOT EXISTS (
  SELECT 1
  FROM public.jobs
  WHERE NOT (
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
  )
) AS schedule_namespace_is_compatible
\gset
\if :schedule_namespace_is_compatible
\else
  \warn 'existing job violates the future schedule namespace constraint'
  \quit 3
\endif

-- The old shared identity must not own any cluster-shared or database-local
-- object in any database. ACL dependencies are expected at 0004 and are
-- removed transactionally by 0005; ownership is never expected.
SELECT NOT EXISTS (
  SELECT 1
  FROM pg_catalog.pg_shdepend dependency
  JOIN pg_catalog.pg_roles role_row
    ON role_row.oid = dependency.refobjid
  WHERE role_row.rolname = 'trading_jobs'
    AND dependency.deptype = 'o'
) AS shared_role_ownership_absent
\gset
\if :shared_role_ownership_absent
\else
  \warn 'shared trading_jobs unexpectedly owns an object'
  \quit 3
\endif

BEGIN;

-- Role rotation while any old or new job-plane identity is connected would
-- leave mixed authority alive. The operator must drain it under a different,
-- explicitly approved procedure; this script never terminates sessions.
SELECT NOT EXISTS (
  SELECT 1
  FROM pg_catalog.pg_stat_activity
  WHERE usename IN (
    'trading_jobs',
    'trading_job_api',
    'trading_job_worker',
    'trading_job_scheduler'
  )
  AND pid <> pg_backend_pid()
) AS job_role_sessions_absent
\gset
\if :job_role_sessions_absent
\else
  \warn 'job-plane role session exists; stop without altering roles'
  \quit 4
\endif

-- Unexpected memberships are an incident, not something to silently repair.
-- No target role may inherit another role and no other role may inherit a
-- target role.
SELECT NOT EXISTS (
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
) AS job_role_memberships_absent
\gset
\if :job_role_memberships_absent
\else
  \warn 'unexpected job-plane role membership exists; stop for review'
  \quit 5
\endif

ALTER ROLE trading_jobs NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD NULL;
ALTER ROLE trading_jobs RESET ALL;
DO $reset_shared_role_database_settings$
DECLARE
  database_name text;
BEGIN
  FOR database_name IN
    SELECT datname FROM pg_catalog.pg_database
  LOOP
    EXECUTE format(
      'ALTER ROLE trading_jobs IN DATABASE %I RESET ALL',
      database_name
    );
  END LOOP;
END
$reset_shared_role_database_settings$;

CREATE ROLE trading_job_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD NULL;
\password trading_job_api
ALTER ROLE trading_job_api RESET ALL;
ALTER ROLE trading_job_api SET timezone = 'UTC';

CREATE ROLE trading_job_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD NULL;
\password trading_job_worker
ALTER ROLE trading_job_worker RESET ALL;
ALTER ROLE trading_job_worker SET timezone = 'UTC';

CREATE ROLE trading_job_scheduler LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD NULL;
\password trading_job_scheduler
ALTER ROLE trading_job_scheduler RESET ALL;
ALTER ROLE trading_job_scheduler SET timezone = 'UTC';

SELECT NOT EXISTS (
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
) AS job_role_memberships_still_absent
\gset

SELECT NOT EXISTS (
  SELECT 1
  FROM pg_catalog.pg_stat_activity
  WHERE usename IN (
    'trading_jobs',
    'trading_job_api',
    'trading_job_worker',
    'trading_job_scheduler'
  )
  AND pid <> pg_backend_pid()
) AS job_role_sessions_still_absent
\gset

SELECT NOT EXISTS (
  SELECT 1
  FROM pg_catalog.pg_db_role_setting role_setting
  JOIN pg_catalog.pg_roles role_row
    ON role_row.oid = role_setting.setrole
  JOIN pg_catalog.pg_database database_row
    ON database_row.oid = role_setting.setdatabase
  WHERE role_row.rolname IN (
    'trading_jobs',
    'trading_job_api',
    'trading_job_worker',
    'trading_job_scheduler'
  )
) AS all_database_role_settings_absent
\gset

-- Fail the transaction transcript if any postcondition is false. This query
-- emits one non-secret boolean only when invoked interactively; \gset keeps it
-- out of normal evidence output.
SELECT
  count(*) FILTER (
    WHERE rolname = 'trading_jobs'
      AND NOT rolcanlogin
      AND NOT rolsuper
      AND NOT rolcreatedb
      AND NOT rolcreaterole
      AND NOT rolinherit
      AND NOT rolreplication
      AND NOT rolbypassrls
      AND rolconfig IS NULL
  ) = 1
  AND count(*) FILTER (
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
      AND cardinality(rolconfig) = 1
      AND EXISTS (
        SELECT 1
        FROM unnest(rolconfig) setting(value)
        WHERE lower(split_part(setting.value, '=', 1)) = 'timezone'
          AND split_part(setting.value, '=', 2) = 'UTC'
      )
  ) = 3
  AS job_role_attributes_valid
FROM pg_catalog.pg_roles
WHERE rolname IN (
  'trading_jobs',
  'trading_job_api',
  'trading_job_worker',
  'trading_job_scheduler'
)
\gset
\if :job_role_attributes_valid
\else
  \warn 'job-plane role postcondition failed'
  \quit 6
\endif
\if :job_role_memberships_still_absent
\else
  \warn 'job-plane membership appeared during provisioning; transaction rolls back'
  \quit 7
\endif
\if :job_role_sessions_still_absent
\else
  \warn 'job-plane session appeared during provisioning; transaction rolls back'
  \quit 8
\endif
\if :all_database_role_settings_absent
\else
  \warn 'database-local job-role settings survived reset; transaction rolls back'
  \quit 9
\endif

COMMIT;

\unset job_role_sessions_absent
\unset job_role_memberships_absent
\unset job_role_memberships_still_absent
\unset job_role_sessions_still_absent
\unset all_database_role_settings_absent
\unset shared_role_exists
\unset exact_job_relation_owners
\unset exact_append_only_function
\unset exact_append_only_trigger
\unset exact_pre_0005_user_trigger_set
\unset runtime_role_names_absent
\unset exact_target_database
\unset exact_admin_executor
\unset exact_postgresql_major
\unset exact_database_owner
\unset exact_pre_migration_head
\unset scram_password_encryption_active
\unset protected_logging_posture
\unset schedule_namespace_is_compatible
\unset shared_role_ownership_absent
\unset job_role_attributes_valid

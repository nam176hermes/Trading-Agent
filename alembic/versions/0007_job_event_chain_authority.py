"""Freeze the reviewed job-event-chain authority as a forward-only repair.

Revision ID: 0007_job_event_chain_authority
Revises: 0006_job_transition_database_authority

The only catalog mutation in this migration is the reviewed global default
function-EXECUTE revoke. All other statements are preflight or postflight
checks against frozen Task 5 authority inputs.
"""
from __future__ import annotations

import hashlib

from alembic import op


revision = "0007_job_event_chain_authority"
down_revision = "0006_job_transition_database_authority"
branch_labels = None
depends_on = None


CATALOG_QUERY_ID = "job-plane-catalog-v1"
CATALOG_SNAPSHOT_SQL = """WITH named_roles(role_name) AS (
  VALUES
    ('trading_owner'),
    ('trading_migrator'),
    ('trading_reader'),
    ('trading_jobs'),
    ('trading_job_api'),
    ('trading_job_worker'),
    ('trading_job_scheduler')
),
application_namespaces AS (
  SELECT namespace_row.oid, namespace_row.nspname
  FROM pg_catalog.pg_namespace namespace_row
  WHERE namespace_row.nspname <> 'information_schema'
    AND namespace_row.nspname !~ '^pg_'
),
role_setting_entries AS (
  SELECT role_row.rolname AS role_name,
         CASE
           WHEN setting_row.setdatabase = 0 THEN NULL
           ELSE database_row.datname
         END AS database_name,
         pg_catalog.lower(pg_catalog.split_part(setting_entry.entry, '=', 1)) AS setting_key,
         CASE
           WHEN pg_catalog.lower(pg_catalog.split_part(setting_entry.entry, '=', 1)) IN (
             'default_transaction_read_only', 'search_path', 'timezone'
           ) THEN pg_catalog.substr(
             setting_entry.entry,
             pg_catalog.length(
               pg_catalog.split_part(setting_entry.entry, '=', 1)
             ) + 2
           )
           ELSE NULL
         END AS safe_value
  FROM pg_catalog.pg_db_role_setting setting_row
  JOIN pg_catalog.pg_roles role_row
    ON role_row.oid = setting_row.setrole
  LEFT JOIN pg_catalog.pg_database database_row
    ON database_row.oid = setting_row.setdatabase
  CROSS JOIN LATERAL pg_catalog.unnest(setting_row.setconfig)
    AS setting_entry(entry)
  WHERE role_row.rolname IN (SELECT role_name FROM named_roles)
    AND (
      setting_row.setdatabase = 0
      OR database_row.datname = pg_catalog.current_database()
    )
),
function_unsafe_settings AS (
  SELECT procedure_row.oid AS procedure_oid,
         pg_catalog.split_part(setting_entry.entry, '=', 1) AS setting_key
  FROM pg_catalog.pg_proc procedure_row
  JOIN application_namespaces namespace_row
    ON namespace_row.oid = procedure_row.pronamespace
  CROSS JOIN LATERAL pg_catalog.unnest(
    coalesce(procedure_row.proconfig, ARRAY[]::text[])
  ) AS setting_entry(entry)
  WHERE pg_catalog.split_part(setting_entry.entry, '=', 1) <> 'search_path'
),
records(record_type, unsafe_key, canonical_line) AS (
  SELECT 'DATABASE', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'database',
           'name', database_row.datname,
           'owner', pg_catalog.pg_get_userbyid(database_row.datdba),
           'encoding', pg_catalog.pg_encoding_to_char(database_row.encoding),
           'collation', database_row.datcollate,
           'character_type', database_row.datctype,
           'locale_provider', database_row.datlocprovider,
           'is_template', database_row.datistemplate,
           'allow_connections', database_row.datallowconn,
           'connection_limit', database_row.datconnlimit,
           'acl', CASE
             WHEN database_row.datacl IS NULL THEN NULL
             ELSE pg_catalog.to_jsonb(ARRAY(
               SELECT acl_entry.item::text
               FROM pg_catalog.unnest(database_row.datacl) AS acl_entry(item)
               ORDER BY acl_entry.item::text COLLATE "C"
             ))
           END
         )::text
  FROM pg_catalog.pg_database database_row
  WHERE database_row.datname = pg_catalog.current_database()

  UNION ALL
  SELECT 'SCHEMA', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'schema',
           'name', namespace_row.nspname,
           'owner', pg_catalog.pg_get_userbyid(namespace_row.nspowner),
           'acl', CASE
             WHEN namespace_row.nspacl IS NULL THEN NULL
             ELSE pg_catalog.to_jsonb(ARRAY(
               SELECT acl_entry.item::text
               FROM pg_catalog.unnest(namespace_row.nspacl) AS acl_entry(item)
               ORDER BY acl_entry.item::text COLLATE "C"
             ))
           END
         )::text
  FROM pg_catalog.pg_namespace namespace_row
  JOIN application_namespaces application_namespace
    ON application_namespace.oid = namespace_row.oid

  UNION ALL
  SELECT 'OBJECT', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'object',
           'schema', namespace_row.nspname,
           'name', relation_row.relname,
           'relation_kind', relation_row.relkind,
           'persistence', relation_row.relpersistence,
           'owner', pg_catalog.pg_get_userbyid(relation_row.relowner),
           'row_security', relation_row.relrowsecurity,
           'force_row_security', relation_row.relforcerowsecurity,
           'replica_identity', relation_row.relreplident,
           'acl', CASE
             WHEN relation_row.relacl IS NULL THEN NULL
             ELSE pg_catalog.to_jsonb(ARRAY(
               SELECT acl_entry.item::text
               FROM pg_catalog.unnest(relation_row.relacl) AS acl_entry(item)
               ORDER BY acl_entry.item::text COLLATE "C"
             ))
           END
         )::text
  FROM pg_catalog.pg_class relation_row
  JOIN application_namespaces namespace_row
    ON namespace_row.oid = relation_row.relnamespace
  WHERE relation_row.relkind IN ('r', 'p', 'v', 'm', 'S', 'f', 'i', 'I')

  UNION ALL
  SELECT 'COLUMN', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'column',
           'schema', namespace_row.nspname,
           'relation', relation_row.relname,
           'position', attribute_row.attnum,
           'name', attribute_row.attname,
           'type', pg_catalog.format_type(
             attribute_row.atttypid, attribute_row.atttypmod
           ),
           'not_null', attribute_row.attnotnull,
           'identity', attribute_row.attidentity,
           'generated', attribute_row.attgenerated,
           'compression', attribute_row.attcompression,
           'storage', attribute_row.attstorage,
           'default', pg_catalog.pg_get_expr(
             default_row.adbin, default_row.adrelid, true
           ),
           'collation', CASE
             WHEN attribute_row.attcollation = 0 THEN NULL
             ELSE pg_catalog.jsonb_build_object(
               'schema', collation_namespace.nspname,
               'name', collation_row.collname
             )
           END,
           'acl', CASE
             WHEN attribute_row.attacl IS NULL THEN NULL
             ELSE pg_catalog.to_jsonb(ARRAY(
               SELECT acl_entry.item::text
               FROM pg_catalog.unnest(attribute_row.attacl) AS acl_entry(item)
               ORDER BY acl_entry.item::text COLLATE "C"
             ))
           END
         )::text
  FROM pg_catalog.pg_attribute attribute_row
  JOIN pg_catalog.pg_class relation_row
    ON relation_row.oid = attribute_row.attrelid
  JOIN application_namespaces namespace_row
    ON namespace_row.oid = relation_row.relnamespace
  LEFT JOIN pg_catalog.pg_attrdef default_row
    ON default_row.adrelid = attribute_row.attrelid
   AND default_row.adnum = attribute_row.attnum
  LEFT JOIN pg_catalog.pg_collation collation_row
    ON collation_row.oid = attribute_row.attcollation
  LEFT JOIN pg_catalog.pg_namespace collation_namespace
    ON collation_namespace.oid = collation_row.collnamespace
  WHERE relation_row.relkind IN ('r', 'p', 'v', 'm', 'f')
    AND attribute_row.attnum > 0
    AND NOT attribute_row.attisdropped

  UNION ALL
  SELECT 'CONSTRAINT', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'constraint',
           'schema', namespace_row.nspname,
           'relation', relation_row.relname,
           'name', constraint_row.conname,
           'constraint_kind', constraint_row.contype,
           'definition', pg_catalog.pg_get_constraintdef(
             constraint_row.oid, true
           ),
           'deferrable', constraint_row.condeferrable,
           'initially_deferred', constraint_row.condeferred,
           'validated', constraint_row.convalidated
         )::text
  FROM pg_catalog.pg_constraint constraint_row
  JOIN pg_catalog.pg_class relation_row
    ON relation_row.oid = constraint_row.conrelid
  JOIN application_namespaces namespace_row
    ON namespace_row.oid = relation_row.relnamespace

  UNION ALL
  SELECT 'INDEX', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'index',
           'schema', namespace_row.nspname,
           'relation', relation_row.relname,
           'name', index_relation.relname,
           'definition', pg_catalog.pg_get_indexdef(index_row.indexrelid),
           'unique', index_row.indisunique,
           'primary', index_row.indisprimary,
           'exclusion', index_row.indisexclusion,
           'immediate', index_row.indimmediate,
           'clustered', index_row.indisclustered,
           'replica_identity', index_row.indisreplident,
           'valid', index_row.indisvalid,
           'ready', index_row.indisready,
           'live', index_row.indislive
         )::text
  FROM pg_catalog.pg_index index_row
  JOIN pg_catalog.pg_class relation_row
    ON relation_row.oid = index_row.indrelid
  JOIN pg_catalog.pg_class index_relation
    ON index_relation.oid = index_row.indexrelid
  JOIN application_namespaces namespace_row
    ON namespace_row.oid = relation_row.relnamespace

  UNION ALL
  SELECT 'SEQUENCE', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'sequence',
           'schema', namespace_row.nspname,
           'name', relation_row.relname,
           'data_type', pg_catalog.format_type(
             sequence_row.seqtypid, NULL
           ),
           'start', sequence_row.seqstart,
           'increment', sequence_row.seqincrement,
           'minimum', sequence_row.seqmin,
           'maximum', sequence_row.seqmax,
           'cache', sequence_row.seqcache,
           'cycle', sequence_row.seqcycle
         )::text
  FROM pg_catalog.pg_sequence sequence_row
  JOIN pg_catalog.pg_class relation_row
    ON relation_row.oid = sequence_row.seqrelid
  JOIN application_namespaces namespace_row
    ON namespace_row.oid = relation_row.relnamespace

  UNION ALL
  SELECT 'FUNCTION', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'function',
           'schema', namespace_row.nspname,
           'name', procedure_row.proname,
           'owner', pg_catalog.pg_get_userbyid(procedure_row.proowner),
           'identity_arguments',
             pg_catalog.pg_get_function_identity_arguments(procedure_row.oid),
           'result', pg_catalog.pg_get_function_result(procedure_row.oid),
           'definition', pg_catalog.pg_get_functiondef(procedure_row.oid),
           'language', language_row.lanname,
           'function_kind', procedure_row.prokind,
           'volatility', procedure_row.provolatile,
           'parallel', procedure_row.proparallel,
           'strict', procedure_row.proisstrict,
           'security_definer', procedure_row.prosecdef,
           'leakproof', procedure_row.proleakproof,
           'acl', CASE
             WHEN procedure_row.proacl IS NULL THEN NULL
             ELSE pg_catalog.to_jsonb(ARRAY(
               SELECT acl_entry.item::text
               FROM pg_catalog.unnest(procedure_row.proacl) AS acl_entry(item)
               ORDER BY acl_entry.item::text COLLATE "C"
             ))
           END
         )::text
  FROM pg_catalog.pg_proc procedure_row
  JOIN application_namespaces namespace_row
    ON namespace_row.oid = procedure_row.pronamespace
  JOIN pg_catalog.pg_language language_row
    ON language_row.oid = procedure_row.prolang
  WHERE NOT EXISTS (
    SELECT 1
    FROM function_unsafe_settings unsafe_setting
    WHERE unsafe_setting.procedure_oid = procedure_row.oid
  )

  UNION ALL
  SELECT 'UNSAFE_FUNCTION_SETTING', unsafe_setting.setting_key, NULL::text
  FROM function_unsafe_settings unsafe_setting

  UNION ALL
  SELECT 'TRIGGER', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'trigger',
           'schema', namespace_row.nspname,
           'relation', relation_row.relname,
           'name', trigger_row.tgname,
           'enabled', trigger_row.tgenabled,
           'trigger_kind', trigger_row.tgtype,
           'definition', pg_catalog.pg_get_triggerdef(trigger_row.oid, true),
           'function_schema', function_namespace.nspname,
           'function_name', procedure_row.proname,
           'function_identity_arguments',
             pg_catalog.pg_get_function_identity_arguments(procedure_row.oid)
         )::text
  FROM pg_catalog.pg_trigger trigger_row
  JOIN pg_catalog.pg_class relation_row
    ON relation_row.oid = trigger_row.tgrelid
  JOIN application_namespaces namespace_row
    ON namespace_row.oid = relation_row.relnamespace
  JOIN pg_catalog.pg_proc procedure_row
    ON procedure_row.oid = trigger_row.tgfoid
  JOIN pg_catalog.pg_namespace function_namespace
    ON function_namespace.oid = procedure_row.pronamespace
  WHERE NOT trigger_row.tgisinternal

  UNION ALL
  SELECT 'POLICY', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'policy',
           'schema', namespace_row.nspname,
           'relation', relation_row.relname,
           'name', policy_row.polname,
           'command', policy_row.polcmd,
           'permissive', policy_row.polpermissive,
           'roles', pg_catalog.to_jsonb(ARRAY(
             SELECT CASE
                      WHEN role_id = 0 THEN 'PUBLIC'
                      ELSE pg_catalog.pg_get_userbyid(role_id)
                    END
             FROM pg_catalog.unnest(policy_row.polroles) AS role_entry(role_id)
             ORDER BY (CASE WHEN role_id = 0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(role_id) END) COLLATE "C"
           )),
           'using', pg_catalog.pg_get_expr(
             policy_row.polqual, policy_row.polrelid, true
           ),
           'with_check', pg_catalog.pg_get_expr(
             policy_row.polwithcheck, policy_row.polrelid, true
           )
         )::text
  FROM pg_catalog.pg_policy policy_row
  JOIN pg_catalog.pg_class relation_row
    ON relation_row.oid = policy_row.polrelid
  JOIN application_namespaces namespace_row
    ON namespace_row.oid = relation_row.relnamespace

  UNION ALL
  SELECT 'DEFAULT_ACL', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'default_acl',
           'owner', pg_catalog.pg_get_userbyid(default_acl.defaclrole),
           'schema', CASE
             WHEN default_acl.defaclnamespace = 0 THEN NULL
             ELSE namespace_row.nspname
           END,
           'object_kind', default_acl.defaclobjtype,
           'acl', CASE
             WHEN default_acl.defaclacl IS NULL THEN NULL
             ELSE pg_catalog.to_jsonb(ARRAY(
               SELECT acl_entry.item::text
               FROM pg_catalog.unnest(default_acl.defaclacl) AS acl_entry(item)
               ORDER BY acl_entry.item::text COLLATE "C"
             ))
           END
         )::text
  FROM pg_catalog.pg_default_acl default_acl
  LEFT JOIN pg_catalog.pg_namespace namespace_row
    ON namespace_row.oid = default_acl.defaclnamespace
  WHERE pg_catalog.pg_get_userbyid(default_acl.defaclrole) = 'trading_owner'

  UNION ALL
  SELECT 'ROLE', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'role',
           'name', role_row.rolname,
           'login', role_row.rolcanlogin,
           'superuser', role_row.rolsuper,
           'create_database', role_row.rolcreatedb,
           'create_role', role_row.rolcreaterole,
           'inherit', role_row.rolinherit,
           'replication', role_row.rolreplication,
           'bypass_rls', role_row.rolbypassrls,
           'connection_limit', role_row.rolconnlimit,
           'valid_until_is_null', role_row.rolvaliduntil IS NULL
         )::text
  FROM pg_catalog.pg_roles role_row
  WHERE role_row.rolname IN (SELECT role_name FROM named_roles)

  UNION ALL
  SELECT 'MEMBERSHIP', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'membership',
           'granted_role', granted_role.rolname,
           'member_role', member_role.rolname,
           'grantor', grantor_role.rolname,
           'admin_option', membership.admin_option,
           'inherit_option', membership.inherit_option,
           'set_option', membership.set_option
         )::text
  FROM pg_catalog.pg_auth_members membership
  JOIN pg_catalog.pg_roles granted_role
    ON granted_role.oid = membership.roleid
  JOIN pg_catalog.pg_roles member_role
    ON member_role.oid = membership.member
  JOIN pg_catalog.pg_roles grantor_role
    ON grantor_role.oid = membership.grantor
  WHERE granted_role.rolname IN (SELECT role_name FROM named_roles)
     OR member_role.rolname IN (SELECT role_name FROM named_roles)

  UNION ALL
  SELECT 'ROLE_SETTING', NULL::text,
         pg_catalog.jsonb_build_object(
           'kind', 'role_setting',
           'role', setting_entry.role_name,
           'database', setting_entry.database_name,
           'key', setting_entry.setting_key,
           'value', setting_entry.safe_value
         )::text
  FROM role_setting_entries setting_entry
  WHERE setting_entry.setting_key IN (
    'default_transaction_read_only', 'search_path', 'timezone'
  )

  UNION ALL
  SELECT 'UNSAFE_ROLE_SETTING', setting_entry.setting_key, NULL::text
  FROM role_setting_entries setting_entry
  WHERE setting_entry.setting_key NOT IN (
    'default_transaction_read_only', 'search_path', 'timezone'
  )
)
SELECT record_type, unsafe_key, canonical_line
FROM records
ORDER BY canonical_line COLLATE "C" NULLS FIRST,
         record_type COLLATE "C",
         unsafe_key COLLATE "C" NULLS FIRST"""
EVENT_CHAIN_QUERY_ID = "job-plane-event-chain-v1"
EVENT_CHAIN_VIOLATIONS_SQL = """WITH ordered_events AS (
  SELECT event_row.*,
         pg_catalog.row_number() OVER (
           PARTITION BY event_row.job_id
           ORDER BY event_row.sequence, event_row.event_id COLLATE "C"
         ) AS event_number,
         pg_catalog.count(*) OVER (
           PARTITION BY event_row.job_id, event_row.sequence
         ) AS sequence_count,
         pg_catalog.lag(event_row.sequence) OVER (
           PARTITION BY event_row.job_id
           ORDER BY event_row.sequence, event_row.event_id COLLATE "C"
         ) AS previous_sequence,
         pg_catalog.lag(event_row.to_state) OVER (
           PARTITION BY event_row.job_id
           ORDER BY event_row.sequence, event_row.event_id COLLATE "C"
         ) AS previous_to_state,
         pg_catalog.lag(event_row.attempt_id) OVER (
           PARTITION BY event_row.job_id
           ORDER BY event_row.sequence, event_row.event_id COLLATE "C"
         ) AS previous_attempt_id,
         pg_catalog.row_number() OVER (
           PARTITION BY event_row.job_id
           ORDER BY event_row.sequence DESC, event_row.event_id COLLATE "C" DESC
         ) AS reverse_event_number
  FROM public.job_events event_row
),
event_context AS (
  SELECT ordered_event.*,
         job_row.max_attempts,
         attempt_row.attempt_number,
         (
           (ordered_event.from_state, ordered_event.to_state) IN (
             ('FAILED', 'QUEUED'), ('TIMED_OUT', 'QUEUED')
           )
         ) AS is_retry,
         (
           ordered_event.attempt_id IS NOT NULL
           AND attempt_row.attempt_id IS NULL
         ) AS attempt_is_forged
  FROM ordered_events ordered_event
  JOIN public.jobs job_row ON job_row.job_id = ordered_event.job_id
  LEFT JOIN public.job_attempts attempt_row
    ON attempt_row.job_id = ordered_event.job_id
   AND attempt_row.attempt_id = ordered_event.attempt_id
),
retry_classification AS (
  SELECT event_context.*,
         (
           event_context.is_retry
           AND (
             (
               event_context.reason_code = 'PROCESS_RETRY_SCHEDULED'
               AND event_context.actor_type = 'WORKER'
             )
             OR (
               event_context.reason_code =
                     'LEASE_EXPIRED_RETRY_SCHEDULED'
               AND event_context.actor_type = 'RECOVERY'
             )
           )
           AND event_context.metadata = '{}'::jsonb
           AND event_context.previous_to_state IS NOT DISTINCT FROM
                 event_context.from_state
           AND (event_context.sequence::numeric -
                 event_context.previous_sequence::numeric) = 1
           AND event_context.attempt_id IS NOT NULL
           AND event_context.previous_attempt_id IS NOT NULL
           AND event_context.attempt_id = event_context.previous_attempt_id
           AND NOT event_context.attempt_is_forged
           AND event_context.attempt_number < event_context.max_attempts
         ) AS is_valid_retry
  FROM event_context
),
epoch_events AS (
  SELECT retry_classification.*,
         pg_catalog.sum(
           CASE WHEN retry_classification.is_valid_retry THEN 1 ELSE 0 END
         ) OVER (
           PARTITION BY retry_classification.job_id
           ORDER BY retry_classification.sequence,
                    retry_classification.event_id COLLATE "C"
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
         ) AS retry_epoch
  FROM retry_classification
),
classified_events AS (
  SELECT epoch_event.*,
         pg_catalog.count(*) FILTER (
           WHERE epoch_event.to_state IN (
             'SUCCEEDED', 'FAILED', 'BLOCKED', 'TIMED_OUT', 'CANCELLED'
           )
         ) OVER (
           PARTITION BY epoch_event.job_id, epoch_event.retry_epoch
         ) AS terminal_count_in_epoch
  FROM epoch_events epoch_event
),
violations(code, job_id, event_id, sequence) AS (
  SELECT 'NO_HISTORY', job_row.job_id, NULL::text, NULL::bigint
  FROM public.jobs job_row
  WHERE NOT EXISTS (
    SELECT 1 FROM public.job_events event_row
    WHERE event_row.job_id = job_row.job_id
  )

  UNION ALL
  SELECT 'SEQUENCE_START', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.event_number = 1 AND event_row.sequence <> 1

  UNION ALL
  SELECT 'SEQUENCE_GAP', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.event_number > 1
    AND (event_row.sequence::numeric -
         event_row.previous_sequence::numeric) > 1

  UNION ALL
  SELECT 'SEQUENCE_DUPLICATE', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.sequence_count > 1

  UNION ALL
  SELECT 'BOOTSTRAP_EDGE', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.event_number = 1
    AND (
      event_row.from_state IS NOT NULL
      OR event_row.to_state IS DISTINCT FROM 'QUEUED'
    )

  UNION ALL
  SELECT 'BOOTSTRAP_ATTEMPT', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.event_number = 1 AND event_row.attempt_id IS NOT NULL

  UNION ALL
  SELECT 'LATER_NULL_FROM_STATE', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.event_number > 1 AND event_row.from_state IS NULL

  UNION ALL
  SELECT 'DISCONNECTED_FROM_STATE', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.event_number > 1
    AND event_row.from_state IS DISTINCT FROM event_row.previous_to_state

  UNION ALL
  SELECT 'UNAPPROVED_EDGE', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.event_number > 1
    AND (event_row.from_state, event_row.to_state) NOT IN (
      ('QUEUED', 'CLAIMED'),
      ('QUEUED', 'CANCELLED'),
      ('CLAIMED', 'RUNNING'),
      ('CLAIMED', 'CANCEL_REQUESTED'),
      ('CLAIMED', 'BLOCKED'),
      ('RUNNING', 'SUCCEEDED'),
      ('RUNNING', 'FAILED'),
      ('RUNNING', 'TIMED_OUT'),
      ('RUNNING', 'CANCEL_REQUESTED'),
      ('RUNNING', 'BLOCKED'),
      ('CANCEL_REQUESTED', 'CANCELLED'),
      ('CANCEL_REQUESTED', 'BLOCKED'),
      ('FAILED', 'QUEUED'),
      ('TIMED_OUT', 'QUEUED')
    )

  UNION ALL
  SELECT 'FINAL_STATE_MISMATCH', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  JOIN public.jobs job_row ON job_row.job_id = event_row.job_id
  WHERE event_row.reverse_event_number = 1
    AND event_row.to_state IS DISTINCT FROM job_row.state

  UNION ALL
  SELECT 'CROSS_JOB_ATTEMPT', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.attempt_is_forged

  UNION ALL
  SELECT 'RETRY_WRONG_ACTOR', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.is_retry
    AND (
      (
        event_row.reason_code = 'PROCESS_RETRY_SCHEDULED'
        AND event_row.actor_type IS DISTINCT FROM 'WORKER'
      )
      OR (
        event_row.reason_code = 'LEASE_EXPIRED_RETRY_SCHEDULED'
        AND event_row.actor_type IS DISTINCT FROM 'RECOVERY'
      )
      OR event_row.actor_type NOT IN ('WORKER', 'RECOVERY')
    )

  UNION ALL
  SELECT 'RETRY_WRONG_REASON', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.is_retry
    AND NOT (
      (
        event_row.reason_code = 'PROCESS_RETRY_SCHEDULED'
        AND event_row.actor_type = 'WORKER'
      )
      OR (
        event_row.reason_code = 'LEASE_EXPIRED_RETRY_SCHEDULED'
        AND event_row.actor_type = 'RECOVERY'
      )
    )

  UNION ALL
  SELECT 'RETRY_METADATA', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.is_retry AND event_row.metadata IS DISTINCT FROM '{}'::jsonb

  UNION ALL
  SELECT 'RETRY_ATTEMPT_CHANGED', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.is_retry
    AND (
      event_row.attempt_id IS NULL
      OR event_row.previous_attempt_id IS NULL
      OR event_row.attempt_id IS DISTINCT FROM event_row.previous_attempt_id
    )

  UNION ALL
  SELECT 'RETRY_FORGED_ATTEMPT', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.is_retry AND event_row.attempt_is_forged

  UNION ALL
  SELECT 'RETRY_OVER_BUDGET', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.is_retry
    AND event_row.attempt_number >= event_row.max_attempts

  UNION ALL
  SELECT 'RETRY_NOT_ADJACENT', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.is_retry
    AND (
      event_row.previous_to_state IS DISTINCT FROM event_row.from_state
      OR (event_row.sequence::numeric -
           event_row.previous_sequence::numeric) IS DISTINCT FROM 1
    )

  UNION ALL
  SELECT 'EVENT_AFTER_TERMINAL', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.previous_to_state IN (
          'SUCCEEDED', 'FAILED', 'BLOCKED', 'TIMED_OUT', 'CANCELLED'
        )
    AND NOT event_row.is_valid_retry

  UNION ALL
  SELECT 'DUPLICATE_TERMINAL_IN_EPOCH', event_row.job_id, event_row.event_id,
         event_row.sequence
  FROM classified_events event_row
  WHERE event_row.to_state IN (
          'SUCCEEDED', 'FAILED', 'BLOCKED', 'TIMED_OUT', 'CANCELLED'
        )
    AND event_row.terminal_count_in_epoch > 1
)
SELECT code, job_id, event_id, sequence
FROM violations
ORDER BY code COLLATE "C", job_id COLLATE "C",
         sequence NULLS FIRST, event_id COLLATE "C" NULLS FIRST"""
ACL_REPAIR_SQL = """ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
"""
REVIEWED_0006_CATALOG_SHA256 = "b2dd91dbb12d585579e69b81394a530128fe84bc1dd2c7ef7683c9353eb1e4d1"
REVIEWED_0007_CATALOG_SHA256 = "1d83e9bc3f5cffe9e2dded41c33f46ce0b6d4395df84d3081d0b5132db487a40"
PRODUCTION_DATABASE_NAME = "trading_agent"
DISPOSABLE_DATABASE_NAME = "trading_agent_disposable_test"
REVIEWED_DISPOSABLE_0006_CATALOG_SHA256 = (
    "69b6886986b9a6e9d6ab824663b4acc2259a41f0e76b1217f15364e13302911f"
)
REVIEWED_DISPOSABLE_0007_CATALOG_SHA256 = (
    "606963c54fbe6cedd25fc166c7b13bcd3a97effa9c83fde2de7c2a567b9359f6"
)


RUNTIME_ROLES = (
    "trading_job_api",
    "trading_job_worker",
    "trading_job_scheduler",
)


def _catalog_digest() -> str:
    """Hash the frozen C-sorted catalog serialization without exposing rows."""

    rows = op.get_bind().exec_driver_sql(CATALOG_SNAPSHOT_SQL).fetchall()
    lines: list[bytes] = []
    for row in rows:
        if len(row) != 3:
            raise RuntimeError("0007 catalog query returned malformed rows")
        record_type, unsafe_key, canonical_line = row
        if record_type in {"UNSAFE_FUNCTION_SETTING", "UNSAFE_ROLE_SETTING"}:
            if not isinstance(unsafe_key, str) or canonical_line is not None:
                raise RuntimeError("0007 catalog query returned malformed rows")
            raise RuntimeError("0007 catalog contains an unreviewed setting key")
        if (
            not isinstance(record_type, str)
            or not record_type
            or unsafe_key is not None
            or not isinstance(canonical_line, str)
            or not canonical_line
            or "\n" in canonical_line
            or "\r" in canonical_line
        ):
            raise RuntimeError("0007 catalog query returned malformed rows")
        try:
            lines.append(canonical_line.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise RuntimeError("0007 catalog query returned malformed rows") from None
    lines.sort()
    canonical_bytes = b"\n".join(lines)
    if lines:
        canonical_bytes += b"\n"
    return hashlib.sha256(canonical_bytes).hexdigest()


def _catalog_database_name() -> str:
    rows = op.get_bind().exec_driver_sql(
        "SELECT pg_catalog.current_database()"
    ).fetchall()
    if (
        len(rows) != 1
        or len(rows[0]) != 1
        or not isinstance(rows[0][0], str)
        or not rows[0][0]
    ):
        raise RuntimeError("0007 database identity is unavailable")
    return rows[0][0]


def _require_catalog_digest(
    production_expected: str,
    disposable_expected: str,
    stage: str,
) -> None:
    expected = {
        PRODUCTION_DATABASE_NAME: production_expected,
        DISPOSABLE_DATABASE_NAME: disposable_expected,
    }.get(_catalog_database_name())
    if expected is None:
        raise RuntimeError("0007 database identity does not match review")
    if _catalog_digest() != expected:
        raise RuntimeError(f"0007 {stage} catalog digest does not match review")


def _require_zero_event_chain_violations() -> None:
    violations = op.get_bind().exec_driver_sql(EVENT_CHAIN_VIOLATIONS_SQL).fetchall()
    if violations:
        raise RuntimeError("0007 event-chain authority violations are present")


def upgrade() -> None:
    op.execute(
        """
        DO $job_event_chain_preflight$
        DECLARE
          active_head text;
        BEGIN
          IF current_user <> 'trading_owner'
             OR session_user <> 'trading_owner' THEN
            RAISE EXCEPTION '0007 requires exact trading_owner session'
              USING ERRCODE = '42501';
          END IF;

          IF current_setting('server_version_num')::integer / 10000 <> 16 THEN
            RAISE EXCEPTION '0007 requires PostgreSQL 16';
          END IF;

          SELECT CASE WHEN count(*) = 1 THEN min(version_num) END
          INTO active_head
          FROM public.alembic_version;
          IF active_head IS DISTINCT FROM '0006_job_transition_database_authority' THEN
            RAISE EXCEPTION '0007 requires exact 0006 head';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_stat_activity
            WHERE usename IN (
              'trading_job_api', 'trading_job_worker', 'trading_job_scheduler'
            )
          ) THEN
            RAISE EXCEPTION 'job-plane runtime sessions must be zero for 0007';
          END IF;
        END
        $job_event_chain_preflight$;
        """
    )
    op.execute(
        """
        LOCK TABLE public.jobs, public.job_attempts, public.job_events
        IN SHARE ROW EXCLUSIVE MODE;
        """
    )

    _require_catalog_digest(
        REVIEWED_0006_CATALOG_SHA256,
        REVIEWED_DISPOSABLE_0006_CATALOG_SHA256,
        "preflight",
    )
    _require_zero_event_chain_violations()

    # This is the sole persistent catalog mutation in revision 0007.
    op.execute(ACL_REPAIR_SQL)

    _require_catalog_digest(
        REVIEWED_0007_CATALOG_SHA256,
        REVIEWED_DISPOSABLE_0007_CATALOG_SHA256,
        "postflight",
    )
    op.execute(
        """
        DO $job_event_chain_postflight$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM unnest(ARRAY[
              'trading_job_api', 'trading_job_worker',
              'trading_job_scheduler'
            ]) role_name(name)
            CROSS JOIN unnest(ARRAY[
              'public.jobs'::regclass,
              'public.job_attempts'::regclass,
              'public.job_events'::regclass
            ]) relation_name(relation)
            WHERE has_any_column_privilege(role_name.name, relation_name.relation, 'INSERT')
               OR has_any_column_privilege(role_name.name, relation_name.relation, 'UPDATE')
               OR has_table_privilege(role_name.name, relation_name.relation, 'DELETE')
               OR has_table_privilege(role_name.name, relation_name.relation, 'TRUNCATE')
          ) THEN
            RAISE EXCEPTION 'direct runtime transition DML remains';
          END IF;

          IF EXISTS (
            WITH expected(procedure_name, identity_types, allowed_role) AS (
              VALUES
                ('api_enqueue_snapshot',
                 'text, jsonb, text, text, text, smallint, text, text',
                 'trading_job_api'),
                ('api_cancel_snapshot', 'text, text, text, text',
                 'trading_job_api'),
                ('scheduler_enqueue_snapshot',
                 'text, jsonb, text, text, text, text, text',
                 'trading_job_scheduler'),
                ('worker_claim_snapshot', 'text, text, text, integer, text, text',
                 'trading_job_worker'),
                ('worker_start_snapshot',
                 'text, text, text, text, bigint, bigint, bigint, text, text, text',
                 'trading_job_worker'),
                ('worker_control_snapshot_lease',
                 'text, text, text, text, integer, text',
                 'trading_job_worker'),
                ('worker_finalize_snapshot',
                 'text, text, text, text, text, text, text, text, text, text, integer, text, text, jsonb, text, text, boolean, text, jsonb',
                 'trading_job_worker'),
                ('worker_recover_expired_snapshot',
                 'text, text, text, text, text, text, bigint, bigint, bigint, text, text, text, text, text, text',
                 'trading_job_worker')
            ),
            actual(procedure_name, identity_types, allowed_role) AS (
              SELECT procedure_row.proname,
                     oidvectortypes(procedure_row.proargtypes),
                     expected.allowed_role
              FROM pg_catalog.pg_proc procedure_row
              JOIN expected
                ON expected.procedure_name = procedure_row.proname
               AND expected.identity_types =
                     oidvectortypes(procedure_row.proargtypes)
              WHERE procedure_row.pronamespace = 'job_plane'::regnamespace
            ),
            differences(procedure_name, identity_types, allowed_role) AS (
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
            RAISE EXCEPTION 'job_plane fixed function catalog is not exact';
          END IF;

          IF EXISTS (
            WITH expected(procedure_name, identity_types, allowed_role) AS (
              VALUES
                ('api_enqueue_snapshot',
                 'text, jsonb, text, text, text, smallint, text, text',
                 'trading_job_api'),
                ('api_cancel_snapshot', 'text, text, text, text',
                 'trading_job_api'),
                ('scheduler_enqueue_snapshot',
                 'text, jsonb, text, text, text, text, text',
                 'trading_job_scheduler'),
                ('worker_claim_snapshot', 'text, text, text, integer, text, text',
                 'trading_job_worker'),
                ('worker_start_snapshot',
                 'text, text, text, text, bigint, bigint, bigint, text, text, text',
                 'trading_job_worker'),
                ('worker_control_snapshot_lease',
                 'text, text, text, text, integer, text',
                 'trading_job_worker'),
                ('worker_finalize_snapshot',
                 'text, text, text, text, text, text, text, text, text, text, integer, text, text, jsonb, text, text, boolean, text, jsonb',
                 'trading_job_worker'),
                ('worker_recover_expired_snapshot',
                 'text, text, text, text, text, text, bigint, bigint, bigint, text, text, text, text, text, text',
                 'trading_job_worker')
            )
            SELECT 1
            FROM expected
            JOIN pg_catalog.pg_proc procedure_row
              ON procedure_row.pronamespace = 'job_plane'::regnamespace
             AND procedure_row.proname = expected.procedure_name
             AND oidvectortypes(procedure_row.proargtypes) = expected.identity_types
            CROSS JOIN unnest(ARRAY[
              'trading_job_api', 'trading_job_worker',
              'trading_job_scheduler'
            ]) role_name(name)
            WHERE has_function_privilege(
                    role_name.name, procedure_row.oid, 'EXECUTE'
                  ) IS DISTINCT FROM
                  (role_name.name = expected.allowed_role)
          ) OR EXISTS (
            WITH expected(procedure_name, identity_types, allowed_role) AS (
              VALUES
                ('api_enqueue_snapshot',
                 'text, jsonb, text, text, text, smallint, text, text',
                 'trading_job_api'),
                ('api_cancel_snapshot', 'text, text, text, text',
                 'trading_job_api'),
                ('scheduler_enqueue_snapshot',
                 'text, jsonb, text, text, text, text, text',
                 'trading_job_scheduler'),
                ('worker_claim_snapshot', 'text, text, text, integer, text, text',
                 'trading_job_worker'),
                ('worker_start_snapshot',
                 'text, text, text, text, bigint, bigint, bigint, text, text, text',
                 'trading_job_worker'),
                ('worker_control_snapshot_lease',
                 'text, text, text, text, integer, text',
                 'trading_job_worker'),
                ('worker_finalize_snapshot',
                 'text, text, text, text, text, text, text, text, text, text, integer, text, text, jsonb, text, text, boolean, text, jsonb',
                 'trading_job_worker'),
                ('worker_recover_expired_snapshot',
                 'text, text, text, text, text, text, bigint, bigint, bigint, text, text, text, text, text, text',
                 'trading_job_worker')
            )
            SELECT 1
            FROM expected
            JOIN pg_catalog.pg_proc procedure_row
              ON procedure_row.pronamespace = 'job_plane'::regnamespace
             AND procedure_row.proname = expected.procedure_name
             AND oidvectortypes(procedure_row.proargtypes) = expected.identity_types
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
                  AND grantee_role.rolname IS DISTINCT FROM expected.allowed_role
                )
              )
          ) THEN
            RAISE EXCEPTION 'job_plane fixed-function EXECUTE grants are not exact';
          END IF;
        END
        $job_event_chain_postflight$;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0007 job event-chain authority is forward-only; use a reviewed forward repair"
    )

"""Repair engine projection recovery and rotate the P1 product closure to C_new.

Revision ID: 0033_p3_core_closure_repair
Revises: 0032_p3_enqueue_freshness

The exact 0032 parent session catalog is checked first. Both functions are then rewritten from their exact observed
prior definitions inside one DO block, so a guard mismatch anywhere rolls back both. Migration 0010 and
plpgsql.variable_conflict are unchanged. This source authority may run only under exact disposable PostgreSQL
approval.
"""
from __future__ import annotations

from alembic import op

from services.job_store.p3_catalog import SESSION_CATALOG_SQL


revision = "0033_p3_core_closure_repair"
down_revision = "0032_p3_enqueue_freshness"
branch_labels = None
depends_on = None

# The parent P3 session catalog observed at 0032, pinned by value: drift is held and diagnosed, never re-baselined.
PARENT_SESSION_CATALOG_SHA256 = "f5092b5f50e6f81aa504e8515d595e8a1e2cf1848c3e4375c8d9990dc2c83d9a"
RECOVERY_SIGNATURE = "public.recover_engine_run_projections()"
RECOVERY_RESULT = (
    "TABLE(engine_run_id uuid, event_count bigint, event_type_counts jsonb, last_sequence bigint, "
    "last_digest character)"
)
RECOVERY_PRIOR_SOURCE_SHA256 = "15f38af183293e48f32864ffdf5a55d2249bc2a566f527a49da8aa1f5c3ac982"
RECOVERY_PRIOR_DEFINITION_SHA256 = "4841af0563ed378dba8fcbd99448829ee4a6e3787cbfe7804df02a594ec94373"
RECOVERY_REPAIRED_SOURCE_SHA256 = "5e8f35dbe62682a799b9037de151c9a50e3aa2c9f564bce7023e9ade609ad736"
RECOVERY_REPAIRED_DEFINITION_SHA256 = "a17c97e4410945cefebd688eb9a48407503408c5d91b25b8b6da26bfae92dc2e"
# The inference clause names a RETURNS TABLE column, which plpgsql rejects as ambiguous (42702).
RECOVERY_OLD_CLAUSE = "          ON CONFLICT (engine_run_id) DO UPDATE SET"
RECOVERY_NEW_CLAUSE = "          ON CONFLICT ON CONSTRAINT engine_run_projections_pkey DO UPDATE SET"
RECOVERY_ANCHOR = "          INSERT INTO public.engine_run_projections (\n"
RECOVERY_FENCE_REASON = "P1_COMPLETED_PROJECTION_MISSING"
# Under the existing SHARE lock and before any write: a durable P1 completion (the RunCompleted event that ends its
# own receipt for the same run) without a projection is refused, never recreated without its completion authority.
RECOVERY_FENCE = """\
          IF EXISTS (
            SELECT 1
            FROM public.engine_events AS completed
            JOIN public.engine_event_batch_receipts AS receipt
              ON receipt.batch_sha256 = completed.batch_sha256
             AND receipt.engine_run_id = completed.engine_run_id
             AND receipt.last_sequence = completed.stream_sequence
            WHERE completed.event_type = 'RunCompleted'
              AND jsonb_path_exists(
                    completed.canonical_json,
                    '$.payload.attributes[*] ? (@.name == "schema_version" && @.value == "nautilus-p1-event-stream-v1")'
                  )
              AND jsonb_path_exists(
                    completed.canonical_json,
                    '$.payload.attributes[*] ? (@.name == "semantic_digest")'
                  )
              AND NOT EXISTS (
                SELECT 1
                FROM public.engine_run_projections AS projection
                WHERE projection.engine_run_id = completed.engine_run_id
              )
          ) THEN
            RAISE EXCEPTION 'completed P1 engine run projection is missing'
              USING ERRCODE = 'P2D01',
                    DETAIL = 'reason=P1_COMPLETED_PROJECTION_MISSING';
          END IF;
"""

INGEST_SIGNATURE = (
    "job_plane.ingest_p1_engine_event_batch_v2(pg_catalog.text,pg_catalog.uuid,pg_catalog.uuid,pg_catalog.uuid,"
    "pg_catalog.uuid,pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text)"
)
INGEST_RESULT = (
    "TABLE(batch_sha256 character, ingestion_digest character, job_id character varying, "
    "attempt_id character varying, engine_run_id uuid, event_count bigint, first_sequence bigint, "
    "last_sequence bigint, last_digest character)"
)
CLOSURE_OLD = "97185d4c0b6090353ba51c1aab25ed4ea4dfab08113b655fac623af9e7db2b80"
CLOSURE_NEW = "5863b3c96c9bf31c8c6239cfedb9617b2f7418fff3cd7c697028b15392bf2155"
INGEST_PRIOR_SOURCE_SHA256 = "a4b63e0cf7f431c3bd9132e0fef4ec2f79a455761c69e570bb64fc0714ccd88c"
INGEST_PRIOR_DEFINITION_SHA256 = "b0e45b32f4e864e6a841e88047686f55b436ae58b7ce393d777c3b34643ee784"
INGEST_ROTATED_SOURCE_SHA256 = "dbeba3bd292c3b05220fb3d6a36082de2b57fb0f09a132baecec91e9f9717358"
INGEST_ROTATED_DEFINITION_SHA256 = "6f82e5f387910034cd26618dfcf7a41d5f00cf2fb0aad2900a5d06edce734293"


def _authority(signature: str, result: str) -> str:
    """Select one function's definition and hashes only while its owner, attributes and sole grant are exact."""
    return f"""
          SELECT
            pg_catalog.pg_get_functiondef(function_row.oid),
            pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              function_row.prosrc, 'UTF8'
            )), 'hex'),
            pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              pg_catalog.pg_get_functiondef(function_row.oid), 'UTF8'
            )), 'hex')
          INTO v_definition, v_source_sha256, v_definition_sha256
          FROM pg_catalog.pg_proc AS function_row
          WHERE function_row.oid = '{signature}'::pg_catalog.regprocedure
            AND function_row.prokind = 'f'
            AND pg_catalog.pg_get_userbyid(function_row.proowner) = 'trading_owner'
            AND function_row.prosecdef
            AND function_row.provolatile = 'v'
            AND function_row.proparallel = 'u'
            AND function_row.proconfig =
                  ARRAY['search_path=pg_catalog']::pg_catalog.text[]
            AND pg_catalog.pg_get_function_result(function_row.oid) = '{result}'
            AND (
              SELECT pg_catalog.count(*) = 1
                AND pg_catalog.bool_and(
                  acl.grantee = function_row.proowner
                  AND acl.privilege_type = 'EXECUTE'
                  AND NOT acl.is_grantable
                )
              FROM pg_catalog.aclexplode(COALESCE(
                function_row.proacl,
                pg_catalog.acldefault('f', function_row.proowner)
              )) AS acl
            );"""


def _count(needle: str) -> str:
    return (
        f"((pg_catalog.length(v_definition) - pg_catalog.length(pg_catalog.replace(v_definition, {needle}, '')))"
        f" / pg_catalog.length({needle}))"
    )


REPAIR_SQL = rf"""
        DO $p3_core_closure_repair$
        DECLARE
          v_prior_search_path pg_catalog.text;
          v_definition pg_catalog.text;
          v_edited pg_catalog.text;
          v_source_sha256 pg_catalog.text;
          v_definition_sha256 pg_catalog.text;
          v_parent_catalog pg_catalog.text;
          v_old_clause constant pg_catalog.text := $old_clause${RECOVERY_OLD_CLAUSE}$old_clause$;
          v_new_clause constant pg_catalog.text := $new_clause${RECOVERY_NEW_CLAUSE}$new_clause$;
          v_anchor constant pg_catalog.text := $anchor${RECOVERY_ANCHOR}$anchor$;
          v_fence constant pg_catalog.text := $fence${RECOVERY_FENCE}$fence$;
          v_old_closure constant pg_catalog.text := '{CLOSURE_OLD}';
          v_new_closure constant pg_catalog.text := '{CLOSURE_NEW}';
        BEGIN
          v_prior_search_path := pg_catalog.current_setting('search_path', false);
          PERFORM pg_catalog.set_config('search_path', 'pg_catalog', true);
          IF pg_catalog.current_setting('search_path', false)
               IS DISTINCT FROM 'pg_catalog' THEN
            RAISE EXCEPTION '0033 search path is invalid' USING ERRCODE = 'P2D08';
          END IF;
          IF current_user IS DISTINCT FROM 'trading_owner'
             OR session_user IS DISTINCT FROM 'trading_owner'
             OR (SELECT version_row.version_num FROM public.alembic_version AS version_row)
                  IS DISTINCT FROM '0032_p3_enqueue_freshness' THEN
            RAISE EXCEPTION '0033 requires the exact 0032 parent and the owner session'
              USING ERRCODE = 'P2D08';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint AS primary_key
            WHERE primary_key.conrelid = 'public.engine_run_projections'::pg_catalog.regclass
              AND primary_key.contype = 'p'
              AND primary_key.conname = 'engine_run_projections_pkey'
              AND pg_catalog.pg_get_constraintdef(primary_key.oid) = 'PRIMARY KEY (engine_run_id)'
          ) THEN
            RAISE EXCEPTION '0033 projection primary key is invalid' USING ERRCODE = 'P2D08';
          END IF;
          EXECUTE $session_catalog${SESSION_CATALOG_SQL}$session_catalog$ INTO v_parent_catalog;
          IF v_parent_catalog IS DISTINCT FROM '{PARENT_SESSION_CATALOG_SHA256}' THEN
            RAISE EXCEPTION '0033 parent session catalog differs' USING ERRCODE = 'P2D08';
          END IF;

          -- Recovery repair: the exact observed prior, one inference clause, one fence insertion.
{_authority(RECOVERY_SIGNATURE, RECOVERY_RESULT)}
          IF NOT FOUND
             OR pg_catalog.pg_get_function_identity_arguments(
                  '{RECOVERY_SIGNATURE}'::pg_catalog.regprocedure) <> ''
             OR v_source_sha256 IS DISTINCT FROM '{RECOVERY_PRIOR_SOURCE_SHA256}'
             OR v_definition_sha256 IS DISTINCT FROM '{RECOVERY_PRIOR_DEFINITION_SHA256}'
             OR {_count("v_old_clause")} <> 1
             OR {_count("v_anchor")} <> 1
             OR pg_catalog.strpos(v_definition, v_new_clause) <> 0
             OR pg_catalog.strpos(v_definition, '{RECOVERY_FENCE_REASON}') <> 0 THEN
            RAISE EXCEPTION '0033 recovery prior authority is invalid' USING ERRCODE = 'P2D08';
          END IF;
          v_edited := pg_catalog.replace(
            pg_catalog.replace(v_definition, v_old_clause, v_new_clause),
            v_anchor, v_fence || v_anchor
          );
          EXECUTE v_edited;
{_authority(RECOVERY_SIGNATURE, RECOVERY_RESULT)}
          IF NOT FOUND
             OR v_source_sha256 IS DISTINCT FROM '{RECOVERY_REPAIRED_SOURCE_SHA256}'
             OR v_definition_sha256 IS DISTINCT FROM '{RECOVERY_REPAIRED_DEFINITION_SHA256}' THEN
            RAISE EXCEPTION '0033 recovery result authority is invalid' USING ERRCODE = 'P2D08';
          END IF;

          -- Closure rotation: its own guard on the exact 0018 result, both pins to C_new.
{_authority(INGEST_SIGNATURE, INGEST_RESULT)}
          IF NOT FOUND
             OR v_source_sha256 IS DISTINCT FROM '{INGEST_PRIOR_SOURCE_SHA256}'
             OR v_definition_sha256 IS DISTINCT FROM '{INGEST_PRIOR_DEFINITION_SHA256}'
             OR {_count("v_old_closure")} <> 2
             OR pg_catalog.strpos(v_definition, v_new_closure) <> 0 THEN
            RAISE EXCEPTION '0033 closure prior authority is invalid' USING ERRCODE = 'P2D08';
          END IF;
          v_edited := pg_catalog.replace(v_definition, v_old_closure, v_new_closure);
          EXECUTE v_edited;
{_authority(INGEST_SIGNATURE, INGEST_RESULT)}
          IF NOT FOUND
             OR v_source_sha256 IS DISTINCT FROM '{INGEST_ROTATED_SOURCE_SHA256}'
             OR v_definition_sha256 IS DISTINCT FROM '{INGEST_ROTATED_DEFINITION_SHA256}' THEN
            RAISE EXCEPTION '0033 closure result authority is invalid' USING ERRCODE = 'P2D08';
          END IF;

          PERFORM pg_catalog.set_config('search_path', v_prior_search_path, true);
          IF NOT (
            pg_catalog.current_setting('search_path', false)
              OPERATOR(pg_catalog.=) v_prior_search_path
          ) THEN
            RAISE EXCEPTION '0033 search path restore failed' USING ERRCODE = 'P2D08';
          END IF;
        END;
        $p3_core_closure_repair$;
        """


def upgrade() -> None:
    op.get_bind().exec_driver_sql(REPAIR_SQL, execution_options={"no_parameters": True})


def downgrade() -> None:
    raise RuntimeError("0033 P3 core closure repair is forward-only; use a reviewed forward repair")

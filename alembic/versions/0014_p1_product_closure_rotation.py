"""Rotate the exact P1 product closure accepted by durable projection.

Revision ID: 0014_p1_product_closure_rotation
Revises: 0013_engine_backtest_enqueue_authority

This source authority may run only under exact disposable PostgreSQL approval.
"""
from __future__ import annotations

from alembic import op


revision = "0014_p1_product_closure_rotation"
down_revision = "0013_engine_backtest_enqueue_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        r"""
        DO $p1_product_closure_rotation$
        DECLARE
          v_function regprocedure :=
            'job_plane.ingest_p1_engine_event_batch_v2(text,uuid,uuid,uuid,uuid,text,text,text,text,text)'::regprocedure;
          v_oid oid;
          v_definition text;
          v_rotated_definition text;
          v_old constant text :=
            '75467781b920e7172917a96d162fb6e2a3e8f9afee9eff065ef0ed220f623069';
          v_new constant text :=
            '74b4e8864d8c9a2cc8ba9e5944340f013739e496933fa2f5dc9817bfcb7bced1';
          v_old_count integer;
          v_new_count integer;
          v_owner oid;
          v_acl aclitem[];
          v_security_definer boolean;
          v_volatility "char";
          v_parallel "char";
          v_config text[];
        BEGIN
          SELECT
            function_row.oid,
            pg_get_functiondef(function_row.oid),
            function_row.proowner,
            function_row.proacl,
            function_row.prosecdef,
            function_row.provolatile,
            function_row.proparallel,
            function_row.proconfig
          INTO
            v_oid,
            v_definition,
            v_owner,
            v_acl,
            v_security_definer,
            v_volatility,
            v_parallel,
            v_config
          FROM pg_proc AS function_row
          WHERE function_row.oid = v_function
            AND function_row.prokind = 'f';

          IF NOT FOUND
             OR v_security_definer IS DISTINCT FROM true
             OR v_volatility IS DISTINCT FROM 'v'
             OR v_parallel IS DISTINCT FROM 'u'
             OR v_config IS DISTINCT FROM ARRAY['search_path=pg_catalog']::text[] THEN
            RAISE EXCEPTION 'P1 closure rotation prior authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          v_old_count :=
            (length(v_definition) - length(replace(v_definition, v_old, '')))
            / length(v_old);
          v_new_count :=
            (length(v_definition) - length(replace(v_definition, v_new, '')))
            / length(v_new);
          IF v_old_count <> 2 OR v_new_count <> 0 THEN
            RAISE EXCEPTION 'P1 closure rotation prior digest count is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          v_rotated_definition := replace(v_definition, v_old, v_new);
          IF v_rotated_definition = v_definition THEN
            RAISE EXCEPTION 'P1 closure rotation made no exact change'
              USING ERRCODE = 'P2D08';
          END IF;
          EXECUTE v_rotated_definition;

          SELECT
            (length(pg_get_functiondef(function_row.oid))
               - length(replace(
                   pg_get_functiondef(function_row.oid), v_old, ''
                 ))) / length(v_old),
            (length(pg_get_functiondef(function_row.oid))
               - length(replace(
                   pg_get_functiondef(function_row.oid), v_new, ''
                 ))) / length(v_new)
          INTO v_old_count, v_new_count
          FROM pg_proc AS function_row
          WHERE function_row.oid = v_oid
            AND function_row.proowner IS NOT DISTINCT FROM v_owner
            AND function_row.proacl IS NOT DISTINCT FROM v_acl
            AND function_row.prosecdef IS NOT DISTINCT FROM v_security_definer
            AND function_row.provolatile IS NOT DISTINCT FROM v_volatility
            AND function_row.proparallel IS NOT DISTINCT FROM v_parallel
            AND function_row.proconfig IS NOT DISTINCT FROM v_config;

          IF NOT FOUND OR v_old_count <> 0 OR v_new_count <> 2 THEN
            RAISE EXCEPTION 'P1 closure rotation result authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
        END;
        $p1_product_closure_rotation$;
        """,
        execution_options={"no_parameters": True},
    )


def downgrade() -> None:
    raise RuntimeError(
        "0014 P1 product closure rotation is forward-only; "
        "use a reviewed forward repair"
    )

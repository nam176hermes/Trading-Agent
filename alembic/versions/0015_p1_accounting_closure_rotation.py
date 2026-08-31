"""Rotate the exact P1 accounting closure accepted by durable projection.

Revision ID: 0015_p1_accounting_closure_rotation
Revises: 0014_p1_product_closure_rotation

This source authority may run only under exact disposable PostgreSQL approval.
"""
from __future__ import annotations

from alembic import op


revision = "0015_p1_accounting_closure_rotation"
down_revision = "0014_p1_product_closure_rotation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        r"""
        DO $p1_accounting_closure_rotation$
        DECLARE
          v_function pg_catalog.regprocedure;
          v_oid pg_catalog.oid;
          v_definition pg_catalog.text;
          v_rotated_definition pg_catalog.text;
          v_source_sha256 pg_catalog.text;
          v_definition_sha256 pg_catalog.text;
          v_prior_search_path pg_catalog.text;
          v_old constant pg_catalog.text :=
            '74b4e8864d8c9a2cc8ba9e5944340f013739e496933fa2f5dc9817bfcb7bced1';
          v_new constant pg_catalog.text :=
            'b3bbb22552b896612ef93f78a61087d95fb1c061afb6102753e9f4d614b3963b';
          v_prior_source_sha256 constant pg_catalog.text :=
            'e6617353fe79c6e6ec0f6d1ecd824c4f28c2c52278dc1fbaf6e6d259426e2599';
          v_rotated_source_sha256 constant pg_catalog.text :=
            '8972d3cf715cfd761e86d88446161c6c4a36e8b4fb61f76d02ed41bd227ee089';
          v_prior_definition_sha256 constant pg_catalog.text :=
            '81d1858d8e15a1422a893768d5402bb505847bf19a535be401c80398b47ef19d';
          v_rotated_definition_sha256 constant pg_catalog.text :=
            '04ec80653561e0c40cd57d1920642dd6e1e878d0e11f4729cd4b97273e06dd5b';
          v_old_count pg_catalog.int4;
          v_new_count pg_catalog.int4;
        BEGIN
          v_prior_search_path :=
            pg_catalog.current_setting('search_path', false);
          PERFORM pg_catalog.set_config(
            'search_path', 'pg_catalog', true
          );
          IF pg_catalog.current_setting('search_path', false)
               IS DISTINCT FROM 'pg_catalog' THEN
            RAISE EXCEPTION 'P1 accounting closure rotation search path is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          v_function :=
            'job_plane.ingest_p1_engine_event_batch_v2(pg_catalog.text,pg_catalog.uuid,pg_catalog.uuid,pg_catalog.uuid,pg_catalog.uuid,pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text)'::pg_catalog.regprocedure;

          SELECT
            function_row.oid,
            pg_catalog.pg_get_functiondef(function_row.oid),
            pg_catalog.encode(
              pg_catalog.sha256(
                pg_catalog.convert_to(function_row.prosrc, 'UTF8')
              ),
              'hex'
            ),
            pg_catalog.encode(
              pg_catalog.sha256(
                pg_catalog.convert_to(
                  pg_catalog.pg_get_functiondef(function_row.oid), 'UTF8'
                )
              ),
              'hex'
            )
          INTO
            v_oid,
            v_definition,
            v_source_sha256,
            v_definition_sha256
          FROM pg_catalog.pg_proc AS function_row
          WHERE function_row.oid = v_function
            AND function_row.prokind = 'f'
            AND pg_catalog.pg_get_userbyid(function_row.proowner) =
                  'trading_owner'
            AND function_row.prosecdef
            AND function_row.provolatile = 'v'
            AND function_row.proparallel = 'u'
            AND function_row.proconfig =
                  ARRAY['search_path=pg_catalog']::pg_catalog.text[]
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
            );

          IF NOT FOUND
             OR v_source_sha256 <> v_prior_source_sha256
             OR v_definition_sha256 <> v_prior_definition_sha256 THEN
            RAISE EXCEPTION 'P1 accounting closure rotation prior authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          v_old_count :=
            (pg_catalog.length(v_definition) - pg_catalog.length(
              pg_catalog.replace(v_definition, v_old, '')
            )) / pg_catalog.length(v_old);
          v_new_count :=
            (pg_catalog.length(v_definition) - pg_catalog.length(
              pg_catalog.replace(v_definition, v_new, '')
            )) / pg_catalog.length(v_new);
          IF v_old_count <> 2 OR v_new_count <> 0 THEN
            RAISE EXCEPTION 'P1 accounting closure rotation prior digest count is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          v_rotated_definition :=
            pg_catalog.replace(v_definition, v_old, v_new);
          IF v_rotated_definition = v_definition THEN
            RAISE EXCEPTION 'P1 accounting closure rotation made no exact change'
              USING ERRCODE = 'P2D08';
          END IF;
          EXECUTE v_rotated_definition;

          SELECT
            pg_catalog.encode(
              pg_catalog.sha256(
                pg_catalog.convert_to(
                  pg_catalog.pg_get_functiondef(function_row.oid), 'UTF8'
                )
              ),
              'hex'
            ),
            pg_catalog.encode(
              pg_catalog.sha256(
                pg_catalog.convert_to(function_row.prosrc, 'UTF8')
              ),
              'hex'
            ),
            (pg_catalog.length(
               pg_catalog.pg_get_functiondef(function_row.oid)
             ) - pg_catalog.length(pg_catalog.replace(
                   pg_catalog.pg_get_functiondef(function_row.oid), v_old, ''
                 ))) / pg_catalog.length(v_old),
            (pg_catalog.length(
               pg_catalog.pg_get_functiondef(function_row.oid)
             ) - pg_catalog.length(pg_catalog.replace(
                   pg_catalog.pg_get_functiondef(function_row.oid), v_new, ''
                 ))) / pg_catalog.length(v_new)
          INTO
            v_definition_sha256,
            v_source_sha256,
            v_old_count,
            v_new_count
          FROM pg_catalog.pg_proc AS function_row
          WHERE function_row.oid = v_oid
            AND function_row.prokind = 'f'
            AND pg_catalog.pg_get_userbyid(function_row.proowner) =
                  'trading_owner'
            AND function_row.prosecdef
            AND function_row.provolatile = 'v'
            AND function_row.proparallel = 'u'
            AND function_row.proconfig =
                  ARRAY['search_path=pg_catalog']::pg_catalog.text[]
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
            );

          IF NOT FOUND
             OR v_definition_sha256 <> v_rotated_definition_sha256
             OR v_source_sha256 <> v_rotated_source_sha256
             OR v_old_count <> 0
             OR v_new_count <> 2 THEN
            RAISE EXCEPTION 'P1 accounting closure rotation result authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          PERFORM pg_catalog.set_config(
            'search_path', v_prior_search_path, true
          );
          IF NOT (
            pg_catalog.current_setting('search_path', false)
              OPERATOR(pg_catalog.=) v_prior_search_path
          ) THEN
            RAISE EXCEPTION 'P1 accounting closure rotation search path restore failed'
              USING ERRCODE = 'P2D08';
          END IF;
        END;
        $p1_accounting_closure_rotation$;
        """,
        execution_options={"no_parameters": True},
    )


def downgrade() -> None:
    raise RuntimeError(
        "0015 P1 accounting closure rotation is forward-only; "
        "use a reviewed forward repair"
    )

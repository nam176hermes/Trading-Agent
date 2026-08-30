"""Bind P1 request evidence without changing restart-stable ingestion identity.

Revision ID: 0017_p1_request_digest_authority
Revises: 0016_p1_result_authority_repair

This source authority may run only under exact disposable PostgreSQL approval.
"""
from __future__ import annotations

from alembic import op


revision = "0017_p1_request_digest_authority"
down_revision = "0016_p1_result_authority_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        r"""
        DO $p1_request_digest_authority$
        DECLARE
          v_function pg_catalog.regprocedure;
          v_oid pg_catalog.oid;
          v_definition pg_catalog.text;
          v_repaired_definition pg_catalog.text;
          v_source_sha256 pg_catalog.text;
          v_definition_sha256 pg_catalog.text;
          v_prior_search_path pg_catalog.text;
          v_old_keys constant pg_catalog.text :=
            E'              ''engine_upstream_commit'', ''engine_version'', ''event_count'',';
          v_new_keys constant pg_catalog.text :=
            E'              ''engine_request_sha256'', ''engine_upstream_commit'',\n' ||
            E'              ''engine_version'', ''event_count'',';
          v_old_count_check constant pg_catalog.text :=
            'v_total <> 23 OR v_valid <> 23';
          v_new_count_check constant pg_catalog.text :=
            'v_total <> 24 OR v_valid <> 24';
          v_old_string_fields constant pg_catalog.text :=
            E'                 ''engine_upstream_commit'', ''engine_version'', ''fees'',';
          v_new_string_fields constant pg_catalog.text :=
            E'                 ''engine_request_sha256'', ''engine_upstream_commit'',\n' ||
            E'                 ''engine_version'', ''fees'',';
          v_old_digest_check constant pg_catalog.text :=
            E'             OR v_metadata ->> ''p1_product_closure_sha256'' IS DISTINCT FROM\n' ||
            E'                  ''b3bbb22552b896612ef93f78a61087d95fb1c061afb6102753e9f4d614b3963b''';
          v_new_digest_check constant pg_catalog.text :=
            E'             OR v_metadata ->> ''p1_product_closure_sha256'' IS DISTINCT FROM\n' ||
            E'                  ''b3bbb22552b896612ef93f78a61087d95fb1c061afb6102753e9f4d614b3963b''\n' ||
            E'             OR v_metadata ->> ''engine_request_sha256''\n' ||
            E'                  !~ ''^[0-9a-f]{64}$''';
          v_old_identity constant pg_catalog.text :=
            '''validation_metadata'', v_metadata,';
          v_new_identity constant pg_catalog.text :=
            '''validation_metadata'', v_metadata - ''engine_request_sha256'',';
          v_prior_source_sha256 constant pg_catalog.text :=
            '4a04f41c0ac9dcb45ae09aa02b245cd07f4ea5f287c6956011d1c63d7c8c5eb4';
          v_repaired_source_sha256 constant pg_catalog.text :=
            'f914250fd1baca39063ed355d8a663e7aeb58c58367f699e1a92fb4602a7419f';
          v_prior_definition_sha256 constant pg_catalog.text :=
            'd2bd044da6afcb5647160e32fffbfa49619fabcfdc8a85dba78beaf3e30c330e';
          v_repaired_definition_sha256 constant pg_catalog.text :=
            '19bd7f67d5344ba4b2ee4473488fafe7c063db33d6518ebf6ea68aa6e8edf023';
          v_old_count pg_catalog.int4;
          v_new_count pg_catalog.int4;
        BEGIN
          v_prior_search_path := pg_catalog.current_setting('search_path', false);
          PERFORM pg_catalog.set_config('search_path', 'pg_catalog', true);
          IF pg_catalog.current_setting('search_path', false)
               IS DISTINCT FROM 'pg_catalog' THEN
            RAISE EXCEPTION 'P1 request-digest repair search path is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          v_function :=
            'job_plane.ingest_p1_engine_event_batch_v2(pg_catalog.text,pg_catalog.uuid,pg_catalog.uuid,pg_catalog.uuid,pg_catalog.uuid,pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text)'::pg_catalog.regprocedure;
          SELECT
            function_row.oid,
            pg_catalog.pg_get_functiondef(function_row.oid),
            pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              function_row.prosrc, 'UTF8'
            )), 'hex'),
            pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              pg_catalog.pg_get_functiondef(function_row.oid), 'UTF8'
            )), 'hex')
          INTO v_oid, v_definition, v_source_sha256, v_definition_sha256
          FROM pg_catalog.pg_proc AS function_row
          WHERE function_row.oid = v_function
            AND function_row.prokind = 'f'
            AND pg_catalog.pg_get_userbyid(function_row.proowner) = 'trading_owner'
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
            RAISE EXCEPTION 'P1 request-digest prior authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          FOREACH v_repaired_definition IN ARRAY ARRAY[
            v_old_keys, v_old_count_check, v_old_string_fields,
            v_old_digest_check, v_old_identity
          ] LOOP
            v_old_count := (
              pg_catalog.length(v_definition) - pg_catalog.length(
                pg_catalog.replace(v_definition, v_repaired_definition, '')
              )
            ) / pg_catalog.length(v_repaired_definition);
            IF v_old_count <> 1 THEN
              RAISE EXCEPTION 'P1 request-digest prior fragment is invalid'
                USING ERRCODE = 'P2D08';
            END IF;
          END LOOP;
          FOREACH v_repaired_definition IN ARRAY ARRAY[
            v_new_keys, v_new_count_check, v_new_string_fields,
            v_new_digest_check, v_new_identity
          ] LOOP
            v_new_count := (
              pg_catalog.length(v_definition) - pg_catalog.length(
                pg_catalog.replace(v_definition, v_repaired_definition, '')
              )
            ) / pg_catalog.length(v_repaired_definition);
            IF v_new_count <> 0 THEN
              RAISE EXCEPTION 'P1 request-digest new fragment already exists'
                USING ERRCODE = 'P2D08';
            END IF;
          END LOOP;

          v_repaired_definition := pg_catalog.replace(
            pg_catalog.replace(
              pg_catalog.replace(
                pg_catalog.replace(
                  pg_catalog.replace(v_definition, v_old_keys, v_new_keys),
                  v_old_count_check, v_new_count_check
                ),
                v_old_string_fields, v_new_string_fields
              ),
              v_old_digest_check, v_new_digest_check
            ),
            v_old_identity, v_new_identity
          );
          EXECUTE v_repaired_definition;

          SELECT
            pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              function_row.prosrc, 'UTF8'
            )), 'hex'),
            pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              pg_catalog.pg_get_functiondef(function_row.oid), 'UTF8'
            )), 'hex')
          INTO v_source_sha256, v_definition_sha256
          FROM pg_catalog.pg_proc AS function_row
          WHERE function_row.oid = v_oid
            AND function_row.prokind = 'f'
            AND pg_catalog.pg_get_userbyid(function_row.proowner) = 'trading_owner'
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
             OR v_source_sha256 <> v_repaired_source_sha256
             OR v_definition_sha256 <> v_repaired_definition_sha256 THEN
            RAISE EXCEPTION 'P1 request-digest repaired authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          PERFORM pg_catalog.set_config(
            'search_path', v_prior_search_path, true
          );
          IF pg_catalog.current_setting('search_path', false)
               IS DISTINCT FROM v_prior_search_path THEN
            RAISE EXCEPTION 'P1 request-digest search path restore failed'
              USING ERRCODE = 'P2D08';
          END IF;
        END;
        $p1_request_digest_authority$;
        """,
        execution_options={"no_parameters": True},
    )


def downgrade() -> None:
    raise RuntimeError(
        "0017 P1 request-digest authority is forward-only; "
        "use a reviewed forward repair"
    )

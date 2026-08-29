"""Repair P1 UUID authority and artifact RLS for exact paper BACKTEST jobs.

Revision ID: 0016_p1_result_authority_repair
Revises: 0015_p1_accounting_closure_rotation

This source authority may run only under exact disposable PostgreSQL approval.
"""
from __future__ import annotations

from alembic import op


revision = "0016_p1_result_authority_repair"
down_revision = "0015_p1_accounting_closure_rotation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        r"""
        DO $p1_result_authority_repair$
        DECLARE
          v_function pg_catalog.regprocedure;
          v_oid pg_catalog.oid;
          v_definition pg_catalog.text;
          v_repaired_definition pg_catalog.text;
          v_source_sha256 pg_catalog.text;
          v_definition_sha256 pg_catalog.text;
          v_policy_sha256 pg_catalog.text;
          v_prior_search_path pg_catalog.text;
          v_old constant pg_catalog.text := '88889999aaaabbbb';
          v_new constant pg_catalog.text := '89ab89ab89ab89ab';
          v_prior_source_sha256 constant pg_catalog.text :=
            '6e8cae1e8f9f120fbf79fc0a9eb444ce0c1163b708e35ec71ec813561c20f445';
          v_repaired_source_sha256 constant pg_catalog.text :=
            '69704d5c3ac1516339095865238eb650da4b2bda0f95a3b5a675b50f00a389b5';
          v_prior_definition_sha256 constant pg_catalog.text :=
            'aea1129235f91d7645741c04912590a31cc3e667df43867c8b3a7cecfec9b743';
          v_repaired_definition_sha256 constant pg_catalog.text :=
            '6d76e0cadddd6f204cb445f38e7bc7462ac92342be3cca5fa639071bf182db2a';
          v_prior_p1_source_sha256 constant pg_catalog.text :=
            '8972d3cf715cfd761e86d88446161c6c4a36e8b4fb61f76d02ed41bd227ee089';
          v_repaired_p1_source_sha256 constant pg_catalog.text :=
            '54d6e1445973fbe7902709d7a118c65b152cb7d6e6a2e2cd1b257e83ede5f96e';
          v_prior_p1_definition_sha256 constant pg_catalog.text :=
            '04ec80653561e0c40cd57d1920642dd6e1e878d0e11f4729cd4b97273e06dd5b';
          v_repaired_p1_definition_sha256 constant pg_catalog.text :=
            'bd787ebf4a5e3f1526667346b47e9474716061c7be75271ed8cfc8a9f270177f';
          v_helper_source_sha256 constant pg_catalog.text :=
            '342200fa9e9feefd84031d758232273aef8aa00c05880b5ee16f42fa5b967253';
          v_helper_definition_sha256 constant pg_catalog.text :=
            '663f12490d3dceba5aa4e9878d21e70cd7ff9f5f1baa0fd5a636acc5f77938d8';
          v_prior_policy_sha256 constant pg_catalog.text :=
            '4f9c03425a69edf9844a1ae9188660ac7ea4285e5a1ecd87e8e6ecc31be6ec78';
          v_repaired_policy_sha256 constant pg_catalog.text :=
            '42daedaeeb38b9d9f18f8c030ea5d28e3b38c25a5ec592d94b18d6be697b0c3c';
          v_old_count pg_catalog.int4;
          v_new_count pg_catalog.int4;
        BEGIN
          v_prior_search_path := pg_catalog.current_setting('search_path', false);
          PERFORM pg_catalog.set_config('search_path', 'pg_catalog', true);
          IF pg_catalog.current_setting('search_path', false)
               IS DISTINCT FROM 'pg_catalog' THEN
            RAISE EXCEPTION 'P1 result-authority repair search path is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          v_function :=
            'job_plane.ingest_engine_job_result_v2(pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text)'::pg_catalog.regprocedure;
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
              SELECT pg_catalog.count(*) = 2
                AND pg_catalog.count(*) FILTER (
                  WHERE acl.grantee = (
                    SELECT role_row.oid FROM pg_catalog.pg_roles AS role_row
                    WHERE role_row.rolname = 'trading_job_worker'
                  )
                ) = 1
                AND pg_catalog.bool_and(
                  acl.privilege_type = 'EXECUTE'
                  AND NOT acl.is_grantable
                  AND acl.grantee IN (
                    function_row.proowner,
                    (
                      SELECT role_row.oid FROM pg_catalog.pg_roles AS role_row
                      WHERE role_row.rolname = 'trading_job_worker'
                    )
                  )
                )
              FROM pg_catalog.aclexplode(COALESCE(
                function_row.proacl,
                pg_catalog.acldefault('f', function_row.proowner)
              )) AS acl
            );
          IF NOT FOUND
             OR v_source_sha256 <> v_prior_source_sha256
             OR v_definition_sha256 <> v_prior_definition_sha256 THEN
            RAISE EXCEPTION 'P1 result-authority prior function is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          v_old_count := (
            pg_catalog.length(v_definition)
              - pg_catalog.length(pg_catalog.replace(v_definition, v_old, ''))
          ) / pg_catalog.length(v_old);
          v_new_count := (
            pg_catalog.length(v_definition)
              - pg_catalog.length(pg_catalog.replace(v_definition, v_new, ''))
          ) / pg_catalog.length(v_new);
          IF v_old_count <> 1 OR v_new_count <> 0 THEN
            RAISE EXCEPTION 'P1 result-authority UUID variant count is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          v_repaired_definition := pg_catalog.replace(v_definition, v_old, v_new);
          EXECUTE v_repaired_definition;

          SELECT
            pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              function_row.prosrc, 'UTF8'
            )), 'hex'),
            pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              pg_catalog.pg_get_functiondef(function_row.oid), 'UTF8'
            )), 'hex'),
            (
              pg_catalog.length(pg_catalog.pg_get_functiondef(function_row.oid))
                - pg_catalog.length(pg_catalog.replace(
                    pg_catalog.pg_get_functiondef(function_row.oid), v_old, ''
                  ))
            ) / pg_catalog.length(v_old),
            (
              pg_catalog.length(pg_catalog.pg_get_functiondef(function_row.oid))
                - pg_catalog.length(pg_catalog.replace(
                    pg_catalog.pg_get_functiondef(function_row.oid), v_new, ''
                  ))
            ) / pg_catalog.length(v_new)
          INTO v_source_sha256, v_definition_sha256, v_old_count, v_new_count
          FROM pg_catalog.pg_proc AS function_row
          WHERE function_row.oid = v_oid;
          IF NOT FOUND
             OR v_source_sha256 <> v_repaired_source_sha256
             OR v_definition_sha256 <> v_repaired_definition_sha256
             OR v_old_count <> 0 OR v_new_count <> 1 THEN
            RAISE EXCEPTION 'P1 result-authority repaired function is invalid'
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
             OR v_source_sha256 <> v_prior_p1_source_sha256
             OR v_definition_sha256 <> v_prior_p1_definition_sha256 THEN
            RAISE EXCEPTION 'P1 event authority prior function is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          v_old_count := (
            pg_catalog.length(v_definition)
              - pg_catalog.length(pg_catalog.replace(v_definition, v_old, ''))
          ) / pg_catalog.length(v_old);
          v_new_count := (
            pg_catalog.length(v_definition)
              - pg_catalog.length(pg_catalog.replace(v_definition, v_new, ''))
          ) / pg_catalog.length(v_new);
          IF v_old_count <> 1 OR v_new_count <> 0 THEN
            RAISE EXCEPTION 'P1 event authority UUID variant count is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          v_repaired_definition := pg_catalog.replace(v_definition, v_old, v_new);
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
                  ARRAY['search_path=pg_catalog']::pg_catalog.text[];
          IF NOT FOUND
             OR v_source_sha256 <> v_repaired_p1_source_sha256
             OR v_definition_sha256 <> v_repaired_p1_definition_sha256 THEN
            RAISE EXCEPTION 'P1 event authority repaired function is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                   pg_catalog.pg_get_expr(policy_row.polwithcheck, policy_row.polrelid),
                   'UTF8'
                 )), 'hex')
          INTO v_policy_sha256
          FROM pg_catalog.pg_policy AS policy_row
          WHERE policy_row.polname = 'job_plane_worker_artifacts_insert'
            AND policy_row.polrelid = 'public.job_artifacts'::pg_catalog.regclass
            AND policy_row.polcmd = 'a'
            AND policy_row.polpermissive
            AND (
              SELECT pg_catalog.array_agg(role_row.rolname ORDER BY role_row.rolname)
              FROM pg_catalog.unnest(policy_row.polroles) AS role_oid(oid)
              JOIN pg_catalog.pg_roles AS role_row ON role_row.oid = role_oid.oid
            ) = ARRAY['trading_job_worker']::pg_catalog.name[];
          IF NOT FOUND OR v_policy_sha256 <> v_prior_policy_sha256 THEN
            RAISE EXCEPTION 'P1 result-authority prior artifact policy is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          SELECT
            pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              function_row.prosrc, 'UTF8'
            )), 'hex'),
            pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              pg_catalog.pg_get_functiondef(function_row.oid), 'UTF8'
            )), 'hex')
          INTO v_source_sha256, v_definition_sha256
          FROM pg_catalog.pg_proc AS function_row
          WHERE function_row.oid =
                'job_plane.paper_worker_job_allowed(pg_catalog.text,pg_catalog.jsonb)'::pg_catalog.regprocedure
            AND function_row.prokind = 'f'
            AND pg_catalog.pg_get_userbyid(function_row.proowner) = 'trading_owner'
            AND NOT function_row.prosecdef
            AND function_row.provolatile = 'i'
            AND function_row.proparallel = 's'
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
             OR v_source_sha256 <> v_helper_source_sha256
             OR v_definition_sha256 <> v_helper_definition_sha256 THEN
            RAISE EXCEPTION 'P1 artifact policy helper is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          GRANT EXECUTE ON FUNCTION job_plane.paper_worker_job_allowed(
            pg_catalog.text, pg_catalog.jsonb
          ) TO trading_job_worker;

          DROP POLICY job_plane_worker_artifacts_insert
            ON public.job_artifacts;
          CREATE POLICY job_plane_worker_artifacts_insert
            ON public.job_artifacts FOR INSERT TO trading_job_worker
            WITH CHECK (
              EXISTS (
                SELECT 1 FROM public.jobs
                WHERE jobs.job_id = job_artifacts.job_id
                  AND job_plane.paper_worker_job_allowed(
                        jobs.job_type, jobs.payload
                      )
              )
            );

          SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                   pg_catalog.pg_get_expr(policy_row.polwithcheck, policy_row.polrelid),
                   'UTF8'
                 )), 'hex')
          INTO v_policy_sha256
          FROM pg_catalog.pg_policy AS policy_row
          WHERE policy_row.polname = 'job_plane_worker_artifacts_insert'
            AND policy_row.polrelid = 'public.job_artifacts'::pg_catalog.regclass
            AND policy_row.polcmd = 'a'
            AND policy_row.polpermissive
            AND (
              SELECT pg_catalog.array_agg(role_row.rolname ORDER BY role_row.rolname)
              FROM pg_catalog.unnest(policy_row.polroles) AS role_oid(oid)
              JOIN pg_catalog.pg_roles AS role_row ON role_row.oid = role_oid.oid
            ) = ARRAY['trading_job_worker']::pg_catalog.name[];
          IF NOT FOUND OR v_policy_sha256 <> v_repaired_policy_sha256 THEN
            RAISE EXCEPTION 'P1 result-authority repaired artifact policy is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          IF (
            SELECT pg_catalog.count(*) <> 2
              OR pg_catalog.count(*) FILTER (
                WHERE acl.grantee = (
                  SELECT role_row.oid FROM pg_catalog.pg_roles AS role_row
                  WHERE role_row.rolname = 'trading_job_worker'
                )
              ) <> 1
              OR NOT pg_catalog.bool_and(
                acl.privilege_type = 'EXECUTE'
                AND NOT acl.is_grantable
                AND acl.grantee IN (
                  function_row.proowner,
                  (
                    SELECT role_row.oid FROM pg_catalog.pg_roles AS role_row
                    WHERE role_row.rolname = 'trading_job_worker'
                  )
                )
              )
            FROM pg_catalog.pg_proc AS function_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(
              function_row.proacl,
              pg_catalog.acldefault('f', function_row.proowner)
            )) AS acl
            WHERE function_row.oid =
                  'job_plane.paper_worker_job_allowed(pg_catalog.text,pg_catalog.jsonb)'::pg_catalog.regprocedure
            GROUP BY function_row.proowner
          ) THEN
            RAISE EXCEPTION 'P1 artifact policy helper grant is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          PERFORM pg_catalog.set_config(
            'search_path', v_prior_search_path, true
          );
          IF pg_catalog.current_setting('search_path', false)
               IS DISTINCT FROM v_prior_search_path THEN
            RAISE EXCEPTION 'P1 result-authority search path restore failed'
              USING ERRCODE = 'P2D08';
          END IF;
        END;
        $p1_result_authority_repair$;
        """,
        execution_options={"no_parameters": True},
    )


def downgrade() -> None:
    raise RuntimeError(
        "0016 P1 result-authority repair is forward-only; "
        "use a reviewed forward repair"
    )

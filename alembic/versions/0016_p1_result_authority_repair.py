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
          v_old_conflict constant pg_catalog.text :=
            '          ) ON CONFLICT (job_id) DO NOTHING;';
          v_new_conflict constant pg_catalog.text :=
            '          ) ON CONFLICT ON CONSTRAINT engine_job_results_pkey DO NOTHING;';
          v_old_canonical constant pg_catalog.text :=
            E'          v_legacy_document := public.canonical_domain_json_string(\n' ||
            E'            (v_document - ''validation_metadata'' - ''validator_id'')::text\n' ||
            E'          );';
          v_new_canonical constant pg_catalog.text :=
            E'          v_legacy_document := public.canonical_domain_json(\n' ||
            E'            v_document - ''validation_metadata'' - ''validator_id''\n' ||
            E'          );';
          v_old_wrapper_binding constant pg_catalog.text := $old_wrapper_binding$          v_legacy_document := public.canonical_domain_json_string(
            (v_document - 'validation_metadata' - 'validator_id')::text
          );
          SELECT * INTO bound
          FROM job_plane.ingest_engine_job_result(
            p_job_id, p_attempt_id, p_worker_id, p_lease_token,
            v_legacy_document
          );
          IF NOT FOUND
             OR bound.batch_sha256 IS DISTINCT FROM accepted.batch_sha256
             OR bound.ingestion_digest IS DISTINCT FROM accepted.ingestion_digest
             OR bound.job_id IS DISTINCT FROM accepted.job_id
             OR bound.attempt_id IS DISTINCT FROM accepted.attempt_id
             OR bound.engine_run_id IS DISTINCT FROM accepted.engine_run_id THEN
            RAISE EXCEPTION 'P1 job result differs from durable projection authority'
              USING ERRCODE = 'P2D01';
          END IF;
          RETURN QUERY SELECT
            bound.batch_sha256, bound.ingestion_digest,
            bound.job_id, bound.attempt_id, bound.engine_run_id,
            bound.event_count, bound.first_sequence,
            bound.last_sequence, bound.last_digest;$old_wrapper_binding$;
          v_new_wrapper_binding constant pg_catalog.text := $new_wrapper_binding$          INSERT INTO public.engine_job_results (
            job_id, batch_sha256, attempt_id
          ) VALUES (
            accepted.job_id, accepted.batch_sha256, accepted.attempt_id
          ) ON CONFLICT ON CONSTRAINT engine_job_results_pkey DO NOTHING;
          SELECT result.* INTO bound
          FROM public.engine_job_results AS result
          WHERE result.job_id = accepted.job_id
          FOR SHARE;
          IF NOT FOUND
             OR bound.batch_sha256 IS DISTINCT FROM accepted.batch_sha256
             OR bound.attempt_id IS DISTINCT FROM accepted.attempt_id THEN
            RAISE EXCEPTION 'P1 job result differs from durable projection authority'
              USING ERRCODE = 'P2D01';
          END IF;
          RETURN QUERY SELECT
            accepted.batch_sha256, accepted.ingestion_digest,
            accepted.job_id, accepted.attempt_id, accepted.engine_run_id,
            accepted.event_count, accepted.first_sequence,
            accepted.last_sequence, accepted.last_digest;$new_wrapper_binding$;
          v_prior_source_sha256 constant pg_catalog.text :=
            '6e8cae1e8f9f120fbf79fc0a9eb444ce0c1163b708e35ec71ec813561c20f445';
          v_repaired_source_sha256 constant pg_catalog.text :=
            'a9d2bcca28d01e3c03c1d953e48ef50925059fe7148da6715fb777ea78e03369';
          v_prior_definition_sha256 constant pg_catalog.text :=
            'aea1129235f91d7645741c04912590a31cc3e667df43867c8b3a7cecfec9b743';
          v_repaired_definition_sha256 constant pg_catalog.text :=
            'c07827a548d5e87df02b302d11819a641e4322a7df243be301585cab38800b3b';
          v_prior_p1_source_sha256 constant pg_catalog.text :=
            '8972d3cf715cfd761e86d88446161c6c4a36e8b4fb61f76d02ed41bd227ee089';
          v_repaired_p1_source_sha256 constant pg_catalog.text :=
            '4a04f41c0ac9dcb45ae09aa02b245cd07f4ea5f287c6956011d1c63d7c8c5eb4';
          v_prior_p1_definition_sha256 constant pg_catalog.text :=
            '04ec80653561e0c40cd57d1920642dd6e1e878d0e11f4729cd4b97273e06dd5b';
          v_repaired_p1_definition_sha256 constant pg_catalog.text :=
            'd2bd044da6afcb5647160e32fffbfa49619fabcfdc8a85dba78beaf3e30c330e';
          v_prior_legacy_source_sha256 constant pg_catalog.text :=
            '10e24e84094478e5b4994dab8ffdb22dec021ca235cfe51a05e94ae49e62fd34';
          v_repaired_legacy_source_sha256 constant pg_catalog.text :=
            '1bac6ed97eec8dcd1dbd3ff1de27ec111fc8fbeff291d135bd423391441ded0e';
          v_prior_legacy_definition_sha256 constant pg_catalog.text :=
            '5fcb5c4542ef72c38639922535d2d1065fb6198da28bcdce9634f66aa59b69e0';
          v_repaired_legacy_definition_sha256 constant pg_catalog.text :=
            '67cf705a214d242e2a197327c53117c4508c16f289b31dbb4fe7be6653219372';
          v_helper_source_sha256 constant pg_catalog.text :=
            'e4ffec60fa1e4f02b56b7484b6b84423c1d0349c3d5781062c4ad600a7090416';
          v_helper_definition_sha256 constant pg_catalog.text :=
            'f9d54384bb1dae2a0cda166118cc0ceb016e8590c58579820384284ea8e42e9b';
          v_prior_policy_sha256 constant pg_catalog.text :=
            '4f9c03425a69edf9844a1ae9188660ac7ea4285e5a1ecd87e8e6ecc31be6ec78';
          v_repaired_policy_sha256 constant pg_catalog.text :=
            '002fd4c9ccc597c2016b7ae1ec32be78138d0e05448ca24a1d6051a0a84b6141';
          v_old_count pg_catalog.int4;
          v_new_count pg_catalog.int4;
          v_old_canonical_count pg_catalog.int4;
          v_new_canonical_count pg_catalog.int4;
          v_old_wrapper_count pg_catalog.int4;
          v_new_wrapper_count pg_catalog.int4;
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
          v_old_wrapper_count := (
            pg_catalog.length(v_definition) - pg_catalog.length(
              pg_catalog.replace(v_definition, v_old_wrapper_binding, '')
            )
          ) / pg_catalog.length(v_old_wrapper_binding);
          v_new_wrapper_count := (
            pg_catalog.length(v_definition) - pg_catalog.length(
              pg_catalog.replace(v_definition, v_new_wrapper_binding, '')
            )
          ) / pg_catalog.length(v_new_wrapper_binding);
          IF v_old_wrapper_count <> 1 OR v_new_wrapper_count <> 0 THEN
            RAISE EXCEPTION 'P1 result-authority binding path is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          v_repaired_definition := pg_catalog.replace(
            pg_catalog.replace(v_definition, v_old, v_new),
            v_old_wrapper_binding,
            v_new_wrapper_binding
          );
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
          v_old_canonical_count := (
            pg_catalog.length(v_definition) - pg_catalog.length(
              pg_catalog.replace(v_definition, v_old_canonical, '')
            )
          ) / pg_catalog.length(v_old_canonical);
          v_new_canonical_count := (
            pg_catalog.length(v_definition) - pg_catalog.length(
              pg_catalog.replace(v_definition, v_new_canonical, '')
            )
          ) / pg_catalog.length(v_new_canonical);
          IF v_old_canonical_count <> 1 OR v_new_canonical_count <> 0 THEN
            RAISE EXCEPTION 'P1 event-authority canonical projection is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          v_repaired_definition := pg_catalog.replace(
            pg_catalog.replace(v_definition, v_old, v_new),
            v_old_canonical,
            v_new_canonical
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
                  ARRAY['search_path=pg_catalog']::pg_catalog.text[];
          IF NOT FOUND
             OR v_source_sha256 <> v_repaired_p1_source_sha256
             OR v_definition_sha256 <> v_repaired_p1_definition_sha256 THEN
            RAISE EXCEPTION 'P1 event authority repaired function is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          v_function :=
            'job_plane.ingest_engine_job_result(pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text,pg_catalog.text)'::pg_catalog.regprocedure;
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
             OR v_source_sha256 <> v_prior_legacy_source_sha256
             OR v_definition_sha256 <> v_prior_legacy_definition_sha256 THEN
            RAISE EXCEPTION 'legacy result authority prior function is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          v_old_count := (
            pg_catalog.length(v_definition) - pg_catalog.length(
              pg_catalog.replace(v_definition, v_old_conflict, '')
            )
          ) / pg_catalog.length(v_old_conflict);
          v_new_count := (
            pg_catalog.length(v_definition) - pg_catalog.length(
              pg_catalog.replace(v_definition, v_new_conflict, '')
            )
          ) / pg_catalog.length(v_new_conflict);
          IF v_old_count <> 1 OR v_new_count <> 0 THEN
            RAISE EXCEPTION 'legacy result conflict target is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          v_repaired_definition := pg_catalog.replace(
            v_definition, v_old_conflict, v_new_conflict
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
          WHERE function_row.oid = v_oid;
          IF NOT FOUND
             OR v_source_sha256 <> v_repaired_legacy_source_sha256
             OR v_definition_sha256 <> v_repaired_legacy_definition_sha256 THEN
            RAISE EXCEPTION 'legacy result authority repaired function is invalid'
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
                'job_plane.paper_worker_job_id_allowed(pg_catalog.text)'::pg_catalog.regprocedure
            AND function_row.prokind = 'f'
            AND pg_catalog.pg_get_userbyid(function_row.proowner) = 'trading_owner'
            AND function_row.prosecdef
            AND function_row.provolatile = 's'
            AND function_row.proparallel = 's'
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
             OR v_source_sha256 <> v_helper_source_sha256
             OR v_definition_sha256 <> v_helper_definition_sha256 THEN
            RAISE EXCEPTION 'P1 artifact policy helper is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          IF pg_catalog.has_any_column_privilege(
               'trading_job_worker', 'public.engine_events', 'SELECT'
             )
             OR pg_catalog.has_any_column_privilege(
               'trading_job_worker', 'public.engine_run_projections', 'SELECT'
             ) THEN
            RAISE EXCEPTION 'P1 durable parity prior read authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          GRANT SELECT (
            message_id, engine_run_id, stream_sequence, event_type,
            event_family, canonical_json_text, digest, batch_sha256
          ) ON TABLE public.engine_events TO trading_job_worker;
          GRANT SELECT (
            engine_run_id, event_count, event_type_counts, last_sequence,
            last_digest, batch_sha256, semantic_digest, request_message_id
          ) ON TABLE public.engine_run_projections TO trading_job_worker;
          IF (
            SELECT pg_catalog.array_agg(
                     attribute.attname::pg_catalog.text ORDER BY attribute.attnum
                   )
            FROM pg_catalog.pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.engine_events'::pg_catalog.regclass
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
              AND pg_catalog.has_column_privilege(
                    'trading_job_worker', attribute.attrelid,
                    attribute.attnum, 'SELECT'
                  )
          ) IS DISTINCT FROM ARRAY[
            'message_id', 'engine_run_id', 'stream_sequence', 'event_type',
            'event_family', 'canonical_json_text', 'digest', 'batch_sha256'
          ]::pg_catalog.text[]
          OR (
            SELECT pg_catalog.array_agg(
                     attribute.attname::pg_catalog.text ORDER BY attribute.attnum
                   )
            FROM pg_catalog.pg_attribute AS attribute
            WHERE attribute.attrelid =
                  'public.engine_run_projections'::pg_catalog.regclass
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
              AND pg_catalog.has_column_privilege(
                    'trading_job_worker', attribute.attrelid,
                    attribute.attnum, 'SELECT'
                  )
          ) IS DISTINCT FROM ARRAY[
            'engine_run_id', 'event_count', 'event_type_counts',
            'last_sequence', 'last_digest', 'batch_sha256',
            'semantic_digest', 'request_message_id'
          ]::pg_catalog.text[] THEN
            RAISE EXCEPTION 'P1 durable parity read authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;

          DROP POLICY job_plane_worker_artifacts_insert
            ON public.job_artifacts;
          CREATE POLICY job_plane_worker_artifacts_insert
            ON public.job_artifacts FOR INSERT TO trading_job_worker
            WITH CHECK (
              job_plane.paper_worker_job_id_allowed(
                job_artifacts.job_id
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
                  'job_plane.paper_worker_job_id_allowed(pg_catalog.text)'::pg_catalog.regprocedure
            GROUP BY function_row.proowner
          ) THEN
            RAISE EXCEPTION 'P1 artifact policy helper authority is invalid'
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

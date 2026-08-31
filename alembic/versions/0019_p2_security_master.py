"""Add the source-closed P2 point-in-time security-master ledger.

Revision ID: 0019_p2_security_master
Revises: 0018_p1_paper_closure_rotation

Runtime proof requires a separately approved disposable PostgreSQL fixture.
"""
from __future__ import annotations

from alembic import op


revision = "0019_p2_security_master"
down_revision = "0018_p1_paper_closure_rotation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        r"""
        DO $p2_security_master_preflight$
        DECLARE
          v_head pg_catalog.text;
        BEGIN
          PERFORM pg_catalog.set_config('search_path', 'pg_catalog', true);
          IF pg_catalog.current_setting('search_path', false)
               IS DISTINCT FROM 'pg_catalog'
             OR pg_catalog.current_setting('server_version_num')::pg_catalog.int4 / 10000 <> 16
             OR current_user <> 'trading_owner'
             OR session_user <> 'trading_owner'
             OR (
               SELECT pg_catalog.pg_get_userbyid(database_row.datdba)
               FROM pg_catalog.pg_database AS database_row
               WHERE database_row.datname = pg_catalog.current_database()
             ) IS DISTINCT FROM 'trading_owner'
             OR (
               SELECT pg_catalog.pg_get_userbyid(namespace_row.nspowner)
               FROM pg_catalog.pg_namespace AS namespace_row
               WHERE namespace_row.nspname = 'public'
             ) IS DISTINCT FROM 'trading_owner' THEN
            RAISE EXCEPTION 'P2 security-master host authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          SELECT version_num INTO v_head FROM public.alembic_version;
          IF v_head IS DISTINCT FROM '0018_p1_paper_closure_rotation'
             OR pg_catalog.to_regclass('public.security_master_identities') IS NOT NULL
             OR pg_catalog.to_regclass('public.security_master_revisions') IS NOT NULL
             OR pg_catalog.to_regprocedure(
                  'public.append_security_master_revision(pg_catalog.text)'
                ) IS NOT NULL
             OR pg_catalog.to_regprocedure(
                  'public.reject_security_master_mutation()'
                ) IS NOT NULL THEN
            RAISE EXCEPTION 'P2 security-master prior source state is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          IF (
            SELECT pg_catalog.count(*)
            FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid IN (
              pg_catalog.to_regprocedure(
                'public.canonical_domain_json(pg_catalog.jsonb)'
              ),
              pg_catalog.to_regprocedure(
                'public.canonical_domain_json_string(pg_catalog.text)'
              )
            )
              AND function_row.prokind = 'f'
              AND pg_catalog.pg_get_userbyid(function_row.proowner) = 'trading_owner'
              AND NOT function_row.prosecdef
              AND function_row.proisstrict
              AND function_row.provolatile = 'i'
              AND function_row.proparallel = 'u'
              AND function_row.prolang = (
                SELECT language_row.oid
                FROM pg_catalog.pg_language AS language_row
                WHERE language_row.lanname = 'plpgsql'
              )
              AND function_row.prorettype = 'pg_catalog.text'::pg_catalog.regtype
              AND function_row.proconfig =
                    ARRAY['search_path=pg_catalog, public']::pg_catalog.text[]
              AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                    function_row.prosrc, 'UTF8'
                  )), 'hex') = CASE function_row.oid
                WHEN pg_catalog.to_regprocedure(
                  'public.canonical_domain_json(pg_catalog.jsonb)'
                ) THEN '6dfdd6d74df0ee7300bc543788d9f58f5161c80df586ae087cdc5514a8ddf1ed'
                WHEN pg_catalog.to_regprocedure(
                  'public.canonical_domain_json_string(pg_catalog.text)'
                ) THEN 'c1165db17b4f6aead5fdbdd5dfffe34dbbb02390bba189a54046358ffcf732d1'
              END
              AND (
                SELECT pg_catalog.count(*) = 1
                  AND pg_catalog.bool_and(
                    acl.grantee = function_row.proowner
                    AND acl.privilege_type = 'EXECUTE'
                    AND NOT acl.is_grantable
                  )
                FROM pg_catalog.aclexplode(coalesce(
                  function_row.proacl,
                  pg_catalog.acldefault('f', function_row.proowner)
                )) AS acl
              )
          ) <> 2 THEN
            RAISE EXCEPTION 'canonical helper authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
        END;
        $p2_security_master_preflight$;

        CREATE TABLE public.security_master_identities (
          identity_id uuid PRIMARY KEY,
          identity_kind varchar(32) NOT NULL CHECK (
            identity_kind IN (
              'ISSUER', 'ASSET', 'SECURITY', 'VENUE', 'LISTING',
              'SYMBOL_MAPPING', 'CORPORATE_ACTION'
            )
          ),
          created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          UNIQUE (identity_id, identity_kind)
        );

        CREATE TABLE public.security_master_revisions (
          revision_id uuid PRIMARY KEY,
          fact_id uuid NOT NULL,
          subject_id uuid NOT NULL,
          subject_kind varchar(32) NOT NULL,
          revision_ordinal bigint NOT NULL CHECK (revision_ordinal BETWEEN 1 AND 4096),
          operation varchar(8) NOT NULL CHECK (operation IN ('ASSERT', 'RETRACT')),
          effective_from timestamptz NOT NULL,
          effective_to timestamptz,
          known_at timestamptz NOT NULL,
          supersedes_revision_id uuid,
          lookup_provider varchar(64),
          lookup_symbol varchar(128),
          related_security_id uuid,
          canonical_revision jsonb NOT NULL,
          canonical_revision_text text NOT NULL,
          revision_digest char(64) NOT NULL,
          recorded_at timestamptz NOT NULL,
          CONSTRAINT security_master_subject_fkey
            FOREIGN KEY (subject_id, subject_kind)
            REFERENCES public.security_master_identities(identity_id, identity_kind)
            ON DELETE RESTRICT,
          CONSTRAINT security_master_related_security_fkey
            FOREIGN KEY (related_security_id)
            REFERENCES public.security_master_identities(identity_id)
            ON DELETE RESTRICT,
          CONSTRAINT security_master_revision_identity_unique
            UNIQUE (revision_id, fact_id, subject_id, subject_kind),
          CONSTRAINT security_master_predecessor_fkey
            FOREIGN KEY (
              supersedes_revision_id, fact_id, subject_id, subject_kind
            ) REFERENCES public.security_master_revisions(
              revision_id, fact_id, subject_id, subject_kind
            ) ON DELETE RESTRICT,
          UNIQUE (fact_id, revision_ordinal),
          UNIQUE (revision_digest),
          CHECK (effective_to IS NULL OR effective_from < effective_to),
          CHECK (known_at <= recorded_at),
          CHECK (
            (supersedes_revision_id IS NULL AND revision_ordinal = 1 AND operation = 'ASSERT')
            OR (supersedes_revision_id IS NOT NULL AND revision_ordinal > 1)
          ),
          CHECK (
            (subject_kind = 'SYMBOL_MAPPING' AND lookup_provider IS NOT NULL AND lookup_symbol IS NOT NULL)
            OR (subject_kind <> 'SYMBOL_MAPPING' AND lookup_provider IS NULL AND lookup_symbol IS NULL)
          ),
          CHECK (
            (subject_kind = 'CORPORATE_ACTION' AND related_security_id IS NOT NULL AND effective_to IS NULL)
            OR (subject_kind <> 'CORPORATE_ACTION' AND related_security_id IS NULL)
          ),
          CHECK (
            (operation = 'ASSERT' AND pg_catalog.jsonb_typeof(canonical_revision -> 'payload') = 'object')
            OR (operation = 'RETRACT' AND pg_catalog.jsonb_typeof(canonical_revision -> 'payload') = 'null')
          ),
          CHECK (pg_catalog.octet_length(canonical_revision_text) <= 1048576),
          CHECK (canonical_revision_text::jsonb = canonical_revision),
          CHECK (
            canonical_revision_text = public.canonical_domain_json_string(canonical_revision_text)
          ),
          CHECK (revision_digest ~ '^[0-9a-f]{64}$'),
          CHECK (
            revision_digest = pg_catalog.encode(
              pg_catalog.sha256(pg_catalog.convert_to(canonical_revision_text, 'UTF8')),
              'hex'
            )
          ),
          CHECK ((canonical_revision ->> 'revision_id')::uuid = revision_id),
          CHECK ((canonical_revision ->> 'fact_id')::uuid = fact_id),
          CHECK ((canonical_revision ->> 'subject_id')::uuid = subject_id),
          CHECK (canonical_revision ->> 'subject_kind' = subject_kind),
          CHECK ((canonical_revision ->> 'revision_ordinal')::bigint = revision_ordinal),
          CHECK (canonical_revision ->> 'operation' = operation),
          CHECK ((canonical_revision ->> 'effective_from')::timestamptz = effective_from),
          CHECK (
            (effective_to IS NULL AND pg_catalog.jsonb_typeof(canonical_revision -> 'effective_to') = 'null')
            OR (canonical_revision ->> 'effective_to')::timestamptz = effective_to
          ),
          CHECK ((canonical_revision ->> 'known_at')::timestamptz = known_at),
          CHECK (
            (supersedes_revision_id IS NULL AND pg_catalog.jsonb_typeof(canonical_revision -> 'supersedes_revision_id') = 'null')
            OR (canonical_revision ->> 'supersedes_revision_id')::uuid = supersedes_revision_id
          )
        );

        CREATE UNIQUE INDEX security_master_one_root_per_fact_uidx
          ON public.security_master_revisions(fact_id)
          WHERE supersedes_revision_id IS NULL;
        CREATE UNIQUE INDEX security_master_one_child_per_revision_uidx
          ON public.security_master_revisions(supersedes_revision_id)
          WHERE supersedes_revision_id IS NOT NULL;
        CREATE INDEX security_master_revisions_subject_pit_idx
          ON public.security_master_revisions(
            subject_kind, subject_id, fact_id, recorded_at DESC, revision_ordinal DESC
          );
        CREATE INDEX security_master_revisions_symbol_pit_idx
          ON public.security_master_revisions(
            lookup_provider, lookup_symbol, fact_id, recorded_at DESC, revision_ordinal DESC
          ) WHERE subject_kind = 'SYMBOL_MAPPING';
        CREATE INDEX security_master_revisions_action_pit_idx
          ON public.security_master_revisions(
            related_security_id, fact_id, recorded_at DESC, revision_ordinal DESC,
            effective_from
          ) WHERE subject_kind = 'CORPORATE_ACTION';
        CREATE INDEX security_master_revisions_export_idx
          ON public.security_master_revisions(recorded_at, revision_id);

        CREATE FUNCTION public.append_security_master_revision(
          p_canonical_revision_text text
        )
        RETURNS TABLE(revision_id uuid, revision_digest text, inserted boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $append_security_master_revision$
        DECLARE
          v_revision pg_catalog.jsonb;
          v_payload pg_catalog.jsonb;
          v_evidence pg_catalog.jsonb;
          v_reference pg_catalog.jsonb;
          v_locator pg_catalog.jsonb;
          v_total pg_catalog.int8;
          v_valid pg_catalog.int8;
          v_revision_id pg_catalog.uuid;
          v_fact_id pg_catalog.uuid;
          v_subject_id pg_catalog.uuid;
          v_subject_kind pg_catalog.text;
          v_revision_ordinal pg_catalog.int8;
          v_operation pg_catalog.text;
          v_effective_from pg_catalog.timestamptz;
          v_effective_to pg_catalog.timestamptz;
          v_known_at pg_catalog.timestamptz;
          v_recorded_at pg_catalog.timestamptz;
          v_latest_recorded_at pg_catalog.timestamptz;
          v_supersedes_revision_id pg_catalog.uuid;
          v_revision_digest pg_catalog.text;
          v_lookup_provider pg_catalog.text;
          v_lookup_symbol pg_catalog.text;
          v_related_security_id pg_catalog.uuid;
          v_payload_identity pg_catalog.uuid;
          v_evidence_known_at pg_catalog.timestamptz;
          v_evidence_observed_at pg_catalog.timestamptz;
          v_evidence_fetched_at pg_catalog.timestamptz;
          v_max_evidence_known_at pg_catalog.timestamptz;
          v_evidence_id pg_catalog.text;
          v_previous_evidence_id pg_catalog.text;
          v_locator_component pg_catalog.text;
          v_locator_normalized pg_catalog.text;
          v_locator_candidate pg_catalog.text;
          v_locator_candidates pg_catalog.text[];
          v_locator_all pg_catalog.text;
          v_locator_paths pg_catalog.text;
          v_existing_text pg_catalog.text;
          v_existing_digest pg_catalog.text;
          v_parent public.security_master_revisions%ROWTYPE;
          v_timestamp_pattern constant pg_catalog.text :=
            '^[0-9]{4}-[0-9]{2}-[0-9]{2}T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{6})?Z$';
          v_uuid_pattern constant pg_catalog.text :=
            '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
          v_token_pattern constant pg_catalog.text :=
            '^[A-Z0-9][A-Z0-9._-]{0,63}$';
          v_version_pattern constant pg_catalog.text :=
            '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$';
          v_positive_decimal_pattern constant pg_catalog.text :=
            '^([1-9][0-9]*|(0|[1-9][0-9]*)\.[0-9]*[1-9])$';
        BEGIN
          IF pg_catalog.current_setting('transaction_isolation', false)
               IS DISTINCT FROM 'read committed' THEN
            RAISE EXCEPTION 'security-master append requires read committed isolation'
              USING ERRCODE = 'P2S04';
          END IF;
          IF p_canonical_revision_text IS NULL
             OR pg_catalog.octet_length(p_canonical_revision_text) > 1048576 THEN
            RAISE EXCEPTION 'canonical revision exceeds the supported bound'
              USING ERRCODE = 'P2S04';
          END IF;
          BEGIN
            v_revision := p_canonical_revision_text::pg_catalog.jsonb;
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'canonical revision JSON is invalid'
              USING ERRCODE = 'P2S04';
          END;
          IF p_canonical_revision_text IS DISTINCT FROM
               public.canonical_domain_json_string(p_canonical_revision_text)
             OR pg_catalog.jsonb_typeof(v_revision) <> 'object' THEN
            RAISE EXCEPTION 'canonical revision encoding is invalid'
              USING ERRCODE = 'P2S04';
          END IF;
          SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
            WHERE key = ANY (ARRAY[
              'schema_version', 'revision_id', 'fact_id', 'subject_id',
              'subject_kind', 'revision_ordinal', 'operation', 'effective_from',
              'effective_to', 'known_at', 'supersedes_revision_id', 'evidence',
              'payload'
            ]::pg_catalog.text[])
          ) INTO v_total, v_valid
          FROM pg_catalog.jsonb_object_keys(v_revision) AS keys(key);
          IF v_total <> 13 OR v_valid <> 13
             OR v_revision ->> 'schema_version' <> 'security-master-revision-v1'
             OR pg_catalog.jsonb_typeof(v_revision -> 'revision_id') <> 'string'
             OR pg_catalog.jsonb_typeof(v_revision -> 'fact_id') <> 'string'
             OR pg_catalog.jsonb_typeof(v_revision -> 'subject_id') <> 'string'
             OR pg_catalog.jsonb_typeof(v_revision -> 'subject_kind') <> 'string'
             OR pg_catalog.jsonb_typeof(v_revision -> 'revision_ordinal') <> 'number'
             OR pg_catalog.jsonb_typeof(v_revision -> 'operation') <> 'string'
             OR pg_catalog.jsonb_typeof(v_revision -> 'effective_from') <> 'string'
             OR pg_catalog.jsonb_typeof(v_revision -> 'known_at') <> 'string'
             OR pg_catalog.jsonb_typeof(v_revision -> 'evidence') <> 'array' THEN
            RAISE EXCEPTION 'canonical revision shape is invalid'
              USING ERRCODE = 'P2S04';
          END IF;
          IF v_revision ->> 'revision_id' !~ v_uuid_pattern
             OR v_revision ->> 'fact_id' !~ v_uuid_pattern
             OR v_revision ->> 'subject_id' !~ v_uuid_pattern
             OR v_revision ->> 'revision_ordinal' !~ '^[1-9][0-9]{0,3}$'
             OR v_revision ->> 'effective_from' !~ v_timestamp_pattern
             OR v_revision ->> 'known_at' !~ v_timestamp_pattern
             OR v_revision ->> 'effective_from' ~ '\.000000Z$'
             OR v_revision ->> 'known_at' ~ '\.000000Z$' THEN
            RAISE EXCEPTION 'canonical revision scalar spelling is invalid'
              USING ERRCODE = 'P2S04';
          END IF;
          v_revision_id := (v_revision ->> 'revision_id')::pg_catalog.uuid;
          v_fact_id := (v_revision ->> 'fact_id')::pg_catalog.uuid;
          v_subject_id := (v_revision ->> 'subject_id')::pg_catalog.uuid;
          v_subject_kind := v_revision ->> 'subject_kind';
          v_revision_ordinal := (v_revision ->> 'revision_ordinal')::pg_catalog.int8;
          v_operation := v_revision ->> 'operation';
          v_effective_from := (v_revision ->> 'effective_from')::pg_catalog.timestamptz;
          v_known_at := (v_revision ->> 'known_at')::pg_catalog.timestamptz;
          IF pg_catalog.jsonb_typeof(v_revision -> 'effective_to') = 'null' THEN
            v_effective_to := NULL;
          ELSIF pg_catalog.jsonb_typeof(v_revision -> 'effective_to') = 'string'
                AND v_revision ->> 'effective_to' ~ v_timestamp_pattern
                AND v_revision ->> 'effective_to' !~ '\.000000Z$' THEN
            v_effective_to := (v_revision ->> 'effective_to')::pg_catalog.timestamptz;
          ELSE
            RAISE EXCEPTION 'canonical revision effective_to is invalid'
              USING ERRCODE = 'P2S04';
          END IF;
          IF pg_catalog.jsonb_typeof(v_revision -> 'supersedes_revision_id') = 'null' THEN
            v_supersedes_revision_id := NULL;
          ELSIF pg_catalog.jsonb_typeof(v_revision -> 'supersedes_revision_id') = 'string'
                AND v_revision ->> 'supersedes_revision_id' ~ v_uuid_pattern THEN
            v_supersedes_revision_id :=
              (v_revision ->> 'supersedes_revision_id')::pg_catalog.uuid;
          ELSE
            RAISE EXCEPTION 'canonical revision predecessor is invalid'
              USING ERRCODE = 'P2S04';
          END IF;
          IF v_subject_kind NOT IN (
               'ISSUER', 'ASSET', 'SECURITY', 'VENUE', 'LISTING',
               'SYMBOL_MAPPING', 'CORPORATE_ACTION'
             )
             OR v_operation NOT IN ('ASSERT', 'RETRACT')
             OR v_revision_ordinal NOT BETWEEN 1 AND 4096
             OR (v_effective_to IS NOT NULL AND v_effective_to <= v_effective_from) THEN
            RAISE EXCEPTION 'canonical revision domain scalar is invalid'
              USING ERRCODE = 'P2S04';
          END IF;

          IF pg_catalog.jsonb_array_length(v_revision -> 'evidence') NOT BETWEEN 1 AND 16 THEN
            RAISE EXCEPTION 'canonical revision evidence count is invalid'
              USING ERRCODE = 'P2S04';
          END IF;
          FOR v_evidence IN
            SELECT item FROM pg_catalog.jsonb_array_elements(v_revision -> 'evidence') AS entries(item)
          LOOP
            IF pg_catalog.jsonb_typeof(v_evidence) <> 'object' THEN
              RAISE EXCEPTION 'canonical revision evidence is invalid'
                USING ERRCODE = 'P2S04';
            END IF;
            SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
              WHERE key = ANY (ARRAY[
                'schema_version', 'reference', 'fetched_at', 'known_at',
                'content_sha256', 'media_type', 'source_revision',
                'normalization_version'
              ]::pg_catalog.text[])
            ) INTO v_total, v_valid
            FROM pg_catalog.jsonb_object_keys(v_evidence) AS keys(key);
            v_reference := v_evidence -> 'reference';
            IF v_total <> 8 OR v_valid <> 8
               OR v_evidence ->> 'schema_version' <> 'security-master-evidence-v1'
               OR pg_catalog.jsonb_typeof(v_evidence -> 'schema_version') <> 'string'
               OR pg_catalog.jsonb_typeof(v_reference) <> 'object'
               OR pg_catalog.jsonb_typeof(v_evidence -> 'fetched_at') <> 'string'
               OR pg_catalog.jsonb_typeof(v_evidence -> 'known_at') <> 'string'
               OR pg_catalog.jsonb_typeof(v_evidence -> 'content_sha256') <> 'string'
               OR pg_catalog.jsonb_typeof(v_evidence -> 'media_type') <> 'string'
               OR pg_catalog.jsonb_typeof(v_evidence -> 'source_revision') <> 'string'
               OR pg_catalog.jsonb_typeof(v_evidence -> 'normalization_version') <> 'string'
               OR v_evidence ->> 'fetched_at' !~ v_timestamp_pattern
               OR v_evidence ->> 'known_at' !~ v_timestamp_pattern
               OR v_evidence ->> 'fetched_at' ~ '\.000000Z$'
               OR v_evidence ->> 'known_at' ~ '\.000000Z$'
               OR v_evidence ->> 'content_sha256' !~ '^[0-9a-f]{64}$'
               OR v_evidence ->> 'source_revision' !~ v_version_pattern
               OR v_evidence ->> 'normalization_version' !~ v_version_pattern
               OR v_evidence ->> 'media_type' NOT IN (
                    'application/json', 'application/pdf', 'text/csv',
                    'text/html', 'text/plain'
                  ) THEN
              RAISE EXCEPTION 'canonical revision evidence descriptor is invalid'
                USING ERRCODE = 'P2S04';
            END IF;
            SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
              WHERE key = ANY (ARRAY[
                'evidence_id', 'source', 'locator', 'observed_at', 'schema_version'
              ]::pg_catalog.text[])
            ) INTO v_total, v_valid
            FROM pg_catalog.jsonb_object_keys(v_reference) AS keys(key);
            v_evidence_id := v_reference ->> 'evidence_id';
            IF v_total <> 5 OR v_valid <> 5
               OR pg_catalog.jsonb_typeof(v_reference -> 'evidence_id') <> 'string'
               OR pg_catalog.jsonb_typeof(v_reference -> 'source') <> 'string'
               OR pg_catalog.jsonb_typeof(v_reference -> 'locator') <> 'object'
               OR pg_catalog.jsonb_typeof(v_reference -> 'observed_at') <> 'string'
               OR pg_catalog.jsonb_typeof(v_reference -> 'schema_version') <> 'string'
               OR v_evidence_id !~ v_uuid_pattern
               OR v_reference ->> 'source' NOT IN (
                    'market_data', 'news', 'filing', 'on_chain', 'research'
                  )
               OR v_reference ->> 'observed_at' !~ v_timestamp_pattern
               OR v_reference ->> 'observed_at' ~ '\.000000Z$'
               OR v_reference ->> 'schema_version' !~ v_version_pattern THEN
              RAISE EXCEPTION 'canonical revision evidence reference is invalid'
                USING ERRCODE = 'P2S04';
            END IF;
            v_locator := v_reference -> 'locator';
            SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
              WHERE key = ANY (ARRAY[
                'kind', 'authority', 'path'
              ]::pg_catalog.text[])
            ) INTO v_total, v_valid
            FROM pg_catalog.jsonb_object_keys(v_locator) AS keys(key);
            IF v_total <> 3 OR v_valid <> 3
               OR pg_catalog.jsonb_typeof(v_locator -> 'kind') <> 'string'
               OR pg_catalog.jsonb_typeof(v_locator -> 'authority') <> 'string'
               OR pg_catalog.jsonb_typeof(v_locator -> 'path') <> 'array'
               OR v_locator ->> 'kind' NOT IN ('https', 'dataset', 'document', 'block')
               OR v_locator ->> 'authority' !~
                    '^[a-z0-9]([a-z0-9.-]{0,126}[a-z0-9])?$' THEN
              RAISE EXCEPTION 'canonical revision evidence locator is invalid'
                USING ERRCODE = 'P2S04';
            END IF;
            IF pg_catalog.jsonb_array_length(v_locator -> 'path') NOT BETWEEN 1 AND 16 THEN
              RAISE EXCEPTION 'canonical revision evidence locator is invalid'
                USING ERRCODE = 'P2S04';
            END IF;
            IF EXISTS (
              SELECT 1
              FROM pg_catalog.jsonb_array_elements(
                v_locator -> 'path'
              ) AS entries(item)
              WHERE pg_catalog.jsonb_typeof(item) <> 'string'
            ) THEN
              RAISE EXCEPTION 'canonical revision evidence locator path is invalid'
                USING ERRCODE = 'P2S04';
            END IF;
            v_locator_normalized := pg_catalog.regexp_replace(
              pg_catalog.lower(v_locator ->> 'authority'), '[^a-z0-9]', '', 'g'
            );
            v_locator_candidates :=
              ARRAY[v_locator_normalized]::pg_catalog.text[];
            v_locator_all := v_locator_normalized;
            v_locator_paths := '';
            FOR v_locator_component IN
              SELECT item
              FROM pg_catalog.jsonb_array_elements_text(
                v_locator -> 'path'
              ) AS entries(item)
            LOOP
              IF v_locator_component !~ '^[A-Za-z0-9][A-Za-z0-9._~-]{0,63}$' THEN
                RAISE EXCEPTION 'canonical revision evidence locator path is invalid'
                  USING ERRCODE = 'P2S04';
              END IF;
              v_locator_normalized := pg_catalog.regexp_replace(
                pg_catalog.lower(v_locator_component), '[^a-z0-9]', '', 'g'
              );
              v_locator_candidates := pg_catalog.array_append(
                v_locator_candidates, v_locator_normalized
              );
              v_locator_all := v_locator_all || v_locator_normalized;
              v_locator_paths := v_locator_paths || v_locator_normalized;
            END LOOP;
            v_locator_candidates := pg_catalog.array_append(
              pg_catalog.array_append(v_locator_candidates, v_locator_all),
              v_locator_paths
            );
            FOREACH v_locator_candidate IN ARRAY v_locator_candidates LOOP
              IF pg_catalog.strpos(v_locator_candidate, 'credential') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'apikey') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'password') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'accountid') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'accountnumber') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'accountrouting') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'routingnumber') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'ordertype') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'executioninstruction') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'executiontext') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'brokeraccount') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'apisecret') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'clientsecret') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'apitoken') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'accesstoken') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'authtoken') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'bearertoken') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'sessiontoken') > 0
                 OR pg_catalog.strpos(
                      pg_catalog.replace(v_locator_candidate, 'tokeniz', '#'),
                      'token'
                    ) > 0
                 OR pg_catalog.strpos(
                      pg_catalog.replace(v_locator_candidate, 'secretar', '#'),
                      'secret'
                    ) > 0
                 OR pg_catalog.strpos(
                      pg_catalog.replace(v_locator_candidate, 'accounting', '#'),
                      'account'
                    ) > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'routing') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'execution') > 0
                 OR pg_catalog.strpos(v_locator_candidate, 'execute') > 0 THEN
                RAISE EXCEPTION 'canonical revision evidence locator path is invalid'
                  USING ERRCODE = 'P2S04';
              END IF;
            END LOOP;
            IF v_previous_evidence_id IS NOT NULL
               AND v_evidence_id <= v_previous_evidence_id THEN
              RAISE EXCEPTION 'canonical revision evidence must be unique and sorted'
                USING ERRCODE = 'P2S04';
            END IF;
            v_previous_evidence_id := v_evidence_id;
            v_evidence_observed_at :=
              (v_reference ->> 'observed_at')::pg_catalog.timestamptz;
            v_evidence_fetched_at :=
              (v_evidence ->> 'fetched_at')::pg_catalog.timestamptz;
            v_evidence_known_at :=
              (v_evidence ->> 'known_at')::pg_catalog.timestamptz;
            IF NOT (
              v_evidence_observed_at <= v_evidence_fetched_at
              AND v_evidence_fetched_at <= v_evidence_known_at
              AND v_evidence_known_at <= v_known_at
            ) THEN
              RAISE EXCEPTION 'canonical revision evidence time is invalid'
                USING ERRCODE = 'P2S04';
            END IF;
            v_max_evidence_known_at := greatest(
              v_max_evidence_known_at, v_evidence_known_at
            );
          END LOOP;
          IF v_known_at IS DISTINCT FROM v_max_evidence_known_at THEN
            RAISE EXCEPTION 'revision known_at must equal maximum evidence known_at'
              USING ERRCODE = 'P2S04';
          END IF;

          v_payload := v_revision -> 'payload';
          IF v_operation = 'RETRACT' THEN
            IF pg_catalog.jsonb_typeof(v_payload) <> 'null' OR v_revision_ordinal = 1 THEN
              RAISE EXCEPTION 'RETRACT revision shape is invalid'
                USING ERRCODE = 'P2S04';
            END IF;
          ELSIF pg_catalog.jsonb_typeof(v_payload) <> 'object' THEN
            RAISE EXCEPTION 'ASSERT payload is invalid'
              USING ERRCODE = 'P2S04';
          ELSE
            IF v_subject_kind = 'ISSUER' THEN
              SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
                WHERE key = ANY (ARRAY[
                  'issuer_id', 'legal_name', 'jurisdiction'
                ]::pg_catalog.text[])
              ) INTO v_total, v_valid
              FROM pg_catalog.jsonb_object_keys(v_payload) AS keys(key);
              IF v_total <> 3 OR v_valid <> 3
                 OR pg_catalog.jsonb_typeof(v_payload -> 'issuer_id') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'legal_name') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'jurisdiction') <> 'string'
                 OR v_payload ->> 'issuer_id' !~ v_uuid_pattern
                 OR v_payload ->> 'legal_name' !~ '^[ -~]{1,256}$'
                 OR v_payload ->> 'legal_name' IS DISTINCT FROM
                      pg_catalog.btrim(v_payload ->> 'legal_name')
                 OR v_payload ->> 'jurisdiction' !~ v_token_pattern THEN
                RAISE EXCEPTION 'ISSUER payload is invalid' USING ERRCODE = 'P2S04';
              END IF;
              v_payload_identity := (v_payload ->> 'issuer_id')::pg_catalog.uuid;
            ELSIF v_subject_kind = 'ASSET' THEN
              SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
                WHERE key = ANY (ARRAY[
                  'asset_id', 'code', 'asset_kind', 'issuer_id'
                ]::pg_catalog.text[])
              ) INTO v_total, v_valid
              FROM pg_catalog.jsonb_object_keys(v_payload) AS keys(key);
              IF v_total <> 4 OR v_valid <> 4
                 OR pg_catalog.jsonb_typeof(v_payload -> 'asset_id') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'code') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'asset_kind') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'issuer_id') <> 'string'
                 OR v_payload ->> 'asset_id' !~ v_uuid_pattern
                 OR v_payload ->> 'issuer_id' !~ v_uuid_pattern
                 OR v_payload ->> 'code' !~ v_token_pattern
                 OR v_payload ->> 'asset_kind' NOT IN ('FIAT', 'CRYPTO', 'EQUITY') THEN
                RAISE EXCEPTION 'ASSET payload is invalid' USING ERRCODE = 'P2S04';
              END IF;
              v_payload_identity := (v_payload ->> 'asset_id')::pg_catalog.uuid;
            ELSIF v_subject_kind = 'SECURITY' THEN
              SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
                WHERE key = ANY (ARRAY[
                  'security_id', 'product_type', 'primary_asset_id'
                ]::pg_catalog.text[])
              ) INTO v_total, v_valid
              FROM pg_catalog.jsonb_object_keys(v_payload) AS keys(key);
              IF v_total <> 3 OR v_valid <> 3
                 OR pg_catalog.jsonb_typeof(v_payload -> 'security_id') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'product_type') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'primary_asset_id') <> 'string'
                 OR v_payload ->> 'security_id' !~ v_uuid_pattern
                 OR v_payload ->> 'primary_asset_id' !~ v_uuid_pattern
                 OR v_payload ->> 'product_type' NOT IN ('crypto_spot', 'equity') THEN
                RAISE EXCEPTION 'SECURITY payload is invalid' USING ERRCODE = 'P2S04';
              END IF;
              v_payload_identity := (v_payload ->> 'security_id')::pg_catalog.uuid;
            ELSIF v_subject_kind = 'VENUE' THEN
              SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
                WHERE key = ANY (ARRAY[
                  'venue_id', 'code', 'mic', 'timezone'
                ]::pg_catalog.text[])
              ) INTO v_total, v_valid
              FROM pg_catalog.jsonb_object_keys(v_payload) AS keys(key);
              IF v_total <> 4 OR v_valid <> 4
                 OR pg_catalog.jsonb_typeof(v_payload -> 'venue_id') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'code') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'mic') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'timezone') <> 'string'
                 OR v_payload ->> 'venue_id' !~ v_uuid_pattern
                 OR v_payload ->> 'code' !~ v_token_pattern
                 OR v_payload ->> 'mic' !~ '^[A-Z0-9]{4}$'
                 OR v_payload ->> 'timezone' !~ '^[A-Za-z0-9_+./-]{1,64}$' THEN
                RAISE EXCEPTION 'VENUE payload is invalid' USING ERRCODE = 'P2S04';
              END IF;
              v_payload_identity := (v_payload ->> 'venue_id')::pg_catalog.uuid;
            ELSIF v_subject_kind = 'LISTING' THEN
              SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
                WHERE key = ANY (ARRAY[
                  'listing_id', 'security_id', 'venue_id', 'quote_asset_id',
                  'session_calendar', 'tick_size', 'size_increment',
                  'minimum_quantity', 'maximum_quantity', 'minimum_notional',
                  'maximum_notional'
                ]::pg_catalog.text[])
              ) INTO v_total, v_valid
              FROM pg_catalog.jsonb_object_keys(v_payload) AS keys(key);
              IF v_total <> 11 OR v_valid <> 11
                 OR pg_catalog.jsonb_typeof(v_payload -> 'listing_id') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'security_id') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'venue_id') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'quote_asset_id') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'session_calendar') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'tick_size') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'size_increment') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'minimum_quantity') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'maximum_quantity') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'minimum_notional') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'maximum_notional') <> 'string'
                 OR v_payload ->> 'listing_id' !~ v_uuid_pattern
                 OR v_payload ->> 'security_id' !~ v_uuid_pattern
                 OR v_payload ->> 'venue_id' !~ v_uuid_pattern
                 OR v_payload ->> 'quote_asset_id' !~ v_uuid_pattern
                 OR v_payload ->> 'session_calendar' !~ v_token_pattern
                 OR v_payload ->> 'tick_size' !~ v_positive_decimal_pattern
                 OR v_payload ->> 'size_increment' !~ v_positive_decimal_pattern
                 OR v_payload ->> 'minimum_quantity' !~ v_positive_decimal_pattern
                 OR v_payload ->> 'maximum_quantity' !~ v_positive_decimal_pattern
                 OR v_payload ->> 'minimum_notional' !~ v_positive_decimal_pattern
                 OR v_payload ->> 'maximum_notional' !~ v_positive_decimal_pattern
                 OR pg_catalog.length(v_payload ->> 'tick_size') > 128
                 OR pg_catalog.length(v_payload ->> 'size_increment') > 128
                 OR pg_catalog.length(v_payload ->> 'minimum_quantity') > 128
                 OR pg_catalog.length(v_payload ->> 'maximum_quantity') > 128
                 OR pg_catalog.length(v_payload ->> 'minimum_notional') > 128
                 OR pg_catalog.length(v_payload ->> 'maximum_notional') > 128
                 OR (v_payload ->> 'minimum_quantity')::pg_catalog.numeric >
                      (v_payload ->> 'maximum_quantity')::pg_catalog.numeric
                 OR (v_payload ->> 'minimum_notional')::pg_catalog.numeric >
                      (v_payload ->> 'maximum_notional')::pg_catalog.numeric THEN
                RAISE EXCEPTION 'LISTING payload is invalid' USING ERRCODE = 'P2S04';
              END IF;
              v_payload_identity := (v_payload ->> 'listing_id')::pg_catalog.uuid;
            ELSIF v_subject_kind = 'SYMBOL_MAPPING' THEN
              SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
                WHERE key = ANY (ARRAY[
                  'mapping_id', 'provider', 'raw_symbol', 'canonical_symbol',
                  'listing_id'
                ]::pg_catalog.text[])
              ) INTO v_total, v_valid
              FROM pg_catalog.jsonb_object_keys(v_payload) AS keys(key);
              IF v_total <> 5 OR v_valid <> 5
                 OR pg_catalog.jsonb_typeof(v_payload -> 'mapping_id') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'provider') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'raw_symbol') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'canonical_symbol') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'listing_id') <> 'string'
                 OR v_payload ->> 'mapping_id' !~ v_uuid_pattern
                 OR v_payload ->> 'listing_id' !~ v_uuid_pattern
                 OR v_payload ->> 'provider' !~ v_token_pattern
                 OR v_payload ->> 'canonical_symbol' !~ v_token_pattern
                 OR v_payload ->> 'raw_symbol' !~ '^[ -~]{1,128}$'
                 OR v_payload ->> 'raw_symbol' IS DISTINCT FROM
                      pg_catalog.btrim(v_payload ->> 'raw_symbol') THEN
                RAISE EXCEPTION 'SYMBOL_MAPPING payload is invalid' USING ERRCODE = 'P2S04';
              END IF;
              v_payload_identity := (v_payload ->> 'mapping_id')::pg_catalog.uuid;
              v_lookup_provider := v_payload ->> 'provider';
              v_lookup_symbol := v_payload ->> 'raw_symbol';
            ELSE
              IF v_effective_to IS NOT NULL
                 OR pg_catalog.jsonb_typeof(v_payload -> 'action_id') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'security_id') <> 'string'
                 OR pg_catalog.jsonb_typeof(v_payload -> 'action_type') <> 'string'
                 OR v_payload ->> 'action_id' !~ v_uuid_pattern
                 OR v_payload ->> 'security_id' !~ v_uuid_pattern
                 OR v_payload ->> 'action_type' NOT IN (
                      'SPLIT', 'CASH_DIVIDEND', 'SYMBOL_CHANGE', 'DELISTING'
                    ) THEN
                RAISE EXCEPTION 'CORPORATE_ACTION payload is invalid' USING ERRCODE = 'P2S04';
              END IF;
              IF v_payload ->> 'action_type' = 'SPLIT' THEN
                SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
                  WHERE key = ANY (ARRAY[
                    'action_id', 'security_id', 'action_type', 'new_units',
                    'old_units'
                  ]::pg_catalog.text[])
                ) INTO v_total, v_valid
                FROM pg_catalog.jsonb_object_keys(v_payload) AS keys(key);
                IF v_total <> 5 OR v_valid <> 5
                   OR pg_catalog.jsonb_typeof(v_payload -> 'new_units') <> 'string'
                   OR pg_catalog.jsonb_typeof(v_payload -> 'old_units') <> 'string'
                   OR v_payload ->> 'new_units' !~ v_positive_decimal_pattern
                   OR v_payload ->> 'old_units' !~ v_positive_decimal_pattern
                   OR pg_catalog.length(v_payload ->> 'new_units') > 128
                   OR pg_catalog.length(v_payload ->> 'old_units') > 128 THEN
                  RAISE EXCEPTION 'SPLIT payload is invalid' USING ERRCODE = 'P2S04';
                END IF;
              ELSIF v_payload ->> 'action_type' = 'CASH_DIVIDEND' THEN
                SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
                  WHERE key = ANY (ARRAY[
                    'action_id', 'security_id', 'action_type', 'amount',
                    'currency_asset_id'
                  ]::pg_catalog.text[])
                ) INTO v_total, v_valid
                FROM pg_catalog.jsonb_object_keys(v_payload) AS keys(key);
                IF v_total <> 5 OR v_valid <> 5
                   OR pg_catalog.jsonb_typeof(v_payload -> 'amount') <> 'string'
                   OR pg_catalog.jsonb_typeof(v_payload -> 'currency_asset_id') <> 'string'
                   OR v_payload ->> 'amount' !~ v_positive_decimal_pattern
                   OR pg_catalog.length(v_payload ->> 'amount') > 128
                   OR v_payload ->> 'currency_asset_id' !~ v_uuid_pattern THEN
                  RAISE EXCEPTION 'CASH_DIVIDEND payload is invalid' USING ERRCODE = 'P2S04';
                END IF;
              ELSIF v_payload ->> 'action_type' = 'SYMBOL_CHANGE' THEN
                SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
                  WHERE key = ANY (ARRAY[
                    'action_id', 'security_id', 'action_type', 'old_mapping_id',
                    'new_mapping_id'
                  ]::pg_catalog.text[])
                ) INTO v_total, v_valid
                FROM pg_catalog.jsonb_object_keys(v_payload) AS keys(key);
                IF v_total <> 5 OR v_valid <> 5
                   OR pg_catalog.jsonb_typeof(v_payload -> 'old_mapping_id') <> 'string'
                   OR pg_catalog.jsonb_typeof(v_payload -> 'new_mapping_id') <> 'string'
                   OR v_payload ->> 'old_mapping_id' !~ v_uuid_pattern
                   OR v_payload ->> 'new_mapping_id' !~ v_uuid_pattern
                   OR v_payload ->> 'old_mapping_id' = v_payload ->> 'new_mapping_id' THEN
                  RAISE EXCEPTION 'SYMBOL_CHANGE payload is invalid' USING ERRCODE = 'P2S04';
                END IF;
              ELSE
                SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
                  WHERE key = ANY (ARRAY[
                    'action_id', 'security_id', 'action_type', 'listing_id'
                  ]::pg_catalog.text[])
                ) INTO v_total, v_valid
                FROM pg_catalog.jsonb_object_keys(v_payload) AS keys(key);
                IF v_total <> 4 OR v_valid <> 4
                   OR pg_catalog.jsonb_typeof(v_payload -> 'listing_id') <> 'string'
                   OR v_payload ->> 'listing_id' !~ v_uuid_pattern THEN
                  RAISE EXCEPTION 'DELISTING payload is invalid' USING ERRCODE = 'P2S04';
                END IF;
              END IF;
              v_payload_identity := (v_payload ->> 'action_id')::pg_catalog.uuid;
              v_related_security_id := (v_payload ->> 'security_id')::pg_catalog.uuid;
            END IF;
            IF v_payload_identity IS DISTINCT FROM v_subject_id THEN
              RAISE EXCEPTION 'payload subject identity is invalid'
                USING ERRCODE = 'P2S04';
            END IF;
          END IF;

          v_revision_digest := pg_catalog.encode(
            pg_catalog.sha256(pg_catalog.convert_to(p_canonical_revision_text, 'UTF8')),
            'hex'
          );
          PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(v_revision_id::pg_catalog.text, 1901)
          );
          PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(v_subject_id::pg_catalog.text, 1902)
          );
          PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(v_fact_id::pg_catalog.text, 1903)
          );
          IF v_operation = 'RETRACT' THEN
            SELECT
              parent_revision.lookup_provider,
              parent_revision.lookup_symbol,
              parent_revision.related_security_id
            INTO v_lookup_provider, v_lookup_symbol, v_related_security_id
            FROM public.security_master_revisions AS parent_revision
            WHERE parent_revision.revision_id = v_supersedes_revision_id
              AND parent_revision.fact_id = v_fact_id
              AND parent_revision.subject_id = v_subject_id
              AND parent_revision.subject_kind = v_subject_kind;
          END IF;
          IF v_subject_kind = 'SYMBOL_MAPPING' THEN
            PERFORM pg_catalog.pg_advisory_xact_lock(
              pg_catalog.hashtextextended(
                coalesce(v_lookup_provider, '') || '|' ||
                coalesce(v_lookup_symbol, ''), 1904
              )
            );
          ELSIF v_subject_kind = 'CORPORATE_ACTION' THEN
            PERFORM pg_catalog.pg_advisory_xact_lock(
              pg_catalog.hashtextextended(
                coalesce(v_related_security_id::pg_catalog.text, ''), 1905
              )
            );
          END IF;
          -- ponytail: one global write clock keeps PIT visibility total-ordered;
          -- shard only after measured security-master ingest contention.
          PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended('security-master-record-clock', 1906)
          );

          SELECT
            existing_revision.canonical_revision_text,
            existing_revision.revision_digest
          INTO v_existing_text, v_existing_digest
          FROM public.security_master_revisions AS existing_revision
          WHERE existing_revision.revision_id = v_revision_id;
          IF FOUND THEN
            IF v_existing_text = p_canonical_revision_text
               AND v_existing_digest = v_revision_digest THEN
              RETURN QUERY SELECT v_revision_id, v_revision_digest, false;
              RETURN;
            END IF;
            RAISE EXCEPTION 'revision identity conflicts with existing content'
              USING ERRCODE = 'P2S01';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.security_master_revisions AS digest_revision
            WHERE digest_revision.revision_digest = v_revision_digest
          ) THEN
            RAISE EXCEPTION 'revision digest conflicts with another identity'
              USING ERRCODE = 'P2S01';
          END IF;
          v_recorded_at := pg_catalog.clock_timestamp();
          IF v_known_at > v_recorded_at THEN
            RAISE EXCEPTION 'revision evidence knowledge exceeds database record time'
              USING ERRCODE = 'P2S04';
          END IF;
          SELECT pg_catalog.max(recorded_at)
          INTO v_latest_recorded_at
          FROM public.security_master_revisions;
          IF v_latest_recorded_at IS NOT NULL
             AND v_recorded_at <= v_latest_recorded_at THEN
            RAISE EXCEPTION 'database knowledge time is not strictly increasing'
              USING ERRCODE = 'P2S02';
          END IF;

          IF v_supersedes_revision_id IS NULL THEN
            IF v_revision_ordinal <> 1 OR v_operation <> 'ASSERT' THEN
              RAISE EXCEPTION 'root lineage is invalid' USING ERRCODE = 'P2S02';
            END IF;
            IF EXISTS (
              SELECT 1
              FROM public.security_master_revisions AS root_revision
              WHERE root_revision.fact_id = v_fact_id
            ) THEN
              RAISE EXCEPTION 'fact already has a root revision'
                USING ERRCODE = 'P2S02';
            END IF;
            INSERT INTO public.security_master_identities(identity_id, identity_kind)
            VALUES (v_subject_id, v_subject_kind)
            ON CONFLICT (identity_id) DO NOTHING;
            IF NOT EXISTS (
              SELECT 1 FROM public.security_master_identities
              WHERE identity_id = v_subject_id AND identity_kind = v_subject_kind
            ) THEN
              RAISE EXCEPTION 'subject identity kind conflicts' USING ERRCODE = 'P2S01';
            END IF;
          ELSE
            SELECT parent_revision.* INTO v_parent
            FROM public.security_master_revisions AS parent_revision
            WHERE parent_revision.revision_id = v_supersedes_revision_id
            FOR UPDATE;
            IF NOT FOUND
               OR v_parent.fact_id <> v_fact_id
               OR v_parent.subject_id <> v_subject_id
               OR v_parent.subject_kind <> v_subject_kind THEN
              RAISE EXCEPTION 'revision predecessor identity is invalid'
                USING ERRCODE = 'P2S02';
            END IF;
            IF EXISTS (
              SELECT 1 FROM public.security_master_revisions
              WHERE supersedes_revision_id = v_parent.revision_id
            ) THEN
              RAISE EXCEPTION 'parent is not the current head'
                USING ERRCODE = 'P2S02';
            END IF;
            IF v_revision_ordinal <> v_parent.revision_ordinal + 1 THEN
              RAISE EXCEPTION 'revision ordinal is not contiguous'
                USING ERRCODE = 'P2S02';
            END IF;
            IF v_recorded_at <= v_parent.recorded_at THEN
              RAISE EXCEPTION 'database knowledge time is not strictly increasing'
                USING ERRCODE = 'P2S02';
            END IF;
            IF v_parent.operation = 'RETRACT' THEN
              RAISE EXCEPTION 'cannot append after RETRACT'
                USING ERRCODE = 'P2S02';
            END IF;
            IF v_operation = 'RETRACT' THEN
              v_lookup_provider := v_parent.lookup_provider;
              v_lookup_symbol := v_parent.lookup_symbol;
              v_related_security_id := v_parent.related_security_id;
              IF v_effective_from IS DISTINCT FROM v_parent.effective_from
                 OR v_effective_to IS DISTINCT FROM v_parent.effective_to THEN
                RAISE EXCEPTION 'RETRACT must repeat parent interval and lookup keys'
                  USING ERRCODE = 'P2S02';
              END IF;
            ELSIF v_lookup_provider IS DISTINCT FROM v_parent.lookup_provider
               OR v_lookup_symbol IS DISTINCT FROM v_parent.lookup_symbol
               OR v_related_security_id IS DISTINCT FROM v_parent.related_security_id THEN
              RAISE EXCEPTION 'revision lookup keys changed within a fact'
                USING ERRCODE = 'P2S02';
            END IF;
          END IF;

          IF v_operation = 'ASSERT' THEN
            IF v_subject_kind = 'ASSET' AND NOT EXISTS (
                 SELECT 1 FROM public.security_master_identities
                 WHERE identity_id = (v_payload ->> 'issuer_id')::pg_catalog.uuid
                   AND identity_kind = 'ISSUER'
               ) THEN
              RAISE EXCEPTION 'ASSET relation is invalid' USING ERRCODE = 'P2S04';
            ELSIF v_subject_kind = 'SECURITY' AND NOT EXISTS (
                 SELECT 1 FROM public.security_master_identities
                 WHERE identity_id = (v_payload ->> 'primary_asset_id')::pg_catalog.uuid
                   AND identity_kind = 'ASSET'
               ) THEN
              RAISE EXCEPTION 'SECURITY relation is invalid' USING ERRCODE = 'P2S04';
            ELSIF v_subject_kind = 'LISTING' AND (
              NOT EXISTS (
                SELECT 1 FROM public.security_master_identities
                WHERE identity_id = (v_payload ->> 'security_id')::pg_catalog.uuid
                  AND identity_kind = 'SECURITY'
              ) OR NOT EXISTS (
                SELECT 1 FROM public.security_master_identities
                WHERE identity_id = (v_payload ->> 'venue_id')::pg_catalog.uuid
                  AND identity_kind = 'VENUE'
              ) OR NOT EXISTS (
                SELECT 1 FROM public.security_master_identities
                WHERE identity_id = (v_payload ->> 'quote_asset_id')::pg_catalog.uuid
                  AND identity_kind = 'ASSET'
              )
            ) THEN
              RAISE EXCEPTION 'LISTING relation is invalid' USING ERRCODE = 'P2S04';
            ELSIF v_subject_kind = 'SYMBOL_MAPPING' AND NOT EXISTS (
              SELECT 1 FROM public.security_master_identities
              WHERE identity_id = (v_payload ->> 'listing_id')::pg_catalog.uuid
                AND identity_kind = 'LISTING'
            ) THEN
              RAISE EXCEPTION 'SYMBOL_MAPPING relation is invalid' USING ERRCODE = 'P2S04';
            ELSIF v_subject_kind = 'CORPORATE_ACTION' AND (
              NOT EXISTS (
                SELECT 1 FROM public.security_master_identities
                WHERE identity_id = v_related_security_id
                  AND identity_kind = 'SECURITY'
              ) OR (
                v_payload ->> 'action_type' = 'CASH_DIVIDEND'
                AND NOT EXISTS (
                  SELECT 1 FROM public.security_master_identities
                  WHERE identity_id =
                          (v_payload ->> 'currency_asset_id')::pg_catalog.uuid
                    AND identity_kind = 'ASSET'
                )
              ) OR (
                v_payload ->> 'action_type' = 'SYMBOL_CHANGE'
                AND (
                  NOT EXISTS (
                    SELECT 1 FROM public.security_master_identities
                    WHERE identity_id =
                            (v_payload ->> 'old_mapping_id')::pg_catalog.uuid
                      AND identity_kind = 'SYMBOL_MAPPING'
                  ) OR NOT EXISTS (
                    SELECT 1 FROM public.security_master_identities
                    WHERE identity_id =
                            (v_payload ->> 'new_mapping_id')::pg_catalog.uuid
                      AND identity_kind = 'SYMBOL_MAPPING'
                  )
                )
              ) OR (
                v_payload ->> 'action_type' = 'DELISTING'
                AND NOT EXISTS (
                  SELECT 1 FROM public.security_master_identities
                  WHERE identity_id = (v_payload ->> 'listing_id')::pg_catalog.uuid
                    AND identity_kind = 'LISTING'
                )
              )
            ) THEN
              RAISE EXCEPTION 'corporate action relation is invalid'
                USING ERRCODE = 'P2S04';
            END IF;
          END IF;

          IF v_operation = 'ASSERT' AND v_subject_kind NOT IN (
               'SYMBOL_MAPPING', 'CORPORATE_ACTION'
             ) AND EXISTS (
            SELECT 1 FROM public.security_master_revisions AS existing
            WHERE existing.subject_kind = v_subject_kind
              AND existing.subject_id = v_subject_id
              AND existing.fact_id <> v_fact_id
              AND existing.operation = 'ASSERT'
              AND NOT EXISTS (
                SELECT 1 FROM public.security_master_revisions AS child
                WHERE child.supersedes_revision_id = existing.revision_id
              )
              AND existing.effective_from < coalesce(
                    v_effective_to, 'infinity'::pg_catalog.timestamptz
                  )
              AND v_effective_from < coalesce(
                    existing.effective_to, 'infinity'::pg_catalog.timestamptz
                  )
          ) THEN
            RAISE EXCEPTION 'definition interval overlaps an active fact'
              USING ERRCODE = 'P2S03';
          END IF;
          IF v_operation = 'ASSERT' AND v_subject_kind = 'SYMBOL_MAPPING' AND EXISTS (
            SELECT 1 FROM public.security_master_revisions AS existing
            WHERE existing.subject_kind = 'SYMBOL_MAPPING'
              AND existing.lookup_provider = v_lookup_provider
              AND existing.lookup_symbol = v_lookup_symbol
              AND existing.fact_id <> v_fact_id
              AND existing.operation = 'ASSERT'
              AND NOT EXISTS (
                SELECT 1 FROM public.security_master_revisions AS child
                WHERE child.supersedes_revision_id = existing.revision_id
              )
              AND existing.effective_from < coalesce(
                    v_effective_to, 'infinity'::pg_catalog.timestamptz
                  )
              AND v_effective_from < coalesce(
                    existing.effective_to, 'infinity'::pg_catalog.timestamptz
                  )
          ) THEN
            RAISE EXCEPTION 'symbol mapping interval is ambiguous'
              USING ERRCODE = 'P2S03';
          END IF;

          INSERT INTO public.security_master_revisions(
            revision_id, fact_id, subject_id, subject_kind, revision_ordinal,
            operation, effective_from, effective_to, known_at,
            supersedes_revision_id, lookup_provider, lookup_symbol,
            related_security_id, canonical_revision, canonical_revision_text,
            revision_digest, recorded_at
          ) VALUES (
            v_revision_id, v_fact_id, v_subject_id, v_subject_kind,
            v_revision_ordinal, v_operation, v_effective_from, v_effective_to,
            v_known_at, v_supersedes_revision_id, v_lookup_provider,
            v_lookup_symbol, v_related_security_id, v_revision,
            p_canonical_revision_text, v_revision_digest, v_recorded_at
          );
          RETURN QUERY SELECT v_revision_id, v_revision_digest, true;
        EXCEPTION
          WHEN invalid_text_representation OR datetime_field_overflow
               OR numeric_value_out_of_range THEN
            RAISE EXCEPTION 'canonical revision scalar conversion is invalid'
              USING ERRCODE = 'P2S04';
        END;
        $append_security_master_revision$;

        CREATE FUNCTION public.reject_security_master_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $reject_security_master_mutation$
        BEGIN
          RAISE EXCEPTION 'security-master ledger is append-only'
            USING ERRCODE = 'P2S04';
        END;
        $reject_security_master_mutation$;

        CREATE TRIGGER security_master_identities_append_only
          BEFORE UPDATE OR DELETE ON public.security_master_identities
          FOR EACH ROW EXECUTE FUNCTION public.reject_security_master_mutation();
        CREATE TRIGGER security_master_identities_truncate_guard
          BEFORE TRUNCATE ON public.security_master_identities
          FOR EACH STATEMENT EXECUTE FUNCTION public.reject_security_master_mutation();
        CREATE TRIGGER security_master_revisions_append_only
          BEFORE UPDATE OR DELETE ON public.security_master_revisions
          FOR EACH ROW EXECUTE FUNCTION public.reject_security_master_mutation();
        CREATE TRIGGER security_master_revisions_truncate_guard
          BEFORE TRUNCATE ON public.security_master_revisions
          FOR EACH STATEMENT EXECUTE FUNCTION public.reject_security_master_mutation();

        REVOKE ALL PRIVILEGES ON TABLE public.security_master_identities FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON TABLE public.security_master_revisions FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON TABLE public.security_master_identities
          FROM trading_job_api, trading_job_scheduler, trading_job_worker, trading_reader;
        REVOKE ALL PRIVILEGES ON TABLE public.security_master_revisions
          FROM trading_job_api, trading_job_scheduler, trading_job_worker, trading_reader;
        REVOKE ALL PRIVILEGES ON FUNCTION public.append_security_master_revision(text) FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.append_security_master_revision(text) FROM trading_job_api;
        REVOKE ALL PRIVILEGES ON FUNCTION public.append_security_master_revision(text) FROM trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION public.append_security_master_revision(text) FROM trading_job_worker;
        REVOKE ALL PRIVILEGES ON FUNCTION public.append_security_master_revision(text) FROM trading_reader;
        REVOKE ALL PRIVILEGES ON FUNCTION public.reject_security_master_mutation() FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.reject_security_master_mutation()
          FROM trading_job_api, trading_job_scheduler, trading_job_worker, trading_reader;

        DO $p2_security_master_postflight$
        DECLARE
          v_function pg_catalog.regprocedure;
          v_mutation_function pg_catalog.regprocedure;
        BEGIN
          IF pg_catalog.pg_get_userbyid(
               (SELECT relowner FROM pg_catalog.pg_class
                WHERE oid = 'public.security_master_identities'::pg_catalog.regclass)
             ) <> 'trading_owner'
             OR pg_catalog.pg_get_userbyid(
               (SELECT relowner FROM pg_catalog.pg_class
                WHERE oid = 'public.security_master_revisions'::pg_catalog.regclass)
             ) <> 'trading_owner' THEN
            RAISE EXCEPTION 'P2 security-master table owner is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          IF (
            SELECT pg_catalog.count(*)
            FROM pg_catalog.pg_class AS table_row
            WHERE table_row.oid IN (
              'public.security_master_identities'::pg_catalog.regclass,
              'public.security_master_revisions'::pg_catalog.regclass
            )
              AND table_row.relkind = 'r'
              AND pg_catalog.pg_get_userbyid(table_row.relowner) = 'trading_owner'
              AND (
                SELECT pg_catalog.count(*) = 7
                  AND pg_catalog.bool_and(
                    acl.grantee = table_row.relowner
                    AND acl.privilege_type IN (
                      'DELETE', 'INSERT', 'REFERENCES', 'SELECT',
                      'TRIGGER', 'TRUNCATE', 'UPDATE'
                    )
                    AND NOT acl.is_grantable
                  )
                FROM pg_catalog.aclexplode(coalesce(
                  table_row.relacl,
                  pg_catalog.acldefault('r', table_row.relowner)
                )) AS acl
              )
          ) <> 2 THEN
            RAISE EXCEPTION 'P2 security-master table ACL is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          v_function :=
            'public.append_security_master_revision(pg_catalog.text)'::pg_catalog.regprocedure;
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc
            WHERE oid = v_function
              AND prokind = 'f'
              AND pg_catalog.pg_get_userbyid(proowner) = 'trading_owner'
              AND prosecdef
              AND provolatile = 'v'
              AND proparallel = 'u'
              AND proconfig = ARRAY['search_path=pg_catalog']::pg_catalog.text[]
              AND (
                SELECT pg_catalog.count(*) = 1
                  AND pg_catalog.bool_and(
                    acl.grantee = proowner
                    AND acl.privilege_type = 'EXECUTE'
                    AND NOT acl.is_grantable
                  )
                FROM pg_catalog.aclexplode(coalesce(
                  proacl, pg_catalog.acldefault('f', proowner)
                )) AS acl
              )
          ) THEN
            RAISE EXCEPTION 'P2 security-master function authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          v_mutation_function :=
            'public.reject_security_master_mutation()'::pg_catalog.regprocedure;
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc
            WHERE oid = v_mutation_function
              AND prokind = 'f'
              AND pg_catalog.pg_get_userbyid(proowner) = 'trading_owner'
              AND NOT prosecdef
              AND provolatile = 'v'
              AND proparallel = 'u'
              AND proconfig = ARRAY['search_path=pg_catalog']::pg_catalog.text[]
              AND (
                SELECT pg_catalog.count(*) = 1
                  AND pg_catalog.bool_and(
                    acl.grantee = proowner
                    AND acl.privilege_type = 'EXECUTE'
                    AND NOT acl.is_grantable
                  )
                FROM pg_catalog.aclexplode(coalesce(
                  proacl, pg_catalog.acldefault('f', proowner)
                )) AS acl
              )
          ) THEN
            RAISE EXCEPTION 'P2 security-master mutation authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
          IF (
            SELECT pg_catalog.count(*) = 4
              AND pg_catalog.count(*) FILTER (
                WHERE trigger_row.tgenabled = 'O'
                  AND trigger_row.tgfoid = v_mutation_function
                  AND (
                    (
                      trigger_row.tgname = 'security_master_identities_append_only'
                      AND trigger_row.tgrelid =
                            'public.security_master_identities'::pg_catalog.regclass
                      AND trigger_row.tgtype = 27
                    ) OR (
                      trigger_row.tgname = 'security_master_identities_truncate_guard'
                      AND trigger_row.tgrelid =
                            'public.security_master_identities'::pg_catalog.regclass
                      AND trigger_row.tgtype = 34
                    ) OR (
                      trigger_row.tgname = 'security_master_revisions_append_only'
                      AND trigger_row.tgrelid =
                            'public.security_master_revisions'::pg_catalog.regclass
                      AND trigger_row.tgtype = 27
                    ) OR (
                      trigger_row.tgname = 'security_master_revisions_truncate_guard'
                      AND trigger_row.tgrelid =
                            'public.security_master_revisions'::pg_catalog.regclass
                      AND trigger_row.tgtype = 34
                    )
                  )
              ) = 4
            FROM pg_catalog.pg_trigger AS trigger_row
            WHERE trigger_row.tgrelid IN (
              'public.security_master_identities'::pg_catalog.regclass,
              'public.security_master_revisions'::pg_catalog.regclass
            )
              AND NOT trigger_row.tgisinternal
          ) IS NOT TRUE THEN
            RAISE EXCEPTION 'P2 security-master trigger authority is invalid'
              USING ERRCODE = 'P2D08';
          END IF;
        END;
        $p2_security_master_postflight$;
        SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);
        """,
        execution_options={"no_parameters": True},
    )


def downgrade() -> None:
    raise RuntimeError(
        "0019 P2 security-master ledger is forward-only; use a reviewed forward repair"
    )

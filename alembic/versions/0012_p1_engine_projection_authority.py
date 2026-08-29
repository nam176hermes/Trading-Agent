"""Persist validated P1 batch and semantic projection authority.

Revision ID: 0012_p1_engine_projection_authority
Revises: 0011_engine_backtest_worker_authority

This migration is source authority only and is never applied by validation.
"""
from __future__ import annotations

from alembic import op


revision = "0012_p1_engine_projection_authority"
down_revision = "0011_engine_backtest_worker_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        ALTER TABLE public.engine_run_projections
          ADD COLUMN batch_sha256 char(64),
          ADD COLUMN semantic_digest char(64),
          ADD COLUMN request_message_id uuid,
          ADD CONSTRAINT engine_run_projection_result_authority_complete CHECK (
            (
              batch_sha256 IS NULL
              AND semantic_digest IS NULL
              AND request_message_id IS NULL
            )
            OR (
              batch_sha256 IS NOT NULL
              AND semantic_digest IS NOT NULL
              AND request_message_id IS NOT NULL
              AND batch_sha256 ~ '^[0-9a-f]{64}$'
              AND semantic_digest ~ '^[0-9a-f]{64}$'
            )
          ),
          ADD CONSTRAINT engine_run_projection_batch_fkey
            FOREIGN KEY (batch_sha256)
            REFERENCES public.engine_event_batch_receipts(batch_sha256)
            ON DELETE RESTRICT;

        CREATE FUNCTION public.engine_run_completion_append_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $engine_run_completion_append_guard$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.engine_run_projections AS projection
            WHERE projection.engine_run_id = NEW.engine_run_id
              AND projection.semantic_digest IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'completed P1 engine run cannot advance'
              USING ERRCODE = 'P2D01';
          END IF;
          RETURN NEW;
        END;
        $engine_run_completion_append_guard$;
        CREATE TRIGGER engine_events_reject_after_p1_completion
          BEFORE INSERT ON public.engine_events
          FOR EACH ROW EXECUTE FUNCTION
            public.engine_run_completion_append_guard();

        CREATE FUNCTION job_plane.paper_worker_job_id_allowed(p_job_id text)
        RETURNS boolean
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $paper_worker_job_id_allowed$
          SELECT EXISTS (
            SELECT 1
            FROM public.jobs AS job_row
            WHERE job_row.job_id = p_job_id
              AND job_plane.paper_worker_job_allowed(
                    job_row.job_type, job_row.payload
                  )
          )
        $paper_worker_job_id_allowed$;

        DROP POLICY job_plane_worker_heartbeats_insert
          ON public.worker_heartbeats;
        CREATE POLICY job_plane_worker_heartbeats_insert
          ON public.worker_heartbeats FOR INSERT TO trading_job_worker
          WITH CHECK (
            current_job_id IS NULL
            OR job_plane.paper_worker_job_id_allowed(current_job_id)
          );
        DROP POLICY job_plane_worker_heartbeats_update
          ON public.worker_heartbeats;
        CREATE POLICY job_plane_worker_heartbeats_update
          ON public.worker_heartbeats FOR UPDATE TO trading_job_worker
          USING (true)
          WITH CHECK (
            current_job_id IS NULL
            OR job_plane.paper_worker_job_id_allowed(current_job_id)
          );

        CREATE FUNCTION job_plane.ingest_p1_engine_event_batch_v2(
          p_batch_document text,
          p_expected_request_message_id uuid,
          p_expected_correlation_id uuid,
          p_expected_causation_id uuid,
          p_expected_engine_run_id uuid,
          p_expected_config_digest text,
          p_expected_catalog_digest text,
          p_expected_data_digest text,
          p_expected_producer_identity text,
          p_expected_source_commit text
        )
        RETURNS TABLE (
          batch_sha256 char(64),
          ingestion_digest char(64),
          job_id varchar(36),
          attempt_id varchar(40),
          engine_run_id uuid,
          event_count bigint,
          first_sequence bigint,
          last_sequence bigint,
          last_digest char(64)
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $ingest_p1_engine_event_batch_v2$
        DECLARE
          v_document jsonb;
          v_metadata jsonb;
          v_legacy_document text;
          v_first_envelope jsonb;
          v_last_envelope jsonb;
          v_semantic_digest text;
          v_batch_sha256 text;
          v_p1_event jsonb;
          v_p1_envelope jsonb;
          v_p1_payload jsonb;
          v_p1_attribute jsonb;
          v_p1_attributes jsonb;
          v_p1_typed_event jsonb;
          v_source_signal_ids jsonb;
          v_first_authority jsonb;
          v_semantic_events jsonb := '[]'::jsonb;
          v_targets jsonb := '{}'::jsonb;
          v_plans jsonb := '{}'::jsonb;
          v_orders jsonb := '{}'::jsonb;
          v_filled jsonb := '{}'::jsonb;
          v_submitted_targets jsonb := '{}'::jsonb;
          v_native_orders jsonb := '{}'::jsonb;
          v_native_fills jsonb := '{}'::jsonb;
          v_last_position jsonb;
          v_last_account jsonb;
          v_event_type text;
          v_event_family text;
          v_attribute_name text;
          v_attribute_names text[];
          v_expected_attribute_names text[];
          v_message_hex text;
          v_expected_message_id uuid;
          v_previous_simulation_time timestamptz;
          v_ordinal bigint;
          v_attribute_count bigint;
          v_distinct_attribute_count bigint;
          v_target_count bigint := 0;
          v_order_count bigint := 0;
          v_fill_count bigint := 0;
          v_raw_size bigint;
          v_expected_ingestion_digest text;
          v_filled_quantity numeric;
          v_total bigint;
          v_valid bigint;
          accepted record;
          projection public.engine_run_projections%ROWTYPE;
        BEGIN
          IF p_batch_document IS NULL
             OR octet_length(p_batch_document) > 67108864 THEN
            RAISE EXCEPTION 'P1 engine-event batch document exceeds the bound'
              USING ERRCODE = 'P2D04';
          END IF;
          BEGIN
            v_document := p_batch_document::jsonb;
          EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'P1 engine-event batch document is invalid JSON'
              USING ERRCODE = 'P2D04';
          END;
          IF p_batch_document IS DISTINCT FROM
                  public.canonical_domain_json_string(p_batch_document)
             OR jsonb_typeof(v_document) <> 'object' THEN
            RAISE EXCEPTION 'P1 engine-event batch document is not canonical'
              USING ERRCODE = 'P2D04';
          END IF;

          SELECT count(*), count(*) FILTER (
            WHERE key = ANY (ARRAY[
              'attempt_id', 'batch_sha256', 'engine_run_id', 'event_count',
              'events', 'first_sequence', 'ingestion_digest', 'job_id',
              'last_digest', 'last_sequence', 'validation_metadata',
              'validator_id'
            ]::text[])
          ) INTO v_total, v_valid
          FROM jsonb_object_keys(v_document) AS keys(key);
          v_metadata := v_document -> 'validation_metadata';
          IF v_total <> 12 OR v_valid <> 12
             OR v_document ->> 'validator_id'
                  IS DISTINCT FROM 'nautilus-p1-event-stream-v1'
             OR jsonb_typeof(v_document -> 'events') <> 'array'
             OR jsonb_typeof(v_document -> 'batch_sha256') <> 'string'
             OR jsonb_typeof(v_document -> 'ingestion_digest') <> 'string'
             OR jsonb_typeof(v_document -> 'job_id') <> 'string'
             OR jsonb_typeof(v_document -> 'attempt_id') <> 'string'
             OR jsonb_typeof(v_document -> 'engine_run_id') <> 'string'
             OR jsonb_typeof(v_document -> 'event_count') <> 'number'
             OR jsonb_typeof(v_document -> 'first_sequence') <> 'number'
             OR jsonb_typeof(v_document -> 'last_sequence') <> 'number'
             OR jsonb_typeof(v_document -> 'last_digest') <> 'string'
             OR jsonb_typeof(v_metadata) <> 'object' THEN
            RAISE EXCEPTION 'P1 engine-event projection authority is invalid'
              USING ERRCODE = 'P2D04';
          END IF;

          SELECT count(*), count(*) FILTER (
            WHERE key = ANY (ARRAY[
              'attempt_id', 'config_digest', 'engine_run_id',
              'engine_upstream_commit', 'engine_version', 'event_count',
              'fees', 'fill_count', 'final_cash', 'final_position',
              'first_sequence', 'job_id', 'last_sequence', 'order_count',
              'p1_product_closure_sha256', 'realized_pnl',
              'request_message_id', 'runtime_family', 'semantic_digest',
              'source_commit', 'target_count', 'unrealized_pnl',
              'validator_id'
            ]::text[])
          ) INTO v_total, v_valid
          FROM jsonb_object_keys(v_metadata) AS keys(key);
          IF v_total <> 23 OR v_valid <> 23
             OR jsonb_typeof(v_metadata -> 'event_count') <> 'number'
             OR jsonb_typeof(v_metadata -> 'first_sequence') <> 'number'
             OR jsonb_typeof(v_metadata -> 'last_sequence') <> 'number'
             OR jsonb_typeof(v_metadata -> 'target_count') <> 'number'
             OR jsonb_typeof(v_metadata -> 'order_count') <> 'number'
             OR jsonb_typeof(v_metadata -> 'fill_count') <> 'number'
             OR EXISTS (
               SELECT 1
               FROM unnest(ARRAY[
                 'event_count', 'first_sequence', 'last_sequence',
                 'target_count', 'order_count', 'fill_count'
               ]::text[]) AS count_fields(field_name)
               WHERE v_metadata ->> field_name !~ '^(0|[1-9][0-9]*)$'
                 OR length(v_metadata ->> field_name) > 19
                 OR (v_metadata ->> field_name)::numeric
                      > 9223372036854775807
             )
             OR EXISTS (
               SELECT 1
               FROM unnest(ARRAY[
                 'attempt_id', 'config_digest', 'engine_run_id',
                 'engine_upstream_commit', 'engine_version', 'fees',
                 'final_cash', 'final_position', 'job_id',
                 'p1_product_closure_sha256', 'realized_pnl',
                 'request_message_id', 'runtime_family', 'semantic_digest',
                 'source_commit', 'unrealized_pnl', 'validator_id'
               ]::text[]) AS string_fields(field_name)
               WHERE jsonb_typeof(v_metadata -> field_name) <> 'string'
             )
             OR v_metadata ->> 'validator_id'
                  IS DISTINCT FROM 'nautilus-p1-event-stream-v1'
             OR v_metadata ->> 'runtime_family' IS DISTINCT FROM 'cython-v1'
             OR v_metadata ->> 'engine_version' IS DISTINCT FROM '1.231.0'
             OR v_metadata ->> 'engine_upstream_commit' IS DISTINCT FROM
                  '27a8e54e7ac3c57d6cbf8891f0283dfbaee97317'
             OR v_metadata ->> 'p1_product_closure_sha256' IS DISTINCT FROM
                  '75467781b920e7172917a96d162fb6e2a3e8f9afee9eff065ef0ed220f623069'
             OR v_metadata ->> 'attempt_id' IS DISTINCT FROM
                  v_document ->> 'attempt_id'
             OR v_metadata ->> 'job_id' IS DISTINCT FROM
                  v_document ->> 'job_id'
             OR v_metadata ->> 'engine_run_id' IS DISTINCT FROM
                  v_document ->> 'engine_run_id'
             OR v_metadata ->> 'request_message_id' IS DISTINCT FROM
                  p_expected_request_message_id::text
             OR v_metadata ->> 'engine_run_id' IS DISTINCT FROM
                  p_expected_engine_run_id::text
             OR v_metadata ->> 'config_digest' IS DISTINCT FROM
                  p_expected_config_digest
             OR v_metadata ->> 'source_commit' IS DISTINCT FROM
                  p_expected_source_commit
             OR v_metadata ->> 'event_count' IS DISTINCT FROM
                  v_document ->> 'event_count'
             OR v_metadata ->> 'first_sequence' IS DISTINCT FROM
                  v_document ->> 'first_sequence'
             OR v_metadata ->> 'last_sequence' IS DISTINCT FROM
                  v_document ->> 'last_sequence'
             OR (v_metadata ->> 'config_digest') !~ '^[0-9a-f]{64}$'
             OR (v_metadata ->> 'p1_product_closure_sha256')
                  !~ '^[0-9a-f]{64}$'
             OR (v_metadata ->> 'semantic_digest') !~ '^[0-9a-f]{64}$'
             OR (v_metadata ->> 'request_message_id')
                  !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
             OR (v_metadata ->> 'source_commit')
                  !~ '^([0-9a-f]{40}|[0-9a-f]{64})$' THEN
            RAISE EXCEPTION 'P1 validation metadata is invalid'
              USING ERRCODE = 'P2D04';
          END IF;

          IF (v_document ->> 'event_count')::bigint < 5
             OR (v_document ->> 'first_sequence')::bigint <> 2 THEN
            RAISE EXCEPTION 'P1 event stream is incomplete'
              USING ERRCODE = 'P2D04';
          END IF;
          FOR v_p1_event, v_ordinal IN
            SELECT item, ordinal
            FROM jsonb_array_elements(v_document -> 'events')
              WITH ORDINALITY AS entries(item, ordinal)
          LOOP
            BEGIN
              v_p1_envelope := (v_p1_event ->> 'canonical_json')::jsonb;
            EXCEPTION WHEN invalid_text_representation THEN
              RAISE EXCEPTION 'P1 event envelope is invalid'
                USING ERRCODE = 'P2D04';
            END;
            IF jsonb_typeof(v_p1_envelope) <> 'object' THEN
              RAISE EXCEPTION 'P1 event envelope is invalid'
                USING ERRCODE = 'P2D04';
            END IF;
            SELECT count(*), count(*) FILTER (
              WHERE key = ANY (ARRAY[
                'message_id', 'correlation_id', 'causation_id',
                'engine_run_id', 'stream_sequence', 'event_time',
                'initialization_time', 'schema_version', 'producer_identity',
                'source_commit', 'config_digest', 'payload_digest', 'payload'
              ]::text[])
            ) INTO v_total, v_valid
            FROM jsonb_object_keys(v_p1_envelope) AS envelope_keys(key);
            v_p1_payload := v_p1_envelope -> 'payload';
            IF v_total <> 13 OR v_valid <> 13
               OR jsonb_typeof(v_p1_payload) <> 'object'
               OR jsonb_typeof(v_p1_envelope -> 'stream_sequence') <> 'number'
               OR jsonb_typeof(v_p1_envelope -> 'event_time') <> 'string'
               OR jsonb_typeof(v_p1_envelope -> 'initialization_time') <> 'string'
               OR EXISTS (
                 SELECT 1
                 FROM unnest(ARRAY[
                   'message_id', 'correlation_id', 'causation_id',
                   'engine_run_id', 'schema_version', 'producer_identity',
                   'source_commit', 'config_digest', 'payload_digest'
                 ]::text[]) AS envelope_strings(field_name)
                 WHERE jsonb_typeof(v_p1_envelope -> field_name) <> 'string'
               )
               OR v_p1_envelope ->> 'event_time' !~
                    '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{6})?Z$'
               OR v_p1_envelope ->> 'event_time' ~ '\.000000Z$'
               OR v_p1_envelope ->> 'initialization_time' !~
                    '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{6})?Z$'
               OR v_p1_envelope ->> 'initialization_time' ~ '\.000000Z$' THEN
              RAISE EXCEPTION 'P1 event envelope schema is not closed'
                USING ERRCODE = 'P2D04';
            END IF;
            IF jsonb_typeof(v_p1_payload) <> 'object' THEN
              RAISE EXCEPTION 'P1 event payload schema is not closed'
                USING ERRCODE = 'P2D04';
            END IF;
            SELECT count(*), count(*) FILTER (
              WHERE key = ANY (ARRAY[
                'attributes', 'event_type', 'family'
              ]::text[])
            ) INTO v_total, v_valid
            FROM jsonb_object_keys(v_p1_payload) AS payload_keys(key);
            IF v_total <> 3 OR v_valid <> 3
               OR jsonb_typeof(v_p1_payload -> 'attributes') <> 'array'
               OR jsonb_typeof(v_p1_payload -> 'event_type') <> 'string'
               OR jsonb_typeof(v_p1_payload -> 'family') <> 'string'
               OR v_p1_envelope ->> 'payload_digest' IS DISTINCT FROM encode(
                    public.digest(
                      convert_to(
                        public.canonical_domain_json(v_p1_payload), 'UTF8'
                      ),
                      'sha256'
                    ),
                    'hex'
                  ) THEN
              RAISE EXCEPTION 'P1 event payload schema is not closed'
                USING ERRCODE = 'P2D04';
            END IF;

            v_p1_attributes := '{}'::jsonb;
            v_attribute_count := 0;
            v_attribute_names := ARRAY[]::text[];
            FOR v_p1_attribute IN
              SELECT item
              FROM jsonb_array_elements(v_p1_payload -> 'attributes')
                AS attributes(item)
            LOOP
              IF jsonb_typeof(v_p1_attribute) <> 'object' THEN
                RAISE EXCEPTION 'P1 event attributes are not closed'
                  USING ERRCODE = 'P2D04';
              END IF;
              SELECT count(*), count(*) FILTER (
                WHERE key = ANY (ARRAY['name', 'value']::text[])
              ) INTO v_total, v_valid
              FROM jsonb_object_keys(v_p1_attribute) AS attribute_keys(key);
              v_attribute_name := v_p1_attribute ->> 'name';
              IF v_total <> 2 OR v_valid <> 2
                 OR jsonb_typeof(v_p1_attribute -> 'name') <> 'string'
                 OR jsonb_typeof(v_p1_attribute -> 'value')
                      NOT IN ('string', 'number')
                 OR octet_length(v_p1_attribute ->> 'value') > 4096
                 OR v_attribute_name !~ '^[a-z][a-z0-9_]{0,63}$'
                 OR v_p1_attributes ? v_attribute_name THEN
                RAISE EXCEPTION 'P1 event attributes are not closed'
                  USING ERRCODE = 'P2D04';
              END IF;
              v_p1_attributes := v_p1_attributes || jsonb_build_object(
                v_attribute_name, v_p1_attribute -> 'value'
              );
              v_attribute_names := array_append(
                v_attribute_names, v_attribute_name
              );
              v_attribute_count := v_attribute_count + 1;
            END LOOP;
            SELECT count(DISTINCT key) INTO v_distinct_attribute_count
            FROM jsonb_object_keys(v_p1_attributes) AS attribute_names(key);
            IF v_attribute_count <> v_distinct_attribute_count THEN
              RAISE EXCEPTION 'P1 event attributes are not closed'
                USING ERRCODE = 'P2D04';
            END IF;

            v_event_type := v_p1_payload ->> 'event_type';
            v_event_family := v_p1_payload ->> 'family';
            CASE v_event_type
              WHEN 'RunStarted' THEN
                v_expected_attribute_names := ARRAY[
                  'schema_version', 'sequence', 'simulation_time', 'origin',
                  'runtime_family', 'engine_version', 'upstream_commit',
                  'closure_digest', 'config_digest', 'catalog_digest',
                  'data_digest'
                ]::text[];
              WHEN 'TargetAccepted' THEN
                v_expected_attribute_names := ARRAY[
                  'schema_version', 'sequence', 'simulation_time', 'origin',
                  'target_id', 'source_signal_ids', 'target_weight'
                ]::text[];
              WHEN 'TargetQuantityPlanned' THEN
                v_expected_attribute_names := ARRAY[
                  'schema_version', 'sequence', 'simulation_time', 'origin',
                  'target_id', 'quantity'
                ]::text[];
              WHEN 'OrderSubmitted' THEN
                v_expected_attribute_names := ARRAY[
                  'schema_version', 'sequence', 'simulation_time', 'origin',
                  'native_type', 'client_order_id', 'native_order_id',
                  'target_id', 'source_signal_ids', 'side', 'quantity',
                  'order_type'
                ]::text[];
              WHEN 'Fill' THEN
                v_expected_attribute_names := ARRAY[
                  'schema_version', 'sequence', 'simulation_time', 'origin',
                  'native_type', 'client_order_id', 'native_fill_id', 'side',
                  'quantity', 'price', 'fee', 'fee_currency'
                ]::text[];
              WHEN 'PositionObserved' THEN
                v_expected_attribute_names := ARRAY[
                  'schema_version', 'sequence', 'simulation_time', 'origin',
                  'native_type', 'quantity', 'average_entry_price',
                  'realized_pnl', 'unrealized_pnl'
                ]::text[];
              WHEN 'AccountObserved' THEN
                v_expected_attribute_names := ARRAY[
                  'schema_version', 'sequence', 'simulation_time', 'origin',
                  'native_type', 'cash_balance', 'fees', 'realized_pnl',
                  'unrealized_pnl'
                ]::text[];
              WHEN 'RunCompleted' THEN
                v_expected_attribute_names := ARRAY[
                  'schema_version', 'sequence', 'simulation_time', 'origin',
                  'runtime_family', 'engine_version', 'upstream_commit',
                  'closure_digest', 'target_count', 'order_count',
                  'fill_count', 'final_cash', 'final_position', 'fees',
                  'realized_pnl', 'unrealized_pnl', 'semantic_digest'
                ]::text[];
              ELSE
                RAISE EXCEPTION 'P1 event state transition is invalid'
                  USING ERRCODE = 'P2D04';
            END CASE;
            IF v_attribute_names IS DISTINCT FROM v_expected_attribute_names THEN
              RAISE EXCEPTION 'P1 event attributes are not closed'
                USING ERRCODE = 'P2D04';
            END IF;
            IF v_ordinal = 1 THEN
              v_first_authority := v_p1_envelope;
            END IF;
            IF v_p1_envelope ->> 'engine_run_id' IS DISTINCT FROM
                  p_expected_engine_run_id::text
               OR v_p1_envelope ->> 'config_digest' IS DISTINCT FROM
                    p_expected_config_digest
               OR v_p1_envelope ->> 'source_commit' IS DISTINCT FROM
                    p_expected_source_commit
               OR v_p1_envelope ->> 'correlation_id' IS DISTINCT FROM
                    p_expected_correlation_id::text
               OR v_p1_envelope ->> 'causation_id' IS DISTINCT FROM
                    p_expected_causation_id::text
               OR v_p1_envelope ->> 'producer_identity' IS DISTINCT FROM
                    p_expected_producer_identity
               OR v_p1_envelope ->> 'schema_version' IS DISTINCT FROM '1.0.0'
               OR v_p1_envelope ->> 'stream_sequence' IS DISTINCT FROM
                    (v_ordinal + 1)::text
               OR v_p1_attributes ->> 'sequence' IS DISTINCT FROM
                    (v_ordinal + 1)::text
               OR v_p1_envelope ->> 'correlation_id' IS DISTINCT FROM
                    v_first_authority ->> 'correlation_id'
               OR v_p1_envelope ->> 'causation_id' IS DISTINCT FROM
                    v_first_authority ->> 'causation_id'
               OR v_p1_envelope ->> 'event_time' IS DISTINCT FROM
                    v_first_authority ->> 'event_time'
               OR v_p1_envelope ->> 'initialization_time' IS DISTINCT FROM
                    v_first_authority ->> 'initialization_time'
               OR v_p1_envelope ->> 'schema_version' IS DISTINCT FROM
                    v_first_authority ->> 'schema_version'
               OR v_p1_envelope ->> 'producer_identity' IS DISTINCT FROM
                    v_first_authority ->> 'producer_identity'
               OR v_p1_attributes ->> 'schema_version'
                    IS DISTINCT FROM 'nautilus-p1-event-stream-v1'
               OR jsonb_typeof(v_p1_attributes -> 'sequence') <> 'number'
               OR jsonb_typeof(v_p1_attributes -> 'simulation_time') <> 'string'
               OR (v_p1_attributes ->> 'simulation_time') !~
                    '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{6})?Z$'
               OR (v_p1_attributes ->> 'simulation_time') ~ '\.000000Z$' THEN
              RAISE EXCEPTION 'P1 event authority differs from the request'
                USING ERRCODE = 'P2D04';
            END IF;
            BEGIN
              IF (v_p1_envelope ->> 'initialization_time')::timestamptz
                   > (v_p1_envelope ->> 'event_time')::timestamptz
                 OR (
                   v_previous_simulation_time IS NOT NULL
                   AND (v_p1_attributes ->> 'simulation_time')::timestamptz
                         < v_previous_simulation_time
                 ) THEN
                RAISE EXCEPTION 'P1 event authority differs from the request'
                  USING ERRCODE = 'P2D04';
              END IF;
              v_previous_simulation_time :=
                (v_p1_attributes ->> 'simulation_time')::timestamptz;
            EXCEPTION
              WHEN invalid_datetime_format OR datetime_field_overflow THEN
                RAISE EXCEPTION 'P1 event authority differs from the request'
                  USING ERRCODE = 'P2D04';
            END;

            v_message_hex := encode(
              public.digest(
                uuid_send(p_expected_request_message_id)
                  || convert_to(
                    'nautilus-p1-event-stream-v1:'
                    || (v_ordinal + 1)::text || ':' || v_event_type,
                    'UTF8'
                  ),
                'sha1'
              ),
              'hex'
            );
            v_message_hex := overlay(v_message_hex placing '5' from 13 for 1);
            v_message_hex := overlay(
              v_message_hex
              placing substr(
                '88889999aaaabbbb',
                position(substr(v_message_hex, 17, 1) in '0123456789abcdef'),
                1
              )
              from 17 for 1
            );
            v_expected_message_id := (
              substr(v_message_hex, 1, 8) || '-'
              || substr(v_message_hex, 9, 4) || '-'
              || substr(v_message_hex, 13, 4) || '-'
              || substr(v_message_hex, 17, 4) || '-'
              || substr(v_message_hex, 21, 12)
            )::uuid;
            IF v_p1_envelope ->> 'message_id' IS DISTINCT FROM
                 v_expected_message_id::text THEN
              RAISE EXCEPTION 'P1 event message identity is invalid'
                USING ERRCODE = 'P2D04';
            END IF;

            v_p1_typed_event := jsonb_build_object('event_type', v_event_type)
              || v_p1_attributes;
            IF EXISTS (
              SELECT 1
              FROM jsonb_each(v_p1_attributes) AS typed_fields(field_name, value)
              WHERE (
                  field_name = ANY (ARRAY[
                    'sequence', 'target_count', 'order_count', 'fill_count'
                  ]::text[])
                  AND jsonb_typeof(value) <> 'number'
                ) OR (
                  NOT (
                    field_name = ANY (ARRAY[
                      'sequence', 'target_count', 'order_count', 'fill_count'
                    ]::text[])
                  )
                  AND jsonb_typeof(value) <> 'string'
                )
            ) OR EXISTS (
              SELECT 1
              FROM unnest(ARRAY[
                'target_weight', 'quantity', 'price', 'fee',
                'average_entry_price', 'realized_pnl', 'unrealized_pnl',
                'cash_balance', 'fees', 'final_cash', 'final_position'
              ]::text[]) AS decimal_fields(field_name)
              WHERE v_p1_attributes ? field_name
                AND (
                  jsonb_typeof(v_p1_attributes -> field_name) <> 'string'
                  OR v_p1_attributes ->> field_name !~
                       '^(?:0|-?[1-9][0-9]*|-?(?:0|[1-9][0-9]*)\.[0-9]*[1-9])$'
                )
            ) OR EXISTS (
              SELECT 1
              FROM unnest(ARRAY[
                'target_count', 'order_count', 'fill_count'
              ]::text[]) AS count_fields(field_name)
              WHERE v_p1_attributes ? field_name
                AND (
                  jsonb_typeof(v_p1_attributes -> field_name) <> 'number'
                  OR v_p1_attributes ->> field_name !~ '^(0|[1-9][0-9]*)$'
                  OR length(v_p1_attributes ->> field_name) > 19
                  OR (v_p1_attributes ->> field_name)::numeric
                       > 9223372036854775807
                )
            ) OR EXISTS (
              SELECT 1
              FROM unnest(ARRAY[
                'target_id', 'client_order_id', 'native_order_id',
                'native_fill_id'
              ]::text[]) AS identifier_fields(field_name)
              WHERE v_p1_attributes ? field_name
                AND (
                  jsonb_typeof(v_p1_attributes -> field_name) <> 'string'
                  OR v_p1_attributes ->> field_name !~
                       '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                )
            ) THEN
              RAISE EXCEPTION 'P1 event attributes are not closed'
                USING ERRCODE = 'P2D04';
            END IF;
            v_source_signal_ids := NULL;
            IF v_p1_attributes ? 'source_signal_ids' THEN
              BEGIN
                v_source_signal_ids :=
                  (v_p1_attributes ->> 'source_signal_ids')::jsonb;
                IF jsonb_typeof(v_source_signal_ids) <> 'array' THEN
                  RAISE EXCEPTION 'P1 event attributes are not closed'
                    USING ERRCODE = 'P2D04';
                END IF;
                v_p1_typed_event := jsonb_set(
                  v_p1_typed_event,
                  '{source_signal_ids}',
                  v_source_signal_ids
                );
              EXCEPTION WHEN invalid_text_representation THEN
                RAISE EXCEPTION 'P1 event attributes are not closed'
                  USING ERRCODE = 'P2D04';
              END;
            END IF;
            IF NOT (v_p1_attributes ? 'native_type') THEN
              v_p1_typed_event := v_p1_typed_event
                || jsonb_build_object('native_type', NULL);
            END IF;
            v_semantic_events := v_semantic_events || jsonb_build_array(
              v_p1_typed_event
                - 'native_fill_id' - 'native_order_id' - 'semantic_digest'
            );

            IF v_event_type = 'RunStarted' THEN
              IF v_ordinal <> 1 OR v_event_family <> 'ENGINE_LIFECYCLE'
                 OR v_attribute_count <> 11
                 OR NOT (v_p1_attributes ?& ARRAY[
                   'schema_version', 'sequence', 'simulation_time', 'origin',
                   'runtime_family', 'engine_version', 'upstream_commit',
                   'closure_digest', 'config_digest', 'catalog_digest',
                   'data_digest'
                 ]::text[])
                 OR v_p1_attributes ->> 'origin' <> 'CONTROL_PLANE'
                 OR v_p1_attributes ->> 'config_digest' IS DISTINCT FROM
                      v_metadata ->> 'config_digest'
                 OR v_p1_attributes ->> 'catalog_digest' IS DISTINCT FROM
                      p_expected_catalog_digest
                 OR v_p1_attributes ->> 'data_digest' IS DISTINCT FROM
                      p_expected_data_digest
                 OR v_p1_attributes ->> 'closure_digest' IS DISTINCT FROM
                      '75467781b920e7172917a96d162fb6e2a3e8f9afee9eff065ef0ed220f623069' THEN
                RAISE EXCEPTION 'P1 event state transition is invalid'
                  USING ERRCODE = 'P2D04';
              END IF;
            ELSIF v_event_type = 'TargetAccepted' THEN
              IF v_event_family <> 'STRATEGY_LIFECYCLE'
                 OR v_attribute_count <> 7
                 OR NOT (v_p1_attributes ?& ARRAY[
                   'schema_version', 'sequence', 'simulation_time', 'origin',
                   'target_id', 'source_signal_ids', 'target_weight'
                 ]::text[])
                 OR v_p1_attributes ->> 'origin' <> 'CONTROL_PLANE'
                 OR (v_p1_attributes ->> 'target_id') !~
                      '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                 OR (v_p1_attributes ->> 'target_weight')::numeric
                      NOT BETWEEN 0 AND 1
                 OR v_targets ? (v_p1_attributes ->> 'target_id') THEN
                RAISE EXCEPTION 'P1 event state transition is invalid'
                  USING ERRCODE = 'P2D04';
              END IF;
              BEGIN
                IF jsonb_array_length(v_source_signal_ids) NOT BETWEEN 1 AND 64
                   OR public.canonical_domain_json_string(
                     v_p1_attributes ->> 'source_signal_ids'
                   ) IS DISTINCT FROM v_p1_attributes ->> 'source_signal_ids'
                   OR EXISTS (
                     SELECT 1
                     FROM jsonb_array_elements(v_source_signal_ids)
                       AS signal_ids(value)
                     WHERE jsonb_typeof(value) <> 'string'
                       OR value #>> '{}' !~
                            '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                   )
                   OR EXISTS (
                     SELECT 1
                     FROM jsonb_array_elements_text(v_source_signal_ids)
                       AS signal_ids(value)
                     GROUP BY value HAVING count(*) > 1
                   ) THEN
                  RAISE EXCEPTION 'P1 event state transition is invalid'
                    USING ERRCODE = 'P2D04';
                END IF;
              EXCEPTION WHEN invalid_text_representation THEN
                RAISE EXCEPTION 'P1 event state transition is invalid'
                  USING ERRCODE = 'P2D04';
              END;
              v_targets := v_targets || jsonb_build_object(
                v_p1_attributes ->> 'target_id',
                v_p1_attributes -> 'source_signal_ids'
              );
              v_target_count := v_target_count + 1;
            ELSIF v_event_type = 'TargetQuantityPlanned' THEN
              IF v_event_family <> 'STRATEGY_LIFECYCLE'
                 OR v_attribute_count <> 6
                 OR NOT (v_p1_attributes ?& ARRAY[
                   'schema_version', 'sequence', 'simulation_time', 'origin',
                   'target_id', 'quantity'
                 ]::text[])
                 OR v_p1_attributes ->> 'origin' <> 'CONTROL_PLANE'
                 OR NOT (v_targets ? (v_p1_attributes ->> 'target_id'))
                 OR v_plans ? (v_p1_attributes ->> 'target_id')
                 OR (v_p1_attributes ->> 'quantity')::numeric < 0 THEN
                RAISE EXCEPTION 'P1 event state transition is invalid'
                  USING ERRCODE = 'P2D04';
              END IF;
              v_plans := v_plans || jsonb_build_object(
                v_p1_attributes ->> 'target_id',
                v_p1_attributes -> 'quantity'
              );
            ELSIF v_event_type = 'OrderSubmitted' THEN
              IF v_event_family <> 'ORDER_LIFECYCLE'
                 OR v_attribute_count <> 12
                 OR NOT (v_p1_attributes ?& ARRAY[
                   'schema_version', 'sequence', 'simulation_time', 'origin',
                   'native_type', 'client_order_id', 'native_order_id',
                   'target_id', 'source_signal_ids', 'side', 'quantity',
                   'order_type'
                 ]::text[])
                 OR v_p1_attributes ->> 'origin' <> 'CONTROL_PLANE'
                 OR v_p1_attributes ->> 'native_type' <> 'Order'
                 OR v_p1_attributes ->> 'order_type' <> 'MARKET'
                 OR v_p1_attributes ->> 'side' NOT IN ('BUY', 'SELL')
                 OR NOT (v_plans ? (v_p1_attributes ->> 'target_id'))
                 OR v_submitted_targets ? (v_p1_attributes ->> 'target_id')
                 OR v_targets ->> (v_p1_attributes ->> 'target_id')
                      IS DISTINCT FROM v_p1_attributes ->> 'source_signal_ids'
                 OR v_plans ->> (v_p1_attributes ->> 'target_id')
                      IS DISTINCT FROM v_p1_attributes ->> 'quantity'
                 OR v_orders ? (v_p1_attributes ->> 'client_order_id')
                 OR v_native_orders ? (v_p1_attributes ->> 'native_order_id')
                 OR (v_p1_attributes ->> 'quantity')::numeric <= 0 THEN
                RAISE EXCEPTION 'P1 event state transition is invalid'
                  USING ERRCODE = 'P2D04';
              END IF;
              v_orders := v_orders || jsonb_build_object(
                v_p1_attributes ->> 'client_order_id',
                jsonb_build_object(
                  'side', v_p1_attributes ->> 'side',
                  'quantity', v_p1_attributes ->> 'quantity'
                )
              );
              v_filled := v_filled || jsonb_build_object(
                v_p1_attributes ->> 'client_order_id', '0'
              );
              v_native_orders := v_native_orders || jsonb_build_object(
                v_p1_attributes ->> 'native_order_id', true
              );
              v_submitted_targets := v_submitted_targets || jsonb_build_object(
                v_p1_attributes ->> 'target_id', true
              );
              v_order_count := v_order_count + 1;
            ELSIF v_event_type = 'Fill' THEN
              IF v_event_family <> 'FILLS'
                 OR v_attribute_count <> 12
                 OR NOT (v_p1_attributes ?& ARRAY[
                   'schema_version', 'sequence', 'simulation_time', 'origin',
                   'native_type', 'client_order_id', 'native_fill_id', 'side',
                   'quantity', 'price', 'fee', 'fee_currency'
                 ]::text[])
                 OR v_p1_attributes ->> 'origin' <> 'NAUTILUS_CALLBACK'
                 OR v_p1_attributes ->> 'native_type' <> 'OrderFilled'
                 OR v_p1_attributes ->> 'fee_currency' <> 'USDT'
                 OR NOT (v_orders ? (v_p1_attributes ->> 'client_order_id'))
                 OR v_native_fills ? (v_p1_attributes ->> 'native_fill_id')
                 OR v_orders #>> ARRAY[
                      v_p1_attributes ->> 'client_order_id', 'side'
                    ] IS DISTINCT FROM v_p1_attributes ->> 'side'
                 OR (v_p1_attributes ->> 'quantity')::numeric <= 0
                 OR (v_p1_attributes ->> 'price')::numeric <= 0
                 OR (v_p1_attributes ->> 'fee')::numeric < 0 THEN
                RAISE EXCEPTION 'P1 event state transition is invalid'
                  USING ERRCODE = 'P2D04';
              END IF;
              v_filled_quantity := (
                v_filled ->> (v_p1_attributes ->> 'client_order_id')
              )::numeric + (v_p1_attributes ->> 'quantity')::numeric;
              IF v_filled_quantity > (
                   v_orders #>> ARRAY[
                     v_p1_attributes ->> 'client_order_id', 'quantity'
                   ]
                 )::numeric THEN
                RAISE EXCEPTION 'P1 event state transition is invalid'
                  USING ERRCODE = 'P2D04';
              END IF;
              v_filled := jsonb_set(
                v_filled,
                ARRAY[v_p1_attributes ->> 'client_order_id'],
                to_jsonb(v_filled_quantity::text)
              );
              v_native_fills := v_native_fills || jsonb_build_object(
                v_p1_attributes ->> 'native_fill_id', true
              );
              v_fill_count := v_fill_count + 1;
            ELSIF v_event_type = 'PositionObserved' THEN
              IF v_event_family <> 'POSITIONS'
                 OR v_attribute_count <> 9
                 OR NOT (v_p1_attributes ?& ARRAY[
                   'schema_version', 'sequence', 'simulation_time', 'origin',
                   'native_type', 'quantity', 'average_entry_price',
                   'realized_pnl', 'unrealized_pnl'
                 ]::text[])
                 OR v_p1_attributes ->> 'origin'
                      <> 'NAUTILUS_CACHE_OBSERVATION'
                 OR v_p1_attributes ->> 'native_type' <> 'Position'
                 OR (v_p1_attributes ->> 'quantity')::numeric < 0
                 OR (v_p1_attributes ->> 'average_entry_price')::numeric < 0 THEN
                RAISE EXCEPTION 'P1 event state transition is invalid'
                  USING ERRCODE = 'P2D04';
              END IF;
              v_last_position := v_p1_attributes;
            ELSIF v_event_type = 'AccountObserved' THEN
              IF v_event_family <> 'ACCOUNT_STATE'
                 OR v_attribute_count <> 9
                 OR NOT (v_p1_attributes ?& ARRAY[
                   'schema_version', 'sequence', 'simulation_time', 'origin',
                   'native_type', 'cash_balance', 'fees', 'realized_pnl',
                   'unrealized_pnl'
                 ]::text[])
                 OR v_p1_attributes ->> 'origin'
                      <> 'NAUTILUS_CACHE_OBSERVATION'
                 OR v_p1_attributes ->> 'native_type' <> 'Account'
                 OR (v_p1_attributes ->> 'cash_balance')::numeric < 0
                 OR (v_p1_attributes ->> 'fees')::numeric < 0 THEN
                RAISE EXCEPTION 'P1 event state transition is invalid'
                  USING ERRCODE = 'P2D04';
              END IF;
              v_last_account := v_p1_attributes;
            ELSIF v_event_type = 'RunCompleted' THEN
              IF v_ordinal <> (v_document ->> 'event_count')::bigint
                 OR v_event_family <> 'ENGINE_LIFECYCLE'
                 OR v_attribute_count <> 17
                 OR NOT (v_p1_attributes ?& ARRAY[
                   'schema_version', 'sequence', 'simulation_time', 'origin',
                   'runtime_family', 'engine_version', 'upstream_commit',
                   'closure_digest', 'target_count', 'order_count',
                   'fill_count', 'final_cash', 'final_position', 'fees',
                   'realized_pnl', 'unrealized_pnl', 'semantic_digest'
                 ]::text[])
                 OR v_p1_attributes ->> 'origin' <> 'CONTROL_PLANE' THEN
                RAISE EXCEPTION 'P1 event state transition is invalid'
                  USING ERRCODE = 'P2D04';
              END IF;
            ELSE
              RAISE EXCEPTION 'P1 event state transition is invalid'
                USING ERRCODE = 'P2D04';
            END IF;
          END LOOP;

          IF v_target_count = 0
             OR v_target_count <> (v_metadata ->> 'target_count')::bigint
             OR v_order_count <> (v_metadata ->> 'order_count')::bigint
             OR v_fill_count <> (v_metadata ->> 'fill_count')::bigint
             OR v_last_position IS NULL OR v_last_account IS NULL
             OR v_document -> 'events' ->
                  ((v_document ->> 'event_count')::integer - 3)
                    ->> 'event_type' IS DISTINCT FROM 'PositionObserved'
             OR v_document -> 'events' ->
                  ((v_document ->> 'event_count')::integer - 2)
                    ->> 'event_type' IS DISTINCT FROM 'AccountObserved'
             OR EXISTS (
               SELECT 1 FROM jsonb_object_keys(v_targets) AS target_ids(key)
               WHERE NOT (v_plans ? key)
             )
             OR EXISTS (
               SELECT 1 FROM jsonb_each_text(v_orders) AS order_rows(key, value)
               WHERE (v_filled ->> key)::numeric
                     <> (value::jsonb ->> 'quantity')::numeric
             )
             OR v_last_position ->> 'quantity' IS DISTINCT FROM
                  v_metadata ->> 'final_position'
             OR v_last_account ->> 'cash_balance' IS DISTINCT FROM
                  v_metadata ->> 'final_cash'
             OR v_last_account ->> 'fees' IS DISTINCT FROM
                  v_metadata ->> 'fees'
             OR v_last_account ->> 'realized_pnl' IS DISTINCT FROM
                  v_metadata ->> 'realized_pnl'
             OR v_last_account ->> 'unrealized_pnl' IS DISTINCT FROM
                  v_metadata ->> 'unrealized_pnl'
             OR v_last_position ->> 'realized_pnl' IS DISTINCT FROM
                  v_last_account ->> 'realized_pnl'
             OR v_last_position ->> 'unrealized_pnl' IS DISTINCT FROM
                  v_last_account ->> 'unrealized_pnl' THEN
            RAISE EXCEPTION 'P1 event state transition is invalid'
              USING ERRCODE = 'P2D04';
          END IF;
          IF encode(
               public.digest(
                 convert_to(
                   public.canonical_domain_json(v_semantic_events), 'UTF8'
                 ),
                 'sha256'
               ),
               'hex'
             ) IS DISTINCT FROM v_metadata ->> 'semantic_digest' THEN
            RAISE EXCEPTION 'P1 semantic projection digest is invalid'
              USING ERRCODE = 'P2D04';
          END IF;

          BEGIN
            v_first_envelope := (
              v_document -> 'events' -> 0 ->> 'canonical_json'
            )::jsonb;
            v_last_envelope := (
              v_document -> 'events' ->
                (jsonb_array_length(v_document -> 'events') - 1)
                ->> 'canonical_json'
            )::jsonb;
          EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'P1 completion envelope is invalid'
              USING ERRCODE = 'P2D04';
          END;
          v_semantic_digest := v_metadata ->> 'semantic_digest';
          v_batch_sha256 := v_document ->> 'batch_sha256';
          IF v_first_envelope #>> '{payload,event_type}'
                IS DISTINCT FROM 'RunStarted'
             OR v_last_envelope #>> '{payload,event_type}'
                IS DISTINCT FROM 'RunCompleted'
             OR jsonb_path_query_first(
                  v_first_envelope,
                  '$.payload.attributes[*] ? (@.name == "runtime_family").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'runtime_family'
             OR jsonb_path_query_first(
                  v_first_envelope,
                  '$.payload.attributes[*] ? (@.name == "engine_version").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'engine_version'
             OR jsonb_path_query_first(
                  v_first_envelope,
                  '$.payload.attributes[*] ? (@.name == "upstream_commit").value'
                ) #>> '{}' IS DISTINCT FROM
                  v_metadata ->> 'engine_upstream_commit'
             OR jsonb_path_query_first(
                  v_first_envelope,
                  '$.payload.attributes[*] ? (@.name == "closure_digest").value'
                ) #>> '{}' IS DISTINCT FROM
                  v_metadata ->> 'p1_product_closure_sha256'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "runtime_family").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'runtime_family'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "engine_version").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'engine_version'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "upstream_commit").value'
                ) #>> '{}' IS DISTINCT FROM
                  v_metadata ->> 'engine_upstream_commit'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "closure_digest").value'
                ) #>> '{}' IS DISTINCT FROM
                  v_metadata ->> 'p1_product_closure_sha256'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "semantic_digest").value'
                ) #>> '{}' IS DISTINCT FROM v_semantic_digest
             OR jsonb_array_length(jsonb_path_query_array(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "semantic_digest").value'
                )) <> 1
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "target_count").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'target_count'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "order_count").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'order_count'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "fill_count").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'fill_count'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "final_cash").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'final_cash'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "final_position").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'final_position'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "fees").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'fees'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "realized_pnl").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'realized_pnl'
             OR jsonb_path_query_first(
                  v_last_envelope,
                  '$.payload.attributes[*] ? (@.name == "unrealized_pnl").value'
                ) #>> '{}' IS DISTINCT FROM v_metadata ->> 'unrealized_pnl' THEN
            RAISE EXCEPTION 'P1 completion differs from validation metadata'
              USING ERRCODE = 'P2D04';
          END IF;

          SELECT octet_length(string_agg(
                   item ->> 'canonical_json' || chr(10), '' ORDER BY ordinal
                 ))
          INTO v_raw_size
          FROM jsonb_array_elements(v_document -> 'events')
            WITH ORDINALITY AS entries(item, ordinal);
          v_expected_ingestion_digest := encode(
            public.digest(
              convert_to(
                public.canonical_domain_json(jsonb_build_object(
                  'artifact_type', 'engine_event_batch',
                  'media_type', 'application/x-ndjson',
                  'relative_ref',
                    'engine-results/' || (v_document ->> 'job_id') || '/'
                    || (v_document ->> 'attempt_id') || '/'
                    || v_batch_sha256 || '.jsonl',
                  'sha256', v_batch_sha256,
                  'size_bytes', v_raw_size,
                  'truncated', false,
                  'validation_metadata', v_metadata,
                  'validator_id', 'nautilus-p1-event-stream-v1'
                )),
                'UTF8'
              ),
              'sha256'
            ),
            'hex'
          );
          IF v_document ->> 'ingestion_digest' IS DISTINCT FROM
               v_expected_ingestion_digest THEN
            RAISE EXCEPTION 'P1 ingestion identity is invalid'
              USING ERRCODE = 'P2D04';
          END IF;

          v_legacy_document := public.canonical_domain_json_string(
            (v_document - 'validation_metadata' - 'validator_id')::text
          );
          SELECT * INTO accepted
          FROM public.ingest_engine_event_batch(v_legacy_document);
          IF NOT FOUND OR accepted.batch_sha256 IS DISTINCT FROM v_batch_sha256 THEN
            RAISE EXCEPTION 'P1 batch receipt differs from projection authority'
              USING ERRCODE = 'P2D01';
          END IF;

          SELECT stored.* INTO projection
          FROM public.engine_run_projections AS stored
          WHERE stored.engine_run_id = accepted.engine_run_id
          FOR UPDATE;
          IF NOT FOUND
             OR (
               projection.batch_sha256 IS NOT NULL
               AND (
                 projection.batch_sha256 IS DISTINCT FROM v_batch_sha256
                 OR projection.semantic_digest IS DISTINCT FROM v_semantic_digest
                 OR projection.request_message_id IS DISTINCT FROM
                      (v_metadata ->> 'request_message_id')::uuid
               )
             ) THEN
            RAISE EXCEPTION 'P1 projection conflicts with durable authority'
              USING ERRCODE = 'P2D01';
          END IF;
          UPDATE public.engine_run_projections AS stored
          SET batch_sha256 = v_batch_sha256,
              semantic_digest = v_semantic_digest,
              request_message_id =
                (v_metadata ->> 'request_message_id')::uuid,
              updated_at = transaction_timestamp()
          WHERE stored.engine_run_id = accepted.engine_run_id;

          RETURN QUERY SELECT
            accepted.batch_sha256, accepted.ingestion_digest,
            accepted.job_id, accepted.attempt_id, accepted.engine_run_id,
            accepted.event_count, accepted.first_sequence,
            accepted.last_sequence, accepted.last_digest;
        END;
        $ingest_p1_engine_event_batch_v2$;

        CREATE FUNCTION job_plane.ingest_legacy_engine_job_result_v2(
          p_job_id text,
          p_attempt_id text,
          p_worker_id text,
          p_lease_token text,
          p_batch_document text
        )
        RETURNS TABLE (
          batch_sha256 char(64),
          ingestion_digest char(64),
          job_id varchar(36),
          attempt_id varchar(40),
          engine_run_id uuid,
          event_count bigint,
          first_sequence bigint,
          last_sequence bigint,
          last_digest char(64)
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $ingest_legacy_engine_job_result_v2$
        DECLARE
          v_document jsonb;
          v_event jsonb;
          v_envelope jsonb;
          v_attribute jsonb;
          accepted record;
        BEGIN
          IF session_user <> 'trading_job_worker' THEN
            RAISE EXCEPTION 'legacy engine job result authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_batch_document IS NULL
             OR octet_length(p_batch_document) > 67108864 THEN
            RAISE EXCEPTION 'legacy engine job result document exceeds the bound'
              USING ERRCODE = 'P2D04';
          END IF;
          BEGIN
            v_document := p_batch_document::jsonb;
          EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'legacy engine job result document is invalid'
              USING ERRCODE = 'P2D04';
          END;
          IF jsonb_typeof(v_document -> 'events') = 'array' THEN
            FOR v_event IN
              SELECT event_row.value
              FROM jsonb_array_elements(v_document -> 'events') AS event_row
            LOOP
              IF jsonb_typeof(v_event -> 'canonical_json') = 'string' THEN
                BEGIN
                  v_envelope := (v_event ->> 'canonical_json')::jsonb;
                EXCEPTION WHEN invalid_text_representation THEN
                  RAISE EXCEPTION 'legacy engine event envelope is invalid'
                    USING ERRCODE = 'P2D04';
                END;
                IF jsonb_typeof(v_envelope #> '{payload,attributes}') = 'array' THEN
                  FOR v_attribute IN
                    SELECT attribute_row.value
                    FROM jsonb_array_elements(
                      v_envelope #> '{payload,attributes}'
                    ) AS attribute_row
                  LOOP
                    IF jsonb_typeof(v_attribute) = 'object'
                       AND v_attribute ->> 'name' = 'schema_version'
                       AND v_attribute ->> 'value'
                             = 'nautilus-p1-event-stream-v1' THEN
                      RAISE EXCEPTION
                        'legacy engine job result contains P1 authority'
                        USING ERRCODE = 'P2D04';
                    END IF;
                  END LOOP;
                END IF;
              END IF;
            END LOOP;
          END IF;
          SELECT * INTO accepted
          FROM job_plane.ingest_engine_job_result(
            p_job_id, p_attempt_id, p_worker_id, p_lease_token,
            p_batch_document
          );
          RETURN QUERY SELECT
            accepted.batch_sha256, accepted.ingestion_digest,
            accepted.job_id, accepted.attempt_id, accepted.engine_run_id,
            accepted.event_count, accepted.first_sequence,
            accepted.last_sequence, accepted.last_digest;
        END;
        $ingest_legacy_engine_job_result_v2$;

        CREATE FUNCTION job_plane.ingest_engine_job_result_v2(
          p_job_id text,
          p_attempt_id text,
          p_worker_id text,
          p_lease_token text,
          p_batch_document text
        )
        RETURNS TABLE (
          batch_sha256 char(64),
          ingestion_digest char(64),
          job_id varchar(36),
          attempt_id varchar(40),
          engine_run_id uuid,
          event_count bigint,
          first_sequence bigint,
          last_sequence bigint,
          last_digest char(64)
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $ingest_engine_job_result_v2$
        DECLARE
          v_document jsonb;
          v_legacy_document text;
          v_first_envelope jsonb;
          v_expected_request_message_id uuid;
          v_expected_correlation_id uuid;
          v_expected_causation_id uuid;
          v_expected_engine_run_id uuid;
          v_expected_config_digest text;
          v_uuid_purpose text;
          v_uuid_hex text;
          v_derived_uuid uuid;
          accepted record;
          bound record;
          current_job public.jobs%ROWTYPE;
          current_attempt public.job_attempts%ROWTYPE;
          current_heartbeat public.worker_heartbeats%ROWTYPE;
        BEGIN
          IF session_user <> 'trading_job_worker' THEN
            RAISE EXCEPTION 'P1 engine job result authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_job_id IS NULL OR p_job_id !~ '^job_[0-9a-f]{32}$'
             OR p_attempt_id IS NULL
             OR p_attempt_id !~ '^attempt_[0-9a-f]{32}$'
             OR p_worker_id IS NULL
             OR p_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_lease_token IS NULL
             OR p_lease_token !~ '^[A-Za-z0-9_-]{16,128}$' THEN
            RAISE EXCEPTION 'P1 engine job result identity rejected'
              USING ERRCODE = '22023';
          END IF;
          IF p_batch_document IS NULL
             OR octet_length(p_batch_document) > 67108864 THEN
            RAISE EXCEPTION 'P1 engine job result document exceeds the bound'
              USING ERRCODE = 'P2D04';
          END IF;
          SELECT job_row.* INTO current_job
          FROM public.jobs AS job_row
          WHERE job_row.job_id = p_job_id
            AND job_row.job_type = 'BACKTEST'
            AND job_plane.paper_worker_job_allowed(
                  job_row.job_type, job_row.payload
                )
          FOR UPDATE;
          IF NOT FOUND
             OR current_job.state <> 'RUNNING'
             OR current_job.lease_owner IS DISTINCT FROM p_worker_id
             OR current_job.lease_token IS DISTINCT FROM p_lease_token
             OR current_job.lease_expires_at <= statement_timestamp() THEN
            RAISE EXCEPTION 'P1 engine job current job authority rejected'
              USING ERRCODE = 'P2D01';
          END IF;
          SELECT attempt_row.* INTO current_attempt
          FROM public.job_attempts AS attempt_row
          WHERE attempt_row.attempt_id = p_attempt_id
            AND attempt_row.job_id = p_job_id
          FOR UPDATE;
          IF NOT FOUND
             OR current_attempt.attempt_number <> current_job.attempt_count
             OR current_attempt.outcome <> 'RUNNING'
             OR current_attempt.worker_id IS DISTINCT FROM p_worker_id
             OR current_attempt.lease_token IS DISTINCT FROM p_lease_token
             OR current_attempt.lease_expires_at <= statement_timestamp() THEN
            RAISE EXCEPTION 'P1 engine job current attempt authority rejected'
              USING ERRCODE = 'P2D01';
          END IF;
          SELECT heartbeat_row.* INTO current_heartbeat
          FROM public.worker_heartbeats AS heartbeat_row
          WHERE heartbeat_row.worker_id = p_worker_id
          FOR UPDATE;
          IF NOT FOUND
             OR current_heartbeat.status <> 'BUSY'
             OR current_heartbeat.current_job_id IS DISTINCT FROM p_job_id
             OR current_heartbeat.current_attempt_id IS DISTINCT FROM p_attempt_id
             OR current_heartbeat.code_commit
                  !~ '^([0-9a-f]{40}|[0-9a-f]{64})$' THEN
            RAISE EXCEPTION 'P1 engine job worker authority rejected'
              USING ERRCODE = 'P2D01';
          END IF;

          FOR v_uuid_purpose IN
            SELECT unnest(ARRAY[
              'message', 'correlation', 'causation', 'engine-run'
            ]::text[])
          LOOP
            v_uuid_hex := encode(
              public.digest(
                uuid_send(
                  '6ba7b811-9dad-11d1-80b4-00c04fd430c8'::uuid
                ) || convert_to(
                  'trading-agent:engine-command:v1:' || p_job_id || ':'
                  || p_attempt_id || ':'
                  || current_attempt.attempt_number::text || ':'
                  || v_uuid_purpose,
                  'UTF8'
                ),
                'sha1'
              ),
              'hex'
            );
            v_uuid_hex := overlay(v_uuid_hex placing '5' from 13 for 1);
            v_uuid_hex := overlay(
              v_uuid_hex
              placing substr(
                '88889999aaaabbbb',
                position(substr(v_uuid_hex, 17, 1) in '0123456789abcdef'),
                1
              )
              from 17 for 1
            );
            v_derived_uuid := (
              substr(v_uuid_hex, 1, 8) || '-'
              || substr(v_uuid_hex, 9, 4) || '-'
              || substr(v_uuid_hex, 13, 4) || '-'
              || substr(v_uuid_hex, 17, 4) || '-'
              || substr(v_uuid_hex, 21, 12)
            )::uuid;
            CASE v_uuid_purpose
              WHEN 'message' THEN
                v_expected_request_message_id := v_derived_uuid;
              WHEN 'correlation' THEN
                v_expected_correlation_id := v_derived_uuid;
              WHEN 'causation' THEN
                v_expected_causation_id := v_derived_uuid;
              WHEN 'engine-run' THEN
                v_expected_engine_run_id := v_derived_uuid;
            END CASE;
          END LOOP;
          v_expected_config_digest := encode(
            public.digest(
              convert_to(
                public.canonical_domain_json(jsonb_build_object(
                  'engine_configuration',
                    current_job.payload #>
                      '{engine_backtest,engine_configuration}',
                  'instrument_catalog',
                    current_job.payload #>
                      '{engine_backtest,instrument_catalog}',
                  'strategy_configuration',
                    current_job.payload #>
                      '{engine_backtest,strategy_configuration}'
                )),
                'UTF8'
              ),
              'sha256'
            ),
            'hex'
          );
          BEGIN
            v_document := p_batch_document::jsonb;
            v_first_envelope := (
              v_document -> 'events' -> 0 ->> 'canonical_json'
            )::jsonb;
          EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'P1 job result request authority rejected'
              USING ERRCODE = 'P2D04';
          END;
          IF v_document #>> '{validation_metadata,job_id}'
                IS DISTINCT FROM p_job_id
             OR v_document #>> '{validation_metadata,attempt_id}'
                  IS DISTINCT FROM p_attempt_id
             OR v_document #>> '{validation_metadata,request_message_id}'
                  IS DISTINCT FROM v_expected_request_message_id::text
             OR v_document #>> '{validation_metadata,engine_run_id}'
                  IS DISTINCT FROM v_expected_engine_run_id::text
             OR v_document #>> '{validation_metadata,config_digest}'
                  IS DISTINCT FROM v_expected_config_digest
             OR v_document #>> '{validation_metadata,source_commit}'
                  IS DISTINCT FROM current_heartbeat.code_commit
             OR v_first_envelope ->> 'correlation_id'
                  IS DISTINCT FROM v_expected_correlation_id::text
             OR v_first_envelope ->> 'causation_id'
                  IS DISTINCT FROM v_expected_causation_id::text
             OR v_first_envelope ->> 'engine_run_id'
                  IS DISTINCT FROM v_expected_engine_run_id::text
             OR v_first_envelope ->> 'config_digest'
                  IS DISTINCT FROM v_expected_config_digest
             OR v_first_envelope ->> 'producer_identity'
                  IS DISTINCT FROM p_worker_id
             OR v_first_envelope ->> 'source_commit'
                  IS DISTINCT FROM current_heartbeat.code_commit
             OR v_first_envelope ->> 'event_time'
                  IS DISTINCT FROM v_first_envelope ->> 'initialization_time' THEN
            RAISE EXCEPTION 'P1 job result request authority rejected'
              USING ERRCODE = 'P2D04';
          END IF;
          SELECT * INTO accepted
          FROM job_plane.ingest_p1_engine_event_batch_v2(
            p_batch_document,
            v_expected_request_message_id,
            v_expected_correlation_id,
            v_expected_causation_id,
            v_expected_engine_run_id,
            v_expected_config_digest,
            current_job.payload #>>
              '{engine_backtest,instrument_catalog,sha256}',
            current_job.payload #>> '{engine_backtest,market_data,sha256}',
            p_worker_id,
            current_heartbeat.code_commit
          );
          v_legacy_document := public.canonical_domain_json_string(
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
            bound.last_sequence, bound.last_digest;
        END;
        $ingest_engine_job_result_v2$;

        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.paper_worker_job_id_allowed(text)
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        GRANT EXECUTE ON FUNCTION
          job_plane.paper_worker_job_id_allowed(text)
          TO trading_job_worker;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.ingest_p1_engine_event_batch_v2(
            text, uuid, uuid, uuid, uuid, text, text, text, text, text
          )
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION
          public.engine_run_completion_append_guard()
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.ingest_legacy_engine_job_result_v2(
            text, text, text, text, text
          )
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        REVOKE EXECUTE ON FUNCTION
          job_plane.ingest_engine_job_result(text, text, text, text, text)
          FROM trading_job_worker;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.ingest_engine_job_result_v2(text, text, text, text, text)
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        GRANT EXECUTE ON FUNCTION
          job_plane.ingest_legacy_engine_job_result_v2(
            text, text, text, text, text
          )
          TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION
          job_plane.ingest_engine_job_result_v2(text, text, text, text, text)
          TO trading_job_worker;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0012 P1 projection authority is forward-only; use a reviewed forward repair"
    )

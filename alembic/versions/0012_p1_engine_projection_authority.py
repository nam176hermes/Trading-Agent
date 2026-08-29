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
          ADD CONSTRAINT engine_run_projection_result_authority_complete CHECK (
            (batch_sha256 IS NULL AND semantic_digest IS NULL)
            OR (
              batch_sha256 IS NOT NULL
              AND semantic_digest IS NOT NULL
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

        CREATE FUNCTION public.ingest_engine_event_batch_v2(
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
        AS $ingest_engine_event_batch_v2$
        DECLARE
          v_document jsonb;
          v_metadata jsonb;
          v_legacy_document text;
          v_first_envelope jsonb;
          v_last_envelope jsonb;
          v_semantic_digest text;
          v_batch_sha256 text;
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

          -- Exact legacy documents retain the reviewed 0010 behavior.
          IF NOT (v_document ? 'validator_id')
             AND NOT (v_document ? 'validation_metadata') THEN
            RETURN QUERY SELECT *
            FROM public.ingest_engine_event_batch(p_batch_document);
            RETURN;
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
             OR v_metadata ->> 'attempt_id' IS DISTINCT FROM
                  v_document ->> 'attempt_id'
             OR v_metadata ->> 'job_id' IS DISTINCT FROM
                  v_document ->> 'job_id'
             OR v_metadata ->> 'engine_run_id' IS DISTINCT FROM
                  v_document ->> 'engine_run_id'
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
             OR (v_metadata ->> 'source_commit') !~ '^[0-9a-f]{40}$' THEN
            RAISE EXCEPTION 'P1 validation metadata is invalid'
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
               )
             ) THEN
            RAISE EXCEPTION 'P1 projection conflicts with durable authority'
              USING ERRCODE = 'P2D01';
          END IF;
          UPDATE public.engine_run_projections AS stored
          SET batch_sha256 = v_batch_sha256,
              semantic_digest = v_semantic_digest,
              updated_at = transaction_timestamp()
          WHERE stored.engine_run_id = accepted.engine_run_id;

          RETURN QUERY SELECT
            accepted.batch_sha256, accepted.ingestion_digest,
            accepted.job_id, accepted.attempt_id, accepted.engine_run_id,
            accepted.event_count, accepted.first_sequence,
            accepted.last_sequence, accepted.last_digest;
        END;
        $ingest_engine_event_batch_v2$;

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
          accepted record;
          bound record;
        BEGIN
          SELECT * INTO accepted
          FROM public.ingest_engine_event_batch_v2(p_batch_document);
          v_document := p_batch_document::jsonb;
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
          public.ingest_engine_event_batch_v2(text)
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION
          public.engine_run_completion_append_guard()
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.ingest_engine_job_result_v2(text, text, text, text, text)
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        GRANT EXECUTE ON FUNCTION
          job_plane.ingest_engine_job_result_v2(text, text, text, text, text)
          TO trading_job_worker;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0012 P1 projection authority is forward-only; use a reviewed forward repair"
    )

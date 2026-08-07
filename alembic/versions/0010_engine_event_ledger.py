"""Add protected durable engine-event ingestion.

Revision ID: 0010_engine_event_ledger
Revises: 0009_canonical_market_data

The migration is source authority only. Applying it requires the separately
reviewed disposable/production migration workflow; importing this module does
not touch a database.
"""
from __future__ import annotations

from alembic import op


revision = "0010_engine_event_ledger"
down_revision = "0009_canonical_market_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        -- Source-contract proof does not prove PostgreSQL runtime behavior.
        -- Runtime proof requires a separately approved disposable PostgreSQL fixture.
        CREATE TABLE public.engine_event_batch_receipts (
          batch_sha256 char(64) PRIMARY KEY
            CHECK (batch_sha256 ~ '^[0-9a-f]{64}$'),
          ingestion_digest char(64) NOT NULL
            CHECK (ingestion_digest ~ '^[0-9a-f]{64}$'),
          job_id varchar(36) NOT NULL
            CHECK (job_id ~ '^job_[0-9a-f]{32}$'),
          attempt_id varchar(40) NOT NULL
            CHECK (attempt_id ~ '^attempt_[0-9a-f]{32}$'),
          engine_run_id uuid NOT NULL,
          event_count bigint NOT NULL CHECK (event_count BETWEEN 1 AND 4096),
          first_sequence bigint NOT NULL CHECK (first_sequence > 0),
          last_sequence bigint NOT NULL CHECK (last_sequence >= first_sequence),
          last_digest char(64) NOT NULL CHECK (last_digest ~ '^[0-9a-f]{64}$'),
          ingested_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CHECK (last_sequence - first_sequence + 1 = event_count)
        );
        CREATE INDEX engine_event_receipts_run_sequence_idx
          ON public.engine_event_batch_receipts (engine_run_id, first_sequence);

        CREATE TABLE public.engine_events (
          message_id uuid PRIMARY KEY,
          engine_run_id uuid NOT NULL,
          stream_sequence bigint NOT NULL CHECK (stream_sequence > 0),
          event_type varchar(128) NOT NULL
            CHECK (event_type ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
          event_family varchar(32) NOT NULL CHECK (event_family IN (
            'ENGINE_LIFECYCLE', 'MARKET_DATA_CONTINUITY',
            'STRATEGY_LIFECYCLE', 'ORDER_LIFECYCLE', 'FILLS', 'POSITIONS',
            'ACCOUNT_STATE', 'RUNTIME_RISK', 'RECONCILIATION', 'HEALTH', 'HALT'
          )),
          canonical_json jsonb NOT NULL,
          canonical_json_text text NOT NULL,
          digest char(64) NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
          batch_sha256 char(64) NOT NULL,
          recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CHECK (canonical_json_text::jsonb = canonical_json),
          CHECK (
            canonical_json_text =
              public.canonical_domain_json_string(canonical_json_text)
          ),
          CHECK (
            digest = encode(
              public.digest(convert_to(canonical_json_text, 'UTF8'), 'sha256'),
              'hex'
            )
          ),
          UNIQUE (engine_run_id, stream_sequence),
          FOREIGN KEY (batch_sha256)
            REFERENCES public.engine_event_batch_receipts(batch_sha256)
            ON DELETE RESTRICT
        );
        CREATE INDEX engine_events_run_sequence_idx
          ON public.engine_events (engine_run_id, stream_sequence, message_id);
        CREATE INDEX engine_events_batch_idx
          ON public.engine_events (batch_sha256, stream_sequence);

        CREATE TABLE public.engine_run_projections (
          engine_run_id uuid PRIMARY KEY,
          event_count bigint NOT NULL CHECK (event_count > 0),
          event_type_counts jsonb NOT NULL
            CHECK (jsonb_typeof(event_type_counts) = 'array'),
          last_sequence bigint NOT NULL CHECK (last_sequence > 0),
          last_digest char(64) NOT NULL CHECK (last_digest ~ '^[0-9a-f]{64}$'),
          updated_at timestamptz NOT NULL DEFAULT transaction_timestamp()
        );

        CREATE FUNCTION public.ingest_engine_event_batch(p_batch_document text)
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
        AS $ingest_engine_event_batch$
        DECLARE
          v_document jsonb;
          v_event jsonb;
          v_envelope jsonb;
          v_ordinal bigint;
          v_total bigint;
          v_valid bigint;
          v_batch_sha256 text;
          v_ingestion_digest text;
          v_job_id text;
          v_attempt_id text;
          v_engine_run_id uuid;
          v_event_count bigint;
          v_first_sequence bigint;
          v_last_sequence bigint;
          v_last_digest text;
          v_expected_sequence numeric;
          v_actual_sequence bigint;
          v_message_id uuid;
          v_event_type text;
          v_event_family text;
          v_canonical_json_text text;
          v_digest text;
          v_existing_digest text;
          v_computed_batch_sha256 text;
          v_prior public.engine_event_batch_receipts%ROWTYPE;
        BEGIN
          IF p_batch_document IS NULL
             OR octet_length(p_batch_document) > 67108864 THEN
            RAISE EXCEPTION 'engine-event batch document exceeds the supported bound'
              USING ERRCODE = 'P2D04';
          END IF;
          BEGIN
            v_document := p_batch_document::jsonb;
          EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'engine-event batch document is invalid JSON'
              USING ERRCODE = 'P2D04';
          END;
          IF p_batch_document IS DISTINCT FROM
               public.canonical_domain_json_string(p_batch_document)
             OR jsonb_typeof(v_document) <> 'object' THEN
            RAISE EXCEPTION 'engine-event batch document is not canonical'
              USING ERRCODE = 'P2D04';
          END IF;
          SELECT count(*), count(*) FILTER (
            WHERE key = ANY (ARRAY[
              'attempt_id', 'batch_sha256', 'engine_run_id', 'event_count',
              'events', 'first_sequence', 'ingestion_digest', 'job_id',
              'last_digest', 'last_sequence'
            ]::text[])
          ) INTO v_total, v_valid
          FROM jsonb_object_keys(v_document) AS keys(key);
          IF v_total <> 10 OR v_valid <> 10
             OR jsonb_typeof(v_document -> 'events') <> 'array'
             OR jsonb_typeof(v_document -> 'batch_sha256') <> 'string'
             OR jsonb_typeof(v_document -> 'ingestion_digest') <> 'string'
             OR jsonb_typeof(v_document -> 'job_id') <> 'string'
             OR jsonb_typeof(v_document -> 'attempt_id') <> 'string'
             OR jsonb_typeof(v_document -> 'engine_run_id') <> 'string'
             OR jsonb_typeof(v_document -> 'event_count') <> 'number'
             OR jsonb_typeof(v_document -> 'first_sequence') <> 'number'
             OR jsonb_typeof(v_document -> 'last_sequence') <> 'number'
             OR jsonb_typeof(v_document -> 'last_digest') <> 'string' THEN
            RAISE EXCEPTION 'engine-event batch fields are invalid'
              USING ERRCODE = 'P2D04';
          END IF;

          v_batch_sha256 := v_document ->> 'batch_sha256';
          v_ingestion_digest := v_document ->> 'ingestion_digest';
          v_job_id := v_document ->> 'job_id';
          v_attempt_id := v_document ->> 'attempt_id';
          IF v_batch_sha256 !~ '^[0-9a-f]{64}$'
             OR v_ingestion_digest !~ '^[0-9a-f]{64}$'
             OR v_job_id !~ '^job_[0-9a-f]{32}$'
             OR v_attempt_id !~ '^attempt_[0-9a-f]{32}$'
             OR (v_document ->> 'engine_run_id') !~
                  '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
             OR (v_document ->> 'event_count') !~ '^[1-9][0-9]*$'
             OR (v_document ->> 'first_sequence') !~ '^[1-9][0-9]*$'
             OR (v_document ->> 'last_sequence') !~ '^[1-9][0-9]*$'
             OR (v_document ->> 'last_digest') !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'engine-event batch identity is invalid'
              USING ERRCODE = 'P2D04';
          END IF;
          IF length(v_document ->> 'event_count') > 19
             OR length(v_document ->> 'first_sequence') > 19
             OR length(v_document ->> 'last_sequence') > 19
             OR (v_document ->> 'event_count')::numeric > 9223372036854775807
             OR (v_document ->> 'first_sequence')::numeric > 9223372036854775807
             OR (v_document ->> 'last_sequence')::numeric > 9223372036854775807 THEN
            RAISE EXCEPTION 'engine-event batch sequence is outside bigint bounds'
              USING ERRCODE = 'P2D04';
          END IF;
          v_engine_run_id := (v_document ->> 'engine_run_id')::uuid;
          v_event_count := (v_document ->> 'event_count')::bigint;
          v_first_sequence := (v_document ->> 'first_sequence')::bigint;
          v_last_sequence := (v_document ->> 'last_sequence')::bigint;
          v_last_digest := v_document ->> 'last_digest';
          IF v_event_count NOT BETWEEN 1 AND 4096
             OR jsonb_array_length(v_document -> 'events') <> v_event_count THEN
            RAISE EXCEPTION 'engine-event batch sequence shape is invalid'
              USING ERRCODE = 'P2D04';
          END IF;

          v_expected_sequence := v_first_sequence;
          FOR v_event, v_ordinal IN
            SELECT item, ordinal
            FROM jsonb_array_elements(v_document -> 'events')
              WITH ORDINALITY AS entries(item, ordinal)
          LOOP
            IF jsonb_typeof(v_event) <> 'object' THEN
              RAISE EXCEPTION 'engine-event record is invalid'
                USING ERRCODE = 'P2D04';
            END IF;
            SELECT count(*), count(*) FILTER (
              WHERE key = ANY (ARRAY[
                'batch_sha256', 'canonical_json', 'digest', 'engine_run_id',
                'event_family', 'event_type', 'message_id', 'stream_sequence'
              ]::text[])
            ) INTO v_total, v_valid
            FROM jsonb_object_keys(v_event) AS keys(key);
            IF v_total <> 8 OR v_valid <> 8
               OR jsonb_typeof(v_event -> 'batch_sha256') <> 'string'
               OR jsonb_typeof(v_event -> 'canonical_json') <> 'string'
               OR jsonb_typeof(v_event -> 'digest') <> 'string'
               OR jsonb_typeof(v_event -> 'engine_run_id') <> 'string'
               OR jsonb_typeof(v_event -> 'event_family') <> 'string'
               OR jsonb_typeof(v_event -> 'event_type') <> 'string'
               OR jsonb_typeof(v_event -> 'message_id') <> 'string'
               OR jsonb_typeof(v_event -> 'stream_sequence') <> 'number'
               OR (v_event ->> 'message_id') !~
                    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               OR (v_event ->> 'stream_sequence') !~ '^[1-9][0-9]*$'
               OR (v_event ->> 'digest') !~ '^[0-9a-f]{64}$'
               OR (v_event ->> 'event_type') !~
                    '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'
               OR (v_event ->> 'event_family') NOT IN (
                 'ENGINE_LIFECYCLE', 'MARKET_DATA_CONTINUITY',
                 'STRATEGY_LIFECYCLE', 'ORDER_LIFECYCLE', 'FILLS', 'POSITIONS',
                 'ACCOUNT_STATE', 'RUNTIME_RISK', 'RECONCILIATION', 'HEALTH', 'HALT'
               ) THEN
              RAISE EXCEPTION 'engine-event record fields are invalid'
                USING ERRCODE = 'P2D04';
            END IF;
            IF length(v_event ->> 'stream_sequence') > 19
               OR (v_event ->> 'stream_sequence')::numeric > 9223372036854775807 THEN
              RAISE EXCEPTION 'engine-event record sequence is outside bigint bounds'
                USING ERRCODE = 'P2D04';
            END IF;
            v_actual_sequence := (v_event ->> 'stream_sequence')::bigint;
            v_canonical_json_text := v_event ->> 'canonical_json';
            v_digest := v_event ->> 'digest';
            IF v_actual_sequence <> v_expected_sequence THEN
              RAISE EXCEPTION 'engine-event sequence is blocked inside batch'
                USING ERRCODE = CASE
                  WHEN v_actual_sequence > v_expected_sequence THEN 'P2D02'
                  ELSE 'P2D03'
                END,
                DETAIL = format(
                  'engine_run_id=%s;expected=%s;actual=%s',
                  v_engine_run_id, v_expected_sequence, v_actual_sequence
                );
            END IF;
            IF v_event ->> 'batch_sha256' IS DISTINCT FROM v_batch_sha256
               OR v_event ->> 'engine_run_id' IS DISTINCT FROM v_engine_run_id::text
               OR v_canonical_json_text IS DISTINCT FROM
                    public.canonical_domain_json_string(v_canonical_json_text)
               OR v_digest IS DISTINCT FROM encode(
                    public.digest(convert_to(v_canonical_json_text, 'UTF8'), 'sha256'),
                    'hex'
                  ) THEN
              RAISE EXCEPTION 'engine-event record seal is invalid'
                USING ERRCODE = 'P2D04';
            END IF;
            BEGIN
              v_envelope := v_canonical_json_text::jsonb;
            EXCEPTION WHEN invalid_text_representation THEN
              RAISE EXCEPTION 'engine-event canonical envelope is invalid'
                USING ERRCODE = 'P2D04';
            END;
            IF jsonb_typeof(v_envelope) <> 'object'
               OR v_envelope ->> 'message_id' IS DISTINCT FROM
                    v_event ->> 'message_id'
               OR v_envelope ->> 'engine_run_id' IS DISTINCT FROM
                    v_engine_run_id::text
               OR v_envelope ->> 'stream_sequence' IS DISTINCT FROM
                    v_actual_sequence::text
               OR v_envelope #>> '{payload,event_type}' IS DISTINCT FROM
                    v_event ->> 'event_type'
               OR v_envelope #>> '{payload,family}' IS DISTINCT FROM
                    v_event ->> 'event_family' THEN
              RAISE EXCEPTION 'engine-event envelope authority does not match record'
                USING ERRCODE = 'P2D04';
            END IF;
            v_expected_sequence := v_expected_sequence + 1;
          END LOOP;
          IF (v_document -> 'events' -> 0 ->> 'stream_sequence')::bigint
               <> v_first_sequence
             OR (v_document -> 'events' -> (v_event_count::integer - 1)
                   ->> 'stream_sequence')::bigint <> v_last_sequence
             OR v_document -> 'events' -> (v_event_count::integer - 1)
                   ->> 'digest' IS DISTINCT FROM v_last_digest THEN
            RAISE EXCEPTION 'engine-event receipt does not match records'
              USING ERRCODE = 'P2D04';
          END IF;
          SELECT encode(public.digest(convert_to(
                   string_agg(item ->> 'canonical_json' || chr(10), '' ORDER BY ordinal),
                   'UTF8'
                 ), 'sha256'), 'hex')
          INTO v_computed_batch_sha256
          FROM jsonb_array_elements(v_document -> 'events')
            WITH ORDINALITY AS entries(item, ordinal);
          IF v_computed_batch_sha256 IS DISTINCT FROM v_batch_sha256 THEN
            RAISE EXCEPTION 'engine-event batch digest is invalid'
              USING ERRCODE = 'P2D04';
          END IF;

          -- One batch lock and one per-run lock serialize exact retries and
          -- sequence advancement for this transaction-scoped write authority.
          PERFORM pg_advisory_xact_lock(
            hashtextextended(v_batch_sha256, 21)
          );
          PERFORM pg_advisory_xact_lock(
            hashtextextended(v_engine_run_id::text, 22)
          );
          SELECT * INTO v_prior
          FROM public.engine_event_batch_receipts AS receipt
          WHERE receipt.batch_sha256 = v_batch_sha256;
          IF FOUND THEN
            IF v_prior.ingestion_digest = v_ingestion_digest
               AND v_prior.job_id = v_job_id
               AND v_prior.attempt_id = v_attempt_id
               AND v_prior.engine_run_id = v_engine_run_id
               AND v_prior.event_count = v_event_count
               AND v_prior.first_sequence = v_first_sequence
               AND v_prior.last_sequence = v_last_sequence
               AND v_prior.last_digest = v_last_digest THEN
              RETURN QUERY SELECT
                v_prior.batch_sha256, v_prior.ingestion_digest,
                v_prior.job_id, v_prior.attempt_id, v_prior.engine_run_id,
                v_prior.event_count, v_prior.first_sequence,
                v_prior.last_sequence, v_prior.last_digest;
              RETURN;
            END IF;
            RAISE EXCEPTION 'conflicting engine-event batch receipt'
              USING ERRCODE = 'P2D01';
          END IF;

          -- Globally identical message IDs can arrive on different run locks.
          -- Acquire their transaction locks in UUID order before checking any
          -- identity so cross-run races cannot escape as a raw unique error.
          FOR v_message_id IN
            SELECT (item ->> 'message_id')::uuid
            FROM jsonb_array_elements(v_document -> 'events') AS entries(item)
            ORDER BY (item ->> 'message_id')::uuid
          LOOP
            PERFORM pg_advisory_xact_lock(
              hashtextextended(v_message_id::text, 23)
            );
          END LOOP;
          FOR v_event IN
            SELECT item FROM jsonb_array_elements(v_document -> 'events') AS entries(item)
          LOOP
            v_message_id := (v_event ->> 'message_id')::uuid;
            v_digest := v_event ->> 'digest';
            SELECT stored.digest INTO v_existing_digest
            FROM public.engine_events AS stored
            WHERE stored.message_id = v_message_id;
            IF FOUND AND v_existing_digest IS DISTINCT FROM v_digest THEN
              RAISE EXCEPTION 'conflicting engine-event message identity'
                USING ERRCODE = 'P2D01';
            END IF;
          END LOOP;

          SELECT projection.last_sequence::numeric + 1
          INTO v_expected_sequence
          FROM public.engine_run_projections AS projection
          WHERE projection.engine_run_id = v_engine_run_id;
          IF NOT FOUND THEN
            v_expected_sequence := 2;
          END IF;
          IF v_first_sequence <> v_expected_sequence THEN
            RAISE EXCEPTION 'engine-event sequence is blocked'
              USING ERRCODE = CASE
                WHEN v_first_sequence > v_expected_sequence THEN 'P2D02'
                ELSE 'P2D03'
              END,
              DETAIL = format(
                'engine_run_id=%s;expected=%s;actual=%s',
                v_engine_run_id, v_expected_sequence, v_first_sequence
              );
          END IF;

          INSERT INTO public.engine_event_batch_receipts (
            batch_sha256, ingestion_digest, job_id, attempt_id, engine_run_id,
            event_count, first_sequence, last_sequence, last_digest
          ) VALUES (
            v_batch_sha256, v_ingestion_digest, v_job_id, v_attempt_id,
            v_engine_run_id, v_event_count, v_first_sequence,
            v_last_sequence, v_last_digest
          );
          FOR v_event IN
            SELECT item FROM jsonb_array_elements(v_document -> 'events') AS entries(item)
          LOOP
            v_canonical_json_text := v_event ->> 'canonical_json';
            BEGIN
              INSERT INTO public.engine_events (
                message_id, engine_run_id, stream_sequence, event_type,
                event_family, canonical_json, canonical_json_text, digest,
                batch_sha256
              ) VALUES (
                (v_event ->> 'message_id')::uuid,
                v_engine_run_id,
                (v_event ->> 'stream_sequence')::bigint,
                v_event ->> 'event_type',
                v_event ->> 'event_family',
                v_canonical_json_text::jsonb,
                v_canonical_json_text,
                v_event ->> 'digest',
                v_batch_sha256
              );
            EXCEPTION WHEN unique_violation THEN
              RAISE EXCEPTION 'conflicting engine-event uniqueness authority'
                USING ERRCODE = 'P2D01';
            END;
          END LOOP;
          INSERT INTO public.engine_run_projections (
            engine_run_id, event_count, event_type_counts,
            last_sequence, last_digest
          )
          SELECT
            v_engine_run_id,
            sum(grouped.type_count),
            jsonb_agg(
              jsonb_build_object('event_type', grouped.event_type, 'count', grouped.type_count)
              ORDER BY grouped.event_type COLLATE "C"
            ),
            max(grouped.stream_sequence),
            (array_agg(grouped.digest ORDER BY grouped.stream_sequence DESC))[1]
          FROM (
            SELECT stored.event_type, count(*) AS type_count,
                   max(stored.stream_sequence) AS stream_sequence,
                   (array_agg(stored.digest ORDER BY stored.stream_sequence DESC))[1]
                     AS digest
            FROM public.engine_events AS stored
            WHERE stored.engine_run_id = v_engine_run_id
            GROUP BY stored.event_type
          ) AS grouped
          ON CONFLICT ON CONSTRAINT engine_run_projections_pkey DO UPDATE SET
            event_count = EXCLUDED.event_count,
            event_type_counts = EXCLUDED.event_type_counts,
            last_sequence = EXCLUDED.last_sequence,
            last_digest = EXCLUDED.last_digest,
            updated_at = transaction_timestamp();

          RETURN QUERY SELECT
            receipt.batch_sha256, receipt.ingestion_digest,
            receipt.job_id, receipt.attempt_id, receipt.engine_run_id,
            receipt.event_count, receipt.first_sequence,
            receipt.last_sequence, receipt.last_digest
          FROM public.engine_event_batch_receipts AS receipt
          WHERE receipt.batch_sha256 = v_batch_sha256;
        END;
        $ingest_engine_event_batch$;

        CREATE FUNCTION public.recover_engine_run_projections()
        RETURNS TABLE (
          engine_run_id uuid,
          event_count bigint,
          event_type_counts jsonb,
          last_sequence bigint,
          last_digest char(64)
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $recover_engine_run_projections$
        BEGIN
          -- SHARE conflicts with the ROW EXCLUSIVE lock taken by event INSERT.
          -- Recovery therefore sees all or none of every ingest transaction,
          -- and no stale replay can overwrite a newly advanced projection.
          LOCK TABLE public.engine_events IN SHARE MODE;
          IF EXISTS (
            SELECT 1
            FROM (
              SELECT stored.engine_run_id, stored.stream_sequence,
                     row_number() OVER (
                       PARTITION BY stored.engine_run_id
                       ORDER BY stored.stream_sequence
                     ) + 1 AS expected_sequence
              FROM public.engine_events AS stored
            ) AS ordered
            WHERE ordered.stream_sequence <> ordered.expected_sequence
          ) THEN
            RAISE EXCEPTION 'durable engine-event sequence is not recoverable'
              USING ERRCODE = 'P2D01';
          END IF;
          INSERT INTO public.engine_run_projections (
            engine_run_id, event_count, event_type_counts,
            last_sequence, last_digest
          )
          SELECT
            run.engine_run_id,
            run.event_count,
            run.event_type_counts,
            run.last_sequence,
            run.last_digest
          FROM (
            SELECT
              stored.engine_run_id,
              count(*) AS event_count,
              (
                SELECT jsonb_agg(
                  jsonb_build_object(
                    'event_type', counts.event_type,
                    'count', counts.type_count
                  ) ORDER BY counts.event_type COLLATE "C"
                )
                FROM (
                  SELECT typed.event_type, count(*) AS type_count
                  FROM public.engine_events AS typed
                  WHERE typed.engine_run_id = stored.engine_run_id
                  GROUP BY typed.event_type
                ) AS counts
              ) AS event_type_counts,
              max(stored.stream_sequence) AS last_sequence,
              (array_agg(stored.digest ORDER BY stored.stream_sequence DESC))[1]
                AS last_digest
            FROM public.engine_events AS stored
            GROUP BY stored.engine_run_id
          ) AS run
          ON CONFLICT (engine_run_id) DO UPDATE SET
            event_count = EXCLUDED.event_count,
            event_type_counts = EXCLUDED.event_type_counts,
            last_sequence = EXCLUDED.last_sequence,
            last_digest = EXCLUDED.last_digest,
            updated_at = transaction_timestamp();
          DELETE FROM public.engine_run_projections AS projection
          WHERE NOT EXISTS (
            SELECT 1 FROM public.engine_events AS stored
            WHERE stored.engine_run_id = projection.engine_run_id
          );
          RETURN QUERY SELECT
            projection.engine_run_id, projection.event_count,
            projection.event_type_counts, projection.last_sequence,
            projection.last_digest
          FROM public.engine_run_projections AS projection
          ORDER BY projection.engine_run_id;
        END;
        $recover_engine_run_projections$;

        CREATE FUNCTION public.engine_event_records_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $engine_event_records_append_only$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $engine_event_records_append_only$;
        CREATE TRIGGER engine_event_receipts_append_only
          BEFORE UPDATE OR DELETE ON public.engine_event_batch_receipts
          FOR EACH ROW EXECUTE FUNCTION public.engine_event_records_append_only();
        CREATE TRIGGER engine_event_receipts_reject_truncate
          BEFORE TRUNCATE ON public.engine_event_batch_receipts
          FOR EACH STATEMENT EXECUTE FUNCTION public.engine_event_records_append_only();
        CREATE TRIGGER engine_events_append_only
          BEFORE UPDATE OR DELETE ON public.engine_events
          FOR EACH ROW EXECUTE FUNCTION public.engine_event_records_append_only();
        CREATE TRIGGER engine_events_reject_truncate
          BEFORE TRUNCATE ON public.engine_events
          FOR EACH STATEMENT EXECUTE FUNCTION public.engine_event_records_append_only();

        REVOKE ALL PRIVILEGES ON TABLE public.engine_event_batch_receipts FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON TABLE public.engine_events FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON TABLE public.engine_run_projections FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.ingest_engine_event_batch(text) FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.recover_engine_run_projections() FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION public.engine_event_records_append_only() FROM PUBLIC;
        -- Runtime role grants are intentionally absent until reviewed activation.
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0010 engine-event ledger is forward-only; use a reviewed forward repair"
    )

"""Activate protected paper BACKTEST worker and durable result authority.

Revision ID: 0011_engine_backtest_worker_authority
Revises: 0010_engine_event_ledger

This is source authority only. Applying it requires the separately reviewed
migration workflow; importing this module never opens a database.
"""
from __future__ import annotations

from alembic import op


revision = "0011_engine_backtest_worker_authority"
down_revision = "0010_engine_event_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE public.engine_job_results (
          job_id varchar(64) PRIMARY KEY
            REFERENCES public.jobs(job_id) ON DELETE RESTRICT,
          batch_sha256 char(64) NOT NULL UNIQUE
            REFERENCES public.engine_event_batch_receipts(batch_sha256)
            ON DELETE RESTRICT,
          attempt_id varchar(64) NOT NULL,
          bound_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CHECK (job_id ~ '^job_[0-9a-f]{32}$'),
          CHECK (attempt_id ~ '^attempt_[0-9a-f]{32}$')
        );
        CREATE TRIGGER engine_job_results_append_only
          BEFORE UPDATE OR DELETE ON public.engine_job_results
          FOR EACH ROW EXECUTE FUNCTION public.engine_event_records_append_only();
        CREATE TRIGGER engine_job_results_reject_truncate
          BEFORE TRUNCATE ON public.engine_job_results
          FOR EACH STATEMENT EXECUTE FUNCTION public.engine_event_records_append_only();

        CREATE FUNCTION job_plane.ingest_engine_job_result(p_batch_document text)
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
        AS $ingest_engine_job_result$
        DECLARE
          accepted record;
          binding public.engine_job_results%ROWTYPE;
        BEGIN
          IF session_user <> 'trading_job_worker' THEN
            RAISE EXCEPTION 'engine job result authority rejected'
              USING ERRCODE = '42501';
          END IF;
          SELECT * INTO accepted
          FROM public.ingest_engine_event_batch(p_batch_document);
          IF NOT FOUND THEN
            RAISE EXCEPTION 'engine job result receipt is unavailable'
              USING ERRCODE = 'P2D01';
          END IF;

          INSERT INTO public.engine_job_results (
            job_id, batch_sha256, attempt_id
          ) VALUES (
            accepted.job_id, accepted.batch_sha256, accepted.attempt_id
          ) ON CONFLICT (job_id) DO NOTHING;

          SELECT result.* INTO binding
          FROM public.engine_job_results AS result
          WHERE result.job_id = accepted.job_id
          FOR SHARE;
          IF NOT FOUND
             OR binding.batch_sha256 IS DISTINCT FROM accepted.batch_sha256
             OR binding.attempt_id IS DISTINCT FROM accepted.attempt_id THEN
            RAISE EXCEPTION 'job already has a different engine result'
              USING ERRCODE = 'P2D01';
          END IF;
          RETURN QUERY SELECT
            accepted.batch_sha256,
            accepted.ingestion_digest,
            accepted.job_id,
            accepted.attempt_id,
            accepted.engine_run_id,
            accepted.event_count,
            accepted.first_sequence,
            accepted.last_sequence,
            accepted.last_digest;
        END;
        $ingest_engine_job_result$;

        -- The 0006 functions are already the reviewed transition authority.
        -- Clone their catalog-normalized definitions under paper-only names,
        -- changing exactly one SNAPSHOT predicate in each function. The exact
        -- parent revision and one-replacement checks make drift fail closed.
        DO $promote_paper_worker$
        DECLARE
          source_signatures text[] := ARRAY[
            'job_plane.worker_claim_snapshot(text,text,text,integer,text,text)',
            'job_plane.worker_start_snapshot(text,text,text,text,bigint,bigint,bigint,text,text,text)',
            'job_plane.worker_control_snapshot_lease(text,text,text,text,integer,text)',
            'job_plane.worker_finalize_snapshot(text,text,text,text,text,text,text,text,text,text,integer,text,text,jsonb,text,text,boolean,text,jsonb)',
            'job_plane.worker_recover_expired_snapshot(text,text,text,text,text,text,bigint,bigint,bigint,text,text,text,text,text,text)'
          ];
          source_names text[] := ARRAY[
            'worker_claim_snapshot',
            'worker_start_snapshot',
            'worker_control_snapshot_lease',
            'worker_finalize_snapshot',
            'worker_recover_expired_snapshot'
          ];
          target_names text[] := ARRAY[
            'worker_claim_paper',
            'worker_start_paper',
            'worker_control_paper_lease',
            'worker_finalize_paper',
            'worker_recover_expired_paper'
          ];
          source_definition text;
          promoted_definition text;
          snapshot_pattern text :=
            '''SNAPSHOT''(?:::(text|character varying))?';
          replacement text :=
            'ANY (ARRAY[''SNAPSHOT''::text, ''BACKTEST''::text])';
          occurrences integer;
          function_index integer;
        BEGIN
          IF (SELECT version_num FROM public.alembic_version)
             <> '0010_engine_event_ledger' THEN
            RAISE EXCEPTION '0011 parent head changed during migration';
          END IF;
          FOR function_index IN 1..array_length(source_signatures, 1) LOOP
            source_definition := pg_get_functiondef(
              source_signatures[function_index]::regprocedure
            );
            SELECT count(*) INTO occurrences
            FROM regexp_matches(
              source_definition,
              snapshot_pattern,
              'g'
            );
            IF occurrences <> 1 THEN
                RAISE EXCEPTION 'paper worker source predicate drifted: %',
                source_names[function_index];
            END IF;
            promoted_definition := replace(
              source_definition,
              source_names[function_index],
              target_names[function_index]
            );
            promoted_definition := regexp_replace(
              promoted_definition,
              snapshot_pattern,
              replacement,
              'g'
            );
            IF promoted_definition = source_definition
               OR position(target_names[function_index] IN promoted_definition) = 0
               OR position(replacement IN promoted_definition) = 0 THEN
                RAISE EXCEPTION 'paper worker promotion failed closed: %',
                source_names[function_index];
            END IF;
            EXECUTE promoted_definition;
          END LOOP;
        END;
        $promote_paper_worker$;

        REVOKE ALL PRIVILEGES ON TABLE public.engine_job_results FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.ingest_engine_job_result(text) FROM PUBLIC;
        GRANT SELECT ON TABLE public.engine_event_batch_receipts
          TO trading_job_worker;
        GRANT SELECT ON TABLE public.engine_job_results
          TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.ingest_engine_job_result(text)
          TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_claim_paper(
          text, text, text, integer, text, text
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_start_paper(
          text, text, text, text, bigint, bigint, bigint, text, text, text
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_control_paper_lease(
          text, text, text, text, integer, text
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_finalize_paper(
          text, text, text, text, text, text, text, text, text, text,
          integer, text, text, jsonb, text, text, boolean, text, jsonb
        ) TO trading_job_worker;
        GRANT EXECUTE ON FUNCTION job_plane.worker_recover_expired_paper(
          text, text, text, text, text, text, bigint, bigint, bigint,
          text, text, text, text, text, text
        ) TO trading_job_worker;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0011 engine BACKTEST worker authority is forward-only; use a reviewed repair"
    )

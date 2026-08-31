"""Authorize exact engine BACKTEST enqueue through the Job API.

Revision ID: 0013_engine_backtest_enqueue_authority
Revises: 0012_p1_engine_projection_authority

This source authority may run only under exact disposable PostgreSQL approval.
"""
from __future__ import annotations

from alembic import op


revision = "0013_engine_backtest_enqueue_authority"
down_revision = "0012_p1_engine_projection_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION job_plane.api_enqueue_engine_backtest(
          p_job_id text,
          p_payload jsonb,
          p_payload_fingerprint text,
          p_idempotency_key text,
          p_actor_id text,
          p_priority smallint,
          p_trace_id text,
          p_event_id text
        )
        RETURNS TABLE(job_id text, outcome text)
        LANGUAGE plpgsql
        SECURITY DEFINER
        VOLATILE
        PARALLEL UNSAFE
        SET search_path = pg_catalog
        AS $api_enqueue_engine_backtest$
        DECLARE
          existing_job public.jobs%ROWTYPE;
          inserted_job_id text;
          v_payload_fingerprint text;
        BEGIN
          IF session_user <> 'trading_job_api' THEN
            RAISE EXCEPTION 'engine backtest enqueue authority rejected'
              USING ERRCODE = '42501';
          END IF;
          IF p_job_id IS NULL
             OR p_job_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_event_id IS NULL
             OR p_event_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'
             OR p_job_id = p_event_id
             OR p_payload IS NULL
             OR p_payload_fingerprint IS NULL
             OR p_idempotency_key IS NULL
             OR p_idempotency_key !~
                  '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_idempotency_key ~ '^schedule:'
             OR p_actor_id IS NULL
             OR p_actor_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
             OR p_priority IS NULL OR p_priority < 0 OR p_priority > 100
             OR p_trace_id IS NULL
             OR p_trace_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$' THEN
            RAISE EXCEPTION 'engine backtest enqueue input rejected'
              USING ERRCODE = '22023';
          END IF;
          IF NOT coalesce(
            job_plane.paper_worker_job_allowed('BACKTEST', p_payload), false
          ) THEN
            RAISE EXCEPTION 'engine backtest enqueue payload rejected'
              USING ERRCODE = '22023';
          END IF;
          v_payload_fingerprint := pg_catalog.encode(
            public.digest(
              pg_catalog.convert_to(
                public.canonical_domain_json(p_payload), 'UTF8'
              ),
              'sha256'
            ),
            'hex'
          );
          IF p_payload_fingerprint IS DISTINCT FROM v_payload_fingerprint THEN
            RAISE EXCEPTION 'engine backtest enqueue fingerprint rejected'
              USING ERRCODE = '22023';
          END IF;

          INSERT INTO public.jobs (
            job_id, job_type, state, payload, payload_fingerprint,
            idempotency_key, actor_type, actor_id, priority, max_attempts
          ) VALUES (
            p_job_id, 'BACKTEST', 'QUEUED', p_payload,
            v_payload_fingerprint, p_idempotency_key, 'OPERATOR', p_actor_id,
            p_priority, 2
          )
          ON CONFLICT (job_type, idempotency_key) DO NOTHING
          RETURNING public.jobs.job_id INTO inserted_job_id;

          IF inserted_job_id IS NOT NULL THEN
            INSERT INTO public.job_events (
              event_id, job_id, attempt_id, sequence, from_state, to_state,
              reason_code, actor_type, actor_id, trace_id, metadata
            ) VALUES (
              p_event_id, inserted_job_id, NULL, 1, NULL, 'QUEUED',
              'ENQUEUED', 'OPERATOR', p_actor_id, p_trace_id, '{}'::jsonb
            );
            job_id := inserted_job_id;
            outcome := 'ENQUEUED';
            RETURN NEXT;
            RETURN;
          END IF;

          SELECT job_row.*
          INTO existing_job
          FROM public.jobs AS job_row
          WHERE job_row.job_type = 'BACKTEST'
            AND job_row.idempotency_key = p_idempotency_key
          FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'idempotency recovery failed'
              USING ERRCODE = '40001';
          END IF;
          IF existing_job.payload_fingerprint IS DISTINCT FROM
                 v_payload_fingerprint
             OR existing_job.actor_type IS DISTINCT FROM 'OPERATOR'
             OR existing_job.actor_id IS DISTINCT FROM p_actor_id
             OR existing_job.priority IS DISTINCT FROM p_priority THEN
            RAISE EXCEPTION 'idempotency identity conflict'
              USING ERRCODE = '23505',
                    CONSTRAINT = 'job_plane_idempotency_identity';
          END IF;
          job_id := existing_job.job_id;
          outcome := 'DEDUPLICATED';
          RETURN NEXT;
        END;
        $api_enqueue_engine_backtest$;

        REVOKE ALL PRIVILEGES ON FUNCTION
          job_plane.api_enqueue_engine_backtest(
            text, jsonb, text, text, text, smallint, text, text
          )
          FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
               trading_job_api, trading_job_worker, trading_job_scheduler;
        GRANT EXECUTE ON FUNCTION
          job_plane.api_enqueue_engine_backtest(
            text, jsonb, text, text, text, smallint, text, text
          )
          TO trading_job_api;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0013 engine BACKTEST enqueue authority is forward-only; "
        "use a reviewed forward repair"
    )

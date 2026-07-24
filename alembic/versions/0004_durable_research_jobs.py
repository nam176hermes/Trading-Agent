"""Create the Phase 4 durable research job queue.

Revision ID: 0004_durable_research_jobs
Revises: 0003_contract_lineage_repair
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_durable_research_jobs"
down_revision = "0003_contract_lineage_repair"
branch_labels = None
depends_on = None

JOB_TYPES = "('SNAPSHOT','DEBATE','REPLAY','BACKTEST')"
JOB_STATES = (
    "('QUEUED','CLAIMED','RUNNING','SUCCEEDED','FAILED','BLOCKED',"
    "'TIMED_OUT','CANCEL_REQUESTED','CANCELLED')"
)
ACTOR_TYPES = "('OPERATOR','SCHEDULER','WORKER','RECOVERY','SYSTEM')"


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("job_type", sa.String(16), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.SmallInteger(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_token", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_actor_type", sa.String(16)),
        sa.Column("cancel_actor_id", sa.String(128)),
        sa.Column("reason_code", sa.String(128)),
        sa.Column("result_hash", sa.String(64)),
        sa.Column(
            "result_metadata", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_message", sa.String(512)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "job_type", "idempotency_key", name="uq_jobs_type_idempotency"
        ),
        sa.CheckConstraint(f"job_type IN {JOB_TYPES}", name="ck_jobs_type"),
        sa.CheckConstraint(f"state IN {JOB_STATES}", name="ck_jobs_state"),
        sa.CheckConstraint(f"actor_type IN {ACTOR_TYPES}", name="ck_jobs_actor_type"),
        sa.CheckConstraint(
            f"cancel_actor_type IS NULL OR cancel_actor_type IN {ACTOR_TYPES}",
            name="ck_jobs_cancel_actor_type",
        ),
        sa.CheckConstraint(
            "(cancel_requested_at IS NULL AND cancel_actor_type IS NULL "
            "AND cancel_actor_id IS NULL) OR "
            "(cancel_requested_at IS NOT NULL AND cancel_actor_type IS NOT NULL "
            "AND cancel_actor_id IS NOT NULL)",
            name="ck_jobs_cancel_shape",
        ),
        sa.CheckConstraint("priority BETWEEN 0 AND 100", name="ck_jobs_priority"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_jobs_attempt_count"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_jobs_max_attempts"),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_jobs_lease_shape",
        ),
        sa.CheckConstraint(
            "char_length(payload_fingerprint) = 64",
            name="ck_jobs_payload_fingerprint",
        ),
        sa.CheckConstraint(
            "result_hash IS NULL OR char_length(result_hash) = 64",
            name="ck_jobs_result_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="ck_jobs_payload_object"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_metadata) = 'object'",
            name="ck_jobs_result_metadata_object",
        ),
    )
    op.create_index(
        "ix_jobs_list", "jobs", [sa.text("requested_at DESC"), sa.text("job_id DESC")]
    )
    op.create_index(
        "ix_jobs_claim", "jobs",
        ["state", "next_attempt_at", sa.text("priority DESC"), "requested_at", "job_id"],
        postgresql_where=sa.text("state = 'QUEUED'"),
    )
    op.create_index(
        "ix_jobs_lease_expiry", "jobs", ["lease_expires_at"],
        postgresql_where=sa.text("lease_expires_at IS NOT NULL"),
    )

    op.create_table(
        "job_attempts",
        sa.Column("attempt_id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id", sa.String(64),
            sa.ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("lease_token", sa.String(128), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("child_pid", sa.BigInteger()),
        sa.Column("process_group_id", sa.BigInteger()),
        sa.Column("process_start_ticks", sa.BigInteger()),
        sa.Column("command_fingerprint", sa.String(64)),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("termination_reason", sa.String(128)),
        sa.Column("stdout_ref", sa.String(512)),
        sa.Column("stdout_sha256", sa.String(64)),
        sa.Column("stdout_size_bytes", sa.BigInteger()),
        sa.Column("stdout_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stderr_ref", sa.String(512)),
        sa.Column("stderr_sha256", sa.String(64)),
        sa.Column("stderr_size_bytes", sa.BigInteger()),
        sa.Column("stderr_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_message", sa.String(512)),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_attempts_job_number"),
        sa.UniqueConstraint("job_id", "attempt_id", name="uq_job_attempts_job_id"),
        sa.CheckConstraint("attempt_number >= 1", name="ck_job_attempts_number"),
        sa.CheckConstraint(
            "outcome IN ('CLAIMED','RUNNING','SUCCEEDED','FAILED','BLOCKED',"
            "'TIMED_OUT','CANCELLED','INTERRUPTED')",
            name="ck_job_attempts_outcome",
        ),
        sa.CheckConstraint(
            "child_pid IS NULL OR child_pid > 0", name="ck_job_attempts_child_pid"
        ),
        sa.CheckConstraint(
            "process_group_id IS NULL OR process_group_id > 0",
            name="ck_job_attempts_process_group",
        ),
        sa.CheckConstraint(
            "process_start_ticks IS NULL OR process_start_ticks >= 0",
            name="ck_job_attempts_process_start_ticks",
        ),
        sa.CheckConstraint(
            "stdout_size_bytes IS NULL OR stdout_size_bytes >= 0",
            name="ck_job_attempts_stdout_size",
        ),
        sa.CheckConstraint(
            "stderr_size_bytes IS NULL OR stderr_size_bytes >= 0",
            name="ck_job_attempts_stderr_size",
        ),
        sa.CheckConstraint(
            "command_fingerprint IS NULL OR char_length(command_fingerprint) = 64",
            name="ck_job_attempts_command_fingerprint",
        ),
        sa.CheckConstraint(
            "stdout_sha256 IS NULL OR char_length(stdout_sha256) = 64",
            name="ck_job_attempts_stdout_sha256",
        ),
        sa.CheckConstraint(
            "stderr_sha256 IS NULL OR char_length(stderr_sha256) = 64",
            name="ck_job_attempts_stderr_sha256",
        ),
        sa.CheckConstraint(
            "(stdout_ref IS NULL AND stdout_sha256 IS NULL "
            "AND stdout_size_bytes IS NULL) OR "
            "(stdout_ref IS NOT NULL AND stdout_sha256 IS NOT NULL "
            "AND stdout_size_bytes IS NOT NULL)",
            name="ck_job_attempts_stdout_shape",
        ),
        sa.CheckConstraint(
            "(stderr_ref IS NULL AND stderr_sha256 IS NULL "
            "AND stderr_size_bytes IS NULL) OR "
            "(stderr_ref IS NOT NULL AND stderr_sha256 IS NOT NULL "
            "AND stderr_size_bytes IS NOT NULL)",
            name="ck_job_attempts_stderr_shape",
        ),
    )

    op.create_table(
        "job_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id", sa.String(64),
            sa.ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("attempt_id", sa.String(64)),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("from_state", sa.String(32)),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("job_id", "sequence", name="uq_job_events_job_sequence"),
        sa.ForeignKeyConstraint(
            ["job_id", "attempt_id"],
            ["job_attempts.job_id", "job_attempts.attempt_id"],
            name="fk_job_events_job_attempt",
        ),
        sa.CheckConstraint(
            f"from_state IS NULL OR from_state IN {JOB_STATES}",
            name="ck_job_events_from_state",
        ),
        sa.CheckConstraint(f"to_state IN {JOB_STATES}", name="ck_job_events_to_state"),
        sa.CheckConstraint(
            f"actor_type IN {ACTOR_TYPES}", name="ck_job_events_actor_type"
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_job_events_sequence"),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_job_events_metadata_object",
        ),
    )
    op.create_index(
        "ix_job_events_job_sequence", "job_events", ["job_id", "sequence"]
    )

    op.create_table(
        "scheduler_heartbeats",
        sa.Column("heartbeat_id", sa.String(64), primary_key=True),
        sa.Column("scheduler_id", sa.String(128), nullable=False),
        sa.Column("code_commit", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("tick_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slot_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.job_id")),
        sa.Column("reason_code", sa.String(128)),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "outcome IN ('ENQUEUED','DEDUPLICATED','SKIPPED_NOT_SLOT','FAILED')",
            name="ck_scheduler_heartbeats_outcome",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_scheduler_heartbeats_metadata_object",
        ),
    )
    op.create_index(
        "ix_scheduler_heartbeats_tick", "scheduler_heartbeats",
        [sa.text("tick_at DESC"), sa.text("heartbeat_id DESC")],
    )

    op.create_table(
        "job_artifacts",
        sa.Column("artifact_id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id", sa.String(64),
            sa.ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("attempt_id", sa.String(64), nullable=False),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("relative_ref", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("validator_id", sa.String(128), nullable=False),
        sa.Column(
            "validation_metadata", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "attempt_id", "artifact_type", "relative_ref",
            name="uq_job_artifacts_attempt_ref",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "attempt_id"],
            ["job_attempts.job_id", "job_attempts.attempt_id"],
            name="fk_job_artifacts_job_attempt",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_job_artifacts_size"),
        sa.CheckConstraint("char_length(sha256) = 64", name="ck_job_artifacts_sha256"),
        sa.CheckConstraint(
            "relative_ref <> '' AND char_length(sha256) = 64 AND size_bytes >= 0",
            name="ck_job_artifacts_storage_shape",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(validation_metadata) = 'object'",
            name="ck_job_artifacts_validation_metadata_object",
        ),
    )
    op.create_index("ix_job_artifacts_job", "job_artifacts", ["job_id", "created_at"])

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(128), primary_key=True),
        sa.Column("code_commit", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_job_id", sa.String(64), sa.ForeignKey("jobs.job_id")),
        sa.Column("current_attempt_id", sa.String(64)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "status IN ('IDLE','BUSY','STOPPING','UNHEALTHY')",
            name="ck_worker_heartbeats_status",
        ),
        sa.CheckConstraint(
            "(current_job_id IS NULL AND current_attempt_id IS NULL) OR "
            "(current_job_id IS NOT NULL AND current_attempt_id IS NOT NULL)",
            name="ck_worker_heartbeats_current_shape",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_worker_heartbeats_metadata_object",
        ),
        sa.ForeignKeyConstraint(
            ["current_job_id", "current_attempt_id"],
            ["job_attempts.job_id", "job_attempts.attempt_id"],
            name="fk_worker_heartbeats_job_attempt",
        ),
    )
    op.create_index(
        "ix_worker_heartbeats_at", "worker_heartbeats", ["heartbeat_at"]
    )

    op.execute(
        """
        CREATE FUNCTION reject_job_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'job_events is append-only'
            USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_job_events_append_only
        BEFORE UPDATE OR DELETE ON job_events
        FOR EACH ROW EXECUTE FUNCTION reject_job_event_mutation()
        """
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON jobs, job_attempts, worker_heartbeats "
        "TO trading_jobs"
    )
    op.execute(
        "GRANT SELECT, INSERT ON job_events, scheduler_heartbeats, job_artifacts "
        "TO trading_jobs"
    )
    op.execute("GRANT SELECT ON public.alembic_version TO trading_jobs")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON public.alembic_version FROM trading_jobs")
    op.execute("DROP TRIGGER IF EXISTS trg_job_events_append_only ON job_events")
    op.execute("DROP FUNCTION IF EXISTS reject_job_event_mutation()")
    op.drop_table("worker_heartbeats")
    op.drop_table("job_artifacts")
    op.drop_table("scheduler_heartbeats")
    op.drop_table("job_events")
    op.drop_table("job_attempts")
    op.drop_table("jobs")

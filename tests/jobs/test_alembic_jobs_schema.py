from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from tests.jobs._postgres import disposable_legacy_database


ROOT = Path(__file__).parents[2]
REVISION = "0004_durable_research_jobs"
JOB_TABLES = {
    "jobs",
    "job_attempts",
    "job_events",
    "scheduler_heartbeats",
    "job_artifacts",
    "worker_heartbeats",
}

EXPECTED_COLUMNS = {
    "jobs": (
        "job_id", "job_type", "state", "payload", "payload_fingerprint",
        "idempotency_key", "actor_type", "actor_id", "priority",
        "requested_at", "updated_at", "attempt_count", "max_attempts",
        "next_attempt_at", "lease_owner", "lease_token", "lease_expires_at",
        "cancel_requested_at", "cancel_actor_type", "cancel_actor_id",
        "reason_code", "result_hash", "result_metadata", "error_code",
        "error_message", "finished_at",
    ),
    "job_attempts": (
        "attempt_id", "job_id", "attempt_number", "worker_id", "outcome",
        "lease_token", "lease_expires_at", "claimed_at", "started_at",
        "heartbeat_at", "finished_at", "child_pid", "process_group_id",
        "process_start_ticks", "command_fingerprint", "exit_code",
        "termination_reason", "stdout_ref", "stdout_sha256",
        "stdout_size_bytes", "stdout_truncated", "stderr_ref",
        "stderr_sha256", "stderr_size_bytes", "stderr_truncated",
        "error_code", "error_message",
    ),
    "job_events": (
        "event_id", "job_id", "attempt_id", "sequence", "from_state",
        "to_state", "reason_code", "actor_type", "actor_id", "trace_id",
        "metadata", "created_at",
    ),
    "scheduler_heartbeats": (
        "heartbeat_id", "scheduler_id", "code_commit", "actor_id",
        "trace_id", "tick_at", "slot_at", "outcome", "job_id",
        "reason_code", "metadata", "created_at",
    ),
    "job_artifacts": (
        "artifact_id", "job_id", "attempt_id", "artifact_type",
        "relative_ref", "sha256", "size_bytes", "media_type", "truncated",
        "validator_id", "validation_metadata", "created_at",
    ),
    "worker_heartbeats": (
        "worker_id", "code_commit", "status", "current_job_id",
        "current_attempt_id", "heartbeat_at", "metadata",
    ),
}

JSONB_COLUMNS = {
    ("jobs", "payload"), ("jobs", "result_metadata"),
    ("job_events", "metadata"), ("scheduler_heartbeats", "metadata"),
    ("job_artifacts", "validation_metadata"), ("worker_heartbeats", "metadata"),
}
TIMESTAMP_COLUMNS = {
    ("jobs", name) for name in (
        "requested_at", "updated_at", "next_attempt_at", "lease_expires_at",
        "cancel_requested_at", "finished_at",
    )
} | {
    ("job_attempts", name) for name in (
        "lease_expires_at", "claimed_at", "started_at", "heartbeat_at", "finished_at",
    )
} | {
    ("job_events", "created_at"),
    ("scheduler_heartbeats", "tick_at"), ("scheduler_heartbeats", "slot_at"),
    ("scheduler_heartbeats", "created_at"), ("job_artifacts", "created_at"),
    ("worker_heartbeats", "heartbeat_at"),
}
SMALLINT_COLUMNS = {("jobs", "priority"), ("jobs", "max_attempts")}
INTEGER_COLUMNS = {
    ("jobs", "attempt_count"), ("job_attempts", "attempt_number"),
    ("job_attempts", "exit_code"),
}
BIGINT_COLUMNS = {
    ("job_attempts", name) for name in (
        "child_pid", "process_group_id", "process_start_ticks",
        "stdout_size_bytes", "stderr_size_bytes",
    )
} | {("job_events", "sequence"), ("job_artifacts", "size_bytes")}
BOOLEAN_COLUMNS = {
    ("job_attempts", "stdout_truncated"),
    ("job_attempts", "stderr_truncated"),
    ("job_artifacts", "truncated"),
}
VARCHAR_LENGTHS = {
    (table, name): length
    for table, lengths in {
        "jobs": {
            16: ("job_type", "actor_type", "cancel_actor_type"),
            32: ("state",),
            64: ("job_id", "payload_fingerprint", "result_hash"),
            128: (
                "idempotency_key", "actor_id", "lease_owner", "lease_token",
                "cancel_actor_id", "reason_code", "error_code",
            ),
            512: ("error_message",),
        },
        "job_attempts": {
            32: ("outcome",),
            64: ("attempt_id", "job_id", "command_fingerprint", "stdout_sha256", "stderr_sha256"),
            128: ("worker_id", "lease_token", "termination_reason", "error_code"),
            512: ("stdout_ref", "stderr_ref", "error_message"),
        },
        "job_events": {
            16: ("actor_type",), 32: ("from_state", "to_state"),
            64: ("event_id", "job_id", "attempt_id"),
            128: ("reason_code", "actor_id", "trace_id"),
        },
        "scheduler_heartbeats": {
            32: ("outcome",), 64: ("heartbeat_id", "code_commit", "job_id"),
            128: ("scheduler_id", "actor_id", "trace_id", "reason_code"),
        },
        "job_artifacts": {
            64: ("artifact_id", "job_id", "attempt_id", "artifact_type", "sha256"),
            128: ("media_type", "validator_id"), 512: ("relative_ref",),
        },
        "worker_heartbeats": {
            16: ("status",), 64: ("code_commit", "current_job_id", "current_attempt_id"),
            128: ("worker_id",),
        },
    }.items()
    for length, names in lengths.items()
    for name in names
}
NULLABLE_COLUMNS = {
    ("jobs", name) for name in (
        "next_attempt_at", "lease_owner", "lease_token", "lease_expires_at",
        "cancel_requested_at", "cancel_actor_type", "cancel_actor_id", "reason_code",
        "result_hash", "error_code", "error_message", "finished_at",
    )
} | {
    ("job_attempts", name) for name in (
        "started_at", "heartbeat_at", "finished_at", "child_pid", "process_group_id",
        "process_start_ticks", "command_fingerprint", "exit_code", "termination_reason",
        "stdout_ref", "stdout_sha256", "stdout_size_bytes", "stderr_ref", "stderr_sha256",
        "stderr_size_bytes", "error_code", "error_message",
    )
} | {
    ("job_events", "attempt_id"), ("job_events", "from_state"),
    ("scheduler_heartbeats", "slot_at"), ("scheduler_heartbeats", "job_id"),
    ("scheduler_heartbeats", "reason_code"), ("worker_heartbeats", "current_job_id"),
    ("worker_heartbeats", "current_attempt_id"),
}
EXPECTED_DEFAULTS = {
    ("jobs", "priority"): "'0'::smallint",
    ("jobs", "requested_at"): "now()", ("jobs", "updated_at"): "now()",
    ("jobs", "attempt_count"): "0", ("jobs", "result_metadata"): "'{}'::jsonb",
    ("job_attempts", "stdout_truncated"): "false",
    ("job_attempts", "stderr_truncated"): "false",
    ("job_events", "metadata"): "'{}'::jsonb", ("job_events", "created_at"): "now()",
    ("scheduler_heartbeats", "metadata"): "'{}'::jsonb",
    ("scheduler_heartbeats", "created_at"): "now()",
    ("job_artifacts", "truncated"): "false",
    ("job_artifacts", "validation_metadata"): "'{}'::jsonb",
    ("job_artifacts", "created_at"): "now()",
    ("worker_heartbeats", "metadata"): "'{}'::jsonb",
}

EXPECTED_CHECK_NAMES = {
    "jobs": {
        "ck_jobs_actor_type", "ck_jobs_attempt_count", "ck_jobs_cancel_actor_type",
        "ck_jobs_cancel_shape", "ck_jobs_lease_shape", "ck_jobs_max_attempts",
        "ck_jobs_payload_fingerprint", "ck_jobs_payload_object", "ck_jobs_priority",
        "ck_jobs_result_hash", "ck_jobs_result_metadata_object", "ck_jobs_state",
        "ck_jobs_type",
    },
    "job_attempts": {
        "ck_job_attempts_child_pid", "ck_job_attempts_command_fingerprint",
        "ck_job_attempts_number", "ck_job_attempts_outcome",
        "ck_job_attempts_process_group", "ck_job_attempts_process_start_ticks",
        "ck_job_attempts_stderr_sha256", "ck_job_attempts_stderr_shape",
        "ck_job_attempts_stderr_size", "ck_job_attempts_stdout_sha256",
        "ck_job_attempts_stdout_shape", "ck_job_attempts_stdout_size",
    },
    "job_events": {
        "ck_job_events_actor_type", "ck_job_events_from_state",
        "ck_job_events_metadata_object", "ck_job_events_sequence", "ck_job_events_to_state",
    },
    "scheduler_heartbeats": {
        "ck_scheduler_heartbeats_metadata_object", "ck_scheduler_heartbeats_outcome",
    },
    "job_artifacts": {
        "ck_job_artifacts_sha256", "ck_job_artifacts_size", "ck_job_artifacts_storage_shape",
        "ck_job_artifacts_validation_metadata_object",
    },
    "worker_heartbeats": {
        "ck_worker_heartbeats_current_shape", "ck_worker_heartbeats_metadata_object",
        "ck_worker_heartbeats_status",
    },
}


def _enum_check(column: str, values: tuple[str, ...], *, nullable: bool = False) -> str:
    array = ", ".join(f"'{value}'::character varying" for value in values)
    expression = f"{column}::text = ANY (ARRAY[{array}]::text[])"
    return f"{column} IS NULL OR ({expression})" if nullable else expression


ACTOR_VALUES = ("OPERATOR", "SCHEDULER", "WORKER", "RECOVERY", "SYSTEM")
STATE_VALUES = (
    "QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED",
    "TIMED_OUT", "CANCEL_REQUESTED", "CANCELLED",
)
EXPECTED_CHECK_EXPRESSIONS = {
    "jobs": {
        "ck_jobs_actor_type": _enum_check("actor_type", ACTOR_VALUES),
        "ck_jobs_attempt_count": "attempt_count >= 0",
        "ck_jobs_cancel_actor_type": _enum_check(
            "cancel_actor_type", ACTOR_VALUES, nullable=True
        ),
        "ck_jobs_cancel_shape": (
            "cancel_requested_at IS NULL AND cancel_actor_type IS NULL AND "
            "cancel_actor_id IS NULL OR cancel_requested_at IS NOT NULL AND "
            "cancel_actor_type IS NOT NULL AND cancel_actor_id IS NOT NULL"
        ),
        "ck_jobs_lease_shape": (
            "lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL OR "
            "lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL"
        ),
        "ck_jobs_max_attempts": "max_attempts >= 1",
        "ck_jobs_payload_fingerprint": "char_length(payload_fingerprint::text) = 64",
        "ck_jobs_payload_object": "jsonb_typeof(payload) = 'object'::text",
        "ck_jobs_priority": "priority >= 0 AND priority <= 100",
        "ck_jobs_result_hash": "result_hash IS NULL OR char_length(result_hash::text) = 64",
        "ck_jobs_result_metadata_object": "jsonb_typeof(result_metadata) = 'object'::text",
        "ck_jobs_state": _enum_check("state", STATE_VALUES),
        "ck_jobs_type": _enum_check("job_type", ("SNAPSHOT", "DEBATE", "REPLAY", "BACKTEST")),
    },
    "job_attempts": {
        "ck_job_attempts_child_pid": "child_pid IS NULL OR child_pid > 0",
        "ck_job_attempts_command_fingerprint": (
            "command_fingerprint IS NULL OR char_length(command_fingerprint::text) = 64"
        ),
        "ck_job_attempts_number": "attempt_number >= 1",
        "ck_job_attempts_outcome": _enum_check(
            "outcome",
            (
                "CLAIMED", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED",
                "TIMED_OUT", "CANCELLED", "INTERRUPTED",
            ),
        ),
        "ck_job_attempts_process_group": "process_group_id IS NULL OR process_group_id > 0",
        "ck_job_attempts_process_start_ticks": (
            "process_start_ticks IS NULL OR process_start_ticks >= 0"
        ),
        "ck_job_attempts_stderr_sha256": (
            "stderr_sha256 IS NULL OR char_length(stderr_sha256::text) = 64"
        ),
        "ck_job_attempts_stderr_shape": (
            "stderr_ref IS NULL AND stderr_sha256 IS NULL AND stderr_size_bytes IS NULL OR "
            "stderr_ref IS NOT NULL AND stderr_sha256 IS NOT NULL AND stderr_size_bytes IS NOT NULL"
        ),
        "ck_job_attempts_stderr_size": "stderr_size_bytes IS NULL OR stderr_size_bytes >= 0",
        "ck_job_attempts_stdout_sha256": (
            "stdout_sha256 IS NULL OR char_length(stdout_sha256::text) = 64"
        ),
        "ck_job_attempts_stdout_shape": (
            "stdout_ref IS NULL AND stdout_sha256 IS NULL AND stdout_size_bytes IS NULL OR "
            "stdout_ref IS NOT NULL AND stdout_sha256 IS NOT NULL AND stdout_size_bytes IS NOT NULL"
        ),
        "ck_job_attempts_stdout_size": "stdout_size_bytes IS NULL OR stdout_size_bytes >= 0",
    },
    "job_events": {
        "ck_job_events_actor_type": _enum_check("actor_type", ACTOR_VALUES),
        "ck_job_events_from_state": _enum_check("from_state", STATE_VALUES, nullable=True),
        "ck_job_events_metadata_object": "jsonb_typeof(metadata) = 'object'::text",
        "ck_job_events_sequence": "sequence >= 1",
        "ck_job_events_to_state": _enum_check("to_state", STATE_VALUES),
    },
    "scheduler_heartbeats": {
        "ck_scheduler_heartbeats_metadata_object": "jsonb_typeof(metadata) = 'object'::text",
        "ck_scheduler_heartbeats_outcome": _enum_check(
            "outcome", ("ENQUEUED", "DEDUPLICATED", "SKIPPED_NOT_SLOT", "FAILED")
        ),
    },
    "job_artifacts": {
        "ck_job_artifacts_sha256": "char_length(sha256::text) = 64",
        "ck_job_artifacts_size": "size_bytes >= 0",
        "ck_job_artifacts_storage_shape": (
            "relative_ref::text <> ''::text AND char_length(sha256::text) = 64 "
            "AND size_bytes >= 0"
        ),
        "ck_job_artifacts_validation_metadata_object": (
            "jsonb_typeof(validation_metadata) = 'object'::text"
        ),
    },
    "worker_heartbeats": {
        "ck_worker_heartbeats_current_shape": (
            "current_job_id IS NULL AND current_attempt_id IS NULL OR "
            "current_job_id IS NOT NULL AND current_attempt_id IS NOT NULL"
        ),
        "ck_worker_heartbeats_metadata_object": "jsonb_typeof(metadata) = 'object'::text",
        "ck_worker_heartbeats_status": _enum_check(
            "status", ("IDLE", "BUSY", "STOPPING", "UNHEALTHY")
        ),
    },
}


def _config(connection) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def test_empty_database_upgrades_to_exact_0004_and_downgrades() -> None:
    with disposable_legacy_database(
        operation_id="jobs-alembic-empty-upgrade-downgrade-v1"
    ) as settings:
        engine = create_engine(settings.sqlalchemy_url())
        with engine.connect() as connection:
            config = _config(connection)
            command.upgrade(config, REVISION)
            command.upgrade(config, REVISION)

            inspector = inspect(connection)
            assert JOB_TABLES <= set(inspector.get_table_names())
            for table, expected in EXPECTED_COLUMNS.items():
                columns = inspector.get_columns(table)
                assert tuple(column["name"] for column in columns) == expected
                for column in columns:
                    key = (table, column["name"])
                    column_type = column["type"]
                    if key in JSONB_COLUMNS:
                        assert column_type.__class__.__name__ == "JSONB"
                    elif key in TIMESTAMP_COLUMNS:
                        assert column_type.__class__.__name__ == "TIMESTAMP"
                        assert column_type.timezone is True
                    elif key in SMALLINT_COLUMNS:
                        assert column_type.__class__.__name__ == "SMALLINT"
                    elif key in INTEGER_COLUMNS:
                        assert column_type.__class__.__name__ == "INTEGER"
                    elif key in BIGINT_COLUMNS:
                        assert column_type.__class__.__name__ == "BIGINT"
                    elif key in BOOLEAN_COLUMNS:
                        assert column_type.__class__.__name__ == "BOOLEAN"
                    else:
                        assert column_type.__class__.__name__ == "VARCHAR"
                        assert column_type.length == VARCHAR_LENGTHS[key]
                    assert column["nullable"] is (key in NULLABLE_COLUMNS)
                    assert column["default"] == EXPECTED_DEFAULTS.get(key)

            checks = {
                table: {
                    constraint["name"]: constraint["sqltext"]
                    for constraint in inspector.get_check_constraints(table)
                }
                for table in JOB_TABLES
            }
            assert {table: set(definitions) for table, definitions in checks.items()} == (
                EXPECTED_CHECK_NAMES
            )
            assert checks == EXPECTED_CHECK_EXPRESSIONS
            assert {
                constraint["name"]
                for table in JOB_TABLES
                for constraint in inspector.get_unique_constraints(table)
            } >= {
                "uq_jobs_type_idempotency",
                "uq_job_attempts_job_number",
                "uq_job_events_job_sequence",
                "uq_job_artifacts_attempt_ref",
            }
            indexes = {
                index["name"]: (
                    tuple(index["column_names"]),
                    index.get("column_sorting", {}),
                    index.get("dialect_options", {}).get("postgresql_where"),
                )
                for table in JOB_TABLES
                for index in inspector.get_indexes(table)
            }
            assert indexes == {
                "ix_jobs_claim": (
                    ("state", "next_attempt_at", "priority", "requested_at", "job_id"),
                    {"priority": ("desc",)}, "((state)::text = 'QUEUED'::text)",
                ),
                "ix_jobs_lease_expiry": (
                    ("lease_expires_at",), {}, "(lease_expires_at IS NOT NULL)",
                ),
                "ix_jobs_list": (
                    ("requested_at", "job_id"),
                    {"requested_at": ("desc",), "job_id": ("desc",)}, None,
                ),
                "uq_jobs_type_idempotency": (("job_type", "idempotency_key"), {}, None),
                "uq_job_attempts_job_id": (("job_id", "attempt_id"), {}, None),
                "uq_job_attempts_job_number": (("job_id", "attempt_number"), {}, None),
                "ix_job_events_job_sequence": (("job_id", "sequence"), {}, None),
                "uq_job_events_job_sequence": (("job_id", "sequence"), {}, None),
                "ix_scheduler_heartbeats_tick": (
                    ("tick_at", "heartbeat_id"),
                    {"tick_at": ("desc",), "heartbeat_id": ("desc",)}, None,
                ),
                "ix_job_artifacts_job": (("job_id", "created_at"), {}, None),
                "uq_job_artifacts_attempt_ref": (
                    ("attempt_id", "artifact_type", "relative_ref"), {}, None,
                ),
                "ix_worker_heartbeats_at": (("heartbeat_at",), {}, None),
            }

            foreign_keys = {
                (table, fk["referred_table"], tuple(fk["constrained_columns"])):
                fk.get("options", {}).get("ondelete")
                for table in JOB_TABLES
                for fk in inspector.get_foreign_keys(table)
            }
            assert foreign_keys[("job_attempts", "jobs", ("job_id",))] == "CASCADE"
            assert foreign_keys[("job_events", "jobs", ("job_id",))] == "CASCADE"
            assert foreign_keys[("job_artifacts", "jobs", ("job_id",))] == "CASCADE"
            assert foreign_keys[("scheduler_heartbeats", "jobs", ("job_id",))] is None
            assert foreign_keys[("worker_heartbeats", "jobs", ("current_job_id",))] is None
            composite_foreign_keys = {
                (table, fk["name"]): (
                    tuple(fk["constrained_columns"]), fk["referred_table"],
                    tuple(fk["referred_columns"]), fk.get("options", {}).get("ondelete"),
                )
                for table in JOB_TABLES
                for fk in inspector.get_foreign_keys(table)
                if len(fk["constrained_columns"]) > 1
            }
            assert composite_foreign_keys == {
                ("job_events", "fk_job_events_job_attempt"): (
                    ("job_id", "attempt_id"), "job_attempts", ("job_id", "attempt_id"), None,
                ),
                ("job_artifacts", "fk_job_artifacts_job_attempt"): (
                    ("job_id", "attempt_id"), "job_attempts", ("job_id", "attempt_id"), None,
                ),
                ("worker_heartbeats", "fk_worker_heartbeats_job_attempt"): (
                    ("current_job_id", "current_attempt_id"),
                    "job_attempts", ("job_id", "attempt_id"), None,
                ),
            }

            context = MigrationContext.configure(connection)
            assert set(context.get_current_heads()) == {REVISION}

            triggers = connection.exec_driver_sql(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'job_events'::regclass AND NOT tgisinternal"
            ).scalars().all()
            assert triggers == ["trg_job_events_append_only"]

            command.downgrade(config, "0003_contract_lineage_repair")
            assert JOB_TABLES.isdisjoint(inspect(connection).get_table_names())
            assert set(MigrationContext.configure(connection).get_current_heads()) == {
                "0003_contract_lineage_repair"
            }
        engine.dispose()


def test_revision_0003_upgrades_in_place_to_0004() -> None:
    with disposable_legacy_database(
        operation_id="jobs-alembic-0003-upgrade-v1"
    ) as settings:
        engine = create_engine(settings.sqlalchemy_url())
        with engine.connect() as connection:
            config = _config(connection)
            command.upgrade(config, "0003_contract_lineage_repair")
            assert set(MigrationContext.configure(connection).get_current_heads()) == {
                "0003_contract_lineage_repair"
            }
            legacy_tables = set(inspect(connection).get_table_names())

            command.upgrade(config, REVISION)

            assert legacy_tables <= set(inspect(connection).get_table_names())
            assert set(MigrationContext.configure(connection).get_current_heads()) == {
                REVISION
            }
        engine.dispose()


@pytest.mark.parametrize(
    ("table", "statement"),
    (
        (
            "scheduler_heartbeats",
            """
            INSERT INTO scheduler_heartbeats (
              heartbeat_id, scheduler_id, code_commit, actor_id, trace_id,
              tick_at, outcome
            ) VALUES (
              'scheduler-sha1', 'scheduler-one', %s, 'scheduler-one',
              'trace-sha1', now(), 'SKIPPED_NOT_SLOT'
            )
            """,
        ),
        (
            "worker_heartbeats",
            """
            INSERT INTO worker_heartbeats (
              worker_id, code_commit, status, heartbeat_at
            ) VALUES ('worker-sha1', %s, 'IDLE', now())
            """,
        ),
    ),
)
def test_heartbeat_code_commit_accepts_real_git_sha1(
    table: str, statement: str
) -> None:
    git_sha1 = "0123456789abcdef0123456789abcdef01234567"
    assert len(git_sha1) == 40
    with disposable_legacy_database(
        operation_id="jobs-alembic-heartbeat-git-sha1-v1"
    ) as settings:
        engine = create_engine(settings.sqlalchemy_url())
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, REVISION)
            connection.exec_driver_sql(statement, (git_sha1,))
            assert connection.exec_driver_sql(
                f"SELECT code_commit FROM {table}"
            ).scalar_one() == git_sha1
        engine.dispose()

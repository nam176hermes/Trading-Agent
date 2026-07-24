from __future__ import annotations

from datetime import datetime, timezone
import os

import psycopg
import pytest

from packages.job_contracts import ActorIdentity, EnqueueJobRequest, JobState
from services.job_store import JobRepository, JobStoreSettings
from services.job_store.worker_repository import WorkerRepository
from services.job_worker.recovery import ProcessIdentity
from tests.jobs._postgres import (
    _upgrade_to_revision,
    disposable_database,
    disposable_role_settings,
)
from trading_control.db import DatabaseSettings


EXACT_0007_HEAD = "0007_job_event_chain_authority"


TABLES = (
    "jobs",
    "job_attempts",
    "job_events",
    "scheduler_heartbeats",
    "job_artifacts",
    "worker_heartbeats",
)
JOB_ROLES = (
    "trading_job_api",
    "trading_job_worker",
    "trading_job_scheduler",
)
DENIED_LEGACY_ROLES = (
    "trading_jobs",
    "trading_migrator",
    "trading_reader",
)


def _columns(
    table: str,
    privilege: str,
    names: str,
) -> set[tuple[str, str, str]]:
    return {(table, name, privilege) for name in names.split()}


EVENT_INSERT = _columns(
    "job_events",
    "INSERT",
    "event_id job_id attempt_id sequence from_state to_state reason_code "
    "actor_type actor_id trace_id metadata",
)
JOB_PROJECTION = (
    "job_id job_type state payload payload_fingerprint idempotency_key "
    "actor_type actor_id priority requested_at updated_at attempt_count "
    "max_attempts reason_code result_hash cancel_requested_at "
    "cancel_actor_type cancel_actor_id"
)
JOB_INSERT = (
    "job_id job_type state payload payload_fingerprint idempotency_key "
    "actor_type actor_id priority max_attempts"
)
EXPECTED_TABLE_GRANTS = {
    "trading_job_api": {
        ("job_events", "SELECT"),
        ("job_artifacts", "SELECT"),
    },
    "trading_job_worker": {
        ("jobs", "SELECT"),
        ("job_attempts", "SELECT"),
        ("job_events", "SELECT"),
        ("worker_heartbeats", "SELECT"),
    },
    "trading_job_scheduler": {("job_events", "SELECT")},
}
EXPECTED_COLUMN_GRANTS = {
    "trading_job_api": (
        _columns("jobs", "SELECT", JOB_PROJECTION)
        | _columns(
            "job_attempts",
            "SELECT",
            "attempt_id job_id attempt_number worker_id outcome claimed_at "
            "started_at finished_at exit_code termination_reason",
        )
    ),
    "trading_job_worker": (
        _columns(
            "job_artifacts",
            "INSERT",
            "artifact_id job_id attempt_id artifact_type relative_ref sha256 "
            "size_bytes media_type truncated validator_id "
            "validation_metadata",
        )
        | _columns(
            "worker_heartbeats",
            "INSERT",
            "worker_id code_commit status current_job_id current_attempt_id "
            "heartbeat_at metadata",
        )
        | _columns(
            "worker_heartbeats",
            "UPDATE",
            "code_commit status current_job_id current_attempt_id "
            "heartbeat_at metadata",
        )
    ),
    "trading_job_scheduler": (
        _columns("jobs", "SELECT", JOB_PROJECTION)
        | _columns(
            "scheduler_heartbeats",
            "INSERT",
            "heartbeat_id scheduler_id code_commit actor_id trace_id tick_at "
            "slot_at outcome job_id reason_code metadata",
        )
    ),
}
EXPECTED_POLICIES = {
    "job_plane_api_jobs_select",
    "job_plane_worker_jobs_select",
    "job_plane_scheduler_jobs_select",
    "job_plane_api_attempts_select",
    "job_plane_worker_attempts_select",
    "job_plane_api_events_select",
    "job_plane_worker_events_select",
    "job_plane_scheduler_events_select",
    "job_plane_scheduler_heartbeats_insert",
    "job_plane_api_artifacts_select",
    "job_plane_worker_artifacts_insert",
    "job_plane_worker_heartbeats_select",
    "job_plane_worker_heartbeats_insert",
    "job_plane_worker_heartbeats_update",
}
POLICY_TABLES = {
    "jobs": "jobs",
    "attempts": "job_attempts",
    "events": "job_events",
    "scheduler_heartbeats": "scheduler_heartbeats",
    "artifacts": "job_artifacts",
    "worker_heartbeats": "worker_heartbeats",
}


def _expected_policy_binding(name: str) -> tuple[str, str, str, tuple[str, ...]]:
    suffix_to_command = {
        "select": "r",
        "insert": "a",
        "update": "w",
        "cancel": "w",
    }
    _, _, role_fragment, remainder = name.split("_", 3)
    table_fragment, action = remainder.rsplit("_", 1)
    if table_fragment == "heartbeats":
        table_fragment = f"{role_fragment}_heartbeats"
    return (
        name,
        POLICY_TABLES[table_fragment],
        suffix_to_command[action],
        (f"trading_job_{role_fragment}",),
    )


@pytest.fixture(scope="module")
def role_database():
    if (
        os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES"
        or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE")
        != "DISPOSABLE_PG_GREEN"
    ):
        pytest.skip("exact disposable PostgreSQL GREEN authority is not present")
    with disposable_database(operation_id="jobs-role-permissions-green-v1") as owner:
        _upgrade_to_revision(owner, EXACT_0007_HEAD)
        roles = {
            role: disposable_role_settings(owner, role)
            for role in (
                "trading_owner",
                "trading_migrator",
                "trading_reader",
                *JOB_ROLES,
            )
        }
        yield owner, roles


def _store(settings: DatabaseSettings) -> JobStoreSettings:
    return JobStoreSettings(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
        pool_max=2,
    ).require_user(settings.user)


def _operator_request(key: str, *, priority: int = 0) -> EnqueueJobRequest:
    return EnqueueJobRequest.model_validate(
        {
            "job_type": "SNAPSHOT",
            "payload": {"scope": "default", "requested_as_of": None},
            "idempotency_key": key,
            "actor": {
                "actor_type": "OPERATOR",
                "actor_id": "role-matrix-operator",
            },
            "priority": priority,
        }
    )


def _scheduled_request() -> EnqueueJobRequest:
    return EnqueueJobRequest.model_validate(
        {
            "job_type": "SNAPSHOT",
            "payload": {"scope": "default", "requested_as_of": None},
            "idempotency_key": "schedule:snapshot:2026-07-16T12:30Z",
            "actor": {
                "actor_type": "SCHEDULER",
                "actor_id": "role-matrix-scheduler",
            },
            "priority": 0,
        }
    )


def test_job_roles_have_exact_flags_memberships_and_no_ownership(
    role_database,
) -> None:
    owner, _ = role_database
    with psycopg.connect(owner.conninfo()) as connection:
        flags = connection.execute(
            """
            SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                   rolinherit, rolreplication, rolbypassrls
            FROM pg_roles
            WHERE rolname = ANY(%s)
            ORDER BY rolname
            """,
            (["trading_jobs", *JOB_ROLES],),
        ).fetchall()
        assert flags == [
            ("trading_job_api", True, False, False, False, False, False, False),
            (
                "trading_job_scheduler",
                True,
                False,
                False,
                False,
                False,
                False,
                False,
            ),
            (
                "trading_job_worker",
                True,
                False,
                False,
                False,
                False,
                False,
                False,
            ),
            ("trading_jobs", False, False, False, False, False, False, False),
        ]

        role_settings = dict(
            connection.execute(
                "SELECT rolname, rolconfig FROM pg_roles "
                "WHERE rolname = ANY(%s) ORDER BY rolname",
                (["trading_jobs", *JOB_ROLES],),
            ).fetchall()
        )
        assert role_settings == {
            "trading_job_api": ["TimeZone=UTC"],
            "trading_job_scheduler": ["TimeZone=UTC"],
            "trading_job_worker": ["TimeZone=UTC"],
            "trading_jobs": None,
        }
        assert connection.execute(
            """
            SELECT count(*)
            FROM pg_db_role_setting role_setting
            JOIN pg_roles role_row ON role_row.oid = role_setting.setrole
            WHERE role_row.rolname = ANY(%s)
              AND role_setting.setdatabase = (
                SELECT oid FROM pg_database WHERE datname = current_database()
              )
            """,
            (["trading_jobs", *JOB_ROLES],),
        ).fetchone()[0] == 0

        memberships = connection.execute(
            """
            SELECT 1
            FROM pg_auth_members membership
            JOIN pg_roles granted ON granted.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            WHERE granted.rolname = ANY(%s) OR member.rolname = ANY(%s)
            """,
            (["trading_jobs", *JOB_ROLES], ["trading_jobs", *JOB_ROLES]),
        ).fetchall()
        assert memberships == []

        ownership = connection.execute(
            """
            SELECT relation.relname, owner.rolname
            FROM pg_class relation
            JOIN pg_roles owner ON owner.oid = relation.relowner
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname = ANY(%s)
              AND owner.rolname = ANY(%s)
            """,
            (list(TABLES), ["trading_jobs", *JOB_ROLES]),
        ).fetchall()
        assert ownership == []


def test_job_role_acl_and_rls_catalog_is_exact(role_database) -> None:
    owner, _ = role_database
    with psycopg.connect(owner.conninfo()) as connection:
        rls = connection.execute(
            """
            SELECT relname, relrowsecurity, relforcerowsecurity
            FROM pg_class
            WHERE relnamespace = 'public'::regnamespace
              AND relname = ANY(%s)
            ORDER BY relname
            """,
            (list(TABLES),),
        ).fetchall()
        assert rls == sorted((table, True, False) for table in TABLES)

        policies = connection.execute(
            """
            SELECT policy.polname, relation.relname, policy.polcmd,
                   ARRAY(
                     SELECT role_row.rolname
                     FROM unnest(policy.polroles) AS role_oid(oid)
                     JOIN pg_roles role_row ON role_row.oid = role_oid.oid
                     ORDER BY role_row.rolname
                   )
            FROM pg_policy policy
            JOIN pg_class relation ON relation.oid = policy.polrelid
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname = ANY(%s)
            """,
            (list(TABLES),),
        ).fetchall()
        assert {
            (name, table, command, tuple(roles))
            for name, table, command, roles in policies
        } == {_expected_policy_binding(name) for name in EXPECTED_POLICIES}

        triggers = connection.execute(
            """
            SELECT trigger_row.tgname, trigger_row.tgenabled,
                   trigger_row.tgtype, function_row.proname
            FROM pg_trigger trigger_row
            JOIN pg_proc function_row ON function_row.oid = trigger_row.tgfoid
            WHERE trigger_row.tgrelid IN (
              'public.jobs'::regclass,
              'public.job_events'::regclass
            )
              AND NOT trigger_row.tgisinternal
            ORDER BY trigger_row.tgname
            """
        ).fetchall()
        assert triggers == [
            (
                "trg_job_events_append_only",
                "O",
                27,
                "reject_job_event_mutation",
            ),
            (
                "trg_jobs_job_api_cancellation",
                "O",
                19,
                "enforce_job_api_cancellation",
            ),
        ]

        for role in JOB_ROLES:
            table_grants = connection.execute(
                """
                SELECT relation.relname, acl.privilege_type, acl.is_grantable
                FROM pg_class relation
                CROSS JOIN LATERAL aclexplode(relation.relacl) acl
                JOIN pg_roles grantee ON grantee.oid = acl.grantee
                WHERE relation.relnamespace = 'public'::regnamespace
                  AND relation.relname = ANY(%s)
                  AND grantee.rolname = %s
                """,
                (list(TABLES), role),
            ).fetchall()
            assert {(table, privilege) for table, privilege, _ in table_grants} == (
                EXPECTED_TABLE_GRANTS[role]
            )
            assert not any(grantable for _, _, grantable in table_grants)

            column_grants = connection.execute(
                """
                SELECT relation.relname, attribute.attname,
                       acl.privilege_type, acl.is_grantable
                FROM pg_class relation
                JOIN pg_attribute attribute ON attribute.attrelid = relation.oid
                CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
                JOIN pg_roles grantee ON grantee.oid = acl.grantee
                WHERE relation.relnamespace = 'public'::regnamespace
                  AND relation.relname = ANY(%s)
                  AND grantee.rolname = %s
                """,
                (list(TABLES), role),
            ).fetchall()
            assert {
                (table, column, privilege)
                for table, column, privilege, _ in column_grants
            } == EXPECTED_COLUMN_GRANTS[role]
            assert not any(
                grantable for _, _, _, grantable in column_grants
            )


def test_legacy_roles_and_default_acls_have_zero_job_authority(
    role_database,
) -> None:
    owner, _ = role_database
    with psycopg.connect(owner.conninfo()) as connection:
        for role in DENIED_LEGACY_ROLES:
            for table in TABLES:
                for privilege in (
                    "SELECT",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "TRUNCATE",
                    "REFERENCES",
                    "TRIGGER",
                ):
                    assert not connection.execute(
                        "SELECT has_table_privilege(%s, %s, %s)",
                        (role, table, privilege),
                    ).fetchone()[0]
                for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
                    assert not connection.execute(
                        "SELECT has_any_column_privilege(%s, %s, %s)",
                        (role, table, privilege),
                    ).fetchone()[0]

        default_acl = connection.execute(
            """
            SELECT grantee.rolname, defaults.defaclobjtype, acl.privilege_type
            FROM pg_default_acl defaults
            CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            WHERE defaults.defaclnamespace = 'public'::regnamespace
              AND grantee.rolname = ANY(%s)
            """,
            (["trading_jobs", *JOB_ROLES, "trading_migrator", "trading_reader"],),
        ).fetchall()
        assert default_acl == []

        default_function_acl = connection.execute(
            """
            SELECT pg_get_userbyid(defaults.defaclrole),
                   defaults.defaclnamespace,
                   defaults.defaclobjtype,
                   grantee.rolname,
                   acl.privilege_type,
                   acl.is_grantable
            FROM pg_default_acl defaults
            CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            WHERE pg_get_userbyid(defaults.defaclrole) = 'trading_owner'
              AND defaults.defaclnamespace = 0
              AND defaults.defaclobjtype = 'f'
            """
        ).fetchall()
        assert default_function_acl == [
            ("trading_owner", 0, "f", "trading_owner", "EXECUTE", False)
        ]
        assert not connection.execute(
            """
            SELECT EXISTS (
              SELECT 1
              FROM pg_default_acl defaults
              CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl
              WHERE pg_get_userbyid(defaults.defaclrole) = 'trading_owner'
                AND defaults.defaclnamespace = 0
                AND defaults.defaclobjtype = 'f'
                AND acl.grantee = 0
                AND acl.privilege_type = 'EXECUTE'
            )
            """
        ).fetchone()[0]

        public_acl = connection.execute(
            """
            SELECT relation.relname, NULL::text AS column_name,
                   acl.privilege_type
            FROM pg_class relation
            CROSS JOIN LATERAL aclexplode(relation.relacl) acl
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname = ANY(%s)
              AND acl.grantee = 0
            UNION ALL
            SELECT relation.relname, attribute.attname, acl.privilege_type
            FROM pg_class relation
            JOIN pg_attribute attribute ON attribute.attrelid = relation.oid
            CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relname = ANY(%s)
              AND acl.grantee = 0
            """,
            (list(TABLES), list(TABLES)),
        ).fetchall()
        assert public_acl == []

        for role in (*DENIED_LEGACY_ROLES, *JOB_ROLES):
            for function in (
                "public.reject_job_event_mutation()",
                "public.enforce_job_api_cancellation()",
            ):
                assert not connection.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                    (role, function),
                ).fetchone()[0]
        assert connection.execute(
            """
            SELECT count(*)
            FROM pg_proc function_row
            CROSS JOIN LATERAL aclexplode(function_row.proacl) acl
            WHERE function_row.oid IN (
              'public.reject_job_event_mutation()'::regprocedure,
              'public.enforce_job_api_cancellation()'::regprocedure
            )
              AND acl.grantee = 0
            """
        ).fetchone()[0] == 0

        assert not connection.execute(
            "SELECT has_database_privilege("
            "'trading_jobs', current_database(), 'CONNECT')"
        ).fetchone()[0]
        assert not connection.execute(
            "SELECT has_database_privilege("
            "'trading_jobs', current_database(), 'TEMPORARY')"
        ).fetchone()[0]
        for role in JOB_ROLES:
            assert connection.execute(
                "SELECT has_database_privilege(%s, current_database(), 'CONNECT')",
                (role,),
            ).fetchone()[0]
            assert connection.execute(
                "SELECT has_schema_privilege(%s, 'public', 'USAGE')", (role,)
            ).fetchone()[0]
            assert not connection.execute(
                "SELECT has_database_privilege("
                "%s, current_database(), 'TEMPORARY')",
                (role,),
            ).fetchone()[0]
            assert not connection.execute(
                "SELECT has_schema_privilege(%s, 'public', 'CREATE')", (role,)
            ).fetchone()[0]
            for table in TABLES:
                for privilege in ("DELETE", "TRUNCATE"):
                    assert not connection.execute(
                        "SELECT has_table_privilege(%s, %s, %s)",
                        (role, table, privilege),
                    ).fetchone()[0]

        database_acl = connection.execute(
            """
            SELECT grantee.rolname, acl.privilege_type, acl.is_grantable
            FROM pg_database database_row
            CROSS JOIN LATERAL aclexplode(database_row.datacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            WHERE database_row.datname = current_database()
              AND grantee.rolname = ANY(%s)
            """,
            (list(JOB_ROLES),),
        ).fetchall()
        assert set(database_acl) == {
            (role, "CONNECT", False) for role in JOB_ROLES
        }
        assert connection.execute(
            """
            SELECT count(*)
            FROM pg_database database_row
            CROSS JOIN LATERAL aclexplode(database_row.datacl) acl
            WHERE database_row.datname = current_database()
              AND acl.grantee = 0
              AND acl.privilege_type IN ('CONNECT', 'TEMPORARY')
            """
        ).fetchone()[0] == 0

        schema_acl = connection.execute(
            """
            SELECT grantee.rolname, acl.privilege_type, acl.is_grantable
            FROM pg_namespace namespace_row
            CROSS JOIN LATERAL aclexplode(namespace_row.nspacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            WHERE namespace_row.nspname = 'public'
              AND grantee.rolname = ANY(%s)
            """,
            (list(JOB_ROLES),),
        ).fetchall()
        assert set(schema_acl) == {
            (role, "USAGE", False) for role in JOB_ROLES
        }
        assert connection.execute(
            """
            SELECT count(*)
            FROM pg_namespace namespace_row
            CROSS JOIN LATERAL aclexplode(namespace_row.nspacl) acl
            WHERE namespace_row.nspname = 'public'
              AND acl.grantee = 0
              AND acl.privilege_type IN ('USAGE', 'CREATE')
            """
        ).fetchone()[0] == 0


def test_real_api_role_can_enqueue_and_cancel_only(role_database) -> None:
    _, roles = role_database
    request = _operator_request("manual:snapshot:" + "1" * 32)
    actor = ActorIdentity.model_validate(
        {"actor_type": "OPERATOR", "actor_id": "role-matrix-operator"}
    )
    with JobRepository(_store(roles["trading_job_api"])) as repository:
        job = repository.enqueue(request, trace_id="role-api-enqueue").job
        cancelled = repository.request_cancel(
            job.job_id,
            actor,
            "role-api-cancel",
        )

    assert cancelled.state is JobState.CANCELLED
    assert cancelled.cancel_actor == actor


def test_real_worker_role_can_claim_start_finalize_and_heartbeat(
    role_database,
) -> None:
    _, roles = role_database
    with JobRepository(_store(roles["trading_job_api"])) as repository:
        job = repository.enqueue(
            _operator_request("manual:snapshot:" + "2" * 32, priority=100),
            trace_id="role-worker-enqueue",
        ).job

    worker_id = "role-matrix-worker"
    with WorkerRepository(_store(roles["trading_job_worker"])) as repository:
        claimed = repository.claim_next(
            worker_id,
            lease_seconds=30,
            trace_id="role-worker-claim",
        )
        assert claimed is not None and claimed.job_id == job.job_id
        assert repository.start_attempt(
            claimed.job_id,
            claimed.attempt_id,
            worker_id,
            claimed.lease_token,
            ProcessIdentity(123, 123, 1, "a" * 64),
            "role-worker-start",
        )
        repository.worker_heartbeat(
            worker_id,
            "b" * 40,
            "BUSY",
            current_job_id=claimed.job_id,
            current_attempt_id=claimed.attempt_id,
        )
        assert repository.finalize(
            claimed.job_id,
            claimed.attempt_id,
            worker_id,
            claimed.lease_token,
            expected_state=JobState.RUNNING,
            expected_attempt_outcome="RUNNING",
            final_state=JobState.SUCCEEDED,
            reason_code="SUCCEEDED",
            trace_id="role-worker-finish",
            exit_code=0,
            result_hash="c" * 64,
            result_metadata={"validator": "role-matrix"},
        )


def test_real_scheduler_role_can_enqueue_only_one_exact_snapshot_slot(
    role_database,
) -> None:
    owner, roles = role_database
    slot = datetime(2026, 7, 16, 12, 30, tzinfo=timezone.utc)
    with JobRepository(_store(roles["trading_job_scheduler"])) as repository:
        result = repository.schedule_snapshot(
            request=_scheduled_request(),
            scheduler_id="role-matrix-scheduler",
            code_commit="d" * 40,
            trace_id="role-scheduler-slot",
            tick_at=slot,
            slot_at=slot,
        )

    with psycopg.connect(owner.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM jobs WHERE job_id = %s AND actor_type = 'SCHEDULER'",
            (result.job.job_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM scheduler_heartbeats WHERE job_id = %s",
            (result.job.job_id,),
        ).fetchone()[0] == 1


def test_nonexistent_gregorian_schedule_date_is_denied_at_database_boundary(
    role_database,
) -> None:
    owner, roles = role_database
    statement = """
        INSERT INTO jobs (
          job_id, job_type, state, payload, payload_fingerprint,
          idempotency_key, actor_type, actor_id, priority, max_attempts
        ) VALUES (
          %s, 'SNAPSHOT', 'QUEUED',
          '{"scope":"default","requested_as_of":null}'::jsonb,
          %s, 'schedule:snapshot:2026-02-31T12:30Z',
          'SCHEDULER', 'role-matrix-scheduler', 0, 2
        )
    """

    with psycopg.connect(owner.conninfo(), autocommit=True) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(statement, ("job-invalid-date-owner", "e" * 64))

    with psycopg.connect(
        roles["trading_job_scheduler"].conninfo(),
        autocommit=True,
    ) as connection:
        with pytest.raises(
            (psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege)
        ):
            connection.execute(statement, ("job-invalid-date-scheduler", "f" * 64))

    with psycopg.connect(owner.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM jobs WHERE idempotency_key = %s",
            ("schedule:snapshot:2026-02-31T12:30Z",),
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("role", "statement"),
    (
        ("trading_job_api", "SELECT lease_owner FROM jobs"),
        ("trading_job_api", "INSERT INTO job_attempts DEFAULT VALUES"),
        ("trading_job_api", "INSERT INTO job_artifacts DEFAULT VALUES"),
        ("trading_job_api", "INSERT INTO scheduler_heartbeats DEFAULT VALUES"),
        ("trading_job_api", "INSERT INTO worker_heartbeats DEFAULT VALUES"),
        ("trading_job_worker", "INSERT INTO jobs DEFAULT VALUES"),
        ("trading_job_worker", "INSERT INTO scheduler_heartbeats DEFAULT VALUES"),
        ("trading_job_scheduler", "SELECT lease_owner FROM jobs"),
        ("trading_job_scheduler", "UPDATE jobs SET state = state"),
        ("trading_job_scheduler", "INSERT INTO job_attempts DEFAULT VALUES"),
    ),
)
def test_cross_role_sensitive_operations_are_denied(
    role_database,
    role: str,
    statement: str,
) -> None:
    _, roles = role_database
    with psycopg.connect(roles[role].conninfo(), autocommit=True) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(statement)


@pytest.mark.parametrize("role", JOB_ROLES)
def test_each_runtime_role_is_denied_delete_truncate_and_ddl(
    role_database,
    role: str,
) -> None:
    _, roles = role_database
    with psycopg.connect(roles[role].conninfo(), autocommit=True) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("DELETE FROM jobs")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("TRUNCATE jobs CASCADE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("CREATE TABLE forbidden_job_role_ddl (id integer)")


def test_owner_is_also_prevented_from_mutating_append_only_events(
    role_database,
) -> None:
    owner, _ = role_database
    with psycopg.connect(owner.conninfo(), autocommit=True) as connection:
        connection.execute(
            """
            INSERT INTO jobs (
              job_id, job_type, state, payload, payload_fingerprint,
              idempotency_key, actor_type, actor_id, max_attempts
            ) VALUES ('job-owner', 'SNAPSHOT', 'QUEUED', '{}', %s,
                      'owner-key', 'SYSTEM', 'permission-test', 2)
            """,
            ("b" * 64,),
        )
        connection.execute(
            """
            INSERT INTO job_events (
              event_id, job_id, sequence, to_state, reason_code,
              actor_type, actor_id, trace_id
            ) VALUES ('event-owner', 'job-owner', 1, 'QUEUED', 'ENQUEUED',
                      'SYSTEM', 'permission-test', 'trace-owner')
            """
        )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE job_events SET reason_code = 'TAMPERED' "
                "WHERE event_id = 'event-owner'"
            )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute("DELETE FROM job_events WHERE event_id = 'event-owner'")


def test_attempt_children_cannot_cross_job_boundaries(role_database) -> None:
    owner, _ = role_database
    with psycopg.connect(owner.conninfo(), autocommit=True) as connection:
        for suffix in ("one", "two"):
            connection.execute(
                """
                INSERT INTO jobs (
                  job_id, job_type, state, payload, payload_fingerprint,
                  idempotency_key, actor_type, actor_id, max_attempts
                ) VALUES (%s, 'SNAPSHOT', 'QUEUED', '{}', %s, %s,
                          'SYSTEM', 'permission-test', 2)
                """,
                (f"job-{suffix}", suffix[0] * 64, f"key-{suffix}"),
            )
        connection.execute(
            """
            INSERT INTO job_attempts (
              attempt_id, job_id, attempt_number, worker_id, outcome,
              lease_token, lease_expires_at, claimed_at
            ) VALUES (
              'attempt-one', 'job-one', 1, 'worker-one', 'CLAIMED',
              'lease-one', now() + interval '1 minute', now()
            )
            """
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                INSERT INTO job_events (
                  event_id, job_id, attempt_id, sequence, to_state,
                  reason_code, actor_type, actor_id, trace_id
                ) VALUES (
                  'event-crossed', 'job-two', 'attempt-one', 1, 'CLAIMED',
                  'CLAIMED', 'WORKER', 'worker-one', 'trace-crossed'
                )
                """
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                INSERT INTO job_artifacts (
                  artifact_id, job_id, attempt_id, artifact_type,
                  relative_ref, sha256, size_bytes, media_type, validator_id
                ) VALUES (
                  'artifact-crossed', 'job-two', 'attempt-one', 'STDOUT',
                  'jobs/two/stdout.log', %s, 0, 'text/plain', 'stream-v1'
                )
                """,
                ("c" * 64,),
            )

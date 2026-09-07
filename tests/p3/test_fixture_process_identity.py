"""Sequential fixture children retain durable recovery attribution."""

from dataclasses import replace

import pytest

from packages.job_contracts import JobType
from services.job_worker.process_runner import HeartbeatDecision
from services.job_worker.results import ResultValidationError
from services.job_worker.worker import JobWorker
from tests.jobs.test_worker_lifecycle import claim, outcome, safety_evidence
from tests.p3.test_job_api import _alpha_request
from tests.p3.test_worker_profile import P3Repository
from tests.jobs.test_repository_transition_capabilities import _Connection, _worker


@pytest.mark.parametrize("accepted", [False, True])
def test_fixture_rebinds_each_child_before_extending_its_lease(accepted):
    payload = _alpha_request().payload.model_copy(update={
        "operation": "PARITY", "logical_trial_id": "p3-integration-fixture-v1",
    })
    claimed = replace(claim(max_attempts=1), job_type=JobType.ALPHA_CAMPAIGN, payload=payload)
    first = outcome().identity
    second = replace(first, pid=first.pid + 1, process_group=first.process_group + 1,
                     start_ticks=first.start_ticks + 1)

    class Repository(P3Repository):
        def replace_alpha_fixture_process(self, job, previous, current, trace_id):
            self.calls.append(("replace", previous, current))
            return accepted

    class Runner:
        decisions = []

        def run(self, prepared, environment, timeout, heartbeat, **kwargs):
            self.decisions = [heartbeat(first), heartbeat(first), heartbeat(second)]
            return outcome()

    class Validator:
        def validate_p3(self, *args, **kwargs):
            raise ResultValidationError("synthetic test has no qualification receipt")

    repository, runner = Repository(claimed), Runner()
    worker = JobWorker(
        repository, runner, Validator(), worker_id="worker-1", code_commit="e" * 40,
        environment=object(), safety_preflight=lambda: safety_evidence("4" * 64),
        prepare_spawn=lambda _: object(), p3_profile=True, p3_publisher=object(),
    )
    worker.run_once()
    assert [c for c in repository.calls if c[0] == "replace"] == [("replace", first, second)]
    assert runner.decisions == [HeartbeatDecision.CONTINUE, HeartbeatDecision.CONTINUE,
                               HeartbeatDecision.CONTINUE if accepted else HeartbeatDecision.STALE_LEASE]


def test_fixture_process_repository_uses_one_scoped_transaction():
    connection = _Connection({"replaced": True})
    repository = _worker(connection)
    previous = outcome().identity
    current = replace(previous, pid=previous.pid + 1)
    assert repository.replace_alpha_fixture_process(claim(), previous, current, "fixture:replace")
    statement, parameters = connection.calls[0]
    assert "job_plane.worker_replace_alpha_fixture_process" in statement
    assert parameters[4:8] == (previous.pid, previous.process_group, previous.start_ticks,
                               previous.command_fingerprint)
    assert parameters[8:12] == (current.pid, current.process_group, current.start_ticks,
                                current.command_fingerprint)
    assert connection.transaction_events == [("enter", None), ("exit", None)]


def test_fixture_process_sql_checks_fence_after_both_locks():
    import importlib.util
    from tests.p3.test_sql_authority import MIGRATION

    spec = importlib.util.spec_from_file_location("fixture_process_migration", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = []
    migration.op = type("Recorder", (), {"execute": staticmethod(statements.append)})
    migration.upgrade()
    source = "\n".join(statements)
    marker = "$worker_replace_alpha_fixture_process$"
    assert marker in source
    body = source.split(marker)[1]
    assert body.index("FROM public.jobs") < body.index("FROM public.job_attempts")
    assert body.count("FOR UPDATE") == 2
    assert body.rindex("FOR UPDATE") < body.index("clock_timestamp()")
    for fragment in (
        "p3-integration-fixture-v1", "p_previous_pid", "p_previous_group",
        "p_previous_ticks", "p_previous_fingerprint", "UPDATE public.job_attempts",
        "IS DISTINCT FROM p_lease_token", "current_job.attempt_count",
    ):
        assert fragment in body
    assert "OWNER TO trading_p3_owner" in source

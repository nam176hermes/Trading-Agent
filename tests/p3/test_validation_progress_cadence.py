"""Mandatory P3 boundary checks share the callback cadence without losing fences."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from packages.job_contracts import JobType
from services.job_worker import p3_output, worker as worker_module
from services.job_worker.errors import WorkerBlockedError
from services.job_worker.results import ValidatedP3Publication
from services.job_worker.worker import JobWorker
from tests.jobs.test_worker_lifecycle import Runner, claim, outcome, safety_evidence
from tests.p3.test_output_custody import custody
from tests.p3.test_worker_profile import P3Repository


@pytest.mark.parametrize("change", ["none", "drift", "expiry"])
def test_mandatory_checks_share_cadence_with_fresh_control_and_deadline(
    tmp_path, monkeypatch, change,
):
    clock = [0.0]
    timer = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(p3_output, "time", timer)
    monkeypatch.setattr(worker_module, "time", timer)
    handle, output, _ = custody(tmp_path)
    inventory = handle.retain()
    claimed = replace(
        claim(max_attempts=1), job_type=JobType.ALPHA_CAMPAIGN,
        payload=SimpleNamespace(
            operation="REGISTER_FAMILY", logical_trial_id="p3-register-family-v1",
        ),
    )
    repository = P3Repository(claimed)
    postrun = [False]
    full_checks = []
    control_checks = []

    def safety():
        if postrun[0]:
            full_checks.append(clock[0])
            clock[0] += 10
            if change == "drift" and len(full_checks) == 2:
                raise WorkerBlockedError("SAFETY_STATE_STALE", "synthetic drift")
        return safety_evidence("4" * 64)

    def control(*args, **kwargs):
        if postrun[0]:
            control_checks.append(clock[0])
        return "CONTINUE"

    repository.heartbeat_control = control

    class RetainedRunner(Runner):
        def run(self, *args, **kwargs):
            result = super().run(*args, **kwargs)
            postrun[0] = True
            # The existing custody deadline remains 300. Retention used 272s.
            clock[0] = 272.0
            return result

    class Validator:
        def validate_p3(self, *args, **kwargs):
            kwargs["progress"]()
            clock[0] += 0.25 if change == "none" else 1.01
            if change == "expiry":
                clock[0] = 291.0
            kwargs["progress"]()
            return ValidatedP3Publication("request", ("entry",))

    committed = []
    recovered = []

    class Publisher:
        def publish(self, *args, **kwargs):
            committed.append(clock[0])
            return "exact-commit"

        def recover_receipt(self, job_id, **kwargs):
            recovered.append(job_id)

    worker = JobWorker(
        repository, RetainedRunner(replace(
            outcome(), p3_output_custody=handle, p3_output_inventory_ref=inventory,
        )), Validator(), worker_id="worker-1", code_commit="e" * 40,
        environment=object(), safety_preflight=safety, prepare_spawn=lambda _: object(),
        p3_profile=True, p3_publisher=Publisher(),
    )
    assert worker.run_once() is True
    finalized = [call[2] for call in repository.calls if call[0] == "finalize"]
    if change == "none":
        assert committed == [292.25]
        assert recovered == [claimed.job_id]
        assert full_checks == [272.0, 282.25]
        # The callback still asks SQL for current control after each boundary.
        assert control_checks == [282.0, 282.0, 292.25, 292.25]
        assert not finalized
        assert not output.exists()
    else:
        assert not committed and not recovered
        assert len(finalized) == 1
        assert finalized[0]["final_state"].value == "BLOCKED"
        assert finalized[0]["reason_code"] == (
            "SAFETY_STATE_STALE" if change == "drift"
            else "RESULT_RECONCILIATION_REQUIRED"
        )
        # Both adversarial cases require the full check when its interval is due.
        assert len(full_checks) == 2

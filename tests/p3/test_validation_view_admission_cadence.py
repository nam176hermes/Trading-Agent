"""A slow session-view admission must precede the mandatory worker boundary."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from packages.job_contracts import JobType
from services.job_worker import p3_output, worker as worker_module
from services.job_worker.results import ValidatedP3Publication
from services.job_worker.worker import JobWorker
from tests.jobs.test_worker_lifecycle import Runner, claim, outcome, safety_evidence
from tests.p3.test_output_custody import custody
from tests.p3.test_worker_profile import P3Repository


@pytest.mark.parametrize("fault", [None, "cancel", "expiry"])
def test_view_admission_precedes_fresh_validation_boundary(tmp_path, monkeypatch, fault):
    clock = [0.0]
    timer = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(p3_output, "time", timer)
    monkeypatch.setattr(worker_module, "time", timer)
    handle, output, _ = custody(tmp_path)
    inventory = handle.retain()
    claimed = replace(claim(max_attempts=1), job_type=JobType.ALPHA_CAMPAIGN,
        payload=SimpleNamespace(operation="REGISTER_FAMILY", logical_trial_id="p3-register-family-v1"))
    repository = P3Repository(claimed)
    postrun = [False]
    admitted = [False]
    view = object()
    checks = []
    controls = []
    commits = []

    class Session:
        @property
        def view(self):
            clock[0] = 291.0 if fault == "expiry" else clock[0] + 6
            admitted[0] = True
            return view

    def safety():
        if postrun[0]:
            checks.append((clock[0], admitted[0]))
            clock[0] += 10
        return safety_evidence("4" * 64)

    def control(*args, **kwargs):
        if postrun[0]:
            controls.append(clock[0])
            if fault == "cancel" and admitted[0]:
                return "CANCEL"
        return "CONTINUE"

    repository.heartbeat_control = control

    class RetainedRunner(Runner):
        def run(self, *args, **kwargs):
            result = super().run(*args, **kwargs)
            postrun[0] = True
            clock[0] = 270.0
            # Isolate the post-retention seam: no session claim/release is issued.
            worker._p3_session = Session()
            return result

    class Validator:
        def validate_p3(self, *args, **kwargs):
            assert kwargs["holdout_view"] is view
            kwargs["progress"]()
            clock[0] += 0.25
            kwargs["progress"]()
            return ValidatedP3Publication("request", ("entry",))

    class Publisher:
        def publish(self, *args, **kwargs):
            commits.append(clock[0])
            return "exact-commit"

        def recover_receipt(self, job_id, **kwargs):
            assert job_id == claimed.job_id

    worker = JobWorker(repository, RetainedRunner(replace(outcome(),
        p3_output_custody=handle, p3_output_inventory_ref=inventory)), Validator(),
        worker_id="worker-1", code_commit="e" * 40, environment=object(),
        safety_preflight=safety, prepare_spawn=lambda _: object(), p3_profile=True,
        p3_publisher=Publisher())
    assert worker.run_once() is True
    finalized = [call[2] for call in repository.calls if call[0] == "finalize"]
    if fault is None:
        assert commits == [296.25]
        assert checks == [(276.0, True), (286.25, True)]
        assert controls == [286.0, 286.0, 296.25, 296.25]
        assert not finalized and not output.exists()
    else:
        assert not commits
        assert len(finalized) == 1
        assert finalized[0]["reason_code"] == (
            "CANCELLED" if fault == "cancel" else "RESULT_RECONCILIATION_REQUIRED")

from types import SimpleNamespace
import hashlib
from dataclasses import replace

import pytest

from packages.job_contracts import JobType
from services.job_store.worker_repository import WorkerRepository
from services.job_worker.command_registry import p3_command_spec
from services.job_worker.results import ResultValidationError, validate_p3_result_bytes
from services.job_worker.results import ValidatedP3Publication
from services.job_worker.worker import JobWorker
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.jobs.test_worker_lifecycle import (
    Repository, Runner, artifact, claim, outcome, safety_evidence,
)
from tests.jobs.test_repository_transition_capabilities import _Connection, _worker
from tests.p3.test_job_api import _alpha_request


def test_alpha_campaign_claim_is_separate_from_default_worker_profile() -> None:
    assert JobType.ALPHA_CAMPAIGN.value == "ALPHA_CAMPAIGN"
    assert hasattr(WorkerRepository,"claim_next_alpha_campaign")


def test_fixture_claim_requires_a_boolean_lane_selector() -> None:
    with pytest.raises(ValueError, match="fixture_only must be boolean"):
        _worker(_Connection()).claim_next_alpha_campaign(
            "worker-p3", 30, "p3:claim", fixture_only=1
        )


def test_fixture_recovery_requires_the_alpha_campaign_lane() -> None:
    with pytest.raises(ValueError, match="fixture recovery requires"):
        _worker(_Connection()).recover_expired_leases(
            object(), alpha_campaign=False, fixture_only=True
        )


@pytest.mark.parametrize("has_claim", [False, True])
def test_alpha_campaign_claim_uses_only_the_scoped_capability(
    has_claim: bool,
) -> None:
    request = _alpha_request()
    claimed_row = None if not has_claim else {
        "job_id": "job_fixed",
        "job_type": "ALPHA_CAMPAIGN",
        "payload": request.payload.model_dump(mode="json"),
        "attempt_number": 1,
        "max_attempts": 1,
        "lease_expires_at": "2026-09-05T01:00:00Z",
    }
    connection = _Connection(claimed_row)
    repository = _worker(connection)
    generated = iter(("attempt_fixed", "event_fixed"))
    repository._new_id = lambda _prefix: next(generated)

    claimed = repository.claim_next_alpha_campaign(
        "worker-p3", 30, "p3:claim"
    , fixture_only=False)

    assert "job_plane.worker_claim_alpha_campaign" in connection.calls[0][0]
    assert (claimed is not None) is has_claim
    if claimed is not None:
        assert claimed.job_id == "job_fixed"
        assert claimed.payload == request.payload


def test_worker_repository_binds_p3_publication_to_its_pool() -> None:
    repository = object.__new__(WorkerRepository)
    repository._pool = object()
    store = object()

    publication = repository.alpha_publication_repository(store)

    assert publication._pool is repository._pool
    assert publication._store is store


@pytest.mark.parametrize(
    ("workflow_operation", "operation", "validator"),
    (
        ("p3-baselines-v1", "BASELINES", "p3-baseline-selection-v1"),
        ("p3-register-family-v1", "REGISTER_FAMILY", "p3-publication-register-v1"),
        ("p3-oos-a0-v1", "OOS", "p3-publication-research-v1"),
        ("p3-select-primary-v1", "OOS", "p3-primary-selection-v1"),
        ("p3-holdout-primary-v1", "HOLDOUT", "p3-holdout-evaluation-result-v1"),
        ("p3-native-parity-v1", "PARITY", "p3-parity-result-v1"),
        ("p3-phase-exit-v1", "PHASE_EXIT", "p3-publication-exit-v1"),
    ),
)
def test_each_p3_workflow_operation_has_a_fixed_validator(
    workflow_operation: str, operation: str, validator: str
) -> None:
    spec = p3_command_spec(SimpleNamespace(
        operation=operation, logical_trial_id=workflow_operation
    ))
    assert spec.result_validator_id == validator
    assert spec.shell is False


def test_p3_worker_requires_an_explicit_spawn_profile() -> None:
    with pytest.raises(ValueError, match="P3 spawn authority"):
        JobWorker(
            object(), object(), object(), worker_id="worker-p3", code_commit="a" * 40,
            environment=object(), safety_preflight=lambda: object(), p3_profile=True,
        )


def test_p3_worker_requires_atomic_publication_capability() -> None:
    with pytest.raises(ValueError, match="P3 publication capability"):
        JobWorker(
            object(), object(), object(), worker_id="worker-p3", code_commit="a" * 40,
            environment=object(), safety_preflight=lambda: object(),
            prepare_spawn=lambda _: object(), p3_profile=True,
        )


class P3Repository(Repository):
    def claim_next_alpha_campaign(self, *args, **kwargs):
        value, self.claimed = self.claimed, None
        return value

    def start_attempt(self, *args, **kwargs):
        return True

    def pre_spawn_control(self, *args, **kwargs):
        return "CONTINUE"

    def heartbeat_control(self, *args, **kwargs):
        return "CONTINUE"

    def finalize_execution(self, *args, **kwargs):
        self.calls.append(("finalize", args, kwargs))
        return True


@pytest.mark.parametrize("receipt_failure", [False, True])
def test_p3_publication_commits_without_generic_success_finalize(receipt_failure) -> None:
    claimed = claim(max_attempts=1)
    claimed = replace(
        claimed, job_type=JobType.ALPHA_CAMPAIGN,
        payload=SimpleNamespace(
            operation="REGISTER_FAMILY",
            logical_trial_id="p3-register-family-v1",
        ),
    )

    class P3Validator:
        def validate_p3(self, *args, **kwargs):
            return ValidatedP3Publication("request", ("entry",))

    class Publisher:
        def __init__(self):
            self.calls = []
            self.recovered = []

        def publish(self, *args, **kwargs):
            self.calls.append((args, kwargs))

        def recover_receipt(self, job_id):
            assert self.calls, "receipt must follow SQL commit"
            self.recovered.append(job_id)
            if receipt_failure:
                raise RuntimeError("retained CAS temporarily unavailable")

    repository = P3Repository(claimed)
    publisher = Publisher()
    result = outcome()
    result = replace(result, result_validator_id="p3-publication-register-v1")
    worker = JobWorker(
        repository, Runner(result), P3Validator(), worker_id="worker-1",
        code_commit="e" * 40, environment=object(),
        safety_preflight=lambda: safety_evidence("4" * 64),
        prepare_spawn=lambda _: object(), p3_profile=True, p3_publisher=publisher,
    )

    if receipt_failure:
        with pytest.raises(RuntimeError, match="retained CAS"):
            worker.run_once()
    else:
        assert worker.run_once() is True
    assert publisher.recovered == [claimed.job_id]
    assert publisher.calls[0][0][:3] == ("request", claimed, ("entry",))
    assert not [call for call in repository.calls if call[0] == "finalize"]


@pytest.mark.parametrize('framing', [b'', b'\n', b' ', b'\n\n', b'\r\n'])
def test_p3_result_validator_parses_only_its_fixed_terminal_contract(framing) -> None:
    ref = {
        "content_sha256": "b" * 64,
        "size_bytes": 1,
        "media_type": "application/json",
        "locator": f"{'b' * 64}.blob",
    }
    value = {
        "schema_version": "p3-primary-selection-v1",
        "family_review_ref": ref,
        "selection_policy_digest": "c" * 64,
        "primary_alpha_id": None,
        "primary_version": None,
        "primary_candidate_head_ref": None,
        "outcome": "NONE_QUALIFIED",
    }
    value["digest"] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    raw = canonical_json_bytes(value) + framing
    if framing not in {b'', b'\n'}:
        with pytest.raises(ResultValidationError):
            validate_p3_result_bytes("p3-primary-selection-v1", raw)
        return
    assert validate_p3_result_bytes("p3-primary-selection-v1", raw).digest == value["digest"]
    with pytest.raises(ResultValidationError, match="invalid"):
        validate_p3_result_bytes("p3-parity-result-v1", raw)


def test_p3_result_failure_never_automatically_repeats_an_economic_operation() -> None:
    from packages.job_contracts import JobState

    claimed = replace(
        claim(max_attempts=3), job_type=JobType.ALPHA_CAMPAIGN,
        payload=SimpleNamespace(operation="OOS", logical_trial_id="p3-oos-a0-v1"),
    )

    class InvalidResult:
        def validate_p3(self, *args, **kwargs):
            raise ResultValidationError("replay mismatch")

    repository = P3Repository(claimed)
    worker = JobWorker(
        repository, Runner(outcome()), InvalidResult(), worker_id="worker-1",
        code_commit="e" * 40, environment=object(),
        safety_preflight=lambda: safety_evidence("4" * 64),
        prepare_spawn=lambda _: object(), p3_profile=True, p3_publisher=object(),
    )
    assert worker.run_once() is True
    assert not [call for call in repository.calls if call[0] == "retry"]
    final = [call for call in repository.calls if call[0] == "finalize"]
    assert len(final) == 1
    assert final[0][2]["final_state"] is JobState.FAILED
    assert final[0][2]["reason_code"] == "RESULT_VALIDATION_FAILED"


def test_official_claim_requires_explicit_lane():
    with pytest.raises(TypeError, match='fixture_only'):
        _worker(_Connection()).claim_next_alpha_campaign('worker-p3',30,'p3:claim')


@pytest.mark.parametrize('lane', [None, 0, 1, 'false'])
def test_official_recovery_requires_explicit_boolean_lane(lane):
    connection = _Connection()
    with pytest.raises(ValueError):
        _worker(connection).recover_expired_leases(object(),alpha_campaign=True,fixture_only=lane)
    assert connection.calls == []


def test_protected_recovery_call_rejects_an_implicit_lane():
    connection = _Connection()
    with pytest.raises(ValueError, match='explicit lane'):
        _worker(connection)._recover_observed_candidate({},'ABSENT','test:recovery','worker-startup-recovery',alpha_campaign=True)
    assert connection.calls == []


@pytest.mark.parametrize('control',['CANCEL','STALE'])
def test_p3_rechecks_sql_authority_after_preparing_spawn(control):
    from packages.job_contracts import JobState
    claimed=replace(claim(),job_type=JobType.ALPHA_CAMPAIGN,
        payload=SimpleNamespace(operation='BASELINES',logical_trial_id='p3-baselines-v1'))
    prepared=False
    class CurrentRepository(P3Repository):
        def pre_spawn_control(self,*args,**kwargs):
            return control if prepared else 'CONTINUE'
    class BeforePopenRunner:
        def run(self,prepare,*args,preflight,**kwargs):
            preflight()
            prepare()
            preflight()
            pytest.fail('SQL authority changed but the P3 child reached Popen')
    def prepare(_):
        nonlocal prepared
        prepared=True
        return object()
    repository=CurrentRepository(claimed)
    worker=JobWorker(repository,BeforePopenRunner(),object(),worker_id='worker-1',code_commit='e'*40,
        environment=object(),safety_preflight=lambda:safety_evidence('4'*64),
        prepare_spawn=prepare,p3_profile=True,p3_publisher=object())
    assert worker.run_once() is True
    finalizations=[call for call in repository.calls if call[0] == 'finalize']
    if control == 'CANCEL':
        assert len(finalizations) == 1
        assert finalizations[0][2]['expected_state'] is JobState.CANCEL_REQUESTED
        assert finalizations[0][2]['final_state'] is JobState.CANCELLED
    else:
        assert finalizations == []


@pytest.mark.parametrize('publication', [False, True])
@pytest.mark.parametrize('fault', [None, 'commit_error', 'lost_fence', 'receipt_error'])
def test_p3_private_outputs_survive_until_durable_commit(publication, fault):
    claimed=replace(claim(max_attempts=1),job_type=JobType.ALPHA_CAMPAIGN,
        payload=SimpleNamespace(operation='REGISTER_FAMILY' if publication else 'BASELINES',
            logical_trial_id='p3-register-family-v1' if publication else 'p3-baselines-v1'))
    events=[]
    inventory=object()
    class Custody:
        def cleanup(self):
            events.append('cleanup')
        def abandon(self):
            events.append('close')
    class Validator:
        def validate_p3(self,*args,**kwargs):
            events.append('validate')
            assert kwargs['output_inventory_ref'] is inventory
            return ValidatedP3Publication('request',('entry',)) if publication else object()
    class Repository(P3Repository):
        def finalize_execution(self,*args,**kwargs):
            events.append('commit')
            assert 'cleanup' not in events
            assert kwargs['outcome'].p3_output_inventory_ref is inventory
            if fault == 'commit_error':
                raise RuntimeError('SQL unavailable')
            return fault != 'lost_fence'
    class Publisher:
        def publish(self,*args,**kwargs):
            events.append('commit')
            assert 'cleanup' not in events
            if fault in {'commit_error','lost_fence'}:
                raise RuntimeError('SQL unavailable')
        def recover_receipt(self,job_id):
            events.append('receipt')
            if fault == 'receipt_error':
                raise RuntimeError('receipt unavailable')
    worker=JobWorker(Repository(claimed),Runner(replace(outcome(),p3_output_custody=Custody(),
        p3_output_inventory_ref=inventory)),Validator(),worker_id='worker-1',code_commit='e'*40,
        environment=object(),safety_preflight=lambda:safety_evidence('4'*64),
        prepare_spawn=lambda _:object(),p3_profile=True,p3_publisher=Publisher())
    raises=fault == 'commit_error' or publication and fault in {'lost_fence','receipt_error'}
    if raises:
        with pytest.raises(RuntimeError,match='unavailable'):
            worker.run_once()
    else:
        assert worker.run_once()
    assert events[-1] == 'close'
    if raises or fault == 'lost_fence':
        assert 'cleanup' not in events
    elif not publication:
        assert events.index('cleanup') > events.index('commit')

import hashlib
import json
from decimal import Decimal
import pytest

from packages.alpha_lifecycle.contracts.results import ExecutableResult, NativeTraceRow, ReplayReceipt
from packages.alpha_lifecycle.parity import compare_executable_results
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes


def _sealed(store: LocalArtifactStore, value: object):
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def _trace(store: LocalArtifactStore, *, fee: str = "0.1", cash: str = "99899.9"):
    source = store.put_bytes(b"source", media_type="application/octet-stream")
    payload = {
        "schema_version": "p3-native-trace-row-v1",
        "sequence": 0,
        "event_time_ns": 2,
        "init_time_ns": 2,
        "kind": "FILL",
        "side": "BUY",
        "price": "100",
        "quantity": "1",
        "fee_quote": fee,
        "cash_after": cash,
        "position_after": "1",
        "source_day": "2026-01-02",
        "source_artifact_ref": source,
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return _sealed(store, (NativeTraceRow.model_validate(payload),))


def _result(store: LocalArtifactStore, *, trace_ref, ending_cash: str = "99899.9"):
    identity = _sealed(store, {"identity": "fixed"})
    payload = {
        "schema_version": "p3-executable-result-v1",
        "manifest_ref": identity,
        "parity_policy_digest": "a" * 64,
        "instrument_spec_ref": identity,
        "transition_trace_ref": identity,
        "fill_trace_ref": trace_ref,
        "ending_cash": ending_cash,
        "ending_position": "1",
        "net_return": "-0.001001",
        "max_drawdown": "0.001001",
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return _sealed(store, ExecutableResult.model_validate(payload))


def _receipt(store: LocalArtifactStore, result_ref, replicate: str):
    environment = _sealed(store, {"environment": "fixed"})
    payload = {
        "schema_version": "p3-replay-receipt-v1",
        "logical_trial_id": "p3-native-parity",
        "replicate": replicate,
        "manifest_digest": ExecutableResult.model_validate_json(store.read_bytes(result_ref)).manifest_ref.content_sha256,
        "result_ref": result_ref,
        "source": {
            "commit_sha": "c" * 40,
            "tree_sha": "d" * 40,
            "closure_schema_version": "pre-p3-source-closure-v1",
            "closure_policy_sha256": "e" * 64,
            "closure_sha256": "f" * 64,
        },
        "environment_ref": environment,
        "sandbox_policy_digest": "1" * 64,
        "started_at": "2026-01-02T00:00:00Z",
        "completed_at": "2026-01-02T00:00:01Z",
        "process_exit": 0,
        "network_denied": True,
        "output_inventory_digest": "2" * 64,
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return _sealed(store, ReplayReceipt.model_validate(payload))


def test_parity_comparator_checks_fields_tolerances_and_replica_bytes(tmp_path) -> None:
    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    trace = _trace(store)
    reference = _result(store, trace_ref=trace)
    native = _result(store, trace_ref=trace, ending_cash="99899.91")
    receipts = tuple(_receipt(store, native, replicate) for replicate in ("R1", "R2", "R3"))

    passed = compare_executable_results(
        reference,
        (native, native, native),
        receipts,
        store,
        quote_quantum=Decimal("0.01"),
    )
    assert passed.verdict == "PASS"

    changed = _result(store, trace_ref=_trace(store, fee="0.12"))
    changed_receipts = receipts[:2] + (_receipt(store, changed, "R3"),)
    failed = compare_executable_results(
        reference,
        (native, native, changed),
        changed_receipts,
        store,
        quote_quantum=Decimal("0.01"),
    )
    comparison = json.loads(store.read_bytes(failed.comparison_ref))
    assert failed.verdict == "FAIL"
    assert "native-replicas-differ" in comparison["exact_failures"]
    assert "R3:fee-0" in comparison["numeric_failures"]


def test_parity_rejects_intermediate_equity_drift_even_when_ending_cash_matches(tmp_path):
    store = LocalArtifactStore(tmp_path)
    reference = _result(store, trace_ref=_trace(store))
    for cash, verdict in [('99899.92', 'PASS'), ('99899.93', 'FAIL')]:
        native = _result(store, trace_ref=_trace(store, cash=cash))
        receipts = tuple(_receipt(store, native, replica) for replica in ('R1', 'R2', 'R3'))
        comparison = compare_executable_results(reference, (native,)*3, receipts, store, quote_quantum=Decimal('.01'))
        assert comparison.verdict == verdict


@pytest.mark.parametrize("role", ["reference", "native", "receipt"])
def test_parity_rejects_noncanonical_retained_contracts(tmp_path, role) -> None:
    store = LocalArtifactStore(tmp_path)
    result = _result(store, trace_ref=_trace(store))
    receipts = tuple(_receipt(store, result, replicate) for replicate in ("R1", "R2", "R3"))
    ref = receipts[0] if role == "receipt" else result
    altered = store.put_bytes(b" " + store.read_bytes(ref), media_type="application/json")
    with pytest.raises(ValueError, match="canonical"):
        compare_executable_results(
            altered if role == "reference" else result,
            (altered, result, result) if role == "native" else (result, result, result),
            (altered, *receipts[1:]) if role == "receipt" else receipts,
            store, quote_quantum=Decimal("0.01"),
        )


@pytest.mark.parametrize('quantum', ['0', '-0.01', 'NaN', 'Infinity'])
def test_parity_rejects_invalid_tolerance_before_reading_artifacts(tmp_path, quantum):
    store = LocalArtifactStore(tmp_path)
    absent = _sealed(store, {})
    with pytest.raises(ValueError, match='quantum'):
        compare_executable_results(absent, (absent,) * 3, (absent,) * 3, store,
            quote_quantum=Decimal(quantum))


@pytest.mark.parametrize('fault', ['noncanonical_trace', 'manifest', 'source', 'environment', 'trial', 'time'])
def test_parity_rejects_unbound_native_evidence(tmp_path, fault):
    store = LocalArtifactStore(tmp_path)
    trace = _trace(store)
    result = _result(store, trace_ref=trace)
    receipts = [_receipt(store, result, r) for r in ('R1', 'R2', 'R3')]
    if fault == 'noncanonical_trace':
        altered = store.put_bytes(b' ' + store.read_bytes(trace), media_type='application/json')
        native = _result(store, trace_ref=altered)
        receipts = [_receipt(store, native, r) for r in ('R1', 'R2', 'R3')]
        with pytest.raises(ValueError, match='canonical'):
            compare_executable_results(result, (native,) * 3, tuple(receipts), store, quote_quantum=Decimal('.01'))
        return
    body = json.loads(store.read_bytes(receipts[1]))
    if fault == 'manifest': body['manifest_digest'] = '9' * 64
    elif fault == 'source': body['source']['commit_sha'] = '9' * 40
    elif fault == 'environment': body['environment_ref'] = trace.model_dump(mode='json')
    elif fault == 'trial': body['logical_trial_id'] = 'another-experiment'
    else: body['completed_at'] = '2026-01-01T00:00:00Z'
    body.pop('digest')
    body['digest'] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    receipts[1] = _sealed(store, body)
    assert compare_executable_results(result, (result,) * 3, tuple(receipts), store,
        quote_quantum=Decimal('.01')).verdict == 'FAIL'

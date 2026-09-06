import hashlib
import json
from decimal import Decimal

from packages.alpha_lifecycle.contracts.results import ExecutableResult, NativeTraceRow, ReplayReceipt
from packages.alpha_lifecycle.parity import compare_executable_results
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes


def _sealed(store: LocalArtifactStore, value: object):
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def _trace(store: LocalArtifactStore, *, fee: str = "0.1"):
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
        "cash_after": "99899.9",
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
        "manifest_digest": "b" * 64,
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

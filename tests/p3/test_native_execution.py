"""Six-run parent orchestration with synthetic launch authority and child output."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import io
import json
import os

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes, payload_digest
from tests.p3.test_native_spawn import native_provider  # noqa: F401
from tests.p3.test_holdout_session import session_inputs  # noqa: F401
from tests.p3.test_reference_input import reference_seed  # noqa: F401


@pytest.mark.parametrize('fault', [None, 'real_processes', 'cancel', 'exit', 'identity', 'lineage',
    'fingerprint', 'request', 'truncated', 'output', 'revoked'])
def test_native_execution_owns_six_distinct_children_and_stops_on_failure(
    native_provider, tmp_path, monkeypatch, fault,
):
    from packages.alpha_lifecycle.contracts.execution import HoldoutManifest, InstrumentSpec
    from packages.alpha_lifecycle.executable_reference import run_executable_reference, run_selected_baseline_reference
    from packages.alpha_lifecycle.replica_store import _read
    from services.job_worker import p3_native_spawn as native, process_runner
    from services.job_worker.recovery import ProcessIdentity
    from tests.jobs.test_process_runner import safety_evidence

    from services.job_worker.p3_native_runner import NativeExecutionError, run_native_replicas
    provider, x, _, _ = native_provider
    view = x.session.view
    request = provider._requests[0]
    manifest = _read(view, request.manifest_ref, HoldoutManifest)
    spec = _read(view, request.instrument_spec_ref, InstrumentSpec)
    outputs = []
    for calculate in (run_executable_reference, run_selected_baseline_reference):
        result = calculate(manifest, spec, x.store)
        rows = [{k: v for k, v in row.items() if k not in {'schema_version', 'digest', 'source_artifact_ref'}}
            for row in json.loads(x.store.read_bytes(result.fill_trace_ref))]
        outputs.append(canonical_json_bytes(rows)+b'\n')
    calls, identities, writes = [], [], []
    put = x.session.put_bytes
    def retain(raw, *, media_type):
        writes.append(raw)
        return put(raw, media_type=media_type)
    monkeypatch.setattr(x.session, 'put_bytes', retain)

    class Runner:
        def __init__(self, writer): self.writer = writer
        def run(self, prepare, environment, timeout, heartbeat, *, job_id, attempt_id, preflight):
            built = native.consume_prepared_p3_native_spawn(prepare(), job_id=job_id, attempt_id=attempt_id)
            for fd in built.close_after_spawn_fds: os.close(fd)
            index = len(calls)
            calls.append((self.writer.root, job_id, attempt_id))
            identity = ProcessIdentity(900 + (0 if fault == 'identity' else index),
                900 + (0 if fault == 'identity' else index), 99, 'a'*64)
            assert heartbeat(identity) is process_runner.HeartbeatDecision.CONTINUE
            safety = process_runner._safety_metadata(preflight())
            lineage = built.lineage.as_metadata()
            if fault == 'lineage': lineage['os_sandbox_profile_sha256'] = 'd'*64
            if fault == 'request': lineage['engine_request_sha256'] = 'd'*64
            raw = b'[]\n' if fault == 'output' else outputs[index//3]
            stdout = self.writer.capture_stream(job_id, attempt_id, 'stdout', io.BytesIO(raw))
            stderr = self.writer.capture_stream(job_id, attempt_id, 'stderr', io.BytesIO(b''))
            if fault == 'truncated': stdout = replace(stdout, truncated=True)
            if fault == 'revoked': x.session.close()
            return process_runner.ProcessOutcome(7 if fault == 'exit' else 0,
                'CANCELLED' if fault == 'cancel' else None, identity, stdout, stderr,
                'd'*64 if fault == 'fingerprint' else built.capability_fingerprint,
                built.result_validator_id, built.source_revision,
                process_runner.ProcessLineage(lineage, safety, safety))
    if fault == 'real_processes':
        # Real lifecycle/stream capture; the child emits synthetic output instead
        # of running the attested engine. This cannot qualify native custody.
        import subprocess
        import sys
        from services.job_worker.engine_spawn import _sealed_memfd
        actual_runner = process_runner.ProcessRunner
        def factory(writer):
            def popen(argv, **kwargs):
                index = len(calls)
                calls.append((writer.root, provider._claim.job_id, provider._claim.attempt_id))
                fd = _sealed_memfd('synthetic-native-output', outputs[index//3], mode=0o400)
                try:
                    return subprocess.Popen([sys.executable, '-I', '-B', '-c',
                        'import os,sys,time; time.sleep(0.2); os.write(1,os.pread(int(sys.argv[1]),1048576,0))',
                        str(fd)], **{**kwargs, 'pass_fds': (*kwargs['pass_fds'], fd)})
                finally:
                    os.close(fd)
            return actual_runner(writer, popen=popen)
        monkeypatch.setattr(process_runner, 'ProcessRunner', factory)
    else:
        monkeypatch.setattr(process_runner, 'ProcessRunner', Runner)
    def heartbeat(identity):
        identities.append(identity)
        return process_runner.HeartbeatDecision.CONTINUE
    def preflight():
        now = datetime.now(UTC)
        return replace(safety_evidence('3'*64), generated_at=now-timedelta(seconds=1),
            expires_at=now+timedelta(seconds=5))
    kwargs = dict(view=view, output=x.session, artifact_root=tmp_path/'streams',
        heartbeat=heartbeat, preflight=preflight)
    before = datetime.now(UTC)
    if fault in (None, 'real_processes'):
        runs = run_native_replicas(provider, **kwargs)
        assert len(runs) == len(calls) == len(set(identities)) == 6
        assert [(r.role, r.replica) for r in runs] == list(native._ORDER)
        assert len({root for root, _, _ in calls}) == 6
        assert {(job, attempt) for _, job, attempt in calls} == {(provider._claim.job_id, provider._claim.attempt_id)}
        for run in runs:
            assert before <= run.started_at <= run.completed_at <= datetime.now(UTC)
            assert run.request_sha256 == payload_digest(provider._requests[run.role != 'PRIMARY'])
            assert x.store.read_bytes(run.result_ref)
            assert run.outcome.lineage.command['os_sandbox_profile_sha256'] != 'd'*64
        from services.job_worker.p3_native_runner import retain_native_proof
        from packages.alpha_lifecycle.parity import NativeParentProof, validate_native_proof
        proof_ref, receipt_refs = retain_native_proof(provider, runs, x.session)
        proof = _read(x.store, proof_ref, NativeParentProof)
        assert proof.session_sha256 == provider._session_digest
        assert proof.job_id == provider._claim.job_id
        assert proof.lease_token_sha256 == provider._claim.lease_token_sha256
        assert tuple(record.receipt_ref for record in proof.runs) == receipt_refs
        assert len({(record.pid, record.start_ticks) for record in proof.runs}) == 6
        assert validate_native_proof(proof_ref, manifest_ref=request.manifest_ref,
            instrument_spec_ref=request.instrument_spec_ref, store=x.store) == proof
        x.session.native_parent_proof_ref = proof_ref
        x.session._native_receipts = receipt_refs
        monkeypatch.setattr(x.session, 'execute_native', lambda **kwargs: runs)
        parity = x.session.calculate_parity(heartbeat=heartbeat, preflight=preflight)
        assert parity.verdict == 'PASS'
        from packages.alpha_lifecycle.parity import SessionParityPair, validate_parity_pair
        pair = _read(x.store, x.session.parity_pair_ref, SessionParityPair)
        assert pair.native_parent_proof_ref == proof_ref
        assert validate_parity_pair(x.session.parity_pair_ref, manifest_ref=request.manifest_ref,
            instrument_spec_ref=request.instrument_spec_ref, primary_reference_ref=pair.primary_reference_ref,
            baseline_reference_ref=pair.baseline_reference_ref, store=x.store,
            native_parent_proof_ref=proof_ref) == pair
        with pytest.raises(ValueError):
            retain_native_proof(provider, (*runs[:5], runs[0]), x.session)
        assert all(b'"steps"' not in raw for raw in writes)
        assert not list((tmp_path/'streams').rglob('request.log'))
    else:
        with pytest.raises(NativeExecutionError) as failed:
            run_native_replicas(provider, **kwargs)
        assert len(calls) == (2 if fault == 'identity' else 1)
        assert len(failed.value.completed) == (1 if fault == 'identity' else 0)
        if fault == 'cancel': assert failed.value.outcome.termination_reason == 'CANCELLED'
        if fault != 'identity': assert not writes
    with pytest.raises((RuntimeError, ValueError)):
        run_native_replicas(provider, **kwargs)


def test_session_revokes_view_when_native_execution_fails(native_provider, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from services.job_worker import p3_native_runner
    provider, x, _, _ = native_provider
    alias = x.session.view
    original_host = x.session._host
    def host(*args):
        original_host(*args)
        return SimpleNamespace(output_root=str(tmp_path/'protected-output'))
    monkeypatch.setattr(x.session, '_host', host)
    monkeypatch.setattr(x.session, 'native_spawn_provider', lambda: provider)
    def fail(actual, **kwargs):
        assert actual is provider and kwargs['view'] is alias and kwargs['output'] is x.session
        assert kwargs['artifact_root'] == tmp_path/'protected-output'/'native'
        raise RuntimeError('native child failed')
    monkeypatch.setattr(p3_native_runner, 'run_native_replicas', fail)
    with pytest.raises(RuntimeError, match='native child'):
        x.session.execute_native(heartbeat=lambda identity: None, preflight=lambda: None)
    with pytest.raises(ValueError, match='closed'):
        _ = alias.raw

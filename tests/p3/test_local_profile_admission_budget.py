"""Fresh complete LOCAL admission must fit the fixed output-retention budget."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from packages.alpha_lifecycle import authority, local_qualification, local_research
from packages.alpha_lifecycle.contracts.authority import RUN_AUTHORIZATION
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages import local_pre_p3
from packages.engine_contracts import canonical_json_bytes
from packages.runtime_release import config
from services.job_worker import p3_output
from tests.p3.test_host_profile import profile_inputs  # noqa: F401
from tests.p3.test_local_qualification import sealed
from tests.p3.test_output_custody import custody
from tests.p3.test_spawn_capability import synthetic_provider  # noqa: F401


@pytest.mark.parametrize('fault', [None, 'revoked', 'expired', 'source', 'issuer'])
def test_local_retention_rechecks_qualification_within_postrun_budget(
    tmp_path, monkeypatch, profile_inputs, fault,
):
    module, provider, job, closure, document, _, digest, _ = profile_inputs
    from packages.job_contracts import payload_fingerprint
    import json
    value = json.loads(provider._store.read_bytes(job.payload.authorization_ref))
    for key in ('issuer_workflow', 'issuer_run_id', 'issuer_attempt'):
        value.pop(key)
    identity = dict(issuer_kind='LOCAL', issuer_identity='operator-a',
        run_id='00000000-0000-4000-8000-000000000002', attempt=1,
        host_id='a'*64, boot_id='00000000-0000-4000-8000-000000000001')
    auth = RUN_AUTHORIZATION.validate_json(canonical_json_bytes(sealed({**value,
        'schema_version': 'p3-local-run-authorization-v1', 'identity': identity})))
    auth_ref = provider._store.put_bytes(canonical_json_bytes(auth), media_type='application/json')
    job = replace(job, payload=job.payload.model_copy(update={'authorization_ref': auth_ref}))
    document.update(schema_version='p3-local-official-host-profile-v1', identity=identity,
        payload=job.payload.model_dump(mode='json'))
    intent = P3OperationInput.model_validate_json(provider._store.read_bytes(job.payload.manifest_ref))
    request = dict(schema_version='p3-local-authority-request-v1', execution_source=closure.source,
        authorization=auth, operation_input=intent)
    operation_path = Path('/etc/trading-agent/local-qualification/operator-a/operations') / f'{auth.nonce}.json'
    def protected(path):
        assert path == operation_path
        return canonical_json_bytes(request)+b'\n', (1, 2)
    monkeypatch.setattr(config, '_read_protected_file', protected)
    monkeypatch.setattr(local_qualification, '_host_identity',
        lambda: (auth.identity.host_id, UUID(identity['boot_id'])))
    expires = datetime.now(UTC)+timedelta(hours=1)
    monkeypatch.setattr(local_research, 'read_local_research_inputs',
        lambda *args, **kwargs: SimpleNamespace(expires_at=expires))
    monkeypatch.setattr(module, 'validate_integration_job_result',
        lambda *args, **kwargs: SimpleNamespace(identity=auth.identity))
    # Keep the actual nested issuer validator, rather than mocking its readiness contract.
    monkeypatch.setattr(module, 'validate_operation_issuer', authority.validate_operation_issuer)
    clock = [0.0]
    admissions = []
    def prerequisites(root):
        admissions.append(clock[0])
        clock[0] += 1.8  # Observed warm current readback, rounded upward.
        if fault == 'revoked' and len(admissions)>1:
            raise ValueError('synthetic prerequisite revoked')
        return dict(PRE_P3_READY=SimpleNamespace(
            identity=auth.identity.model_copy(update={'attempt': 2}) if fault=='issuer' else auth.identity,
            binding=SimpleNamespace(source=closure.source.model_copy(update={'commit_sha': '9'*40})
                if fault=='source' else closure.source),
            expires_at=auth.expires_at-timedelta(seconds=1) if fault=='expired' else expires))
    monkeypatch.setattr(local_pre_p3, 'read_local_pre_p3', prerequisites)
    monkeypatch.setattr(p3_output, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    handle, output, store = custody(tmp_path)
    (output/'artifacts').mkdir(mode=0o700)
    for index in range(18):
        raw = canonical_json_bytes({'synthetic_output': index})
        path = output/'artifacts'/(hashlib.sha256(raw).hexdigest()+'.blob')
        path.write_bytes(raw)
        path.chmod(0o600)
    def before_retain(*_):
        clock[0] += 24.5  # Other bounded pre-retention work in the pressure reproduction.
    def fence():
        profile, current = module.read_official_profile(Path('/synthetic/profile'), digest(),
            job_id=job.job_id, payload=job.payload, context={})
        assert profile.identity == auth.identity
        assert current == replace(closure, bound_payload_fingerprint=payload_fingerprint(job.payload))
        clock[0] += 9.0  # Other owner/worker work; measured write intervals were about12.6s.
    handle._before_retain = before_retain
    handle._before_write = fence
    try:
        if fault is None:
            inventory = handle.retain()
            clock[0] += 38  # Controlled remaining validation; not a historical inner trace.
            handle.check_deadline()
            assert store.read_bytes(inventory)
            assert admissions and clock[0] < 300
        else:
            with pytest.raises((ValueError, module.AuthorityHeld)):
                handle.retain()
            assert output.exists() and admissions
    finally:
        handle.abandon()

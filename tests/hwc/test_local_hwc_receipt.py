"""Local HWC readback uses the existing headless semantic validator."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from packages.engine_contracts import canonical_json_bytes
from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity, QualificationBinding
from packages.runtime_release import config
from tests.p3.test_local_qualification import local, sealed  # noqa: F401
from tests.hwc.test_headless_portable import _recovery_report

ROOT = Path(__file__).resolve().parents[2]


def test_local_hwc_authenticates_and_revalidates_retained_headless_proof(local, monkeypatch):
    from packages import hwc_status
    assert hasattr(hwc_status, 'validate_local_hwc_portable_receipt'), 'local HWC readback is missing'
    from scripts import qualify_hwc_headless as headless
    from scripts.record_hwc_portable_receipt import _boundary_report
    monkeypatch.setattr(headless, '_recovery_campaign', lambda *_: _recovery_report())
    proof = headless.qualify(require_clean=False, build_dashboard=False)
    _, store, _, identity, binding, receipt, approval, _, _ = local
    binding.update(gate='HWC_PORTABLE_QUALIFIED', source=proof['source'])
    receipt['binding'] = binding
    boundary = _boundary_report(ROOT)
    contracts = canonical_json_bytes(dict(schema_version='hwc-generated-contract-report-v1',
        command='scripts/generate_contracts.py --check', status='PASS', stdout_sha256='a'*64,
        stderr_sha256='b'*64)) + b'\n'
    def validate(candidate):
        raws = (canonical_json_bytes(candidate)+b'\n', boundary, contracts)
        receipt['evidence_refs'] = [store.put_bytes(raw, media_type='application/json').model_dump(mode='json') for raw in raws]
        receipt.update(sealed(receipt))
        approval['receipt_ref'] = store.put_bytes(canonical_json_bytes(receipt), media_type='application/json').model_dump(mode='json')
        approval.update(sealed(approval))
        monkeypatch.setattr(config, '_read_protected_file', lambda path: (canonical_json_bytes(approval)+b'\n', (1,2)))
        return hwc_status.validate_local_hwc_portable_receipt(
            LocalRunIdentity.model_validate_json(canonical_json_bytes(identity)),
            QualificationBinding.model_validate_json(canonical_json_bytes(binding)), root=ROOT, store=store)
    assert validate(proof).identity.issuer_kind == 'LOCAL'
    for mutate in (
        lambda value: value['cleanup'].update(complete=False),
        lambda value: value['source'].update(commit_sha='0'*40),
        lambda value: value['evidence']['recovery_campaign']['records'].pop(),
        lambda value: value['observations'].update(operator_state_unchanged=False),
    ):
        broken = deepcopy(proof)
        mutate(broken)
        broken['receipt_sha256'] = hashlib.sha256(canonical_json_bytes(
            {k:v for k,v in broken.items() if k!='receipt_sha256'})).hexdigest()
        with pytest.raises(ValueError):
            validate(broken)

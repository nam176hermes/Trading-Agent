"""Private pair validation uses synthetic receipts, never native authority."""
import hashlib

import pytest

from packages.alpha_lifecycle.contracts.results import ReplayReceipt
from packages.alpha_lifecycle.parity import build_parity_pair, validate_parity_pair
from packages.alpha_lifecycle.replica_store import _read
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_native_request import native_inputs  # noqa: F401
from tests.p3.test_reference_input import reference_seed  # noqa: F401


def _put(store, value):
    return store.put_bytes(canonical_json_bytes(value), media_type='application/json')


def _change(store, ref, **changes):
    import json
    value = json.loads(store.read_bytes(ref))
    value.pop('digest')
    value.update(changes)
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return _put(store, value)


@pytest.fixture
def pair_inputs(native_inputs):
    return _pair_inputs(*native_inputs)


def _pair_inputs(store, view, manifest_ref, spec_ref):
    from packages.alpha_lifecycle.contracts.execution import HoldoutManifest, InstrumentSpec, EnvironmentIdentity
    from packages.alpha_lifecycle.executable_reference import run_executable_reference, run_selected_baseline_reference
    from packages.alpha_lifecycle.parity import compare_executable_results
    from decimal import Decimal
    manifest = _read(view, manifest_ref, HoldoutManifest)
    spec = _read(view, spec_ref, InstrumentSpec)
    environment = _read(view, manifest.environment_ref, EnvironmentIdentity)
    references, parities = [], []
    for role, calculate in enumerate((run_executable_reference, run_selected_baseline_reference)):
        reference = _put(store, calculate(manifest, spec, store))
        references.append(reference)
        receipts = []
        for replica in range(1, 4):
            value = dict(schema_version='p3-replay-receipt-v1', logical_trial_id='p3-native-parity-v1',
                replicate=f'R{replica}', manifest_digest=manifest_ref.content_sha256, result_ref=reference,
                source=manifest.source, environment_ref=manifest.environment_ref,
                sandbox_policy_digest=environment.sandbox_policy_digest,
                started_at=f'2026-09-02T00:00:0{role}Z', completed_at=f'2026-09-02T00:00:0{role+1}Z',
                process_exit=0, network_denied=True, output_inventory_digest=str(role+1)*64)
            value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
            receipts.append(_put(store, ReplayReceipt.model_validate(value)))
        parities.append(_put(store, compare_executable_results(reference, (reference,)*3,
            tuple(receipts), store, quote_quantum=Decimal(spec.quote_quantum))))
    return store, dict(manifest_ref=manifest_ref, instrument_spec_ref=spec_ref,
        primary_reference_ref=references[0], baseline_reference_ref=references[1],
        primary_parity_ref=parities[0], baseline_parity_ref=parities[1])


def test_pair_recomputes_both_roles_and_rejects_substitution(pair_inputs):
    store, arguments = pair_inputs
    pair = build_parity_pair(store=store, **arguments)
    pair_ref = _put(store, pair)
    before = set(store._root.iterdir())
    assert validate_parity_pair(pair_ref, store=store, **{key: value for key, value in arguments.items()
        if key not in {'primary_parity_ref', 'baseline_parity_ref'}}) == pair
    assert pair.verdict == 'PASS' and set(store._root.iterdir()) == before
    with pytest.raises(ValueError):
        build_parity_pair(store=store, **{**arguments, 'baseline_parity_ref':arguments['primary_parity_ref']})
    from packages.alpha_lifecycle.contracts.results import ParityResult
    original = _read(store, arguments['primary_parity_ref'], ParityResult)
    for fault in ('source', 'trial', 'policy', 'comparison', 'verdict'):
        altered = arguments['primary_parity_ref']
        if fault in {'source', 'trial', 'policy'}:
            receipt = _read(store, original.native_receipt_refs[0], ReplayReceipt)
            changes = {'source': {'source':{**receipt.source.model_dump(mode='json'), 'commit_sha':'9'*40}},
                'trial': {'logical_trial_id':'other-experiment'}, 'policy': {'sandbox_policy_digest':'9'*64}}[fault]
            refs = tuple(_change(store, ref, **changes) for ref in original.native_receipt_refs)
            altered = _change(store, altered, native_receipt_refs=refs)
        else:
            altered = _change(store, altered, **({'comparison_ref':arguments['manifest_ref']}
                if fault == 'comparison' else {'verdict':'FAIL', 'exact_fields_pass':False}))
        with pytest.raises(ValueError):
            build_parity_pair(store=store, **{**arguments, 'primary_parity_ref':altered})


@pytest.mark.parametrize('field,value', [('net_return', '999'), ('max_drawdown', '999')])
def test_pair_rejects_canonical_but_forged_native_summary(pair_inputs, field, value):
    from decimal import Decimal
    from packages.alpha_lifecycle.contracts.results import ParityResult
    from packages.alpha_lifecycle.parity import compare_executable_results
    store, arguments = pair_inputs
    parity = _read(store, arguments['primary_parity_ref'], ParityResult)
    native = _change(store, parity.native_result_refs[0], **{field:value})
    receipts = tuple(_change(store, ref, result_ref=native) for ref in parity.native_receipt_refs)
    comparison = compare_executable_results(parity.reference_ref, (native,)*3, receipts, store, quote_quantum=Decimal('.01'))
    assert comparison.verdict == 'PASS'  # Per-field parity alone does not verify summary arithmetic.
    with pytest.raises(ValueError, match='summary'):
        build_parity_pair(store=store, **{**arguments, 'primary_parity_ref':_put(store, comparison)})

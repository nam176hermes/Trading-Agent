"""Executed PIT evidence must enumerate real passing calls, never just a manifest."""
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.pit_suite import PIT_EVIDENCE_MEDIA
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_publication import _changed


SUITE = Path(__file__).resolve().parents[2]/'docs/implementation/pre-p3/p2-pit-adversarial-suite-v1.json'


def suite_inputs(tmp_path):
    (tmp_path/'artifacts').mkdir(mode=0o700)
    store = LocalArtifactStore(tmp_path/'artifacts')
    suite = json.loads(SUITE.read_bytes())
    manifest_ref = store.put_bytes(SUITE.read_bytes(), media_type=PIT_EVIDENCE_MEDIA)
    source = SourceIdentity(commit_sha='a'*40, tree_sha='b'*40,
        closure_schema_version='synthetic-source', closure_policy_sha256='c'*64, closure_sha256='d'*64)
    report = dict(schema_version=1, component='root', collection_only=False, pytest_exit_status=0,
        summary={'passed':14}, tests=[dict(test_node_id=case['node_id'], component='root',
            outcome='passed', reason='', phase='call') for case in sorted(suite['cases'],key=lambda c:c['node_id'])])
    parameters = {
        'test_symbol_mapping_lookup_keys_cannot_change_within_fact': ('provider','raw-symbol'),
        'test_rejects_stale_ambiguous_or_unrepresentable_inputs': (
            'ambiguous-mapping-cannot be projected','corporate-action-corporate action',
            'missing-issuer-cannot be projected','off-grid-market-market price',
            'off-grid-target-target schedule','stale-market-market dataset','wrong-target-instrument-target schedule'),
    }
    report['tests'] = sorted([{**row,'test_node_id':row['test_node_id']+suffix}
        for row in report['tests'] for suffix in (
            tuple('['+p+']' for p in parameters[row['test_node_id'].split('::')[-1]])
            if row['test_node_id'].split('::')[-1] in parameters else ('',))], key=lambda row:row['test_node_id'])
    qualification = dict(completed_at_utc='2026-09-10T14:00:00Z', producer='scripts/qualify_pre_p3.py', run_id='1', run_attempt='1')
    return store, manifest_ref, source, report, qualification


@pytest.mark.parametrize('fault', [None, 'manifest_only', 'missing', 'duplicate', 'extra',
    'skipped', 'deselected', 'xfailed', 'xpassed', 'failed', 'not_run', 'setup_only',
    'collection_only', 'nonzero_exit', 'wrong_component', 'false_summary', 'float_summary'])
def test_pit_suite_requires_exact_executed_nodes(tmp_path, fault):
    from packages.alpha_lifecycle.pit_suite import build_pit_suite_receipt, validate_pit_suite_receipt
    store, manifest_ref, source, report, qualification = suite_inputs(tmp_path)
    collection = {**report, 'collection_only':True, 'summary':{'collected':14},
        'tests':[{**row, 'outcome':'collected', 'phase':'collection'} for row in report['tests']]}
    collection_ref = store.put_bytes(canonical_json_bytes(collection), media_type='application/json')
    if fault == 'manifest_only':
        report = json.loads(SUITE.read_bytes())
    elif fault == 'missing':
        report['tests'].pop()
    elif fault == 'duplicate':
        report['tests'][-1] = report['tests'][0]
    elif fault == 'extra':
        report['tests'].append({**report['tests'][0], 'test_node_id':'tests/extra.py::test_extra'})
    elif fault in {'skipped','deselected','xfailed','xpassed','failed','not_run'}:
        report['tests'][0]['outcome'] = fault
    elif fault == 'setup_only':
        report['tests'][0]['phase'] = 'setup'
    elif fault == 'collection_only':
        report['collection_only'] = True
    elif fault == 'nonzero_exit':
        report['pytest_exit_status'] = 1
    elif fault == 'wrong_component':
        report['component'] = 'legacy'
    elif fault == 'false_summary':
        report['summary'] = {'passed':6}
    elif fault == 'float_summary':
        report['summary'] = {'passed':14.0}
    raw = (json.dumps(report, indent=2, sort_keys=True)+'\n').encode()
    report_ref = store.put_bytes(raw, media_type=PIT_EVIDENCE_MEDIA)
    if fault:
        with pytest.raises(ValueError):
            build_pit_suite_receipt(source, manifest_ref, collection_ref, report_ref, qualification, store=store)
        return
    receipt = build_pit_suite_receipt(source, manifest_ref, collection_ref, report_ref, qualification, store=store)
    ref = store.put_bytes(canonical_json_bytes(receipt), media_type='application/json')
    assert validate_pit_suite_receipt(ref, source=source, store=store) == receipt
    assert len(receipt.cases) == receipt.logical_selector_count == 7
    assert receipt.passed_node_count == 14
    with pytest.raises(ValueError):
        validate_pit_suite_receipt(ref, source=source.model_copy(update={'commit_sha':'9'*40}), store=store)
    # Consumer must reread the underlying observations even if the receipt self-digest is valid.
    fake_ref = store.put_bytes(b'{}', media_type='application/json')
    forged = _changed(receipt, report_ref=fake_ref.model_dump(mode='json'))
    forged_ref = store.put_bytes(canonical_json_bytes(forged), media_type='application/json')
    with pytest.raises(ValueError):
        validate_pit_suite_receipt(forged_ref, source=source, store=store)


def test_pit_producer_retains_seven_real_portable_test_calls(tmp_path, monkeypatch):
    from scripts import qualify_pre_p3 as producer
    from packages.alpha_lifecycle.pit_suite import validate_pit_suite_receipt
    store, _, source, _, qualification = suite_inputs(tmp_path)
    observed = []
    def source_check():
        observed.append('source')
        return source.model_dump(mode='json')
    monkeypatch.setattr(producer, '_source_v2', source_check)
    from scripts import trusted_test_tmp
    prepare = trusted_test_tmp.prepare_trusted_test_tmp
    private_roots = []
    def tracked_prepare(component):
        session = prepare(component)
        private_roots.append(session.path)
        return session
    monkeypatch.setattr(trusted_test_tmp, 'prepare_trusted_test_tmp', tracked_prepare)
    # All test metadata is explicitly synthetic; only the subprocess observations are real.
    started = datetime.now(UTC).replace(microsecond=0)
    receipt = producer._execute_pit_suite(source.model_dump(mode='json'), qualification, store)
    ref = store.put_bytes(canonical_json_bytes(receipt), media_type='application/json')
    assert validate_pit_suite_receipt(ref, source=source, store=store) == receipt
    assert len(observed) >= 2
    report = json.loads(store.read_bytes(receipt.report_ref))
    assert report['summary'] == {'passed':14}
    assert receipt.logical_selector_count == 7
    assert receipt.collected_node_count == receipt.passed_node_count == 14
    assert receipt.qualification.completed_at_utc >= started
    assert len(private_roots) == 1 and not private_roots[0].exists()


def test_pit_suite_cannot_drop_one_collected_parameter(tmp_path):
    from packages.alpha_lifecycle.pit_suite import build_pit_suite_receipt
    store, manifest_ref, source, report, qualification = suite_inputs(tmp_path)
    collection = {**report, 'collection_only':True, 'summary':{'collected':14},
        'tests':[{**row, 'outcome':'collected', 'phase':'collection'} for row in report['tests']]}
    collection_ref = store.put_bytes(canonical_json_bytes(collection), media_type='application/json')
    report['tests'] = [row for index,row in enumerate(report['tests']) if index != 3]
    report['summary'] = {'passed':13}
    report_ref = store.put_bytes((json.dumps(report,indent=2,sort_keys=True)+'\n').encode(), media_type=PIT_EVIDENCE_MEDIA)
    with pytest.raises(ValueError):
        build_pit_suite_receipt(source, manifest_ref, collection_ref, report_ref, qualification, store=store)


def test_host_workflow_retains_pit_report_objects():
    from scripts.check_p0_ci_closure import _host_workflow_valid
    raw = (SUITE.parents[3]/'.github/workflows/host-authority.yml').read_bytes()
    assert b'/p2-pit-artifacts/*.blob' in raw
    assert _host_workflow_valid(raw)
    assert not _host_workflow_valid(raw.replace(b'/p2-pit-artifacts/*.blob', b'/**'))


@pytest.mark.parametrize('drift_after_execution', [False, True])
def test_p2_source_binds_retained_pit_receipt_before_publication(tmp_path, monkeypatch, drift_after_execution):
    from scripts import qualify_pre_p3 as producer
    from packages.alpha_lifecycle.pit_suite import PITAdversarialSuiteReceiptV1, validate_pit_suite_receipt
    from packages.pre_p3_provenance import SOURCE_CLOSURE_POLICY_SHA256, SOURCE_CLOSURE_SCHEMA, validate_v2_gate_receipt
    _, _, source, _, qualification = suite_inputs(tmp_path)
    source = source.model_copy(update={'closure_schema_version':SOURCE_CLOSURE_SCHEMA,
        'closure_policy_sha256':SOURCE_CLOSURE_POLICY_SHA256})
    calls = []
    def current_source():
        calls.append('source')
        return source.model_copy(update={'commit_sha':'9'*40} if drift_after_execution and len(calls) == 5 else {}).model_dump(mode='json')
    run = producer._run
    def selected_run(*command, **kwargs):
        if 'scripts.test_governance_pytest' in command:
            return run(*command, **kwargs)
        if 'scripts/certify_p2_data_platform.py' in command:
            return json.dumps(dict(schema_version='p2-data-platform-certification-v2', repetitions=3,
                query_parity=True, pit_leakage_closed=True, data_api_epoch=2,
                migration_head='0019_p2_security_master', receipt_sha256='f'*64))
        return ''
    monkeypatch.setattr(producer, '_source_v2', current_source)
    monkeypatch.setattr(producer, '_run', selected_run)
    output = tmp_path/'qualification'/'p2-source-complete-v2.json'
    if drift_after_execution:
        with pytest.raises(producer.QualificationError, match='source changed'):
            producer.p2_source_v2(output, qualification=qualification)
        assert not output.exists()
        return
    producer.p2_source_v2(output, qualification=qualification)
    gate = validate_v2_gate_receipt(json.loads(output.read_bytes()), 'P2_SOURCE_COMPLETE')
    pit_raw = (output.parent/'p2-pit-adversarial-suite-receipt-v1.json').read_bytes()
    pit = PITAdversarialSuiteReceiptV1.model_validate_json(pit_raw)
    assert pit_raw == canonical_json_bytes(pit)
    evidence = next(item for item in gate['evidence'] if item['name'] == 'p2-pit-executed-suite')
    assert evidence['kind'] == 'DERIVED_RECEIPT' and evidence['sha256'] == pit.digest
    assert evidence['sha256'] != pit.report_ref.content_sha256
    store = LocalArtifactStore(output.parent/'p2-pit-artifacts')
    import hashlib
    from packages.data_contracts import ArtifactRefV1
    digest = hashlib.sha256(pit_raw).hexdigest()
    ref = ArtifactRefV1(content_sha256=digest, size_bytes=len(pit_raw), media_type='application/json', locator=digest+'.blob')
    assert validate_pit_suite_receipt(ref, source=source, store=store) == pit


@pytest.mark.parametrize('role', ['outer', 'suite', 'collection', 'execution'])
@pytest.mark.parametrize('fault', ['media', 'size', 'empty', 'locator'])
def test_pit_refs_rejected_before_read(tmp_path, role, fault):
    from packages.alpha_lifecycle.pit_suite import build_pit_suite_receipt, validate_pit_suite_receipt
    store, manifest_ref, source, report, qualification = suite_inputs(tmp_path)
    collection = {**report, 'collection_only':True, 'summary':{'collected':14},
        'tests':[{**row, 'outcome':'collected', 'phase':'collection'} for row in report['tests']]}
    collection_ref = store.put_bytes(canonical_json_bytes(collection), media_type='application/json')
    report_ref = store.put_bytes((json.dumps(report, indent=2, sort_keys=True)+'\n').encode(), media_type=PIT_EVIDENCE_MEDIA)
    receipt = build_pit_suite_receipt(source, manifest_ref, collection_ref, report_ref, qualification, store=store)
    refs = dict(suite=manifest_ref, collection=collection_ref, execution=report_ref)
    outer = store.put_bytes(canonical_json_bytes(receipt), media_type='application/json')
    target = outer if role == 'outer' else refs[role]
    changes = {'media':{'media_type':'text/plain'}, 'size':{'size_bytes':(65537 if role == 'outer' else 131073)},
        'empty':{'size_bytes':0}, 'locator':{'locator':'9'*64+'.blob'}}[fault]
    bad = target.model_copy(update=changes)
    reads = []
    class ObservedStore:
        def read_bytes(self, ref):
            reads.append(ref)
            return store.read_bytes(ref)
    observed = ObservedStore()
    if role == 'outer':
        with pytest.raises(ValueError):
            validate_pit_suite_receipt(bad, source=source, store=observed)
        assert reads == []
        return
    refs[role] = bad
    with pytest.raises(ValueError):
        build_pit_suite_receipt(source, refs['suite'], refs['collection'], refs['execution'], qualification, store=observed)
    assert reads == []
    # Nested metadata must also be checked when reached through a retained receipt.
    import hashlib
    forged = receipt.model_dump(mode='json', exclude={'digest'})
    forged[dict(suite='suite_manifest_ref', collection='collection_ref', execution='report_ref')[role]] = bad.model_dump(mode='json')
    forged['digest'] = hashlib.sha256(canonical_json_bytes(forged)).hexdigest()
    forged_ref = store.put_bytes(canonical_json_bytes(forged), media_type='application/json')
    with pytest.raises(ValueError):
        validate_pit_suite_receipt(forged_ref, source=source, store=observed)
    assert reads == [forged_ref]


def test_real_pit_producer_receipt_fits_worker_input_closure(tmp_path,monkeypatch):
    from scripts import qualify_pre_p3 as producer
    from services.job_worker.p3_output_validation import _closure
    from packages.alpha_lifecycle.pit_suite import validate_pit_suite_receipt
    store,_,source,_,qualification=suite_inputs(tmp_path)
    monkeypatch.setattr(producer,'_source_v2',lambda:source.model_dump(mode='json'))
    receipt=producer._execute_pit_suite(source.model_dump(mode='json'),qualification,store)
    ref=store.put_bytes(canonical_json_bytes(receipt),media_type='application/json')
    assert validate_pit_suite_receipt(ref,source=source,store=store)==receipt
    assert receipt.suite_manifest_ref.media_type==receipt.report_ref.media_type==PIT_EVIDENCE_MEDIA
    assert receipt.collection_ref.media_type==ref.media_type=='application/json'
    assert store.read_bytes(receipt.suite_manifest_ref)==SUITE.read_bytes()
    closed=_closure((ref,),store)
    assert {item.content_sha256 for item in closed.values()}=={
        ref.content_sha256,receipt.suite_manifest_ref.content_sha256,
        receipt.collection_ref.content_sha256,receipt.report_ref.content_sha256}

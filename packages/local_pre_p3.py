"""Local prerequisite readback; retained test records are not issuer authority."""
from __future__ import annotations

from datetime import timedelta
import hashlib
from importlib import import_module
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
from packages.alpha_lifecycle.contracts.authority import QualificationBinding, QualificationReceipt
from packages.alpha_lifecycle.contracts.base import DigestModel, SourceIdentity, StrictModel, Text
from packages.alpha_lifecycle.contracts.models import json_array
from packages.alpha_lifecycle.local_qualification import _read_evidence, validate_local_qualification
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime, canonical_json_bytes
from packages.runtime_release.config import _absolute, read_protected_canonical_json_current


LOCAL_PRE_P3_GATES = (
    'HWC_PORTABLE_QUALIFIED', 'P1_H_COMPLETE', 'P1_LTS_READY',
    'P2_SOURCE_COMPLETE', 'P2_RUNTIME_QUALIFIED', 'P2_QUALIFIED',
    'ALPHA_REGISTRY_FOUNDATION', 'P3_BASELINES_FROZEN',
    'P3_EVALUATION_PROTOCOL_FROZEN', 'PRE_P3_READY',
)

LOCAL_PRE_P3_PROFILE = Path('/etc/trading-agent/local-qualification/pre-p3.json')
POLICY_PATH = 'docs/implementation/p3/specs/p3-policy-set-v21.json'


class LocalPreP3Profile(StrictModel):
    schema_version: Literal['local-pre-p3-profile-v1']
    identity: LocalRunIdentity
    store_root: Text
    bindings: Annotated[tuple[QualificationBinding, ...], BeforeValidator(json_array), Field(min_length=10,max_length=10)]

    @model_validator(mode='after')
    def _gates(self):
        if tuple(binding.gate for binding in self.bindings) != LOCAL_PRE_P3_GATES:
            raise ValueError('local profile must bind the complete ordered prerequisite set')
        return self


def _validate_local_gate_proof(receipt: QualificationReceipt, *, root: Path, store) -> None:
    """Semantic proof readback; called only after local custody authentication."""
    gate, refs = receipt.binding.gate, receipt.evidence_refs
    if gate in ('P2_QUALIFIED','PRE_P3_READY'):
        return  # Exact edges and all authenticated children are checked by the DAG validator.
    if gate == 'HWC_PORTABLE_QUALIFIED':
        hwc = import_module('packages.hwc_status')
        hwc.validate_local_hwc_evidence(receipt,root=root,store=store)
        return
    proof = validate_local_test_evidence(refs[0], gate=gate, source=receipt.binding.source,
        identity=receipt.identity, store=store)
    if not receipt.started_at <= proof.started_at <= proof.completed_at <= receipt.completed_at:
        raise ValueError('local test execution is outside the qualified interval')
    if gate in ('P1_H_COMPLETE','P1_LTS_READY'):
        if len(refs) < 6:
            raise ValueError('local P1 requires complete native and retained external evidence')
        qualifier = import_module('scripts.qualify_p1_engine_lts')
        external = {name:_read_evidence(store,ref) for name,ref in zip(qualifier._EXTERNAL_SPECS,refs[2:5],strict=True)}
        qualifier.validate_complete_receipt(json.loads(_read_evidence(store,refs[1])),external=external,
            retained={ref.content_sha256:_read_evidence(store,ref) for ref in refs[5:]},
            source_identity=(receipt.binding.source.commit_sha,receipt.binding.source.tree_sha))
    elif gate == 'P2_SOURCE_COMPLETE':
        if len(refs) != 3:
            raise ValueError('local P2 source needs PIT and deterministic replay proof')
        from packages.alpha_lifecycle.pit_suite import validate_pit_suite_receipt, PITAdversarialSuiteReceiptV2
        pit = validate_pit_suite_receipt(refs[1],source=receipt.binding.source,store=store)
        if (not isinstance(pit,PITAdversarialSuiteReceiptV2) or pit.qualification.identity != receipt.identity
            or not receipt.started_at <= pit.qualification.completed_at_utc <= receipt.completed_at):
            raise ValueError('local P2 PIT issuer or execution interval differs')
        from packages.data_certification import P2CertificationReceiptV2
        replay = json.loads(_read_evidence(store,refs[2]))
        expected_fields = set(P2CertificationReceiptV2.__dataclass_fields__) | {'schema_version','receipt_sha256','repetitions'}
        if not isinstance(replay,dict) or set(replay) != expected_fields:
            raise ValueError('local P2 replay proof shape differs')
        certificate = P2CertificationReceiptV2(**{k:replay[k] for k in P2CertificationReceiptV2.__dataclass_fields__})
        if (replay['schema_version'] != 'p2-data-platform-certification-v2'
            or type(replay['repetitions']) is not int or replay['repetitions'] != 3
            or replay['receipt_sha256'] != certificate.receipt_sha256
            or certificate.pit_leakage_closed is not True or certificate.query_parity is not True
            or certificate.iceberg_enabled is not False or certificate.data_api_epoch != 2
            or certificate.migration_head != '0019_p2_security_master'):
            raise ValueError('local P2 replay did not satisfy the existing deterministic certification')
    elif gate == 'P2_RUNTIME_QUALIFIED':
        if len(refs) != 2:
            raise ValueError('local P2 runtime requires PostgreSQL binary evidence')
        binary = json.loads(_read_evidence(store,refs[1]))
        if (not isinstance(binary,dict) or set(binary) != {'schema_version','version','binary_sha256','source'}
            or binary['schema_version'] != 'local-postgres-identity-v1'
            or not isinstance(binary['version'],str) or not binary['version'].startswith('postgres (PostgreSQL) 16.')
            or binary['source'] != receipt.binding.source.model_dump(mode='json')
            or binary['binary_sha256'] != hashlib.sha256(Path('/usr/lib/postgresql/16/bin/postgres').read_bytes()).hexdigest()):
            raise ValueError('local P2 PostgreSQL source or binary identity differs')
    elif len(refs) != 1:
        raise ValueError('local foundation proof has unexpected evidence')


def validate_local_environment(root: Path, environment_ref, *, store) -> None:
    from packages.alpha_lifecycle.contracts.execution import EnvironmentIdentity
    from packages.alpha_lifecycle.sandbox_policy import SANDBOX_PROFILE_SHA256
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
    import sys
    import platform

    environment = EnvironmentIdentity.model_validate_json(_read_evidence(store,environment_ref))
    if (environment.python_version != '3.11' or sys.version_info[:2] != (3,11)
        or environment.root_lock_digest != hashlib.sha256((root/'uv.lock').read_bytes()).hexdigest()
        or environment.platform != 'linux-x86_64' or sys.platform != 'linux' or platform.machine() != 'x86_64'
        or environment.native_manifest_digest != P1_REAL_BACKTEST_POLICY.closure_sha256
        or environment.sandbox_policy_digest != SANDBOX_PROFILE_SHA256):
        raise ValueError('local qualification environment differs from current source and host')


def validate_local_phase_binding(root: Path, identity, binding, ready, *, store) -> None:
    """Link a campaign binding to freshly authenticated prerequisites without rebinding either."""
    from packages.alpha_lifecycle.contracts.execution import EnvironmentIdentity, InputSet
    from packages.alpha_lifecycle.replica_store import _read
    from packages.alpha_lifecycle.sandbox_policy import SANDBOX_PROFILE_SHA256, SANDBOX_PROFILE_V2_SHA256

    policy = (root/POLICY_PATH).read_bytes()
    if (binding.gate != 'P3_PHASE_EXIT' or ready.binding.gate != 'PRE_P3_READY'
        or identity != ready.identity or binding.source != ready.binding.source
        or ready.binding.policy_digest != hashlib.sha256(policy).hexdigest()
        or binding.policy_digest != hashlib.sha256(canonical_json_bytes(json.loads(policy))).hexdigest()):
        raise ValueError('local phase and prerequisite source or policy differ')
    inputs = _read(store, binding.input_ref, InputSet)
    if (inputs.source != binding.source or inputs.policy_digest != binding.policy_digest
        or inputs.environment_ref != binding.environment_ref):
        raise ValueError('local phase input set differs from its binding')
    environment = _read(store, binding.environment_ref, EnvironmentIdentity)
    if environment.sandbox_policy_digest not in (SANDBOX_PROFILE_SHA256, SANDBOX_PROFILE_V2_SHA256):
        raise ValueError('local phase sandbox is not an accepted LOCAL transport')
    # LOCAL v2 changes only the sandbox transport; authenticate every other field via the v1 reference.
    original = environment.model_dump(mode='json', exclude={'digest'})
    original['sandbox_policy_digest'] = SANDBOX_PROFILE_SHA256
    original['digest'] = hashlib.sha256(canonical_json_bytes(original)).hexdigest()
    raw = canonical_json_bytes(original)
    digest = hashlib.sha256(raw).hexdigest()
    expected = ArtifactRefV1(content_sha256=digest, locator=digest+'.blob',
        size_bytes=len(raw), media_type='application/json')
    if expected != ready.binding.environment_ref:
        raise ValueError('local phase environment differs from its qualified predecessor')


def read_local_pre_p3(root: Path) -> dict[str, QualificationReceipt]:
    """Fresh admission from fixed protected expectations, never a generated status."""
    from packages.pre_p3_provenance import _git, canonical_source_identity
    from packages.alpha_lifecycle.local_qualification import ProtectedQualificationStore
    from packages.local_authority import AuthorityStore, require_no_bypassing_writer
    from packages.runtime_release.config import read_protected_file_current
    if _git(root,'status','--porcelain'):
        raise ValueError('local qualification requires a clean source checkout')
    source = SourceIdentity.model_validate(canonical_source_identity(root))
    document,digest = read_protected_canonical_json_current(LOCAL_PRE_P3_PROFILE)
    profile = LocalPreP3Profile.model_validate_json(canonical_json_bytes(document))
    policy = hashlib.sha256((root/POLICY_PATH).read_bytes()).hexdigest()
    if any(binding.source != source or binding.policy_digest != policy for binding in profile.bindings):
        raise ValueError('local protected prerequisite source or policy differs')
    # Every LOCAL consumer reaches the issuer store through here, so this is where a write
    # that skipped the authority protocol has to be refused. Detection rather than
    # prevention is the honest mechanism: the helpers that install approvals are root
    # processes outside this repository and cannot be made to take the lock by editing a
    # consumer, but a store whose bytes moved without its generation moving with them is
    # observable, and there is no unlocked revalidation to fall back to.
    require_no_bypassing_writer(AuthorityStore(profile.identity.issuer_identity),
        LOCAL_PRE_P3_GATES, read=read_protected_file_current)
    store = ProtectedQualificationStore(_absolute(profile.store_root))
    validate_local_environment(root,profile.bindings[0].environment_ref,store=store)
    receipts = []
    for binding in profile.bindings:
        receipt = validate_local_qualification(profile.identity,binding,store=store)
        _validate_local_gate_proof(receipt,root=root,store=store)
        receipts.append(receipt)
    gates = validate_local_prerequisite_chain(receipts)
    for binding in profile.bindings:
        if validate_local_qualification(profile.identity,binding,store=store) != gates[binding.gate]:
            raise ValueError('local prerequisite approval changed during admission')
    if (read_protected_canonical_json_current(LOCAL_PRE_P3_PROFILE)[1] != digest
        or _git(root,'status','--porcelain') or SourceIdentity.model_validate(canonical_source_identity(root)) != source):
        raise ValueError('local source or protected expectations changed during admission')
    return gates


def validate_local_prerequisite_chain(receipts) -> dict[str, QualificationReceipt]:
    """Check dependencies after custody and each semantic proof were authenticated.

    This pure structural check alone never grants source readiness.
    """
    values = tuple(QualificationReceipt.model_validate(r) for r in receipts)
    gates = {receipt.binding.gate:receipt for receipt in values}
    if len(values) != len(LOCAL_PRE_P3_GATES) or set(gates) != set(LOCAL_PRE_P3_GATES):
        raise ValueError('local prerequisite set is partial or repeated')
    first = values[0]
    if not isinstance(first.identity, LocalRunIdentity):
        raise ValueError('local prerequisites require a local issuer')
    for receipt in values:
        if (receipt.identity != first.identity or receipt.binding.source != first.binding.source
            or receipt.binding.policy_digest != first.binding.policy_digest
            or receipt.binding.input_ref != first.binding.input_ref
            or receipt.binding.environment_ref != first.binding.environment_ref):
            raise ValueError('local prerequisites mix source, issuer, policy, input or environment')
    for gate, dependencies in (
        ('P2_QUALIFIED', ('P2_SOURCE_COMPLETE','P2_RUNTIME_QUALIFIED')),
        ('PRE_P3_READY', LOCAL_PRE_P3_GATES[:-1]),
    ):
        expected = []
        for dependency in dependencies:
            raw = canonical_json_bytes(gates[dependency])
            digest = hashlib.sha256(raw).hexdigest()
            expected.append(ArtifactRefV1(content_sha256=digest,size_bytes=len(raw),
                media_type='application/json',locator=digest+'.blob'))
            if gates[dependency].completed_at > gates[gate].completed_at:
                raise ValueError('local prerequisite aggregate predates its dependency')
        if tuple(expected) != gates[gate].evidence_refs:
            raise ValueError('local prerequisite aggregate does not bind exact retained dependencies')
    h, lts = gates['P1_H_COMPLETE'], gates['P1_LTS_READY']
    if len(h.evidence_refs) < 2 or len(lts.evidence_refs) < 2 or h.evidence_refs[1] != lts.evidence_refs[1]:
        raise ValueError('local P1 H and LTS evidence do not share the same complete native proof')
    return gates


def local_gate_selectors(gate: str) -> tuple[str, ...]:
    from scripts.qualify_p1_engine_lts import _LOCAL_GATES
    if gate in ('P1_H_COMPLETE', 'P1_LTS_READY'):
        return tuple(dict.fromkeys(node for group in _LOCAL_GATES.values() for node in group))
    if gate == 'P2_SOURCE_COMPLETE':
        root = Path(__file__).resolve().parents[1]
        return tuple(sorted(path.relative_to(root).as_posix()
            for directory in ('tests/data_platform','tests/security_master')
            for path in (root/directory).rglob('test_*.py')
            if path.name != 'test_postgres_runtime.py'))
    if gate == 'P2_RUNTIME_QUALIFIED':
        return ('tests/security_master/test_postgres_runtime.py',)
    if gate in ('ALPHA_REGISTRY_FOUNDATION', 'P3_BASELINES_FROZEN', 'P3_EVALUATION_PROTOCOL_FROZEN'):
        return ('tests/alpha_lifecycle',)
    raise ValueError('local prerequisite has no executable test suite')


class LocalTestEvidence(DigestModel):
    schema_version: Literal['local-pre-p3-test-evidence-v1']
    gate: Text
    source: SourceIdentity
    identity: LocalRunIdentity
    collection_ref: ArtifactRefV1
    report_ref: ArtifactRefV1
    selectors: Annotated[tuple[Text, ...], BeforeValidator(json_array), Field(min_length=1, max_length=32)]
    executed_node_ids: Annotated[tuple[Text, ...], BeforeValidator(json_array), Field(min_length=1, max_length=4096)]
    started_at: CanonicalUtcDateTime
    completed_at: CanonicalUtcDateTime

    @model_validator(mode='after')
    def _bounds(self):
        if not self.started_at <= self.completed_at <= self.started_at + timedelta(hours=24):
            raise ValueError('local test execution validity differs')
        if self.selectors != local_gate_selectors(self.gate):
            raise ValueError('local test selectors differ from the source-owned suite')
        return self


def _observed(raw: bytes, *, collection: bool, selectors: tuple[str, ...]) -> tuple[str, ...]:
    report = json.loads(raw)
    canonical = canonical_json_bytes(report) if collection else (json.dumps(report, indent=2, sort_keys=True)+'\n').encode()
    if (raw != canonical or not isinstance(report, dict)
        or set(report) != {'schema_version','component','collection_only','pytest_exit_status','summary','tests'}
        or type(report['schema_version']) is not int or report['schema_version'] != 1
        or report['component'] != 'root' or report['collection_only'] is not collection
        or type(report['pytest_exit_status']) is not int or report['pytest_exit_status'] != 0
        or not isinstance(report['tests'], list) or not 0 < len(report['tests']) <= 4096):
        raise ValueError('local test observation envelope is invalid')
    rows = report['tests']
    if any(not isinstance(row, dict) or not isinstance(row.get('test_node_id'), str) for row in rows):
        raise ValueError('local test observation inventory is invalid')
    nodes = tuple(row['test_node_id'] for row in rows)
    selected = tuple(s for s in selectors if not s.startswith('--'))
    excluded = tuple(s.removeprefix('--ignore=') for s in selectors if s.startswith('--ignore='))
    def matches(node, path):
        return node.startswith(path+'::') or node.startswith(path+'/')
    if (nodes != tuple(sorted(set(nodes)))
        or any(not any(matches(node,s) for s in selected) or any(matches(node,s) for s in excluded) for node in nodes)
        or any(not any(matches(node,s) for node in nodes) for s in selected)):
        raise ValueError('local test inventory differs from the selected suite')
    outcome, phase = ('collected','collection') if collection else ('passed','call')
    if (rows != [dict(test_node_id=n,component='root',outcome=outcome,reason='',phase=phase) for n in nodes]
        or report['summary'] != {outcome:len(nodes)}
        or any(type(count) is not int for count in report['summary'].values())):
        raise ValueError('local test suite did not execute every collected call successfully')
    return nodes


def build_local_test_evidence(*, gate, source, identity, collection_ref, report_ref,
    started_at, completed_at, store) -> LocalTestEvidence:
    selectors = local_gate_selectors(gate)
    if collection_ref.media_type != 'application/json' or report_ref.media_type != 'application/json':
        raise ValueError('local test evidence media differs')
    collected = _observed(_read_evidence(store, collection_ref), collection=True, selectors=selectors)
    executed = _observed(_read_evidence(store, report_ref), collection=False, selectors=selectors)
    if collected != executed:
        raise ValueError('local test execution differs from complete collection')
    value = dict(schema_version='local-pre-p3-test-evidence-v1', gate=gate, source=source,
        identity=identity, collection_ref=collection_ref, report_ref=report_ref, selectors=selectors,
        executed_node_ids=executed, started_at=started_at, completed_at=completed_at)
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return LocalTestEvidence.model_validate_json(canonical_json_bytes(value))


def validate_local_test_evidence(ref, *, gate, source, identity, store) -> LocalTestEvidence:
    raw = _read_evidence(store, ref)
    proof = LocalTestEvidence.model_validate_json(raw)
    if (canonical_json_bytes(proof) != raw or proof.source != source
        or proof.identity != identity or proof.gate != gate):
        raise ValueError('local test evidence binding differs')
    rebuilt = build_local_test_evidence(gate=gate, source=source, identity=identity,
        collection_ref=proof.collection_ref, report_ref=proof.report_ref,
        started_at=proof.model_dump(mode='json')['started_at'],
        completed_at=proof.model_dump(mode='json')['completed_at'], store=store)
    if proof != rebuilt:
        raise ValueError('local test evidence differs from retained observations')
    return proof

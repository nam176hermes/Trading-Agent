"""Assemble retained LOCAL campaign proposals; none of these functions installs authority."""
from datetime import date, datetime, timedelta
from decimal import localcontext
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from packages.alpha_lifecycle.authority import validate_operation_review, WORKFLOW_OPERATIONS
from packages.alpha_lifecycle.baseline_campaign import validate_research_inputs
from packages.alpha_lifecycle.contracts.authority import INTEGRATION_RECEIPT, LocalRunAuthorization
from packages.alpha_lifecycle.contracts.data import DailyBar, DatasetEvidence
from packages.alpha_lifecycle.contracts.execution import EnvironmentIdentity, InputSet
from packages.alpha_lifecycle.contracts.results import RegimeThreshold
from packages.alpha_lifecycle.data_view import to_daily_close
from packages.alpha_lifecycle.folds import build_fold_manifest
from packages.alpha_lifecycle.operation_input import FAMILY_IDS, P3OperationInput
from packages.alpha_lifecycle.pit_evidence import build_pit_proof
from packages.alpha_lifecycle.pit_suite import validate_pit_suite_receipt
from packages.alpha_lifecycle.regimes import _volatility, nearest_rank_threshold
from packages.alpha_lifecycle.replica_store import _read
from packages.alpha_lifecycle.research_custody import _seal
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def assemble_input_set(*, source, dataset_ref, commitment_ref, pit_suite_ref,
                       environment_ref, integration_receipt_ref, store):
    """Use frozen policy, training-only regimes and existing graph validation."""
    from scripts.generate_p3_specs import _candidate_specs
    policy = json.loads((Path(__file__).resolve().parents[2] /
        'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes())
    policy_digest = hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
    dataset = _read(store, dataset_ref, DatasetEvidence)
    if (dataset.segment != 'RESEARCH' or dataset.usable_rows != 2800
        or str(dataset.date_range.start) != '2018-01-01' or str(dataset.date_range.end) != '2025-08-31'):
        raise ValueError('research requires the frozen 2800-day window')
    _read(store, environment_ref, EnvironmentIdentity)
    integration = _read(store, integration_receipt_ref, INTEGRATION_RECEIPT)
    if integration.source != source:
        raise ValueError('integration receipt source differs')
    validate_pit_suite_receipt(pit_suite_ref, source=source, store=store)
    folds = build_fold_manifest(dataset, policy, store)
    training_start, training_end = map(date.fromisoformat, policy['folds']['training'])
    bars = tuple(_read(store, ref, DailyBar) for ref in dataset.row_refs)
    if len(bars) != 2800 or any(bar.date != dataset.date_range.start + timedelta(days=index)
                               for index, bar in enumerate(bars)):
        raise ValueError('research rows must follow the exact chronological daily window')
    training = tuple(to_daily_close(bar) for bar in bars if training_start <= bar.date <= training_end)
    if len(training) != (training_end-training_start).days+1:
        raise ValueError('training window is incomplete')
    closes = tuple(row.close for row in training)
    with localcontext() as context:
        context.prec = 50
        volatilities = tuple(sorted(_volatility(closes, i) for i in range(63, len(closes))))
        threshold = nearest_rank_threshold(volatilities)
    def text(value):
        return format(value, 'f')
    regime = _seal(dict(schema_version='p3-regime-threshold-v1', policy_digest=policy_digest,
        training_dataset_ref=dataset_ref, training_range=dict(start=str(training_start), end=str(training_end)),
        sample_count=len(volatilities), threshold=text(threshold),
        ordered_volatility_digest=hashlib.sha256(canonical_json_bytes([text(v) for v in volatilities])).hexdigest()),
        RegimeThreshold)
    pit = build_pit_proof(dataset, folds, commitment_ref, pit_suite_ref, store)
    def retain(value):
        return store.put_bytes(canonical_json_bytes(value), media_type='application/json')
    inputs = _seal(dict(schema_version='p3-input-set-v1', source=source,
        epoch_id=policy['campaign']['epoch_id'], policy_digest=policy_digest,
        family_digest=hashlib.sha256(canonical_json_bytes(_candidate_specs(policy))).hexdigest(),
        dataset_evidence_ref=dataset_ref, fold_manifest_ref=retain(folds), pit_proof_ref=retain(pit),
        environment_ref=environment_ref, cost_model=policy['costs']['base'],
        regime_threshold_ref=retain(regime), integration_receipt_ref=integration_receipt_ref), InputSet)
    result = retain(inputs)
    validate_research_inputs(result, store)
    return result


def propose_operation(workflow_operation, input_set_ref, body, *, store, primary_alpha_id=None):
    """Prepare an exact intent only from already retained inputs; never enqueue."""
    operation = WORKFLOW_OPERATIONS.get(workflow_operation)
    if operation is None or workflow_operation == 'p3-integration-fixture-v1':
        raise ValueError('unsupported official campaign operation')
    _read(store, input_set_ref, InputSet)
    if workflow_operation == 'p3-baselines-v1':
        ids = ()
    elif workflow_operation in {'p3-register-family-v1', 'p3-select-primary-v1'}:
        ids = FAMILY_IDS
    elif workflow_operation.startswith('p3-oos-a'):
        ids = (FAMILY_IDS[int(workflow_operation[len('p3-oos-a')])],)
    else:
        ids = (primary_alpha_id,)
    intent = _seal(dict(schema_version='p3-operation-input-v1', workflow_operation=workflow_operation,
        operation=operation, input_set_ref=input_set_ref, allowed_alpha_ids=ids, body=body), P3OperationInput)
    for value in intent.body.__dict__.values():
        for ref in value if isinstance(value, tuple) else (value,):
            if isinstance(ref, ArtifactRefV1):
                store.read_bytes(ref)
    return intent


def prepare_authorization(intent, *, identity, source, review_ref, store, now: datetime, expires_at: datetime):
    """Produce reviewed bytes for the protected issuer, not an active approval."""
    intent = P3OperationInput.model_validate_json(canonical_json_bytes(intent))
    inputs = _read(store, intent.input_set_ref, InputSet)
    if inputs.source != source:
        raise ValueError('operation InputSet source differs')
    authorization = _seal(dict(schema_version='p3-local-run-authorization-v1', identity=identity,
        input_set_ref=intent.input_set_ref, review_ref=review_ref, operation=intent.operation,
        allowed_alpha_ids=intent.allowed_alpha_ids, issued_at=now.isoformat().replace('+00:00', 'Z'),
        expires_at=expires_at.isoformat().replace('+00:00', 'Z'),
        nonce=str(uuid4()), authority=dict(broker=False, live=False, network=False, production=False)), LocalRunAuthorization)
    review = validate_operation_review(authorization, source, intent, store, now=now)
    store.read_bytes(review.evidence_ref)
    return authorization

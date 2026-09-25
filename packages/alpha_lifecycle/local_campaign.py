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


# Bar fields each family reads in packages/alpha_lifecycle/candidates.py.
_FAMILY_FEATURES = {'DONCHIAN': ('close', 'high', 'low'), 'DUAL_SMA': ('close',),
                    'ZSCORE': ('close',), 'TSMOM': ('close',)}


def registration_body(input_set_ref, baseline_selection_ref, *, store):
    """Derive the REGISTER_FAMILY body from the frozen specs and the committed baseline selection.

    Every record field comes from the policy, the InputSet graph, the selection or the candidate
    code; nothing is chosen here. The job revalidates the family before publishing it.
    """
    from datetime import time, timezone
    from packages.alpha_lifecycle.baseline_campaign import validate_baseline_selection
    from packages.alpha_lifecycle.contracts.policy import CandidateSpec
    from packages.alpha_lifecycle.registry import AlphaLifecycleStatus, AlphaRecordV1, QualificationDecision
    from scripts.generate_p3_specs import POLICY_SOURCE, _candidate_specs
    selection = validate_baseline_selection(baseline_selection_ref, input_set_ref, store)
    inputs, folds, _, threshold = validate_research_inputs(input_set_ref, store)
    dataset = _read(store, inputs.dataset_evidence_ref, DatasetEvidence)
    specs = tuple(CandidateSpec.model_validate(spec) for spec in _candidate_specs(json.loads(POLICY_SOURCE.read_bytes())))
    spec_refs = tuple(store.put_bytes(canonical_json_bytes(spec), media_type='application/json') for spec in specs)
    def at(day, clock):
        return datetime.combine(day, clock, timezone.utc)
    records = tuple(AlphaRecordV1(
        alpha_id=spec.alpha_id, version=spec.version, source_sha=inputs.source.commit_sha,
        implementation_identity='packages.alpha_lifecycle.candidates:run_candidate',
        dataset_snapshot_sha256=dataset.snapshot_ref.content_sha256, feature_set=_FAMILY_FEATURES[spec.family],
        parameter_set_sha256=spec.digest,
        training_start_at=at(threshold.training_range.start, time.min), training_end_at=at(threshold.training_range.end, time.max),
        validation_start_at=at(threshold.training_range.end + timedelta(days=1), time.min),
        validation_end_at=at(folds.folds[0].decision_start - timedelta(days=1), time.max),
        oos_start_at=at(folds.folds[0].return_end_range.start, time.min),
        oos_end_at=at(folds.folds[-1].return_end_range.end, time.max),
        universe=('BTCUSDT.BINANCE',), cost_model_sha256=selection.selected_result.cost_model_sha256,
        baseline_id=selection.selected_id, baseline_version=selection.selected_result.baseline_version,
        metrics_sha256=None, robustness_sha256=None, qualification_decision=QualificationDecision.NOT_EVALUATED,
        qualification_reason='preregistered', artifact_digests=(ref.content_sha256,), lineage=(inputs.epoch_id,),
        superseded_version=None, lifecycle_status=AlphaLifecycleStatus.IDEA)
        for spec, ref in zip(specs, spec_refs, strict=True))
    record_refs = tuple(store.put_bytes(canonical_json_bytes(record), media_type='application/json') for record in records)
    return dict(baseline_selection_ref=baseline_selection_ref, candidate_spec_refs=spec_refs, candidate_record_refs=record_refs)


def oos_body(workflow_operation, publication_receipt_ref, *, store):
    """Derive one candidate's OOS body from the committed REGISTER publication receipt.

    The receipt comes from `recover_publication_receipt` over committed SQL custody.
    """
    from packages.alpha_lifecycle.contracts.execution import EvaluationManifest
    from packages.alpha_lifecycle.contracts.lifecycle import PublicationReceipt
    from packages.alpha_lifecycle.contracts.policy import CandidateSpec
    from packages.alpha_lifecycle.publication import build_registration_proof, validate_evaluation_registration
    from scripts.generate_p3_specs import POLICY_SOURCE, _candidate_specs
    if not workflow_operation.startswith('p3-oos-a') or WORKFLOW_OPERATIONS.get(workflow_operation) != 'OOS':
        raise ValueError('OOS body requires a candidate OOS operation')
    index = int(workflow_operation[len('p3-oos-a')])
    proof = build_registration_proof(_read(store, publication_receipt_ref, PublicationReceipt), store=store)
    spec = CandidateSpec.model_validate(_candidate_specs(json.loads(POLICY_SOURCE.read_bytes()))[index])
    if spec.alpha_id != FAMILY_IDS[index]:
        raise ValueError('frozen specification order differs from the family')
    def retain(value):
        return store.put_bytes(canonical_json_bytes(value), media_type='application/json')
    manifest = _seal(dict(schema_version='p3-evaluation-manifest-v1', mode='OOS', input_set_ref=proof.input_set_ref,
        candidate_spec_ref=retain(spec), baseline_selection_ref=proof.baseline_selection_ref,
        candidate_head_ref=proof.candidate_head_refs[index], registration_proof_ref=retain(proof),
        holdout_primary_ref=None), EvaluationManifest)
    validate_evaluation_registration(manifest, store=store)
    return dict(evaluation_manifest_ref=retain(manifest))


def family_disclosure(input_set_ref, oos_receipt_refs, *, store):
    """Disclose all four committed OOS decisions and every frozen trial path, awaiting its review.

    Receipts come from `recover_publication_receipt` for the four OOS jobs, in family order.
    `review_ref` is a placeholder; a reviewer approves `family_disclosure_digest` of this value.
    """
    from packages.alpha_lifecycle.contracts.authority import FamilyReview
    from packages.alpha_lifecycle.contracts.lifecycle import PublicationReceipt, PublicationRequest
    from packages.alpha_lifecycle.contracts.results import ReplayProof, TrialOutcome
    from packages.alpha_lifecycle.primary_selection import validate_candidate_closure
    from packages.alpha_lifecycle.publication import build_closure_report
    from packages.alpha_lifecycle.trials import deterministic_trial_keys
    def retain(value):
        return store.put_bytes(canonical_json_bytes(value), media_type='application/json')
    inputs = _read(store, input_set_ref, InputSet)
    reports, outcomes = [], []
    for alpha_id, receipt_ref in zip(FAMILY_IDS, oos_receipt_refs, strict=True):
        receipt = _read(store, receipt_ref, PublicationReceipt)
        request = _read(store, receipt.request_ref, PublicationRequest)
        closure_ref = retain(build_closure_report(request, request.evidence_ref, receipt, store=store))
        bundle, _, _ = validate_candidate_closure(closure_ref, input_set_ref=input_set_ref, alpha_id=alpha_id, store=store)
        proof = _read(store, bundle.replay_proof_ref, ReplayProof)
        reports.append(closure_ref)
        outcomes.extend(retain(_seal(dict(schema_version='p3-trial-outcome-v1', trial_key=key, status='COMPLETED',
            result_ref=bundle.evaluation_ref, execution_receipt_refs=proof.receipt_refs), TrialOutcome))
            for key in deterministic_trial_keys(inputs.epoch_id, alpha_id))
    return _seal(dict(schema_version='p3-family-review-v1', input_set_ref=input_set_ref, candidate_report_refs=reports,
        trial_outcome_refs=outcomes, review_ref=input_set_ref, complete_disclosure=True), FamilyReview)


def holdout_body(input_set_ref, primary_selection_ref, *, custody_record_ref, holdout_dataset_ref, buffer_ref, store):
    """Derive the HOLDOUT body from the committed primary selection, the custody proposal and the policy.

    Reads only metadata: holdout and buffer rows, snapshots, ciphertext and attestation stay closed.
    The holdout PIT proof reuses the research no-future suite and names the custody record, which carries
    the holdout commitment, as its revision proof.
    """
    from packages.alpha_lifecycle.contracts.authority import FamilyReview, PrimarySelection
    from packages.alpha_lifecycle.contracts.data import PITProof
    from packages.alpha_lifecycle.contracts.execution import EvaluationManifest, InstrumentSpec
    from packages.alpha_lifecycle.contracts.lifecycle import CampaignClosureReport
    from packages.alpha_lifecycle.contracts.results import EvaluationResult, QualificationBundle
    from packages.alpha_lifecycle.executable_reference import ResearchInstrument
    from packages.alpha_lifecycle.folds import build_holdout_fold_manifest
    from scripts.generate_p3_specs import POLICY_SOURCE
    def retain(value):
        return store.put_bytes(canonical_json_bytes(value), media_type='application/json')
    inputs = _read(store, input_set_ref, InputSet)
    primary = _read(store, primary_selection_ref, PrimarySelection)
    if primary.outcome != 'SELECTED' or primary.primary_alpha_id is None:
        raise ValueError('holdout requires a selected primary; NONE_QUALIFIED closes the campaign')
    family = _read(store, primary.family_review_ref, FamilyReview)
    closure = _read(store, family.candidate_report_refs[FAMILY_IDS.index(primary.primary_alpha_id)], CampaignClosureReport)
    if closure.qualification_ref is None:
        raise ValueError('holdout primary has no retained qualification')
    evaluation = _read(store, _read(store, closure.qualification_ref, QualificationBundle).evaluation_ref, EvaluationResult)
    selected = _read(store, evaluation.manifest_ref, EvaluationManifest)
    context = _read(store, inputs.dataset_evidence_ref, DatasetEvidence)
    holdout = _read(store, holdout_dataset_ref, DatasetEvidence)
    folds = build_holdout_fold_manifest(context, holdout, policy_digest=inputs.policy_digest)
    research_pit = _read(store, inputs.pit_proof_ref, PITProof)
    pit = build_pit_proof(holdout, folds, custody_record_ref, research_pit.no_future_suite_ref, store)
    holdout_inputs = _seal({**inputs.model_dump(mode='json', exclude={'digest'}), 'dataset_evidence_ref': holdout_dataset_ref,
        'fold_manifest_ref': retain(folds), 'pit_proof_ref': retain(pit)}, InputSet)
    rules = json.loads(POLICY_SOURCE.read_bytes())['parity']['research_instrument_assumptions']
    increments = {key: rules[key] for key in ('price_increment', 'size_increment', 'quote_quantum', 'minimum_notional')}
    instrument = _seal(dict(schema_version='p3-research-instrument-v1', source=inputs.source, policy_digest=inputs.policy_digest,
        instrument='BTCUSDT.BINANCE', classification=rules['classification'], **increments), ResearchInstrument)
    spec = _seal(dict(schema_version='p3-instrument-spec-v1', instrument='BTCUSDT.BINANCE', security_master_ref=retain(instrument),
        base_currency='BTC', quote_currency='USDT', historical_rule_claim='SOURCE_BOUND_SIMULATION_NOT_HISTORICAL_EXCHANGE_RULES',
        **increments), InstrumentSpec)
    return dict(primary_selection_ref=primary_selection_ref, candidate_spec_ref=selected.candidate_spec_ref,
        registration_proof_ref=selected.registration_proof_ref, custody_record_ref=custody_record_ref,
        holdout_input_set_ref=retain(holdout_inputs), holdout_dataset_ref=holdout_dataset_ref,
        context_dataset_ref=inputs.dataset_evidence_ref, buffer_ref=buffer_ref, environment_ref=inputs.environment_ref,
        instrument_spec_ref=retain(spec), policy_digest=inputs.policy_digest)


def family_review_body(disclosure, review_ref, *, source, store):
    """Bind the independent ReviewApproval of exactly this disclosure; select-primary then recomputes."""
    from packages.alpha_lifecycle.contracts.authority import FamilyReview
    from packages.alpha_lifecycle.primary_selection import _validate_disclosure, validate_family_review_approval
    review = _seal({**disclosure.model_dump(mode='json', exclude={'digest'}), 'review_ref': review_ref}, FamilyReview)
    validate_family_review_approval(review, source, store)
    from packages.alpha_lifecycle.primary_selection import validate_candidate_closure
    bundles = [validate_candidate_closure(ref, input_set_ref=review.input_set_ref, alpha_id=alpha, store=store)[0]
               for alpha, ref in zip(FAMILY_IDS, review.candidate_report_refs, strict=True)]
    _validate_disclosure(review, bundles, _read(store, review.input_set_ref, InputSet), store)
    return dict(family_review_ref=store.put_bytes(canonical_json_bytes(review), media_type='application/json'))


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

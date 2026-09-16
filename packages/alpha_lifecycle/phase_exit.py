"""Recompute the fixed exit decision; SQL and protected launch authority are external."""
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import hashlib

from packages.alpha_lifecycle.contracts.models import SourceIdentity
from packages.alpha_lifecycle.contracts.results import ExitCheck, ExitResult, HoldoutEvaluationResult, ExecutableResult
from packages.alpha_lifecycle.operation_input import P3OperationInput, PhaseExitInput
from packages.alpha_lifecycle.replica_store import ArtifactStore, ReadbackStore, ReplicaArtifactStore, _read
from packages.alpha_lifecycle.holdout_view import HoldoutCalculationView
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def _economic_checks(evaluation: HoldoutEvaluationResult, primary: ExecutableResult,
    baseline: ExecutableResult,
) -> tuple[ExitCheck, ...]:
    # Exact accepted holdout policy; the policy-file regression prevents drift.
    with localcontext(prec=50, rounding=ROUND_HALF_EVEN):
        base = evaluation.primary_base.aggregate_metrics
        values = (
            ('RETURN', base.total_return > 0),
            ('EXCESS', base.total_return-evaluation.baseline_base.aggregate_metrics.total_return >= Decimal('.02')),
            ('DOUBLE_COST', evaluation.primary_double_cost.aggregate_metrics.total_return > 0),
            ('DELAY', evaluation.primary_delayed.aggregate_metrics.total_return > 0),
            ('MAX_DRAWDOWN', base.max_drawdown <= Decimal('.25')),
            ('CAPACITY', Decimal(evaluation.capacity.median) <= Decimal('.02')
                and Decimal(evaluation.capacity.peak) <= Decimal('.10')),
            ('NATIVE_RETURN', Decimal(primary.net_return) > 0),
            ('NATIVE_EXCESS', Decimal(primary.net_return)-Decimal(baseline.net_return) >= Decimal('.02')),
            ('NATIVE_DRAWDOWN', Decimal(primary.max_drawdown) <= Decimal('.25')),
        )
    return tuple(ExitCheck.model_validate(dict(check_id=key, passed=passed, code='E_'+key))
        for key, passed in values)


def evaluate_phase_exit(intent: P3OperationInput, *, expected_source: SourceIdentity,
    store: ArtifactStore, holdout_view: HoldoutCalculationView,
    native_parent_proof_ref: ArtifactRefV1 | None = None,
) -> ExitResult:
    """Require retained recomputation, including access to the already released view.

    This function cannot disclose data, authenticate a launch, or authorize SQL.
    Missing/invalid provenance raises instead of manufacturing a success receipt.
    Economic failures preserve FAIL; a native comparison failure keeps exit HELD.
    """
    from packages.alpha_lifecycle.baseline_campaign import validate_baseline_selection
    from packages.alpha_lifecycle.contracts.authority import PrimarySelection, FamilyReview, HoldoutRequest
    from packages.alpha_lifecycle.contracts.execution import InputSet, HoldoutManifest, InstrumentSpec, EnvironmentIdentity
    from packages.alpha_lifecycle.contracts.lifecycle import CampaignClosureReport, RegistrationProof
    from packages.alpha_lifecycle.contracts.results import QualificationBundle, ReplayProof, ReplayReceipt, ParityResult
    from packages.alpha_lifecycle.evaluation import evaluate_holdout
    from packages.alpha_lifecycle.executable_reference import run_executable_reference, run_selected_baseline_reference
    from packages.alpha_lifecycle.lifecycle import read_registry_event
    from packages.alpha_lifecycle.operation_input import FAMILY_IDS
    from packages.alpha_lifecycle.parity import validate_parity_pair
    from packages.alpha_lifecycle.pit_evidence import _ReadBudget, _reference
    from packages.alpha_lifecycle.primary_selection import select_primary
    from packages.alpha_lifecycle.registry import AlphaLifecycleStatus
    from packages.alpha_lifecycle.replay import validate_replay_proof

    intent = P3OperationInput.model_validate(intent)
    expected_source = SourceIdentity.model_validate(expected_source)
    if type(holdout_view) is not HoldoutCalculationView:
        raise ValueError('HELD E_IDENTITY: phase exit requires the released calculation view')
    if not isinstance(intent.body, PhaseExitInput):
        raise ValueError('phase exit requires its exact operation intent')
    body = intent.body
    reader = ReadbackStore(_ReadBudget(store), store)
    _reference(intent.input_set_ref, 65536)
    result_refs: dict[str, ArtifactRefV1] = dict(primary_selection_ref=body.primary_selection_ref,
        holdout_request_ref=body.holdout_request_ref, holdout_evaluation_ref=body.holdout_evaluation_ref,
        holdout_replay_ref=body.holdout_replay_ref, executable_ref=body.executable_ref,
        baseline_executable_ref=body.baseline_executable_ref, parity_ref=body.parity_ref)
    for name, ref in result_refs.items():
        _reference(ref, 67108864 if name == 'holdout_evaluation_ref' else 65536)
    for ref in (body.primary_qualification_ref, body.baseline_selection_ref, body.current_primary_head_ref):
        _reference(ref, 65536)
    inputs = _read(reader, intent.input_set_ref, InputSet)
    selection = _read(reader, body.primary_selection_ref, PrimarySelection)
    request = _read(reader, body.holdout_request_ref, HoldoutRequest)
    evaluation = _read(reader, body.holdout_evaluation_ref, HoldoutEvaluationResult)
    _reference(evaluation.manifest_ref, 65536)
    manifest = _read(reader, evaluation.manifest_ref, HoldoutManifest)
    _reference(request.holdout_input_set_ref, 65536)
    holdout_inputs = _read(reader, request.holdout_input_set_ref, InputSet)
    if (inputs.source != expected_source or request.source != expected_source or manifest.source != expected_source
        or request.primary_selection_ref != body.primary_selection_ref
        or manifest.primary_selection_ref != body.primary_selection_ref
        or not inputs.policy_digest == request.policy_digest == manifest.policy_digest == selection.selection_policy_digest
        or manifest.environment_ref != inputs.environment_ref
        or request.logical_trial_id != 'p3-holdout-primary-v1'
        or holdout_inputs.dataset_evidence_ref != manifest.holdout_dataset_ref
        or any(getattr(holdout_inputs, key) != getattr(inputs, key) for key in
            ('source', 'epoch_id', 'family_digest', 'policy_digest', 'environment_ref', 'cost_model',
             'regime_threshold_ref', 'integration_receipt_ref'))
        or selection.outcome != 'SELECTED' or selection.primary_alpha_id is None
        or intent.allowed_alpha_ids != (selection.primary_alpha_id,)
        or body.current_primary_head_ref != selection.primary_candidate_head_ref):
        raise ValueError('HELD E_IDENTITY: phase exit differs from the frozen primary')
    _reference(selection.family_review_ref, 65536)
    family = _read(reader, selection.family_review_ref, FamilyReview)
    if family.input_set_ref != intent.input_set_ref or select_primary(family, reader) != selection:
        raise ValueError('HELD E_PRIMARY: phase exit differs from full-family selection')
    closure_ref = family.candidate_report_refs[FAMILY_IDS.index(selection.primary_alpha_id)]
    closure = _read(reader, closure_ref, CampaignClosureReport)
    qualification = _read(reader, body.primary_qualification_ref, QualificationBundle)
    head = read_registry_event(reader, body.current_primary_head_ref)
    _reference(manifest.research_registration_ref, 65536)
    registration = _read(reader, manifest.research_registration_ref, RegistrationProof)
    baseline_selection = validate_baseline_selection(body.baseline_selection_ref, intent.input_set_ref, reader)
    if (closure.qualification_ref != body.primary_qualification_ref or qualification.alpha_verdict != 'PASS'
        or head.record.lifecycle_status is not AlphaLifecycleStatus.OOS_PASS
        or head.record.source_sha != expected_source.commit_sha
        or (head.record.alpha_id, head.record.version) != (selection.primary_alpha_id, selection.primary_version)
        or registration.input_set_ref != intent.input_set_ref
        or registration.baseline_selection_ref != body.baseline_selection_ref
        or manifest.selected_baseline != baseline_selection.selected_id):
        raise ValueError('HELD E_PRIMARY: phase exit qualification or registry head differs')
    primary = _read(reader, body.executable_ref, ExecutableResult)
    baseline = _read(reader, body.baseline_executable_ref, ExecutableResult)
    _reference(primary.instrument_spec_ref, 65536)
    spec = _read(reader, primary.instrument_spec_ref, InstrumentSpec)
    if (_read(holdout_view, evaluation.manifest_ref, HoldoutManifest) != manifest
        or _read(holdout_view, primary.instrument_spec_ref, InstrumentSpec) != spec):
        raise ValueError('HELD E_IDENTITY: phase exit differs from its released view')
    calculation = ReadbackStore(ReplicaArtifactStore(holdout_view, reader), reader)
    if (evaluate_holdout(manifest, spec, calculation) != evaluation
        or run_executable_reference(manifest, spec, calculation) != primary
        or run_selected_baseline_reference(manifest, spec, calculation) != baseline):
        raise ValueError('HELD E_IDENTITY: retained holdout or executable calculation differs')
    proof = _read(reader, body.holdout_replay_ref, ReplayProof)
    _reference(manifest.environment_ref, 65536)
    environment = _read(reader, manifest.environment_ref, EnvironmentIdentity)
    validate_replay_proof(proof, manifest_ref=evaluation.manifest_ref, result_ref=body.holdout_evaluation_ref,
        source=expected_source, environment_ref=manifest.environment_ref,
        sandbox_policy_digest=environment.sandbox_policy_digest, reader=reader)
    if any(_read(reader, ref, ReplayReceipt).logical_trial_id != 'p3-holdout-primary-v1' for ref in proof.receipt_refs):
        raise ValueError('HELD E_REPLAY: holdout replay belongs to another experiment')
    pair = validate_parity_pair(body.parity_ref, manifest_ref=evaluation.manifest_ref,
        instrument_spec_ref=primary.instrument_spec_ref, primary_reference_ref=body.executable_ref,
        baseline_reference_ref=body.baseline_executable_ref, store=reader,
        native_parent_proof_ref=native_parent_proof_ref)
    native = _read(reader, _read(reader, pair.primary_parity_ref, ParityResult).native_result_refs[0], ExecutableResult)
    native_baseline = _read(reader, _read(reader, pair.baseline_parity_ref, ParityResult).native_result_refs[0], ExecutableResult)
    if any(Decimal(result.ending_position) != 0 for result in (primary, baseline, native, native_baseline)):
        raise ValueError('HELD E_IDENTITY: phase exit requires terminal liquidation')
    checks = (*_economic_checks(evaluation, native, native_baseline),
        *(ExitCheck.model_validate(dict(check_id=key, passed=passed, code='E_'+key)) for key, passed in
            (('PARITY', pair.verdict == 'PASS'), ('REPLAY', True), ('PRIMARY', True), ('IDENTITY', True))))
    value = dict(schema_version='p3-exit-result-v1',
        **result_refs,
        checks=checks, verdict='HELD' if pair.verdict != 'PASS' else ('PASS' if all(check.passed for check in checks) else 'FAIL'),
        limitations=('historical_access_controlled_not_informationally_blind', 'synthetic_accounting_parity_only'))
    return ExitResult.model_validate({**value, 'digest':hashlib.sha256(canonical_json_bytes(value)).hexdigest()})

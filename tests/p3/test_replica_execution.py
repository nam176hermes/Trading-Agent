"""Synthetic child execution; these tests confer no research authority."""

import hashlib
import json
import math
from datetime import UTC, datetime, date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
import sys

import pytest

from packages.alpha_lifecycle.contracts.results import BaselinePack
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1, PITQueryV1, PITQueryMode
from packages.engine_contracts.serialization import canonical_json_bytes
from scripts.run_p3_evaluation_child import main
from packages.alpha_lifecycle.sandbox import BubblewrapExecutor, SandboxHeld
from tests.p3.test_dataset import _bar


def _seal(store, **value):
    value["digest"] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def baseline_inputs(root, *, return_count=2):
    root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)
    placeholder = store.put_bytes(b"{}", media_type="application/json")
    start = date(2018, 1, 1)
    query=PITQueryV1(mode=PITQueryMode.SYSTEM_OBSERVED,valid_at=datetime(2025,9,1,tzinfo=UTC),
        cutoff=datetime(2026,9,5,12,tzinfo=UTC))
    rows = []
    for i in range(2800):
        bar = _bar(start + timedelta(days=i)).model_dump(
            mode="json", exclude={"digest"}
        )
        close = str(100 + int(30 * math.sin(i / 20)) + (20 if i % 90 == 0 else 0))
        bar.update(
            open=close, close=close, high=str(int(close) + 1), low=str(int(close) - 1)
        )
        rows.append(_seal(store, **bar))
    rows = tuple(rows)
    dataset = _seal(
        store,
        schema_version="p3-dataset-evidence-v1",
        snapshot_ref=placeholder,
        query_digest=query.canonical_digest,
        segment="RESEARCH",
        usable_rows=2800,
        row_refs=rows,
        date_range={"start": str(start), "end": str(start + timedelta(days=2799))},
        ordered_rows_digest=hashlib.sha256(canonical_json_bytes([row.content_sha256 for row in rows])).hexdigest(),
        vintage_class="RETROSPECTIVE_CURRENT_ARCHIVE",
        observed_cutoff="2026-09-05T12:00:00Z",
        limitations=["retrospective.current_archive"],
    )
    folds = []
    for n, offset in enumerate((400, 500, 600), 1):
        first = start + timedelta(days=offset)
        ref = _seal(
            store,
            schema_version="p3-fold-v1",
            fold_id=f"F{n}",
            return_end_range={
                "start": str(first + timedelta(days=1)),
                "end": str(first + timedelta(days=return_count)),
            },
            context_start=str(first - timedelta(days=300)),
            decision_start=str(first),
            decision_end=str(first + timedelta(days=return_count - 1)),
            return_count=return_count,
            decision_row_refs=rows[offset : offset + return_count],
            return_row_refs=rows[offset + 1 : offset + return_count + 1],
            snapshot_ref=placeholder,
        )
        folds.append(json.loads(store.read_bytes(ref)))
    manifest = _seal(
        store,
        schema_version="p3-fold-manifest-v1",
        mode="OOS",
        folds=folds,
        static_policy_digest="c" * 64,
        dataset_evidence_ref=dataset,
    )
    threshold = _seal(
        store,
        schema_version="p3-regime-threshold-v1",
        policy_digest="c" * 64,
        training_dataset_ref=dataset,
        training_range={"start": str(start), "end": str(start + timedelta(days=399))},
        sample_count=380,
        ordered_volatility_digest="d" * 64,
        threshold="0.02",
    )
    environment = _seal(
        store,
        schema_version="p3-environment-identity-v1",
        python_version="3.11",
        root_lock_digest="a" * 64,
        native_manifest_digest="b" * 64,
        sandbox_policy_digest="c" * 64,
        platform="linux-x86_64",
        decimal_precision=50,
    )
    # Structural fixture declarations only; no authenticated producer or backup custody.
    source=dict(commit_sha='a'*40,tree_sha='b'*40,closure_schema_version='v1',
        closure_policy_sha256='c'*64,closure_sha256='d'*64)
    producer=dict(source=source,producer_repository='nam176hermes/Trading-Agent',
        producer_workflow_ref='nam176hermes/Trading-Agent/.github/workflows/p3-research-inputs.yml@refs/heads/main',
        producer_run_id=1,producer_attempt=1,authority=dict(broker=False,live=False,network=False,production=False))
    backup=_seal(store,schema_version='p3-research-backup-receipt-v1',**producer,
        inventory_content_sha256='8'*64,dataset_content_sha256=dataset.content_sha256,
        snapshot_content_sha256=placeholder.content_sha256,object_inventory_content_sha256='9'*64,
        object_inventory_size_bytes=1000000,destination_namespace='p3.research.backup',object_version='9'*64,
        object_count=20000,total_bytes=10000000,verified_at='2026-09-05T12:00:01Z',status='READBACK_VERIFIED')
    revision=_seal(store,schema_version='p3-research-batch-commitment-v1',**producer,
        policy_digest='c'*64,segment='RESEARCH',query=query,inventory_content_sha256='8'*64,
        inventory_size_bytes=1,inventory_entry_count=2800,dataset_content_sha256=dataset.content_sha256,
        dataset_digest=json.loads(store.read_bytes(dataset))['digest'],snapshot_content_sha256=placeholder.content_sha256,
        batch_ordinal=1,predecessor_commitment_content_sha256=None,
        frozen_at='2026-09-05T11:59:59Z',completed_at='2026-09-05T12:00:00Z',issued_at='2026-09-05T12:00:02Z',
        backup_receipt_ref=backup,status='PASS')
    pit = _seal(
        store,
        schema_version="p3-p-i-t-proof-v1",
        dataset_ref=dataset,
        fold_manifest_ref=manifest,
        vintage_class="RETROSPECTIVE_CURRENT_ARCHIVE",
        historical_vintage_verified=False,
        revision_proof_ref=revision,
        no_future_suite_ref=placeholder,
        limitations=["retrospective.current_archive"],
    )
    inputs = _seal(
        store,
        schema_version="p3-input-set-v1",
        source={
            "commit_sha": "a" * 40,
            "tree_sha": "b" * 40,
            "closure_schema_version": "v1",
            "closure_policy_sha256": "c" * 64,
            "closure_sha256": "d" * 64,
        },
        epoch_id="synthetic",
        policy_digest="c" * 64,
        family_digest="e" * 64,
        dataset_evidence_ref=dataset,
        fold_manifest_ref=manifest,
        pit_proof_ref=pit,
        environment_ref=environment,
        regime_threshold_ref=threshold,
        integration_receipt_ref=placeholder,
        cost_model=dict(
            fee_bps=0, spread_bps=0, slippage_bps=0, funding_bps=0, borrow_bps=0
        ),
    )
    baseline = _seal(
        store,
        schema_version="p3-baseline-manifest-v1",
        input_set_ref=inputs,
        required_baselines=[
            "B0_CASH",
            "B1_BUY_AND_HOLD",
            "B2_EQUAL_WEIGHT",
            "B3_SIMPLE_MOMENTUM",
            "B4_SIMPLE_MEAN_REVERSION",
        ],
    )
    return store, baseline


def test_baseline_child_dispatches_and_preserves_the_input_store(tmp_path):
    store, manifest = baseline_inputs(tmp_path / "inputs")
    before = {p.name: p.read_bytes() for p in (tmp_path / "inputs").iterdir()}
    replica = tmp_path / "replica"
    replica.mkdir(mode=0o700)
    request = replica / "manifest.json"
    request.write_bytes(canonical_json_bytes(manifest))
    result = replica / "result.json"

    main(request, tmp_path / "inputs", result)

    pack = BaselinePack.model_validate_json(result.read_bytes())
    assert len(pack.baseline_results) == 5
    assert pack.baseline_results[0].aggregate_metrics.total_return == Decimal(0)
    assert before == {p.name: p.read_bytes() for p in (tmp_path / "inputs").iterdir()}
    outputs = LocalArtifactStore(replica / "artifacts")
    assert outputs.read_bytes(pack.baseline_results[0].scenario_ref)


@pytest.mark.parametrize('fault',['source','environment','policy'])
def test_executor_rejects_unbound_inputs_before_preparing_child_argv(tmp_path,monkeypatch,fault):
    from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet
    from packages.alpha_lifecycle import sandbox
    store,manifest_ref = baseline_inputs(tmp_path/'inputs')
    manifest = BaselineManifest.model_validate_json(store.read_bytes(manifest_ref))
    inputs = InputSet.model_validate_json(store.read_bytes(manifest.input_set_ref))
    monkeypatch.setattr(sandbox,'require_official_sandbox',lambda value:Path('/usr/bin/bwrap'))
    executor = BubblewrapExecutor(store=store,store_root=tmp_path/'inputs',release_root=Path(__file__).resolve().parents[2],
        python=Path(sys.executable),source=inputs.source.model_copy(update={'commit_sha':'9'*40}) if fault=='source' else inputs.source,
        environment_ref=manifest_ref if fault=='environment' else inputs.environment_ref,
        sandbox_policy_digest=('9' if fault=='policy' else 'c')*64)
    def forbidden(*args):
        raise AssertionError('unbound inputs reached child argv preparation')
    monkeypatch.setattr(executor,'_argv',forbidden)
    output = tmp_path/'output'
    output.mkdir(mode=0o700)
    with pytest.raises(SandboxHeld):
        executor.execute(manifest_ref,replicate='R1',logical_trial_id='synthetic',output_dir=output)


@pytest.mark.parametrize(
    "mutation",
    (
        None,
        "wrong_input",
        "invalid_contract",
        "missing_scenario",
        "wrong_scenario",
        "missing_trace",
        "wrong_source",
        "wrong_environment",
        "wrong_policy",
        "wrong_aggregate",
        "wrong_trace_math",
        "wrong_regime",
    ),
)
def test_parent_retains_child_artifacts_after_real_portable_child_exit(
    tmp_path, monkeypatch, mutation
):
    store, manifest = baseline_inputs(tmp_path / "inputs")
    root = Path(__file__).resolve().parents[2]
    from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet

    baseline_manifest = BaselineManifest.model_validate_json(store.read_bytes(manifest))
    inputs = InputSet.model_validate_json(
        store.read_bytes(baseline_manifest.input_set_ref)
    )
    source = inputs.source
    if mutation == "wrong_source":
        source = source.model_copy(update={"commit_sha": "9" * 40})
    # Portable child-I/O test replaces host isolation; missing-host behavior is
    # separately exercised by test_sandbox.py. This is never qualification.
    monkeypatch.setattr(
        "packages.alpha_lifecycle.sandbox.require_official_sandbox", lambda path: path
    )
    executor = BubblewrapExecutor(
        store=store,
        store_root=tmp_path / "inputs",
        release_root=root,
        python=Path(sys.executable),
        source=source,
        environment_ref=manifest
        if mutation == "wrong_environment"
        else inputs.environment_ref,
        sandbox_policy_digest=("e" if mutation == "wrong_policy" else "c") * 64,
    )
    # This exercises real child I/O, not host isolation or an official receipt.
    monkeypatch.setattr(
        executor,
        "_argv",
        lambda request, result, output, seccomp_fd: (
            sys.executable,
            "-I",
            "-B",
            str(root / "scripts/run_p3_evaluation_child.py"),
            str(request),
            str(tmp_path / "inputs"),
            str(result),
        ),
    )
    output = tmp_path / "replica"
    output.mkdir(mode=0o700)
    if mutation:
        import subprocess

        run = subprocess.run

        def corrupt_result(*args, **kwargs):
            completed = run(*args, **kwargs)
            result_path = output / "result.json"
            body = json.loads(result_path.read_bytes())
            if mutation in {"wrong_source", "wrong_environment", "wrong_policy"}:
                return completed
            if mutation == "wrong_input":
                body["input_set_ref"] = manifest.model_dump(mode="json")
            elif mutation == "invalid_contract":
                body["baseline_results"] = []
            elif mutation == "wrong_scenario":
                body["baseline_results"][1]["scenario_ref"] = body["baseline_results"][
                    0
                ]["scenario_ref"]
            elif mutation == "missing_scenario":
                body["baseline_results"][0]["scenario_ref"] = manifest.model_dump(
                    mode="json"
                )
            else:
                outputs = LocalArtifactStore(output / "artifacts")
                entry = body["baseline_results"][0]
                scenario = json.loads(
                    outputs.read_bytes(
                        ArtifactRefV1.model_validate(entry["scenario_ref"])
                    )
                )
                if mutation == "wrong_aggregate":
                    scenario["aggregate_metrics"]["total_return"] = "0.1"
                    entry["aggregate_metrics"]["total_return"] = "0.1"
                elif mutation in {"wrong_trace_math", "wrong_regime"}:
                    trace = json.loads(
                        outputs.read_bytes(
                            ArtifactRefV1.model_validate(
                                scenario["fold_results"][0]["trace_ref"]
                            )
                        )
                    )
                    if mutation == "wrong_trace_math":
                        trace["samples"][0]["net_return"] = "0.1"
                    else:
                        current = trace["samples"][0]["regime"]
                        trace["samples"][0]["regime"] = (
                            "HIGH_VOL" if current != "HIGH_VOL" else "BULL_LOW_VOL"
                        )
                    for value in (trace["samples"][0], trace):
                        value.pop("digest")
                        value["digest"] = hashlib.sha256(
                            canonical_json_bytes(value)
                        ).hexdigest()
                    scenario["fold_results"][0]["trace_ref"] = outputs.put_bytes(
                        canonical_json_bytes(trace), media_type="application/json"
                    ).model_dump(mode="json")
                else:
                    scenario["fold_results"][0]["trace_ref"] = manifest.model_dump(
                        mode="json"
                    )
                for value in (scenario["fold_results"][0], scenario):
                    value.pop("digest")
                    value["digest"] = hashlib.sha256(
                        canonical_json_bytes(value)
                    ).hexdigest()
                entry["scenario_ref"] = outputs.put_bytes(
                    canonical_json_bytes(scenario), media_type="application/json"
                ).model_dump(mode="json")
            body.pop("digest")
            body["digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
            result_path.write_bytes(canonical_json_bytes(body))
            return completed

        monkeypatch.setattr(subprocess, "run", corrupt_result)
        input_fault = mutation in {'wrong_source','wrong_environment','wrong_policy'}
        with pytest.raises(SandboxHeld, match="input" if input_fault else "result"):
            executor.execute(
                manifest,
                replicate="R1",
                logical_trial_id="synthetic",
                output_dir=output,
            )
        if input_fault:
            assert not (output/'result.json').exists()
        return
    receipt = executor.execute(
        manifest, replicate="R1", logical_trial_id="synthetic", output_dir=output
    )
    pack = BaselinePack.model_validate_json(store.read_bytes(receipt.result_ref))
    assert store.read_bytes(pack.baseline_results[0].scenario_ref)
    assert receipt.output_inventory_digest != receipt.result_ref.content_sha256


def test_oos_child_retains_seven_scenarios_in_private_outputs(tmp_path):
    from packages.alpha_lifecycle.baseline_campaign import (
        run_baseline_pack,
        select_baseline,
    )
    from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet
    from packages.alpha_lifecycle.contracts.results import EvaluationResult
    from packages.alpha_lifecycle.registry import AlphaLifecycleStatus
    from scripts.generate_p3_specs import _candidate_specs, POLICY_SOURCE
    from tests.p3.test_lifecycle import _record

    store, ref = baseline_inputs(tmp_path / "inputs", return_count=90)
    baseline_manifest = BaselineManifest.model_validate_json(store.read_bytes(ref))
    inputs = InputSet.model_validate_json(
        store.read_bytes(baseline_manifest.input_set_ref)
    )
    placeholder = store.put_bytes(b"{}", media_type="application/json")
    pack = run_baseline_pack(baseline_manifest, store)
    snapshot = json.loads(store.read_bytes(inputs.dataset_evidence_ref))[
        "snapshot_ref"
    ]["content_sha256"]
    costs = hashlib.sha256(canonical_json_bytes(inputs.cost_model)).hexdigest()
    selection = select_baseline(
        pack,
        replay_proof_ref=placeholder,
        selection_policy_digest=inputs.policy_digest,
        snapshot_digest=snapshot,
        cost_model_digest=costs,
        store=store,
    )
    selection_ref = store.put_bytes(
        canonical_json_bytes(selection), media_type="application/json"
    )
    specs = _candidate_specs(json.loads(POLICY_SOURCE.read_bytes()))
    heads = []
    for spec in specs:
        record = _record(AlphaLifecycleStatus.CANDIDATE).model_copy(
            update={
                "alpha_id": spec["alpha_id"],
                "parameter_set_sha256": spec["digest"],
                "dataset_snapshot_sha256": snapshot,
                "cost_model_sha256": costs,
                "baseline_id": selection.selected_id,
            }
        )
        heads.append(
            store.put_bytes(
                canonical_json_bytes(
                    dict(
                        schema_version="alpha-registry-event-v1",
                        sequence=2,
                        predecessor_sha256="f" * 64,
                        record=record,
                    )
                ),
                media_type="application/json",
            )
        )
    registration = _seal(
        store,
        schema_version="p3-registration-proof-v1",
        input_set_ref=baseline_manifest.input_set_ref,
        baseline_selection_ref=selection_ref,
        candidate_head_refs=heads,
        publication_ref=placeholder,
    )
    spec_ref = store.put_bytes(
        canonical_json_bytes(specs[0]), media_type="application/json"
    )
    manifest = _seal(
        store,
        schema_version="p3-evaluation-manifest-v1",
        mode="OOS",
        input_set_ref=baseline_manifest.input_set_ref,
        candidate_spec_ref=spec_ref,
        baseline_selection_ref=selection_ref,
        candidate_head_ref=heads[0],
        registration_proof_ref=registration,
        holdout_primary_ref=None,
    )
    output = tmp_path / "replica"
    output.mkdir(mode=0o700)
    request = output / "manifest.json"
    request.write_bytes(canonical_json_bytes(manifest))

    main(request, tmp_path / "inputs", output / "result.json")

    result = EvaluationResult.model_validate_json((output / "result.json").read_bytes())
    assert len(result.perturbations) == 4
    assert len(result.deterministic_trial_keys) == 7
    assert tuple(item.regime_id for item in result.regimes) == (
        "BULL_LOW_VOL",
        "BEAR_LOW_VOL",
        "HIGH_VOL",
    )
    outputs = LocalArtifactStore(output / "artifacts")
    assert outputs.read_bytes(result.base.fold_results[0].trace_ref)
    from packages.alpha_lifecycle.replica_validation import validate_replica_result
    from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore
    from packages.alpha_lifecycle.contracts.execution import EvaluationManifest

    with localcontext() as context:
        context.prec = 50
        validate_replica_result(
            result,
            EvaluationManifest.model_validate_json(store.read_bytes(manifest)),
            store,
            outputs,
            ReplicaArtifactStore(tmp_path / "inputs", output / "artifacts"),
        )
    variant = canonical_json_bytes(result.perturbations[0])
    variant_path = (
        output / "artifacts" / (hashlib.sha256(variant).hexdigest() + ".blob")
    )
    variant_path.unlink()
    with localcontext() as context:
        context.prec = 50
        with pytest.raises(ValueError):
            validate_replica_result(
                result,
                EvaluationManifest.model_validate_json(store.read_bytes(manifest)),
                store,
                outputs,
                ReplicaArtifactStore(tmp_path / "inputs", output / "artifacts"),
            )
    assert not variant_path.exists()
    second = tmp_path / "second"
    second.mkdir(mode=0o700)
    with localcontext() as context:
        context.prec = 12
        main(request, tmp_path / "inputs", second / "result.json")
    assert (second / "result.json").read_bytes() == (
        output / "result.json"
    ).read_bytes()


@pytest.mark.parametrize(
    "damage",
    ("bytes", "symlink", "hardlink", "unknown_name", "noncanonical", "nonjson"),
)
def test_parent_rejects_tampered_replica_outputs(tmp_path, damage):
    from packages.alpha_lifecycle.replica_store import retain_replica_outputs
    from packages.data_catalog.artifact_store import ArtifactIntegrityError

    source_root, target_root = tmp_path / "source", tmp_path / "target"
    source_root.mkdir(mode=0o700)
    target_root.mkdir(mode=0o700)
    source, target = LocalArtifactStore(source_root), LocalArtifactStore(target_root)
    ref = source.put_bytes(b'{"fixture":1}', media_type="application/json")
    result = target.put_bytes(b'{"result":1}', media_type="application/json")
    path = source_root / ref.locator
    if damage == "bytes":
        path.write_bytes(b'{"fixture":2}')
    elif damage == "symlink":
        path.unlink()
        path.symlink_to(target_root / result.locator)
    elif damage == "hardlink":
        (tmp_path / "hardlink").hardlink_to(path)
    elif damage in {"noncanonical", "nonjson"}:
        source.put_bytes(
            b'{ "a": 1 }' if damage == "noncanonical" else b"not json",
            media_type="application/json",
        )
    else:
        path.rename(source_root / "unknown.blob")
    with pytest.raises(ArtifactIntegrityError):
        retain_replica_outputs(source_root, target, result)
    assert not (target_root / ref.locator).exists()


@pytest.mark.parametrize('fault', ['session', 'streams', 'oversized', 'descriptor_read'])
def test_replica_preserves_driver_custody_and_bounds_untrusted_io(tmp_path, monkeypatch, fault):
    import subprocess
    from packages.alpha_lifecycle import sandbox
    from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet

    store, manifest_ref = baseline_inputs(tmp_path / 'inputs')
    manifest = BaselineManifest.model_validate_json(store.read_bytes(manifest_ref))
    inputs = InputSet.model_validate_json(store.read_bytes(manifest.input_set_ref))
    monkeypatch.setattr(sandbox, 'require_official_sandbox', lambda path: path)
    executor = BubblewrapExecutor(
        store=store, store_root=tmp_path / 'inputs',
        release_root=Path(__file__).resolve().parents[2], python=Path(sys.executable),
        source=inputs.source, environment_ref=inputs.environment_ref,
        sandbox_policy_digest='c' * 64,
    )
    output = tmp_path / 'output'
    output.mkdir(mode=0o700)
    result = output / 'result.json'
    original_read = Path.read_bytes

    def no_unbounded_path_read(path):
        if path == result:
            raise AssertionError('untrusted result was read before descriptor bounds')
        return original_read(path)

    def run(argv, **kwargs):
        if fault == 'session':
            assert '--new-session' not in argv
            assert not kwargs.get('start_new_session', False)
        if fault == 'streams':
            assert kwargs['stdout'] == subprocess.DEVNULL
            assert kwargs['stderr'] == subprocess.DEVNULL
        if fault in {'oversized', 'descriptor_read'}:
            result.write_bytes(b'{}')
            if fault == 'oversized':
                with result.open('r+b') as stream:
                    stream.truncate(64 * 1024**2 + 1)
            monkeypatch.setattr(Path, 'read_bytes', no_unbounded_path_read)
            return subprocess.CompletedProcess(argv, 0)
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(sandbox.subprocess, 'run', run)
    with pytest.raises(SandboxHeld):
        executor.execute(manifest_ref, replicate='R1', logical_trial_id='synthetic', output_dir=output)

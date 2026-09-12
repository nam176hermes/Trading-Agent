from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from packages.alpha_lifecycle.folds import fold_specs


def test_oos_fold_specs_are_exact_and_disjoint() -> None:
    policy = json.loads(
        Path("docs/implementation/p3/specs/p3-policy-set-v21.json").read_bytes()
    )
    specs = fold_specs(policy, mode="OOS")
    assert tuple((item.fold_id, item.return_count) for item in specs) == (
        ("F1", 365), ("F2", 366), ("F3", 365)
    )
    assert specs[0].decision_start == date(2022, 8, 31)
    assert specs[-1].decision_end == date(2025, 8, 30)
    assert all(left.return_end < right.return_start for left, right in zip(specs, specs[1:]))


def test_holdout_is_one_fixed_fold() -> None:
    policy = json.loads(
        Path("docs/implementation/p3/specs/p3-policy-set-v21.json").read_bytes()
    )
    assert tuple((item.fold_id, item.return_count) for item in fold_specs(policy, mode="HOLDOUT")) == (("H1", 365),)


def test_build_research_folds_from_all_sealed_daily_rows(tmp_path):
    from packages.alpha_lifecycle.contracts.data import DatasetEvidence, FoldManifest
    from packages.alpha_lifecycle.folds import build_fold_manifest
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from packages.engine_contracts.serialization import canonical_json_bytes
    from tests.p3.test_dataset import _bar
    from tests.p3.test_replica_execution import _seal
    root=tmp_path/'store'
    root.mkdir(mode=0o700)
    store=LocalArtifactStore(root)
    start=date(2018,1,1)
    rows=tuple(store.put_bytes(canonical_json_bytes(_bar(start+timedelta(days=i))),
        media_type='application/json') for i in range(2800))
    placeholder=store.put_bytes(b'{}',media_type='application/json')
    dataset_ref=_seal(store,schema_version='p3-dataset-evidence-v1',snapshot_ref=placeholder,
        query_digest='a'*64,segment='RESEARCH',usable_rows=2800,row_refs=rows,
        date_range={'start':'2018-01-01','end':'2025-08-31'},ordered_rows_digest='b'*64,
        vintage_class='RETROSPECTIVE_CURRENT_ARCHIVE',observed_cutoff='2026-09-05T12:00:01Z',limitations=[])
    dataset=DatasetEvidence.model_validate_json(store.read_bytes(dataset_ref))
    policy=json.loads(Path('docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes())
    manifest=build_fold_manifest(dataset,policy,store)
    assert manifest.dataset_evidence_ref==dataset_ref
    assert tuple(f.return_count for f in manifest.folds)==(365,366,365)
    for fold,spec in zip(manifest.folds,fold_specs(policy,mode='OOS'),strict=True):
        offset=(spec.decision_start-start).days
        assert fold.decision_row_refs==rows[offset:offset+spec.return_count]
        assert fold.return_row_refs==rows[offset+1:offset+spec.return_count+1]
    assert FoldManifest.model_validate_json(canonical_json_bytes(manifest))==manifest

"""Fixed child executable for one deterministic P3 evaluation manifest."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_EVEN, localcontext
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.baseline_campaign import run_baseline_pack
from packages.alpha_lifecycle.contracts.base import parse_contract
from packages.alpha_lifecycle.contracts.execution import InstrumentSpec
from packages.alpha_lifecycle.evaluation import evaluate, evaluate_holdout
from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore, _read
from packages.alpha_lifecycle.pit_evidence import _reference
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.alpha_lifecycle.holdout_view import HoldoutCalculationView, read_calculation_view


def main(manifest_ref_path: Path, store_root: Path, output: Path, *,
    instrument_spec_ref_json: str | None = None) -> None:
    ref = ArtifactRefV1.model_validate(json.loads(manifest_ref_path.read_bytes()))
    input_store=store_root
    spec_ref=None
    if instrument_spec_ref_json is not None:
        if len(instrument_spec_ref_json.encode())>8192:
            raise ValueError('holdout requires a bounded instrument reference')
        spec_ref=ArtifactRefV1.model_validate_json(instrument_spec_ref_json)
        if canonical_json_bytes(spec_ref)!=instrument_spec_ref_json.encode():
            raise ValueError('instrument reference must be canonical')
        _reference(spec_ref,65536)
        view_raw=read_calculation_view(store_root)
        input_store=HoldoutCalculationView(view_raw,ref,spec_ref)
    artifact_root = output.parent / "artifacts"
    artifact_root.mkdir(mode=0o700, exist_ok=False)
    store = ReplicaArtifactStore(input_store, artifact_root)
    raw = store.read_bytes(ref)
    kind=json.loads(raw).get('schema_version')
    spec=None
    if kind=='p3-holdout-manifest-v1':
        if spec_ref is None:
            raise ValueError('holdout requires a bounded instrument reference')
        spec=_read(store,spec_ref,InstrumentSpec)
    elif instrument_spec_ref_json is not None:
        raise ValueError('instrument reference is only valid for holdout')
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        if kind == "p3-baseline-manifest-v1":
            result = run_baseline_pack(parse_contract("BaselineManifest", raw), store)
        elif kind == "p3-evaluation-manifest-v1":
            result = evaluate(parse_contract("EvaluationManifest", raw), store)
        elif kind=='p3-holdout-manifest-v1' and spec is not None:
            result=evaluate_holdout(parse_contract('HoldoutManifest',raw),spec,store)
        else:
            raise ValueError('unsupported evaluation manifest')
    output.write_bytes(canonical_json_bytes(result))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("manifest_ref", type=Path)
    parser.add_argument("store", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument('--instrument-spec-ref')
    args = parser.parse_args()
    main(args.manifest_ref, args.store, args.output,instrument_spec_ref_json=args.instrument_spec_ref)

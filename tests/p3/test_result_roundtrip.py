"""P3 envelopes must preserve the existing metrics wire contract."""

import hashlib
from decimal import Decimal

import pytest

from packages.alpha_lifecycle.contracts.results import BaselineEntry, BaselinePack
from packages.alpha_lifecycle.baselines import BaselineId
from packages.alpha_lifecycle.metrics import CostModelV1, calculate_performance_metrics
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_metric_goldens import _rows
from tests.p3.test_replay import _ref


def test_pack_roundtrip_preserves_legacy_metrics_bytes():
    metrics = calculate_performance_metrics(
        _rows(("100", "100.01")),
        (Decimal(1), Decimal(1)),
        CostModelV1(
            fee_bps=0, spread_bps=0, slippage_bps=0, funding_bps=0, borrow_bps=0
        ),
    )
    payload = dict(
        schema_version="p3-baseline-pack-v1",
        input_set_ref=_ref("a" * 64),
        baseline_results=tuple(
            BaselineEntry(
                baseline_id=item,
                baseline_version="1.0.0",
                scenario_ref=_ref("b" * 64),
                aggregate_metrics=metrics,
            )
            for item in BaselineId
        ),
    )
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    pack = BaselinePack.model_validate(payload)
    decoded = BaselinePack.model_validate_json(canonical_json_bytes(pack))
    assert decoded == pack
    assert canonical_json_bytes(
        decoded.baseline_results[0].aggregate_metrics
    ) == canonical_json_bytes(metrics)
    with pytest.raises(ValueError):
        BaselinePack.model_validate(
            {**payload, "baseline_results": iter(payload["baseline_results"])}
        )

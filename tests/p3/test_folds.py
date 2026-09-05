from __future__ import annotations

import json
from datetime import date
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

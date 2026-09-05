from __future__ import annotations

import hashlib
import json
from pathlib import Path

from packages.alpha_lifecycle.contracts.policy import CandidateSpec


def test_generator_is_deterministic_and_freezes_accepted_policy(tmp_path: Path) -> None:
    """Break caught: generation drifts from the operator-accepted policy bytes."""
    from scripts.generate_p3_specs import POLICY_SHA256, generate_p3_specs

    first = generate_p3_specs(tmp_path / "first")
    second = generate_p3_specs(tmp_path / "second")
    assert tuple(path.name for path in first) == tuple(path.name for path in second)
    assert tuple(path.read_bytes() for path in first) == tuple(path.read_bytes() for path in second)
    assert hashlib.sha256(first[0].read_bytes()).hexdigest() == POLICY_SHA256

    candidates = json.loads(first[1].read_bytes())
    assert len(candidates) == 4
    assert all(CandidateSpec.model_validate(item) for item in candidates)
    assert all(
        set(item["parameters"]) == {
            "family", "entry", "exit", "fast", "slow", "window",
            "entry_z", "exit_z", "horizons", "positive_votes",
        }
        for item in candidates
    )


def test_generator_rejects_policy_with_enabled_external_authority(tmp_path: Path) -> None:
    """Break caught: static source generation can silently grant live capability."""
    from scripts.generate_p3_specs import PolicyFreezeError, generate_p3_specs

    policy = json.loads(
        Path("docs/implementation/p3/specs/p3-policy-set-v21.json").read_bytes()
    )
    policy["authority"]["network"] = True
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(policy))

    import pytest

    with pytest.raises(PolicyFreezeError, match="accepted policy digest"):
        generate_p3_specs(tmp_path / "out", source=changed)

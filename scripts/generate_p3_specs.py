"""Generate deterministic P3 policy and normalized candidate specifications."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.contracts.policy import CandidateSpec
from packages.engine_contracts.serialization import canonical_json_bytes


POLICY_SHA256 = "75a9030d017df1de47c5c42aeb95b612f50fd34630c99f82ffd8d200da506336"
POLICY_SOURCE = ROOT / "docs/implementation/p3/specs/p3-policy-set-v21.json"
_FIELDS = (
    "entry", "exit", "fast", "slow", "window", "entry_z", "exit_z",
    "horizons", "positive_votes",
)


class PolicyFreezeError(ValueError):
    """The source bytes are not the operator-accepted P3 policy."""


def _parameters(family: str, values: dict[str, object]) -> dict[str, object]:
    normalized = {name: None for name in _FIELDS}
    if family == "ZSCORE":
        normalized.update(
            window=values["window"], entry_z=values["entry"], exit_z=values["exit"]
        )
    else:
        normalized.update(values)
    return {"family": family, **normalized}


def _candidate_specs(policy: dict[str, object]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for candidate in policy["candidates"]:
        value = {
            "schema_version": "p3-candidate-spec-v1",
            "alpha_id": candidate["alpha_id"],
            "version": candidate["version"],
            "family": candidate["family"],
            "parameters": _parameters(candidate["family"], candidate["parameters"]),
            "perturbations": [
                {
                    "perturbation_id": item["id"].lower(),
                    "parameters": _parameters(candidate["family"], item["parameters"]),
                }
                for item in candidate["perturbations"]
            ],
        }
        value["digest"] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        result.append(CandidateSpec.model_validate(value).model_dump(mode="json"))
    return result


def generate_p3_specs(
    output_dir: Path, *, source: Path = POLICY_SOURCE
) -> tuple[Path, ...]:
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != POLICY_SHA256:
        raise PolicyFreezeError("source does not match the accepted policy digest")
    policy = json.loads(raw)
    if policy["authority"] != {
        "broker": False, "live": False, "network": False, "production": False
    }:
        raise PolicyFreezeError("external authority must remain disabled")

    output_dir.mkdir(parents=True, exist_ok=True)
    policy_path = output_dir / "p3-policy-set-v21.json"
    candidate_path = output_dir / "candidate-specs-v21.json"
    policy_path.write_bytes(raw)
    candidate_path.write_bytes(canonical_json_bytes(_candidate_specs(policy)) + b"\n")
    return policy_path, candidate_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    for generated in generate_p3_specs(args.output_dir):
        print(generated)

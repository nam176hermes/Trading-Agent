from __future__ import annotations

import json
import hashlib
from importlib import import_module

import pytest
from pydantic import ValidationError
from packages.data_contracts import ArtifactRefV1


def _base():
    return import_module("packages.alpha_lifecycle.contracts.base")


def _policy():
    return import_module("packages.alpha_lifecycle.contracts.policy")


def test_parser_rejects_duplicate_keys_before_model_validation() -> None:
    """Break caught: duplicate JSON fields let an attacker choose parser semantics."""
    raw = (
        b'{"broker":false,"live":false,"network":false,'
        b'"production":false,"network":false}'
    )

    base = _base()
    with pytest.raises(base.ContractError, match="duplicate JSON key: network"):
        base.parse_contract("SafeAuthority", raw)


def test_safe_authority_cannot_enable_external_capabilities() -> None:
    """Break caught: a plan artifact self-grants network or live authority."""
    base = _base()
    safe = base.SafeAuthority(broker=False, live=False, network=False, production=False)

    assert base.parse_contract("SafeAuthority", safe.model_dump_json().encode()) == safe
    with pytest.raises(ValidationError):
        base.SafeAuthority(broker=False, live=False, network=True, production=False)


def test_parser_reuses_and_strengthens_the_existing_artifact_reference() -> None:
    """Break caught: a locator can point at bytes outside its declared digest."""
    digest = "a" * 64
    raw = json.dumps({
        "content_sha256": digest,
        "size_bytes": 1,
        "media_type": "application/json",
        "locator": f"{digest}.blob",
    }).encode()

    assert isinstance(_base().parse_contract("ArtifactRef", raw), ArtifactRefV1)

    forged = json.loads(raw)
    forged["locator"] = f"{'b' * 64}.blob"
    with pytest.raises(_base().ContractError, match="locator"):
        _base().parse_contract("ArtifactRef", json.dumps(forged).encode())


@pytest.mark.parametrize(
    "raw",
    (
        {"family":"DONCHIAN","entry":20,"exit":10,"fast":None,"slow":None,"window":None,"entry_z":None,"exit_z":None,"horizons":None,"positive_votes":None},
        {"family":"DUAL_SMA","entry":None,"exit":None,"fast":50,"slow":200,"window":None,"entry_z":None,"exit_z":None,"horizons":None,"positive_votes":None},
        {"family":"ZSCORE","entry":None,"exit":None,"fast":None,"slow":None,"window":20,"entry_z":"-1","exit_z":"0","horizons":None,"positive_votes":None},
        {"family":"TSMOM","entry":None,"exit":None,"fast":None,"slow":None,"window":None,"entry_z":None,"exit_z":None,"horizons":[21,63,126],"positive_votes":2},
    ),
)
def test_candidate_parameter_families_accept_only_their_exact_field_mask(
    raw: dict[str, object],
) -> None:
    """Break caught: unused family parameters become hidden tuning inputs."""
    parameters = _policy().CandidateParameters
    assert parameters.model_validate(raw).family == raw["family"]

    corrupted = dict(raw)
    corrupted["fast"] = 1 if raw["family"] != "DUAL_SMA" else None
    with pytest.raises(ValidationError):
        parameters.model_validate(corrupted)


def test_candidate_spec_accepts_the_hand_checked_pack_digest() -> None:
    """Break caught: candidate bytes are accepted without their policy-bound digest."""
    candidate = json.loads(
        """{"alpha_id":"a0.donchian-20-10-close-confirm","digest":"c81154f3157832782f394d7b0dbd624d6f29c8fa6f26216697ae9112cf938445","family":"DONCHIAN","parameters":{"entry":20,"entry_z":null,"exit":10,"exit_z":null,"family":"DONCHIAN","fast":null,"horizons":null,"positive_votes":null,"slow":null,"window":null},"perturbations":[{"parameters":{"entry":18,"entry_z":null,"exit":10,"exit_z":null,"family":"DONCHIAN","fast":null,"horizons":null,"positive_votes":null,"slow":null,"window":null},"perturbation_id":"p01"},{"parameters":{"entry":22,"entry_z":null,"exit":10,"exit_z":null,"family":"DONCHIAN","fast":null,"horizons":null,"positive_votes":null,"slow":null,"window":null},"perturbation_id":"p02"},{"parameters":{"entry":20,"entry_z":null,"exit":9,"exit_z":null,"family":"DONCHIAN","fast":null,"horizons":null,"positive_votes":null,"slow":null,"window":null},"perturbation_id":"p03"},{"parameters":{"entry":20,"entry_z":null,"exit":11,"exit_z":null,"family":"DONCHIAN","fast":null,"horizons":null,"positive_votes":null,"slow":null,"window":null},"perturbation_id":"p04"}],"schema_version":"p3-candidate-spec-v1","version":"1.0.0"}"""
    )

    base = _base()
    candidate_spec = _policy().CandidateSpec
    parsed = base.parse_contract("CandidateSpec", json.dumps(candidate).encode())
    assert isinstance(parsed, candidate_spec)

    candidate["parameters"]["entry"] = 19
    with pytest.raises(ValidationError, match="digest"):
        candidate_spec.model_validate(candidate)


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_daily_bar_rejects_impossible_ohlc_and_observation_order() -> None:
    """Break caught: a malformed daily bar enters a sealed research dataset."""
    daily_bar = import_module("packages.alpha_lifecycle.contracts.data").DailyBar
    ref_digest = "a" * 64
    payload: dict[str, object] = {
        "schema_version": "p3-daily-bar-v1",
        "date": "2025-01-01",
        "instrument": "BTCUSDT.BINANCE",
        "opened_at": "2025-01-01T00:00:00Z",
        "closed_at_exclusive": "2025-01-02T00:00:00Z",
        "raw_close_time": 1735775999999,
        "raw_timestamp_unit": "MILLISECONDS",
        "open": "100",
        "high": "110",
        "low": "90",
        "close": "105",
        "base_volume": "1",
        "quote_volume": "100",
        "trade_count": 1,
        "provider_published_at": None,
        "system_observed_at": "2025-01-02T00:00:01Z",
        "ingested_at": "2025-01-02T00:00:02Z",
        "partition_ref": {
            "content_sha256": ref_digest,
            "size_bytes": 1,
            "media_type": "application/json",
            "locator": f"{ref_digest}.blob",
        },
        "row_ordinal": 0,
    }
    payload["digest"] = _digest(payload)

    assert daily_bar.model_validate(payload).date.isoformat() == "2025-01-01"

    impossible = dict(payload, high="99")
    impossible["digest"] = _digest({k: v for k, v in impossible.items() if k != "digest"})
    with pytest.raises(ValidationError, match="OHLC"):
        daily_bar.model_validate(impossible)

    early = dict(payload, system_observed_at="2025-01-01T23:59:59Z")
    early["digest"] = _digest({k: v for k, v in early.items() if k != "digest"})
    with pytest.raises(ValidationError, match="observation"):
        daily_bar.model_validate(early)

    forged_ref = json.loads(json.dumps(payload))
    forged_ref["partition_ref"]["locator"] = f"{'b' * 64}.blob"
    forged_ref["digest"] = _digest({k: v for k, v in forged_ref.items() if k != "digest"})
    with pytest.raises(ValidationError, match="locator"):
        daily_bar.model_validate(forged_ref)


def test_date_range_rejects_reverse_order() -> None:
    """Break caught: fold and holdout ranges can run backward."""
    date_range = import_module("packages.alpha_lifecycle.contracts.data").DateRange
    with pytest.raises(ValidationError, match="ordered"):
        date_range(start="2025-01-02", end="2025-01-01")


@pytest.mark.parametrize(
    ("module_name", "type_names"),
    (
        ("execution", ("BaselineManifest", "EnvironmentIdentity", "EvaluationManifest", "HoldoutManifest", "InputSet", "InstrumentSpec")),
        ("lifecycle", ("CampaignClosureReport", "ExpectedHead", "PrePublicationEvidence", "PublicationReceipt", "PublicationRequest", "RegistrationProof")),
        ("authority", ("CustodyRecord", "ExposureRecord", "FamilyReview", "HoldoutRequest", "IntegrationReceipt", "PrimarySelection", "ReviewApproval", "RunAuthorization")),
        ("results", ("BaselineEntry", "BaselinePack", "BaselineSelection", "CapacityEvidence", "EvaluationResult", "ExecutableResult", "ExitCheck", "ExitResult", "FoldResult", "HoldoutEvaluationResult", "NativeTraceRow", "ParityResult", "PerformanceTrace", "QualificationBundle", "RegimeEvidence", "RegimeResult", "RegimeThreshold", "ReplayProof", "ReplayReceipt", "RobustnessEvidence", "ScenarioResult", "TraceSample", "TrialOutcome")),
    ),
)
def test_contract_modules_expose_every_catalog_owned_type(
    module_name: str, type_names: tuple[str, ...]
) -> None:
    """Break caught: a catalog type cannot be imported by its declared owner."""
    module = import_module(f"packages.alpha_lifecycle.contracts.{module_name}")
    assert all(hasattr(module, name) for name in type_names)


def test_run_authorization_parser_accepts_valid_json_wire_types() -> None:
    from tests.p3.test_authority import _request
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization

    request, _ = _request("2026-01-01T01:00:00Z")
    raw = json.dumps(request["authorization"]).encode()
    expected = RunAuthorization.model_validate_json(raw)
    assert _base().parse_contract("RunAuthorization", raw) == expected

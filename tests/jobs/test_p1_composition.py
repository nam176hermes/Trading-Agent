from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.engine_contracts import ArtifactReference
from packages.job_contracts import JobType, parse_payload
from services.job_worker.engine_artifacts import (
    EngineArtifactBinding,
    HashBoundArtifactResolver,
)
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
from services.job_worker.engine_results import EngineResultValidator
from services.job_worker.nautilus_closure import NautilusClosureConfig
from services.job_worker.p1_engine_spawn import P1EngineSpawnProvider


P1_CLOSURE_SHA256 = (
    "74b4e8864d8c9a2cc8ba9e5944340f013739e496933fa2f5dc9817bfcb7bced1"
)


def _bindings(root: Path) -> tuple[EngineArtifactBinding, ...]:
    return tuple(
        EngineArtifactBinding(
            ArtifactReference(
                artifact_id=UUID(
                    f"{number}{number}{number}{number}{number}{number}{number}{number}"
                    "-1111-4111-8111-111111111111"
                ),
                sha256=str(number) * 64,
                media_type=(
                    "application/jsonl" if number == 4 else "application/json"
                ),
            ),
            root / f"artifact-{number}",
        )
        for number in range(1, 5)
    )


def test_p1_worker_composes_exact_code_owned_execution_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from services.job_worker import main
    from packages.engine_portfolio_projection.parity import verify_p1_portfolio_parity

    ingestor = object()
    repository = SimpleNamespace(engine_event_ingestor=lambda: ingestor)
    authority = SimpleNamespace(
        runtime_paths=SimpleNamespace(artifact_root=tmp_path / "job-artifacts")
    )
    captured: dict[str, object] = {}
    projection_authority_factory = object()
    safety_authority_refresher = object()

    def capture(repository_arg: object, source: object, **kwargs: object) -> str:
        captured.update(repository=repository_arg, source=source, **kwargs)
        return "p1-worker"

    monkeypatch.setattr(main, "build_worker", capture)

    worker = main.build_p1_worker(
        repository,
        {},
        authority=authority,
        closure_config=NautilusClosureConfig(
            tmp_path / "runtime",
            tmp_path / "release-artifacts",
            tmp_path / "bwrap",
        ),
        transport_root=tmp_path / "transport",
        artifact_bindings=_bindings(tmp_path / "inputs"),
        p1_projection_authority_factory=projection_authority_factory,
        safety_authority_refresher=safety_authority_refresher,
    )

    provider = captured["engine_spawn_provider"]
    validator = captured["engine_result_validator"]
    assert worker == "p1-worker"
    assert captured["repository"] is repository
    assert captured["authority"] is authority
    assert captured["engine_event_ingestor"] is ingestor
    assert captured["p1_projection_authority_factory"] is projection_authority_factory
    assert captured["p1_portfolio_parity_verifier"] is verify_p1_portfolio_parity
    assert (
        captured["_p1_safety_authority_refresher"]
        is safety_authority_refresher
    )
    assert type(provider) is P1EngineSpawnProvider
    assert type(provider._provider._attest_inputs) is HashBoundArtifactResolver
    assert type(validator) is EngineResultValidator
    assert validator._p1_product_closure_sha256 == P1_CLOSURE_SHA256
    assert P1_REAL_BACKTEST_POLICY.closure_sha256 == P1_CLOSURE_SHA256


def test_p1_parity_composition_cannot_be_partially_injected() -> None:
    from services.job_worker import main

    with pytest.raises(ValueError, match="P1 portfolio parity authority"):
        main.build_worker(
            object(),
            {},
            p1_projection_authority_factory=object(),
        )


def test_p1_validator_cannot_be_injected_without_complete_engine_authority() -> None:
    from services.job_worker import main

    with pytest.raises(ValueError, match="complete engine authority"):
        main.build_worker(object(), {}, engine_result_validator=object())


def test_p1_ledger_accepts_only_the_closed_validated_result(
    tmp_path: Path,
) -> None:
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger
    from tests.jobs.test_engine_result_validation import _stdout
    from tests.nautilus_runtime_contracts.test_result import (
        _batch,
        _p1_claim,
        _p1_request,
    )

    raw, _events = _batch()
    validated = EngineResultValidator(
        tmp_path, p1_product_closure_sha256="a" * 64
    ).validate(
        P1_REAL_BACKTEST_POLICY.result_validator_id,
        _p1_claim(),
        request=_p1_request(),
        stdout=_stdout(tmp_path, raw),
        exit_code=0,
    )

    repository = InMemoryEngineEventLedger()
    receipt = repository.ingest(validated)
    projection = repository.load_projection(receipt.engine_run_id)

    assert receipt.batch_sha256 == validated.sha256
    assert projection is not None
    assert projection.batch_sha256 == validated.sha256
    assert projection.semantic_digest == validated.profile_result.semantic_sha256


@pytest.mark.parametrize(
    "field",
    (
        "profile",
        "executable",
        "module",
        "output_path",
        "engine_version",
        "closure_sha256",
    ),
)
def test_p1_client_cannot_select_runtime_authority(field: str) -> None:
    bindings = _bindings(Path("/deployment-owned-inputs"))
    names = (
        "engine_configuration",
        "instrument_catalog",
        "strategy_configuration",
        "market_data",
    )
    engine_input: dict[str, object] = {
        name: binding.reference.model_dump(mode="json")
        for name, binding in zip(names, bindings, strict=True)
    }
    engine_input.update(
        start_time="2026-08-05T12:00:00Z",
        end_time="2026-08-05T12:30:00Z",
    )
    engine_input[field] = "client-controlled"

    with pytest.raises(ValidationError):
        parse_payload(JobType.BACKTEST, {"engine_backtest": engine_input})

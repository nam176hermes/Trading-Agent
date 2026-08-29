from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from packages.domain import Currency
from packages.engine_event_ledger import StoredEngineEvent
from packages.engine_portfolio_projection.parity import (
    P1PortfolioParityError,
    P1PortfolioParityReceipt,
)
from packages.job_contracts import JobState
from services.job_store.engine_event_repository import InMemoryEngineEventLedger
from services.job_store.worker_repository import WorkerRepository
from services.job_worker.engine_authority import BacktestEngineAuthorityFactory
from services.job_worker.engine_results import EngineResultValidator
from services.job_worker.worker import JobWorker, WORKER_LEASE_SECONDS
from tests.jobs.test_engine_result_validation import CODE_COMMIT, NOW, _stdout
from tests.jobs.test_engine_worker_lifecycle import (
    Provider,
    Repository,
    Runner,
    Validator,
    _outcome,
    _safety,
)
from tests.nautilus_runtime_contracts.test_result import (
    _batch,
    _p1_claim,
    _p1_request,
)
from tests.portfolio_reducer.test_nautilus_p1_parity import _authority


def _validated_p1_batch(tmp_path: Path):
    raw, _events = _batch()
    return EngineResultValidator(
        tmp_path, p1_product_closure_sha256="a" * 64
    ).validate(
        "nautilus-p1-event-stream-v1",
        _p1_claim(),
        request=_p1_request(),
        stdout=_stdout(tmp_path, raw),
        exit_code=0,
    )


class _P1Validator:
    def __init__(self, result) -> None:
        self.result = result

    def validate(self, *args, **kwargs):
        return self.result


class _LoggedIngestor:
    def __init__(self, calls: list[str], *, reload_error: bool = False) -> None:
        self.calls = calls
        self.reload_error = reload_error
        self.repository = InMemoryEngineEventLedger()

    def load_job_receipt(self, job_id):
        return self.repository.load_job_receipt(job_id)

    def ingest_for_job(self, batch, *, claimed):
        self.calls.append("ingest")
        return self.repository.ingest_for_job(batch, claimed=claimed)

    def load_events(self, engine_run_id):
        self.calls.append("load_events")
        if self.reload_error:
            raise RuntimeError("private database detail")
        return self.repository.load_events(engine_run_id)

    def load_projection(self, engine_run_id):
        self.calls.append("load_projection")
        return self.repository.load_projection(engine_run_id)


class _AuthorityFactory:
    def __init__(self, calls: list[str], *, error: bool = False) -> None:
        self.calls = calls
        self.error = error

    def from_request(self, request):
        self.calls.append("authority")
        if self.error:
            raise RuntimeError("private deployment detail")
        return replace(_authority(), request_message_id=request.message_id)


def _receipt(projection, *, batch_sha256: str) -> P1PortfolioParityReceipt:
    assert projection.semantic_digest is not None
    assert projection.request_message_id is not None
    return P1PortfolioParityReceipt(
        schema_version="nautilus-p1-portfolio-parity-v1",
        normalization_version="nautilus-p1-portfolio-normalization-v1",
        engine_run_id=projection.engine_run_id,
        batch_sha256=batch_sha256,
        semantic_digest=projection.semantic_digest,
        request_message_id=projection.request_message_id,
        engine_event_count=projection.event_count,
        engine_last_sequence=projection.last_sequence,
        engine_last_digest=projection.last_digest,
        projection_identity="1" * 64,
        portfolio_stream_id=projection.engine_run_id,
        portfolio_event_count=2,
        portfolio_last_sequence=2,
        restart_prefix_sequence=1,
        portfolio_state_hash="2" * 64,
        portfolio_prefix_history_hash="3" * 64,
        account_id="account-1",
        account_currency=Currency.USDT,
        terminal_position=Decimal("1"),
        terminal_average_entry_price=Decimal("100"),
        terminal_mark_price=Decimal("101"),
        terminal_cash=Decimal("999899.9"),
        terminal_fees=Decimal("0.1"),
        terminal_realized_pnl=Decimal("0"),
        terminal_unrealized_pnl=Decimal("1"),
        observed_at=NOW,
    )


def _worker(
    repository: Repository,
    validator: _P1Validator | Validator,
    ingestor: _LoggedIngestor,
    *,
    authority_factory: object | None,
    parity_verifier: object | None,
    safety=None,
) -> JobWorker:
    parity_dependencies = (
        {}
        if authority_factory is None and parity_verifier is None
        else {
            "p1_projection_authority_factory": authority_factory,
            "p1_portfolio_parity_verifier": parity_verifier,
        }
    )
    return JobWorker(
        repository,
        Runner(_outcome()),
        object(),
        worker_id="worker-authority-1",
        code_commit=CODE_COMMIT,
        environment=object(),
        safety_preflight=safety or (lambda: _safety()),
        engine_authority_factory=BacktestEngineAuthorityFactory(
            code_commit=CODE_COMMIT, clock=lambda: NOW
        ),
        engine_spawn_provider=Provider(),
        engine_result_validator=validator,
        engine_event_ingestor=ingestor,
        lease_seconds=WORKER_LEASE_SECONDS,
        clock=lambda: NOW,
        **parity_dependencies,
    )


def _final(repository: Repository) -> dict[str, object]:
    return next(value for name, value in repository.calls if name == "finalize")


def test_exact_p1_reloads_durable_state_and_proves_parity_before_success(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    repository = Repository(_p1_claim())
    ingestor = _LoggedIngestor(calls)
    factory = _AuthorityFactory(calls)

    def verify(events, authority, projection, *, batch_sha256):
        calls.append("parity")
        assert events == _validated.profile_result.events
        assert authority.request_message_id == projection.request_message_id
        return _receipt(projection, batch_sha256=batch_sha256)

    _validated = _validated_p1_batch(tmp_path)
    original_finalize = repository.finalize_execution

    def safety():
        calls.append("safety")
        return _safety()

    def finalize(*args, **kwargs):
        calls.append("finalize")
        return original_finalize(*args, **kwargs)

    repository.finalize_execution = finalize

    assert _worker(
        repository,
        _P1Validator(_validated),
        ingestor,
        authority_factory=factory,
        parity_verifier=verify,
        safety=safety,
    ).run_once()

    final = _final(repository)
    result = final["result"]
    assert final["final_state"] is JobState.SUCCEEDED
    parity = calls.index("parity")
    assert tuple(calls[parity - 4 : parity + 1]) == (
        "ingest",
        "load_events",
        "load_projection",
        "authority",
        "parity",
    )
    assert calls[parity + 1 :] == ["safety", "finalize"]
    assert result.validation_metadata["engine_event_receipt"]["batch_sha256"] == (
        _validated.sha256
    )
    assert result.validation_metadata["p1_portfolio_parity"]["batch_sha256"] == (
        _validated.sha256
    )
    expected = tuple(
        StoredEngineEvent.from_envelope(event, batch_sha256=_validated.sha256)
        for event in _validated.events
    )
    assert ingestor.repository.load_events(expected[0].engine_run_id) == expected


@pytest.mark.parametrize("invalid_receipt", (False, True))
def test_exact_p1_parity_mismatch_blocks_without_retry_or_rerun(
    tmp_path: Path, invalid_receipt: bool
) -> None:
    calls: list[str] = []
    repository = Repository(_p1_claim())
    ingestor = _LoggedIngestor(calls)
    validator = _P1Validator(_validated_p1_batch(tmp_path))

    def verify(events, authority, projection, *, batch_sha256):
        calls.append("parity")
        if not invalid_receipt:
            raise P1PortfolioParityError("private mismatch detail")
        return _receipt(projection, batch_sha256="f" * 64)

    worker = _worker(
        repository,
        validator,
        ingestor,
        authority_factory=_AuthorityFactory(calls),
        parity_verifier=verify,
    )
    assert worker.run_once()

    final = _final(repository)
    assert final["final_state"] is JobState.BLOCKED
    assert final["reason_code"] == "P1_PORTFOLIO_PARITY_MISMATCH"
    assert final["result"] is None
    assert not any(name == "retry" for name, _ in repository.calls)
    assert worker._runner.calls == 1


@pytest.mark.parametrize("failure", ("missing", "reload", "factory", "mixed"))
def test_exact_p1_unavailable_authority_blocks_without_retry(
    tmp_path: Path, failure: str
) -> None:
    calls: list[str] = []
    repository = Repository(_p1_claim())
    ingestor = _LoggedIngestor(calls, reload_error=failure == "reload")
    validated = _validated_p1_batch(tmp_path)
    if failure == "mixed":
        original_load = ingestor.load_events

        def load_events(engine_run_id):
            return original_load(engine_run_id)[:-1]

        ingestor.load_events = load_events
    factory = None if failure == "missing" else _AuthorityFactory(
        calls, error=failure == "factory"
    )
    verifier = None if failure == "missing" else (
        lambda events, authority, projection, *, batch_sha256: _receipt(
            projection, batch_sha256=batch_sha256
        )
    )

    worker = _worker(
        repository,
        _P1Validator(validated),
        ingestor,
        authority_factory=factory,
        parity_verifier=verifier,
    )
    assert worker.run_once()

    final = _final(repository)
    assert final["final_state"] is JobState.BLOCKED
    assert final["reason_code"] == "P1_PORTFOLIO_PARITY_UNAVAILABLE"
    assert final["result"] is None
    assert not any(name == "retry" for name, _ in repository.calls)
    assert worker._runner.calls == 1


def test_generic_engine_never_invokes_p1_parity(tmp_path: Path) -> None:
    calls: list[str] = []
    repository = Repository(_p1_claim())
    ingestor = _LoggedIngestor(calls)

    assert _worker(
        repository,
        Validator(),
        ingestor,
        authority_factory=_AuthorityFactory(calls),
        parity_verifier=lambda *args, **kwargs: calls.append("parity"),
    ).run_once()

    assert _final(repository)["final_state"] is JobState.SUCCEEDED
    assert "load_events" not in calls
    assert "parity" not in calls


def test_worker_repository_persists_only_closed_p1_receipts(tmp_path: Path) -> None:
    validated = _validated_p1_batch(tmp_path)
    ledger = InMemoryEngineEventLedger()
    engine_receipt = ledger.ingest_for_job(validated, claimed=_p1_claim())
    projection = ledger.load_projection(engine_receipt.engine_run_id)
    assert projection is not None
    result = replace(
        validated,
        validation_metadata={
            **validated.validation_metadata,
            "engine_event_receipt": engine_receipt.model_dump(mode="json"),
            "p1_portfolio_parity": _receipt(
                projection, batch_sha256=engine_receipt.batch_sha256
            ).model_dump(mode="json"),
        },
    )
    captured: dict[str, object] = {}
    repository = object.__new__(WorkerRepository)
    repository.finalize = lambda *args, **kwargs: captured.update(kwargs) or True

    assert repository.finalize_execution(
        _p1_claim(),
        expected_state=JobState.RUNNING,
        expected_attempt_outcome="RUNNING",
        final_state=JobState.SUCCEEDED,
        reason_code="RESULT_VALIDATED",
        trace_id="p1:receipt-persistence",
        outcome=None,
        result=result,
        stream_artifacts=(),
    )

    metadata = captured["result_metadata"]
    assert metadata["engine_event_receipt"] == (
        result.validation_metadata["engine_event_receipt"]
    )
    assert metadata["p1_portfolio_parity"] == (
        result.validation_metadata["p1_portfolio_parity"]
    )
    assert "semantic_digest" not in metadata
    assert "validator_id" not in metadata

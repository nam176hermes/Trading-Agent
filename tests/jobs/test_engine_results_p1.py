from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Callable

import pytest

from packages.engine_contracts import canonical_json_bytes
from tests.jobs.test_engine_result_validation import _nautilus_event, _stdout
from tests.nautilus_runtime_contracts.test_result import (
    _batch,
    _p1_claim,
    _p1_request,
)


def test_worker_validates_and_seals_exact_p1_stdout_bytes(tmp_path: Path) -> None:
    from services.job_worker.engine_results import EngineResultValidator

    raw, envelopes = _batch()
    result = EngineResultValidator(
        tmp_path, p1_product_closure_sha256="a" * 64
    ).validate(
        "nautilus-p1-event-stream-v1",
        _p1_claim(),
        request=_p1_request(),
        stdout=_stdout(tmp_path, raw),
        exit_code=0,
    )

    assert result.events == envelopes
    assert result.sha256 == sha256(raw).hexdigest()
    assert (tmp_path / result.relative_ref).read_bytes() == raw
    assert result.profile_result is not None
    assert result.profile_result.batch_sha256 == result.sha256
    assert result.profile_result.semantic_sha256 == (
        "454890c4511611b9aa11f695b87e60f2eb2e5fdfe40934c8d7ed59645429d032"
    )
    assert result.validation_metadata == {
        "attempt_id": "attempt_fedcba9876543210fedcba9876543210",
        "config_digest": _p1_request().config_digest,
        "engine_run_id": str(_p1_request().engine_run_id),
        "engine_upstream_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
        "engine_version": "1.231.0",
        "event_count": 8,
        "fees": "0.1",
        "fill_count": 1,
        "final_cash": "999899.9",
        "final_position": "1",
        "first_sequence": 2,
        "job_id": "job_0123456789abcdef0123456789abcdef",
        "last_sequence": 9,
        "order_count": 1,
        "p1_product_closure_sha256": "a" * 64,
        "realized_pnl": "0",
        "request_message_id": str(_p1_request().message_id),
        "runtime_family": "cython-v1",
        "semantic_digest": result.profile_result.semantic_sha256,
        "source_commit": "0123456789abcdef0123456789abcdef01234567",
        "target_count": 1,
        "unrealized_pnl": "1",
        "validator_id": "nautilus-p1-event-stream-v1",
    }


@pytest.mark.parametrize(
    ("mutation", "raw"),
    (
        ("truncated-line", lambda value: value[:-1]),
        ("noncanonical", lambda value: b" " + value),
    ),
)
def test_worker_rejects_incomplete_or_noncanonical_p1_stdout(
    tmp_path: Path, mutation: str, raw: Callable[[bytes], bytes]
) -> None:
    from services.job_worker.engine_results import (
        EngineResultValidationError,
        EngineResultValidator,
    )

    original, _events = _batch()
    changed = raw(original)
    with pytest.raises(EngineResultValidationError):
        EngineResultValidator(
            tmp_path / mutation, p1_product_closure_sha256="a" * 64
        ).validate(
            "nautilus-p1-event-stream-v1",
            _p1_claim(),
            request=_p1_request(),
            stdout=_stdout(tmp_path / mutation, changed),
            exit_code=0,
        )


def test_p1_stream_requires_exact_product_closure_and_dedicated_validator(
    tmp_path: Path,
) -> None:
    from services.job_worker.engine_results import (
        EngineResultValidationError,
        EngineResultValidator,
    )

    raw, _events = _batch()
    for validator_id, closure in (
        ("nautilus-p1-event-stream-v1", None),
        ("nautilus-p1-event-stream-v1", "b" * 64),
        ("engine-event-v1", None),
    ):
        root = tmp_path / f"{validator_id}-{closure}"
        with pytest.raises(EngineResultValidationError):
            EngineResultValidator(
                root, p1_product_closure_sha256=closure
            ).validate(
                validator_id,
                _p1_claim(),
                request=_p1_request(),
                stdout=_stdout(root, raw),
                exit_code=0,
            )


def test_p1_validator_rejects_the_old_aggregate_completion(tmp_path: Path) -> None:
    from services.job_worker.engine_results import (
        EngineResultValidationError,
        EngineResultValidator,
    )

    request = _p1_request()
    aggregate = _nautilus_event(request=request).model_copy(
        update={"causation_id": request.causation_id}
    )
    raw = canonical_json_bytes(aggregate) + b"\n"
    with pytest.raises(EngineResultValidationError, match="P1 Nautilus"):
        EngineResultValidator(
            tmp_path, p1_product_closure_sha256="a" * 64
        ).validate(
            "nautilus-p1-event-stream-v1",
            _p1_claim(),
            request=request,
            stdout=_stdout(tmp_path, raw),
            exit_code=0,
        )

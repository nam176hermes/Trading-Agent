from __future__ import annotations

from hashlib import sha256

from packages.engine_contracts import canonical_json_bytes
from tests.p1_nautilus.test_event_projector import _inputs, _project, _typed_events


def test_three_distinct_custodies_preserve_semantics_and_final_portfolio() -> None:
    custodies = tuple(
        (
            f"job_{index:032x}",
            f"attempt_{index:032x}",
            _inputs(suffix=suffix, event_time=f"2026-08-05T12:0{index}:00Z"),
        )
        for index, suffix in enumerate(("a", "b", "c"), start=1)
    )
    streams = tuple(_project(inputs) for _, _, inputs in custodies)
    terminals = tuple(_typed_events(stream)[-1] for stream in streams)
    final_state_sha256s = {
        sha256(
            canonical_json_bytes(
                {
                    "fees": str(terminal.fees),
                    "final_cash": str(terminal.final_cash),
                    "final_position": str(terminal.final_position),
                    "realized_pnl": str(terminal.realized_pnl),
                    "unrealized_pnl": str(terminal.unrealized_pnl),
                }
            )
        ).hexdigest()
        for terminal in terminals
    }

    assert len({job for job, _, _ in custodies}) == 3
    assert len({attempt for _, attempt, _ in custodies}) == 3
    assert len({inputs.request.message_id for _, _, inputs in custodies}) == 3
    assert len({stream.raw_sha256 for stream in streams}) == 3
    assert len({stream.semantic_sha256 for stream in streams}) == 1
    assert len(final_state_sha256s) == 1

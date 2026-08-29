#!/usr/bin/python3.12
"""Closed entrypoint for the sealed P1 Nautilus runtime."""

from __future__ import annotations

import sys
from importlib.metadata import version as package_version

if __package__ in {None, ""}:
    sys.path.insert(0, "/engine")
    __package__ = "runtime_v1"

from .bootstrap import (  # noqa: E402
    load_product_lineage,
    require_engine_version,
    require_runtime_entry,
)
from .errors import RuntimeFailure, classified, emit_diagnostic, guarded  # noqa: E402


_UPSTREAM = "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"


def _runtime_components():
    from .backtest_runner import run_backtest
    from .event_projector import project_event_stream
    from .final_state import validate_final_state
    from .input_loader import load_inputs
    from .jsonl_writer import write_jsonl

    return load_inputs, run_backtest, validate_final_state, project_event_stream, write_jsonl


def _failed(family: str, cause: Exception) -> int:
    try:
        classified(family, cause)
    except RuntimeFailure as failure:
        emit_diagnostic(failure.family)
        return 70
    raise AssertionError("unreachable")


def main() -> int:
    try:
        require_runtime_entry(
            module_name=__name__, module_spec=__spec__, module_file=__file__
        )
        engine_version = package_version("nautilus_trader")
    except Exception as cause:
        return _failed("ENGINE_SETUP_FAILED", cause)
    try:
        require_engine_version(engine_version)
        lineage = load_product_lineage()
    except Exception as cause:
        return _failed("PROFILE_UNSUPPORTED", cause)
    try:
        load_inputs, run_backtest, validate_final_state, project_event_stream, write_jsonl = guarded(
            "ENGINE_SETUP_FAILED", _runtime_components
        )
    except RuntimeFailure as failure:
        emit_diagnostic(
            failure.family,
            engine_version=engine_version,
            closure_digest=lineage["closure_sha256"],
        )
        return 70
    try:
        inputs = guarded("INPUT_INVALID", load_inputs)
        run = guarded("ENGINE_EXECUTION_FAILED", run_backtest, inputs)
        completion = guarded("FINAL_STATE_MISMATCH", validate_final_state, inputs, lineage, run)
        stream = guarded(
            "EVENT_PROJECTION_FAILED",
            project_event_stream,
            inputs,
            run,
            completion,
            closure_digest=lineage["closure_sha256"],
            upstream_commit=_UPSTREAM,
        )
        guarded("OUTPUT_FAILED", write_jsonl, stream)
    except RuntimeFailure as failure:
        emit_diagnostic(
            failure.family,
            engine_version=engine_version,
            closure_digest=lineage["closure_sha256"],
        )
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

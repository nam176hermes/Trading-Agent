"""Code-owned engine profile policy for the P1 worker lane."""

from __future__ import annotations

from dataclasses import dataclass

from engines.nautilus.runtime_v1.profile import (
    P1_REAL_BACKTEST_PROFILE,
    P1_REAL_BACKTEST_SEMANTIC_PROFILE,
)


@dataclass(frozen=True, slots=True)
class EngineProfilePolicy:
    profile: str
    semantic_profile: str
    command_type: str
    manifest_schema_version: int
    runtime_family: str
    engine_version: str
    engine_upstream_commit: str
    entrypoint: str
    argv_prefix: tuple[str, ...]
    result_validator_id: str
    timeout_seconds: int
    required_artifact_names: tuple[str, ...]
    request_protocol_version: str
    event_schema: str
    dependency_import_policy: str
    runtime_inventory_sha256: str
    sandbox_profile_sha256: str
    p1_baseline_receipt_sha256: str
    p1_baseline_status: str
    p1_baseline_scope: str


P1_REAL_BACKTEST_POLICY = EngineProfilePolicy(
    profile=P1_REAL_BACKTEST_PROFILE,
    semantic_profile=P1_REAL_BACKTEST_SEMANTIC_PROFILE,
    command_type="RunBacktest",
    manifest_schema_version=8,
    runtime_family="cython-v1",
    engine_version="1.231.0",
    engine_upstream_commit="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
    entrypoint="/engine/bin/nautilus-entry-guard",
    argv_prefix=(
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "/engine/runtime_v1/main.py",
        "--profile",
        P1_REAL_BACKTEST_PROFILE,
    ),
    result_validator_id="nautilus-p1-event-stream-v1",
    timeout_seconds=120,
    required_artifact_names=(
        "engine_configuration",
        "instrument_catalog",
        "strategy_configuration",
        "market_data",
    ),
    request_protocol_version="1.0.0",
    event_schema="nautilus-p1-event-stream-v1",
    dependency_import_policy="native-guarded-stdlib-first-sealed-wheel-path-v1",
    runtime_inventory_sha256=(
        "a76c51713a74107d75b6383d43f6d7fa2e9d68df2650c70de100bc15fc87bcf3"
    ),
    sandbox_profile_sha256=(
        "742d3d2cf313a0dc5832fd88d277da1d00e07c6e4abcc4ca51bf0ebcd7c3936e"
    ),
    p1_baseline_receipt_sha256=(
        "b1dfa25502c05cfa6daf529e106e24f0f5d6e25ca33af6ecaca3e1ba1528019b"
    ),
    p1_baseline_status="P1_BASELINE_APPROVED",
    p1_baseline_scope="P1_A_AND_P1_B_ONLY",
)


__all__ = ["EngineProfilePolicy", "P1_REAL_BACKTEST_POLICY"]

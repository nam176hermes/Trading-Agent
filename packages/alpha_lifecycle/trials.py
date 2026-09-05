"""Attempt-independent keys for the seven preregistered candidate paths."""

from __future__ import annotations

from packages.alpha_lifecycle.epoch import logical_trial_id


_SCENARIOS = ("base", "p01", "p02", "p03", "p04", "double_cost", "delayed")


def deterministic_trial_keys(epoch_id: str, alpha_id: str) -> tuple[str, ...]:
    return tuple(logical_trial_id(epoch_id, alpha_id, scenario) for scenario in _SCENARIOS)


__all__ = ["deterministic_trial_keys"]

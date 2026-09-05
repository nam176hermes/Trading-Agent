"""Deterministic P3 robustness calculations."""

from __future__ import annotations

from decimal import Decimal
import hashlib

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore
from packages.alpha_lifecycle.capacity import participation_samples
from packages.alpha_lifecycle.contracts.data import DailyBar
from packages.alpha_lifecycle.contracts.results import (
    BaselinePack, BaselineSelection, CapacityEvidence, PerformanceTrace,
    RegimeResult, RobustnessEvidence, ScenarioResult,
)
from packages.alpha_lifecycle.regimes import Regime, require_regime_coverage
from packages.engine_contracts.serialization import canonical_json_bytes


def delayed_weights(weights: tuple[int, ...]) -> tuple[int, ...]:
    if not weights:
        raise ValueError("delayed scenario requires precomputed targets")
    return (0, *weights[:-1])


def _compound(values: tuple[Decimal, ...]) -> Decimal:
    result = Decimal(1)
    for value in values:
        result *= 1 + value
    return result - 1


def regime_excess(
    candidate_returns: tuple[Decimal, ...],
    baseline_returns: tuple[Decimal, ...],
    labels: tuple[Regime, ...] | tuple[str, ...],
) -> dict[Regime, Decimal]:
    if not (len(candidate_returns) == len(baseline_returns) == len(labels)):
        raise ValueError("candidate, baseline, and regime samples must align")
    require_regime_coverage(labels)
    result: dict[Regime, Decimal] = {}
    for label in ("BULL_LOW_VOL", "BEAR_LOW_VOL", "HIGH_VOL"):
        candidate = tuple(value for value, assigned in zip(candidate_returns, labels, strict=True) if assigned == label)
        baseline = tuple(value for value, assigned in zip(baseline_returns, labels, strict=True) if assigned == label)
        result[label] = _compound(candidate) - _compound(baseline)
    return result


def _seal(store: ArtifactStore, value) :
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def _scenario_samples(scenario: ScenarioResult, store: ArtifactStore):
    output = []
    for fold in scenario.fold_results:
        trace = PerformanceTrace.model_validate_json(store.read_bytes(fold.trace_ref))
        output.extend((fold.fold_id, sample) for sample in trace.samples)
    return tuple(output)


def _capacity(base_samples, policy_digest: str, store: ArtifactStore) -> CapacityEvidence:
    transitions = []
    volumes = []
    keys = []
    for fold_id, sample in base_samples:
        events = []
        if Decimal(sample.turnover) != 0:
            events.append((Decimal(sample.turnover), sample.decision_row_ref or sample.return_row_ref, "transition"))
        if Decimal(sample.exit_cost) != 0:
            events.append((Decimal(sample.applied_weight), sample.return_row_ref, "exit"))
        for transition, ref, kind in events:
            if ref is None:
                raise ValueError("capacity transition is missing its effective-day row")
            bar = DailyBar.model_validate_json(store.read_bytes(ref))
            transitions.append(transition)
            volumes.append(Decimal(bar.quote_volume))
            keys.append(f"{fold_id.lower()}.{sample.sample_index}.{kind}")
    samples = participation_samples(tuple(transitions), tuple(volumes))
    ordered = sorted(samples)
    median = (
        Decimal(0) if not ordered else ordered[len(ordered) // 2]
        if len(ordered) % 2 else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
    )
    payload = {
        "schema_version": "p3-capacity-evidence-v1", "policy_digest": policy_digest,
        "event_participations": samples, "event_keys": tuple(keys),
        "median": format(median, "f"), "peak": format(max(ordered, default=Decimal(0)), "f"),
        "no_trades": not samples,
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return CapacityEvidence.model_validate(payload)


def build_robustness(
    base: ScenarioResult,
    variants: tuple[ScenarioResult, ...],
    baseline: BaselineSelection,
    reader: ArtifactStore,
) -> RobustnessEvidence:
    base = ScenarioResult.model_validate(base)
    baseline = BaselineSelection.model_validate(baseline)
    if len(variants) != 6:
        raise ValueError("robustness requires four perturbations, double cost, and delay")
    perturbations = tuple(item for item in variants if item.scenario == "PERTURBATION")
    double = tuple(item for item in variants if item.scenario == "DOUBLE_COST")
    delayed = tuple(item for item in variants if item.scenario == "DELAYED")
    if (
        tuple(item.perturbation_id for item in perturbations) != ("p01", "p02", "p03", "p04")
        or len(double) != 1 or len(delayed) != 1
    ):
        raise ValueError("robustness scenario inventory is not frozen")

    pack = BaselinePack.model_validate_json(reader.read_bytes(baseline.pack_ref))
    baseline_entry = next(item for item in pack.baseline_results if item.baseline_id.value == baseline.selected_id)
    baseline_scenario = ScenarioResult.model_validate_json(reader.read_bytes(baseline_entry.scenario_ref))
    candidate_samples = _scenario_samples(base, reader)
    baseline_samples = _scenario_samples(baseline_scenario, reader)
    candidate_keys = tuple((fold, sample.sample_index, sample.return_row_ref) for fold, sample in candidate_samples)
    baseline_keys = tuple((fold, sample.sample_index, sample.return_row_ref) for fold, sample in baseline_samples)
    if candidate_keys != baseline_keys:
        raise ValueError("candidate and baseline samples are not identically assigned")
    labels = tuple(sample.regime for _, sample in candidate_samples)
    candidate_returns = tuple(Decimal(sample.net_return) for _, sample in candidate_samples)
    baseline_returns = tuple(Decimal(sample.net_return) for _, sample in baseline_samples)
    excess = regime_excess(candidate_returns, baseline_returns, labels)
    regimes = []
    for label in ("BULL_LOW_VOL", "BEAR_LOW_VOL", "HIGH_VOL"):
        indices = tuple(index for index, assigned in enumerate(labels) if assigned == label)
        candidate_return = _compound(tuple(candidate_returns[index] for index in indices))
        baseline_return = _compound(tuple(baseline_returns[index] for index in indices))
        regimes.append(RegimeResult(
            schema_version="p3-regime-result-v1", regime_id=label,
            sample_indices=indices, candidate_return=format(candidate_return, "f"),
            baseline_return=format(baseline_return, "f"), excess=format(excess[label], "f"),
        ))
    capacity = _capacity(candidate_samples, baseline.selection_policy_digest, reader)
    payload = {
        "schema_version": "p3-robustness-evidence-v1", "regimes": tuple(regimes),
        "perturbation_refs": tuple(_seal(reader, item) for item in perturbations),
        "double_cost_ref": _seal(reader, double[0]), "delayed_ref": _seal(reader, delayed[0]),
        "capacity": capacity,
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return RobustnessEvidence.model_validate(payload)


__all__ = ["build_robustness", "delayed_weights", "regime_excess"]

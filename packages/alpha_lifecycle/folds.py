"""Exact P3 fold construction over sealed daily rows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from packages.alpha_lifecycle.contracts.data import DailyBar, DatasetEvidence, DateRange, Fold, FoldManifest
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


class FoldError(ValueError):
    """The sealed dataset cannot supply the frozen fold boundaries."""


@dataclass(frozen=True, slots=True)
class FoldSpec:
    fold_id: Literal["F1", "F2", "F3", "H1"]
    return_start: date
    return_end: date
    decision_start: date
    decision_end: date
    context_start: date
    return_count: int


def fold_specs(policy: dict[str, object], *, mode: Literal["OOS", "HOLDOUT"]) -> tuple[FoldSpec, ...]:
    section = policy["folds" if mode == "OOS" else "holdout"]
    if not isinstance(section, dict):
        raise FoldError("fold policy section must be an object")
    ranges = section["oos_return_end_ranges"] if mode == "OOS" else ((section["start"], section["end"]),)
    if not isinstance(ranges, (list, tuple)):
        raise FoldError("fold ranges must be an array")
    ids = ("F1", "F2", "F3") if mode == "OOS" else ("H1",)
    result = []
    for fold_id, raw in zip(ids, ranges, strict=True):
        if not isinstance(raw, (list, tuple)) or len(raw) != 2 or not all(isinstance(day, str) for day in raw):
            raise FoldError("each fold range must contain two date strings")
        start, end = map(date.fromisoformat, raw)
        decision_start, decision_end = start - timedelta(days=1), end - timedelta(days=1)
        result.append(FoldSpec(
            fold_id, start, end, decision_start, decision_end,
            decision_start - timedelta(days=300), (end - start).days + 1,
        ))
    if any(left.return_end >= right.return_start for left, right in zip(result, result[1:])):
        raise FoldError("frozen return sets overlap")
    return tuple(result)


def build_fold_manifest(
    dataset: DatasetEvidence,
    policy: dict[str, object],
    store: LocalArtifactStore,
) -> FoldManifest:
    dataset = DatasetEvidence.model_validate(dataset)
    mode: Literal["OOS", "HOLDOUT"] = "HOLDOUT" if dataset.segment == "HOLDOUT" else "OOS"
    bars = tuple(DailyBar.model_validate_json(store.read_bytes(ref)) for ref in dataset.row_refs)
    by_day = {bar.date: (bar, ref) for bar, ref in zip(bars, dataset.row_refs, strict=True)}
    if len(by_day) != len(bars):
        raise FoldError("daily rows contain duplicate dates")
    folds = []
    for spec in fold_specs(policy, mode=mode):
        decisions = tuple(spec.decision_start + timedelta(days=i) for i in range(spec.return_count))
        returns = tuple(spec.return_start + timedelta(days=i) for i in range(spec.return_count))
        context = tuple(spec.context_start + timedelta(days=i) for i in range(300))
        if any(day not in by_day for day in (*context, *decisions, *returns)):
            raise FoldError("dataset does not contain exact context/decision/return coverage")
        payload = {
            "schema_version": "p3-fold-v1",
            "fold_id": spec.fold_id,
            "return_end_range": {"start": spec.return_start.isoformat(), "end": spec.return_end.isoformat()},
            "context_start": spec.context_start.isoformat(),
            "decision_start": spec.decision_start.isoformat(),
            "decision_end": spec.decision_end.isoformat(),
            "return_count": spec.return_count,
            "decision_row_refs": tuple(by_day[day][1] for day in decisions),
            "return_row_refs": tuple(by_day[day][1] for day in returns),
            "snapshot_ref": dataset.snapshot_ref,
        }
        payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        folds.append(Fold.model_validate(payload))
    dataset_ref = store.put_bytes(canonical_json_bytes(dataset), media_type="application/json")
    payload = {
        "schema_version": "p3-fold-manifest-v1",
        "mode": mode,
        "folds": tuple(folds),
        "static_policy_digest": hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
        "dataset_evidence_ref": dataset_ref,
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return FoldManifest.model_validate(payload)


def build_holdout_fold_manifest(context: DatasetEvidence, holdout: DatasetEvidence, *, policy_digest: str) -> FoldManifest:
    """Project H1 metadata without opening daily rows or the execution buffer."""
    context=DatasetEvidence.model_validate(context)
    holdout=DatasetEvidence.model_validate(holdout)
    if (context.segment!='RESEARCH' or context.date_range.start!=date(2018,1,1)
        or context.date_range.end!=date(2025,8,31) or context.usable_rows!=2800
        or holdout.segment!='HOLDOUT' or holdout.date_range.start!=date(2025,9,1)
        or holdout.date_range.end!=date(2026,8,31) or holdout.usable_rows!=365):
        raise FoldError('holdout fold requires the exact research and H1 ranges')
    payload=dict(schema_version='p3-fold-v1',fold_id='H1',context_start='2024-11-04',
        decision_start='2025-08-31',decision_end='2026-08-30',return_end_range=holdout.date_range,
        return_count=365,decision_row_refs=(context.row_refs[-1],*holdout.row_refs[:-1]),
        return_row_refs=holdout.row_refs,snapshot_ref=holdout.snapshot_ref)
    fold=Fold.model_validate({**payload,'digest':hashlib.sha256(canonical_json_bytes(payload)).hexdigest()})
    raw=canonical_json_bytes(holdout)
    digest=hashlib.sha256(raw).hexdigest()
    ref=ArtifactRefV1(content_sha256=digest,size_bytes=len(raw),media_type='application/json',locator=digest+'.blob')
    value=dict(schema_version='p3-fold-manifest-v1',mode='HOLDOUT',folds=(fold,),
        static_policy_digest=policy_digest,dataset_evidence_ref=ref)
    return FoldManifest.model_validate({**value,'digest':hashlib.sha256(canonical_json_bytes(value)).hexdigest()})


__all__ = ["FoldError", "FoldSpec", "build_fold_manifest", "build_holdout_fold_manifest", "fold_specs"]

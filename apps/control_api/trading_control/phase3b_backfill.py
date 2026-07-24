from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .identity import sha256_file
from .normalization import ASSETS
from .phase3b_sources import (
    AssetLineageEvidence,
    CostSymbolEvidence,
    FieldEvidence,
    ProvenanceQuality,
    ReasonCode,
    extract_cost_session_symbols,
    extract_decision_field_evidence,
    make_asset_lineage_evidence,
)
from .real_import import build_real_plan


QUALITY_RANK = {
    ProvenanceQuality.UNKNOWN: 0,
    ProvenanceQuality.LEGACY_ESTIMATED: 1,
    ProvenanceQuality.DERIVED: 2,
    ProvenanceQuality.EXACT: 3,
}


class FieldAction(StrEnum):
    UPDATE = "UPDATE"
    UNCHANGED = "UNCHANGED"
    IGNORE = "IGNORE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class FieldDecision:
    action: FieldAction
    reason_code: ReasonCode | None = None


@dataclass(frozen=True, slots=True)
class Phase3BBackfillPlan:
    source_root: str
    inventory_hash: str
    decision_prices: tuple[FieldEvidence, ...]
    decision_snippets: tuple[FieldEvidence, ...]
    cost_symbols: tuple[CostSymbolEvidence, ...]
    asset_lineage: tuple[AssetLineageEvidence, ...]
    asset_ids: tuple[str, ...]

    def domain_size(self, domain: str) -> int:
        return {
            "decision-price": len(self.decision_prices),
            "decision-snippet": len(self.decision_snippets),
            "cost-symbols": len(self.cost_symbols),
            "asset-lineage": len(self.asset_lineage),
        }[domain]


def decide_field_action(
    *,
    stored_value: str | None,
    stored_quality: ProvenanceQuality,
    incoming_value: str | None,
    incoming_quality: ProvenanceQuality,
) -> FieldDecision:
    incoming_rank = QUALITY_RANK[incoming_quality]
    stored_rank = QUALITY_RANK[stored_quality]
    if incoming_rank > stored_rank:
        return FieldDecision(FieldAction.UPDATE)
    if incoming_rank < stored_rank:
        return FieldDecision(
            FieldAction.IGNORE, ReasonCode.LOWER_QUALITY_SOURCE_IGNORED
        )
    if stored_value == incoming_value:
        return FieldDecision(FieldAction.UNCHANGED)
    if incoming_quality is ProvenanceQuality.UNKNOWN:
        return FieldDecision(FieldAction.UNCHANGED)
    return FieldDecision(FieldAction.CONFLICT, ReasonCode.EQUAL_QUALITY_CONFLICT)


def build_phase3b_backfill_plan(root: Path) -> Phase3BBackfillPlan:
    resolved = root.expanduser().resolve()
    real_plan = build_real_plan(resolved)
    canonical_decision_ids = {item.decision_id for item in real_plan.decisions}
    fields = tuple(
        item
        for item in extract_decision_field_evidence(
            resolved / "memory" / "decisions.jsonl"
        )
        if item.entity_id in canonical_decision_ids
    )
    prices = tuple(item for item in fields if item.field_name == "price_at_decision")
    snippets = tuple(item for item in fields if item.field_name == "report_snippet")

    session_sources = sorted(
        (resolved / ".dexter" / "scratchpad").glob("*.jsonl"), reverse=True
    )[:20]
    costs = tuple(
        extract_cost_session_symbols(source, set(ASSETS)) for source in session_sources
    )

    asset_ids = {
        item.asset_id for item in (*real_plan.decisions, *real_plan.signals)
    }
    asset_ids.update(
        asset.asset_id for report in real_plan.reports for asset in report.assets
    )
    lineage: list[AssetLineageEvidence] = []
    registry_hash = sha256_file(resolved / "asset_registry.py")
    for index, (symbol, asset_id) in enumerate(sorted(ASSETS.items()), 1):
        if asset_id not in asset_ids:
            continue
        lineage.append(make_asset_lineage_evidence(
            asset_id=asset_id,
            symbol=symbol,
            source_type="ASSET_REGISTRY",
            source_path="asset_registry.py",
            source_hash=registry_hash,
            source_record_index=index,
            source_field=f"ASSET_REGISTRY[{symbol}]",
        ))
    for item in real_plan.decisions:
        lineage.append(make_asset_lineage_evidence(
            asset_id=item.asset_id,
            symbol=item.symbol,
            source_type="DECISION_JSONL",
            source_path=item.source_path,
            source_hash=item.source_hash,
            source_record_index=item.source_record_index,
            source_field="ticker",
        ))
    for report in real_plan.reports:
        for asset in report.assets:
            lineage.append(make_asset_lineage_evidence(
                asset_id=asset.asset_id,
                symbol=asset.symbol,
                source_type="MARKET_REPORT",
                source_path=report.source_path,
                source_hash=report.source_hash,
                source_record_index=asset.source_record_index,
                source_field=f"assets[{asset.source_record_index - 1}].symbol",
            ))
    for item in real_plan.signals:
        lineage.append(make_asset_lineage_evidence(
            asset_id=item.asset_id,
            symbol=item.symbol,
            source_type="SQLITE_SIGNAL",
            source_path="memory/trading.db#signals",
            source_hash=item.source_hash,
            source_record_index=item.source_record_index,
            source_field="symbol",
        ))
    for item in costs:
        for symbol in item.symbols:
            lineage.append(make_asset_lineage_evidence(
                asset_id=ASSETS[symbol],
                symbol=symbol,
                source_type="COST_SESSION",
                source_path=f".dexter/scratchpad/{item.source_path}",
                source_hash=item.source_hash,
                source_record_index=item.source_record_index,
                source_field="symbols",
            ))

    return Phase3BBackfillPlan(
        source_root=str(resolved),
        inventory_hash=real_plan.inventory_hash,
        decision_prices=prices,
        decision_snippets=snippets,
        cost_symbols=costs,
        asset_lineage=tuple(lineage),
        asset_ids=tuple(sorted(asset_ids)),
    )

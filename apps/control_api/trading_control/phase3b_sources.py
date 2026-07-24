from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any

from control_api.normalization import normalize_decision

from .identity import sha256_file
from .normalization import ASSETS


PHASE3B_NORMALIZATION_VERSION = "phase3b-v1"


class ProvenanceQuality(StrEnum):
    EXACT = "EXACT"
    DERIVED = "DERIVED"
    LEGACY_ESTIMATED = "LEGACY_ESTIMATED"
    UNKNOWN = "UNKNOWN"


class ReasonCode(StrEnum):
    SOURCE_FIELD_MISSING = "SOURCE_FIELD_MISSING"
    SOURCE_LINK_NOT_FOUND = "SOURCE_LINK_NOT_FOUND"
    AMBIGUOUS_SOURCE_MATCH = "AMBIGUOUS_SOURCE_MATCH"
    PRICE_TIMESTAMP_MISMATCH = "PRICE_TIMESTAMP_MISMATCH"
    SNIPPET_SOURCE_MISSING = "SNIPPET_SOURCE_MISSING"
    SYMBOL_EVIDENCE_MISSING = "SYMBOL_EVIDENCE_MISSING"
    UNKNOWN_ASSET = "UNKNOWN_ASSET"
    LOWER_QUALITY_SOURCE_IGNORED = "LOWER_QUALITY_SOURCE_IGNORED"
    EQUAL_QUALITY_CONFLICT = "EQUAL_QUALITY_CONFLICT"


def _identity(*parts: object) -> str:
    material = json.dumps(parts, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(material.encode()).hexdigest()


def _decimal_text(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


@dataclass(frozen=True, slots=True)
class FieldEvidence:
    identity: str
    entity_id: str
    field_name: str
    value: str | None
    quality: ProvenanceQuality
    source_type: str
    source_path: str
    source_hash: str
    source_record_index: int
    source_field: str
    normalization_version: str
    canonical_fingerprint: str
    reason_code: ReasonCode | None = None


@dataclass(frozen=True, slots=True)
class CostSymbolEvidence:
    identity: str
    session: str
    symbols: tuple[str, ...]
    unknown_symbols: tuple[str, ...]
    quality: ProvenanceQuality
    source_path: str
    source_hash: str
    source_record_index: int | None
    source_field: str | None
    normalization_version: str
    reason_code: ReasonCode | None = None


@dataclass(frozen=True, slots=True)
class AssetLineageEvidence:
    identity: str
    asset_id: str
    symbol: str
    source_type: str
    source_path: str
    source_hash: str
    source_record_index: int | None
    source_field: str
    normalization_version: str
    quality: ProvenanceQuality
    canonical_fingerprint: str


@dataclass(frozen=True, slots=True)
class Phase3BSourceAnalysis:
    inventory_hash: str
    decision_total: int
    price_counts: dict[str, int]
    snippet_counts: dict[str, int]
    price_source_breakdown: dict[str, int]
    snippet_source_breakdown: dict[str, int]
    cost_sessions: int
    cost_sessions_with_evidence: int
    cost_sessions_without_evidence: int
    cost_unknown_assets: tuple[str, ...]
    cost_symbol_links: int
    asset_count: int
    asset_lineage_rows_planned: int
    asset_source_files: int
    asset_lineage_breakdown: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "inventory_hash": self.inventory_hash,
            "decision_price": {
                "total": self.decision_total,
                "counts": self.price_counts,
                "source_breakdown": self.price_source_breakdown,
            },
            "decision_snippet": {
                "total": self.decision_total,
                "counts": self.snippet_counts,
                "source_breakdown": self.snippet_source_breakdown,
            },
            "cost_symbols": {
                "sessions": self.cost_sessions,
                "sessions_with_evidence": self.cost_sessions_with_evidence,
                "sessions_without_evidence": self.cost_sessions_without_evidence,
                "unknown_assets": list(self.cost_unknown_assets),
                "asset_links": self.cost_symbol_links,
            },
            "asset_lineage": {
                "assets": self.asset_count,
                "rows_planned": self.asset_lineage_rows_planned,
                "distinct_source_files": self.asset_source_files,
                "source_breakdown": self.asset_lineage_breakdown,
            },
        }


def extract_decision_field_evidence(source: Path) -> tuple[FieldEvidence, ...]:
    digest = sha256_file(source)
    evidence: list[FieldEvidence] = []
    with source.open(encoding="utf-8") as handle:
        for index, raw in enumerate(handle, 1):
            raw_line = raw.rstrip("\n")
            if not raw_line:
                continue
            value = json.loads(raw_line)
            if not isinstance(value, dict):
                continue
            decision = normalize_decision(value, line_number=index, raw_line=raw_line)
            price = _decimal_text(value.get("price_at_decision"))
            price_quality = (
                ProvenanceQuality.EXACT if price is not None else ProvenanceQuality.UNKNOWN
            )
            price_reason = None if price is not None else ReasonCode.SOURCE_FIELD_MISSING
            price_fingerprint = hashlib.sha256(
                (price if price is not None else "UNKNOWN").encode()
            ).hexdigest()
            evidence.append(FieldEvidence(
                identity=_identity(
                    "decision-price", decision.decision_id, digest, index,
                    PHASE3B_NORMALIZATION_VERSION,
                ),
                entity_id=decision.decision_id,
                field_name="price_at_decision",
                value=price,
                quality=price_quality,
                source_type="DECISION_JSONL",
                source_path="memory/decisions.jsonl",
                source_hash=digest,
                source_record_index=index,
                source_field="price_at_decision",
                normalization_version=PHASE3B_NORMALIZATION_VERSION,
                canonical_fingerprint=price_fingerprint,
                reason_code=price_reason,
            ))
            raw_snippet = value.get("report_snippet")
            snippet = raw_snippet if isinstance(raw_snippet, str) and raw_snippet else None
            snippet_quality = (
                ProvenanceQuality.EXACT
                if snippet is not None
                else ProvenanceQuality.UNKNOWN
            )
            snippet_reason = (
                None if snippet is not None else ReasonCode.SNIPPET_SOURCE_MISSING
            )
            snippet_fingerprint = hashlib.sha256(
                (snippet if snippet is not None else "UNKNOWN").encode()
            ).hexdigest()
            evidence.append(FieldEvidence(
                identity=_identity(
                    "decision-snippet", decision.decision_id, digest, index,
                    PHASE3B_NORMALIZATION_VERSION,
                ),
                entity_id=decision.decision_id,
                field_name="report_snippet",
                value=snippet,
                quality=snippet_quality,
                source_type="DECISION_JSONL",
                source_path="memory/decisions.jsonl",
                source_hash=digest,
                source_record_index=index,
                source_field="report_snippet",
                normalization_version=PHASE3B_NORMALIZATION_VERSION,
                canonical_fingerprint=snippet_fingerprint,
                reason_code=snippet_reason,
            ))
    return tuple(evidence)


def extract_cost_session_symbols(
    source: Path,
    allowed_symbols: set[str] | frozenset[str],
) -> CostSymbolEvidence:
    digest = sha256_file(source)
    allowed = {item.upper() for item in allowed_symbols}
    record_index: int | None = None
    raw_symbols: list[object] | None = None
    with source.open(encoding="utf-8") as handle:
        for index, raw in enumerate(handle, 1):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and isinstance(value.get("symbols"), list):
                record_index = index
                raw_symbols = value["symbols"]
                break
    if raw_symbols is None:
        return CostSymbolEvidence(
            identity=_identity(
                "cost-symbols", source.stem, digest, None,
                PHASE3B_NORMALIZATION_VERSION,
            ),
            session=source.stem,
            symbols=(),
            unknown_symbols=(),
            quality=ProvenanceQuality.UNKNOWN,
            source_path=source.name,
            source_hash=digest,
            source_record_index=None,
            source_field=None,
            normalization_version=PHASE3B_NORMALIZATION_VERSION,
            reason_code=ReasonCode.SYMBOL_EVIDENCE_MISSING,
        )
    candidates = {
        item.upper() for item in raw_symbols if isinstance(item, str) and item.strip()
    }
    known = tuple(sorted(candidates & allowed))
    unknown = tuple(sorted(candidates - allowed))
    return CostSymbolEvidence(
        identity=_identity(
            "cost-symbols", source.stem, digest, record_index,
            PHASE3B_NORMALIZATION_VERSION,
        ),
        session=source.stem,
        symbols=known,
        unknown_symbols=unknown,
        quality=ProvenanceQuality.EXACT,
        source_path=source.name,
        source_hash=digest,
        source_record_index=record_index,
        source_field="symbols",
        normalization_version=PHASE3B_NORMALIZATION_VERSION,
        reason_code=ReasonCode.UNKNOWN_ASSET if unknown else None,
    )


def make_asset_lineage_evidence(
    *,
    asset_id: str,
    symbol: str,
    source_type: str,
    source_path: str,
    source_hash: str,
    source_record_index: int | None,
    source_field: str,
) -> AssetLineageEvidence:
    canonical_symbol = symbol.upper()
    return AssetLineageEvidence(
        identity=_identity(
            "asset-lineage", asset_id, source_type, source_path, source_hash,
            source_record_index, source_field, PHASE3B_NORMALIZATION_VERSION,
        ),
        asset_id=asset_id,
        symbol=canonical_symbol,
        source_type=source_type,
        source_path=source_path,
        source_hash=source_hash,
        source_record_index=source_record_index,
        source_field=source_field,
        normalization_version=PHASE3B_NORMALIZATION_VERSION,
        quality=ProvenanceQuality.EXACT,
        canonical_fingerprint=hashlib.sha256(canonical_symbol.encode()).hexdigest(),
    )


def analyze_phase3b_sources(root: Path) -> Phase3BSourceAnalysis:
    # Import locally to keep the pure evidence helpers independent from the
    # real-data planner and avoid a module cycle during test collection.
    from .real_import import build_real_plan

    resolved = root.expanduser().resolve()
    plan = build_real_plan(resolved)
    canonical_decisions = {item.decision_id for item in plan.decisions}
    decision_evidence = tuple(
        item
        for item in extract_decision_field_evidence(
            resolved / "memory" / "decisions.jsonl"
        )
        if item.entity_id in canonical_decisions
    )
    price_evidence = tuple(
        item for item in decision_evidence if item.field_name == "price_at_decision"
    )
    snippet_evidence = tuple(
        item for item in decision_evidence if item.field_name == "report_snippet"
    )

    def quality_counts(items: tuple[FieldEvidence, ...]) -> dict[str, int]:
        return {
            quality.value: sum(item.quality is quality for item in items)
            for quality in ProvenanceQuality
        }

    session_sources = sorted(
        (resolved / ".dexter" / "scratchpad").glob("*.jsonl"), reverse=True
    )[:20]
    cost_evidence = tuple(
        extract_cost_session_symbols(source, set(ASSETS)) for source in session_sources
    )
    cost_symbol_links = sum(len(item.symbols) for item in cost_evidence)
    unknown_assets = tuple(sorted({
        symbol for item in cost_evidence for symbol in item.unknown_symbols
    }))

    canonical_asset_ids = {
        item.asset_id for item in (*plan.decisions, *plan.signals)
    }
    canonical_asset_ids.update(
        asset.asset_id for report in plan.reports for asset in report.assets
    )
    report_asset_rows = sum(len(report.assets) for report in plan.reports)
    lineage_breakdown = {
        "ASSET_REGISTRY": len(canonical_asset_ids),
        "DECISION_JSONL": len(plan.decisions),
        "MARKET_REPORT": report_asset_rows,
        "SQLITE_SIGNAL": len(plan.signals),
        "COST_SESSION": cost_symbol_links,
    }
    source_files = {
        "asset_registry.py",
        "memory/decisions.jsonl",
        "memory/trading.db#signals",
        *(report.source_path for report in plan.reports),
        *(
            f".dexter/scratchpad/{source.name}"
            for source in session_sources
        ),
    }
    return Phase3BSourceAnalysis(
        inventory_hash=plan.inventory_hash,
        decision_total=len(plan.decisions),
        price_counts=quality_counts(price_evidence),
        snippet_counts=quality_counts(snippet_evidence),
        price_source_breakdown={"memory/decisions.jsonl:price_at_decision": len(plan.decisions)},
        snippet_source_breakdown={
            "memory/decisions.jsonl:report_snippet": sum(
                item.quality is ProvenanceQuality.EXACT for item in snippet_evidence
            ),
            "missing_or_empty": sum(
                item.quality is ProvenanceQuality.UNKNOWN for item in snippet_evidence
            ),
        },
        cost_sessions=len(cost_evidence),
        cost_sessions_with_evidence=sum(
            item.quality is ProvenanceQuality.EXACT for item in cost_evidence
        ),
        cost_sessions_without_evidence=sum(
            item.quality is ProvenanceQuality.UNKNOWN for item in cost_evidence
        ),
        cost_unknown_assets=unknown_assets,
        cost_symbol_links=cost_symbol_links,
        asset_count=len(canonical_asset_ids),
        asset_lineage_rows_planned=sum(lineage_breakdown.values()),
        asset_source_files=len(source_files),
        asset_lineage_breakdown=lineage_breakdown,
    )

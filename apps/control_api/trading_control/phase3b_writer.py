from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import psycopg

from .db import DatabaseSettings, connect
from .normalization import ASSETS
from .phase3b_backfill import (
    FieldAction,
    Phase3BBackfillPlan,
    decide_field_action,
)
from .phase3b_sources import (
    PHASE3B_NORMALIZATION_VERSION,
    AssetLineageEvidence,
    FieldEvidence,
    ProvenanceQuality,
    ReasonCode,
)


DOMAINS = (
    "decision-price", "decision-snippet", "cost-symbols", "asset-lineage"
)


class Phase3BApplyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DomainResult:
    domain: str
    run_id: str
    seen: int
    updated: int
    unchanged: int
    unknown: int
    conflicted: int
    lineage_inserted: int


def phase3b_run_id(domain: str, inventory_hash: str) -> str:
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"trading-phase3b:{domain}:{inventory_hash}:{PHASE3B_NORMALIZATION_VERSION}",
    ))


def _chunks(items: tuple, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _decimal_text(value: object) -> str | None:
    if value is None:
        return None
    rendered = format(Decimal(value), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _event_id(run_id: str, entity_id: str, reason: ReasonCode, fingerprint: str) -> str:
    return hashlib.sha256(
        f"{run_id}:{entity_id}:{reason.value}:{fingerprint}".encode()
    ).hexdigest()


def _insert_event(
    connection: psycopg.Connection,
    *,
    run_id: str,
    domain: str,
    entity_id: str,
    reason: ReasonCode,
    evidence: FieldEvidence | AssetLineageEvidence | None = None,
    field_name: str | None = None,
    stored_fingerprint: str | None = None,
) -> None:
    incoming = evidence.canonical_fingerprint if evidence else "none"
    connection.execute(
        """
        INSERT INTO phase3b_backfill_events (
          event_id,backfill_run_id,domain,entity_id,field_name,reason_code,
          source_type,source_path,source_hash,source_record_index,
          incoming_fingerprint,stored_fingerprint
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (event_id) DO NOTHING
        """,
        (
            _event_id(run_id, entity_id, reason, incoming), run_id, domain,
            entity_id, field_name, reason.value,
            evidence.source_type if evidence else None,
            evidence.source_path if evidence else None,
            evidence.source_hash if evidence else None,
            evidence.source_record_index if evidence else None,
            incoming if evidence else None, stored_fingerprint,
        ),
    )


def _prepare_run(
    connection: psycopg.Connection,
    *,
    plan: Phase3BBackfillPlan,
    domain: str,
    code_commit: str,
    resume_run_id: str | None,
) -> tuple[str, bool]:
    run_id = phase3b_run_id(domain, plan.inventory_hash)
    existing = connection.execute(
        "SELECT status FROM phase3b_backfill_runs WHERE backfill_run_id=%s",
        (run_id,),
    ).fetchone()
    if existing is None:
        if resume_run_id is not None:
            raise Phase3BApplyError("resume run does not exist")
        connection.execute(
            """
            INSERT INTO phase3b_backfill_runs (
              backfill_run_id,domain,status,started_at,source_root,
              source_inventory_hash,code_commit,normalization_version
            ) VALUES (%s,%s,'RUNNING',now(),%s,%s,%s,%s)
            """,
            (
                run_id, domain, plan.source_root, plan.inventory_hash,
                code_commit, PHASE3B_NORMALIZATION_VERSION,
            ),
        )
        connection.commit()
        return run_id, False
    status = existing[0]
    if status == "COMPLETED":
        if resume_run_id is not None:
            raise Phase3BApplyError("completed run cannot be resumed")
        connection.rollback()
        return run_id, True
    if status != "FAILED" or resume_run_id != run_id:
        raise Phase3BApplyError("incomplete run requires matching --resume")
    connection.execute(
        "UPDATE phase3b_backfill_runs SET status='RUNNING',finished_at=NULL "
        "WHERE backfill_run_id=%s",
        (run_id,),
    )
    connection.commit()
    return run_id, False


def _apply_decision_domain(
    connection: psycopg.Connection,
    *,
    domain: str,
    run_id: str,
    items: tuple[FieldEvidence, ...],
    chunk_size: int,
) -> tuple[int, int, int, int, int]:
    updated = unchanged = unknown = conflicted = inserted = 0
    for chunk in _chunks(items, chunk_size):
        with connection.transaction():
            for item in chunk:
                exists = connection.execute(
                    "SELECT 1 FROM decision_field_lineage WHERE lineage_id=%s",
                    (item.identity,),
                ).fetchone()
                if exists:
                    unchanged += 1
                    continue
                row = connection.execute(
                    """
                    SELECT price_at_decision,price_provenance_quality,
                           report_snippet,snippet_provenance_quality
                    FROM decisions WHERE decision_id=%s
                    """,
                    (item.entity_id,),
                ).fetchone()
                if row is None:
                    _insert_event(
                        connection, run_id=run_id, domain=domain,
                        entity_id=item.entity_id,
                        reason=ReasonCode.SOURCE_LINK_NOT_FOUND, evidence=item,
                        field_name=item.field_name,
                    )
                    conflicted += 1
                    continue
                if item.field_name == "price_at_decision":
                    stored_value = _decimal_text(row[0])
                    stored_quality = ProvenanceQuality(row[1])
                else:
                    stored_value = row[2]
                    stored_quality = ProvenanceQuality(row[3])
                decision = decide_field_action(
                    stored_value=stored_value, stored_quality=stored_quality,
                    incoming_value=item.value, incoming_quality=item.quality,
                )
                connection.execute(
                    """
                    INSERT INTO decision_field_lineage (
                      lineage_id,decision_id,field_name,value_text,value_numeric,
                      provenance_quality,source_type,source_path,source_hash,
                      source_record_index,source_field,normalization_version,
                      canonical_fingerprint,reason_code,backfill_run_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        item.identity, item.entity_id, item.field_name,
                        item.value if item.field_name == "report_snippet" else None,
                        Decimal(item.value) if item.field_name == "price_at_decision" and item.value is not None else None,
                        item.quality.value, item.source_type, item.source_path,
                        item.source_hash, item.source_record_index, item.source_field,
                        item.normalization_version, item.canonical_fingerprint,
                        item.reason_code.value if item.reason_code else None, run_id,
                    ),
                )
                inserted += 1
                if item.quality is ProvenanceQuality.UNKNOWN:
                    unknown += 1
                if decision.action is FieldAction.UPDATE:
                    if item.field_name == "price_at_decision":
                        connection.execute(
                            "UPDATE decisions SET price_at_decision=%s,"
                            "price_provenance_quality=%s WHERE decision_id=%s",
                            (Decimal(item.value), item.quality.value, item.entity_id),
                        )
                    else:
                        connection.execute(
                            "UPDATE decisions SET report_snippet=%s,"
                            "snippet_provenance_quality=%s WHERE decision_id=%s",
                            (item.value, item.quality.value, item.entity_id),
                        )
                    updated += 1
                elif decision.action is FieldAction.CONFLICT:
                    _insert_event(
                        connection, run_id=run_id, domain=domain,
                        entity_id=item.entity_id,
                        reason=ReasonCode.EQUAL_QUALITY_CONFLICT, evidence=item,
                        field_name=item.field_name,
                        stored_fingerprint=hashlib.sha256(
                            (stored_value or "UNKNOWN").encode()
                        ).hexdigest(),
                    )
                    conflicted += 1
                elif decision.action is FieldAction.IGNORE:
                    _insert_event(
                        connection, run_id=run_id, domain=domain,
                        entity_id=item.entity_id,
                        reason=ReasonCode.LOWER_QUALITY_SOURCE_IGNORED,
                        evidence=item, field_name=item.field_name,
                    )
                    unchanged += 1
                else:
                    unchanged += int(item.quality is not ProvenanceQuality.UNKNOWN)
    return updated, unchanged, unknown, conflicted, inserted


def _apply_cost_symbols(
    connection: psycopg.Connection,
    *,
    run_id: str,
    plan: Phase3BBackfillPlan,
) -> tuple[int, int, int, int, int]:
    updated = unchanged = unknown = conflicted = inserted = 0
    for item in plan.cost_symbols:
        with connection.transaction():
            row = connection.execute(
                "SELECT cost_session_id,symbols_provenance_quality "
                "FROM cost_sessions WHERE session=%s",
                (item.session,),
            ).fetchone()
            if row is None:
                _insert_event(
                    connection, run_id=run_id, domain="cost-symbols",
                    entity_id=item.session, reason=ReasonCode.SOURCE_LINK_NOT_FOUND,
                )
                conflicted += 1
                continue
            session_id, quality_text = row
            stored = tuple(value[0] for value in connection.execute(
                "SELECT a.symbol FROM cost_session_assets csa JOIN assets a "
                "ON a.asset_id=csa.asset_id WHERE csa.cost_session_id=%s "
                "ORDER BY a.symbol",
                (session_id,),
            ).fetchall())
            if item.quality is ProvenanceQuality.UNKNOWN:
                unknown += 1
                continue
            quality = ProvenanceQuality(quality_text)
            if quality is ProvenanceQuality.EXACT and stored == item.symbols:
                unchanged += 1
                continue
            if quality is ProvenanceQuality.EXACT and stored != item.symbols:
                _insert_event(
                    connection, run_id=run_id, domain="cost-symbols",
                    entity_id=session_id, reason=ReasonCode.EQUAL_QUALITY_CONFLICT,
                )
                conflicted += 1
                continue
            for symbol in item.symbols:
                link_id = hashlib.sha256(
                    f"{item.identity}:{ASSETS[symbol]}".encode()
                ).hexdigest()
                created = connection.execute(
                    """
                    INSERT INTO cost_session_assets (
                      cost_session_asset_id,cost_session_id,asset_id,source_type,
                      source_path,source_hash,source_record_index,source_field,
                      provenance_quality,normalization_version,
                      canonical_fingerprint,backfill_run_id
                    ) VALUES (%s,%s,%s,'COST_SESSION',%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (cost_session_asset_id) DO NOTHING
                    RETURNING cost_session_asset_id
                    """,
                    (
                        link_id, session_id, ASSETS[symbol],
                        f".dexter/scratchpad/{item.source_path}", item.source_hash,
                        item.source_record_index, item.source_field,
                        item.quality.value, item.normalization_version,
                        hashlib.sha256(symbol.encode()).hexdigest(), run_id,
                    ),
                ).fetchone()
                inserted += int(created is not None)
            connection.execute(
                "UPDATE cost_sessions SET symbols_provenance_quality='EXACT',"
                "symbols_evidence_state='EVIDENCED' WHERE cost_session_id=%s",
                (session_id,),
            )
            updated += 1
    return updated, unchanged, unknown, conflicted, inserted


def _apply_asset_lineage(
    connection: psycopg.Connection,
    *,
    run_id: str,
    items: tuple[AssetLineageEvidence, ...],
    chunk_size: int,
) -> tuple[int, int, int, int, int]:
    unchanged = inserted = 0
    for chunk in _chunks(items, chunk_size):
        with connection.transaction():
            for item in chunk:
                created = connection.execute(
                    """
                    INSERT INTO asset_source_lineage (
                      asset_source_lineage_id,asset_id,source_type,source_path,
                      source_hash,source_record_index,source_field,
                      normalization_version,provenance_quality,
                      canonical_fingerprint,backfill_run_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (asset_source_lineage_id) DO NOTHING
                    RETURNING asset_source_lineage_id
                    """,
                    (
                        item.identity, item.asset_id, item.source_type,
                        item.source_path, item.source_hash,
                        item.source_record_index, item.source_field,
                        item.normalization_version, item.quality.value,
                        item.canonical_fingerprint, run_id,
                    ),
                ).fetchone()
                inserted += int(created is not None)
                unchanged += int(created is None)
    return 0, unchanged, 0, 0, inserted


def _apply_domain(
    plan: Phase3BBackfillPlan,
    settings: DatabaseSettings,
    *,
    domain: str,
    code_commit: str,
    resume_run_id: str | None,
    chunk_size: int,
) -> DomainResult:
    with psycopg.connect(settings.conninfo()) as connection:
        run_id, already_completed = _prepare_run(
            connection, plan=plan, domain=domain, code_commit=code_commit,
            resume_run_id=resume_run_id,
        )
        try:
            if domain == "decision-price":
                counts = _apply_decision_domain(
                    connection, domain=domain, run_id=run_id,
                    items=plan.decision_prices, chunk_size=chunk_size,
                )
            elif domain == "decision-snippet":
                counts = _apply_decision_domain(
                    connection, domain=domain, run_id=run_id,
                    items=plan.decision_snippets, chunk_size=chunk_size,
                )
            elif domain == "cost-symbols":
                counts = _apply_cost_symbols(connection, run_id=run_id, plan=plan)
            else:
                counts = _apply_asset_lineage(
                    connection, run_id=run_id, items=plan.asset_lineage,
                    chunk_size=chunk_size,
                )
            updated, unchanged, unknown, conflicted, lineage_inserted = counts
            if already_completed and (updated or conflicted or lineage_inserted):
                raise Phase3BApplyError("completed run no longer matches stored state")
            if not already_completed:
                connection.execute(
                    """
                    UPDATE phase3b_backfill_runs SET status='COMPLETED',
                      finished_at=now(),rows_seen=%s,rows_updated=%s,
                      rows_unchanged=%s,rows_unknown=%s,rows_conflicted=%s
                    WHERE backfill_run_id=%s
                    """,
                    (
                        plan.domain_size(domain), updated, unchanged, unknown,
                        conflicted, run_id,
                    ),
                )
                connection.commit()
            return DomainResult(
                domain, run_id, plan.domain_size(domain), updated, unchanged,
                unknown, conflicted, lineage_inserted,
            )
        except Exception as error:
            connection.rollback()
            if not already_completed:
                connection.execute(
                    "UPDATE phase3b_backfill_runs SET status='FAILED',"
                    "finished_at=now() WHERE backfill_run_id=%s",
                    (run_id,),
                )
                connection.commit()
            if isinstance(error, Phase3BApplyError):
                raise
            raise Phase3BApplyError(f"{domain} backfill failed: {type(error).__name__}") from error


def apply_phase3b_plan(
    plan: Phase3BBackfillPlan,
    settings: DatabaseSettings,
    *,
    domains: tuple[str, ...] = DOMAINS,
    apply: bool = False,
    code_commit: str,
    resume_run_id: str | None = None,
    chunk_size: int = 500,
) -> dict[str, DomainResult]:
    invalid = set(domains) - set(DOMAINS)
    if invalid:
        raise ValueError(f"unsupported Phase 3B domain: {sorted(invalid)}")
    if resume_run_id is not None and len(domains) != 1:
        raise Phase3BApplyError("resume requires exactly one domain")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if not apply:
        return {
            domain: DomainResult(
                domain, phase3b_run_id(domain, plan.inventory_hash),
                plan.domain_size(domain), 0, 0,
                (
                    sum(item.quality is ProvenanceQuality.UNKNOWN for item in plan.decision_prices)
                    if domain == "decision-price" else
                    sum(item.quality is ProvenanceQuality.UNKNOWN for item in plan.decision_snippets)
                    if domain == "decision-snippet" else
                    sum(item.quality is ProvenanceQuality.UNKNOWN for item in plan.cost_symbols)
                    if domain == "cost-symbols" else 0
                ),
                0, 0,
            )
            for domain in domains
        }
    return {
        domain: _apply_domain(
            plan, settings, domain=domain, code_commit=code_commit,
            resume_run_id=resume_run_id, chunk_size=chunk_size,
        )
        for domain in domains
    }


def inspect_phase3b_dry_run(
    plan: Phase3BBackfillPlan,
    settings: DatabaseSettings,
) -> dict[str, dict[str, int]]:
    with connect(settings, read_only=True) as connection:
        decision_rows = {
            row[0]: row[1:]
            for row in connection.execute(
                "SELECT decision_id,price_at_decision,price_provenance_quality,"
                "report_snippet,snippet_provenance_quality FROM decisions"
            ).fetchall()
        }
        price = {
            "total": len(plan.decision_prices), "already_exact": 0,
            "backfillable_exact": 0, "backfillable_derived": 0,
            "unknown": 0, "conflicts": 0,
        }
        for item in plan.decision_prices:
            row = decision_rows.get(item.entity_id)
            if row is None:
                price["conflicts"] += 1
                continue
            stored_value = _decimal_text(row[0])
            stored_quality = ProvenanceQuality(row[1])
            decision = decide_field_action(
                stored_value=stored_value, stored_quality=stored_quality,
                incoming_value=item.value, incoming_quality=item.quality,
            )
            if item.quality is ProvenanceQuality.UNKNOWN:
                price["unknown"] += 1
            elif decision.action is FieldAction.CONFLICT:
                price["conflicts"] += 1
            elif decision.action is FieldAction.UPDATE:
                key = (
                    "backfillable_exact"
                    if item.quality is ProvenanceQuality.EXACT
                    else "backfillable_derived"
                )
                price[key] += 1
            elif stored_quality is ProvenanceQuality.EXACT and stored_value == item.value:
                price["already_exact"] += 1

        snippet = {
            "total": len(plan.decision_snippets), "already_populated": 0,
            "backfillable": 0, "unknown": 0, "conflicts": 0,
        }
        for item in plan.decision_snippets:
            row = decision_rows.get(item.entity_id)
            if row is None:
                snippet["conflicts"] += 1
                continue
            stored_value = row[2]
            stored_quality = ProvenanceQuality(row[3])
            decision = decide_field_action(
                stored_value=stored_value, stored_quality=stored_quality,
                incoming_value=item.value, incoming_quality=item.quality,
            )
            if item.quality is ProvenanceQuality.UNKNOWN:
                snippet["unknown"] += 1
            elif decision.action is FieldAction.CONFLICT:
                snippet["conflicts"] += 1
            elif decision.action is FieldAction.UPDATE:
                snippet["backfillable"] += 1
            elif stored_value is not None:
                snippet["already_populated"] += 1

        cost_rows = {
            row[0]: (row[1], tuple(row[2] or ()))
            for row in connection.execute(
                """
                SELECT cs.session,cs.symbols_provenance_quality,
                  array_agg(a.symbol ORDER BY a.symbol)
                    FILTER (WHERE a.symbol IS NOT NULL)
                FROM cost_sessions cs
                LEFT JOIN cost_session_assets csa
                  ON csa.cost_session_id=cs.cost_session_id
                LEFT JOIN assets a ON a.asset_id=csa.asset_id
                GROUP BY cs.cost_session_id,cs.session,cs.symbols_provenance_quality
                """
            ).fetchall()
        }
        cost_conflicts = 0
        for item in plan.cost_symbols:
            stored = cost_rows.get(item.session)
            if stored is None:
                cost_conflicts += 1
            elif stored[0] == "EXACT" and stored[1] != item.symbols:
                cost_conflicts += 1

        existing_lineage = {
            row[0] for row in connection.execute(
                "SELECT asset_source_lineage_id FROM asset_source_lineage"
            ).fetchall()
        }
        existing_assets = {
            row[0] for row in connection.execute("SELECT asset_id FROM assets").fetchall()
        }
    return {
        "decision-price": price,
        "decision-snippet": snippet,
        "cost-symbols": {
            "sessions": len(plan.cost_symbols),
            "sessions_with_evidenced_symbols": sum(
                item.quality is ProvenanceQuality.EXACT for item in plan.cost_symbols
            ),
            "sessions_with_no_evidence": sum(
                item.quality is ProvenanceQuality.UNKNOWN for item in plan.cost_symbols
            ),
            "unknown_assets": sum(len(item.unknown_symbols) for item in plan.cost_symbols),
            "conflicts": cost_conflicts,
        },
        "asset-lineage": {
            "assets": len(plan.asset_ids),
            "source_lineage_rows_planned": sum(
                item.identity not in existing_lineage for item in plan.asset_lineage
            ),
            "distinct_source_files": len({
                item.source_path for item in plan.asset_lineage
            }),
            "conflicts": sum(asset_id not in existing_assets for asset_id in plan.asset_ids),
        },
    }

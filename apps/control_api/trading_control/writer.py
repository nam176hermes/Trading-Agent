from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg

from .db import DatabaseSettings
from .identity import chunk_ranges


class ApplyRejected(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ApplyRecord:
    record_id: str
    asset_id: str
    symbol: str
    action: str
    confidence: float
    as_of: datetime
    source_path: str
    source_hash: str
    source_record_index: int
    payload_hash: str
    canonical_fingerprint: str
    audit_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannedQuarantine:
    source_path: str
    source_hash: str
    source_record_index: int
    payload_hash: str
    legacy_value: str | None
    error_code: str
    sanitized_message: str


@dataclass(frozen=True, slots=True)
class ApplyReportAsset:
    snapshot_id: str
    asset_id: str
    symbol: str
    price: float
    action: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ApplyReport:
    report_id: str
    as_of: datetime
    source_path: str
    source_hash: str
    canonical_fingerprint: str
    assets: tuple[ApplyReportAsset, ...]


@dataclass(frozen=True, slots=True)
class ApplyPlan:
    source_root: str
    source_inventory_hash: str
    normalization_version: str
    schema_revision: str
    code_commit: str
    records: tuple[ApplyRecord, ...]
    quarantines: tuple[PlannedQuarantine, ...]
    reports: tuple[ApplyReport, ...] = ()


@dataclass(frozen=True, slots=True)
class ApplyResult:
    run_id: str
    inserted: int
    skipped: int
    updated: int
    invalid: int


def _safe_legacy_value(value: str | None) -> str | None:
    return value if value in {"WATCH", "WATCH FOR EXIT"} else None


def _chunk_hash(items: list[ApplyRecord | PlannedQuarantine]) -> str:
    material = "\n".join(item.payload_hash for item in items)
    return hashlib.sha256(material.encode()).hexdigest()


def _insert_error(
    connection: psycopg.Connection,
    *,
    run_id: str,
    plan: ApplyPlan,
    error: PlannedQuarantine,
) -> None:
    connection.execute(
        """
        INSERT INTO migration_errors (
          error_id, run_id, source_path, source_hash, source_record_index,
          error_code, error_message_sanitized, payload_hash, legacy_value,
          normalization_version
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            str(uuid.uuid4()), run_id, error.source_path, error.source_hash,
            error.source_record_index, error.error_code,
            "legacy record rejected by strict normalization",
            error.payload_hash, _safe_legacy_value(error.legacy_value),
            plan.normalization_version,
        ),
    )


def apply_plan(
    plan: ApplyPlan,
    settings: DatabaseSettings,
    *,
    apply: bool,
    resume_run_id: str | None = None,
    fail_chunk_first_index: int | None = None,
    fail_after_records_in_chunk: int | None = None,
) -> ApplyResult:
    if apply is not True:
        raise ApplyRejected("explicit apply=True is required")
    connection = psycopg.connect(settings.conninfo(), autocommit=True)
    inserted = skipped = invalid = 0
    try:
        if resume_run_id:
            row = connection.execute(
                "SELECT source_root, source_inventory_hash, normalization_version, "
                "schema_version FROM migration_runs WHERE run_id=%s",
                (resume_run_id,),
            ).fetchone()
            if row is None or tuple(row) != (
                plan.source_root, plan.source_inventory_hash,
                plan.normalization_version, plan.schema_revision,
            ):
                raise ApplyRejected("resume metadata does not match the original run")
            run_id = resume_run_id
            connection.execute("UPDATE migration_runs SET status='RUNNING', finished_at=NULL WHERE run_id=%s", (run_id,))
        else:
            run_id = str(uuid.uuid4())
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO migration_runs (
                      run_id, started_at, status, code_commit, schema_version,
                      normalization_version, source_root, source_inventory_hash
                    ) VALUES (%s,now(),'RUNNING',%s,%s,%s,%s,%s)
                    """,
                    (run_id, plan.code_commit, plan.schema_revision,
                     plan.normalization_version, plan.source_root,
                     plan.source_inventory_hash),
                )
                connection.execute(
                    """
                    INSERT INTO migration_source_files (
                      source_file_id, run_id, domain, source_path, source_hash,
                      source_size, status
                    ) VALUES (%s,%s,'decisions','memory/decisions.jsonl',%s,0,'RUNNING')
                    """,
                    (str(uuid.uuid4()), run_id,
                     plan.records[0].source_hash if plan.records else plan.quarantines[0].source_hash),
                )

        for report in plan.reports:
            checkpoint = connection.execute(
                "SELECT status FROM migration_source_chunks WHERE run_id=%s AND "
                "source_hash=%s AND domain='reports' AND first_record_index=1",
                (run_id, report.source_hash),
            ).fetchone()
            if checkpoint and checkpoint[0] == "COMMITTED":
                continue
            with connection.transaction():
                report_inserted = report_skipped = report_invalid = 0
                source_file_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO migration_source_files (
                      source_file_id,run_id,domain,source_path,source_hash,
                      source_size,status
                    ) VALUES (%s,%s,'reports',%s,%s,0,'RUNNING')
                    """,
                    (
                        source_file_id, run_id, report.source_path,
                        report.source_hash,
                    ),
                )
                existing_report = connection.execute(
                    "SELECT source_record_fingerprint FROM market_reports "
                    "WHERE source_hash=%s AND normalization_version=%s",
                    (report.source_hash, plan.normalization_version),
                ).fetchone()
                if existing_report and existing_report[0] != report.canonical_fingerprint:
                    _insert_error(
                        connection,
                        run_id=run_id,
                        plan=plan,
                        error=PlannedQuarantine(
                            report.source_path, report.source_hash, 1,
                            report.source_hash, None, "DUPLICATE_SOURCE_RECORD",
                            "report source identity has different canonical content",
                        ),
                    )
                    invalid += 1
                    report_invalid = 1
                elif not existing_report:
                    connection.execute(
                        """
                        INSERT INTO market_reports (
                          report_id,as_of,freshness_status,schema_version,
                          normalization_version,provenance_quality,source_type,
                          source_path,source_hash,source_record_index,
                          source_record_fingerprint,event_time,known_at,ingested_at,
                          migration_run_id
                        ) VALUES (%s,%s,'STALE','1.0.0',%s,'LEGACY_ESTIMATED',
                          'JSON',%s,%s,1,%s,%s,NULL,now(),%s)
                        """,
                        (
                            report.report_id, report.as_of,
                            plan.normalization_version, report.source_path,
                            report.source_hash, report.canonical_fingerprint,
                            report.as_of, run_id,
                        ),
                    )
                    for asset in report.assets:
                        asset_class = "CRYPTO" if asset.asset_id.startswith("crypto:") else "EQUITY"
                        quote = "USDT" if asset_class == "CRYPTO" else "USD"
                        instrument = "spot" if asset_class == "CRYPTO" else "stock"
                        connection.execute(
                            """
                            INSERT INTO assets (
                              asset_id,symbol,asset_class,instrument_type,
                              base_currency,quote_currency,status,schema_version
                            ) VALUES (%s,%s,%s,%s,%s,%s,'DISABLED','1.0.0')
                            ON CONFLICT (asset_id) DO NOTHING
                            """,
                            (
                                asset.asset_id, asset.symbol, asset_class,
                                instrument, asset.symbol, quote,
                            ),
                        )
                        connection.execute(
                            """
                            INSERT INTO market_asset_snapshots (
                              snapshot_id,report_id,asset_id,price,action,confidence,
                              schema_version,normalization_version,provenance_quality,
                              source_hash,source_record_index,ingested_at,migration_run_id
                            ) VALUES (%s,%s,%s,%s,%s,%s,'1.0.0',%s,
                              'LEGACY_ESTIMATED',%s,1,now(),%s)
                            """,
                            (
                                asset.snapshot_id, report.report_id, asset.asset_id,
                                asset.price, asset.action, asset.confidence,
                                plan.normalization_version, report.source_hash, run_id,
                            ),
                        )
                    report_inserted = 1
                else:
                    report_skipped = 1
                connection.execute(
                    """
                    INSERT INTO migration_source_chunks (
                      chunk_id,run_id,source_hash,domain,first_record_index,
                      last_record_index,chunk_hash,status,committed_at
                    ) VALUES (%s,%s,%s,'reports',1,1,%s,'COMMITTED',now())
                    ON CONFLICT (run_id,source_hash,domain,first_record_index)
                    DO UPDATE SET status='COMMITTED', committed_at=now()
                    """,
                    (
                        str(uuid.uuid4()), run_id, report.source_hash,
                        report.canonical_fingerprint,
                    ),
                )
                connection.execute(
                    """
                    UPDATE migration_source_files SET status='COMPLETED',
                      records_seen=1,records_inserted=%s,records_skipped=%s,
                      records_invalid=%s WHERE source_file_id=%s
                    """,
                    (
                        report_inserted, report_skipped, report_invalid,
                        source_file_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE migration_runs SET records_seen=records_seen+1,
                      records_inserted=records_inserted+%s,
                      records_skipped=records_skipped+%s,
                      records_invalid=records_invalid+%s WHERE run_id=%s
                    """,
                    (
                        report_inserted, report_skipped, report_invalid, run_id,
                    ),
                )

        by_index: dict[int, ApplyRecord | PlannedQuarantine] = {
            item.source_record_index: item for item in (*plan.records, *plan.quarantines)
        }
        total = max(by_index, default=0)
        for first, last in chunk_ranges(total):
            items = [by_index[index] for index in range(first, last + 1) if index in by_index]
            if not items:
                continue
            existing_status = connection.execute(
                "SELECT status FROM migration_source_chunks WHERE run_id=%s AND "
                "source_hash=%s AND domain='decisions' AND first_record_index=%s",
                (run_id, items[0].source_hash, first),
            ).fetchone()
            if existing_status and existing_status[0] == "COMMITTED":
                continue
            try:
                with connection.transaction():
                    if fail_chunk_first_index == first:
                        raise RuntimeError("injected database failure")
                    chunk_inserted = chunk_skipped = chunk_invalid = 0
                    for processed, item in enumerate(items, 1):
                        if isinstance(item, PlannedQuarantine):
                            _insert_error(connection, run_id=run_id, plan=plan, error=item)
                            chunk_invalid += 1
                            continue
                        connection.execute(
                            """
                            INSERT INTO assets (
                              asset_id,symbol,asset_class,instrument_type,
                              base_currency,quote_currency,status,schema_version
                            ) VALUES (%s,%s,'CRYPTO','spot',%s,'USDT','DISABLED','1.0.0')
                            ON CONFLICT (asset_id) DO NOTHING
                            """,
                            (item.asset_id, item.symbol, item.symbol),
                        )
                        existing = connection.execute(
                            """
                            SELECT decision_id, source_record_fingerprint FROM decisions
                            WHERE source_hash=%s AND source_record_index=%s
                              AND normalization_version=%s
                            """,
                            (item.source_hash, item.source_record_index, plan.normalization_version),
                        ).fetchone()
                        if existing:
                            if existing[1] == item.canonical_fingerprint:
                                chunk_skipped += 1
                            else:
                                _insert_error(
                                    connection, run_id=run_id, plan=plan,
                                    error=PlannedQuarantine(
                                        item.source_path, item.source_hash,
                                        item.source_record_index, item.payload_hash,
                                        None, "DUPLICATE_SOURCE_RECORD",
                                        "source identity has different canonical content",
                                    ),
                                )
                                chunk_invalid += 1
                        else:
                            connection.execute(
                                """
                                INSERT INTO decisions (
                                  decision_id,asset_id,action,confidence,as_of,
                                  schema_version,normalization_version,
                                  provenance_quality,source_type,source_path,
                                  source_hash,source_record_index,
                                  source_record_fingerprint,event_time,known_at,
                                  ingested_at,migration_run_id
                                ) VALUES (%s,%s,%s,%s,%s,'1.0.0',%s,
                                  'LEGACY_ESTIMATED','JSONL',%s,%s,%s,%s,%s,NULL,
                                  now(),%s)
                                """,
                                (item.record_id, item.asset_id, item.action,
                                 item.confidence, item.as_of,
                                 plan.normalization_version, item.source_path,
                                 item.source_hash, item.source_record_index,
                                 item.canonical_fingerprint, item.as_of, run_id),
                            )
                            chunk_inserted += 1
                        for code in item.audit_codes:
                            connection.execute(
                                """
                                INSERT INTO audit_events (
                                  audit_event_id,run_id,event_code,domain,source_path,
                                  source_record_index,source_record_fingerprint,
                                  normalization_version,details
                                ) VALUES (%s,%s,%s,'decisions',%s,%s,%s,%s,%s)
                                """,
                                (str(uuid.uuid4()), run_id, code, item.source_path,
                                 item.source_record_index, item.canonical_fingerprint,
                                 plan.normalization_version, json.dumps({"to": item.action})),
                            )
                        if fail_after_records_in_chunk == processed:
                            raise RuntimeError("injected mid-chunk database failure")
                    connection.execute(
                        """
                        INSERT INTO migration_source_chunks (
                          chunk_id,run_id,source_hash,domain,first_record_index,
                          last_record_index,chunk_hash,status,committed_at
                        ) VALUES (%s,%s,%s,'decisions',%s,%s,%s,'COMMITTED',now())
                        ON CONFLICT (run_id,source_hash,domain,first_record_index)
                        DO UPDATE SET status='COMMITTED', committed_at=now(),
                          chunk_hash=excluded.chunk_hash,
                          last_record_index=excluded.last_record_index
                        """,
                        (str(uuid.uuid4()), run_id, items[0].source_hash, first,
                         last, _chunk_hash(items)),
                    )
                    connection.execute(
                        """
                        UPDATE migration_runs SET records_seen=records_seen+%s,
                          records_inserted=records_inserted+%s,
                          records_skipped=records_skipped+%s,
                          records_invalid=records_invalid+%s
                        WHERE run_id=%s
                        """,
                        (len(items), chunk_inserted, chunk_skipped, chunk_invalid, run_id),
                    )
                    inserted += chunk_inserted
                    skipped += chunk_skipped
                    invalid += chunk_invalid
            except Exception:
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO migration_source_chunks (
                          chunk_id,run_id,source_hash,domain,first_record_index,
                          last_record_index,chunk_hash,status
                        ) VALUES (%s,%s,%s,'decisions',%s,%s,%s,'FAILED')
                        ON CONFLICT (run_id,source_hash,domain,first_record_index)
                        DO UPDATE SET status='FAILED', committed_at=NULL
                        """,
                        (str(uuid.uuid4()), run_id, items[0].source_hash, first,
                         last, _chunk_hash(items)),
                    )
                    connection.execute("UPDATE migration_runs SET status='FAILED', finished_at=now() WHERE run_id=%s", (run_id,))
                raise
        with connection.transaction():
            connection.execute("UPDATE migration_runs SET status='COMPLETED', finished_at=now() WHERE run_id=%s", (run_id,))
            connection.execute("UPDATE migration_source_files SET status='COMPLETED' WHERE run_id=%s", (run_id,))
        return ApplyResult(run_id, inserted, skipped, 0, invalid)
    finally:
        connection.close()

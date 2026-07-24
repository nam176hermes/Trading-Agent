from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from control_api.normalization import (
    normalize_asset,
    normalize_decision as normalize_contract_decision,
    parse_datetime,
)
from control_api.repositories.capabilities import CAPABILITIES
from control_api.repositories.costs import LegacyCostRepository

from . import NORMALIZATION_VERSION
from .identity import record_key, sha256_bytes, sha256_file
from .normalization import ASSETS, MigrationValidationError, normalize_decision
from .planner import plan_migration
from .db import DatabaseSettings
from .writer import ApplyRejected, ApplyResult

import psycopg


@dataclass(frozen=True, slots=True)
class RealQuarantine:
    source_path: str
    source_hash: str
    source_record_index: int | None
    payload_hash: str
    legacy_value: str | None
    error_code: str


@dataclass(frozen=True, slots=True)
class RealReportAsset:
    snapshot_id: str
    asset_id: str
    symbol: str
    price: float
    action: str
    confidence: float
    canonical_json: str
    source_record_index: int


@dataclass(frozen=True, slots=True)
class RealReport:
    report_id: str
    as_of: datetime
    source_path: str
    source_hash: str
    canonical_fingerprint: str
    assets: tuple[RealReportAsset, ...]


@dataclass(frozen=True, slots=True)
class RealDecision:
    decision_id: str
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
    canonical_json: str
    audit_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RealSignal:
    signal_id: str
    asset_id: str
    symbol: str
    action: str
    confidence: float
    as_of: datetime
    source_hash: str
    source_record_index: int
    canonical_fingerprint: str
    model_id: str | None
    model_version: str | None


@dataclass(frozen=True, slots=True)
class RealCapability:
    evidence_id: str
    capability_id: str
    name: str
    source_hash: str


@dataclass(frozen=True, slots=True)
class RealCostSession:
    cost_session_id: str
    session: str
    symbols: tuple[str, ...]
    steps: int
    llm_calls: int
    tool_calls: int
    decisions: int
    estimated_cost: float
    source_path: str
    source_hash: str


@dataclass(frozen=True, slots=True)
class RealCostSummary:
    cost_summary_id: str
    evidence_quality: str
    total_sessions: int
    total_llm_calls: int | None
    total_tool_calls: int | None
    amount: float | None
    source_hash: str


@dataclass(frozen=True, slots=True)
class RealApplyPlan:
    source_root: str
    inventory_hash: str
    planner_manifest_hash: str
    reports: tuple[RealReport, ...]
    invalid_reports: tuple[RealQuarantine, ...]
    decisions: tuple[RealDecision, ...]
    invalid_decisions: tuple[RealQuarantine, ...]
    signals: tuple[RealSignal, ...]
    capabilities: tuple[RealCapability, ...]
    cost_summary: RealCostSummary
    cost_sessions: tuple[RealCostSession, ...]

    @property
    def domain_counts(self) -> dict[str, int]:
        asset_ids = {
            item.asset_id
            for item in (*self.decisions, *self.signals)
        }
        asset_ids.update(asset.asset_id for report in self.reports for asset in report.assets)
        return {
            "assets": len(asset_ids),
            "market_reports": len(self.reports),
            "market_asset_snapshots": sum(len(item.assets) for item in self.reports),
            "decisions": len(self.decisions),
            "signals": len(self.signals),
            "capability_evidence": len(self.capabilities),
            "cost_summaries": 1,
            "cost_sessions": len(self.cost_sessions),
        }

    @property
    def canonical_total(self) -> int:
        return sum(self.domain_counts.values())

    @property
    def quarantine_total(self) -> int:
        return len(self.invalid_reports) + len(self.invalid_decisions)


def _inventory_digest(paths: list[Path]) -> str:
    material = b"".join(
        f"{sha256_file(path)}  {path}\n".encode() for path in sorted(paths)
    )
    return sha256_bytes(material)


def _sqlite_export_hash(connection: sqlite3.Connection, tables: tuple[str, ...]) -> str:
    def sqlite_cli_text(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            rendered = format(value, ".15g")
            return rendered if "." in rendered or "e" in rendered.lower() else rendered + ".0"
        return str(value)

    output: list[str] = []
    for table in tables:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY id"):
            output.append("|".join(sqlite_cli_text(value) for value in row) + "\n")
    return sha256_bytes("".join(output).encode())


def _combined_inventory(root: Path, signal_hash: str) -> tuple[str, str]:
    report_hash = _inventory_digest(list((root / "reports").glob("report_*.json")))
    cost_hash = _inventory_digest(list((root / ".dexter" / "scratchpad").glob("*.jsonl")))
    asset_hash = sha256_file(root / "asset_registry.py")
    decision_hash = sha256_file(root / "memory" / "decisions.jsonl")
    material = (
        f"asset_registry={asset_hash}\n"
        f"decisions={decision_hash}\n"
        f"market_reports={report_hash}\n"
        f"scratchpad_cost_sources={cost_hash}\n"
        f"sqlite_signals={signal_hash}\n"
    )
    return sha256_bytes(material.encode()), cost_hash


def _aware_sqlite_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("signal timestamp is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def build_real_plan(source_root: Path) -> RealApplyPlan:
    root = source_root.expanduser().resolve()
    planned = plan_migration(root)
    reports: list[RealReport] = []
    invalid_reports: list[RealQuarantine] = []
    for path in sorted((root / "reports").glob("report_*.json")):
        digest = sha256_file(path)
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        try:
            value = json.loads(payload)
            if not isinstance(value, dict) or not isinstance(value.get("assets"), list):
                raise ValueError("market report requires assets")
            as_of = parse_datetime(value.get("timestamp") or value.get("as_of"))
            normalized_assets = [normalize_asset(item) for item in value["assets"]]
            if any(item.symbol not in ASSETS for item in normalized_assets):
                raise ValueError("market asset is not registered")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            error = next(item for item in planned.errors if item.source_path == relative)
            invalid_reports.append(
                RealQuarantine(relative, digest, None, digest, None, error.code)
            )
            continue
        report_id = "report_" + hashlib.sha256(
            f"{path.name}:{as_of.isoformat()}".encode()
        ).hexdigest()[:24]
        assets: list[RealReportAsset] = []
        for index, item in enumerate(normalized_assets, 1):
            canonical_json = item.model_dump_json()
            assets.append(
                RealReportAsset(
                    snapshot_id=record_key(
                        "market_asset_snapshots", digest, index, NORMALIZATION_VERSION
                    ),
                    asset_id=ASSETS[item.symbol],
                    symbol=item.symbol,
                    price=item.current_price,
                    action=item.suggestion.value,
                    confidence={"low": 0.25, "medium": 0.5, "high": 0.75}[
                        item.confidence.value
                    ],
                    canonical_json=canonical_json,
                    source_record_index=index,
                )
            )
        fingerprint = sha256_bytes(
            json.dumps(
                {
                    "as_of": as_of.isoformat(),
                    "assets": [json.loads(item.canonical_json) for item in assets],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        reports.append(
            RealReport(report_id, as_of, relative, digest, fingerprint, tuple(assets))
        )

    decision_path = root / "memory" / "decisions.jsonl"
    decision_hash = sha256_file(decision_path)
    decisions: list[RealDecision] = []
    invalid_decisions: list[RealQuarantine] = []
    with decision_path.open(encoding="utf-8") as handle:
        for index, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            payload_hash = sha256_bytes(raw.encode())
            value = json.loads(raw)
            try:
                strict = normalize_decision(
                    value, source_hash=decision_hash, record_index=index
                )
            except MigrationValidationError as error:
                legacy = value.get("suggestion")
                invalid_decisions.append(
                    RealQuarantine(
                        "memory/decisions.jsonl",
                        decision_hash,
                        index,
                        payload_hash,
                        legacy if legacy in {"WATCH", "WATCH FOR EXIT"} else None,
                        error.code,
                    )
                )
                continue
            contract = normalize_contract_decision(
                value, line_number=index, raw_line=raw.rstrip("\n")
            )
            decisions.append(
                RealDecision(
                    contract.decision_id,
                    strict.asset_id,
                    strict.symbol,
                    strict.action,
                    strict.confidence,
                    strict.as_of,
                    "memory/decisions.jsonl",
                    decision_hash,
                    index,
                    payload_hash,
                    strict.canonical_fingerprint,
                    contract.model_dump_json(),
                    tuple(item.code for item in strict.audit_events),
                )
            )

    database = root / "memory" / "trading.db"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        signal_hash = _sqlite_export_hash(connection, ("signals",))
        columns = [row[1] for row in connection.execute("PRAGMA table_info(signals)")]
        signals: list[RealSignal] = []
        for row in connection.execute("SELECT * FROM signals ORDER BY id"):
            value = dict(zip(columns, row, strict=True))
            index = int(value["id"])
            symbol = str(value["symbol"]).upper()
            canonical = {
                "asset_id": ASSETS[symbol],
                "action": str(value["direction"]),
                "confidence": float(value["confidence"] or 0.0),
                "as_of": _aware_sqlite_timestamp(value["created_at"]).isoformat(),
                "model_id": value.get("strategy"),
                "model_version": value.get("source"),
            }
            signals.append(
                RealSignal(
                    record_key("signals", signal_hash, index, NORMALIZATION_VERSION),
                    canonical["asset_id"],
                    symbol,
                    canonical["action"],
                    canonical["confidence"],
                    _aware_sqlite_timestamp(value["created_at"]),
                    signal_hash,
                    index,
                    sha256_bytes(
                        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
                    ),
                    str(value["strategy"]) if value.get("strategy") else None,
                    str(value["source"]) if value.get("source") else None,
                )
            )
    finally:
        connection.close()

    inventory_hash, cost_inventory_hash = _combined_inventory(root, signal_hash)
    capability_hash = sha256_bytes(b"UNKNOWN:9")
    capabilities = tuple(
        RealCapability(
            record_key("capability_evidence", capability_hash, index, NORMALIZATION_VERSION),
            identifier,
            name,
            capability_hash,
        )
        for index, (identifier, name) in enumerate(CAPABILITIES, 1)
    )

    cost_contract = LegacyCostRepository(root).get()
    cost_paths = sorted((root / ".dexter" / "scratchpad").glob("*.jsonl"), reverse=True)[:20]
    path_by_stem = {path.stem: path for path in cost_paths}
    cost_sessions = tuple(
        RealCostSession(
            record_key(
                "cost_sessions", sha256_file(path_by_stem[item.session]), 1,
                NORMALIZATION_VERSION,
            ),
            item.session,
            tuple(item.symbols),
            item.steps,
            item.llm_calls,
            item.tool_calls,
            item.decisions,
            item.estimated_cost,
            path_by_stem[item.session].relative_to(root).as_posix(),
            sha256_file(path_by_stem[item.session]),
        )
        for item in cost_contract.sessions
    )
    cost_summary = RealCostSummary(
        record_key("cost_summaries", cost_inventory_hash, 1, NORMALIZATION_VERSION),
        cost_contract.evidence_quality.value,
        cost_contract.total_sessions,
        cost_contract.total_llm_calls,
        cost_contract.total_tool_calls,
        cost_contract.amount,
        cost_inventory_hash,
    )
    return RealApplyPlan(
        str(root), inventory_hash, planned.inventory_hash, tuple(reports),
        tuple(invalid_reports), tuple(decisions), tuple(invalid_decisions),
        tuple(signals), capabilities, cost_summary, cost_sessions,
    )


CANONICAL_TABLES = (
    "assets", "market_reports", "market_asset_snapshots", "decisions",
    "signals", "capability_evidence", "cost_summaries", "cost_sessions",
)


def _canonical_count(connection: psycopg.Connection) -> int:
    return sum(
        connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in CANONICAL_TABLES
    )


def _insert_asset(
    connection: psycopg.Connection, asset_id: str, symbol: str
) -> bool:
    asset_class = "CRYPTO" if asset_id.startswith("crypto:") else "EQUITY"
    instrument = "spot" if asset_class == "CRYPTO" else "stock"
    quote = "USDT" if asset_class == "CRYPTO" else "USD"
    row = connection.execute(
        """
        INSERT INTO assets (
          asset_id,symbol,asset_class,instrument_type,base_currency,
          quote_currency,status,schema_version
        ) VALUES (%s,%s,%s,%s,%s,%s,'DISABLED','1.0.0')
        ON CONFLICT (asset_id) DO NOTHING RETURNING asset_id
        """,
        (asset_id, symbol, asset_class, instrument, symbol, quote),
    ).fetchone()
    return row is not None


def _insert_source_file(
    connection: psycopg.Connection,
    *,
    run_id: str,
    domain: str,
    source_path: str,
    source_hash: str,
    source_size: int,
) -> str:
    source_file_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO migration_source_files (
          source_file_id,run_id,domain,source_path,source_hash,source_size,status
        ) VALUES (%s,%s,%s,%s,%s,%s,'RUNNING')
        """,
        (source_file_id, run_id, domain, source_path, source_hash, source_size),
    )
    return source_file_id


def _commit_chunk(
    connection: psycopg.Connection,
    *,
    run_id: str,
    source_hash: str,
    domain: str,
    first: int,
    last: int,
    chunk_hash: str,
) -> None:
    connection.execute(
        """
        INSERT INTO migration_source_chunks (
          chunk_id,run_id,source_hash,domain,first_record_index,last_record_index,
          chunk_hash,status,committed_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,'COMMITTED',now())
        """,
        (str(uuid.uuid4()), run_id, source_hash, domain, first, last, chunk_hash),
    )


def _insert_quarantine_once(
    connection: psycopg.Connection, *, run_id: str, item: RealQuarantine
) -> bool:
    existing = connection.execute(
        """
        SELECT 1 FROM migration_errors
        WHERE source_path=%s AND source_hash=%s
          AND source_record_index IS NOT DISTINCT FROM %s
          AND payload_hash=%s AND error_code=%s AND normalization_version=%s
        """,
        (
            item.source_path, item.source_hash, item.source_record_index,
            item.payload_hash, item.error_code, NORMALIZATION_VERSION,
        ),
    ).fetchone()
    if existing:
        return False
    connection.execute(
        """
        INSERT INTO migration_errors (
          error_id,run_id,source_path,source_hash,source_record_index,error_code,
          error_message_sanitized,payload_hash,legacy_value,normalization_version
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            str(uuid.uuid4()), run_id, item.source_path, item.source_hash,
            item.source_record_index, item.error_code,
            "legacy record rejected by strict normalization", item.payload_hash,
            item.legacy_value, NORMALIZATION_VERSION,
        ),
    )
    return True


def _insert_audit_once(
    connection: psycopg.Connection, *, run_id: str, item: RealDecision, code: str
) -> bool:
    existing = connection.execute(
        """
        SELECT 1 FROM audit_events
        WHERE event_code=%s AND domain='decisions' AND source_path=%s
          AND source_record_index=%s AND source_record_fingerprint=%s
          AND normalization_version=%s
        """,
        (
            code, item.source_path, item.source_record_index,
            item.canonical_fingerprint, NORMALIZATION_VERSION,
        ),
    ).fetchone()
    if existing:
        return False
    connection.execute(
        """
        INSERT INTO audit_events (
          audit_event_id,run_id,event_code,domain,source_path,source_record_index,
          source_record_fingerprint,normalization_version,details
        ) VALUES (%s,%s,%s,'decisions',%s,%s,%s,%s,%s)
        """,
        (
            str(uuid.uuid4()), run_id, code, item.source_path,
            item.source_record_index, item.canonical_fingerprint,
            NORMALIZATION_VERSION, json.dumps({"to": item.action}),
        ),
    )
    return True


def _insert_decision_signal(
    connection: psycopg.Connection, item: RealDecision
) -> None:
    contract = json.loads(item.canonical_json)
    signals = contract["signals"]
    connection.execute(
        """
        INSERT INTO decision_signal_snapshots (
          decision_id,symbol,close,rsi_14,macd_line,macd_signal_line,
          macd_histogram,sma_200,price_vs_sma200,volume_24h,volume_30d_avg,
          volume_trend_ratio,signal,calculated_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (decision_id) DO NOTHING
        """,
        (
            item.decision_id, signals["symbol"], signals["close"],
            signals["rsi_14"], signals["macd_line"],
            signals["macd_signal_line"], signals["macd_histogram"],
            signals["sma_200"], signals["price_vs_sma200"],
            signals["volume_24h"], signals["volume_30d_avg"],
            signals["volume_trend_ratio"], signals["signal"],
            signals["calculated_at"],
        ),
    )


def apply_real_plan(
    plan: RealApplyPlan,
    settings: DatabaseSettings,
    *,
    apply: bool,
    code_commit: str,
) -> ApplyResult:
    if apply is not True:
        raise ApplyRejected("explicit apply=True is required")
    if settings.redacted_identity() != {
        "host": "127.0.0.1",
        "port": 55432,
        "database": settings.database,
        "role": "trading_migrator",
    }:
        raise ApplyRejected("real apply requires the localhost migrator role")
    connection = psycopg.connect(settings.conninfo(), autocommit=True)
    run_id = str(uuid.uuid4())
    before = _canonical_count(connection)
    root = Path(plan.source_root)
    records_seen = (
        len(plan.reports) + len(plan.invalid_reports)
        + len(plan.decisions) + len(plan.invalid_decisions)
        + len(plan.signals) + len(plan.capabilities) + len(plan.cost_sessions)
    )
    try:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO migration_runs (
                  run_id,started_at,status,code_commit,schema_version,
                  normalization_version,source_root,source_inventory_hash
                ) VALUES (%s,now(),'RUNNING',%s,'0002_quarantine_lineage',%s,%s,%s)
                """,
                (
                    run_id, code_commit, NORMALIZATION_VERSION,
                    plan.source_root, plan.inventory_hash,
                ),
            )

        reports_by_path = {item.source_path: item for item in plan.reports}
        invalid_reports = {item.source_path: item for item in plan.invalid_reports}
        for source_path in sorted((*reports_by_path, *invalid_reports)):
            path = root / source_path
            source_hash = sha256_file(path)
            with connection.transaction():
                source_file_id = _insert_source_file(
                    connection, run_id=run_id, domain="reports",
                    source_path=source_path, source_hash=source_hash,
                    source_size=path.stat().st_size,
                )
                inserted = skipped = invalid = 0
                if source_path in invalid_reports:
                    _insert_quarantine_once(
                        connection, run_id=run_id, item=invalid_reports[source_path]
                    )
                    invalid = 1
                else:
                    report = reports_by_path[source_path]
                    created = connection.execute(
                        """
                        INSERT INTO market_reports (
                          report_id,as_of,freshness_status,schema_version,
                          normalization_version,provenance_quality,source_type,
                          source_path,source_hash,source_record_index,
                          source_record_fingerprint,event_time,known_at,ingested_at,
                          migration_run_id
                        ) VALUES (%s,%s,'STALE','1.0.0',%s,'LEGACY_ESTIMATED',
                          'JSON',%s,%s,1,%s,%s,NULL,now(),%s)
                        ON CONFLICT (source_hash,normalization_version) DO NOTHING
                        RETURNING report_id
                        """,
                        (
                            report.report_id, report.as_of, NORMALIZATION_VERSION,
                            report.source_path, report.source_hash,
                            report.canonical_fingerprint, report.as_of, run_id,
                        ),
                    ).fetchone()
                    if created:
                        inserted += 1
                        for asset in report.assets:
                            inserted += int(_insert_asset(
                                connection, asset.asset_id, asset.symbol
                            ))
                            connection.execute(
                                """
                                INSERT INTO market_asset_snapshots (
                                  snapshot_id,report_id,asset_id,price,action,
                                  confidence,raw_evidence_ref,schema_version,
                                  normalization_version,provenance_quality,
                                  source_hash,source_record_index,ingested_at,
                                  migration_run_id
                                ) VALUES (%s,%s,%s,%s,%s,%s,%s,'1.0.0',%s,
                                  'LEGACY_ESTIMATED',%s,%s,now(),%s)
                                """,
                                (
                                    asset.snapshot_id, report.report_id,
                                    asset.asset_id, asset.price, asset.action,
                                    asset.confidence,
                                    "canonical-json:" + asset.canonical_json,
                                    NORMALIZATION_VERSION, report.source_hash,
                                    asset.source_record_index, run_id,
                                ),
                            )
                            inserted += 1
                    else:
                        skipped = 1 + len(report.assets)
                _commit_chunk(
                    connection, run_id=run_id, source_hash=source_hash,
                    domain="reports", first=1, last=1,
                    chunk_hash=(reports_by_path[source_path].canonical_fingerprint
                                if source_path in reports_by_path else source_hash),
                )
                connection.execute(
                    """
                    UPDATE migration_source_files SET status='COMPLETED',
                      records_seen=1,records_inserted=%s,records_skipped=%s,
                      records_invalid=%s WHERE source_file_id=%s
                    """,
                    (inserted, skipped, invalid, source_file_id),
                )

        decisions_by_index = {item.source_record_index: item for item in plan.decisions}
        invalid_by_index = {
            item.source_record_index: item for item in plan.invalid_decisions
        }
        decision_path = root / "memory" / "decisions.jsonl"
        with connection.transaction():
            decision_file_id = _insert_source_file(
                connection, run_id=run_id, domain="decisions",
                source_path="memory/decisions.jsonl",
                source_hash=sha256_file(decision_path),
                source_size=decision_path.stat().st_size,
            )
        decision_inserted = decision_skipped = 0
        for first in range(1, max((*decisions_by_index, *invalid_by_index)) + 1, 500):
            last = min(first + 499, max((*decisions_by_index, *invalid_by_index)))
            hashes: list[str] = []
            with connection.transaction():
                for index in range(first, last + 1):
                    if index in invalid_by_index:
                        item = invalid_by_index[index]
                        hashes.append(item.payload_hash)
                        _insert_quarantine_once(connection, run_id=run_id, item=item)
                        continue
                    item = decisions_by_index[index]
                    hashes.append(item.payload_hash)
                    _insert_asset(connection, item.asset_id, item.symbol)
                    created = connection.execute(
                        """
                        INSERT INTO decisions (
                          decision_id,asset_id,action,confidence,as_of,
                          schema_version,normalization_version,provenance_quality,
                          source_type,source_path,source_hash,source_record_index,
                          source_record_fingerprint,event_time,known_at,ingested_at,
                          migration_run_id
                        ) VALUES (%s,%s,%s,%s,%s,'1.0.0',%s,
                          'LEGACY_ESTIMATED','JSONL',%s,%s,%s,%s,%s,NULL,now(),%s)
                        ON CONFLICT (source_hash,source_record_index,normalization_version)
                        DO NOTHING RETURNING decision_id
                        """,
                        (
                            item.decision_id, item.asset_id, item.action,
                            item.confidence, item.as_of, NORMALIZATION_VERSION,
                            item.source_path, item.source_hash,
                            item.source_record_index, item.canonical_fingerprint,
                            item.as_of, run_id,
                        ),
                    ).fetchone()
                    if created:
                        decision_inserted += 1
                        _insert_decision_signal(connection, item)
                    else:
                        decision_skipped += 1
                    for code in item.audit_codes:
                        _insert_audit_once(connection, run_id=run_id, item=item, code=code)
                _commit_chunk(
                    connection, run_id=run_id,
                    source_hash=sha256_file(decision_path), domain="decisions",
                    first=first, last=last,
                    chunk_hash=sha256_bytes("\n".join(hashes).encode()),
                )
        with connection.transaction():
            connection.execute(
                """
                UPDATE migration_source_files SET status='COMPLETED',
                  records_seen=%s,records_inserted=%s,records_skipped=%s,
                  records_invalid=%s WHERE source_file_id=%s
                """,
                (
                    len(decisions_by_index) + len(invalid_by_index),
                    decision_inserted, decision_skipped, len(invalid_by_index),
                    decision_file_id,
                ),
            )

        with connection.transaction():
            signal_file_id = _insert_source_file(
                connection, run_id=run_id, domain="signals",
                source_path="memory/trading.db#signals",
                source_hash=plan.signals[0].source_hash, source_size=0,
            )
            signal_inserted = signal_skipped = 0
            for item in plan.signals:
                _insert_asset(connection, item.asset_id, item.symbol)
                created = connection.execute(
                    """
                    INSERT INTO signals (
                      signal_id,asset_id,action,confidence,as_of,model_id,
                      model_version,schema_version,normalization_version,
                      provenance_quality,source_type,source_path,source_hash,
                      source_record_index,source_record_fingerprint,known_at,
                      ingested_at,migration_run_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,'1.0.0',%s,
                      'LEGACY_ESTIMATED','SQLITE','memory/trading.db#signals',
                      %s,%s,%s,NULL,now(),%s)
                    ON CONFLICT (source_hash,source_record_index,normalization_version)
                    DO NOTHING RETURNING signal_id
                    """,
                    (
                        item.signal_id, item.asset_id, item.action,
                        item.confidence, item.as_of, item.model_id,
                        item.model_version, NORMALIZATION_VERSION,
                        item.source_hash, item.source_record_index,
                        item.canonical_fingerprint, run_id,
                    ),
                ).fetchone()
                signal_inserted += int(created is not None)
                signal_skipped += int(created is None)
            _commit_chunk(
                connection, run_id=run_id, source_hash=plan.signals[0].source_hash,
                domain="signals", first=1, last=len(plan.signals),
                chunk_hash=sha256_bytes(
                    "\n".join(item.canonical_fingerprint for item in plan.signals).encode()
                ),
            )
            connection.execute(
                """
                UPDATE migration_source_files SET status='COMPLETED',
                  records_seen=%s,records_inserted=%s,records_skipped=%s
                WHERE source_file_id=%s
                """,
                (len(plan.signals), signal_inserted, signal_skipped, signal_file_id),
            )

        with connection.transaction():
            capability_hash = plan.capabilities[0].source_hash
            capability_file_id = _insert_source_file(
                connection, run_id=run_id, domain="capabilities",
                source_path="synthetic/capabilities", source_hash=capability_hash,
                source_size=0,
            )
            cap_inserted = cap_skipped = 0
            for item in plan.capabilities:
                created = connection.execute(
                    """
                    INSERT INTO capability_evidence (
                      evidence_id,capability_id,status,source_hash,schema_version,
                      normalization_version,provenance_quality,ingested_at,
                      migration_run_id
                    ) VALUES (%s,%s,'UNKNOWN',%s,'1.0.0',%s,'UNKNOWN',now(),%s)
                    ON CONFLICT (evidence_id) DO NOTHING RETURNING evidence_id
                    """,
                    (
                        item.evidence_id, item.capability_id, item.source_hash,
                        NORMALIZATION_VERSION, run_id,
                    ),
                ).fetchone()
                cap_inserted += int(created is not None)
                cap_skipped += int(created is None)
            _commit_chunk(
                connection, run_id=run_id, source_hash=capability_hash,
                domain="capabilities", first=1, last=len(plan.capabilities),
                chunk_hash=sha256_bytes(
                    "\n".join(item.evidence_id for item in plan.capabilities).encode()
                ),
            )
            connection.execute(
                """
                UPDATE migration_source_files SET status='COMPLETED',
                  records_seen=%s,records_inserted=%s,records_skipped=%s
                WHERE source_file_id=%s
                """,
                (len(plan.capabilities), cap_inserted, cap_skipped, capability_file_id),
            )

        for index, item in enumerate(plan.cost_sessions, 1):
            path = root / item.source_path
            with connection.transaction():
                cost_file_id = _insert_source_file(
                    connection, run_id=run_id, domain="costs",
                    source_path=item.source_path, source_hash=item.source_hash,
                    source_size=path.stat().st_size,
                )
                summary_created = connection.execute(
                    """
                    INSERT INTO cost_summaries (
                      cost_summary_id,evidence_quality,currency,total_sessions,
                      total_llm_calls,total_tool_calls,amount,source_hash,
                      schema_version,normalization_version,provenance_quality,
                      ingested_at,migration_run_id
                    ) VALUES (%s,%s,'USD',%s,%s,%s,%s,%s,'1.0.0',%s,
                      'UNKNOWN',now(),%s)
                    ON CONFLICT (cost_summary_id) DO NOTHING RETURNING cost_summary_id
                    """,
                    (
                        plan.cost_summary.cost_summary_id,
                        plan.cost_summary.evidence_quality,
                        plan.cost_summary.total_sessions,
                        plan.cost_summary.total_llm_calls,
                        plan.cost_summary.total_tool_calls,
                        plan.cost_summary.amount, plan.cost_summary.source_hash,
                        NORMALIZATION_VERSION, run_id,
                    ),
                ).fetchone()
                session_created = connection.execute(
                    """
                    INSERT INTO cost_sessions (
                      cost_session_id,cost_summary_id,session,steps,llm_calls,
                      tool_calls,decisions,estimated_cost,source_path,source_hash,
                      source_record_index
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                    ON CONFLICT (source_hash,source_record_index) DO NOTHING
                    RETURNING cost_session_id
                    """,
                    (
                        item.cost_session_id, plan.cost_summary.cost_summary_id,
                        item.session, item.steps, item.llm_calls, item.tool_calls,
                        item.decisions, item.estimated_cost, item.source_path,
                        item.source_hash,
                    ),
                ).fetchone()
                inserted = int(summary_created is not None) + int(session_created is not None)
                skipped = 1 - int(session_created is not None)
                if index == 1 and summary_created is None:
                    skipped += 1
                _commit_chunk(
                    connection, run_id=run_id, source_hash=item.source_hash,
                    domain="costs", first=1, last=1, chunk_hash=item.source_hash,
                )
                connection.execute(
                    """
                    UPDATE migration_source_files SET status='COMPLETED',
                      records_seen=1,records_inserted=%s,records_skipped=%s
                    WHERE source_file_id=%s
                    """,
                    (inserted, skipped, cost_file_id),
                )

        after = _canonical_count(connection)
        inserted = after - before
        skipped = plan.canonical_total - inserted
        with connection.transaction():
            connection.execute(
                """
                UPDATE migration_runs SET status='COMPLETED',finished_at=now(),
                  records_seen=%s,records_inserted=%s,records_updated=0,
                  records_skipped=%s,records_invalid=%s WHERE run_id=%s
                """,
                (
                    records_seen, inserted, skipped, plan.quarantine_total, run_id,
                ),
            )
        return ApplyResult(run_id, inserted, skipped, 0, plan.quarantine_total)
    except Exception:
        with connection.transaction():
            connection.execute(
                "UPDATE migration_runs SET status='FAILED',finished_at=now() WHERE run_id=%s",
                (run_id,),
            )
        raise
    finally:
        connection.close()

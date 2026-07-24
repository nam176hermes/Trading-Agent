from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from control_api.normalization import normalize_asset, parse_datetime

from .identity import sha256_bytes, sha256_file
from .normalization import ASSETS, MigrationValidationError, normalize_decision


@dataclass(frozen=True, slots=True)
class PlannedError:
    source_path: str
    source_hash: str
    source_record_index: int | None
    code: str
    message: str
    payload_hash: str
    legacy_value: str | None
    normalization_version: str = "phase3-v1"


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    source_root: str
    inventory_hash: str
    counts: dict[str, int]
    would_insert: int
    would_insert_by_domain: dict[str, int]
    would_insert_tracking: dict[str, int]
    would_quarantine: int
    would_audit: int
    would_skip: int
    records_updated: int
    errors: tuple[PlannedError, ...]
    warnings: tuple[str, ...]
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["errors"] = [asdict(item) for item in self.errors]
        return value


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _inventory_hash(entries: list[tuple[str, str]]) -> str:
    material = "".join(f"{path}\0{digest}\n" for path, digest in sorted(entries))
    return sha256_bytes(material.encode())


def plan_migration(
    source_root: Path, *, domain: str | None = None, limit: int | None = None,
    source_file: Path | None = None,
) -> MigrationPlan:
    root = source_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("source root must be a directory")
    if source_file is not None:
        selected = source_file.expanduser().resolve()
        try:
            selected.relative_to(root)
        except ValueError as error:
            raise ValueError("source file must be inside source root") from error
    counts = {
        "report_files_discovered": 0, "valid_reports": 0,
        "invalid_reports": 0, "report_asset_rows": 0,
        "decisions_seen": 0, "valid_decisions": 0, "invalid_decisions": 0,
        "sqlite_signals": 0, "capabilities": 0,
        "verified_capabilities": 0, "cost_sessions": 0,
    }
    errors: list[PlannedError] = []
    inventory: list[tuple[str, str]] = []
    planned_rows = 0
    asset_ids: set[str] = set()
    audit_count = 0
    domains = {domain} if domain else {"reports", "decisions", "signals", "capabilities", "costs"}

    if "reports" in domains:
        report_paths = sorted((root / "reports").glob("report_*.json"))
        if source_file is not None:
            report_paths = [path for path in report_paths if path.resolve() == source_file.resolve()]
        if limit is not None:
            report_paths = report_paths[:limit]
        counts["report_files_discovered"] = len(report_paths)
        for path in report_paths:
            digest = sha256_file(path)
            relative = _relative(path, root)
            inventory.append((relative, digest))
            payload = path.read_bytes()
            try:
                value = json.loads(payload)
            except json.JSONDecodeError:
                counts["invalid_reports"] += 1
                errors.append(PlannedError(relative, digest, None, "INVALID_JSON", "source is not valid JSON", digest, None))
                continue
            try:
                if not isinstance(value, dict) or not isinstance(value.get("assets"), list):
                    raise MigrationValidationError("MISSING_REQUIRED_FIELD", "market report requires assets")
                parse_datetime(value.get("timestamp") or value.get("as_of"))
                assets = []
                for item in value["assets"]:
                    if not isinstance(item, dict):
                        raise MigrationValidationError("SCHEMA_VALIDATION_FAILED", "market asset must be an object")
                    normalized = normalize_asset(item)
                    if normalized.symbol not in ASSETS:
                        raise MigrationValidationError("UNKNOWN_ASSET", "market asset is not registered")
                    assets.append(normalized)
                    asset_ids.add(ASSETS[normalized.symbol])
            except MigrationValidationError as error:
                counts["invalid_reports"] += 1
                errors.append(PlannedError(relative, digest, None, error.code, error.message, digest, None))
                continue
            except (ValueError, TypeError) as error:
                counts["invalid_reports"] += 1
                message = "market report schema validation failed"
                errors.append(PlannedError(relative, digest, None, "SCHEMA_VALIDATION_FAILED", message, digest, None))
                continue
            counts["valid_reports"] += 1
            counts["report_asset_rows"] += len(assets)
            planned_rows += 1 + len(assets)

    if "decisions" in domains:
        path = root / "memory" / "decisions.jsonl"
        if path.is_file() and (source_file is None or path.resolve() == source_file.resolve()):
            digest = sha256_file(path)
            inventory.append((_relative(path, root), digest))
            with path.open(encoding="utf-8") as handle:
                for index, raw in enumerate(handle, 1):
                    if not raw.strip():
                        continue
                    if limit is not None and counts["decisions_seen"] >= limit:
                        break
                    counts["decisions_seen"] += 1
                    payload_hash = sha256_bytes(raw.encode())
                    try:
                        value = json.loads(raw)
                        if not isinstance(value, dict):
                            raise MigrationValidationError("SCHEMA_VALIDATION_FAILED", "decision must be an object")
                        normalized_decision = normalize_decision(value, source_hash=digest, record_index=index)
                    except json.JSONDecodeError:
                        counts["invalid_decisions"] += 1
                        errors.append(PlannedError(_relative(path, root), digest, index, "INVALID_JSON", "decision line is not valid JSON", payload_hash, None))
                    except MigrationValidationError as error:
                        counts["invalid_decisions"] += 1
                        legacy_value = value.get("suggestion") if isinstance(value.get("suggestion"), str) else None
                        legacy_value = legacy_value if legacy_value in {"WATCH", "WATCH FOR EXIT"} else None
                        errors.append(PlannedError(_relative(path, root), digest, index, error.code, error.message, payload_hash, legacy_value))
                    else:
                        counts["valid_decisions"] += 1
                        planned_rows += 1
                        asset_ids.add(normalized_decision.asset_id)
                        audit_count += len(normalized_decision.audit_events)

    if "signals" in domains:
        path = root / "memory" / "trading.db"
        if path.is_file() and (source_file is None or path.resolve() == source_file.resolve()):
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                counts["sqlite_signals"] = int(connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0])
                for (symbol,) in connection.execute("SELECT DISTINCT symbol FROM signals"):
                    if symbol in ASSETS:
                        asset_ids.add(ASSETS[symbol])
            finally:
                connection.close()
            planned_rows += counts["sqlite_signals"]
            inventory.append(("memory/trading.db#signals", str(counts["sqlite_signals"])))

    if "capabilities" in domains:
        counts["capabilities"] = 9
        counts["verified_capabilities"] = 0
        planned_rows += 9
        inventory.append(("synthetic/capabilities", "UNKNOWN:9"))

    if "costs" in domains:
        paths = sorted((root / ".dexter" / "scratchpad").glob("*.jsonl"), reverse=True)[:20]
        counts["cost_sessions"] = len(paths)
        for path in paths:
            inventory.append((_relative(path, root), sha256_file(path)))
        planned_rows += len(paths) + int(bool(paths))

    domain_rows = {
        "assets": len(asset_ids),
        "market_reports": counts["valid_reports"],
        "market_asset_snapshots": counts["report_asset_rows"],
        "decisions": counts["valid_decisions"],
        "signals": counts["sqlite_signals"],
        "capability_evidence": counts["capabilities"],
        "cost_summaries": int(counts["cost_sessions"] > 0),
        "cost_sessions": counts["cost_sessions"],
    }
    source_files = counts["report_files_discovered"] + int(counts["decisions_seen"] > 0) + int(counts["sqlite_signals"] > 0) + int(counts["capabilities"] > 0) + counts["cost_sessions"]
    decision_chunks = (counts["decisions_seen"] + 499) // 500
    signal_chunks = (counts["sqlite_signals"] + 499) // 500
    chunks = counts["report_files_discovered"] + decision_chunks + signal_chunks + int(counts["capabilities"] > 0) + counts["cost_sessions"]
    return MigrationPlan(
        source_root=str(root), inventory_hash=_inventory_hash(inventory), counts=counts,
        would_insert=sum(domain_rows.values()), would_insert_by_domain=domain_rows,
        would_insert_tracking={"migration_runs": 1, "migration_source_files": source_files, "migration_source_chunks": chunks},
        would_quarantine=len(errors), would_audit=audit_count,
        would_skip=0, records_updated=0,
        errors=tuple(errors), warnings=("cost evidence is UNKNOWN or ESTIMATED, never exact",),
    )

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from dataclasses import replace
from pathlib import Path

import psycopg

from . import NORMALIZATION_VERSION
from .approval import (
    APPROVED_ALEMBIC_REVISION,
    APPROVED_INVENTORY_HASH,
    ApprovalContext,
    validate_real_apply_approval,
)
from .db import DatabaseSettings
from .planner import plan_migration
from .real_import import CANONICAL_TABLES, apply_real_plan, build_real_plan
from .writer import ApplyRejected

APPROVED_PLANNER_MANIFEST_HASH = (
    "06964c9ce162bf0fefa637c0a04d86eaea9b21deae0060ddec1555ba63f20892"
)
PRODUCTION_CREDENTIAL_NAMES = (
    "TRADING_MASTER_KEY", "COINBASE_API_KEY", "COINBASE_API_SECRET",
    "KRAKEN_API_KEY", "KRAKEN_API_SECRET", "BINANCE_API_KEY",
    "BINANCE_API_SECRET", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or apply a Phase 3 legacy import")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="explicit dry-run; this is the default")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--resume")
    parser.add_argument("--domain", choices=("reports", "decisions", "signals", "capabilities", "costs"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--source-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply:
        if os.environ.get("TRADING_REAL_APPLY_APPROVED", "").strip().lower() != "true":
            raise SystemExit("explicit real apply approval is not enabled")
        if args.resume:
            raise SystemExit(
                "resume of a completed real-data run is rejected by policy; "
                "use the isolated resume test database for failure recovery evidence"
            )
        started_at = datetime.now(UTC)
        started = time.monotonic()
        try:
            real_plan = build_real_plan(args.source_root)
            if real_plan.planner_manifest_hash != APPROVED_PLANNER_MANIFEST_HASH:
                raise ApplyRejected("planner manifest hash does not match approval")
            settings = DatabaseSettings.from_env()
            with psycopg.connect(settings.conninfo()) as connection:
                identity = connection.execute(
                    "SELECT inet_server_addr()::text,inet_server_port(),"
                    "current_database(),current_user"
                ).fetchone()
                revision = connection.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()[0]
                canonical_rows = sum(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in CANONICAL_TABLES
                )
                quarantine_rows = connection.execute(
                    "SELECT count(*) FROM migration_errors"
                ).fetchone()[0]
            root = args.source_root.expanduser().resolve()
            context = ApprovalContext(
                explicit_apply=True,
                source_root=str(root),
                actual_inventory_hash=real_plan.inventory_hash,
                approval_enabled=os.environ.get("TRADING_REAL_APPLY_APPROVED", ""),
                approved_inventory_hash=os.environ.get(
                    "TRADING_REAL_APPLY_INVENTORY_HASH", ""
                ),
                normalization_version=NORMALIZATION_VERSION,
                approved_normalization_version=os.environ.get(
                    "TRADING_REAL_APPLY_NORMALIZATION_VERSION", ""
                ),
                alembic_revision=revision,
                approved_alembic_revision=os.environ.get(
                    "TRADING_REAL_APPLY_ALEMBIC_REVISION", ""
                ),
                database_host=identity[0].split("/")[0],
                database_port=identity[1],
                database_name=identity[2],
                database_role=identity[3],
                expected_canonical_rows=int(os.environ.get(
                    "TRADING_REAL_APPLY_EXPECTED_CANONICAL_ROWS", "-1"
                )),
                actual_canonical_rows=canonical_rows,
                expected_quarantine_rows=int(os.environ.get(
                    "TRADING_REAL_APPLY_EXPECTED_QUARANTINE_ROWS", "-1"
                )),
                actual_quarantine_rows=quarantine_rows,
                requested_mode=(root / ".mode").read_text(encoding="utf-8").strip(),
                live_execution_enabled=os.environ.get(
                    "LIVE_EXECUTION_ENABLED", "missing"
                ),
                live_trading_approved=os.environ.get(
                    "LIVE_TRADING_APPROVED", "missing"
                ),
                kill_switch_active=Path(os.environ.get(
                    "TRADING_KILL_SWITCH_PATH", str(root / ".kill_switch")
                )).exists(),
                production_credential_names=tuple(
                    name for name in PRODUCTION_CREDENTIAL_NAMES
                    if os.environ.get(name)
                ),
            )
            validate_real_apply_approval(context)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
                text=True,
            ).stdout.strip()
            result = apply_real_plan(
                real_plan, settings, apply=True, code_commit=commit
            )
        except (ApplyRejected, ValueError, OSError, psycopg.Error) as error:
            raise SystemExit(str(error)) from error
        finished_at = datetime.now(UTC)
        print(json.dumps({
            "run_id": result.run_id,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round(time.monotonic() - started, 6),
            "source_inventory_hash": real_plan.inventory_hash,
            "planner_manifest_hash": real_plan.planner_manifest_hash,
            "schema_revision": APPROVED_ALEMBIC_REVISION,
            "normalization_version": NORMALIZATION_VERSION,
            "records_seen": (
                len(real_plan.reports) + len(real_plan.invalid_reports)
                + len(real_plan.decisions) + len(real_plan.invalid_decisions)
                + len(real_plan.signals) + len(real_plan.capabilities)
                + len(real_plan.cost_sessions)
            ),
            "records_inserted": result.inserted,
            "records_skipped": result.skipped,
            "records_updated": result.updated,
            "records_invalid": result.invalid,
            "quarantine_rows": real_plan.quarantine_total,
            "audit_rows_expected_source_scoped": sum(
                len(item.audit_codes) for item in real_plan.decisions
            ),
            "source_files": 2295,
            "source_chunks": 2328,
            "canonical_counts": real_plan.domain_counts,
        }, sort_keys=True, indent=2))
        return 0
    started = time.monotonic()
    plan = plan_migration(
        args.source_root, domain=args.domain, limit=args.limit,
        source_file=args.source_file,
    )
    plan = replace(plan, duration_seconds=round(time.monotonic() - started, 6))
    print(json.dumps(plan.to_dict(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

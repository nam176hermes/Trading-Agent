from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import psycopg

from .db import DatabaseSettings
from .migrate import PRODUCTION_CREDENTIAL_NAMES
from .phase3b_approval import (
    Phase3BApprovalContext,
    Phase3BApprovalRejected,
    validate_phase3b_approval,
)
from .phase3b_backfill import build_phase3b_backfill_plan
from .phase3b_sources import PHASE3B_NORMALIZATION_VERSION
from .phase3b_writer import (
    DOMAINS,
    Phase3BApplyError,
    apply_phase3b_plan,
    inspect_phase3b_dry_run,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or apply Phase 3B backfills")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--domain", dest="domains", action="append", choices=DOMAINS)
    parser.add_argument("--resume")
    return parser


def _result_json(results) -> dict[str, dict[str, object]]:
    return {
        domain: {
            "run_id": item.run_id,
            "rows_seen": item.seen,
            "rows_updated": item.updated,
            "rows_unchanged": item.unchanged,
            "rows_unknown": item.unknown,
            "rows_conflicted": item.conflicted,
            "lineage_inserted": item.lineage_inserted,
        }
        for domain, item in results.items()
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply and args.dry_run:
        raise SystemExit("--apply and --dry-run are mutually exclusive")
    if args.resume and not args.apply:
        raise SystemExit("--resume requires --apply")
    domains = tuple(args.domains or DOMAINS)
    if args.resume and len(domains) != 1:
        raise SystemExit("--resume requires exactly one --domain")
    if args.apply and os.environ.get(
        "TRADING_PHASE3B_APPLY_APPROVED", ""
    ).strip().lower() != "true":
        raise SystemExit("Phase 3B apply approval is not enabled")

    plan = build_phase3b_backfill_plan(args.source_root)
    settings = DatabaseSettings.from_env()
    if args.apply:
        try:
            with psycopg.connect(settings.conninfo()) as connection:
                identity = connection.execute(
                    "SELECT inet_server_addr()::text,inet_server_port(),"
                    "current_database(),current_user"
                ).fetchone()
                revision = connection.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()[0]
            root = args.source_root.expanduser().resolve()
            validate_phase3b_approval(Phase3BApprovalContext(
                approval_enabled=os.environ.get("TRADING_PHASE3B_APPLY_APPROVED", ""),
                actual_inventory_hash=plan.inventory_hash,
                approved_inventory_hash=os.environ.get(
                    "TRADING_PHASE3B_SOURCE_INVENTORY_HASH", ""
                ),
                actual_revision=revision,
                approved_revision=os.environ.get(
                    "TRADING_PHASE3B_ALEMBIC_REVISION", ""
                ),
                actual_normalization_version=PHASE3B_NORMALIZATION_VERSION,
                approved_normalization_version=os.environ.get(
                    "TRADING_PHASE3B_NORMALIZATION_VERSION", ""
                ),
                database_host=identity[0].split("/")[0],
                database_port=identity[1],
                database_name=identity[2],
                database_role=identity[3],
                requested_mode=(root / ".mode").read_text(encoding="utf-8").strip(),
                live_execution_enabled=os.environ.get("LIVE_EXECUTION_ENABLED", "missing"),
                live_trading_approved=os.environ.get("LIVE_TRADING_APPROVED", "missing"),
                kill_switch_active=Path(os.environ.get(
                    "TRADING_KILL_SWITCH_PATH", str(root / ".kill_switch")
                )).exists(),
                production_credential_names=tuple(
                    name for name in PRODUCTION_CREDENTIAL_NAMES if os.environ.get(name)
                ),
            ))
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
                text=True,
            ).stdout.strip()
            results = apply_phase3b_plan(
                plan, settings, domains=domains, apply=True, code_commit=commit,
                resume_run_id=args.resume,
            )
        except (Phase3BApprovalRejected, Phase3BApplyError, psycopg.Error, OSError) as error:
            raise SystemExit(str(error)) from error
    else:
        dry_run = inspect_phase3b_dry_run(plan, settings)
        results = apply_phase3b_plan(
            plan, settings, domains=domains, apply=False, code_commit="dry-run"
        )
    output = {
        "apply": args.apply,
        "source_inventory_hash": plan.inventory_hash,
        "normalization_version": PHASE3B_NORMALIZATION_VERSION,
        "domains": _result_json(results),
    }
    if not args.apply:
        output["dry_run"] = {domain: dry_run[domain] for domain in domains}
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

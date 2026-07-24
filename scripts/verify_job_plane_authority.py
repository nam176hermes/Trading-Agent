#!/usr/bin/env python3
"""Run the frozen job-plane verifier without emitting connection secrets."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

import psycopg

from packages.job_authority import verify_authority


_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--migration", required=True, type=Path)
    parser.add_argument("--conninfo-env", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if _ENVIRONMENT_NAME.fullmatch(arguments.conninfo_env) is None:
        raise SystemExit("connection environment name is invalid")
    conninfo = os.environ.get(arguments.conninfo_env)
    if not conninfo:
        raise SystemExit("connection environment is unavailable")
    try:
        with psycopg.connect(
            conninfo,
            options="-c default_transaction_read_only=on",
        ) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                evidence = verify_authority(
                    connection,
                    arguments.contract,
                    arguments.migration,
                )
    except Exception as exc:
        raise SystemExit(
            f"job-plane verification failed: {type(exc).__name__}"
        ) from None

    print(
        json.dumps(
            {
                "head": evidence.head,
                "catalog_query_id": evidence.catalog.query_id,
                "catalog_sha256": evidence.catalog.sha256,
                "catalog_row_count": evidence.catalog.row_count,
                "event_chain_query_id": evidence.event_chain_query_id,
                "violations": [
                    {
                        "code": violation.code,
                        "job_id": violation.job_id,
                        "event_id": violation.event_id,
                        "sequence": violation.sequence,
                    }
                    for violation in evidence.violations
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

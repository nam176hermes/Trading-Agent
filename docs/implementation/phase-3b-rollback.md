# Phase 3B Rollback

## Application rollback

Set the isolated Control API process to the documented legacy backend:

```text
TRADING_STORE_BACKEND=legacy
TRADING_DATA_ROOT=/home/thenam176/.hermes/crypto-research
```

The explicit local smoke returned legacy decision total 16,653 and paper/paper.
No active service restart or public cutover was performed in Phase 3B.

## Database rollback

Schema downgrade is intentionally unsupported. Restore the verified custom
dump using protected credentials, never a command-line password:

```bash
set -a
. "$HOME/.config/trading-agent/postgres-owner.env"
set +a
export PGPASSWORD="$TRADING_DATABASE_PASSWORD"
pg_restore --clean --if-exists --no-owner \
  -h "$TRADING_DATABASE_HOST" -p "$TRADING_DATABASE_PORT" \
  -U "$TRADING_DATABASE_USER" -d "$TRADING_DATABASE_NAME" \
  /home/thenam176/.local/share/trading-agent-backups/phase3b-prechange-20260711T194536-0400.dump
unset PGPASSWORD TRADING_DATABASE_PASSWORD
```

Backup SHA-256 is
`56541c875d2edccec2dd1f4fd28c5d888c46a9c73a9f7666403d8fc4c137b161`,
mode `0600`. Its restore drill verified 15 application tables, Alembic `0002`,
43,055 canonical rows, and 222 quarantine rows.

Restoring removes Phase 3B schema, four backfill run rows, contract values, and
74,273 lineage/link rows while preserving all Phase 3 canonical data/runs as of
the checkpoint. Stop isolated readers before a restore. Do not restore over an
active target without a separately reviewed operational change window.

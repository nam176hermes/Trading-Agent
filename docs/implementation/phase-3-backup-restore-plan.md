# Phase 3 Backup and Restore Plan

## Current target state

The target has Alembic revision `0001_phase3_operational_store`, fifteen empty
operational/migration tables, and no legacy domain data. There is no operational
data requiring a pre-import backup. A full custom-format empty-target dump was
still created outside Git with mode 0600 to test schema/bootstrap restore.

Restore drill evidence:

```text
restored operational tables: 15
restored Alembic revision: 0001_phase3_operational_store
restored domain rows: 0
temporary restore database removed: yes
```

## Exact password-safe commands

Load credentials only from a protected 0600 environment file and expose the
password to libpq through process environment, never a command-line DSN:

```bash
set -a
. "$HOME/.config/trading-agent/postgres-owner.env"
set +a
export PGPASSWORD="$TRADING_DATABASE_PASSWORD"
umask 077
pg_dump -Fc -h 127.0.0.1 -p 55432 -U trading_owner \
  -d trading_agent \
  -f "$HOME/.local/state/trading-agent/backups/trading_agent.dump"
unset PGPASSWORD TRADING_DATABASE_PASSWORD
```

The bootstrap administrator creates a temporary database; the dump is restored
with `--no-owner`, then table counts and `alembic_version` are checked. The
temporary database is dropped only after those checks pass. Dump files are
never stored under the repository.

Before any future real apply, repeat the dump if any target table is non-empty,
repeat the restore drill, recapture source hashes, and obtain explicit user
approval.

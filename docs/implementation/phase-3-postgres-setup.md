# Phase 3 PostgreSQL Setup

Captured 2026-07-11 (America/Toronto), before Alembic or legacy import.

## Provisioned cluster

| Field | Evidence |
|---|---|
| PostgreSQL | 16.14 (Ubuntu package) |
| Cluster name | `trading-agent` |
| Ownership model | user-owned native `initdb/pg_ctl` cluster |
| Bind | `127.0.0.1` only |
| Port | 55432 |
| Database | `trading_agent` |
| Data directory permission | 0700 |
| Authentication | SCRAM-SHA-256 for local and host rules |
| Password encryption | SCRAM-SHA-256 |
| Distro cluster | independent `16/main` remains on localhost port 5432 |

The user-owned cluster was selected because the installed server binaries are
available while non-interactive sudo is not. This avoids requesting or
processing a sudo password and still supplies a named, isolated native
PostgreSQL cluster. No active trading service was restarted.

## Roles and database

`trading_agent` is owned by `trading_owner`. All three application roles are
login roles with `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`, and
`NOREPLICATION`:

- `trading_owner`: schema/Alembic owner only.
- `trading_migrator`: future operational importer.
- `trading_reader`: future PostgreSQL-backed Control API; database default
  transaction read-only.

Initial permission probes passed:

```text
trading_reader SELECT 1: allowed
trading_reader default_transaction_read_only: on
trading_migrator CREATE ROLE: denied
trading_migrator CREATE DATABASE: denied
```

Table-level reader INSERT/UPDATE and migrator write permissions will be tested
after Alembic creates the schema. No ad-hoc operational table was created.

## Secret handling

The local configuration directory is 0700. Four role/admin environment files
are 0600. Passwords were generated locally, passed through protected files or
process environment, and were not emitted in command output, documentation, or
Git. Repository `.env.example` values are placeholders.

## Health and isolation

`pg_isready -h 127.0.0.1 -p 55432` reports accepting connections. Socket
inspection found only `127.0.0.1:55432` for this cluster. The active legacy
dashboard remains on port 3002, and the active trading agent/dashboard services
were not restarted.

No Alembic schema, migration run, domain row, legacy import, broker call, or
exchange call occurred during provisioning.

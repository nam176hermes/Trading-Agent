# Phase 3 Local PostgreSQL 16

This directory documents the user-owned native PostgreSQL cluster used only by
the staged Trading Agent Control API and migration tests. It does not manage or
restart the active trading agent, active dashboard, distro PostgreSQL cluster,
port 3002, or Cloudflare.

## Identity

```text
PostgreSQL: 16.14
cluster_name: trading-agent
host: 127.0.0.1
port: 55432
database: trading_agent
data: ~/.local/share/trading-agent/postgres/16/trading-agent
socket: ~/.local/run/trading-agent
log: ~/.local/state/trading-agent/postgres/trading-agent.log
```

The cluster is initialized with SCRAM for local and host authentication. Its
data directory is 0700. The configuration directory is
`~/.config/trading-agent` mode 0700; local role files are 0600. Those files and
their passwords/DSNs are never committed or printed.

## Roles

- `trading_owner`: owns database/schema and runs Alembic; non-superuser.
- `trading_migrator`: imports operational data; non-superuser, no role/database
  creation.
- `trading_reader`: Control API reads; SELECT-only after schema grants and
  database-scoped default read-only transactions.
- `trading_jobs`: Job API, worker, and scheduler queue access; non-owner,
  non-superuser, no DDL or DELETE, and no job-event UPDATE.

The bootstrap `postgres` role is used only for local administration. No
application process uses it.

## Start, stop, and health

```bash
/usr/lib/postgresql/16/bin/pg_ctl \
  -D "$HOME/.local/share/trading-agent/postgres/16/trading-agent" \
  -l "$HOME/.local/state/trading-agent/postgres/trading-agent.log" start

ops/postgres/verify-cluster.sh

/usr/lib/postgresql/16/bin/pg_ctl \
  -D "$HOME/.local/share/trading-agent/postgres/16/trading-agent" stop -m fast
```

The health command contains no password. Use the protected role environment
files to establish authenticated sessions without exposing their contents.

## Local configuration files

```text
~/.config/trading-agent/postgres-admin.env
~/.config/trading-agent/postgres-owner.env
~/.config/trading-agent/postgres-migrator.env
~/.config/trading-agent/postgres-reader.env
~/.config/trading-agent/postgres-jobs.env
```

Each file contains split host/port/database/user/password values. Do not source
them in a shell that enables command tracing.
`ops/postgres/postgres.env.example` contains placeholders only.

Schema and role tests use the explicitly protected `postgres-admin.env` only
to create and remove a uniquely named disposable database. They never migrate
the `trading_agent` runtime database and skip when that admin connection is
unavailable or lacks `CREATEDB`.

## Backup and restore

Before real apply, first determine whether the target has operational rows. An
empty target needs no data dump, but the schema/bootstrap restore path must
still be drilled. For non-empty data, use a protected password source and a
custom-format dump outside Git:

```bash
umask 077
pg_dump -Fc -h 127.0.0.1 -p 55432 -U trading_owner \
  -d trading_agent -f "$HOME/.local/state/trading-agent/backups/trading_agent.dump"
```

The tested restore command and temporary database lifecycle are implemented in
the Phase 3 backup/restore task. Never put a password on the command line.

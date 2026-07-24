# Foundation disposable PostgreSQL setup

Date: 2026-07-23

PostgreSQL 16 executables were resolved only from
`/usr/lib/postgresql/16/bin`. The harness used loopback-only TCP, the exact
approved ports, private roots under `/tmp`, a private per-cluster socket and
home, and the exact database `trading_agent_disposable_test`.

Every runtime invocation supplied the exact paper baseline:

```text
requested_mode=paper
effective_mode=paper
LIVE_EXECUTION_ENABLED=false
LIVE_TRADING_APPROVED=false
LIVE_TRADING_ENABLED=false
kill_switch=INACTIVE
```

The harness rejected inherited runtime PostgreSQL variables, source drift,
unapproved operation identifiers, unapproved paths or ports, reused fixture
slots, missing lifecycle Greenlight and selected runtime tests that skipped.

No operator-managed PostgreSQL service was started, stopped, recovered or
queried. No scheduler, provider, broker, exchange, dashboard, Cloudflare or
live-trading action was performed.

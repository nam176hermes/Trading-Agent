# Foundation disposable PostgreSQL setup

Date: 2026-07-26

The run used PostgreSQL `16.14` from `/usr/lib/postgresql/16/bin` and was bound
to commit `b42607cb8c21d7a7b5ffeb854f08d62e9d15ff2f`, tree
`017481771e33a09ad6fdde26a61dacea88b1faad`.

```text
bind=127.0.0.1
database=trading_agent_disposable_test
cluster=trading-agent-disposable-tests
ports=56520..56528
roots=/tmp/phase4-postgres-p02-b42607c-01..09
```

Nine explicit slots covered eight approved operation IDs; restore owned two
slots. Every root and port was absent before startup.

Each child received:

```text
requested_mode=paper
effective_mode=paper
LIVE_EXECUTION_ENABLED=false
LIVE_TRADING_APPROVED=false
LIVE_TRADING_ENABLED=false
kill_switch=INACTIVE
```

The controller inherited no runtime database setting. The harness rejected
source drift, runtime identifiers, unapproved paths or ports, reused slots,
missing Greenlight, non-paper state and selected tests that skipped. Bind,
socket and home directories were private; ports `3002`, `8401` and `55432` were
forbidden.

No operator-managed PostgreSQL service, scheduler, dashboard, provider, broker,
exchange or live path was invoked.

```text
PASS - DISPOSABLE POSTGRESQL SETUP VALIDATED
```

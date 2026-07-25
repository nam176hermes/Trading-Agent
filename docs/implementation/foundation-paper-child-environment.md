# Foundation paper child environment

## Boundary

The job worker never copies its parent environment into a research child. `ResearchEnvironmentSettings` validates protected roots and a small dedicated research credential namespace. `build_child_environment` creates a new mapping from that validated authority.

## Forced values

Every paper child receives these exact values regardless of parent configuration:

```text
TRADING_MODE=paper
LIVE_EXECUTION_ENABLED=false
LIVE_TRADING_APPROVED=false
LIVE_TRADING_ENABLED=false
```

The child also receives a fixed `PATH`, isolated scratch `HOME`, locale, timezone, protected semantic-input root, reports root, signals root and approved dedicated research credentials only.

## Credential non-inheritance

Negative tests seed parent mappings with sentinel values and assert that neither key nor value enters the child. Covered categories include:

| Category | Representative denied names |
|---|---|
| Exchange | `BINANCE_API_KEY`, `BINANCE_API_SECRET`, `KRAKEN_API_KEY`, `KRAKEN_PRIVATE_KEY` |
| Broker | `ALPACA_API_KEY`, `APCA_API_SECRET_KEY`, `BROKER_API_KEY`, `BROKER_ACCESS_TOKEN` |
| Withdrawal | `WITHDRAWAL_API_KEY`, `WITHDRAWAL_PRIVATE_KEY` |
| Dashboard and service | `TRADING_DASHBOARD_SERVICE_TOKEN`, `DASHBOARD_SERVICE_TOKEN`, `TRADING_JOB_API_TOKEN` |
| Database owner | `DATABASE_URL`, `DATABASE_OWNER_URL`, `POSTGRES_PASSWORD` |
| Generic shared providers | `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` |

Dedicated research credentials use `TRADING_RESEARCH_*` names and are independently allowlisted. They cannot grant exchange, broker, withdrawal, dashboard or database-owner authority.

## Defense layers

1. Parent environment is not inherited.
2. Credential names require an exact dedicated allowlist match.
3. Runtime roots cannot be supplied by the job payload.
4. Root ownership, mode and symlink checks run before spawn.
5. Four paper values are written after validation and cannot be overridden.
6. The generated worker service unit independently forces the same four values before the worker starts.
7. The command registry can resolve only `paper_main.py`.
8. The paper artifact has no loader capable of consuming trading credentials even if an unknown variable appeared.

## Evidence tests

- `tests/jobs/test_child_environment.py`
- `tests/jobs/test_command_registry.py`
- `tests/runtime_release/test_paper_boundary.py`

The tests assert exact child mappings, forbidden credential stripping, forced values, fixed argv and rejection of forged or stale spawn capabilities.

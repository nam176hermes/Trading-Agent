# Nautilus Adoption — 01A Baseline Inventory

## Capture boundary

This is a static, source-only inventory for Phase 1 of the Nautilus adoption
program. It was captured from clean base commit
`d677b9c62a6f5c7e75cd5e7420175ef3b9c50f0e` (tree
`4b40385d8b9fc02aa75ccdc44bd73a133bf5bc0d`). No runtime authority was
consulted and no database, service, broker, exchange, account, order, or
network endpoint was accessed.

The machine-readable record is [baseline-inventory.json](baseline-inventory.json).

## Component and dependency authority

| Component | Runtime | Manifest and lock authority |
| --- | --- | --- |
| Core/control plane | Python 3.11 | `pyproject.toml` and `uv.lock` |
| Preserved research backend | Python 3.11 | `legacy/research-backend/pyproject.toml` and `uv.lock` |
| Dashboard | Node/npm | `apps/dashboard/package.json` and `package-lock.json` |

The root is explicitly not a unified Python or npm workspace. Root Python must
not import the flat research backend in process. These boundaries are defined
by [README.md](../../README.md), the root [AGENTS.md](../../AGENTS.md), and the
component-local instructions.

## Alembic authority map

The source migration graph is linear:

```text
0001 -> 0002 -> 0003 -> 0004 -> 0005 -> 0006 -> 0007 -> 0008 -> 0009
```

`0009_canonical_market_data` is the source head. The Job API and static
runtime-release authority intentionally remain pinned to
`0008_trading_domain_ledger`. This is an explicit **deferred runtime
activation**, not a `0006`/`0008`/`0009` graph mismatch: the reviewed verdict
records `0007 -> 0008 -> 0009` as the only source chain and forbids changing
the runtime revision without a separately reviewed activation.

Evidence:

- [0009_canonical_market_data.py](../../alembic/versions/0009_canonical_market_data.py)
- [config.py](../../services/job_store/config.py)
- [config.py](../../apps/job_api/config.py)
- [Track C verdict](../plans/track-c-p10-canonical-market-data/PACKET-3-VERDICT.md)

## Legacy authority surfaces to replace or quarantine

| Surface | Current source paths | Migration treatment |
| --- | --- | --- |
| Direct execution | `broker.py`, `execute_live.py`, `exchange/adapter.py`, `exchange/ccxt_bridge.py`, `exchange/executor.py` | Keep unreachable from the new Control/API/AI paths; quarantine before deletion in WS-06. |
| Paper portfolio | `paper_trader.py` persists JSON portfolio, orders and trades | It cannot coexist as a second authoritative portfolio once Nautilus paper is authoritative. |
| Backtest portfolios | `backtest_engine.py`, `alpha_backtest.py`, `backtest_runner.py` | Use only as differential-reference inputs until unified parity is established. |
| Legacy events | `event_bus.py` (`LocalEventBus`, `RedisEventBridge`) and `event_hub.py` (JSONL) | Do not use as the Nautilus authority transport; bridge through typed, durable contracts. |
| Canonical durable events | `packages/event_ledger/` and Job repository/state transitions | Preserve; these are the appropriate ingestion and audit targets for WS-02. |

The primary execution import fan-in is
[`main.py:540`](../../legacy/research-backend/main.py:540): it loads the paper
trader, legacy broker, live executor and CCXT mode bridge together. The source
is retained for audit, but it must not become part of the Nautilus control path.

## Source and runtime authority documents

Source provenance is bound by:

- [source-authority.json](../../ops/consolidation/source-authority.json)
- [backend-source-manifest.json](../../ops/consolidation/backend-source-manifest.json)
- [dashboard-source-manifest.json](../../ops/consolidation/dashboard-source-manifest.json)

Runtime and release boundaries are described by:

- [release-authority-v2.md](../production/release-authority-v2.md)
- [promotion-status.json](../production/promotion-status.json)
- [foundation-live-boundary-evidence.md](../implementation/foundation-live-boundary-evidence.md)
- [foundation-live-path-inventory.md](../implementation/foundation-live-path-inventory.md)

All of them preserve paper-only operation and keep both live approvals false.

## Phase 1 decisions recorded

1. Create the engine as an isolated Python 3.12 dependency graph; do not mix
   it with the root or legacy Python 3.11 locks.
2. Treat legacy broker/CCXT modules and JSON portfolio state as non-authority
   inputs during migration.
3. Build the later process bridge on typed contracts, worker fencing and the
   durable event ledger—not `LocalEventBus`, Redis pub/sub, or “newest file”
   discovery.
4. Do not alter the current runtime revision pin or run any migration as part
   of Phase 1 source work.

## Evidence commands

```bash
git status --short
git show -s --format='%H%n%T%n%cI%n%s' HEAD
find alembic/versions -maxdepth 1 -type f -print | sort
rg -l --glob '*.py' '(^|\\s)(import|from) ccxt|ccxt\\.' legacy/research-backend
rg -l --glob '*.py' 'create_order\\(|cancel_order\\(|place_order\\(|execute_signal\\(' legacy/research-backend
rg -l --glob '*.py' 'save_portfolio\\(|PORTFOLIO_FILE|PAPER_DIR|portfolio\\.json' legacy/research-backend
```

No functional behavior changed in this packet. The next packet is 01B:
provenance/legal boundary for pinned Nautilus source.

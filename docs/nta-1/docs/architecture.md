# NTA-1 target architecture

## Design goals

1. Point-in-time correctness and reproducible decisions.
2. Explicit contracts between research, portfolio, risk, and execution.
3. Deterministic, independent, fail-closed risk authority.
4. One execution engine per account or subaccount.
5. Complete lineage from source snapshot to fill and reconciliation.
6. Paper evidence before any separately approved limited-live phase.

## Planes

### Data plane

Every observation stores `event_time`, `known_at`, and `ingested_at`. Historical
queries require `known_at <= decision_as_of`. Fundamentals additionally retain
period end, publication time, and revision time.

### Research plane

Technical, fundamental/on-chain, news, regime, bull/bear, and evidence-auditor
agents have no broker credentials. Their only accepted output is a validated
`ResearchPacket` with claims, source references, freshness, uncertainty, prompt
hash, and tool trace.

### Signal and ensemble plane

Each signal identifies its model and feature versions, source snapshot,
validity window, score, and uncertainty. Signals are normalized to a common
scale, optionally neutralized against known exposures, weighted by rolling
out-of-sample reliability, freshness, and diversity, and capped per model. An
expired signal has weight zero.

### Portfolio plane

Portfolio construction translates signals and current holdings into target
weights and `TradeIntent`. It cannot choose a broker or submit an order.

### Risk plane

The deterministic Risk Engine is the only component allowed to transform a
`TradeIntent` into a signed plan. It enforces asset, account, exposure, loss,
liquidity, cost, strategy-state, data-freshness, and operational-health policy.

Risk responses are limited to:

```text
ALLOW | RESIZE | REJECT | REDUCE_ONLY | HALT
```

### Execution plane

Execution verifies signature, TTL, idempotency, asset route, venue metadata,
precision, minimum notional, account, spread, liquidity, kill switch, positions,
and open orders. A timeout is reconciled by client order ID before retry; blind
retry is prohibited.

Use one engine per account:

- LEAN for equities, ETFs, futures, or multi-asset profiles.
- Freqtrade/FreqAI for directional crypto strategies.
- Hummingbot for order-book, market-making, TWAP, grid, or arbitrage profiles.

### Reconciliation and audit plane

Reconcile cash, positions, open orders, fills, fees, average cost, realized P&L,
and buying power at startup, after fills, on intervals, after reconnect, and
around rebalances. Unresolved mismatch changes the account state to `HALT`.

Every decision records snapshot, feature set, universe, code commit, model
versions, strategy version, risk policy, trace ID, order plan, and resulting
order/fill events.

## Initial engineering risk defaults

These are system-test defaults, not personal financial recommendations:

| Limit | Initial value |
|---|---:|
| Maximum gross exposure | 1.0x NAV |
| Minimum cash | 20% |
| Maximum one asset | 10% NAV |
| Risk per trade | 0.25% NAV |
| New exposure per decision | 5% NAV |
| Order slice | 1% NAV |
| Volume participation | 5% |
| Minimum expected edge/cost | 3x |
| Daily reduce-only threshold | 1% NAV |
| Weekly reduce-only threshold | 2.5% NAV |
| Hard drawdown halt | 5% NAV |
| Initial LLM ensemble cap | 15% |

New exposure fails closed when data, model, risk, audit, broker state, clock,
strategy promotion, order-plan validity, or expected edge is outside policy.

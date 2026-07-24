# NTA-1 Personal Trading Agent

NTA-1 separates probabilistic research from deterministic risk and execution.
Its primary invariant is:

> An LLM never has a direct path from market data to a broker order.

## Core flow

```text
Point-in-time data
  -> read-only research agents
  -> ResearchPacket
  -> deterministic and ML Signals
  -> normalize / neutralize / ensemble
  -> portfolio construction
  -> independent deterministic Risk Engine
  -> SignedOrderPlan
  -> execution adapter
  -> broker or exchange
  -> reconciliation, audit, monitoring
```

## Authority boundaries

- Research agents may produce evidence, directional scores, uncertainty, bull
  and bear cases, and invalidation conditions.
- Portfolio construction may propose target weights, not broker instructions.
- Risk may return `ALLOW`, `RESIZE`, `REJECT`, `REDUCE_ONLY`, or `HALT`.
- Only the execution service may hold broker credentials.
- Execution accepts only an unexpired, validly signed, idempotent order plan.
- Reconciliation mismatch fails closed and halts new exposure.

## Promotion lifecycle

```text
DRAFT -> VALIDATED -> BACKTESTED -> PAPER -> SHADOW
      -> LIVE_LIMITED -> LIVE -> PAUSED or RETIRED
```

No model, strategy, or LLM factor skips a stage. New LLM factors begin in
shadow mode with ensemble weight zero.

See `docs/architecture.md` for the detailed target boundaries.
